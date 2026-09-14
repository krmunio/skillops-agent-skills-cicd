import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skillops.core import DEMO_SNAPSHOT, DEMO_TARGET_REPO, RunResult, evaluate_artifact, validate_manifest

ROOT = Path(__file__).resolve().parents[1]


class SkillOpsCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "skillops", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_risky_candidate_is_rejected_for_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("demo", "risky", "--out-dir", tmp)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["execution_mode"], "fixture")
        self.assertEqual(report["decision"], "REJECTED")
        self.assertIn("GET retry limit is not exceeded", report["regressions"])
        self.assertIn("POST with possible side effect is not retried", report["regressions"])

    def test_corrected_fixture_candidate_is_demo_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("demo", "corrected", "--out-dir", tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
            markdown = (Path(tmp) / "report.md").read_text(encoding="utf-8")
        self.assertEqual(report["decision"], "DEMO_ONLY")
        self.assertEqual(report["summary"], {"candidate_passed": 4, "total_cases": 4})
        self.assertIn("fixture 결과는 파이프라인 데모", markdown)

    def test_unauthenticated_live_request_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("demo", "live-blocked", "--out-dir", tmp)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["execution_mode"], "live")
        self.assertEqual(report["decision"], "BLOCKED")
        self.assertIn("no supported authenticated runner interface", report["decision_reasons"][0])

    def test_missing_candidate_artifact_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "evaluate",
                "--execution-mode", "fixture",
                "--baseline-skill", "examples/skills/http-retry/baseline",
                "--candidate-skill", "examples/skills/http-retry/corrected-candidate",
                "--baseline-artifact", "examples/fixtures/http-retry/baseline/retry_client.py",
                "--candidate-artifact", "examples/fixtures/http-retry/corrected-candidate/missing.py",
                "--target-repo", DEMO_TARGET_REPO,
                "--snapshot", DEMO_SNAPSHOT,
                "--out-dir", tmp,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["decision"], "REJECTED")
        self.assertFalse(report["candidate"]["valid_result_data"])

    def test_manifest_validation_accepts_pinned_example_and_rejects_bad_manifest(self):
        ok, errors = validate_manifest(ROOT / "examples/manifests/corrected-fixture.json")
        self.assertTrue(ok, errors)
        bad_ok, bad_errors = validate_manifest(ROOT / "examples/manifests/invalid-manifest.json")
        self.assertFalse(bad_ok)
        self.assertTrue(any("target_commit_sha" in error for error in bad_errors))
        self.assertTrue(any("skill_content_hash" in error for error in bad_errors))

    def test_manifest_validation_cli_reports_json_and_exit_codes(self):
        valid = self.run_cli("validate-manifest", "--manifest", "examples/manifests/corrected-fixture.json")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertTrue(json.loads(valid.stdout)["valid"])
        invalid = self.run_cli("validate-manifest", "--manifest", "examples/manifests/invalid-manifest.json")
        self.assertEqual(invalid.returncode, 1, invalid.stderr)
        self.assertFalse(json.loads(invalid.stdout)["valid"])

    def test_protected_file_change_rejects_candidate(self):
        artifact = ROOT / "examples/fixtures/http-retry/corrected-candidate/retry_client.py"
        result = RunResult(
            subject="candidate",
            skill_hash="sha256:" + "0" * 64,
            workspace=artifact.parent,
            artifact_path=artifact,
            changed_files=["tests/test_skillops.py"],
        )
        evaluation = evaluate_artifact(result)
        self.assertEqual(evaluation["protected_files_changed"], ["tests/test_skillops.py"])

    def test_untrusted_artifact_is_not_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "retry_client.py"
            artifact.write_text("raise RuntimeError('should not execute')\n", encoding="utf-8")
            result = RunResult(
                subject="candidate",
                skill_hash="sha256:" + "0" * 64,
                workspace=Path(tmp),
                artifact_path=artifact,
                changed_files=[],
            )
            evaluation = evaluate_artifact(result)
        self.assertFalse(evaluation["valid_result_data"])
        self.assertEqual(evaluation["cases"][0]["name"], "trusted fixture artifact")


if __name__ == "__main__":
    unittest.main()
