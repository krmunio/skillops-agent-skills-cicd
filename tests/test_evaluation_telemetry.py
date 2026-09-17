import copy
from contextlib import nullcontext, redirect_stdout
import importlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from copilot_runtime import RuntimeFailure, usage_metrics
import project_results
from test_skill_assessments import fixture


class EvaluationTelemetryTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("evaluation_telemetry"),
                             "Durable stage telemetry is not implemented.")
        return importlib.import_module("evaluation_telemetry")

    def receipt(self, cost=7):
        return {"content": "PRIVATE RESPONSE", "prompt": "PRIVATE PROMPT",
                "stderr": "PRIVATE DIAGNOSTICS", "session_id": "PRIVATE SESSION",
                "usage": usage_metrics({"totalNanoAiu": cost}, "gpt-6-astra")}

    def test_records_elapsed_calls_and_allowlisted_usage_without_private_content(self):
        m = self.module()
        recorder = m.Recorder()
        raw = Mock()
        raw.invoke.return_value = self.receipt()
        with patch.object(m.time, "monotonic", side_effect=[10, 11, 13, 14]):
            recorder("base_quality", "started")
            value = recorder.invoke(raw, "private prompt")
            recorder("base_quality", "completed")
        self.assertEqual(value, raw.invoke.return_value)
        stage = recorder.stages["base_quality"]
        self.assertEqual(stage["status"], "completed")
        self.assertEqual(stage["elapsed_seconds"], 4)
        self.assertEqual(stage["invocations"][0]["elapsed_seconds"], 2)
        self.assertEqual(stage["invocations"][0]["usage"]["nano_aiu"], 7)
        self.assertIsNone(stage["invocations"][0]["usage"]["input_tokens"])
        self.assertNotIn("PRIVATE", json.dumps(recorder.stages))
        self.assertEqual(recorder.stages["candidate_quality"]["status"], "not_started")
        self.assertIsNone(recorder.stages["candidate_quality"]["elapsed_seconds"])

    def test_partial_batch_usage_survives_a_later_failure_without_zero_filling(self):
        m = self.module()
        recorder = m.Recorder()
        error = RuntimeFailure("timeout", "PRIVATE FAILURE")
        raw = Mock()
        raw.invoke.side_effect = [self.receipt(), error]
        recorder("base_quality", "started")
        recorder.invoke(raw, "first")
        with self.assertRaises(RuntimeFailure) as raised:
            recorder.invoke(raw, "second")
        self.assertIs(raised.exception, error)
        recorder("base_quality", "blocked", "timeout")
        stage = recorder.stages["base_quality"]
        self.assertEqual(stage["status"], "blocked")
        self.assertEqual(stage["code"], "timeout")
        self.assertEqual(len(stage["invocations"]), 2)
        self.assertEqual(stage["invocations"][0]["usage"]["nano_aiu"], 7)
        self.assertIsNone(stage["invocations"][1]["usage"]["nano_aiu"])
        self.assertEqual(stage["invocations"][1]["status"], "failed")
        self.assertNotIn("PRIVATE", json.dumps(stage))

    def test_failed_cli_preserves_already_reported_usage_without_exposing_diagnostics(self):
        m = self.module()
        import copilot_runtime
        with tempfile.TemporaryDirectory() as folder, patch.object(
                copilot_runtime.shutil, "which", return_value="/test-only/copilot"):
            root = Path(folder)
            runtime = copilot_runtime.CopilotRuntime(root, inherited={})
            recorder = m.Recorder()
            def fail(command, **kwargs):
                Path(command[command.index("--usage-output-file") + 1]).write_text('{"totalNanoAiu":11}')
                return copilot_runtime.subprocess.CompletedProcess(command, 1, "", "PRIVATE STDERR")
            with patch.object(runtime, "configure", return_value={}), patch.object(
                    copilot_runtime, "capture", side_effect=fail):
                recorder("base_quality", "started")
                with self.assertRaises(RuntimeFailure):
                    recorder.invoke(runtime, "PRIVATE PROMPT", "gpt-6-astra", "judge", root, root / "failed.json")
                recorder("base_quality", "blocked", "cli_error")
            call = recorder.stages["base_quality"]["invocations"][0]
            self.assertEqual(call["usage"]["nano_aiu"], 11)
            self.assertEqual(call["status"], "failed")
            self.assertNotIn("PRIVATE", json.dumps(recorder.stages))

    def test_budget_rejection_is_not_counted_as_another_cli_invocation(self):
        m = self.module()
        from project_evaluation import BudgetRuntime
        recorder = m.Recorder()
        raw = Mock()
        raw.invoke.return_value = self.receipt()
        budget = BudgetRuntime(raw, {"calls": 0, "max_calls": 1,
                                     "deadline": m.time.monotonic() + 60})
        budget.recorder = recorder
        recorder("base_quality", "started")
        budget.invoke("first")
        with self.assertRaises(RuntimeFailure):
            budget.invoke("second")
        recorder("base_quality", "blocked", "call_limit")
        self.assertEqual(len(recorder.stages["base_quality"]["invocations"]), 1)
        self.assertEqual(raw.invoke.call_count, 1)

    def telemetry_fixture(self):
        m = self.module()
        report, lifecycle, assessment = fixture()
        recorder = m.Recorder()
        raw = Mock()
        raw.invoke.return_value = self.receipt()
        for stage in ("base_quality", "generation", "candidate_quality"):
            recorder(stage, "started")
            recorder.invoke(raw, "fixture")
            recorder(stage, "completed")
        report["execution"]["metrics"] = {"cli_invocations": 3}
        # Bind the unchanged fixture attachments to the new report measurement.
        from hashlib import sha256
        digest = sha256(project_results.encoded(report)).hexdigest()
        lifecycle["report_sha256"] = assessment["report_sha256"] = digest
        row = assessment["skills"][0]
        data = m.bind(report, assessment, [
            {"skill_key": row["skill_key"], "source_path": row["source_path"], "stages": recorder.stages}])
        return report, lifecycle, assessment, data

    def store(self, folder):
        report, lifecycle, assessment, data = self.telemetry_fixture()
        project_results.store(folder, report)
        project_results.store_evolution(folder, lifecycle)
        project_results.store_assessments(folder, assessment)
        project_results.store_telemetry(folder, data)
        return report, assessment, data

    def test_sidecar_is_optional_immutable_and_bound_to_report_and_assessment(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(project_results.load_telemetry(folder), {})
            report, assessment, data = self.store(folder)
            self.assertEqual(project_results.load_telemetry(folder),
                             {(report["project_id"], report["run_id"]): data})
            project_results.store_telemetry(folder, data)
            changed = copy.deepcopy(data)
            changed["skills"][0]["stages"]["base_quality"]["elapsed_seconds"] += 1
            with self.assertRaises(RuntimeFailure) as raised:
                project_results.store_telemetry(folder, changed)
            self.assertEqual(raised.exception.code, "immutable_conflict")
            for field in ("report_sha256", "assessment_sha256"):
                changed = copy.deepcopy(data)
                changed[field] = "0" * 64
                with self.subTest(field=field), self.assertRaises(RuntimeFailure):
                    m.validate(changed, report, assessment)

    def test_validator_rejects_extra_fields_bad_numbers_and_wrong_identity(self):
        m = self.module()
        report, _, assessment, data = self.telemetry_fixture()
        for field, value in (("elapsed_seconds", -1), ("elapsed_seconds", float("nan")),
                             ("elapsed_seconds", 10 ** 400),
                             ("elapsed_seconds", True), ("prompt", "private"),
                             ("status", "invented")):
            changed = copy.deepcopy(data)
            changed["skills"][0]["stages"]["base_quality"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeFailure):
                m.validate(changed, report, assessment)
        changed = copy.deepcopy(data)
        changed["skills"][0]["source_path"] = "skills/other"
        with self.assertRaises(RuntimeFailure):
            m.validate(changed, report, assessment)
        changed = copy.deepcopy(data)
        changed["skills"][0]["stages"]["base_quality"]["invocations"][0]["usage"]["input_tokens"] = "private"
        with self.assertRaises(RuntimeFailure):
            m.validate(changed, report, assessment)

    def test_summary_does_not_overflow_a_sum_of_finite_measurements(self):
        self.module()
        import evaluation_reporting
        _, _, _, data = self.telemetry_fixture()
        stage = data["skills"][0]["stages"]["base_quality"]
        stage["invocations"][0]["usage"]["nano_aiu"] = 1e308
        stage["invocations"].append(copy.deepcopy(stage["invocations"][0]))
        text = "\n".join(evaluation_reporting.measurement_lines(data))
        self.assertNotIn("| inf |", text)

    def test_merge_retains_telemetry_and_rejects_conflict_before_writing(self):
        self.module()
        root = Path(project_results.__file__).parent
        with tempfile.TemporaryDirectory() as folder:
            incoming, target = Path(folder) / "incoming", Path(folder) / "target"
            self.store(incoming)
            project_results.merge_results(root, incoming, target)
            relative = Path("sample_repo/123-1/stage-metrics.json")
            self.assertEqual((incoming / relative).read_bytes(), (target / relative).read_bytes())
            before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*.json")}
            data = project_results.read_json(incoming / relative)
            data["skills"][0]["stages"]["base_quality"]["elapsed_seconds"] += 1
            (incoming / relative).write_bytes(project_results.encoded(data))
            with self.assertRaises(RuntimeFailure):
                project_results.merge_results(root, incoming, target)
            self.assertEqual(before, {p.relative_to(target): p.read_bytes() for p in target.rglob("*.json")})

    def test_static_build_preserves_validated_telemetry_with_hashed_assets(self):
        self.module()
        root = Path(project_results.__file__).parent
        with tempfile.TemporaryDirectory() as folder:
            incoming, site = Path(folder) / "incoming", Path(folder) / "site"
            self.store(incoming)
            project_results.build(root, incoming, site)
            relative = Path("sample_repo/123-1/stage-metrics.json")
            self.assertTrue((site / "results" / relative).is_file(),
                            "The existing publisher must retain stage measurements.")
            self.assertEqual((site / "results" / relative).read_bytes(), (incoming / relative).read_bytes())
            self.assertEqual(len(list(site.glob("app.*.js"))), 1)
            data = project_results.read_json(incoming / relative)
            data["report_sha256"] = "0" * 64
            (incoming / relative).write_bytes(project_results.encoded(data))
            with self.assertRaises(RuntimeFailure):
                project_results.build(root, incoming, Path(folder) / "invalid-site")

    def test_main_persists_real_pipeline_stages_even_when_budget_or_preparation_blocks(self):
        self.module()
        import project_evaluation as runner
        for limit in (1, 2, 3):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                skill = root / "projects/sample_repo/.github/skills/develop/SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text("---\nname: develop\ndescription: Check code.\n---\nRead code.\n")
                (root / "eval").mkdir()
                (root / "eval/skill-guide-rubric.json").write_text("{}")
                raw = Mock(env={})
                raw.private = root / "private"
                raw.private.mkdir()
                raw.locked.side_effect = nullcontext
                def invoke(prompt, model, role, *args, **kwargs):
                    return {**self.receipt(), "content": json.dumps({
                        "instructions": "Read code and verify existing behavior.",
                        "addressed_findings": ["boundaries"], "hypothesis": "Fixture only.",
                    })}
                raw.invoke.side_effect = invoke
                quality = fixture()[2]["skills"][0]["quality"]
                def assess_quality(runtime, model, project, path, rubric, artifact):
                    runtime.invoke("fixture quality", model, "judge", project, artifact / "judge.json")
                    return copy.deepcopy(quality["base" if artifact.name == "base-quality" else "candidate"])
                assess = runner.assess_with_details
                policy = {"enabled": True, "authenticated": True, "progress": False,
                          "budget": {"calls": 0, "max_calls": limit,
                                     "deadline": runner.time.monotonic() + 60}}
                output = root / "results"
                argv = ["project_evaluation.py", "--root", str(root), "--output", str(output),
                        "--project", "sample_repo", "--run-id", "123-1", "--source-commit", "a" * 40]
                with patch.object(runner.sys, "argv", argv), patch.object(
                        runner, "policy_from_environment", return_value=policy), patch.object(
                        runner, "assess_with_details",
                        side_effect=lambda *a, **kw: assess(*a, **kw, runtime_factory=lambda _: raw)), patch.object(
                        runner, "resolve_images", side_effect=RuntimeFailure("missing_check_image", "fixture")), patch.object(
                        runner.skill_pipeline, "assess_quality", side_effect=assess_quality), redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(), 2)
                data = project_results.load_telemetry(output)[("sample_repo", "123-1")]
                self.assertEqual(raw.invoke.call_count, limit)
                stages = data["skills"][0]["stages"]
                self.assertEqual(sum(len(stage["invocations"]) for stage in stages.values()), limit)
                self.assertEqual(stages["base_quality"]["status"], "completed")
                if limit < 3:
                    blocked = "generation" if limit == 1 else "candidate_quality"
                    self.assertEqual(stages[blocked]["code"], "call_limit")
                    self.assertEqual(stages[blocked]["invocations"], [])
                else:
                    self.assertEqual(stages["candidate_quality"]["status"], "completed")
                details = project_results.load_assessments(output)[("sample_repo", "123-1")]
                self.assertEqual(details["skills"][0]["decision"]["status"], "unverified")
                self.assertNotIn("PRIVATE", (output / "sample_repo/123-1/stage-metrics.json").read_text())
                from evaluation_reporting import summary
                text = summary(output, "123-1")
                self.assertIn(f"Skill quality cycles: {int(limit == 3)}/1 complete; {int(limit < 3)} incomplete", text)

    def test_actions_summary_shows_telemetry_and_legacy_absence_explicitly(self):
        self.module()
        import evaluation_reporting
        with tempfile.TemporaryDirectory() as folder:
            self.store(folder)
            text = evaluation_reporting.summary(folder, "123-1")
            self.assertIn("Stage execution measurements", text)
            self.assertIn("CLI invocations", text)
            self.assertIn("nano-AIU", text)
            self.assertIn("not_started", text)
            self.assertIn("Assessment policy: 1", text)
            self.assertLess(text.index("Improvement / APO-inspired candidate generation"),
                            text.index("Baseline / candidate / Anthropic"))
            self.assertNotIn("PRIVATE", text)
            (Path(folder) / "sample_repo/123-1/stage-metrics.json").unlink()
            self.assertIn("Stage measurements not recorded", evaluation_reporting.summary(folder, "123-1"))


if __name__ == "__main__":
    unittest.main()
