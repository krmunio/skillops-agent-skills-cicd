import importlib
import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import Mock
from unittest.mock import patch, MagicMock


class ProjectEvaluationTests(unittest.TestCase):
    def test_live_history_ignores_examples_but_retains_real_assessments(self):
        from hashlib import sha256
        import dashboard_samples
        from test_skill_assessments import fixture
        m = self.module()
        root = Path(m.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            history, output = Path(folder) / "history", Path(folder) / "output"
            dashboard_samples.seed(root, history)
            report, lifecycle, assessment = fixture()
            report["project_id"] = lifecycle["project_id"] = assessment["project_id"] = "project-a"
            lifecycle["report_sha256"] = assessment["report_sha256"] = sha256(m.results.encoded(report)).hexdigest()
            m.results.store(history, report)
            m.results.store_evolution(history, lifecycle)
            m.results.store_assessments(history, assessment)
            args = ["project_evaluation.py", "--root", str(root), "--output", str(output),
                    "--history", str(history), "--project", "project-a", "--run-id", "999-1",
                    "--source-commit", "a" * 40]
            with patch.object(m.sys, "argv", args), patch.dict(m.os.environ, {
                "SKILLOPS_LIVE_EVALUATION_ENABLED": "false",
            }, clear=True), patch.object(m, "assess_with_details", wraps=m.assess_with_details) as assessed, redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(), 2)
            self.assertEqual(assessed.call_args.kwargs["history"], [assessment])

    def test_explicit_long_project_window_remains_bounded(self):
        m = self.module()
        for seconds in ("7200", "7201", "0"):
            with self.subTest(seconds=seconds), patch.dict(m.os.environ, {
                    "SKILLOPS_MAX_INVOCATIONS": "96", "SKILLOPS_MAX_SECONDS": seconds,
                    "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "60"}, clear=True), patch.object(
                    m.time, "monotonic", return_value=100):
                policy = m.policy_from_environment()
                if seconds == "7200":
                    self.assertIn("budget", policy)
                    self.assertEqual(policy["budget"], {"calls": 0, "max_calls": 96,
                                                       "deadline": 7300, "max_seconds": 7200,
                                                       "max_ai_credits": 60})
                else:
                    self.assertNotIn("budget", policy)

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


class ChangedProjectTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("project_evaluation")
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git("init", "-q")
        self.git("config", "user.name", "SkillOps test")
        self.git("config", "user.email", "test@example.invalid")
        for project in ("alpha", "beta"):
            self.write(f"projects/{project}/app.py", "VALUE = 1\n")
        self.write("README.md", "fixture\n")
        self.before = self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.root, check=True, capture_output=True, text=True, timeout=10).stdout.strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def select(self, after, before=None):
        self.assertTrue(hasattr(self.module, "changed_projects"), "Changed-project selection is not implemented.")
        projects = self.module.results.catalog(self.root)
        return self.module.changed_projects(self.root, projects, before or self.before, after)

    def test_new_project_and_skill_resource_change_select_only_affected_projects(self):
        self.write("projects/alpha/.github/skills/review/references/check.md", "Check changes.")
        self.write("projects/gamma/app.py", "VALUE = 2\n")
        selected = self.select(self.commit())
        self.assertEqual([project["id"] for project in selected], ["alpha", "gamma"])

    def test_cross_project_move_selects_both_projects_and_deletion_is_not_evaluated(self):
        self.git("mv", "projects/alpha/app.py", "projects/beta/moved.py")
        self.write("projects/alpha/remaining.py", "VALUE = 1\n")
        self.assertEqual([project["id"] for project in self.select(self.commit())], ["alpha", "beta"])
        (self.root / "projects/alpha/remaining.py").unlink()
        (self.root / "projects/alpha").rmdir()
        self.assertEqual([project["id"] for project in self.select(self.commit())], ["beta"])

    def test_shared_evaluator_change_selects_all_but_dashboard_and_results_do_not(self):
        self.write("dashboard/app.js", "export const changed = true;\n")
        self.write("results/note.txt", "not evaluator input")
        self.assertEqual(self.select(self.commit()), [])
        self.write("eval/rubric.json", "{}")
        self.assertEqual([project["id"] for project in self.select(self.commit())], ["alpha", "beta"])

    def test_shared_runtime_manifest_change_selects_all_projects(self):
        for name in ("package.json", "package-lock.json"):
            with self.subTest(name=name):
                self.write(name, "{}")
                self.assertEqual([project["id"] for project in self.select(self.commit())], ["alpha", "beta"])

    def test_invalid_or_missing_revision_fails_without_falling_back_to_full_catalog(self):
        for before, after in (("invalid", self.before), ("f" * 40, self.before), (self.before, "e" * 40)):
            with self.subTest(before=before, after=after):
                self.assertTrue(hasattr(self.module, "changed_projects"))
                with self.assertRaises(self.module.RuntimeFailure):
                    self.select(after, before)
        self.assertEqual([project["id"] for project in self.select(self.before, "0" * 40)], ["alpha", "beta"])

    def test_no_changed_projects_is_an_explicit_noop_without_runtime_or_fake_reports(self):
        self.write("README.md", "documentation only\n")
        after = self.commit()
        output = self.root / "output"
        step_output = self.root / "step-output"
        args = ["project_evaluation.py", "--root", str(self.root), "--output", str(output),
                "--run-id", "500-1", "--source-commit", after, "--changed-since", self.before]
        with patch.object(self.module.sys, "argv", args), patch.dict(self.module.os.environ, {
                "SKILLOPS_ACTIONS_PROGRESS": "true", "GITHUB_OUTPUT": str(step_output),
                "SKILLOPS_LIVE_EVALUATION_ENABLED": "false"}, clear=True), patch.object(
                self.module, "assess_with_details") as assess, redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.module.main(), 0)
        assess.assert_not_called()
        self.assertIn("no_changed_projects", log.getvalue())
        self.assertEqual(step_output.read_text(), "selected_projects=0\n")
        self.assertEqual(self.module.results.load_reports(output), [])

    def test_changed_project_cli_preserves_blocked_status_and_does_not_assess_other_projects(self):
        self.write("projects/alpha/app.py", "VALUE = 2\n")
        after = self.commit()
        output = self.root / "output"
        args = ["project_evaluation.py", "--root", str(self.root), "--output", str(output),
                "--run-id", "501-1", "--source-commit", after, "--changed-since", self.before]
        with patch.object(self.module.sys, "argv", args), patch.dict(
                self.module.os.environ, {"SKILLOPS_LIVE_EVALUATION_ENABLED": "false"}, clear=True
        ), redirect_stdout(io.StringIO()):
            self.assertEqual(self.module.main(), 2)
        rows = self.module.results.load_reports(output)
        self.assertEqual([row["project_id"] for row in rows], ["alpha"])
        self.assertEqual(rows[0]["execution"]["reason_code"], "live_disabled")


if __name__ == "__main__":
    unittest.main()
