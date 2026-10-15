"""Host orchestrator: external source is executed only in constrained containers."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent
PYTHON = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
REDIS = "redis@sha256:3aaec283e6e593bde528077d60280ac1589887067a39273348860837c9346d7e"
REVISIONS = {
    "rq-2515": ("rq/rq", "a7233f3a25c2848a071f69b6e4f5be3316b7ab82", "f114a649c57555bed3100b7b1e00fb8996d5d1d5"),
    "rq-2516": ("rq/rq", "a41a36b431b67e329d671bc5cf7455df5890ab1e", "7d7f925c9a4a5bc0ac5d947d0a227e5fe167c44c"),
    "urllib3-3743": ("urllib3/urllib3", "8c480bf87bcefd321b3a1ae47f04e908b6b2ed7b", "13f0bfd55e4468fe1ea9c6f809d3a87b0f93ebab"),
}
EXPECTED_FAILURES = {
    "rq-2515": {"test_enqueue_many_with_generator": "Lists differ"},
    "rq-2516": {
        "test_terminal_failure_contract[dependents-failure]": "slot leaked",
        "test_terminal_failure_contract[dependents-retry_exhausted]": "slot leaked",
        "test_terminal_failure_contract[persist_once-failure]": 'assert not set(events)',
    },
    "urllib3-3743": {
        "test_numeric_cap_and_clone": "9999999999 == 21600",
        "test_http_date_cap": "999999 == 21600",
        "test_sleep_receives_bounded_duration": "Actual: sleep(9999999999)",
    },
}
LIMITS = [
    "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
    "--pids-limit=64", "--memory=512m", "--memory-swap=512m", "--cpus=1",
    "--ulimit=nofile=1024:1024", "--tmpfs=/tmp:rw,nosuid,nodev,size=128m",
]


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def mount(source, target, writable=False):
    return ["--mount", f"type=bind,src={source},dst={target}" + ("" if writable else ",readonly")]


def prepare():
    """Network-enabled preparation; no repository code/build hooks are executed."""
    import ast
    import tarfile

    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    provenance = []
    for image in (PYTHON, REDIS):
        command(["docker", "pull", image])
    for candidate, (repo, before, after) in REVISIONS.items():
        bare = cache / (repo.split("/")[0] + ".git")
        if not bare.exists():
            command(["git", "init", "--bare", "--quiet", str(bare)])
        git = ["git", "-c", "core.hooksPath=/dev/null", f"--git-dir={bare}"]
        command(git + ["fetch", "--quiet", "--depth=2", f"https://github.com/{repo}.git", after])
        parent = command(git + ["rev-parse", after + "^"], capture_output=True).stdout.strip()
        assert parent == before, (parent, before)
        record = {"candidate": candidate, "before": before, "after": after, "actual_first_parent": parent}
        record["changed_files"] = command(git + ["diff", "--name-only", before, after], capture_output=True).stdout.splitlines()
        record["license_sha256"] = {}
        for revision in (before, after):
            archive = cache / (revision + ".tar")
            command(git + ["archive", "-o", str(archive), revision])
            destination = cache / revision
            destination.mkdir(exist_ok=True)
            with tarfile.open(archive) as source:
                source.extractall(destination, filter="data")
            archive.unlink()
            license_name = "LICENSE" if repo == "rq/rq" else "LICENSE.txt"
            license_text = (destination / license_name).read_bytes()
            assert license_text == (ROOT / "licenses" / (repo.split("/")[0] + "-" + license_name)).read_bytes()
            record["license_sha256"][revision] = hashlib.sha256(license_text).hexdigest()
        if candidate == "rq-2515":
            def method(path, name):
                return next(node for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.FunctionDef) and node.name == name)

            name = "test_enqueue_many_with_generator"
            upstream = method(cache / after / "tests/test_queue.py", name)
            copied = method(ROOT / "evaluator" / candidate / "check.py", name)
            assert ast.dump(upstream) == ast.dump(copied)
            for name in ("test_enqueue_many_internal_pipeline", "test_enqueue_many_with_passed_pipeline"):
                assert ast.dump(method(cache / before / "tests/test_queue.py", name)) == ast.dump(method(cache / after / "tests/test_queue.py", name))
            record["upstream_regression_ast_identical"] = True
            record["upstream_controls_ast_identical_between_commits"] = True
        provenance.append(record)
    deps = cache / "deps"
    deps.mkdir(exist_ok=True)
    requirement_hash = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    receipt = deps / "requirements.sha256"
    if not receipt.exists() or receipt.read_text().strip() != requirement_hash:
        uid = os.getuid()
        if uid == 0:
            raise RuntimeError("Run preparation as a non-root host user.")
        command([
            "docker", "run", "--rm", "--network=bridge", *LIMITS, "--user", f"{uid}:{os.getgid()}", "--env=HOME=/tmp",
            *mount(ROOT / "requirements.txt", "/requirements.txt"),
            *mount(deps, "/deps", writable=True), PYTHON, "python", "-I", "-m", "pip",
            "--isolated", "install", "--disable-pip-version-check", "--no-cache-dir",
            "--only-binary=:all:", "--no-deps", "--upgrade", "--target=/deps", "--report=/deps/install-report.json",
            "-r", "/requirements.txt",
        ])
        receipt.write_text(requirement_hash + "\n")
    report = json.loads((deps / "install-report.json").read_text())
    for package in report["install"]:
        package["metadata"] = {
            key: value for key, value in package["metadata"].items()
            if key in ("name", "version", "requires_python", "requires_dist", "license", "license_expression")
        }
    report["metadata_filtered"] = "Descriptions, author/contact fields, and unrelated package metadata omitted."
    (ROOT / "evaluator/dependency-install-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (ROOT / "evaluator/provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def run(candidate, phase):
    repo, before, after = REVISIONS[candidate]
    revision = before if phase == "before" else after
    source = ROOT / ".cache" / revision
    check = ROOT / "evaluator" / candidate / "check.py"
    if not source.is_dir() or not (ROOT / ".cache/deps/install-report.json").exists():
        raise RuntimeError("Missing sources/dependencies. Run prepare first.")
    assert (ROOT / ".cache/deps/requirements.sha256").read_text().strip() == hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest(), "Dependencies changed; run prepare."
    output = ROOT / "evaluator" / candidate / "logs"
    output.mkdir(exist_ok=True)
    name = "skillops-candidates-" + uuid.uuid4().hex[:12]
    redis_name = name + "-redis"
    redis_started = False
    try:
        network = "none"
        if repo == "rq/rq":
            command([
                "docker", "run", "-d", "--name", redis_name, "--network=none", *LIMITS,
                "--user=65534:65534", "--tmpfs=/data:rw,nosuid,nodev,size=32m",
                "--entrypoint=redis-server", REDIS, "--bind", "127.0.0.1",
                "--save", "", "--appendonly", "no", "--maxmemory", "64mb",
            ], capture_output=True)
            redis_started = True
            command(["docker", "exec", redis_name, "redis-cli", "ping"], capture_output=True)
            network = "container:" + redis_name
        args = [
            "docker", "run", "--rm", "--name", name, "--network", network, *LIMITS,
            "--user=65534:65534", "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "--env=PYTHONPATH=/src:/src/src:/deps",
            *mount(source, "/src"), *mount(ROOT / ".cache/deps", "/deps"),
            *mount(check, "/checks/check.py"), *mount(ROOT / "inside.py", "/runner/inside.py"),
            "--workdir=/tmp", PYTHON, "python", "/runner/inside.py", repo.split("/")[0],
        ]
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        (output / f"{phase}.log").write_text(result.stdout)
        lines = [line.removeprefix("RESULT_JSON ") for line in result.stdout.splitlines() if line.startswith("RESULT_JSON ")]
        evidence = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "revision": revision,
            "check_sha256": hashlib.sha256(check.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256((ROOT / "inside.py").read_bytes()).hexdigest(),
            "requirements_sha256": hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest(),
            "dependency_report_sha256": hashlib.sha256((ROOT / "evaluator/dependency-install-report.json").read_bytes()).hexdigest(),
            "python_image": PYTHON,
            "redis_image": REDIS if redis_started else None,
            "command": args,
            "process_exit_code": result.returncode,
            "result": json.loads(lines[-1]) if lines else None,
        }
        (output / f"{phase}.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(candidate, phase, "exit", result.returncode)
        if not lines:
            raise RuntimeError(f"No test result; environment/setup failure. See {output / (phase + '.log')}")
        tests = evidence["result"]["tests"]
        failed = [test for test in tests if test["outcome"] != "passed"]
        if phase == "before":
            assert result.returncode == 1 and failed, "Expected regression failures"
            assert all(test["outcome"] == "failure" and "AssertionError" in test["detail"] for test in failed), failed
            assert {test["name"] for test in failed} == set(EXPECTED_FAILURES[candidate]), failed
            assert all(EXPECTED_FAILURES[candidate][test["name"]] in test["detail"] for test in failed), failed
        else:
            assert result.returncode == 0 and tests and not failed, tests
    finally:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if redis_started:
            command(["docker", "rm", "-f", redis_name], stdout=subprocess.DEVNULL)


def verify():
    """Validate paired evidence without importing any upstream repository."""
    manifest = json.loads((ROOT / "manifest.json").read_text())
    candidates = {item["id"]: item for item in manifest["candidates"]}
    provenance = {item["candidate"]: item for item in json.loads((ROOT / "evaluator/provenance.json").read_text())}
    for candidate, (_, before, after) in REVISIONS.items():
        records = [
            json.loads((ROOT / "evaluator" / candidate / "logs" / f"{phase}.json").read_text())
            for phase in ("before", "after")
        ]
        old, new = records
        assert (old["revision"], new["revision"]) == (before, after)
        entry = candidates[candidate]
        assert (entry["before_commit"], entry["after_commit"]) == (before, after)
        assert provenance[candidate]["actual_first_parent"] == before
        for phase, record in zip(("before", "after"), records):
            for outcome, count_field in (("passed", "passed"), ("failure", "failed"), ("error", "errors")):
                assert sum(t["outcome"] == outcome for t in record["result"]["tests"]) == entry[phase][count_field]
            assert record["process_exit_code"] == entry[phase]["exit_code"]
            assert (ROOT / entry[phase]["log"]).is_file()
        for field in (
            "check_sha256", "runner_sha256", "requirements_sha256", "dependency_report_sha256",
            "python_image", "redis_image",
        ):
            assert old[field] == new[field], (candidate, field)
        for field, path in (
            ("check_sha256", ROOT / "evaluator" / candidate / "check.py"),
            ("runner_sha256", ROOT / "inside.py"),
            ("requirements_sha256", ROOT / "requirements.txt"),
            ("dependency_report_sha256", ROOT / "evaluator/dependency-install-report.json"),
        ):
            assert new[field] == hashlib.sha256(path.read_bytes()).hexdigest(), (candidate, path)
        assert old["result"]["environment"] == new["result"]["environment"]
        assert (old["process_exit_code"], new["process_exit_code"]) == (1, 0)
        previous = old["result"]["tests"]
        current = new["result"]["tests"]
        assert [(t["class"], t["name"]) for t in previous] == [(t["class"], t["name"]) for t in current]
        failures = [t for t in previous if t["outcome"] != "passed"]
        assert {t["name"] for t in failures} == set(EXPECTED_FAILURES[candidate])
        assert all(t["outcome"] == "failure" and EXPECTED_FAILURES[candidate][t["name"]] in t["detail"] for t in failures)
        assert current and all(t["outcome"] == "passed" for t in current)
        print(f"{candidate}: same checks/environment; {len(failures)} intended failures -> {len(current)} passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "verify"])
    parser.add_argument("--candidate", choices=list(REVISIONS))
    parser.add_argument("--phase", choices=["before", "after"])
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "verify":
        verify()
    else:
        for candidate in [args.candidate] if args.candidate else REVISIONS:
            for phase in [args.phase] if args.phase else ("before", "after"):
                run(candidate, phase)
