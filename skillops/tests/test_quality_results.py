from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from copilot_runtime import RuntimeFailure
import project_results as results
import quality_results
import skill_guide


class QualityResultsTests(unittest.TestCase):
    def setUp(self):
        self.rubric = results.read_json(Path(__file__).resolve().parents[2] / "eval/skill-guide-rubric.json")
        self.applicability = {key: "not_applicable" for key in ("progressive_disclosure", "resource_organization")}
        scores = [3, 2, 3, 3, None, None, 4]
        dimensions = {key: {"status": "not_applicable" if score is None else "review" if score < 3 else "pass",
                            "score": score, "rationale": "PRIVATE RATIONALE"}
                      for key, score in zip(self.rubric["dimensions"], scores)}
        judge = skill_guide.validate_judge(dimensions, self.rubric, self.applicability)
        self.result = {"status": "review", "judge_calls": 1, "judge": judge,
                       "static": {"findings": [], "applicability": self.applicability}}
        self.receipt = {
            "status": "completed", "returncode": 0, "role": "judge", "requested_model": "test-model",
            "content": json.dumps(dimensions), "prompt": "PRIVATE PROMPT", "elapsed_seconds": 2,
            "usage": {key: {"value": value, "unit": unit} for key, value, unit in
                      (("nano_aiu", 100, "nano_aiu"), ("input_tokens", 12, "tokens"), ("output_tokens", 8, "tokens"))},
        }
        self.summary = {
            "schema_version": 1, "project_id": "project_d", "purpose": "original_skill_quality",
            "execution_mode": "live", "created_at": "2026-10-15T01:44:30+00:00",
            "source_commit": "a" * 40, "project_tree_sha256": "b" * 64, "evaluator_sha256": "c" * 64,
            "original_version_id": "sha256:" + "d" * 64,
            "rubric_sha256": sha256(results.encoded(self.rubric)).hexdigest(),
            "model": "test-model", "model_calls": 1, "elapsed_seconds": 3,
            "status": "review", "static_findings": [], "judge": judge, "usage": self.receipt["usage"],
            "candidate_generated": False, "gepa_executed": False, "approved": False,
        }

    def report(self):
        return quality_results.project_report(
            self.summary, self.result, self.receipt, self.rubric, digest="e" * 64,
            tree_hash="b" * 64, evaluator_hash="c" * 64, version_id="sha256:" + "d" * 64)

    def test_quality_only_projection_has_no_private_text_or_fake_execution(self):
        row = self.report()
        self.assertEqual(row["execution"], results.axis("not_assessed", "quality_only"))
        metrics = row["guide"]["metrics"]
        self.assertEqual(metrics["mean_judge_score"], 75)
        self.assertEqual(metrics["judge_score_denominator"], 5)
        self.assertEqual(metrics["quality_workflow_clarity"], 2)
        self.assertIsNone(metrics["quality_resource_organization"])
        self.assertEqual(metrics["review"], 1)
        self.assertNotIn(b"PRIVATE", results.encoded(row))

    def test_rejects_cross_record_and_score_tampering(self):
        original = deepcopy(self.summary)
        for change in (
            {"model_calls": True}, {"candidate_generated": True}, {"status": "pass"},
            {"original_version_id": "sha256:" + "f" * 64},
            {"project_tree_sha256": "f" * 64}, {"elapsed_seconds": float("nan")},
            {"rubric_sha256": "f" * 64}, {"judge": {**original["judge"], "score": 100}},
        ):
            self.summary = {**deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(RuntimeFailure):
                self.report()

    def test_public_contract_rejects_fake_quality_or_candidate_claims(self):
        for mutate in (
            lambda r: r["guide"]["metrics"].update(mean_judge_score=100),
            lambda r: r["guide"]["metrics"].update(quality_workflow_clarity=True),
            lambda r: r["guide"]["metrics"].update(quality_workflow_clarity=5),
            lambda r: r["guide"]["metrics"].update(candidate_cost_nano_aiu=5),
            lambda r: r["execution"].update(status="completed"),
        ):
            row = self.report()
            mutate(row)
            with self.assertRaises(RuntimeFailure):
                results.validate(row)


if __name__ == "__main__":
    unittest.main()
