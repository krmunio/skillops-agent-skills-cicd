import importlib
import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch, MagicMock


class ProjectEvaluationTests(unittest.TestCase):
    def test_cli_selects_one_project_and_rejects_unknown_ids_before_assessment(self):
        m = self.module()
        root = Path(m.__file__).resolve().parent
        for identifier in ("sample_repo", "unknown-project", "../outside"):
            with self.subTest(project=identifier), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "results"
                result = subprocess.run([
                    m.sys.executable, str(root / "project_evaluation.py"), "--root", str(root),
                    "--output", str(output), "--run-id", "109-1", "--source-commit", "a" * 40,
                    "--project", identifier,
                ], env={**m.os.environ, "SKILLOPS_LIVE_EVALUATION_ENABLED": "false"},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2)
                if identifier == "sample_repo":
                    self.assertEqual([path.relative_to(output).as_posix()
                                      for path in output.glob("*/*/report.json")], ["sample_repo/109-1/report.json"])
                else:
                    self.assertEqual(json.loads(result.stderr)["code"], "unknown_project")
                    self.assertFalse(output.exists())

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("project_evaluation"), "orchestrator is missing")
        return importlib.import_module("project_evaluation")

    def test_disabled_evaluation_records_blocked_without_initializing_runtime(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "projects/sample_repo").mkdir(parents=True)
            (root / "projects/sample_repo/a.py").write_text("a=1")
            runtime = Mock()
            row = {"id": "sample_repo", "adapter": "issue-management-v2", "tree_sha256": "b" * 64, "error": None}
            result = m.assess(root, row, "123-1", "a" * 40, {}, runtime_factory=runtime)
            self.assertEqual(result["guide"]["status"], "not_assessed")
            self.assertEqual(result["execution"]["reason_code"], "live_disabled")
            runtime.assert_not_called()

    def test_skills_and_unsupported_adapter_have_explicit_nonpassing_states(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / "projects/other/.github/skills/test/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: test\ndescription: test\n---\nInstruction")
            row = {"id": "other", "adapter": "arbitrary-shell", "tree_sha256": "b" * 64, "error": None}
            result = m.assess(root, row, "123-1", "a" * 40, {})
            self.assertEqual(result["guide"]["reason_code"], "guide_integration_pending")
            self.assertEqual(result["execution"]["reason_code"], "unsupported_adapter")
            self.assertNotIn("Instruction", json.dumps(result))

    def test_budget_stops_before_extra_invocation(self):
        m = self.module()
        raw = Mock()
        budget = m.BudgetRuntime(raw, {"calls": 0, "max_calls": 1, "deadline": m.time.monotonic() + 60})
        budget.invoke("first")
        self.assertGreater(raw.invoke.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(raw.invoke.call_args.kwargs["timeout"], 60)
        with self.assertRaises(m.RuntimeFailure):
            budget.invoke("second")
        self.assertEqual(raw.invoke.call_count, 1)

    def test_missing_auth_or_limits_never_starts_model_runtime(self):
        m = self.module()
        for policy, reason in (
            ({"enabled": True, "authenticated": False}, "missing_auth"),
            ({"enabled": True, "authenticated": True}, "missing_limits"),
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "projects/sample_repo").mkdir(parents=True)
                runtime = Mock()
                row = {"id": "sample_repo", "adapter": "issue-management-v2", "tree_sha256": "b" * 64, "error": None}
                result = m.assess(root, row, "123-1", "a" * 40, policy, runtime_factory=runtime)
                self.assertEqual(result["execution"]["reason_code"], reason)
                runtime.assert_not_called()

    def test_discovered_skill_uses_new_pipeline_without_root_adapter_registration(self):
        m = self.module()
        from test_skill_assessments import fixture
        import base64
        report, lifecycle, assessment = fixture()
        captures = [(version, {
            item["path"]: base64.b64decode(item["data"]) for item in lifecycle["file_contents"]
            if item["version_id"] == version["version_id"]
        }) for version in lifecycle["records"]["versions"]]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "projects/sample_repo"
            skill = project / ".github/skills/develop/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: develop\ndescription: Develop.\n---\nCheck.")
            (root / "eval").mkdir()
            (root / "eval/skill-guide-rubric.json").write_text('{"dimensions":["clarity"]}')
            runtime = MagicMock()
            runtime.private = root / "private"
            runtime.private.mkdir()
            policy = {"enabled": True, "authenticated": True,
                      "budget": {"calls": 0, "max_calls": 8, "deadline": m.time.monotonic() + 60}}
            row = {"id": "sample_repo", "adapter": None, "tree_sha256": "b" * 64, "error": None}
            def evaluate(*args, **kwargs):
                value = assessment["skills"][0]
                value["skill_key"] = args[4]
                return value, captures
            bad = project / ".github/skills/bad/SKILL.md"
            bad.parent.mkdir(parents=True)
            bad.write_text("invalid")
            def evaluate_one(*args, **kwargs):
                if args[3]["path"].endswith("/bad"):
                    raise m.RuntimeFailure("invalid_skill_name", "Invalid Skill name.")
                return evaluate(*args, **kwargs)
            with patch.object(m, "resolve_images", return_value={"python": "sha256:" + "a" * 64}), patch.object(
                    m.skill_pipeline, "evaluate_skill", side_effect=evaluate_one) as evaluated, patch.object(
                    m.project_checks, "prepared_images", wraps=m.project_checks.prepared_images) as prepared:
                result, versions, details = m.assess_with_details(
                    root, row, "123-1", "a" * 40, policy, runtime_factory=lambda path: runtime)
            self.assertEqual(evaluated.call_count, 2)
            prepared.assert_called_once()
            self.assertEqual(result["execution"]["status"], "blocked")
            self.assertEqual(result["guide"]["metrics"]["errors"], 1)
            self.assertEqual(details["skills"][0]["decision"]["status"], "improved")
            self.assertEqual(details["skills"][0]["skill_key"],
                             m.skill_pipeline.skill_key("sample_repo", ".github/skills/develop", []))
            self.assertEqual(versions["report_sha256"], details["report_sha256"])
            self.assertFalse((root / ".skillops/registry.json").exists())

    def test_budget_forwards_credit_cap_and_accepts_job_scoped_auth(self):
        m = self.module()
        raw = Mock()
        bounded = m.BudgetRuntime(raw, {"calls": 0, "max_calls": 2, "deadline": m.time.monotonic() + 20,
                                       "max_ai_credits": 30})
        bounded.invoke("prompt")
        self.assertEqual(raw.invoke.call_args.kwargs["max_ai_credits"], 30)
        with patch.dict(m.os.environ, {"GITHUB_TOKEN": "test-job-token",
                                     "SKILLOPS_LIVE_EVALUATION_ENABLED": "true",
                                     "SKILLOPS_MAX_INVOCATIONS": "8", "SKILLOPS_MAX_SECONDS": "120",
                                     "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "30"}, clear=True):
            policy = m.policy_from_environment()
            self.assertTrue(policy["authenticated"])
            self.assertEqual(policy["budget"]["max_ai_credits"], 30)

    def test_environment_rejects_credit_limits_below_pinned_cli_minimum(self):
        m = self.module()
        for value in ("3", "29.99", "0", "-1", "nan", "inf", "invalid"):
            with self.subTest(value=value), patch.dict(m.os.environ, {
                    "GITHUB_TOKEN": "test-job-token", "SKILLOPS_LIVE_EVALUATION_ENABLED": "true",
                    "SKILLOPS_MAX_INVOCATIONS": "8", "SKILLOPS_MAX_SECONDS": "900",
                    "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": value}, clear=True):
                policy = m.policy_from_environment()
                self.assertNotIn("budget", policy)
                self.assertEqual(m.os.environ["SKILLOPS_MAX_AI_CREDITS_PER_SESSION"], value)


if __name__ == "__main__":
    unittest.main()
