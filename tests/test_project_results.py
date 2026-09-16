import copy
import importlib
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest


class ProjectResultsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("project_results"), "results module is not implemented")
        return importlib.import_module("project_results")

    def fixture(self):
        return {
            "schema_version": 1, "project_id": "sample_repo", "run_id": "123-1",
            "created_at": "2026-09-16T05:00:00+00:00", "origin": "github_actions",
            "purpose": "project_assessment", "source_commit": "a" * 40,
            "project_tree_sha256": "b" * 64, "evaluator_sha256": "c" * 64,
            "source_report_sha256": None, "source_schema_version": None,
            "guide": {"status": "not_assessed", "reason_code": "no_skills", "metrics": None, "decision": None},
            "execution": {"status": "blocked", "reason_code": "live_disabled", "metrics": None, "decision": None},
        }

    def test_sample_paths_are_migrated(self):
        import evaluation
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "sample_repo").exists())
        for spec in evaluation.FAMILIES.values():
            self.assertTrue(spec["seed"].startswith("projects/sample_repo/"))
            self.assertTrue((root / spec["seed"]).is_file())

    def test_contract_rejects_extra_fields_secrets_bad_types_and_paths(self):
        m = self.module()
        self.assertEqual(m.validate(self.fixture()), self.fixture())
        for change in (
            {"raw_prompt": "secret"}, {"project_id": "../escape"}, {"run_id": "bad"},
            {"schema_version": True}, {"created_at": "not-a-date"},
            {"source_commit": "not-a-hash"},
        ):
            row = {**self.fixture(), **change}
            with self.subTest(change=change), self.assertRaises(m.RuntimeFailure):
                m.validate(row)
        for metrics in ({"raw": "secret"}, {"requested": True}, {"requested": -1},
                        {"mean_judge_score": float("nan")}, {"mean_judge_score": 101}):
            row = self.fixture()
            row["execution"]["metrics"] = metrics
            with self.subTest(metrics=metrics), self.assertRaises(m.RuntimeFailure):
                m.validate(row)

    def test_history_is_immutable_and_retries_are_idempotent(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = m.store(root, self.fixture())
            self.assertEqual(m.store(root, self.fixture()), path)
            changed = self.fixture()
            changed["execution"]["reason_code"] = "missing_auth"
            with self.assertRaises(m.RuntimeFailure):
                m.store(root, changed)
            self.assertEqual(json.loads(path.read_text()), self.fixture())

    def test_catalog_hashes_change_with_source_and_evaluator_inputs(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "projects/sample_repo"
            project.mkdir(parents=True)
            source = project / "a.py"
            source.write_text("a=1")
            (root / "project_profiles.json").write_text('{"schema_version":1,"projects":{}}')
            first = m.catalog(root)
            self.assertEqual(first[0]["id"], "sample_repo")
            source.write_text("a=2")
            self.assertNotEqual(first[0]["tree_sha256"], m.catalog(root)[0]["tree_sha256"])
            before = m.evaluator_hash(root)
            (root / "project_evaluation.py").write_text("changed")
            self.assertNotEqual(before, m.evaluator_hash(root))
            before = m.evaluator_hash(root)
            (root / "skill_guide.py").write_text("available")
            self.assertNotEqual(before, m.evaluator_hash(root))
            source.unlink()
            source.symlink_to(root / "project_profiles.json")
            self.assertEqual(m.catalog(root)[0]["error"], "unsafe_project")

    def test_indices_preserve_old_runs_without_marking_stale_as_current(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "projects/sample_repo").mkdir(parents=True)
            (root / "projects/sample_repo/a.py").write_text("a=1")
            (root / "project_profiles.json").write_text('{"schema_version":1,"projects":{}}')
            row = self.fixture()
            row["project_tree_sha256"] = m.catalog(root)[0]["tree_sha256"]
            row["evaluator_sha256"] = m.evaluator_hash(root)
            m.store(root / "results", row)
            index = m.reindex(root, root / "results")
            self.assertEqual(index["projects"][0]["current_run"], "123-1")
            (root / "projects/sample_repo/a.py").write_text("a=2")
            index = m.reindex(root, root / "results")
            self.assertIsNone(index["projects"][0]["current_run"])
            self.assertEqual(index["projects"][0]["history_count"], 1)
            (root / "projects/sample_repo/a.py").unlink()
            (root / "projects/sample_repo").rmdir()
            self.assertEqual(m.reindex(root, root / "results")["projects"][0]["state"], "removed")

    def test_legacy_export_excludes_free_text_and_preserves_unknown_provenance(self):
        m = self.module()
        raw = {
            "schema_version": 2, "run_id": "20260914T072451Z-50354146d951",
            "created_at": "2026-09-14T07:24:51+00:00", "purpose": "baseline",
            "status": "completed", "context": {"token": "DO_NOT_PUBLISH"},
            "tasks": [{"request": "DO_NOT_PUBLISH"}], "rationale": "DO_NOT_PUBLISH",
            "aggregate": {"requested": 5, "attempted": 5, "evaluation_completed": 5,
                          "errors": 0, "blocked": 0, "correctness_successes": 5,
                          "mean_judge_score": 100, "judge_score_denominator": 5},
            "fingerprint": "f" * 64,
        }
        result = m.export_legacy(raw, "sample_repo", "d" * 64)
        self.assertNotIn("DO_NOT_PUBLISH", json.dumps(result))
        self.assertEqual(result["origin"], "historical_import")
        self.assertIsNone(result["source_commit"])
        self.assertIsNone(result["project_tree_sha256"])
        self.assertEqual(result["execution"]["metrics"]["requested"], 5)
        bad = copy.deepcopy(raw)
        bad["aggregate"]["requested"] = "5"
        with self.assertRaises(m.RuntimeFailure):
            m.export_legacy(bad, "sample_repo", "d" * 64)

    def test_unknown_report_fields_and_symlinks_are_not_published(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "sample_repo/123-1"
            folder.mkdir(parents=True)
            target = root / "raw.json"
            target.write_text(json.dumps(self.fixture()))
            (folder / "report.json").symlink_to(target)
            with self.assertRaises(m.RuntimeFailure):
                m.load_reports(root)

    def test_build_includes_sample_assets_without_importing_synthetic_history(self):
        m = self.module()
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "site"
            real = m.load_reports(root / "results")
            m.build(root, root / "results", output)
            self.assertTrue((output / "sample-data.json").is_file(), "sample view asset must be deployed")
            self.assertTrue((output / "views.js").is_file())
            sample = json.loads((output / "sample-data.json").read_text())
            self.assertIs(sample["synthetic"], True)
            self.assertEqual(m.load_reports(output / "results"), real)
            self.assertNotIn(sample["project_id"], [
                item["id"] for item in json.loads((output / "results/index.json").read_text())["projects"]
            ])

    def snapshot(self, report):
        m = self.module()
        text = "---\nname: develop\n---\nRead the supplied requirements.\n"
        return {"schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
                "report_sha256": sha256(m.encoded(report)).hexdigest(), "skill_id": "develop",
                "base": {"content": text, "sha256": sha256(text.encode()).hexdigest()}, "candidate": None}

    def test_snapshots_are_bound_and_immutable_without_changing_reports(self):
        m = self.module()
        self.assertTrue(hasattr(m, "store_snapshots"), "public Skill snapshots are not implemented")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = self.fixture()
            path = m.store(root, report)
            before = path.read_bytes()
            snapshot = self.snapshot(report)
            target = m.store_snapshots(root, snapshot)
            self.assertEqual(m.store_snapshots(root, snapshot), target)
            self.assertEqual(path.read_bytes(), before)
            for key, value in (("report_sha256", "0" * 64), ("raw_prompt", "not allowed")):
                with self.subTest(key=key), self.assertRaises(m.RuntimeFailure):
                    m.validate_snapshots({**snapshot, key: value}, report)
            bad = copy.deepcopy(snapshot)
            bad["base"]["content"] += "tampered"
            with self.assertRaises(m.RuntimeFailure):
                m.validate_snapshots(bad, report)
            changed = copy.deepcopy(snapshot)
            changed["skill_id"] = "another"
            with self.assertRaises(m.RuntimeFailure):
                m.store_snapshots(root, changed)

    def test_snapshot_index_build_and_merge_preserve_optional_skill_evidence(self):
        m = self.module()
        self.assertTrue(hasattr(m, "store_snapshots"), "public Skill snapshots are not implemented")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            incoming, durable, output = (Path(temp) / name for name in ("incoming", "durable", "site"))
            report = self.fixture()
            m.store(incoming, report)
            m.store_snapshots(incoming, self.snapshot(report))
            m.merge_results(root, incoming, durable)
            index = m.read_json(durable / "sample_repo/index.json")
            self.assertEqual(index["history"][0]["skill_id"], "develop")
            self.assertEqual(index["history"][0]["skill_snapshots"], "123-1/skill-snapshots.json")
            m.build(root, durable, output)
            self.assertEqual(m.load_snapshots(durable), m.load_snapshots(output / "results"))
            self.assertEqual(m.load_reports(durable), m.load_reports(output / "results"))

    def test_archived_import_checks_source_and_skill_hashes_and_excludes_rationale(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "runs", root / "results"
            identifier = "20260915T005404Z-611d681b8bf7"
            base = "---\nname: develop\n---\nRead requirements.\n"
            candidate = base + "Check boundaries.\n"
            original = {"schema_version": 2, "run_id": identifier, "purpose": "candidate",
                        "status": "completed", "created_at": "2026-09-15T00:54:04+00:00",
                        "base_skill_sha256": sha256(base.encode()).hexdigest(),
                        "skill_sha256": sha256(candidate.encode()).hexdigest(),
                        "rationale": "DO_NOT_PUBLISH"}
            folder = source / identifier
            folder.mkdir(parents=True)
            raw = m.encoded(original)
            (folder / "candidate.json").write_bytes(raw)
            (folder / "base-SKILL.md").write_text(base)
            (folder / "SKILL.md").write_text(candidate)
            public = m.export_legacy(original, "sample_repo", sha256(raw).hexdigest())
            report_path = m.store(target, public)
            before = report_path.read_bytes()
            self.assertEqual(m.import_skill_snapshots(source, target, "sample_repo", identifier, "develop"), 1)
            data = m.load_snapshots(target)[("sample_repo", public["run_id"])]
            self.assertNotIn("DO_NOT_PUBLISH", json.dumps(data))
            self.assertEqual(data["candidate"]["content"], candidate)
            self.assertEqual(report_path.read_bytes(), before)
            (folder / "SKILL.md").write_text(candidate + "tampered")
            with self.assertRaises(m.RuntimeFailure):
                m.import_skill_snapshots(source, target, "sample_repo", identifier, "develop")

    def test_orphan_and_symlinked_snapshots_block_publication(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "sample_repo/123-1"
            folder.mkdir(parents=True)
            path = folder / "skill-snapshots.json"
            path.write_text("{}")
            with self.assertRaises(m.RuntimeFailure):
                m.load_snapshots(root)
            m.store(root, self.fixture())
            path.unlink()
            path.symlink_to(folder / "report.json")
            with self.assertRaises(m.RuntimeFailure):
                m.load_snapshots(root)


if __name__ == "__main__":
    unittest.main()
