from pathlib import Path
import unittest


class WorkflowContractTests(unittest.TestCase):
    def test_evaluation_exposes_stage_logs_and_validated_failure_summary(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        self.assertIn("SKILLOPS_ACTIONS_PROGRESS: 'true'", evaluate)
        self.assertIn("name: Summarize baseline and project evaluation\n        if: always()", evaluate)
        self.assertIn('python3 evaluation_reporting.py --results ci-results --run-id "$RESULT_RUN" >> "$GITHUB_STEP_SUMMARY"', evaluate)
        self.assertLess(evaluate.index("python3 project_evaluation.py "), evaluate.index("python3 evaluation_reporting.py "))
        self.assertLess(evaluate.index("python3 evaluation_reporting.py "), evaluate.index("name: public-project-results"))

    def test_repository_validation_is_not_named_skill_baseline_evaluation(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/ci.yml").read_text()
        self.assertTrue(text.startswith("name: SkillOps validation\n"))
        self.assertIn("## SkillOps validation", text)
        self.assertIn("  offline-and-container:", text)
        self.assertIn("SKILLOPS_CONTAINER_TESTS", text)
        self.assertIn("python -m unittest discover", text)
        self.assertNotIn("copilot-requests:", text)
        self.assertNotIn("${{ secrets.", text)

    def test_project_triggers_and_dispatch_keep_live_execution_explicit(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        triggers = text.split("\npermissions:", 1)[0]
        self.assertIn("  pull_request:\n    branches: [main]", triggers)
        self.assertIn("  push:\n    branches: [main]", triggers)
        self.assertIn("      - 'projects/**'", triggers)
        self.assertIn("  workflow_dispatch:", triggers)
        self.assertRegex(triggers, r"(?s)      live:\n.*?type: boolean\n        default: false")
        self.assertNotIn("evaluation-results", triggers)
        self.assertNotIn("pull_request_target", triggers)
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        self.assertIn("needs: contracts", evaluate)
        self.assertIn("github.ref == 'refs/heads/main'", evaluate)
        self.assertIn("github.event_name == 'push' || github.event_name == 'workflow_dispatch'", evaluate)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.live", evaluate)
        self.assertEqual(evaluate.count("python3 project_evaluation.py "), 1)
        self.assertNotIn("strategy:", evaluate)

    def test_job_permissions_and_failure_evidence_are_separated(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        contracts = text.split("\n  contracts:\n", 1)[1].split("\n  evaluate:\n", 1)[0]
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        persist = text.split("\n  persist:\n", 1)[1].split("\n  deploy:\n", 1)[0]
        deploy = text.split("\n  deploy:\n", 1)[1]
        self.assertNotIn("${{ secrets.", contracts)
        self.assertIn("SKILLOPS_LIVE_EVALUATION_ENABLED: 'false'", contracts)
        self.assertIn('test "$assessment_status" -eq 2', contracts)
        self.assertIn("copilot-requests: write", evaluate)
        self.assertNotIn("contents: write", evaluate)
        self.assertNotIn("SKILLOPS_SWA_DEPLOYMENT_TOKEN", evaluate)
        self.assertIn("if: always()", evaluate)
        self.assertIn("name: public-project-results", evaluate)
        self.assertIn("needs: evaluate", persist)
        self.assertIn("needs.evaluate.result == 'failure'", persist)
        self.assertIn("python3 project_results.py validate --results ci-results", persist)
        self.assertIn("contents: write", persist)
        self.assertNotIn("copilot-requests:", persist)
        self.assertIn("needs: persist", deploy)
        self.assertIn("needs.persist.result == 'success'", deploy)
        self.assertIn("SKILLOPS_SWA_DEPLOYMENT_TOKEN", deploy)

    def test_privileged_workflow_is_main_only_and_publishes_data_branch(self):
        root = Path(__file__).resolve().parents[1]
        path = root / ".github/workflows/project-evaluation.yml"
        self.assertTrue(path.is_file(), "project workflow is missing")
        text = path.read_text()
        self.assertNotIn("pull_request_target", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("evaluation-results", text)
        self.assertNotIn("git add .", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("SKILLOPS_MAX_INVOCATIONS", text)
        self.assertIn("SKILLOPS_LIVE_EVALUATION_ENABLED", text)
        self.assertIn("if: always()", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_infrastructure_is_swa_only_and_not_free(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "infra/public-dashboard.bicep"
        self.assertTrue(path.is_file(), "SWA template is missing")
        text = path.read_text()
        self.assertIn("Microsoft.Web/staticSites@", text)
        self.assertIn("'Standard'", text)
        self.assertNotIn("Microsoft.Storage", text)
        self.assertNotIn("repositoryToken", text)

    def test_automatic_evaluation_has_scoped_auth_history_and_both_language_images(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        self.assertIn("copilot-requests: write", text)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", text)
        self.assertIn("SKILLOPS_MAX_AI_CREDITS_PER_SESSION", text)
        self.assertIn("--history .prior-results/results", text)
        self.assertIn("group: skillops-project-evaluation-${{ github.ref }}", text)
        for workflow in ("ci.yml", "project-evaluation.yml"):
            self.assertIn("node@sha256:", (root / ".github/workflows" / workflow).read_text())


if __name__ == "__main__":
    unittest.main()
