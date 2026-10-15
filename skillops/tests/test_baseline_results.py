from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import subprocess
import tempfile
import unittest

import baseline_results
from copilot_runtime import RuntimeFailure
import project_results as results


class BaselineResultsTests(unittest.TestCase):
    def setUp(self):
        self.work = {
            "schema_version": 1, "project_id": "project_d", "task_id": "failure-lifecycle",
            "source_commit": "a" * 40, "project_tree_sha256": "b" * 64,
            "split": "development", "request": "PRIVATE REQUEST", "sources": {"failure.py": "c" * 64},
            "checks": {"plan_sha256": "d" * 64, "protected_sha256": "e" * 64,
                       "required_case_ids": ["check-a", "check-b"], "required_gate_ids": []},
        }
        self.work["input_sha256"] = sha256(results.encoded(self.work)).hexdigest()
        self.version = "sha256:" + "f" * 64
        self.receipt = {
            "status": "completed", "returncode": 0, "role": "developer", "requested_model": "test-model",
            "skill_activated": True, "skill_version_verified": True, "staged_version_id": self.version,
            "elapsed_seconds": 2.5, "usage": {
                "nano_aiu": {"value": 123456, "unit": "nano_aiu"},
                "input_tokens": {"value": 10, "unit": "tokens"},
                "output_tokens": {"value": 20, "unit": "tokens"},
            },
            "prompt": "PRIVATE PROMPT", "content": "PRIVATE GENERATED SOURCE",
            "stderr": "PRIVATE LOG", "inventory": {"headers": "PRIVATE HEADER"},
        }
        self.summary = {
            "status": "completed", "execution_mode": "live", "model": "test-model",
            "eligible_for_gepa": False, "next_action": "stop_baseline_saturated",
            "confirmation_status": "not_run", "approval_eligible": False, "model_calls_attempted": 1,
            **{key: self.work[key] for key in ("project_id", "task_id", "source_commit", "input_sha256")},
            "original_version_id": self.version, "elapsed_seconds": 3.0,
            "required_scores": [1, 1], "required_total": 2, "required_passed": 2,
            "application": {
                "version_id": self.version, "staged_version_id": self.version, "activated": True,
                "work_sha256": self.work["input_sha256"], "task_outcome": "satisfied",
                "measurement": {"cost_nano_aiu": 123456, "elapsed_seconds": 2.5},
                "changed": ["PRIVATE PATH"],
            },
            "checks": {
                "plan_sha256": "d" * 64, "protected_sha256": "e" * 64, "environment_sha256": "1" * 64,
                "status": "failed", "elapsed_seconds": 0.1,
                "cases": [{"id": "check-a", "status": "passed"}, {"id": "check-b", "status": "passed"},
                          {"id": "other-role", "status": "failed"}], "gates": [],
            },
        }

    def report(self):
        return baseline_results.project_report(
            self.summary, self.work, self.receipt, tree_hash="b" * 64, evaluator_hash="2" * 64,
            created_at="2026-10-15T00:28:38+00:00", digest="3" * 64)

    def test_projection_is_allowlisted_and_keeps_stages_separate(self):
        row = self.report()
        self.assertEqual(row["purpose"], "baseline")
        self.assertEqual(row["guide"], results.axis("not_assessed", "baseline_screening_only"))
        self.assertEqual(row["execution"]["reason_code"], "baseline_saturated")
        self.assertIsNone(row["execution"]["decision"])
        metrics = row["execution"]["metrics"]
        self.assertEqual((metrics["requested"], metrics["required_cases"], metrics["passed_cases"]), (1, 2, 2))
        self.assertFalse(any(key.startswith("candidate_") for key in metrics))
        self.assertNotIn(b"PRIVATE", results.encoded(row))
        self.assertEqual(set(row), results.FIELDS)

    def test_cross_record_tampering_and_unverified_execution_are_rejected(self):
        original_summary, original_receipt = deepcopy(self.summary), deepcopy(self.receipt)
        changes = [
            lambda s, r: s.update(eligible_for_gepa=True),
            lambda s, r: s.update(confirmation_status="passed"),
            lambda s, r: s.update(model_calls_attempted=True),
            lambda s, r: s.update(required_total=True),
            lambda s, r: s.update(required_scores=[True, True]),
            lambda s, r: s.update(source_commit="9" * 40),
            lambda s, r: s.update(required_passed=1),
            lambda s, r: s["checks"]["cases"][0].update(status="failed"),
            lambda s, r: s["checks"].update(protected_sha256="9" * 64),
            lambda s, r: s["application"].update(staged_version_id="sha256:" + "0" * 64),
            lambda s, r: r.update(skill_activated=False),
            lambda s, r: r.update(requested_model="other-model"),
            lambda s, r: r.update(returncode=False),
            lambda s, r: r["usage"]["nano_aiu"].update(value=True),
            lambda s, r: r["usage"]["nano_aiu"].update(value=999),
            lambda s, r: r["usage"]["input_tokens"].update(unit="currency"),
            lambda s, r: s.update(elapsed_seconds=float("nan")),
            lambda s, r: s.update(elapsed_seconds=0.1),
        ]
        for index, change in enumerate(changes):
            self.summary, self.receipt = deepcopy(original_summary), deepcopy(original_receipt)
            change(self.summary, self.receipt)
            with self.subTest(change=index), self.assertRaises(RuntimeFailure):
                self.report()

    def test_work_digest_and_unknown_usage_are_not_invented(self):
        self.work["request"] = "tampered"
        with self.assertRaises(RuntimeFailure):
            self.report()
        self.work["request"] = "PRIVATE REQUEST"
        self.receipt["usage"]["nano_aiu"]["value"] = None
        self.summary["application"]["measurement"]["cost_nano_aiu"] = None
        self.assertIsNone(self.report()["execution"]["metrics"]["base_cost_nano_aiu"])
        self.receipt["usage"] = {}
        self.assertIsNone(self.report()["execution"]["metrics"]["input_tokens"])
        self.assertIsNone(self.report()["execution"]["metrics"]["base_cost_nano_aiu"])

    def test_public_contract_rejects_impossible_counts_and_fake_candidate_metrics(self):
        for change in (
            lambda r: r["execution"]["metrics"].update(passed_cases=3),
            lambda r: r["execution"]["metrics"].update(passed_cases=True),
            lambda r: r["execution"]["metrics"].update(required_cases=None, passed_cases=None),
            lambda r: r["execution"]["metrics"].update(candidate_cost_nano_aiu=0),
            lambda r: r["execution"]["metrics"].update(cost_improvement_percent=100),
            lambda r: r["execution"].update(decision="eligible_for_canary"),
            lambda r: r["guide"].update(status="completed"),
        ):
            row = self.report()
            change(row)
            with self.assertRaises(RuntimeFailure):
                results.validate(row)

    def test_committed_identity_ignores_today_uncommitted_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "projects/project_d"
            project.mkdir(parents=True)
            (project / "failure.py").write_text("original source\n")
            (root / "eval").mkdir()
            (root / "eval/rubric.json").write_text("{}\n")
            for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture",
                         "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Baseline")):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            expected = (results.tree_hash(project), results.evaluator_hash(root))
            (project / "failure.py").write_text("changed source\n")
            (root / "eval/rubric.json").write_text('{"changed": true}\n')
            self.assertEqual(baseline_results.committed_identity(root, commit, "project_d"), expected)
            with self.assertRaises(RuntimeFailure):
                baseline_results.committed_identity(root, "--help", "project_d")

    def test_projection_uses_existing_immutable_result_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.report()
            path = results.store(directory, row)
            self.assertEqual(results.store(directory, row), path)
            self.assertEqual(results.load_reports(directory), [row])
            row["execution"]["metrics"]["input_tokens"] = 11
            with self.assertRaises(RuntimeFailure):
                results.store(directory, row)


if __name__ == "__main__":
    unittest.main()
