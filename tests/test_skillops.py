import importlib
import importlib.util
from copy import deepcopy
from decimal import Decimal
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("copilot_runtime"),
            "The approved Copilot runtime has not been implemented.",
        )
        self.runtime = importlib.import_module("copilot_runtime")
        self.enterContext(patch.object(self.runtime.shutil, "which", return_value="/test-only/copilot"))

    def test_runtime_rejects_missing_cli_before_creating_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.runtime.shutil, "which", return_value=None):
                with self.assertRaises(self.runtime.RuntimeFailure) as caught:
                    self.runtime.CopilotRuntime(Path(directory))
            self.assertEqual(caught.exception.code, "missing_cli")
            self.assertFalse((Path(directory) / ".skillops-private").exists())

    def test_private_profile_filters_inherited_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory), {
                "PATH": os.environ["PATH"],
                "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "/unexpected",
                "COPILOT_ALLOW_ALL": "true",
                "COPILOT_PROVIDER_BASE_URL": "https://unexpected.invalid",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://unexpected.invalid",
                "COPILOT_GITHUB_TOKEN": "dummy-auth-value",
            })
            self.assertEqual(runtime.home.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("COPILOT_ALLOW_ALL", runtime.env)
            self.assertNotIn("COPILOT_PROVIDER_BASE_URL", runtime.env)
            self.assertNotIn("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", runtime.env)
            self.assertNotIn("OTEL_EXPORTER_OTLP_ENDPOINT", runtime.env)
            self.assertEqual(runtime.env["COPILOT_GITHUB_TOKEN"], "dummy-auth-value")
            self.assertNotIn("dummy-auth-value", runtime.login_command())
            self.assertIn(str(runtime.home), runtime.login_command())

    def test_refuses_unowned_private_directory_and_symlinks(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                private = project / ".skillops-private"
                if symlink:
                    private.symlink_to(project, target_is_directory=True)
                else:
                    private.mkdir()
                with self.assertRaises(self.runtime.RuntimeFailure):
                    self.runtime.CopilotRuntime(project)

    def test_provider_registry_blocks_without_disclosing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory))
            (runtime.config / "providers.json").write_text('{"secret":"do-not-print"}')
            with self.assertRaises(self.runtime.RuntimeFailure) as caught:
                runtime.check_profile()
            self.assertEqual(caught.exception.code, "alternate_provider")
            self.assertNotIn("do-not-print", str(caught.exception))

    def test_profile_lock_rejects_concurrent_use(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.runtime.CopilotRuntime(Path(directory))
            second = self.runtime.CopilotRuntime(Path(directory))
            with first.locked():
                with self.assertRaises(self.runtime.RuntimeFailure) as caught:
                    with second.locked():
                        self.fail("Concurrent profile access must not be allowed.")
                self.assertEqual(caught.exception.code, "profile_busy")

    def test_judge_command_has_no_tools_or_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory))
            command = runtime.model_command("synthetic prompt", "gpt-6-astra", "judge")
            self.assertIn("--available-tools=skill", command)
            self.assertIn("--excluded-tools=skill", command)
            self.assertNotIn("--no-auto-login", command)
            self.assertIn("--no-custom-instructions", command)
            self.assertIn("--disable-builtin-mcps", command)
            self.assertNotIn("--allow-all", command)
            self.assertNotIn("--resume", command)
            self.assertNotIn("--continue", command)
            developer = runtime.model_command("synthetic prompt", "gpt-6-astra", "developer")
            self.assertIn("--available-tools=skill", developer)
            self.assertNotIn("--excluded-tools=skill", developer)
            with self.assertRaises(self.runtime.RuntimeFailure):
                runtime.model_command("prompt", "gpt-6-astra", "unknown")

    def test_generator_uses_zero_tool_command_and_rejects_tool_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory))
            try:
                command = runtime.model_command("prompt", "gpt-6-astra", "generator")
            except self.runtime.RuntimeFailure:
                self.fail("The explicit zero-tool generator role is missing.")
            self.assertIn("--excluded-tools=skill", command)
            fixture = Path(__file__).resolve().parent / "fixtures/cli-contract.json"
            events = json.loads(fixture.read_text())["judge"]
            result = self.runtime.parse_events("\n".join(map(json.dumps, events)), "gpt-6-astra", "generator")
            self.assertFalse(result["skill_activated"])
            events.insert(1, {"type": "tool.execution_start", "data": {
                "toolName": "skill", "toolCallId": "unexpected", "arguments": {"skill": "develop"},
            }})
            with self.assertRaises(self.runtime.RuntimeFailure) as caught:
                self.runtime.parse_events("\n".join(map(json.dumps, events)), "gpt-6-astra", "generator")
            self.assertEqual(caught.exception.code, "tool_execution")

    def test_staging_uses_explicit_immutable_skill_bytes(self):
        import inspect
        import skillops
        self.assertIn("skill_bytes", inspect.signature(skillops.stage_repository).parameters)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self.runtime.CopilotRuntime(root)
            work = root / "work"
            work.mkdir()
            expected = b"---\nname: develop\n---\nPinned instructions.\n"
            _, staged = skillops.stage_repository(runtime, work, "pass\n", expected)
            self.assertEqual(staged.read_bytes(), expected)

    def test_skill_selection_requires_exact_source_and_unique_name(self):
        expected = Path("/owned/.github/skills/develop/SKILL.md")
        rows = [
            {"name": "develop", "path": str(expected.parent), "enabled": True},
            {"name": "other", "path": "/other", "enabled": True},
        ]
        self.assertEqual(self.runtime.disabled_skills(rows, expected), ["other"])
        self.assertEqual(self.runtime.disabled_skills(rows, None), ["develop", "other"])
        for bad in (rows[:1] * 2, [dict(rows[0], path="/wrong")], []):
            with self.subTest(rows=bad), self.assertRaises(self.runtime.RuntimeFailure):
                self.runtime.disabled_skills(bad, expected)

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(text=text), self.assertRaises(self.runtime.RuntimeFailure):
                self.runtime.strict_json(text)
        self.assertEqual(self.runtime.strict_json('{"x":1}'), {"x": 1})

    def test_bounded_capture_preserves_exit_status(self):
        result = self.runtime.capture(
            [sys.executable, "-c", "import sys;print('ok');sys.stderr.write('err');sys.exit(3)"],
            timeout=2,
            limit=1024,
        )
        self.assertEqual((result.returncode, result.stdout.strip(), result.stderr), (3, "ok", "err"))

    def test_bounded_capture_stops_output_flood(self):
        with self.assertRaises(self.runtime.RuntimeFailure) as caught:
            self.runtime.capture(
                [sys.executable, "-c", "import os\nwhile True: os.write(1,b'x'*8192)"],
                timeout=2,
                limit=4096,
            )
        self.assertEqual(caught.exception.code, "output_limit_exceeded")
        self.assertLessEqual(len(str(caught.exception)), 1024)

    def test_bounded_capture_stops_timeout(self):
        with self.assertRaises(self.runtime.RuntimeFailure) as caught:
            self.runtime.capture(
                [sys.executable, "-c", "import time;time.sleep(5)"],
                timeout=0.1,
                limit=1024,
            )
        self.assertEqual(caught.exception.code, "timeout")

    def test_diagnostics_redact_credentials(self):
        text = "dummy-secret github_pat_abcdefghijk https://name:password@example.invalid"
        redacted = self.runtime.redact(text, {"GH_TOKEN": "dummy-secret"})
        self.assertNotIn("dummy-secret", redacted)
        self.assertNotIn("github_pat_abcdefghijk", redacted)
        self.assertNotIn("password", redacted)

    def test_role_configuration_rejects_plugins_and_mcp(self):
        for unexpected in ("plugin", "mcp"):
            with self.subTest(unexpected=unexpected), tempfile.TemporaryDirectory() as directory:
                runtime = self.runtime.CopilotRuntime(Path(directory))
                inventories = {
                    "skill": [],
                    "instruction": [],
                    "plugin": [{"name": "unexpected"}] if unexpected == "plugin" else [],
                    "mcp": {"mcpServers": {"unexpected": {}} if unexpected == "mcp" else {}},
                }
                with patch.object(runtime, "inventory", side_effect=lambda work, kind: inventories[kind]):
                    with self.assertRaises(self.runtime.RuntimeFailure):
                        runtime.configure(Path(directory))

    def test_cli_entrypoint_exposes_preflight_and_probe(self):
        entrypoint = Path(__file__).resolve().parents[1] / "skillops.py"
        self.assertTrue(entrypoint.is_file(), "The SkillOps entrypoint is missing.")
        result = self.runtime.capture([sys.executable, str(entrypoint), "--help"], timeout=2)
        self.assertEqual(result.returncode, 0)
        self.assertIn("doctor", result.stdout)
        self.assertIn("probe", result.stdout)

    def test_capture_kills_descendant_even_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            code = (
                "import os,sys,time,signal\n"
                "if os.fork(): os._exit(0)\n"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                "open(sys.argv[1],'w').write(str(os.getpid()))\n"
                "time.sleep(10)\n"
            )
            pid = None
            try:
                with self.assertRaises(self.runtime.RuntimeFailure):
                    self.runtime.capture([sys.executable, "-c", code, str(pidfile)], timeout=0.3)
                pid = int(pidfile.read_text())
                import time
                time.sleep(0.05)
                status = Path(f"/proc/{pid}/status")
                if status.exists():
                    self.assertIn("State:\tZ", status.read_text(), "Descendant is still running.")
            finally:
                if pid is not None:
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass

    def test_runtime_parser_rejects_incomplete_or_exposed_tool_contract(self):
        self.assertTrue(hasattr(self.runtime, "parse_events"), "Live CLI parser is missing.")
        events = [
            {"type": "model.call_start", "data": {"model": "gpt-6-astra"}},
            {"type": "assistant.message", "data": {
                "model": "gpt-6-astra", "phase": "final_answer", "content": '{"ok":true}', "toolRequests": [],
            }},
            {"type": "session.usage_checkpoint", "data": {"promptCacheBreakState": [
                {"models": {"gpt-6-astra": {"model": "gpt-6-astra", "tool_count": 0, "tools": [], "tools_truncated": 0}}},
            ]}},
            {"type": "result", "sessionId": "synthetic-parser-fixture", "exitCode": 0},
        ]
        text = "\n".join(json.dumps(row) for row in events)
        self.assertEqual(self.runtime.parse_events(text, "gpt-6-astra", "judge")["content"], '{"ok":true}')
        with self.assertRaises(self.runtime.RuntimeFailure):
            self.runtime.parse_events("\n".join(text.splitlines()[:-1]), "gpt-6-astra", "judge")
        model = events[2]["data"]["promptCacheBreakState"][0]["models"]["gpt-6-astra"]
        model.update(tool_count=1, tools=[{"name": "bash"}])
        with self.assertRaises(self.runtime.RuntimeFailure):
            self.runtime.parse_events("\n".join(json.dumps(row) for row in events), "gpt-6-astra", "judge")

    def test_developer_requires_completed_target_skill_invocation(self):
        root = Path(__file__).resolve().parents[1]
        fixture_path = root / "tests/fixtures/cli-contract.json"
        self.assertTrue(fixture_path.is_file(), "Sanitized live contract fixture is missing.")
        fixture = json.loads(fixture_path.read_text())
        events = fixture["developer"]
        parsed = self.runtime.parse_events("\n".join(map(json.dumps, events)), "gpt-6-astra", "developer")
        self.assertTrue(parsed["skill_activated"])
        events = [e for e in events if e["type"] != "tool.execution_complete"]
        with self.assertRaises(self.runtime.RuntimeFailure):
            self.runtime.parse_events("\n".join(map(json.dumps, events)), "gpt-6-astra", "developer")

    def test_usage_preserves_units_and_missing_values(self):
        self.assertTrue(hasattr(self.runtime, "usage_metrics"), "Usage parser is missing.")
        metrics = self.runtime.usage_metrics({"currentModel": "gpt-6-astra", "totalNanoAiu": 123}, "gpt-6-astra")
        self.assertEqual(metrics["nano_aiu"]["value"], 123)
        self.assertIsNone(metrics["premium_request_cost"]["value"])
        self.assertTrue(metrics["premium_request_cost"]["reason"])
        with self.assertRaises(self.runtime.RuntimeFailure):
            self.runtime.usage_metrics({"currentModel": "gpt-6-astra", "totalNanoAiu": True}, "gpt-6-astra")
        for value in ({"modelMetrics": []}, {"modelMetrics": {"gpt-6-astra": []}},
                      {"modelMetrics": {"gpt-6-astra": {"usage": []}}}):
            with self.subTest(value=value), self.assertRaises(self.runtime.RuntimeFailure):
                self.runtime.usage_metrics(value, "gpt-6-astra")

    def test_invocation_collision_precedes_any_cli_call(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory))
            artifact = Path(directory) / "existing.json"
            artifact.write_text("original")
            with patch.object(runtime, "configure") as configure, patch.object(self.runtime, "capture") as capture:
                with self.assertRaises(self.runtime.RuntimeFailure) as caught:
                    runtime.invoke("prompt", "gpt-6-astra", "judge", Path(directory), artifact)
                self.assertEqual(caught.exception.code, "artifact_exists")
                configure.assert_not_called()
                capture.assert_not_called()
            self.assertEqual(artifact.read_text(), "original")

    def test_invocation_timeout_persists_failure_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory))
            artifact = Path(directory) / "call.json"
            with patch.object(runtime, "configure", return_value={}), patch.object(
                self.runtime, "capture", side_effect=self.runtime.RuntimeFailure("timeout", "Timed out.")
            ):
                with self.assertRaises(self.runtime.RuntimeFailure):
                    runtime.invoke("prompt", "gpt-6-astra", "judge", Path(directory), artifact)
            self.assertEqual(json.loads(artifact.read_text())["error"]["code"], "timeout")

    def test_malformed_nested_events_persist_terminal_failure_receipts(self):
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root / "tests/fixtures/cli-contract.json").read_text())
        variants = [
            None, {}, [None], [{"models": []}], [{"models": {"gpt-6-astra": None}}],
        ]
        for change in ({"tools": None}, {"tools": [None]}, {"tool_count": False}, {"tools_truncated": False}):
            manifest = {"model": "gpt-6-astra", "tool_count": 0, "tools": [], "tools_truncated": 0, **change}
            variants.append([{"models": {"gpt-6-astra": manifest}}])
        malformed = []
        for states in variants:
            events = deepcopy(fixture["judge"])
            for event in events:
                if event["type"] == "session.usage_checkpoint":
                    event["data"]["promptCacheBreakState"] = states
            malformed.append(("judge", events))
        for kind in ("tool.execution_start", "tool.execution_complete"):
            events = deepcopy(fixture["developer"])
            for event in events:
                if event["type"] == kind:
                    event["data"]["toolCallId"] = []
            malformed.append(("developer", events))
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime.CopilotRuntime(Path(directory))
            for index, (role, events) in enumerate(malformed):
                artifact = Path(directory) / f"case-{index}.json"
                result = self.runtime.subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
                with self.subTest(index=index), patch.object(runtime, "configure", return_value={}), patch.object(
                    self.runtime, "capture", return_value=result
                ):
                    try:
                        runtime.invoke("Synthetic malformed input.", "gpt-6-astra", role, Path(directory), artifact)
                    except self.runtime.RuntimeFailure:
                        pass
                    except (AttributeError, TypeError) as error:
                        self.fail(f"Malformed input escaped the RuntimeFailure boundary: {type(error).__name__}")
                    else:
                        self.fail("Malformed nested event was accepted.")
                    receipt = json.loads(artifact.read_text())
                    self.assertEqual(receipt["status"], "contract_error")
                    self.assertIn(receipt["error"]["code"], ("invalid_events", "tool_exposure"))

    def test_baseline_assets_have_family_disjoint_tasks_and_reasonable_skill(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("sample_repo/issues.py", "skills/develop/SKILL.md", "eval/tasks.json", "eval/rubric.json", "eval/calibration.json", "eval/fixed_checks.py"):
            self.assertTrue((root / name).is_file(), f"Missing baseline asset: {name}")
        data = json.loads((root / "eval/tasks.json").read_text())
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual([task["split"] for task in data["tasks"]], ["development"] * 4 + ["heldout"])
        self.assertEqual(len({task["id"] for task in data["tasks"]}), 5)
        self.assertEqual({task["family"] for task in data["tasks"] if task["split"] == "heldout"}, {"updates"})
        self.assertEqual(set(data["families"]), {"listing", "labels", "updates"})
        skill = (root / "skills/develop/SKILL.md").read_text()
        self.assertIn("name: develop", skill)
        self.assertNotIn("judge", skill.lower())


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("evaluation"), "Evaluation module is missing.")
        self.evaluation = importlib.import_module("evaluation")
        self.runtime = importlib.import_module("copilot_runtime")
        self.root = Path(__file__).resolve().parents[1]
        self.enterContext(patch.object(self.runtime.shutil, "which", return_value="/test-only/copilot"))

    def controls(self, family="listing"):
        data = json.loads((self.root / "eval/calibration.json").read_text())
        self.assertIn("families", data, "Family-specific calibration controls are missing.")
        self.assertIn(family, data["families"])
        return data["families"][family]

    def test_catalog_rejects_cross_split_or_unknown_families(self):
        import skillops
        data = json.loads((self.root / "eval/tasks.json").read_text())
        self.assertEqual(data["schema_version"], 2, "Family catalog migration is missing.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "eval").mkdir()
            variants = []
            for key, value in (("family", "unknown"), ("family", []), ("id", "../outside")):
                changed = deepcopy(data)
                changed["tasks"][0][key] = value
                variants.append(changed)
            changed = deepcopy(data)
            changed["tasks"][1]["split"] = "heldout"
            variants.append(changed)
            changed = deepcopy(data)
            changed["families"]["labels"]["contract"] = ""
            variants.append(changed)
            variants.append({**data, "schema_version": 1})
            variants.append({**data, "schema_version": 2.0})
            changed = deepcopy(data)
            changed["families"]["labels"]["seed"] = "../outside.py"
            variants.append(changed)
            for value in variants:
                (root / "eval/tasks.json").write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(self.runtime.RuntimeFailure):
                    skillops.load_tasks(root)

    def test_unknown_family_fails_before_container_launch(self):
        self.assertTrue(hasattr(self.evaluation, "FAMILIES"), "Fixed family registry is missing.")
        with patch.object(self.evaluation, "container_run") as run:
            with self.assertRaises(self.runtime.RuntimeFailure):
                self.evaluation.execute(self.root, "sha256:" + "0" * 64, "unknown")
            run.assert_not_called()

    def test_fixed_payload_contains_only_family_callable_and_inputs(self):
        def run(directory, image, script, payload=None):
            if script == self.evaluation.FIXED_HARNESS:
                self.assertTrue(payload)
                family = next(name for name, spec in self.evaluation.FAMILIES.items()
                              if spec["entrypoint"] == payload[0]["entrypoint"])
                for row in payload:
                    self.assertEqual(set(row), {"id", "kwargs", "entrypoint"})
                    self.assertEqual(row["entrypoint"], self.evaluation.FAMILIES[family]["entrypoint"])
                return {"completed": True, "cases": [
                    {"id": row["id"], "value": row["expected"], "exception": row["exception"], "input_after": row["kwargs"]}
                    for row in self.evaluation.cases(family)
                ]}
            return {"completed": True, "tests_run": 1, "failures": 0, "errors": 0, "skipped": 0,
                    "expected_failures": 0, "unexpected_successes": 0}
        with patch.object(self.evaluation, "container_run", side_effect=run):
            for family in self.evaluation.FAMILIES:
                self.assertTrue(self.evaluation.execute(self.root, "sha256:" + "0" * 64, family)["fixed"]["all_passed"])

    def test_all_calibration_groups_are_validated_before_any_judge_call(self):
        import skillops
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in self.evaluation.FINGERPRINT_FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / name, target)
            data = json.loads((root / "eval/calibration.json").read_text())
            data["families"]["updates"]["fixtures"][-1]["tests"] = "unknown"
            (root / "eval/calibration.json").write_text(json.dumps(data))
            runtime = self.runtime.CopilotRuntime(root)
            with patch.object(skillops, "context_for", return_value={"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}), patch.object(runtime, "invoke") as invoke:
                report, code = skillops.calibrate(runtime, "gpt-6-astra", root)
            self.assertEqual((report["status"], code), ("blocked", 2))
            invoke.assert_not_called()
            stored = json.loads((root / report["artifact"]).read_text())
            self.assertEqual(stored["error"]["code"], "invalid_fixture")
            self.assertEqual(stored["results"], [])

    def test_calibration_binds_nine_controls_to_their_family_evidence(self):
        import skillops
        catalog = json.loads((self.root / "eval/tasks.json").read_text())
        controls = json.loads((self.root / "eval/calibration.json").read_text())["families"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in self.evaluation.FINGERPRINT_FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / name, target)
            runtime = self.runtime.CopilotRuntime(root)
            calls = []
            def execute(path, image, family):
                passed = (path / "issues.py").read_text() == controls[family]["good_source"]
                total = len(self.evaluation.cases(family))
                return {"fixed": {"all_passed": passed, "passed": total if passed else 0, "total": total},
                        "generated": {"passed": True}}
            def invoke(prompt, model, role, work, artifact, expected_skill=None):
                evidence = json.loads(prompt.split("UNTRUSTED_EVIDENCE:\n", 1)[1])
                family = artifact.parent.parent.name
                self.assertEqual(evidence["contract"], catalog["families"][family]["contract"])
                self.assertEqual(evidence["before"], (root / self.evaluation.FAMILIES[family]["seed"]).read_text())
                self.assertEqual(role, "judge")
                self.assertIsNone(expected_skill)
                calls.append((family, str(work)))
                value = 4 if evidence["execution"]["fixed"]["all_passed"] else 1
                scores = {name: {"score": value, "rationale": "Synthetic calibration transport fixture."}
                          for name in self.evaluation.DIMENSIONS}
                return {"content": json.dumps(scores), "usage": {}}
            context = {"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}
            with patch.object(skillops, "context_for", return_value=context), patch.object(
                skillops, "execute", side_effect=execute
            ), patch.object(runtime, "invoke", side_effect=invoke):
                report, code = skillops.calibrate(runtime, "gpt-6-astra", root)
            self.assertEqual((report["passed"], code), (True, 0))
            self.assertEqual(len(calls), 9)
            self.assertEqual(len({work for _, work in calls}), 9)
            stored = json.loads((root / report["artifact"]).read_text())
            self.assertEqual(len({(row["family"], row["id"]) for row in stored["results"]}), 9)

    def proposal(self):
        return {"files": {"issues.py": "def f(): pass\n", "test_generated.py": "import unittest\n"},
                "review": {"summary": "Static review only.", "risks": []}}

    def test_proposals_reject_unapproved_paths_and_types(self):
        self.evaluation.validate_proposal(self.proposal())
        for path in ("../outside.py", "/tmp/outside.py", "extra.py"):
            value = self.proposal()
            value["files"][path] = "unapproved"
            with self.subTest(path=path), self.assertRaises(self.runtime.RuntimeFailure):
                self.evaluation.validate_proposal(value)
        value = self.proposal()
        value["files"]["issues.py"] = "x" * (128 * 1024 + 1)
        with self.assertRaises(self.runtime.RuntimeFailure):
            self.evaluation.validate_proposal(value)

    def test_proposal_application_rejects_symlinks_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            outside = root / "outside.py"
            outside.write_text("unchanged")
            (source / "issues.py").symlink_to(outside)
            with self.assertRaises(self.runtime.RuntimeFailure):
                self.evaluation.apply_proposal(source, self.proposal())
            self.assertEqual(outside.read_text(), "unchanged")
            self.assertFalse((source / "test_generated.py").exists())

    def test_judge_scoring_is_strict_and_runner_computed(self):
        names = ("requirement_fulfillment", "test_quality", "review_quality")
        scores = {name: {"score": 3, "rationale": "Specific evidence."} for name in names}
        self.assertEqual(self.evaluation.validate_judge(scores)["score"], 75)
        for invalid in (True, 4.0, -1, 5, "4"):
            scores[names[0]]["score"] = invalid
            with self.subTest(value=invalid), self.assertRaises(self.runtime.RuntimeFailure):
                self.evaluation.validate_judge(scores)

    def test_fixed_check_results_require_completion_and_all_cases(self):
        cases = [{"id": "one", "category": "pagination", "kwargs": {"issues": []},
                  "expected": [], "exception": None}]
        result = {"completed": True, "cases": [{"id": "one", "value": [], "exception": None,
                                               "input_after": {"issues": []}}]}
        self.assertTrue(self.evaluation.compare_fixed(cases, result)["all_passed"])
        result["cases"][0]["value"] = [{"wrong": True}]
        self.assertFalse(self.evaluation.compare_fixed(cases, result)["all_passed"])
        for bad in ({}, {"completed": True, "cases": []}):
            with self.assertRaises(self.runtime.RuntimeFailure):
                self.evaluation.compare_fixed(cases, bad)

    def test_fingerprint_changes_for_every_evaluation_input(self):
        for name in ("sample_repo/labels.py", "sample_repo/updates.py"):
            self.assertIn(name, self.evaluation.FINGERPRINT_FILES, "All family seeds must invalidate calibration.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in self.evaluation.FINGERPRINT_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original")
            context = {"model": "gpt-6-astra", "image": "first", "role_settings": {"disabledSkills": []}}
            original = self.evaluation.fingerprint(root, context)
            for name in self.evaluation.FINGERPRINT_FILES:
                path = root / name
                path.write_text("changed")
                self.assertNotEqual(original, self.evaluation.fingerprint(root, context))
                path.write_text("original")
            for key, value in (("image", "second"), ("role_settings", {"disabledSkills": ["x"]})):
                changed = dict(context, **{key: value})
                self.assertNotEqual(original, self.evaluation.fingerprint(root, changed))
            self.assertEqual(original, self.evaluation.fingerprint(root, context))

    def test_generated_tests_cannot_claim_success_without_running(self):
        for value in ({}, {"completed": True, "tests_run": 0, "skipped": 0, "failures": 0, "errors": 0,
                          "expected_failures": 0, "unexpected_successes": 0}):
            if not value:
                with self.assertRaises(self.runtime.RuntimeFailure):
                    self.evaluation.generated_result(value)
            else:
                self.assertFalse(self.evaluation.generated_result(value)["passed"])

    def test_trial_count_requires_a_positive_integer(self):
        import skillops
        self.assertEqual(skillops.positive_int("3"), 3)
        for value in ("0", "-1", "1.5", "true"):
            with self.subTest(value=value), self.assertRaises(skillops.argparse.ArgumentTypeError):
                skillops.positive_int(value)

    def test_trial_summary_reports_success_rate_and_variance(self):
        import skillops
        def usage(value):
            return {"nano_aiu": {"value": value, "unit": "nano_aiu"}}
        rows = [
            {"status": "completed", "execution": {
                "fixed": {"all_passed": True}, "generated": {"passed": True},
            }, "judge": {"score": 100}, "elapsed_seconds": 10,
             "developer_usage": usage(10), "judge_usage": usage(20)},
            {"status": "completed", "execution": {
                "fixed": {"all_passed": False}, "generated": {"passed": True},
            }, "judge": {"score": 50}, "elapsed_seconds": 14,
             "developer_usage": usage(15), "judge_usage": usage(25)},
        ]
        result = skillops.summarize(rows, 2)
        self.assertEqual(result["correctness_successes"], 1)
        self.assertEqual(result["trial_successes"], 1)
        self.assertEqual(result["trial_success_rate"], 0.5)
        self.assertFalse(result["all_trials_passed"])
        self.assertEqual(result["judge_score"], {
            "mean": 75.0, "standard_deviation": 25.0, "denominator": 2,
        })
        self.assertEqual(result["elapsed_seconds"], {
            "mean": 12.0, "standard_deviation": 2.0, "denominator": 2,
        })
        self.assertEqual(result["cost_nano_aiu"], {
            "mean": 35.0, "standard_deviation": 5.0, "denominator": 2,
        })
        json.dumps(result, allow_nan=False)
        self.assertEqual(skillops.row_cost(rows[0]), Decimal("30"))
        self.assertIsNone(skillops.row_cost({}))
        self.assertIsNone(skillops.row_cost({
            "developer_usage": usage(None), "judge_usage": usage(1),
        }))
        for metric in (
            {"value": 1, "unit": "tokens"},
            {"value": -1, "unit": "nano_aiu"},
            {"value": float("inf"), "unit": "nano_aiu"},
            {"value": True, "unit": "nano_aiu"},
        ):
            with self.subTest(metric=metric), self.assertRaises(self.runtime.RuntimeFailure) as caught:
                skillops.row_cost({"developer_usage": {"nano_aiu": metric}})
            self.assertEqual(caught.exception.code, "invalid_metric")

    def test_aggregate_keeps_failures_in_requested_denominator(self):
        import skillops
        self.assertTrue(hasattr(skillops, "summarize"), "Baseline summarizer is missing.")
        rows = [
            {"id": "a", "split": "development", "status": "completed",
             "execution": {"fixed": {"all_passed": True}}, "judge": {"score": 100}},
            {"id": "b", "split": "development", "status": "completed",
             "execution": {"fixed": {"all_passed": False}}, "judge": {"score": 25}},
            {"id": "c", "split": "heldout", "status": "validation_error"},
        ]
        result = skillops.summarize(rows, 3)
        self.assertEqual(result["correctness_successes"], 1)
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["evaluation_completed"], 2)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["mean_judge_score"], 62.5)
        self.assertEqual(result["judge_score_denominator"], 2)

    def test_calibration_rejects_bad_order_and_stale_fingerprints(self):
        import skillops
        self.assertTrue(hasattr(skillops, "calibration_passed"), "Calibration gate is missing.")
        def scored(name, score, correctness):
            return {"id": name, "status": "completed", "judge": {
                "score": score, "dimensions": {key: {"score": correctness, "rationale": "Synthetic test evidence."}
                                             for key in self.evaluation.DIMENSIONS},
            }}
        rows = [{**scored(name, score, correctness), "family": family}
                for family in ("listing", "labels", "updates")
                for name, score, correctness in (("known_good", 100, 4), ("known_bad", 25, 1), ("instruction_in_data", 25, 1))]
        self.assertTrue(skillops.calibration_passed(rows))
        self.assertFalse(skillops.calibration_passed(rows[:3]), "One family must not authorize the whole benchmark.")
        self.assertFalse(skillops.calibration_passed(rows + [rows[0]]))
        rows[1]["judge"]["score"] = 100
        self.assertFalse(skillops.calibration_passed(rows))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "run"
            record.mkdir()
            (record / "calibration.json").write_text(json.dumps({
                "fingerprint": "old", "passed": True, "purpose": "calibration",
            }))
            self.assertIsNone(skillops.find_calibration(root, "new"))
            rows[1]["judge"]["score"] = 25
            (record / "calibration.json").write_text(json.dumps({
                "fingerprint": "new", "passed": True, "purpose": "calibration", "results": rows,
            }))
            self.assertEqual(skillops.find_calibration(root, "new")["fingerprint"], "new")

    def test_pipeline_blocks_without_calibration_and_preserves_failed_attempt(self):
        import skillops
        self.controls()
        data = json.loads((self.root / "eval/calibration.json").read_text())["families"]
        task_data = json.loads((self.root / "eval/tasks.json").read_text())
        requested = len(task_data["tasks"])
        scores = {name: {"score": 4, "rationale": "Synthetic unit-test response."}
                  for name in self.evaluation.DIMENSIONS}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*self.evaluation.FINGERPRINT_FILES, "skills/develop/SKILL.md", "sample_repo/issues.py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / name, target)
            runtime = self.runtime.CopilotRuntime(root)
            context = {"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}
            with patch.object(skillops, "context_for", return_value=context), patch.object(runtime, "invoke") as invoke:
                blocked, exit_code = skillops.baseline(runtime, "gpt-6-astra", root)
                self.assertEqual(exit_code, 2)
                self.assertEqual((blocked["requested"], blocked["attempted"], blocked["blocked"]), (requested, 0, requested))
                invoke.assert_not_called()
            calls = []
            selected = []
            real_invoke = runtime.invoke
            def invoke(prompt, model, role, work, artifact, expected_skill=None):
                calls.append((role, str(work), prompt))
                if role == "developer":
                    family = task_data["tasks"][len(selected)]["family"]
                    selected.append(family)
                    seed_path = self.evaluation.FAMILIES[family]["seed"]
                    self.assertEqual((work / "repo/issues.py").read_text(), (root / seed_path).read_text())
                    self.assertFalse((work / "eval").exists())
                    self.assertTrue(expected_skill.is_file())
                    if len(calls) == 1:
                        events = json.loads((self.root / "tests/fixtures/cli-contract.json").read_text())["developer"]
                        for event in events:
                            if event["type"] == "session.usage_checkpoint":
                                event["data"]["promptCacheBreakState"] = [{"models": []}]
                        result = self.runtime.subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
                        with patch.object(runtime, "configure", return_value={}), patch.object(self.runtime, "capture", return_value=result):
                            return real_invoke(prompt, model, role, work, artifact, expected_skill)
                    result = {"files": {"issues.py": data[family]["good_source"], "test_generated.py": data[family]["good_tests"]},
                              "review": {"summary": "Synthetic unit-test proposal.", "risks": []}}
                else:
                    self.assertIsNone(expected_skill)
                    self.assertFalse((work / ".github/skills").exists())
                    result = scores
                return {"content": json.dumps(result), "usage": {}, "skill_activated": role == "developer"}
            def execute(work, image, family):
                self.assertEqual(family, selected[-1])
                count = len(self.evaluation.cases(family))
                return {"fixed": {"all_passed": True, "passed": count, "total": count}, "generated": {"passed": True}}
            with patch.object(skillops, "context_for", return_value=context), patch.object(
                skillops, "find_calibration", return_value={"path": str(root / "runs/control/calibration.json")}
            ), patch.object(runtime, "invoke", side_effect=invoke), patch.object(skillops, "execute", side_effect=execute):
                try:
                    summary, exit_code = skillops.baseline(runtime, "gpt-6-astra", root)
                except (AttributeError, TypeError) as error:
                    self.fail(f"Malformed CLI evidence aborted later tasks: {type(error).__name__}")
            self.assertEqual(exit_code, 2)
            self.assertEqual((summary["requested"], summary["attempted"], summary["errors"]), (requested, requested, 1))
            self.assertEqual((summary["correctness_successes"], summary["judge_score_denominator"]), (requested - 1, requested - 1))
            self.assertEqual(len({work for _, work, _ in calls}), len(calls))
            developers = [prompt for role, _, prompt in calls if role == "developer"]
            for index, prompt in enumerate(developers):
                for other_index, task in enumerate(task_data["tasks"]):
                    self.assertEqual(task["request"] in prompt, index == other_index)
                payload = json.loads(prompt.split("\n", 1)[1])
                self.assertEqual(set(payload), {"request", "contract", "source"})
                family = task_data["tasks"][index]["family"]
                self.assertEqual(payload["contract"], task_data["families"][family]["contract"])
                for other_family, contract in task_data["families"].items():
                    self.assertEqual(contract["contract"] in prompt, family == other_family)
            report_path = root / summary["report"]
            report = json.loads(report_path.with_suffix(".json").read_text())
            self.assertEqual([row["split"] for row in report["tasks"]], ["development"] * 4 + ["heldout"])
            self.assertEqual(report["catalog_version"], 2)
            self.assertEqual({key: value["requested"] for key, value in report["families"].items()},
                             {"listing": 3, "labels": 1, "updates": 1})
            self.assertEqual(report["tasks"][0]["status"], "generation_error")
            receipt = json.loads((root / report["tasks"][0]["artifacts"] / "developer.json").read_text())
            self.assertEqual(receipt["status"], "contract_error")
            self.assertEqual(report["tasks"][0]["error"]["code"], "invalid_events")
            diff = root / report["tasks"][1]["artifacts"] / "diff.patch"
            self.assertIn("diff --git", diff.read_text())
            self.assertIn("test_generated.py", diff.read_text())

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "Opt-in Docker checks")
    def test_actual_container_detects_hardcoded_page_size(self):
        data = self.controls()
        mutant = data["good_source"].replace("start + page_size", "start + 2")
        self.assertNotEqual(mutant, data["good_source"])
        with tempfile.TemporaryDirectory() as directory:
            self.evaluation.apply_proposal(directory, {
                "files": {"issues.py": mutant, "test_generated.py": data["good_tests"]},
                "review": {"summary": "Hardcoded-size negative control.", "risks": []},
            })
            actual = self.evaluation.execute(directory, self.evaluation.resolve_image())
            self.assertFalse(actual["fixed"]["all_passed"], "A hardcoded slice length must fail protected checks.")

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "Opt-in Docker checks")
    def test_actual_container_good_bad_and_partial_fixes(self):
        data = self.controls()
        image = self.evaluation.resolve_image()
        seed = (self.root / "sample_repo/issues.py").read_text()
        partial = seed.replace("start = page * page_size", "start = (page - 1) * page_size")
        header = "def list_issues(issues, status=None, page=1, page_size=2):\n"
        self.assertIn(header, seed)
        boundary_only = seed.replace(header, header + (
            "    if type(page) is not int or type(page_size) is not int or page < 1 or page_size < 1:\n"
            "        raise ValueError('invalid paging')\n"
        ))
        for source, expected in ((data["good_source"], True), (seed, False), (partial, False), (boundary_only, False)):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                files = Path(directory)
                self.evaluation.apply_proposal(files, {
                    "files": {"issues.py": source, "test_generated.py": data["good_tests"]},
                    "review": {"summary": "Fixture.", "risks": []},
                })
                actual = self.evaluation.execute(files, image)
                self.assertEqual(actual["fixed"]["all_passed"], expected)

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "Opt-in Docker checks")
    def test_actual_generated_test_summary_cannot_replace_runner_completion(self):
        data = self.controls()
        image = self.evaluation.resolve_image()
        with tempfile.TemporaryDirectory() as directory:
            files = Path(directory)
            self.evaluation.apply_proposal(files, {
                "files": {"issues.py": data["good_source"],
                          "test_generated.py": "print('Ran 100 tests OK')\nraise SystemExit(0)\n"},
                "review": {"summary": "Synthetic forged-test control.", "risks": []},
            })
            actual = self.evaluation.execute(files, image)
            self.assertTrue(actual["fixed"]["all_passed"])
            self.assertFalse(actual["generated"]["passed"])
            self.assertEqual(actual["generated"]["code"], "invalid_json")

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "Opt-in Docker checks")
    def test_family_good_seed_and_partial_controls_in_actual_containers(self):
        self.assertTrue(hasattr(self.evaluation, "FAMILIES"), "Family registry is missing.")
        image = self.evaluation.resolve_image()
        counts = {"listing": 26, "labels": 17, "updates": 22}
        for family, spec in self.evaluation.FAMILIES.items():
            data = self.controls(family)
            seed = (self.root / spec["seed"]).read_text()
            partial = data["good_source"].replace(".casefold()", ".lower()") if family == "labels" else (
                data["good_source"].replace("result = [dict(issue) for issue in issues]", "result = issues")
                if family == "updates" else data["good_source"].replace("start + page_size", "start + 2")
            )
            self.assertNotEqual(partial, data["good_source"])
            for source, passed in ((data["good_source"], True), (seed, False), (partial, False)):
                with self.subTest(family=family, passed=passed), tempfile.TemporaryDirectory() as directory:
                    self.evaluation.apply_proposal(directory, {
                        "files": {"issues.py": source, "test_generated.py": data["good_tests"]},
                        "review": {"summary": "Authored family control.", "risks": []},
                    })
                    actual = self.evaluation.execute(directory, image, family)
                    self.assertEqual(actual["fixed"]["total"], counts[family])
                    self.assertEqual(actual["fixed"]["all_passed"], passed)
                    if passed:
                        self.assertTrue(actual["generated"]["passed"])

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "Opt-in Docker checks")
    def test_wrong_family_cannot_receive_listing_success(self):
        self.assertTrue(hasattr(self.evaluation, "FAMILIES"), "Family registry is missing.")
        data = self.controls()
        with tempfile.TemporaryDirectory() as directory:
            self.evaluation.apply_proposal(directory, {
                "files": {"issues.py": data["good_source"], "test_generated.py": data["good_tests"]},
                "review": {"summary": "Wrong-family negative control.", "risks": []},
            })
            result = self.evaluation.execute(directory, self.evaluation.resolve_image(), "labels")
            self.assertFalse(result["fixed"]["all_passed"])

    @unittest.skipUnless(os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1", "Opt-in Docker checks")
    def test_actual_container_rejects_forged_output_early_exit_and_flood(self):
        image = self.evaluation.resolve_image()
        for source, code in (("print('Ran 10 tests OK')\nraise SystemExit(0)\n", "invalid_json"),
                             ("raise SystemExit(0)\n", "invalid_json"),
                             ("while True: print('x'*8192,flush=True)\n", "output_limit_exceeded")):
            with self.subTest(source=source[:30]), tempfile.TemporaryDirectory() as directory:
                files = Path(directory)
                self.evaluation.apply_proposal(files, {
                    "files": {"issues.py": source, "test_generated.py": "import unittest\n"},
                    "review": {"summary": "Negative control.", "risks": []},
                })
                with self.assertRaises(self.runtime.RuntimeFailure) as caught:
                    self.evaluation.execute(files, image)
                self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
