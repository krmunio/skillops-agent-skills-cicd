from copy import deepcopy
from pathlib import Path
from publication_fixtures import dashboard_fixture
import tempfile
import unittest

import dashboard_samples
import project_results as results
import skill_assessments


ROOT = Path(__file__).resolve().parents[1]


class DashboardSamplesTests(unittest.TestCase):
    def generate(self, destination):
        self.assertTrue(hasattr(dashboard_samples, "seed"), "native results generator is missing")
        return dashboard_samples.seed(ROOT, destination)

    def test_native_rounds_have_all_skills_and_valid_version_bound_candidates(self):
        before = results.catalog(ROOT)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            self.assertEqual(self.generate(output), 6)
            reports = results.load_reports(output)
            lifecycles = results.load_evolution(output, reports)
            assessments = results.load_assessments(output, reports, lifecycles)
            self.assertEqual(len(reports), 6)
            candidates = 0
            for identifier, count in (("project-a", 1), ("project-b", 14)):
                selected = sorted((r for r in reports if r["project_id"] == identifier),
                                  key=lambda r: r["created_at"], reverse=True)
                self.assertEqual(len(selected), 3)
                for report, expected in zip(selected, ("improved", "not_improved", "rejected")):
                    self.assertEqual(report["origin"], "sample")
                    self.assertTrue(report["run_id"].startswith("sample-"))
                    self.assertIsNone(report["source_commit"])
                    self.assertIsNone(report["evaluator_sha256"])
                    self.assertIsNone(report["execution"]["decision"])
                    self.assertEqual(report["guide"]["metrics"]["cli_invocations"], 0)
                    key = (identifier, report["run_id"])
                    rows = assessments[key]["skills"]
                    self.assertEqual(len(rows), count)
                    self.assertEqual(lifecycles[key]["records"]["adoptions"], [])
                    for row in rows:
                        self.assertEqual(row["decision"]["status"], expected)
                        self.assertEqual(row["decision"], skill_assessments.decide(row))
                        self.assertEqual(row["generation"]["status"], "generated")
                        self.assertNotEqual(row["base_version_id"], row["candidate_version_id"])
                        self.assertEqual(len(row["quality"]["base"]["dimensions"]), 7)
                        self.assertEqual(len(row["checks"]["candidate"]["cases"]), 5)
                        candidates += 1
                    for filename in ("report.json", "skill-assessments.json", "skill-evolution.json"):
                        self.assertTrue((output / identifier / report["run_id"] / filename).is_file())
                entry = next(p for p in results.read_json(output / "index.json")["projects"] if p["id"] == identifier)
                self.assertIsNone(entry["current_run"])
            self.assertEqual(candidates, 45)
            original = {p.relative_to(output): p.read_bytes() for p in output.glob("*/*/*.json")}
            self.assertEqual(self.generate(output), 6)
            self.assertEqual(original, {p.relative_to(output): p.read_bytes() for p in output.glob("*/*/*.json")})
        self.assertEqual(before, results.catalog(ROOT))

    def test_sample_provenance_cannot_be_relabelled_as_real_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            self.generate(Path(temp))
            report = results.load_reports(Path(temp))[0]
            for mutation in ({"origin": "github_actions"}, {"origin": "local"},
                             {"run_id": "123-1"}, {"source_commit": "a" * 40},
                             {"evaluator_sha256": "b" * 64}):
                changed = {**report, **mutation}
                with self.subTest(mutation=mutation), self.assertRaises(results.RuntimeFailure):
                    results.validate(changed)
            changed = deepcopy(report)
            changed["guide"]["metrics"]["cli_invocations"] = 1
            with self.assertRaises(results.RuntimeFailure):
                results.validate(changed)

    def test_publisher_merges_checked_in_examples_before_a_pure_build(self):
        self.assertTrue(hasattr(results, "merge_samples"), "sample result merge is missing")
        real = [r for r in results.load_reports(ROOT / "results") if r["origin"] != "sample"]
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            data, site = folder / "data", folder / "site"
            for report in real:
                results.store(data, report)
            results.merge_samples(ROOT, data)
            results.build(dashboard_fixture(folder / "publisher"), data, site)
            built = results.load_reports(site / "results")
            self.assertEqual([r for r in built if r["origin"] != "sample"], real)
            self.assertEqual(len([r for r in built if r["origin"] == "sample"]), 6)
            self.assertFalse((site / "sample-project-a.json").exists())
            self.assertFalse((site / "sample-project-b.json").exists())
            results.load_assessments(site / "results", built)
            results.merge_samples(ROOT, data)
            results.merge_samples(ROOT, data)
            self.assertEqual(len([r for r in results.load_reports(data) if r["origin"] == "sample"]), 6)


if __name__ == "__main__":
    unittest.main()
