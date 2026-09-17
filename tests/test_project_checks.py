from copy import deepcopy
from hashlib import sha256
import json
import os
import socket
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from subprocess import CompletedProcess
from urllib.parse import urlsplit

from copilot_runtime import RuntimeFailure
import project_checks as checks


def observation(cases=None, **changes):
    return {
        "plan_sha256": "a" * 64,
        "environment_sha256": "b" * 64,
        "protected_sha256": "c" * 64,
        "status": "completed",
        "cases": cases if cases is not None else [{"id": "tests/test_api.py::test_list", "status": "passed"}],
        "gates": [{"id": "build", "status": "passed"}],
        "elapsed_seconds": 1.0,
        **changes,
    }


class ProjectChecksTests(unittest.TestCase):
    def test_replay_sources_reject_protected_nested_config_skills_and_hash_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            forbidden = ("tests/test_api.py", "src/package.json", "src/setup.py",
                         "src/requirements-extra.txt", "skills/review/helper.py",
                         "custom/SKILL.md", "custom/helper.py", "skill_pipeline.py",
                         "api_test.py", ".github/workflows/build.py")
            for name in ("api.py", *forbidden):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original\n")
            sources = {"api.py": sha256(b"original\n").hexdigest()}
            self.assertEqual(checks.replay_sources(root, sources), {"api.py": "original\n"})
            for name in forbidden:
                with self.subTest(name=name), self.assertRaises(RuntimeFailure):
                    checks.replay_sources(root, {name: sha256(b"original\n").hexdigest()})
            (root / "api.py").write_text("changed\n")
            with self.assertRaises(RuntimeFailure) as caught:
                checks.replay_sources(root, sources)
            self.assertEqual(caught.exception.code, "work_inputs_changed")

    def test_replay_sources_enforce_bounds_and_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "api.py").write_bytes(b"x" * 65537)
            with self.assertRaises(RuntimeFailure):
                checks.replay_sources(root, {"api.py": sha256(b"x" * 65537).hexdigest()})
            (root / "link.py").symlink_to(root / "api.py")
            with self.assertRaises(RuntimeFailure):
                checks.replay_sources(root, {"link.py": "a" * 64})
            with self.assertRaises(RuntimeFailure):
                checks.replay_sources(root, {})

    def test_replay_task_outcome_distinguishes_failure_from_missing_or_runtime_evidence(self):
        required = {"required_case_ids": ["task"], "required_gate_ids": ["build"]}
        for state, expected in (("passed", "satisfied"), ("failed", "not_satisfied"),
                                ("error", "unverified"), ("skipped", "unverified"),
                                ("expected_failure", "unverified"), (None, "unverified")):
            cases = [] if state is None else [{"id": "task", "status": state}]
            row = observation(cases, status="failed" if state in ("failed", "error") else "completed")
            with self.subTest(state=state):
                self.assertEqual(checks.replay_outcome(row, required), expected)
        row = observation([{"id": "task", "status": "passed"}], status="blocked")
        self.assertEqual(checks.replay_outcome(row, required), "unverified")

    def test_static_dependencies_allow_source_tree_checks_without_running_legacy_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            setup = "raise RuntimeError('setup must never execute')\n"
            (root / "setup.py").write_text(setup)
            (root / "setup.cfg").write_text("[metadata]\nname = fixture\n")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "fixture"\ndynamic = ["version", "authors"]\n'
                'dependencies = ["pytz"]\n[tool.pytest.ini_options]\n')
            manifest = checks.dependency_manifest(root, "python")
            self.assertEqual(manifest, {"requirements.txt": "pytz\npytest\n"})
            self.assertEqual((root / "setup.py").read_text(), setup)
            for dynamic in ('["dependencies"]', '["optional-dependencies"]', '"version"'):
                (root / "pyproject.toml").write_text(f'[project]\ndependencies = []\ndynamic = {dynamic}\n')
                with self.subTest(dynamic=dynamic), self.assertRaises(RuntimeFailure):
                    checks.dependency_manifest(root, "python")
            (root / "pyproject.toml").unlink()
            with self.assertRaises(RuntimeFailure):
                checks.dependency_manifest(root, "python")

    def test_pytest_source_hints_are_inspected_without_imports(self):
        for source in ("import pytest\nraise RuntimeError('do not execute')\n",
                       "from pytest import fixture\n",
                       "def test_behavior(): assert True\n"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "test_api.py").write_text(source)
                self.assertEqual(checks.discover(root)["checks"][0]["runner"], "pytest")
                self.assertIn("pytest", checks.dependency_manifest(root, "python")["requirements.txt"])

    def test_mixed_checks_run_supported_python_but_preserve_unexecuted_harnesses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests/server").mkdir(parents=True)
            (root / "tests/test_api.py").write_text("import pytest\n")
            (root / "tests/test_shell.sh").write_text("exit 0\n")
            (root / "tests/server/package.json").write_text(json.dumps({
                "scripts": {"test": "node custom.js && bash wrapper.sh"}}))
            plan = checks.discover(root)
            self.assertEqual(plan["status"], "partial")
            self.assertEqual(plan["checks"][0]["runner"], "pytest")
            self.assertEqual({item["path"] for item in plan["exclusions"]},
                             {"tests/test_shell.sh", "tests/server/package.json"})
            output = CompletedProcess([], 0, json.dumps({
                "status": "completed", "cases": [{"id": "test_api.py::test_one", "status": "passed"}]}), "")
            with patch.object(checks, "container_capture", return_value=output) as execute:
                observed = checks.execute(root, plan, {"python": "sha256:" + "a" * 64})
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(observed["status"], "blocked")
            self.assertEqual(observed["cases"][0]["status"], "passed")
            self.assertEqual(len(observed["gates"]), 2)
            self.assertTrue(all(gate["status"] == "error" for gate in observed["gates"]))
            self.assertTrue(any("tests/server/package.json" in gate["id"] for gate in observed["gates"]))
            self.assertEqual(checks.compare(observed, observed, observed)["status"], "unverified")

    def test_zero_collected_cases_are_blocked_even_if_the_runner_returns_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "test_api.py").write_text("import unittest\n")
            output = CompletedProcess([], 0, '{"status":"completed","cases":[]}', "")
            with patch.object(checks, "container_capture", return_value=output):
                result = checks.execute(root, checks.discover(root), {"python": "sha256:" + "a" * 64})
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["cases"], [])

    def test_proxy_closes_an_incomplete_client_when_preparation_is_cancelled(self):
        entered = threading.Event()
        parse = checks.BaseHTTPRequestHandler.parse_request
        def parse_request(handler):
            entered.set()
            return parse(handler)
        with patch.object(checks.BaseHTTPRequestHandler, "parse_request", parse_request), patch.object(
                checks.socket, "getaddrinfo", return_value=[]) as resolve:
            client = None
            try:
                with checks.registry_proxy("127.0.0.1", checks.time.monotonic() + 20) as address:
                    port = urlsplit(address).port
                    client = socket.socket()
                    client.settimeout(2)
                    client.connect(("127.0.0.1", port))
                    client.sendall(b"CONNECT registry.npmjs.org:443 HTTP/1.1\r\n")
                    self.assertTrue(entered.wait(2))
                self.assertEqual(client.recv(1), b"")
                resolve.assert_not_called()
            finally:
                if client is not None:
                    client.close()

    def test_expired_shared_deadline_never_starts_a_container(self):
        with patch.object(checks, "container_capture") as run:
            with self.assertRaises(RuntimeFailure) as error:
                checks.execute(Path("."), {}, {}, deadline=checks.time.monotonic() - 1)
            self.assertEqual(error.exception.code, "time_limit")
            run.assert_not_called()

    def test_failed_image_inspection_cleans_the_already_built_owned_tag(self):
        calls = []
        def capture(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["docker", "network", "inspect"]:
                return CompletedProcess(argv, 0, "172.30.0.1\n", "")
            if argv[:3] == ["docker", "image", "inspect"]:
                return CompletedProcess(argv, 1, "", "inspection failed")
            return CompletedProcess(argv, 0, "", "")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "requirements.txt").write_text("pytest==8.3.5\n")
            with patch.object(checks, "capture", side_effect=capture), patch.object(
                    checks, "registry_proxy", return_value=nullcontext("http://172.30.0.1:1234")):
                with self.assertRaises(RuntimeFailure):
                    with checks.prepared_images(root, {"python": "sha256:" + "a" * 64}):
                        self.fail("Preparation must fail before yielding an image.")
        built = next(argv[argv.index("--tag") + 1] for argv in calls if argv[:2] == ["docker", "build"])
        self.assertIn(["docker", "image", "rm", built], calls)

    def test_dependency_preparation_rejects_remote_code_and_unsupported_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for requirement in ("git+https://example.invalid/code", "-r /etc/passwd", "local @ file:///etc/passwd"):
                (root / "requirements.txt").write_text(requirement)
                with self.subTest(requirement=requirement), self.assertRaises(RuntimeFailure):
                    checks.dependency_manifest(root, "python")
            (root / "requirements.txt").write_text("pytest==8.3.5\n")
            self.assertIn("pytest==8.3.5", checks.dependency_manifest(root, "python")["requirements.txt"])
            (root / "package.json").write_text(json.dumps({"dependencies": {"unsafe": "file:../outside"}}))
            with self.assertRaises(RuntimeFailure):
                checks.dependency_manifest(root, "node")

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "container opt-in required")
    def test_prepared_pytest_jest_and_vitest_collect_real_checks_without_install_hooks(self):
        cases = [
            ("python", {"requirements.txt": "pytest==8.3.5\n",
                        "test_api.py": "def test_pass(): assert True\ndef test_fail(): assert False\n"}),
            ("node", {"package.json": json.dumps({
                "name": "jest-fixture", "version": "1.0.0",
                "scripts": {"test": "jest --runInBand", "postinstall": "node -e \"process.exit(73)\""},
                "devDependencies": {"jest": "30.2.0"}}),
                "api.test.js": "test('pass',()=>expect(1).toBe(1)); test('fail',()=>expect(1).toBe(2));"}),
            ("node", {"package.json": json.dumps({
                "name": "vitest-fixture", "version": "1.0.0", "type": "module",
                "scripts": {"test": "vitest run --maxWorkers=1 --no-file-parallelism"},
                "devDependencies": {"vitest": "3.2.4"}}),
                "api.test.js": "import {test,expect} from 'vitest'; test('pass',()=>expect(1).toBe(1)); test('fail',()=>expect(1).toBe(2));"}),
        ]
        for language, files in cases:
            with self.subTest(language=language, files=list(files)), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                for name, content in files.items():
                    (root / name).write_text(content)
                tag = "python:3.12-slim" if language == "python" else "node:22-slim"
                image = checks.capture(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
                self.assertTrue(hasattr(checks, "prepared_images"), "dependency preparation is missing")
                with checks.prepared_images(root, {language: image}, timeout=180) as images:
                    self.assertNotEqual(image, images[language])
                    result = checks.execute(root, checks.discover(root), images)
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(sorted(row["status"] for row in result["cases"]), ["failed", "passed"])
                self.assertEqual(checks.capture(["docker", "image", "inspect", images[language]]).returncode, 1)
                self.assertFalse((root / "node_modules").exists())
                self.assertFalse((root / "__pycache__").exists())

    def test_discovers_existing_python_checks_without_executing_project_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/test_api.py").write_text("raise RuntimeError('must not import during discovery')")
            plan = checks.discover(root)
            self.assertEqual(plan["checks"], [{"id": "python-tests", "runner": "unittest",
                                              "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}])
            (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
            self.assertEqual(checks.discover(root)["checks"][0]["runner"], "pytest")

    def test_recognizes_node_runners_but_not_opaque_wrappers_or_hooks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for command, runner in (("node --test", "node-test"), ("jest --ci", "jest"),
                                    ("vitest run", "vitest"), ("echo ok", None),
                                    ("jest && echo ok", None), ("npx jest", None)):
                with self.subTest(command=command):
                    package = {"scripts": {"test": command}}
                    (root / "package.json").write_text(json.dumps(package))
                    plan = checks.discover(root)
                    if runner:
                        self.assertEqual(plan["checks"][0]["runner"], runner)
                    else:
                        self.assertEqual(plan["status"], "unsupported")
            package["scripts"] = {"pretest": "node prepare.js", "test": "jest"}
            (root / "package.json").write_text(json.dumps(package))
            self.assertEqual(checks.discover(root)["status"], "unsupported")

    def test_discovery_missing_checks_or_unsafe_inputs_are_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(checks.discover(root)["status"], "missing")
            (root / "package.json").symlink_to(root / "elsewhere")
            with self.assertRaises(RuntimeFailure):
                checks.discover(root)

    def test_discovery_commits_config_and_preserves_declared_gate_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = {"scripts": {"test": "node --test", "build": "tsc -p tsconfig.json"}}
            (root / "package.json").write_text(json.dumps(package))
            before = checks.discover(root)
            self.assertEqual(before["gates"], [{"id": "build", "argv": ["npm", "run", "build"]}])
            (root / "package-lock.json").write_text('{"lockfileVersion":3}')
            self.assertNotEqual(before["sha256"], checks.discover(root)["sha256"])

    def test_identical_completed_populations_have_scoped_non_regression(self):
        result = checks.compare(observation(), observation(), observation())
        self.assertEqual(result, {"status": "passed", "reasons": [], "regressions": []})

    def test_new_failures_skips_and_expected_failures_are_regressions(self):
        for status in ("failed", "error", "skipped", "expected_failure"):
            with self.subTest(status=status):
                changed = observation([{"id": "tests/test_api.py::test_list", "status": status}],
                                      status="failed" if status in ("failed", "error") else "completed")
                result = checks.compare(observation(), observation(), changed)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("test:tests/test_api.py::test_list", result["regressions"])

    def test_both_arms_introducing_same_failure_is_still_regression(self):
        failed = observation([{"id": "tests/test_api.py::test_list", "status": "failed"}], status="failed")
        self.assertEqual(checks.compare(observation(), failed, failed)["status"], "rejected")

    def test_inherited_failure_is_not_reported_as_new(self):
        rows = [{"id": "passing", "status": "passed"}, {"id": "inherited", "status": "failed"}]
        failed = observation(rows, status="failed")
        self.assertEqual(checks.compare(failed, failed, failed)["status"], "passed")

    def test_candidate_cannot_lose_a_baseline_fix(self):
        broken = observation([{"id": "tests/test_api.py::test_list", "status": "failed"}], status="failed")
        self.assertEqual(checks.compare(broken, observation(), broken)["status"], "rejected")

    def test_equal_counts_do_not_hide_changed_or_missing_test_identity(self):
        for rows in ([{"id": "different", "status": "passed"}], []):
            with self.subTest(rows=rows):
                self.assertEqual(checks.compare(observation(), observation(), observation(rows))["status"], "rejected")

    def test_input_drift_never_qualifies(self):
        for key in ("plan_sha256", "environment_sha256", "protected_sha256"):
            with self.subTest(key=key):
                result = checks.compare(observation(), observation(), observation(**{key: "d" * 64}))
                self.assertEqual(result["status"], "unverified")
                self.assertIn("input_mismatch", result["reasons"])

    def test_missing_zero_and_only_skipped_coverage_are_unverified(self):
        for value in (None, observation([]), observation([{"id": "skipped", "status": "skipped"}])):
            with self.subTest(value=value):
                self.assertEqual(checks.compare(value, value, value)["status"], "unverified")

    def test_incomplete_run_preserves_observed_regressions(self):
        changed = observation([{"id": "tests/test_api.py::test_list", "status": "failed"}], status="blocked")
        result = checks.compare(observation(), observation(), changed)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("incomplete_checks", result["reasons"])

    def test_failed_required_gate_is_not_hidden_by_green_tests(self):
        changed = observation(gates=[{"id": "build", "status": "failed"}], status="failed")
        self.assertEqual(checks.compare(observation(), observation(), changed)["regressions"], ["gate:build"])

    def test_observation_contract_rejects_ambiguous_or_success_shaped_results(self):
        mutations = [
            lambda row: row["cases"].append(deepcopy(row["cases"][0])),
            lambda row: row.update(elapsed_seconds=float("nan")),
            lambda row: row.update(elapsed_seconds=True),
            lambda row: row.update(status="success"),
            lambda row: row.update(environment_sha256=None),
            lambda row: row["cases"][0].update(status="failed"),
            lambda row: row.update(secret="not allowed"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                row = observation()
                mutation(row)
                with self.assertRaises(RuntimeFailure):
                    checks.validate_observation(row)

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "opt-in real Docker check")
    def test_actual_unittest_container_preserves_case_identities_and_limits(self):
        from copilot_runtime import capture
        image = capture(["docker", "image", "inspect", "python:3.12-slim", "--format", "{{.Id}}"]).stdout.strip()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/test_api.py").write_text(
                "import os, unittest\n"
                "class Cases(unittest.TestCase):\n"
                " def test_pass(self): self.assertNotIn('COPILOT_GITHUB_TOKEN', os.environ)\n"
                " def test_failure(self): self.assertEqual(1, 2)\n"
                " @unittest.skip('existing skip')\n"
                " def test_skip(self): pass\n"
            )
            plan = checks.discover(root)
            result = checks.execute(root, plan, {"python": image})
            self.assertEqual(result["status"], "failed")
            self.assertEqual({row["id"]: row["status"] for row in result["cases"]}, {
                "python-tests:test_api.Cases.test_failure": "failed",
                "python-tests:test_api.Cases.test_pass": "passed",
                "python-tests:test_api.Cases.test_skip": "skipped",
            })
            self.assertEqual(list(root.iterdir()), [root / "tests"])
            (root / "tests/test_api.py").write_text(
                "import pathlib, unittest\n"
                "class Cases(unittest.TestCase):\n"
                " def test_mutate(self): pathlib.Path(__file__).write_text('# removed tests')\n"
            )
            with self.assertRaises(RuntimeFailure) as caught:
                checks.execute(root, checks.discover(root), {"python": image})
            self.assertEqual(caught.exception.code, "check_runtime_error")

    def test_container_command_is_bounded_and_timeout_cleans_owned_container(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def run(args, **kwargs):
                if args[:2] == ["docker", "run"]:
                    self.assertIn("--network=none", args)
                    self.assertIn("--cap-drop=ALL", args)
                    self.assertIn("--pids-limit=64", args)
                    self.assertIn("--memory=512m", args)
                    self.assertIn("--user=65534:65534", args)
                    self.assertNotIn("COPILOT_GITHUB_TOKEN", " ".join(args))
                    self.assertEqual(kwargs["limit"], 1024 * 1024)
                    raise RuntimeFailure("timeout", "test timeout")
                from subprocess import CompletedProcess
                return CompletedProcess(args, 0, "", "")
            with patch.object(checks, "capture", side_effect=run) as invoked:
                with self.assertRaises(RuntimeFailure):
                    checks.container_capture(root, root, "sha256:" + "a" * 64, ["python", "-V"], timeout=1)
                commands = [call.args[0] for call in invoked.call_args_list]
                self.assertEqual(commands[-1][:3], ["docker", "rm", "--force"])
                self.assertTrue(commands[-1][-1].startswith("skillops-check-"))

    def test_runner_rejects_changed_plan_and_missing_image_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/test_api.py").write_text("import unittest")
            plan = checks.discover(root)
            with patch.object(checks, "container_capture") as run:
                with self.assertRaises(RuntimeFailure):
                    checks.execute(root, plan, {})
                changed = deepcopy(plan)
                changed["checks"][0]["argv"].append("--unsafe")
                with self.assertRaises(RuntimeFailure):
                    checks.execute(root, changed, {"python": "sha256:" + "a" * 64})
                run.assert_not_called()

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "opt-in real Docker check")
    def test_actual_node_container_reports_cases_and_build_gate(self):
        from copilot_runtime import capture
        inspected = capture(["docker", "image", "inspect", "node:22-slim", "--format", "{{.Id}}"])
        self.assertEqual(inspected.returncode, 0, "Node image is missing; prepare node:22-slim explicitly.")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.json").write_text(json.dumps({"scripts": {
                "test": "node --test", "build": "node -e \"process.exit(0)\"",
            }}))
            (root / "api.test.cjs").write_text(
                "const test = require('node:test'); const assert = require('node:assert/strict');\n"
                "test('pass', () => assert.equal(process.env.COPILOT_GITHUB_TOKEN, undefined));\n"
                "test('fail', () => assert.equal(1, 2));\n"
                "test.skip('skip', () => {});\n"
            )
            result = checks.execute(root, checks.discover(root), {"node": inspected.stdout.strip()})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(sorted(row["status"] for row in result["cases"]), ["failed", "passed", "skipped"])
            self.assertEqual(result["gates"], [{"id": "build", "status": "passed"}])


if __name__ == "__main__":
    unittest.main()
