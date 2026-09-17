"""Project check observations and conservative, identity-based regression decisions."""

import ast
from hashlib import sha256
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
from pathlib import Path
import re
import shlex
import select
import socket
import tempfile
import threading
import time
import tomllib
from uuid import uuid4
from urllib.parse import urlsplit

from copilot_runtime import RuntimeFailure, capture, strict_json
from evolution_records import exact, matches, DIGEST, require
from repositories import read_file


CASE_STATES = {"passed", "failed", "error", "skipped", "expected_failure"}
CONFIG_FILES = (
    "pyproject.toml", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "setup.py",
    "requirements.txt", "requirements-dev.txt", "uv.lock", "poetry.lock",
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    ".npmrc", "tsconfig.json",
)

PYTHON_REPORTER = r'''
import contextlib, json, sys, unittest
cases = {}
with contextlib.redirect_stdout(sys.stderr):
    sys.path.insert(0, "/work")
    if sys.argv[1] == "unittest":
        class Result(unittest.TestResult):
            def startTest(self, test):
                super().startTest(test)
                if test.id() in cases:
                    raise ValueError("duplicate test identity")
                cases[test.id()] = "running"
            def addSuccess(self, test):
                super().addSuccess(test)
                cases[test.id()] = "passed"
            def addFailure(self, test, err):
                super().addFailure(test, err)
                cases[test.id()] = "failed"
            def addError(self, test, err):
                super().addError(test, err)
                cases[test.id()] = "error"
            def addSkip(self, test, reason):
                super().addSkip(test, reason)
                cases[test.id()] = "skipped"
            def addExpectedFailure(self, test, err):
                super().addExpectedFailure(test, err)
                cases[test.id()] = "expected_failure"
            def addUnexpectedSuccess(self, test):
                super().addUnexpectedSuccess(test)
                cases[test.id()] = "failed"
            def addSubTest(self, test, subtest, err):
                super().addSubTest(test, subtest, err)
                if err is not None:
                    cases[test.id()] = "failed" if issubclass(err[0], test.failureException) else "error"
        class Runner:
            def run(self, suite):
                result = Result()
                suite(result)
                return result
        program = unittest.TestProgram(module=None, argv=["unittest", *sys.argv[2:]],
                                       testRunner=Runner(), exit=False)
        state = "completed" if program.result.wasSuccessful() else "failed"
    else:
        import pytest
        class Plugin:
            def pytest_collection_finish(self, session):
                for item in session.items:
                    if item.nodeid in cases:
                        raise ValueError("duplicate test identity")
                    cases[item.nodeid] = "running"
            def pytest_runtest_logreport(self, report):
                if report.when == "call":
                    cases[report.nodeid] = (
                        "expected_failure" if getattr(report, "wasxfail", False)
                        else {"passed":"passed", "failed":"failed", "skipped":"skipped"}[report.outcome])
                elif report.failed:
                    cases[report.nodeid] = "error"
                elif report.skipped and cases.get(report.nodeid) == "running":
                    cases[report.nodeid] = "skipped"
        code = pytest.main(sys.argv[2:], plugins=[Plugin()])
        state = "completed" if code == 0 else "failed" if code == 1 else "blocked"
if any(value == "running" for value in cases.values()):
    state = "blocked"
    cases = {key:value for key,value in cases.items() if value != "running"}
print(json.dumps({"status":state, "cases":[{"id":key,"status":value} for key,value in sorted(cases.items())]}))
'''

PYTHON_GUARD = r'''
import hashlib, json, pathlib, subprocess, sys
expected = json.loads(pathlib.Path("/controls/protected.json").read_text())
def intact():
    return all(not pathlib.Path(name).is_symlink() and pathlib.Path(name).is_file()
               and hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == value
               for name, value in expected.items())
if not intact():
    sys.exit(86)
result = subprocess.run(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if not intact():
    sys.exit(86)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
'''

NODE_REPORTER = r'''
module.exports = async function* (events) {
  const cases = [];
  for await (const event of events) {
    if (!["test:pass", "test:fail"].includes(event.type) || event.data.details?.type === "suite") continue;
    const data = event.data;
    if (typeof data.file !== "string" || typeof data.name !== "string") throw Error("missing test identity");
    const id = `${data.file.replace(/^\/work\//, "")}:${data.line}:${data.column}:${data.name}`;
    const status = data.skip ? "skipped" : data.todo ? "expected_failure"
      : event.type === "test:pass" ? "passed" : "failed";
    cases.push({ id, status });
  }
  yield JSON.stringify({ status: cases.some(row => row.status === "failed") ? "failed" : "completed", cases });
};
'''

NODE_GUARD = r'''
const fs = require("node:fs");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const expected = JSON.parse(fs.readFileSync("/controls/protected.json", "utf8"));
function intact() {
  return Object.entries(expected).every(([name, digest]) => {
    if (!fs.existsSync(name) || !fs.lstatSync(name).isFile()) return false;
    return crypto.createHash("sha256").update(fs.readFileSync(name)).digest("hex") === digest;
  });
}
if (!intact()) process.exit(86);
const [mode, command, ...args] = process.argv.slice(2);
const outcome = spawnSync(command, args, { encoding: "utf8", maxBuffer: 1024 * 1024,
  env: { ...process.env, PATH: `/work/node_modules/.bin:${process.env.PATH}` } });
if (!intact()) process.exit(86);
if (outcome.error || outcome.signal) process.exit(87);
if (mode === "gate") {
  process.stdout.write(JSON.stringify({status: outcome.status === 0 ? "passed" : "failed"}));
} else if (mode === "node-test") {
  const value = JSON.parse(outcome.stdout);
  if (!["completed", "failed", "blocked"].includes(value.status) || !Array.isArray(value.cases)) process.exit(88);
  if (outcome.status !== 0 && value.status !== "failed") value.status = "blocked";
  process.stdout.write(JSON.stringify(value));
} else {
  const value = JSON.parse(fs.readFileSync("/tmp/test-results.json", "utf8"));
  if (!Array.isArray(value.testResults) || !Number.isInteger(value.numTotalTests)) process.exit(88);
  const cases = [];
  let incomplete = false;
  for (const suite of value.testResults) {
    if (typeof suite.name !== "string" || !Array.isArray(suite.assertionResults)) process.exit(88);
    if (suite.status === "failed" && suite.assertionResults.length === 0) incomplete = true;
    for (const item of suite.assertionResults) {
      if (typeof item.fullName !== "string") process.exit(88);
      const status = { passed:"passed", failed:"failed", pending:"skipped", skipped:"skipped", todo:"expected_failure" }[item.status];
      if (!status) process.exit(88);
      cases.push({ id: `${suite.name.replace(/^\/work\//, "")}:${item.fullName}`, status });
    }
  }
  if (cases.length !== value.numTotalTests || value.numRuntimeErrorTestSuites > 0) incomplete = true;
  let status = cases.some(row => row.status === "failed") ? "failed" : "completed";
  if (incomplete || (outcome.status !== 0 && status !== "failed")) status = "blocked";
  process.stdout.write(JSON.stringify({ status, cases }));
}
'''


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def container_capture(source, controls, image, argv, *, timeout=120):
    source, controls = Path(source).absolute(), Path(controls).absolute()
    require(matches("sha256:" + DIGEST, image), "invalid_container")
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1200,
            "invalid_limit")
    require(isinstance(argv, list) and argv and all(isinstance(arg, str) and "\0" not in arg for arg in argv),
            "invalid_container")
    for directory in (source, controls):
        require(directory.is_dir() and "," not in str(directory)
                and not any(path.is_symlink() for path in (directory, *directory.parents)), "unsafe_project")
    name = "skillops-check-" + uuid4().hex
    command = [
        "docker", "run", "--rm", "--name", name, "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=512m", "--cpus=1",
        "--user=65534:65534", "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
        "--tmpfs", "/work:rw,nosuid,nodev,exec,size=256m,mode=1777",
        "--mount", f"type=bind,src={source},dst=/input,readonly",
        "--mount", f"type=bind,src={controls},dst=/controls,readonly",
        "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint", "/bin/sh", image, "-c",
        'cp -R /input/. /work/ && '
        'if [ -d /opt/skillops/node/node_modules ]; then cp -R /opt/skillops/node/node_modules /work/; fi && '
        'cd /work && exec "$@"', "skillops-check", *argv,
    ]
    try:
        return capture(command, timeout=timeout, limit=1024 * 1024)
    finally:
        cleanup = capture(["docker", "rm", "--force", name], timeout=15, limit=16384)
        if cleanup.returncode and "No such container" not in cleanup.stderr:
            raise RuntimeFailure("cleanup_failed", "Could not remove the owned project-check container.")


def protected_files(root):
    paths = []
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if (relative.as_posix() in CONFIG_FILES or any(part in ("tests", "test", "__tests__", "fixtures")
                                                     for part in relative.parts)
                or path.name.startswith(("test_", "conftest."))
                or ".test." in path.name or ".spec." in path.name
                or ".config." in path.name):
            paths.append(relative.as_posix())
    return sorted(paths)


def dependency_manifest(root, language):
    """Only declarative registry requirements reach the network-enabled resolver."""
    root = Path(root)
    if language == "python":
        dependencies = []
        static_project_dependencies = False
        for name in ("requirements.txt", "requirements-dev.txt"):
            if (root / name).exists():
                dependencies.extend(line.split("#", 1)[0].strip() for line in
                                    read_file(root / name).decode("utf-8").splitlines())
        if (root / "pyproject.toml").exists():
            try:
                metadata = tomllib.loads(read_file(root / "pyproject.toml").decode("utf-8"))
            except (UnicodeError, tomllib.TOMLDecodeError) as error:
                raise RuntimeFailure("invalid_check_config", "Invalid Python dependency metadata.") from error
            project = metadata.get("project", {})
            require(isinstance(project, dict), "unsupported_dependencies")
            dynamic = project.get("dynamic", [])
            require(isinstance(dynamic, list) and all(isinstance(name, str) for name in dynamic)
                    and not {"dependencies", "optional-dependencies"}.intersection(dynamic),
                    "unsupported_dependencies")
            declared = project.get("dependencies", [])
            require(isinstance(declared, list), "unsupported_dependencies")
            static_project_dependencies = "dependencies" in project
            dependencies.extend(declared)
            optional = project.get("optional-dependencies", {})
            require(isinstance(optional, dict), "unsupported_dependencies")
            for group in ("test", "tests", "dev"):
                require(isinstance(optional.get(group, []), list), "unsupported_dependencies")
                dependencies.extend(optional.get(group, []))
            require("poetry" not in metadata.get("tool", {}), "unsupported_dependencies")
        require(not any((root / name).exists() for name in ("uv.lock", "poetry.lock")),
                "unsupported_dependencies")
        require(static_project_dependencies or not any((root / name).exists() for name in ("setup.py", "setup.cfg")),
                "unsupported_dependencies")
        dependencies = [item for item in dependencies if item]
        for item in dependencies:
            require(isinstance(item, str) and re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*", item)
                    and not any(char in item for char in "@:/\\\r\n"), "unsupported_dependencies")
        if any(check["runner"] == "pytest" for check in discover(root)["checks"]) and not any(
                re.match(r"(?i)^pytest(?:\b|\[)", item) for item in dependencies):
            dependencies.append("pytest")
        return {"requirements.txt": "\n".join(dependencies) + "\n"} if dependencies else {}
    require(language == "node", "unsupported_dependencies")
    if not (root / "package.json").exists():
        return {}
    package = strict_json(read_file(root / "package.json").decode("utf-8"))
    require(isinstance(package, dict) and not any(name in package for name in
            ("workspaces", "overrides", "bundledDependencies", "bundleDependencies")), "unsupported_dependencies")
    require(not (root / ".npmrc").exists() and not (root / "npm-shrinkwrap.json").exists(),
            "unsupported_dependencies")
    count = 0
    for kind in ("dependencies", "devDependencies", "optionalDependencies"):
        values = package.get(kind, {})
        require(isinstance(values, dict), "unsupported_dependencies")
        for name, value in values.items():
            require(matches(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", name)
                    and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.*~^<>=|+ -]+", value),
                    "unsupported_dependencies")
        count += len(values)
    if not count:
        return {}
    manifest = {"package.json": json.dumps(package)}
    if (root / "package-lock.json").exists():
        raw = read_file(root / "package-lock.json").decode("utf-8")
        lock = strict_json(raw)
        require(isinstance(lock, dict) and lock.get("lockfileVersion") in (2, 3)
                and isinstance(lock.get("packages"), dict), "unsupported_dependencies")
        for name, value in lock["packages"].items():
            require(isinstance(value, dict) and not value.get("link"), "unsupported_dependencies")
            if name:
                target = urlsplit(value.get("resolved", ""))
                require(target.scheme == "https" and target.hostname == "registry.npmjs.org"
                        and target.port in (None, 443) and not target.username and not target.password,
                        "unsupported_dependencies")
        manifest["package-lock.json"] = raw
    return manifest


@contextmanager
def registry_proxy(gateway, deadline):
    allowed = {"pypi.org", "files.pythonhosted.org", "registry.npmjs.org"}
    slots = threading.BoundedSemaphore(16)
    stopping, lock, connections = threading.Event(), threading.Lock(), set()

    class Proxy(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
            with lock:
                connections.add(self.connection)

        def finish(self):
            try:
                super().finish()
            finally:
                with lock:
                    connections.discard(self.connection)

        def log_message(self, *args):
            return

        def do_CONNECT(self):
            if stopping.is_set():
                return
            if not slots.acquire(blocking=False):
                self.send_error(503)
                return
            upstream = None
            try:
                target = urlsplit("//" + self.path)
                if target.hostname not in allowed or target.port != 443 or target.username or target.password:
                    self.send_error(403)
                    return
                addresses = socket.getaddrinfo(target.hostname, 443, type=socket.SOCK_STREAM)
                address = next((item[4][0] for item in addresses if ipaddress.ip_address(item[4][0]).is_global), None)
                if address is None:
                    self.send_error(403)
                    return
                with socket.create_connection((address, 443), timeout=10) as upstream:
                    with lock:
                        connections.add(upstream)
                    if stopping.is_set():
                        return
                    self.send_response(200)
                    self.end_headers()
                    peers = [self.connection, upstream]
                    while not stopping.is_set() and time.monotonic() < deadline:
                        readable, _, _ = select.select(peers, [], [], min(10, max(0, deadline - time.monotonic())))
                        if not readable:
                            return
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is self.connection else self.connection).sendall(data)
            except (OSError, ValueError):
                self.close_connection = True
            finally:
                if upstream is not None:
                    with lock:
                        connections.discard(upstream)
                slots.release()

    server = ThreadingHTTPServer((gateway, 0), Proxy)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{gateway}:{server.server_port}"
    finally:
        stopping.set()
        server.shutdown()
        with lock:
            active = list(connections)
        for connection in active:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                # The worker may have closed its socket between the snapshot and shutdown.
                pass
        server.server_close()
        thread.join(timeout=5)
        require(not thread.is_alive(), "cleanup_failed")


@contextmanager
def prepared_images(root, images, *, timeout=120):
    """Resolve wheels/npm packages without hooks, then freeze images for offline checks."""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1200, "invalid_limit")
    manifests = {language: dependency_manifest(root, language) for language in images}
    prepared, owned = dict(images), []
    deadline = time.monotonic() + timeout
    try:
        for language, manifest in manifests.items():
            if not manifest:
                continue
            image = images[language]
            require(matches("sha256:" + DIGEST, image), "invalid_container")
            token = uuid4().hex
            name, network, base_tag, tag = (prefix + token for prefix in (
                "skillops-resolve-", "skillops-network-", "skillops-base:", "skillops-prepared:"))

            def command(argv):
                remaining = deadline - time.monotonic()
                require(remaining > 0, "dependency_timeout")
                result = capture(argv, timeout=remaining, limit=1024 * 1024)
                if result.returncode:
                    raise RuntimeFailure("dependency_preparation_failed", result.stderr[-4096:])
                return result.stdout.strip()

            with tempfile.TemporaryDirectory(prefix="skillops-deps-") as folder:
                work = Path(folder)
                work.chmod(0o755)
                inputs, output = work / "manifest", work / "deps"
                inputs.mkdir(mode=0o755)
                output.mkdir()
                output.chmod(0o777)
                for path, raw in manifest.items():
                    (inputs / path).write_text(raw, encoding="utf-8")
                owned.append(tag)
                try:
                    command(["docker", "network", "create", "--internal", network])
                    gateway = command(["docker", "network", "inspect", network, "--format", "{{(index .IPAM.Config 0).Gateway}}"])
                    require(ipaddress.ip_address(gateway).is_private, "invalid_container")
                    with registry_proxy(gateway, deadline) as proxy:
                        if language == "python":
                            install = ["python", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                                       "--no-cache-dir", "--only-binary=:all:", "--no-compile", "--proxy", proxy,
                                       "--index-url", "https://pypi.org/simple", "--target", "/deps",
                                       "-r", "/manifest/requirements.txt"]
                        else:
                            install = ["npm", "ci" if "package-lock.json" in manifest else "install",
                                       "--ignore-scripts", "--no-audit", "--no-fund", "--cache=/tmp/npm",
                                       "--registry=https://registry.npmjs.org", "--proxy=" + proxy, "--https-proxy=" + proxy,
                                       "--userconfig=/dev/null", "--globalconfig=/tmp/skillops-global-npmrc"]
                        command([
                            "docker", "run", "--name", name, "--network", network, "--read-only", "--cap-drop=ALL",
                            "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=512m", "--cpus=1",
                            "--user=65534:65534", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                            "--tmpfs", "/deps:rw,nosuid,nodev,size=256m,mode=1777",
                            "--mount", f"type=bind,src={inputs},dst=/manifest,readonly",
                            "--mount", f"type=bind,src={output},dst=/output", "--env", "HOME=/tmp",
                            "--entrypoint", "/bin/sh", image, "-c",
                            'cp /manifest/* /deps/ && cd /deps && "$@" && cp -R /deps/. /output/ && '
                            'find /output -mindepth 1 -type d -exec chmod 777 {} + && '
                            'find /output -type f -exec chmod a+rw {} +',
                            "skillops-resolve", *install,
                        ])
                    command(["docker", "tag", image, base_tag])
                    (work / "Dockerfile").write_text(
                        f"FROM {base_tag}\nCOPY deps/ /opt/skillops/{language}/\n"
                        + ("ENV PYTHONPATH=/opt/skillops/python\n" if language == "python" else "")
                        + f"LABEL skillops.owner={token}\n", encoding="utf-8")
                    command(["docker", "build", "--network=none", "--pull=false", "--quiet", "--tag", tag, str(work)])
                    identity = command(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
                    require(matches("sha256:" + DIGEST, identity), "invalid_container")
                    prepared[language] = identity
                finally:
                    failures = []
                    for argv, absent in (
                        (["docker", "rm", "--force", name], f"No such container: {name}"),
                        (["docker", "network", "rm", network], f"network {network} not found"),
                        (["docker", "image", "rm", base_tag], f"No such image: {base_tag}"),
                    ):
                        result = capture(argv, timeout=15, limit=16384)
                        if result.returncode and absent not in result.stderr:
                            failures.append(argv[1])
                    require(not failures, "cleanup_failed")
        yield prepared
    finally:
        failed = False
        for tag in owned:
            result = capture(["docker", "image", "rm", tag], timeout=30, limit=16384)
            failed |= bool(result.returncode and f"No such image: {tag}" not in result.stderr)
        require(not failed, "cleanup_failed")


def protected_digest(root, paths):
    from evolution_records import relative_path
    require(isinstance(paths, list) and len(paths) <= 10000, "invalid_check_plan")
    for name in paths:
        relative_path(name)
    require(paths == sorted(set(paths)), "invalid_check_plan")
    return digest({name: sha256(read_file(Path(root) / name, 1024 * 1024)).hexdigest() for name in paths})


def execute(root, plan, images, *, timeout=120, protected=None, deadline=None):
    """Run project-owned checks in a frozen caller-prepared image, never on the host."""
    started = time.monotonic()
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1200,
            "invalid_limit")
    require(deadline is None or type(deadline) in (int, float) and math.isfinite(deadline), "invalid_limit")
    end = min(started + timeout, deadline) if deadline is not None else started + timeout
    require(end > started, "time_limit")
    root = Path(root).absolute()
    require(discover(root) == plan and plan["status"] in ("supported", "partial"), "invalid_check_plan")
    require(isinstance(images, dict) and images
            and all(key in ("python", "node") and matches("sha256:" + DIGEST, value)
                    for key, value in images.items()), "missing_check_image")
    languages = {"unittest": "python", "pytest": "python", "node-test": "node", "jest": "node", "vitest": "node"}
    require(all(check["runner"] in languages for check in plan["checks"]), "unsupported_check_runner")
    require(all(languages[check["runner"]] in images for check in plan["checks"])
            and (not plan["gates"] or "node" in images), "missing_check_image")
    protected = protected_files(root) if protected is None else protected
    before = protected_digest(root, protected)
    result = {"plan_sha256": plan["sha256"], "environment_sha256": digest(images),
              "protected_sha256": before, "status": "blocked" if plan["exclusions"] else "completed", "cases": [],
              "gates": [{"id": item["reason"] + ":" + item["path"], "status": "error"}
                        for item in plan["exclusions"]],
              "elapsed_seconds": 0}
    with tempfile.TemporaryDirectory(prefix="skillops-check-") as folder:
        work = Path(folder)
        work.chmod(0o755)
        stage, controls = work / "source", work / "controls"
        stage.mkdir(mode=0o755)
        controls.mkdir(mode=0o755)
        total = 0
        for path in root.rglob("*"):
            require(not path.is_symlink(), "unsafe_project")
            if path.is_dir():
                continue
            raw = read_file(path, 1024 * 1024)
            total += len(raw)
            require(total <= 128 * 1024 * 1024, "project_limit")
            target = stage / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o644)
        script = controls / "report.py"
        script.write_text(PYTHON_REPORTER)
        script.chmod(0o644)
        guard = controls / "guard.py"
        guard.write_text(PYTHON_GUARD)
        guard.chmod(0o644)
        for name, source in (("node-reporter.cjs", NODE_REPORTER), ("node-guard.cjs", NODE_GUARD)):
            path = controls / name
            path.write_text(source)
            path.chmod(0o644)
        manifest = controls / "protected.json"
        manifest.write_text(json.dumps({name: sha256(read_file(stage / name)).hexdigest() for name in protected}))
        manifest.chmod(0o644)
        for check in plan["checks"]:
            remaining = end - time.monotonic()
            require(remaining > 0, "time_limit")
            language = languages[check["runner"]]
            if language == "python":
                argv = ["python", "-B", "/controls/guard.py", "python", "-B", "/controls/report.py",
                        check["runner"], *check["argv"][3:]]
            else:
                original = check["argv"]
                if check["runner"] == "node-test":
                    args = [original[0], "--test-reporter=/controls/node-reporter.cjs", *original[1:]]
                elif check["runner"] == "jest":
                    args = [*original, "--json", "--outputFile=/tmp/test-results.json", "--runInBand"]
                else:
                    args = [*original, "--reporter=json", "--outputFile=/tmp/test-results.json"]
                argv = ["node", "/controls/node-guard.cjs", check["runner"], *args]
            outcome = container_capture(stage, controls, images[language], argv, timeout=remaining)
            if outcome.returncode:
                raise RuntimeFailure("check_runtime_error", "The project reporter did not finish successfully.")
            observed = strict_json(outcome.stdout)
            exact(observed, "status cases")
            require(isinstance(observed["cases"], list), "invalid_check_observation")
            for case in observed["cases"]:
                exact(case, "id status")
                require(isinstance(case["id"], str), "invalid_check_observation")
                result["cases"].append({"id": check["id"] + ":" + case["id"], "status": case["status"]})
            require(observed["status"] in ("completed", "failed", "blocked"), "invalid_check_observation")
            if not observed["cases"] or observed["status"] == "blocked" or result["status"] == "blocked":
                result["status"] = "blocked"
            elif observed["status"] == "failed":
                result["status"] = "failed"
        for gate in plan["gates"]:
            remaining = end - time.monotonic()
            require(remaining > 0, "time_limit")
            outcome = container_capture(stage, controls, images["node"],
                                        ["node", "/controls/node-guard.cjs", "gate", *gate["argv"]],
                                        timeout=remaining)
            require(outcome.returncode == 0, "check_runtime_error")
            observed = strict_json(outcome.stdout)
            exact(observed, "status")
            require(observed["status"] in ("passed", "failed"), "invalid_check_observation")
            result["gates"].append({"id": gate["id"], "status": observed["status"]})
            if observed["status"] == "failed" and result["status"] != "blocked":
                result["status"] = "failed"
        require(protected_digest(root, protected) == before and discover(root) == plan, "check_inputs_changed")
    result["elapsed_seconds"] = time.monotonic() - started
    return validate_observation(result)


def discover(root):
    """Inspect source/configuration only. No project imports or commands run during discovery."""
    root = Path(root).absolute()
    require(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents)),
            "unsafe_project")
    checks, gates, reasons, fingerprints = [], [], [], {}
    files = []
    for path in root.rglob("*"):
        require(not path.is_symlink(), "unsafe_project")
        if path.is_dir():
            continue
        require(path.is_file() and len(files) < 10000, "project_limit")
        files.append(path.relative_to(root).as_posix())
    configs = {}
    for name in CONFIG_FILES:
        path = root / name
        if path.exists() or path.is_symlink():
            raw = read_file(path, 1024 * 1024)
            try:
                configs[name] = raw.decode("utf-8")
            except UnicodeError as error:
                raise RuntimeFailure("invalid_check_config", "Check configuration must be UTF-8.") from error
            fingerprints[name] = sha256(raw).hexdigest()
    python_tests = [name for name in files if name.endswith(".py")
                    and (Path(name).name.startswith("test") or name.endswith("_test.py"))]
    pytest = any(name in configs for name in ("pytest.ini", ".pytest.ini"))
    if "pyproject.toml" in configs:
        try:
            metadata = tomllib.loads(configs["pyproject.toml"])
        except tomllib.TOMLDecodeError as error:
            raise RuntimeFailure("invalid_check_config", "Invalid Python project configuration.") from error
        pytest |= isinstance(metadata.get("tool"), dict) and "pytest" in metadata["tool"]
    pytest |= any(re.search(r"(?mi)^\s*pytest(?:\s|[<>=~!\[]|$)", configs.get(name, ""))
                  for name in ("requirements.txt", "requirements-dev.txt"))
    pytest |= any(section in configs.get(name, "") for name, section in
                  (("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")))
    if not pytest:
        for name in sorted(python_tests):
            try:
                tree = ast.parse(read_file(root / name).decode("utf-8"))
            except (SyntaxError, UnicodeError) as error:
                raise RuntimeFailure("invalid_check_config", "Python test syntax could not be inspected.") from error
            pytest |= name.endswith("_test.py") or any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
                for node in tree.body)
            pytest |= any(
                (isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "pytest" for alias in node.names))
                or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pytest")
                for node in ast.walk(tree))
            if pytest:
                break
    if python_tests or pytest:
        start = ["-s", "tests"] if (root / "tests").is_dir() else []
        checks.append({"id": "python-tests", "runner": "pytest" if pytest else "unittest",
                       "argv": ["python", "-m", "pytest"] if pytest
                       else ["python", "-m", "unittest", "discover", *start]})
    if "package.json" in configs:
        package = strict_json(configs["package.json"])
        require(isinstance(package, dict), "invalid_check_config")
        scripts = package.get("scripts", {})
        require(isinstance(scripts, dict) and all(isinstance(key, str) and isinstance(value, str)
                                                for key, value in scripts.items()), "invalid_check_config")
        command = scripts.get("test")
        if command is not None:
            try:
                argv = shlex.split(command)
            except ValueError as error:
                raise RuntimeFailure("invalid_check_config", "Invalid Node test script.") from error
            runner = None
            if not any(char in command for char in "&|;<>`$\n\r") and argv:
                if argv[:2] == ["node", "--test"]:
                    runner = "node-test"
                elif argv[0] == "jest":
                    runner = "jest"
                elif argv[:2] == ["vitest", "run"]:
                    runner = "vitest"
            if runner is None or "pretest" in scripts or "posttest" in scripts:
                reasons.append("unsupported_node_test_script")
            else:
                checks.append({"id": "node-tests", "runner": runner, "argv": argv})
        for name in ("build", "lint", "typecheck"):
            if name in scripts:
                gates.append({"id": name, "argv": ["npm", "run", name]})
        if any(name in configs for name in ("yarn.lock", "pnpm-lock.yaml")):
            reasons.append("unsupported_package_manager")
    elif any(name.endswith((".test.js", ".test.mjs", ".test.cjs")) for name in files):
        checks.append({"id": "node-tests", "runner": "node-test", "argv": ["node", "--test"]})
    exclusions = [{"path": "package.json", "reason": reason} for reason in reasons]
    for name in sorted(files):
        path = Path(name)
        if path.name == "package.json" and name != "package.json":
            raw = read_file(root / name)
            package = strict_json(raw.decode("utf-8"))
            require(isinstance(package, dict) and isinstance(package.get("scripts", {}), dict),
                    "invalid_check_config")
            if "test" in package.get("scripts", {}):
                exclusions.append({"path": name, "reason": "unsupported_nested_node_tests"})
                fingerprints[name] = sha256(raw).hexdigest()
        elif path.suffix == ".sh" and "test" in path.name:
            exclusions.append({"path": name, "reason": "unsupported_shell_tests"})
    status = ("partial" if checks else "unsupported") if exclusions else ("supported" if checks else "missing")
    plan = {"schema_version": 1, "status": status, "checks": checks, "gates": gates,
            "config_sha256": fingerprints, "reasons": sorted({item["reason"] for item in exclusions}),
            "exclusions": exclusions}
    plan["sha256"] = sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return plan


def validate_observation(row):
    exact(row, "plan_sha256 environment_sha256 protected_sha256 status cases gates elapsed_seconds")
    for key in ("plan_sha256", "environment_sha256", "protected_sha256"):
        require(matches(DIGEST, row[key]), "invalid_check_observation")
    require(isinstance(row["status"], str) and row["status"] in ("completed", "failed", "blocked"),
            "invalid_check_observation")
    elapsed = row["elapsed_seconds"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
            "invalid_check_observation")
    failures = False
    for name, states in (("cases", CASE_STATES), ("gates", {"passed", "failed", "error"})):
        values = row[name]
        require(isinstance(values, list) and len(values) <= 10000, "invalid_check_observation")
        seen = set()
        for item in values:
            exact(item, "id status")
            identifier = item["id"]
            require(isinstance(identifier, str) and 0 < len(identifier.encode("utf-8")) <= 1024
                    and not any(ord(char) < 32 or ord(char) == 127 for char in identifier)
                    and identifier not in seen, "invalid_check_observation")
            require(isinstance(item["status"], str) and item["status"] in states,
                    "invalid_check_observation")
            seen.add(identifier)
            failures |= item["status"] in ("failed", "error")
    require(row["status"] != "completed" or not failures, "invalid_check_observation")
    require(row["status"] != "failed" or failures, "invalid_check_observation")
    return row


def compare(original, base, candidate):
    """A pass covers observed checks only; task completion and Skill quality are separate gates."""
    rows = (original, base, candidate)
    for row in rows:
        if row is not None:
            validate_observation(row)
    if any(row is None for row in rows):
        return {"status": "unverified", "reasons": ["missing_checks"], "regressions": []}
    if any(len({row[key] for row in rows}) != 1
           for key in ("plan_sha256", "environment_sha256", "protected_sha256")):
        return {"status": "unverified", "reasons": ["input_mismatch"], "regressions": []}
    reasons, regressions = set(), set()
    if any(row["status"] == "blocked" for row in rows):
        reasons.add("incomplete_checks")
    for name, prefix in (("cases", "test"), ("gates", "gate")):
        populations = [{item["id"]: item["status"] for item in row[name]} for row in rows]
        before, baseline, after = populations
        if set(before) != set(baseline):
            reasons.add("baseline_population_changed")
        if set(after) != set(before) or set(after) != set(baseline):
            reasons.add("candidate_population_changed")
            regressions.update(f"{prefix}:{key}" for key in (set(before) | set(baseline)) - set(after))
        for reference in (before, baseline):
            regressions.update(
                f"{prefix}:{key}" for key, status in reference.items()
                if status == "passed" and after.get(key) != "passed"
            )
        if name == "cases" and not any(
                before.get(key) == baseline.get(key) == after.get(key) == "passed" for key in before):
            reasons.add("no_passing_coverage")
        if name == "gates" and any(status != "passed" for population in populations for status in population.values()):
            reasons.add("required_gate_failed")
    status = "rejected" if regressions else "unverified" if reasons else "passed"
    return {"status": status, "reasons": sorted(reasons), "regressions": sorted(regressions)}
