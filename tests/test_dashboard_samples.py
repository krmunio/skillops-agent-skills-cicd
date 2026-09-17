import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import project_results


ROOT = Path(__file__).resolve().parents[1]


class DashboardSamplesTests(unittest.TestCase):
    def samples(self):
        self.assertIsNotNone(importlib.util.find_spec("dashboard_samples"), "sample generator is missing")
        return importlib.import_module("dashboard_samples")

    def test_detected_projects_have_three_rounds_with_candidate_generation(self):
        module = self.samples()
        before = project_results.catalog(ROOT)
        with tempfile.TemporaryDirectory() as temp:
            index = project_results.reindex(ROOT, Path(temp))
            for identifier, count in (("project-a", 1), ("project-b", 14)):
                inventory = next(row["detected_skills"] for row in index["projects"] if row["id"] == identifier)
                sample = module.project_sample(identifier, inventory)
                self.assertIs(sample["synthetic"], True)
                self.assertEqual(sample["source_project_id"], identifier)
                self.assertNotEqual(sample["project_id"], identifier)
                self.assertEqual(len(sample["skills"]), count)
                self.assertEqual(sample["round_count"], 3)
                for skill, detected in zip(sample["skills"], inventory):
                    self.assertEqual(skill["id"], detected["skill_key"])
                    self.assertEqual(skill["source_path"], detected["source_path"])
                    self.assertEqual(len(skill["reports"]), 3)
                    self.assertEqual(len(skill["versions"]), 4)
                    candidates = set()
                    for report, scenario in zip(skill["reports"], ("improved", "unchanged", "regressed")):
                        self.assertEqual(report["scenario"], scenario)
                        self.assertEqual(report["origin"], "synthetic")
                        self.assertIsNone(report["execution"]["decision"])
                        detail = report["details"]
                        self.assertEqual(len(detail["quality"]), 7)
                        self.assertTrue(detail["tasks"])
                        generation = detail["generation"]
                        self.assertIs(generation["synthetic"], True)
                        self.assertEqual(generation["candidate_count"], 1)
                        self.assertEqual(generation["model_invocations"], 0)
                        self.assertEqual(generation["adoption"], "not_adopted")
                        self.assertIs(generation["requires_explicit_approval"], True)
                        self.assertEqual(generation["candidate_version"], detail["candidate"])
                        self.assertIn("+", generation["diff"])
                        self.assertNotEqual(skill["versions"][detail["base"]]["content"],
                                            skill["versions"][detail["candidate"]]["content"])
                        candidates.add(detail["candidate"])
                    self.assertEqual(len(candidates), 3)
                self.assertLess(len(project_results.encoded(sample)), project_results.LIMIT)
                self.assertEqual(sample, module.project_sample(identifier, inventory))
        self.assertEqual(before, project_results.catalog(ROOT), "sample generation must not modify project sources")

    def test_build_publishes_samples_outside_real_results(self):
        original = project_results.load_reports(ROOT / "results")
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site"
            index = project_results.build(ROOT, ROOT / "results", site)
            for identifier, count in (("project-a", 1), ("project-b", 14)):
                path = site / f"sample-{identifier}.json"
                self.assertTrue(path.exists(), "project sample must be included in the deployed build")
                sample = json.loads(path.read_text())
                self.assertEqual(len(sample["skills"]), count)
                self.assertNotIn(sample["project_id"], [entry["id"] for entry in index["projects"]])
            self.assertEqual(project_results.load_reports(site / "results"), original)

    def test_generated_scenarios_keep_quality_and_execution_distinct(self):
        module = self.samples()
        sample = module.project_sample("project-a", [{
            "skill_key": "path:" + "a" * 24, "display_name": "example", "source_path": "skills/example",
        }])
        for report in sample["skills"][0]["reports"]:
            detail = report["details"]
            metrics = report["execution"]["metrics"]
            base = metrics["base_correctness_successes"]
            candidate = metrics["candidate_correctness_successes"]
            score = detail["quality"][0]
            if report["scenario"] == "improved":
                self.assertGreater(candidate, base)
                self.assertGreater(score["candidate_score"], score["base_score"])
            elif report["scenario"] == "unchanged":
                self.assertEqual(candidate, base)
                self.assertEqual(score["candidate_score"], score["base_score"])
            else:
                self.assertLess(candidate, base)
                self.assertLess(score["candidate_score"], score["base_score"])
            self.assertIn("합성", detail["scope"])
            self.assertIn("미채택", detail["improvement"]["conclusion"])


if __name__ == "__main__":
    unittest.main()
