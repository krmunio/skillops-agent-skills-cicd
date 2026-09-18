"""Temporary synthetic live-shaped CLI contracts; not operational approval or model evidence."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from copilot_runtime import RuntimeFailure
import project_checks
import project_evaluation as runner
import project_results as results
import repositories
import skillops
import test_hackathon_integration as integration


class ApprovalFlowTests(unittest.TestCase):
    def setUp(self):
        self.case = integration.ReplayIntegrationTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        code_root = Path(runner.__file__).parent
        for name in results.CORE:
            if (code_root / name).is_file():
                shutil.copyfile(code_root / name, self.case.root / name)
        for name in ("eval", "skills"):
            shutil.copytree(code_root / name, self.case.root / name, dirs_exist_ok=True)
        self.assertEqual(results.evaluator_hash(self.case.root), results.evaluator_hash(code_root))
        self.runtimes = []
        self.enterContext(patch.dict(os.environ, {
            "CI": "", "GITHUB_ACTIONS": "", "SKILLOPS_LIVE_EVALUATION_ENABLED": "true",
            "COPILOT_GITHUB_TOKEN": "offline-test-only", "SKILLOPS_MAX_INVOCATIONS": "30",
            "SKILLOPS_MAX_SECONDS": "120", "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "30",
        }))
        self.enterContext(patch.object(skillops, "__file__", str(self.case.root / "skillops.py")))
        self.enterContext(patch.object(runner, "CopilotRuntime", side_effect=self.factory))
        self.enterContext(patch.object(runner, "resolve_images", return_value=self.case.images))
        @contextmanager
        def prepared(project, images, **kwargs):
            yield images
        self.enterContext(patch.object(project_checks, "prepared_images", side_effect=prepared))

    def factory(self, root):
        raw = integration.OfflineTransport(root)
        raw.reject_confirmation = False
        self.runtimes.append(raw)
        return raw

    def cli(self, args, answer=""):
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["skillops", *args]), patch("sys.stdin.isatty", return_value=True), \
                patch.object(errors, "isatty", return_value=True), patch("builtins.input", return_value=answer), \
                redirect_stdout(output), redirect_stderr(errors):
            code = skillops.main()
        return code, json.loads(output.getvalue()) if output.getvalue() else None, errors.getvalue()

    def approve_selected(self):
        common = ["--project", "sample_repo", "--skill-key", self.case.key, "--results", str(self.case.output)]
        code, cycle, error = self.cli([
            "iterate", *common, "--work-item", str(self.case.work_path),
            "--confirmation-work-item", str(self.case.final_path),
            "--confirmation-disclosure", str(self.case.disclosure_path), "--max-rounds", "2", "--live",
        ])
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(cycle["confirmation_status"], "passed")
        self.assertTrue(cycle["approval_eligible"])
        self.assertEqual(self.runtimes[0].generated, 2)
        self.assertIsNone(repositories.active_version(self.case.root, project_id="sample_repo", skill_key=self.case.key))
        candidate = cycle["selected_candidate_version_id"]
        evidence = cycle["cycle_ref"]["sha256"]
        code, approval, error = self.cli([
            "approve", *common, "--candidate-version", candidate, "--cycle", cycle["cycle_id"],
            "--evidence-sha256", evidence, "--expected-active-version", "none",
            "--expected-active-execution-sha256", "none", "--publish-reviewed",
        ], answer="approve " + candidate)
        self.assertEqual(code, 0, error)
        self.assertIsNone(repositories.active_version(self.case.root, project_id="sample_repo", skill_key=self.case.key))
        work = deepcopy(self.case.work)
        work.update(task_id="new-execution-task", request="A separately authorized next task.")
        work["input_sha256"] = integration.digest({key: value for key, value in work.items() if key != "input_sha256"})
        path = self.case.root / "next-work.json"
        path.write_bytes(results.encoded(work))
        return ["run-approved", *common, "--approval", approval["approval_id"], "--candidate-version", candidate,
                "--evidence-sha256", evidence, "--work-item", str(path), "--live", "--publish-reviewed"], candidate

    def test_actual_cli_chain_uses_fresh_runtime_and_only_observed_exact_capture_updates_active(self):
        args, candidate = self.approve_selected()
        immutable = {p: p.read_bytes() for p in self.case.output.glob("*/*/*.json")}
        code, used, error = self.cli(args)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(used["loaded_version_id"], candidate)
        self.assertTrue(used["skill_version_verified"])
        self.assertEqual(len(self.runtimes), 2)
        self.assertIsNot(self.runtimes[0], self.runtimes[1])
        self.assertEqual(self.runtimes[1].generated, 0)
        self.assertEqual(used["calls"], 1)
        self.assertEqual(repositories.active_version(self.case.root, project_id="sample_repo", skill_key=self.case.key), candidate)
        self.assertEqual(len(results.load_adoptions(self.case.output)), 2)
        self.assertEqual(immutable, {p: p.read_bytes() for p in immutable})
        self.assertEqual(results.tree_hash(self.case.project), self.case.work["project_tree_sha256"])
        private = results.read_json(self.case.root / ".skillops/executions" / (used["execution_id"] + ".json"))
        self.assertEqual(repositories.active_snapshot(self.case.root, project_id="sample_repo", skill_key=self.case.key),
                         {"version_id": candidate, "execution_sha256": integration.digest(private)})

    def test_next_use_activation_failure_does_not_write_a_receipt_or_active(self):
        args, _ = self.approve_selected()
        factory = self.factory
        def failed_factory(root):
            raw = factory(root)
            invoke = raw.invoke
            def missing_activation(*args, **kwargs):
                return {**invoke(*args, **kwargs), "skill_activated": False}
            raw.invoke = missing_activation
            return raw
        with patch.object(runner, "CopilotRuntime", side_effect=failed_factory):
            code, _, error = self.cli(args)
        self.assertEqual(code, 2)
        self.assertIn("skill_version_mismatch", error)
        self.assertIsNone(repositories.active_version(self.case.root, project_id="sample_repo", skill_key=self.case.key))
        self.assertFalse(list((self.case.root / ".skillops/executions").glob("*.json")))

    def test_verified_use_is_distinct_from_failed_task_outcome(self):
        args, candidate = self.approve_selected()
        factory = self.factory
        def failing_task(root):
            raw = factory(root)
            invoke = raw.invoke
            def wrong_output(*args, **kwargs):
                return {**invoke(*args, **kwargs), "content": json.dumps({"files": {"app.py": "value = 0\n"}})}
            raw.invoke = wrong_output
            return raw
        with patch.object(runner, "CopilotRuntime", side_effect=failing_task):
            code, used, error = self.cli(args)
        self.assertEqual((code, error), (2, ""))
        self.assertEqual(used["task_outcome"], "not_satisfied")
        self.assertTrue(used["skill_version_verified"])
        self.assertEqual(repositories.active_version(self.case.root, project_id="sample_repo", skill_key=self.case.key), candidate)
        report = next(row for row in results.load_reports(self.case.output) if row["run_id"] == used["run_id"])
        self.assertEqual(report["execution"]["status"], "failed")
        self.assertEqual(len(results.load_adoptions(self.case.output)), 2)

    def test_previously_retained_work_is_not_fresh_next_execution(self):
        args, _ = self.approve_selected()
        work = results.read_json(args[args.index("--work-item") + 1])
        runner.retain_work_item(self.case.root, work)
        code, _, error = self.cli(args)
        self.assertEqual(code, 2)
        self.assertIn("fresh_execution_work_required", error)
        self.assertEqual(self.runtimes[-1].calls, [])

    def test_next_use_cannot_reuse_the_experiment_budget(self):
        with self.assertRaises(RuntimeFailure) as raised:
            runner.run_approved(
                self.case.root, project_id="sample_repo", skill_key=self.case.key, approval_id="not-read",
                candidate_version_id="not-read", evidence_sha256="not-read", work_item="not-read",
                output=self.case.output, model="offline-model",
                policy={"enabled": True, "authenticated": True, "budget":{
                    "calls": 1, "max_calls": 30, "max_seconds": 120,
                    "deadline": time.monotonic() + 120, "max_ai_credits": 30,
                }})
        self.assertEqual(raised.exception.code, "fresh_execution_budget_required")
        self.assertEqual(self.runtimes, [])


if __name__ == "__main__":
    unittest.main()
