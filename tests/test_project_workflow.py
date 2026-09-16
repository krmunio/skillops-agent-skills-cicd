from pathlib import Path
import unittest


class WorkflowContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
