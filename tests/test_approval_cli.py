"""CLI authorization boundary tests; positive inputs are synthetic, temporary contracts."""

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import os
import unittest
from unittest.mock import patch

import repositories
import project_results
import skillops
import test_skill_approvals as approval_fixtures


class ApprovalCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = approval_fixtures.ApprovalIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cycle = self.fixture.synthetic_live_contract()
        self.root = self.fixture.root
        self.evidence = sha256((self.fixture.output / "sample_repo" / self.cycle["cycle_id"] / "cycle.json").read_bytes()).hexdigest()
        self.candidate = self.cycle["selected_candidate_version_id"]
        self.argv = [
            "skillops", "approve", "--project", "sample_repo", "--skill-key", self.cycle["skill_key"],
            "--candidate-version", self.candidate, "--cycle", self.cycle["cycle_id"],
            "--evidence-sha256", self.evidence, "--expected-active-version", "none",
            "--expected-active-execution-sha256", "none", "--results", str(self.fixture.output),
        ]

    def run_cli(self, *, interactive=True, response=None):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(skillops, "__file__", str(self.root / "skillops.py")), \
                patch("sys.argv", self.argv), patch("sys.stdin.isatty", return_value=interactive), \
                patch.object(errors, "isatty", return_value=interactive), \
                patch("builtins.input", return_value=response or "cancel") as prompt, \
                redirect_stdout(output), redirect_stderr(errors):
            try:
                status = skillops.main()
            except SystemExit as error:
                self.fail(f"Approval command is not wired: {error}")
        return status, output.getvalue(), errors.getvalue(), prompt

    def test_ci_and_noninteractive_refused_before_human_prompt(self):
        for ci, interactive in (("true", True), ("", False)):
            with self.subTest(ci=ci), patch.dict(os.environ, {"CI": ci, "GITHUB_ACTIONS": ""}):
                status, _, errors, prompt = self.run_cli(interactive=interactive)
                self.assertEqual(status, 2)
                self.assertIn("local_approval_only" if ci else "interactive_approval_required", errors)
                prompt.assert_not_called()
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

    def test_separate_human_confirmation_is_required_and_shows_full_binding(self):
        status, _, errors, prompt = self.run_cli()
        self.assertEqual(status, 2)
        prompt.assert_called_once()
        for value in ("sample_repo", self.cycle["skill_key"], self.candidate, self.evidence,
                      "previous_active_version_id", "previous_active_execution_sha256", "null"):
            self.assertIn(value, errors)
        self.assertIn("approval_cancelled", errors)
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

    def test_synthetic_positive_cli_uses_real_approval_store_but_never_sets_active(self):
        status, output, _, _ = self.run_cli(response="approve " + self.candidate)
        self.assertEqual(status, 0)
        self.assertIn('"status": "approved"', output)
        self.assertNotIn("environment_id", output)
        self.assertNotIn("uid:", output)
        self.assertEqual(len(list((self.root / ".skillops/approvals").glob("*.json"))), 1)
        self.assertIsNone(repositories.active_version(
            self.root, project_id="sample_repo", skill_key=self.cycle["skill_key"]))

    def test_stale_or_half_null_pair_is_not_a_wildcard(self):
        self.argv[self.argv.index("--expected-active-version") + 1] = self.candidate
        status, _, errors, prompt = self.run_cli()
        self.assertEqual(status, 2)
        self.assertIn("invalid_active_pair", errors)
        prompt.assert_not_called()
        self.argv[self.argv.index("--expected-active-execution-sha256") + 1] = "1" * 64
        status, _, errors, prompt = self.run_cli()
        self.assertEqual(status, 2)
        self.assertIn("active_conflict", errors)
        prompt.assert_not_called()

    def test_reviewed_cli_projection_uses_real_public_loader(self):
        self.argv.append("--publish-reviewed")
        status, output, _, _ = self.run_cli(response="approve " + self.candidate)
        self.assertEqual(status, 0)
        self.assertIn('"publication_status": "stored_locally"', output)
        data = list(project_results.load_adoptions(self.fixture.output).values())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["executions"], [])
        self.assertEqual(data[0]["approvals"][0]["approved_by"], "local_operator")


if __name__ == "__main__":
    unittest.main()
