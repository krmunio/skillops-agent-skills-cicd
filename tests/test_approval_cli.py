"""CLI authorization boundary tests; positive inputs are synthetic, temporary contracts."""

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sys
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

    def select_preflight(self):
        self.argv[1] = "approval-preflight"
        for flag in ("--expected-active-version", "--expected-active-execution-sha256"):
            offset = self.argv.index(flag)
            del self.argv[offset:offset + 2]

    def test_preflight_noninteractive_first_use_exact_argv_and_private_output(self):
        import project_evaluation
        import skill_approvals
        self.select_preflight()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.object(skillops, "CopilotRuntime", side_effect=AssertionError("runtime")), \
                patch.object(skill_approvals, "approve", side_effect=AssertionError("approve")), \
                patch.object(project_evaluation, "persist_adoption", side_effect=AssertionError("publication")), \
                patch.object(project_results, "atomic_json", side_effect=AssertionError("write")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch.object(os, "fsync", side_effect=AssertionError("fsync")), \
                patch.object(skill_approvals, "_evidence", wraps=skill_approvals._evidence) as evidence:
            status, output, errors, prompt = self.run_cli(interactive=False)
        self.assertEqual(status, 0, errors or output)
        report = json.loads(output)
        self.assertEqual(report["status"], "eligible")
        self.assertEqual(report["trust_scope"], "local_private")
        self.assertTrue(report["observation_only"])
        self.assertFalse(report["reserved"])
        self.assertTrue(report["approval_revalidates"])
        self.assertFalse(report["active_changed"])
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["target"], {"root": str(self.root), "project_id": "sample_repo",
                                          "skill_key": self.cycle["skill_key"]})
        self.assertEqual(report["binding"]["candidate_version_id"], self.candidate)
        self.assertEqual(report["binding"]["cycle_id"], self.cycle["cycle_id"])
        self.assertEqual(report["binding"]["evidence_sha256"], self.evidence)
        self.assertEqual(report["results_path"], str(self.fixture.output))
        self.assertEqual(report["approval_argv"], [
            sys.executable, "-B", str(self.root / "skillops.py"), "approve",
            "--project", "sample_repo", "--skill-key", self.cycle["skill_key"],
            "--candidate-version", self.candidate, "--cycle", self.cycle["cycle_id"],
            "--evidence-sha256", self.evidence, "--results", str(self.fixture.output),
            "--expected-active-version", "none", "--expected-active-execution-sha256", "none",
        ])
        evidence.assert_called_once()
        prompt.assert_not_called()
        self.assertEqual(errors, "")
        for forbidden in ("environment_id", "approved_by", "uid:", '"request"', "publish-reviewed"):
            self.assertNotIn(forbidden, output)
        with self.assertRaises(repositories.RuntimeFailure):
            project_results.validate(report)
        self.assertFalse((self.root / ".skillops").exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_preflight_bad_evidence_is_blocked_without_actionable_command(self):
        self.select_preflight()
        self.argv[self.argv.index("--evidence-sha256") + 1] = "0" * 64
        with patch.object(skillops, "CopilotRuntime", side_effect=AssertionError("runtime")):
            status, output, errors, prompt = self.run_cli(interactive=False)
        self.assertEqual(status, 2)
        report = json.loads(output)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["active"], {"state": "unknown"})
        self.assertEqual(report["blockers"], ["approval_evidence_changed"])
        self.assertNotIn("approval_argv", report)
        self.assertEqual(errors, "")
        prompt.assert_not_called()
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_argv_preserves_results_path_spaces_and_existing_active_pair(self):
        import skill_approvals
        approved = self.fixture.approve_cycle(self.cycle)
        receipt = approval_fixtures.ApprovalTests.receipt(self.fixture, approved)
        skill_approvals.record_execution(self.root, approval=approved, receipt=receipt)
        path = self.root / "results with spaces"
        self.fixture.output.rename(path)
        self.select_preflight()
        self.argv[self.argv.index("--results") + 1] = str(path)
        status, output, errors, prompt = self.run_cli(interactive=False)
        self.assertEqual(status, 0, errors or output)
        report = json.loads(output)
        argv = report["approval_argv"]
        self.assertEqual(argv[argv.index("--results") + 1], str(path))
        self.assertEqual(argv[argv.index("--expected-active-version") + 1], self.candidate)
        self.assertEqual(argv[argv.index("--expected-active-execution-sha256") + 1],
                         approval_fixtures.digest(receipt))
        self.assertNotIn("none", argv)
        prompt.assert_not_called()

    def test_preflight_real_validator_rejects_malformed_graph_without_initialization(self):
        self.select_preflight()
        path = self.fixture.output / "sample_repo" / self.cycle["cycle_id"] / "cycle.json"
        path.write_bytes(b"[]\n")
        self.argv[self.argv.index("--evidence-sha256") + 1] = sha256(path.read_bytes()).hexdigest()
        status, output, _, _ = self.run_cli(interactive=False)
        self.assertEqual(status, 2)
        report = json.loads(output)
        self.assertTrue(report["blockers"])
        self.assertNotIn("approval_argv", report)
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_ci_is_blocked_without_initialization(self):
        self.select_preflight()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            status, output, _, prompt = self.run_cli(interactive=False)
        self.assertEqual(status, 2)
        report = json.loads(output)
        self.assertEqual(report["blockers"], ["local_approval_only"])
        self.assertNotIn("approval_argv", report)
        prompt.assert_not_called()
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_parser_requires_exact_inputs_and_has_no_write_options(self):
        self.select_preflight()
        complete = self.argv[:]
        variants = []
        for flag in ("--project", "--skill-key", "--candidate-version", "--cycle", "--evidence-sha256", "--results"):
            offset = complete.index(flag)
            variants.append(complete[:offset] + complete[offset + 2:])
        variants.extend(complete + [flag] for flag in ("--live", "--publish-reviewed", "--latest"))
        variants.append(complete + ["--expected-active-version", "none"])
        for argv in variants:
            errors = io.StringIO()
            with self.subTest(argv=argv), patch("sys.argv", argv), \
                    patch.object(skillops, "CopilotRuntime", side_effect=AssertionError("runtime")), \
                    redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
                skillops.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertNotIn("invalid choice", errors.getvalue())
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_command_does_not_bypass_later_human_or_evidence_revalidation(self):
        self.select_preflight()
        status, output, _, _ = self.run_cli(interactive=False)
        self.assertEqual(status, 0)
        self.argv = json.loads(output)["approval_argv"][2:]
        status, _, errors, prompt = self.run_cli(interactive=False)
        self.assertEqual(status, 2)
        self.assertIn("interactive_approval_required", errors)
        prompt.assert_not_called()
        path = self.fixture.output / "sample_repo" / self.cycle["cycle_id"] / "cycle.json"
        path.write_bytes(path.read_bytes() + b"\n")
        status, _, errors, prompt = self.run_cli(response="approve " + self.candidate)
        self.assertEqual(status, 2)
        self.assertIn("approval_evidence_changed", errors)
        prompt.assert_called_once()
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

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

    def test_run_approved_requires_a_separate_live_opt_in_and_blocks_ci(self):
        import project_evaluation
        approved = self.fixture.approve_cycle(self.cycle)
        self.argv = [
            "skillops", "run-approved", "--project", "sample_repo", "--skill-key", self.cycle["skill_key"],
            "--candidate-version", self.candidate, "--approval", approved["approval_id"],
            "--evidence-sha256", self.evidence, "--work-item", "not-read-before-authorization.json",
            "--results", str(self.fixture.output),
        ]
        with patch.object(project_evaluation, "policy_from_environment", return_value={
                "enabled": True, "authenticated": True, "budget": {}}):
            status, _, errors, prompt = self.run_cli()
        self.assertEqual(status, 2)
        self.assertIn("live_disabled", errors)
        prompt.assert_not_called()
        self.argv.append("--live")
        with patch.dict(os.environ, {"CI": "true"}):
            status, _, errors, _ = self.run_cli()
        self.assertEqual(status, 2)
        self.assertIn("local_approval_only", errors)
        self.assertFalse((self.root / ".skillops/active.json").exists())


if __name__ == "__main__":
    unittest.main()
