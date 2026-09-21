import json
from pathlib import Path
import unittest


class PublishedEvidenceTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "evidence/baseline-v2.json"
        self.assertTrue(path.is_file(), "Historical public evidence has not been generated.")
        self.text = path.read_text()
        self.data = json.loads(self.text)

    def test_historical_origin_and_private_content_exclusion(self):
        self.assertEqual(set(self.data), {
            "schema_version", "origin", "remote_model_run", "baseline", "calibration",
        })
        self.assertEqual(self.data["schema_version"], 1)
        self.assertEqual(self.data["origin"], "historical-local-copilot-run")
        self.assertIs(self.data["remote_model_run"], False)
        for forbidden in ("/home/", "/tmp/", "runs/", ".skillops-private", "session_id",
                          "rationale", "disabledSkills", "github_pat_", "ghp_", "COPILOT_GITHUB_TOKEN"):
            self.assertNotIn(forbidden, self.text)
        for report in (self.data["baseline"], self.data["calibration"]):
            self.assertRegex(report["original_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(report["fingerprint"], r"^[0-9a-f]{64}$")
            for value in report["input_sha256"].values():
                self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertEqual(self.data["baseline"]["fingerprint"], self.data["calibration"]["fingerprint"])
        self.assertEqual(self.data["baseline"]["input_sha256"], self.data["calibration"]["input_sha256"])

    def test_task_denominators_and_recorded_quality(self):
        baseline = self.data["baseline"]
        rows = baseline["tasks"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(len({row["id"] for row in rows}), 5)
        self.assertEqual(baseline["aggregate"], {
            "requested": 5, "attempted": 5, "evaluation_completed": 5,
            "errors": 0, "blocked": 0, "correctness_successes": 5,
            "mean_judge_score": 100.0, "judge_score_denominator": 5,
        })
        self.assertEqual({r["family"] for r in rows}, {"listing", "labels", "updates"})
        for row in rows:
            self.assertEqual(set(row), {
                "id", "family", "split", "status", "attempted", "skill_activated",
                "seed_sha256", "source_sha256", "fixed", "generated", "judge",
                "developer_usage", "judge_usage", "elapsed_seconds",
            })
            self.assertEqual(row["status"], "completed")
            self.assertTrue(row["attempted"])
            self.assertTrue(row["skill_activated"])
            expected = {"listing": 26, "labels": 17, "updates": 22}[row["family"]]
            self.assertEqual(row["fixed"], {"all_passed": True, "passed": expected, "total": expected})
            self.assertTrue(row["generated"]["passed"])
            self.assertGreater(row["generated"]["tests_run"], 0)
            self.assertEqual(row["judge"]["score"], 100.0)
            self.assertEqual(row["split"], "heldout" if row["family"] == "updates" else "development")
            for usage in (row["developer_usage"], row["judge_usage"]):
                self.assertEqual(set(usage), {
                    "nano_aiu", "premium_request_cost", "api_duration_ms", "input_tokens",
                    "output_tokens", "cache_read_tokens", "cache_write_tokens",
                })
                for metric in usage.values():
                    self.assertEqual(set(metric), {"value", "unit", "reason"})
                    if metric["value"] is None:
                        self.assertEqual(metric["reason"], "not_reported_by_cli")
                    else:
                        self.assertIn(type(metric["value"]), (int, float))
                        self.assertGreaterEqual(metric["value"], 0)
                        self.assertIsNone(metric["reason"])

    def test_nine_family_calibration_controls(self):
        controls = self.data["calibration"]["results"]
        self.assertEqual(len(controls), 9)
        self.assertIs(self.data["calibration"]["passed"], True)
        for family in ("listing", "labels", "updates"):
            group = {row["id"]: row for row in controls if row["family"] == family}
            self.assertEqual(set(group), {"known_good", "known_bad", "instruction_in_data"})
            self.assertGreater(group["known_good"]["judge"]["score"], group["known_bad"]["judge"]["score"])
            self.assertLess(group["instruction_in_data"]["judge"]["score"], 100)
            self.assertTrue(group["known_good"]["fixed"]["all_passed"])
            self.assertFalse(group["known_bad"]["fixed"]["all_passed"])


if __name__ == "__main__":
    unittest.main()
