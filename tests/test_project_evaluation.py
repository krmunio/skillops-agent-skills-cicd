import importlib
import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
import io
from contextlib import nullcontext, redirect_stdout, redirect_stderr
from copy import deepcopy
import inspect
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


class TargetedQualityTests(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module("project_evaluation")
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "projects/sample_repo"
        for name in ("develop", "review"):
            path = self.project / f".github/skills/{name}/SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: Check code safely.\n---\nOriginal {name}.\n")
        (self.root / "eval").mkdir()
        (self.root / "eval/skill-guide-rubric.json").write_text('{"dimensions":["workflow_clarity"]}')
        self.raw = MagicMock(project=self.root, cli="/offline/copilot", env={})
        self.raw.private = self.root / "private"
        self.raw.private.mkdir()
        self.raw.invoke.return_value = {"content": json.dumps({
            "instructions": "Inspect the current code and validate the requested change.",
            "hypothesis": "Synthetic test hypothesis.", "addressed_findings": ["workflow_clarity"]})}
        self.factory = Mock(return_value=self.raw)
        self.quality = self.enterContext(patch.object(self.m.skill_guide, "evaluate_bundle", return_value={
            "status": "pass", "static": {"findings": []},
            "judge": {"dimensions": {"workflow_clarity": {"score": 3}}}}))
        images = {"python": "sha256:" + "a" * 64}
        self.enterContext(patch.object(self.m, "resolve_images", return_value=images))
        self.enterContext(patch.object(self.m.project_checks, "prepared_images",
                                      side_effect=lambda *a, **k: nullcontext(images)))
        self.enterContext(patch.object(self.m.project_checks, "execute", side_effect=lambda project, plan, *a, **k: {
            "plan_sha256": plan["sha256"], "environment_sha256": "b" * 64, "protected_sha256": "c" * 64,
            "elapsed_seconds": 1, "status": "completed",
            "cases": [{"id": "existing-case", "status": "passed"}], "gates": []}))
        self.project_row = {"id": "sample_repo", "adapter": None, "error": None,
                            "tree_sha256": self.m.results.tree_hash(self.project)}
        self.policy = {"enabled": True, "authenticated": True, "progress": False,
                       "budget": {"calls": 0, "max_calls": 20, "deadline": self.m.time.monotonic() + 60}}
        self.key = self.m.skill_pipeline.skill_key("sample_repo", ".github/skills/develop", [])

    def evaluate(self, selector=None, history=()):
        options = {}
        if selector is not None:
            self.assertIn("assessment_skill_key", inspect.signature(self.m.assess_with_details).parameters)
            options["assessment_skill_key"] = selector
        telemetry = []
        value = self.m.assess_with_details(
            self.root, self.project_row, "998-1", "a" * 40, self.policy,
            history=history, telemetry=telemetry, runtime_factory=self.factory, **options)
        return (*value, telemetry)

    def test_selector_runs_one_fresh_quality_cycle_and_retains_full_inventory_provenance(self):
        before = self.m.results.tree_hash(self.project)
        report, lifecycle, assessment, telemetry = self.evaluate(self.key)
        self.assertEqual([s["skill_key"] for s in assessment["skills"]], [self.key])
        self.assertEqual(self.quality.call_count, 2)
        self.assertEqual(self.raw.invoke.call_count, 1)
        self.assertEqual(report["guide"]["metrics"], {"skills": 2, "requested": 1, "errors": 0})
        self.assertEqual(report["project_tree_sha256"], before)
        self.assertEqual(report["source_commit"], "a" * 40)
        self.assertEqual(report["evaluator_sha256"], self.m.results.evaluator_hash(self.root))
        self.assertEqual(self.m.results.tree_hash(self.project), before)
        self.assertEqual(len(telemetry), 1)
        output = self.root / "results"
        self.m.results.store(output, report)
        self.m.results.store_evolution(output, lifecycle)
        self.m.results.store_assessments(output, assessment)
        self.m.results.store_telemetry(output, self.m.evaluation_telemetry.bind(report, assessment, telemetry))
        self.m.results.reindex(self.root, output)
        self.assertEqual(len(self.m.results.load_assessments(output)[("sample_repo", "998-1")]["skills"]), 1)
        self.m.results.load_evolution(output)
        self.m.results.load_telemetry(output)
        catalog = json.loads((output / "index.json").read_text())
        self.assertEqual(len(catalog["projects"][0]["detected_skills"]), 2)
        summary = "\n".join(self.m.evaluation_reporting.report_lines(report, assessment))
        self.assertIn("1/2 complete", summary)
        self.assertIn("1/2 current Skills selected", summary)
        self.assertIn("not full-project coverage", summary)

    def test_default_still_evaluates_all_current_skills(self):
        report, _, assessment, _ = self.evaluate()
        self.assertEqual(len(assessment["skills"]), 2)
        self.assertEqual(self.quality.call_count, 4)
        self.assertEqual(self.raw.invoke.call_count, 2)
        self.assertEqual(report["guide"]["metrics"], {"skills": 2, "errors": 0})

    def test_history_identity_is_selectable_without_reusing_its_old_quality_rv001(self):
        history = [{"project_id": "sample_repo", "skills": [{
            "source_path": ".github/skills/develop", "skill_key": "auto:existing",
            "quality": {"base": "OLD_BASELINE_SENTINEL"}}]}]
        before = deepcopy(history)
        report, _, assessment, _ = self.evaluate("auto:existing", history)
        self.assertEqual(assessment["skills"][0]["skill_key"], "auto:existing")
        self.assertEqual(self.quality.call_count, 2)
        self.assertEqual(history, before)
        self.assertNotIn("OLD_BASELINE_SENTINEL", json.dumps([report, assessment]))

    def test_unknown_malformed_stale_and_ambiguous_selectors_never_construct_runtime(self):
        histories = [
            ("path:unknown", (), "unknown_assessment_skill"),
            ("../PRIVATE_SENTINEL", (), "invalid_assessment_skill_key"),
            ("path:key\nPRIVATE_SENTINEL", (), "invalid_assessment_skill_key"),
            ("auto:removed", [{"project_id": "sample_repo", "skills": [
                {"source_path": "skills/removed", "skill_key": "auto:removed"}]}], "unknown_assessment_skill"),
            (self.key, [{"project_id": "sample_repo", "skills": [
                {"source_path": ".github/skills/develop", "skill_key": "auto:existing"}]}], "unknown_assessment_skill"),
            ("auto:shared", [{"project_id": "sample_repo", "skills": [
                {"source_path": f".github/skills/{name}", "skill_key": "auto:shared"}]}
                for name in ("develop", "review")], "ambiguous_assessment_skill"),
        ]
        for selector, history, code in histories:
            with self.subTest(code=code):
                with self.assertRaises(self.m.RuntimeFailure) as caught:
                    self.evaluate(selector, history)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("PRIVATE_SENTINEL", str(caught.exception))
        self.factory.assert_not_called()
        self.quality.assert_not_called()
        self.raw.invoke.assert_not_called()

    def test_cli_requires_explicit_project_and_rejects_recorded_work_conflicts(self):
        self.assertIn("--assessment-skill-key", Path(self.m.__file__).read_text())
        for extra, code in [
            ([], "assessment_selection_requires_project"),
            (["--changed-since", "b" * 40], "assessment_selection_requires_project"),
            (["--project", "sample_repo", "--skill-key", self.key], "assessment_selection_conflict"),
            (["--project", "sample_repo", "--work-id", "recorded"], "assessment_selection_conflict"),
            (["--project", "sample_repo", "--max-rounds", "1"], "assessment_selection_conflict"),
        ]:
            with self.subTest(extra=extra):
                args = ["project_evaluation.py", "--root", str(self.root), "--output", str(self.root / "out"),
                        "--run-id", "998-1", "--source-commit", "a" * 40,
                        "--assessment-skill-key", self.key, *extra]
                error = io.StringIO()
                with patch.object(self.m.sys, "argv", args), patch.object(
                        self.m, "assess_with_details") as assess, patch.object(
                        self.m, "run_recorded_iterations") as recorded, redirect_stderr(error):
                    self.assertEqual(self.m.main(), 2)
                self.assertEqual(json.loads(error.getvalue())["code"], code)
                assess.assert_not_called()
                recorded.assert_not_called()

    def test_cli_history_selection_persists_new_graph_without_mutating_prior_evidence(self):
        self.assertIn("--assessment-skill-key", Path(self.m.__file__).read_text())
        report, lifecycle, assessment, _ = self.evaluate("auto:existing", [{
            "project_id": "sample_repo", "skills": [
                {"source_path": ".github/skills/develop", "skill_key": "auto:existing"}]}])
        history = self.root / "history"
        self.m.results.store(history, report)
        self.m.results.store_evolution(history, lifecycle)
        self.m.results.store_assessments(history, assessment)
        original = {p.relative_to(history): p.read_bytes() for p in history.rglob("*.json")}
        self.quality.reset_mock()
        self.raw.invoke.reset_mock()
        output = self.root / "new-results"
        args = ["project_evaluation.py", "--root", str(self.root), "--output", str(output),
                "--run-id", "999-1", "--source-commit", "b" * 40, "--project", "sample_repo",
                "--history", str(history), "--assessment-skill-key", "auto:existing"]
        assess = self.m.assess_with_details
        with patch.object(self.m.sys, "argv", args), patch.object(
                self.m, "policy_from_environment", return_value=self.policy), patch.object(
                self.m, "assess_with_details", side_effect=lambda *a, **k: assess(
                    *a, **k, runtime_factory=self.factory)), redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(), 2)
        rows = self.m.results.load_reports(output)
        details = self.m.results.load_assessments(output)
        self.assertEqual(rows[0]["source_commit"], "b" * 40)
        self.assertEqual(len(details[("sample_repo", "999-1")]["skills"]), 1)
        self.assertEqual(details[("sample_repo", "999-1")]["skills"][0]["skill_key"], "auto:existing")
        self.assertEqual(self.quality.call_count, 2)
        self.assertEqual(self.raw.invoke.call_count, 1)
        self.assertEqual(original, {p.relative_to(history): p.read_bytes() for p in history.rglob("*.json")})


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
