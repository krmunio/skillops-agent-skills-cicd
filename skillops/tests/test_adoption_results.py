"""Synthetic offline public adoption graphs, never operational approval evidence."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from copilot_runtime import RuntimeFailure
from hackathon_fixtures import fixture, write_results
import project_results as results
from publication_fixtures import dashboard_fixture


class AdoptionResultsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "fixture").mkdir()
        self.data = fixture(self.root / "fixture")
        self.output = write_results(self.root / "results", self.data)
        cycle = self.data["cycle"]
        self.report = deepcopy(self.data["cycle_report"])
        self.report["run_id"] = "104-1"
        results.store(self.output, self.report)
        self.approval = {
            "approval_id": "approval-offline-contract", "project_id": "sample_repo",
            "skill_key": cycle["skill_key"], "candidate_version_id": cycle["selected_candidate_version_id"],
            "cycle_id": cycle["cycle_id"], "evidence_sha256": sha256(results.encoded(cycle)).hexdigest(),
            "approved_at": self.report["created_at"], "approved_by": "local_operator",
            "trust_scope": "local_environment", "previous_active_version_id": None,
        }
        self.wrapper = {
            "schema_version": 1, "project_id": "sample_repo", "run_id": "104-1",
            "report_sha256": sha256(results.encoded(self.report)).hexdigest(), "execution_mode": "offline_test",
            "approvals": [self.approval], "executions": [],
        }

    def writer(self):
        writer = getattr(results, "store_adoption", None)
        self.assertTrue(callable(writer), "Missing adoption store API")
        return writer

    def test_offline_projection_survives_real_loader_merge_index_and_official_build(self):
        path = self.writer()(self.output, self.wrapper)
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.glob("*/*/*.json")}
        self.assertEqual(results.load_adoptions(self.output)[("sample_repo", "104-1")], self.wrapper)
        root = dashboard_fixture(self.root / "publisher")
        merged, site = self.root / "merged", self.root / "site"
        results.merge_results(root, self.output, merged)
        results.build(root, merged, site)
        self.assertEqual((site / "results/sample_repo/104-1/adoption.json").read_bytes(), path.read_bytes())
        history = results.read_json(site / "results/sample_repo/index.json")["history"]
        row = next(row for row in history if row["run_id"] == "104-1")
        self.assertEqual(row["adoption"], "104-1/adoption.json")
        self.assertEqual(before, {p: (self.output / p).read_bytes() for p in before})

    def test_private_fields_modes_and_evidence_drift_are_rejected_before_writing(self):
        for change in (
            {"environment_id": "private"}, {"approved_by": "uid:1000:private"},
            {"previous_active_execution_sha256": "a" * 64}, {"evidence_sha256": "f" * 64},
            {"candidate_version_id": "sha256:" + "a" * 64},
        ):
            data = deepcopy(self.wrapper)
            data["approvals"][0].update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeFailure):
                self.writer()(self.output, data)
        data = {**self.wrapper, "execution_mode": "live"}
        with self.assertRaises(RuntimeFailure):
            self.writer()(self.output, data)
        self.assertFalse((self.output / "sample_repo/104-1/adoption.json").exists())

    def execution(self):
        report = {**self.report, "run_id": "105-1"}
        results.store(self.output, report)
        return {
            "execution_id": "execution-offline-contract", "run_id": "105-1", "project_id": "sample_repo",
            "skill_key": self.approval["skill_key"], "approval_id": self.approval["approval_id"],
            "evidence_sha256": self.approval["evidence_sha256"], "work_input_sha256": "c" * 64,
            "approved_version_id": self.approval["candidate_version_id"],
            "loaded_version_id": self.approval["candidate_version_id"], "skill_version_verified": True,
            "observed_at": self.report["created_at"], "status": "verified", "reason_code": None,
        }

    def test_execution_resolves_previous_approval_and_never_claims_missing_use(self):
        self.writer()(self.output, self.wrapper)
        use = {**deepcopy(self.wrapper), "run_id": "106-1", "approvals": [], "executions": [self.execution()]}
        report = {**self.report, "run_id": "106-1"}
        results.store(self.output, report)
        use["report_sha256"] = sha256(results.encoded(report)).hexdigest()
        self.writer()(self.output, use)
        self.assertEqual(len(results.load_adoptions(self.output)), 2)
        for change in ({"loaded_version_id": None}, {"skill_version_verified": False},
                       {"approval_id": "missing"}, {"run_id": "999-1"}, {"approved_version_id": "sha256:" + "a" * 64}):
            changed = deepcopy(use)
            changed["executions"][0].update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeFailure):
                self.writer()(self.output, changed)

    def test_global_duplicate_ids_and_immutable_conflicts_fail_without_partial_merge(self):
        self.writer()(self.output, self.wrapper)
        duplicate = deepcopy(self.wrapper)
        duplicate["run_id"] = "107-1"
        report = {**self.report, "run_id": "107-1"}
        results.store(self.output, report)
        duplicate["report_sha256"] = sha256(results.encoded(report)).hexdigest()
        duplicate["approvals"][0]["previous_active_version_id"] = "sha256:" + "d" * 64
        with self.assertRaises(RuntimeFailure):
            self.writer()(self.output, duplicate)
        target = self.root / "target"
        results.store(target, self.report)
        results.atomic_json(target / "sample_repo/104-1/adoption.json", {"schema_version": 1})
        with self.assertRaises(RuntimeFailure):
            results.merge_results(Path(results.__file__).resolve().parents[1], self.output, target)
        self.assertFalse((target / "sample_repo/100-1/report.json").exists())

    def test_private_projection_requires_review_and_discards_private_authority_fields(self):
        import project_evaluation
        import test_skill_approvals as approval_fixtures
        scenario = approval_fixtures.ApprovalIntegrationTests()
        scenario.setUp()
        self.addCleanup(scenario.doCleanups)
        cycle = scenario.synthetic_live_contract()
        approval = scenario.approve_cycle(cycle)
        publish = getattr(project_evaluation, "persist_adoption", None)
        self.assertTrue(callable(publish), "Missing reviewed private-to-public projection")
        before = set(scenario.output.glob("*/*/report.json"))
        with self.assertRaises(RuntimeFailure):
            publish(scenario.output, approval=approval)
        self.assertEqual(set(scenario.output.glob("*/*/report.json")), before)
        ref = publish(scenario.output, approval=approval, reviewed=True)
        data = results.load_adoptions(scenario.output)[("sample_repo", ref["run_id"])]
        self.assertEqual(data["approvals"][0]["approved_by"], "local_operator")
        self.assertEqual(data["executions"], [])
        serialized = results.encoded(data).decode()
        for private in ("environment_id", "previous_active_execution_sha256", "source_path", "uid:", str(scenario.root)):
            self.assertNotIn(private, serialized)
        report = results.read_json(scenario.output / "sample_repo" / ref["run_id"] / "report.json")
        self.assertEqual(report["execution"]["status"], "not_assessed")
        self.assertIsNone(report["execution"]["metrics"])


if __name__ == "__main__":
    unittest.main()
