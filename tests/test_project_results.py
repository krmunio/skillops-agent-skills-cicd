import copy
import base64
import importlib
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


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
            self.assertEqual(len(list(output.glob("views.*.js"))), 1)
            sample = json.loads((output / "sample-data.json").read_text())
            self.assertIs(sample["synthetic"], True)
            self.assertEqual(m.load_reports(output / "results"), real)
            self.assertNotIn(sample["project_id"], [
                item["id"] for item in json.loads((output / "results/index.json").read_text())["projects"]
            ])

    def test_build_hashes_final_modules_and_resolves_entrypoint_and_imports(self):
        module = self.module()
        root = Path(__file__).resolve().parents[1]
        names = ("app", "views", "evolution", "assessments")
        originals = {name: (root / "dashboard" / f"{name}.js").read_bytes() for name in names}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "site"
            module.build(root, root / "results", output)
            scripts = list(output.glob("*.js"))
            self.assertEqual(len(scripts), len(names))
            for script in scripts:
                self.assertRegex(script.name, r"^(app|views|evolution|assessments)\.[a-f0-9]{12}\.js$")
                self.assertEqual(script.name.split(".")[1], sha256(script.read_bytes()).hexdigest()[:12])
                source = script.read_text(encoding="utf-8")
                for imported in re.findall(r"\bfrom\s+['\"]\./([^'\"]+)['\"]", source):
                    self.assertTrue((output / imported).is_file(), imported)
                    self.assertRegex(imported, r"\.[a-f0-9]{12}\.js$")
            index = (output / "index.html").read_text(encoding="utf-8")
            entrypoints = re.findall(r'<script src="/([^\"]+)" type="module">', index)
            self.assertEqual(entrypoints, [next(output.glob("app.*.js")).name])
            self.assertNotIn('src="/app.js"', index)
            config = json.loads((output / "staticwebapp.config.json").read_text())
            routes = {row["route"]: row["headers"]["Cache-Control"] for row in config["routes"]}
            self.assertEqual(routes["/results/*"], "no-store")
            self.assertEqual(routes["/index.html"], "no-cache")
            self.assertEqual({route for route, header in routes.items() if "immutable" in header},
                             {f"/{script.name}" for script in scripts})
            for script in scripts:
                self.assertEqual(routes[f"/{script.name}"], "public, max-age=31536000, immutable")
            self.assertEqual(config["globalHeaders"], module.read_json(root / "dashboard/staticwebapp.config.json")["globalHeaders"])
            for name in names:
                self.assertFalse((output / f"{name}.js").exists())
                self.assertEqual((root / "dashboard" / f"{name}.js").read_bytes(), originals[name])

    def test_build_dependency_changes_invalidate_importers_without_changing_source_files(self):
        module = self.module()
        root = Path(__file__).resolve().parents[1]
        read_bytes = module.read_bytes

        def changed_dependency(path, *args):
            raw = read_bytes(path, *args)
            return raw + b"\n" if Path(path) == root / "dashboard/views.js" else raw

        with tempfile.TemporaryDirectory() as temp:
            original, repeated, changed = (Path(temp) / name for name in ("original", "repeated", "changed"))
            module.build(root, root / "results", original)
            module.build(root, root / "results", repeated)
            self.assertEqual(sorted(path.name for path in original.glob("*.js")),
                             sorted(path.name for path in repeated.glob("*.js")))
            with mock.patch.object(module, "read_bytes", side_effect=changed_dependency):
                module.build(root, root / "results", changed)
            for name in ("app", "views", "evolution", "assessments"):
                self.assertNotEqual(next(original.glob(f"{name}.*.js")).name,
                                    next(changed.glob(f"{name}.*.js")).name)

    def snapshot(self, report):
        m = self.module()
        text = "---\nname: develop\n---\nRead the supplied requirements.\n"
        return {"schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
                "report_sha256": sha256(m.encoded(report)).hexdigest(), "skill_id": "develop",
                "base": {"content": text, "sha256": sha256(text.encode()).hexdigest()}, "candidate": None}

    def evolution(self, report, snapshot=None, files=None):
        import evolution_records as e
        source = files or {"SKILL.md": snapshot["base"]["content"].encode() if snapshot else b"Historical Skill\n"}
        scope = "complete_bundle" if files else "entrypoint_only"
        version, retained = e.capture_version(source, capture_scope=scope,
                                               complete_inventory=list(source) if files else None)
        rows = e.empty_records()
        rows.update(identities=[{"skill_key": "skillops:develop", "display_name": "Develop"}],
                    versions=[version], skill_versions=[{"skill_key": "skillops:develop",
                                                        "version_id": version["version_id"]}])
        return {"schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
                "report_sha256": sha256(self.module().encoded(report)).hexdigest(), "records": rows,
                "bindings": [{"skill_key": "skillops:develop", "base_version_id": version["version_id"],
                              "candidate_version_id": None, "legacy_skill_id": snapshot["skill_id"] if snapshot else None}],
                "file_contents": [{"version_id": version["version_id"], "path": path, "encoding": "base64",
                                   "data": base64.b64encode(raw).decode()} for path, raw in retained.items()]}

    def test_evolution_retains_exact_files_and_rejects_false_bindings(self):
        m = self.module()
        self.assertTrue(hasattr(m, "validate_evolution"), "evolution attachments are not implemented")
        report = self.fixture()
        snapshot = self.snapshot(report)
        value = self.evolution(report, snapshot)
        self.assertEqual(m.validate_evolution(value, report, snapshot), value)
        for mutate in (
            lambda v: v.update(private_prompt="private"),
            lambda v: v.update(report_sha256="f" * 64),
            lambda v: v["bindings"][0].update(legacy_skill_id="other"),
            lambda v: v["bindings"][0].update(base_version_id="sha256:" + "f" * 64),
            lambda v: v["file_contents"][0].update(data="Yg=="),
            lambda v: v["file_contents"].append(dict(v["file_contents"][0])),
            lambda v: v["file_contents"].clear(),
            lambda v: v["records"]["identities"].append({"skill_key": "other:develop", "display_name": "Develop"}),
            lambda v: v.update(records={key: [] for key in v["records"]}, bindings=[], file_contents=[]),
        ):
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.assertRaises(m.RuntimeFailure):
                m.validate_evolution(changed, report, snapshot)
        files = {"SKILL.md": b"line\n" * 401, "assets/blob.bin": b"\xff\0\x80"}
        complete = self.evolution(report, files=files)
        m.validate_evolution(complete, report)
        self.assertEqual({item["path"]: base64.b64decode(item["data"]) for item in complete["file_contents"]}, files)
        oversized = self.evolution(report, files={"SKILL.md": b"a" * (2 * m.LIMIT)})
        with self.assertRaises(m.RuntimeFailure):
            m.validate_evolution(oversized, report)
        empty = self.evolution(report)
        empty.update(records={key: [] for key in empty["records"]}, bindings=[], file_contents=[])
        with self.assertRaises(m.RuntimeFailure):
            m.validate_evolution(empty, report)

    def test_evolution_store_merge_index_build_and_dual_conflicts(self):
        m = self.module()
        self.assertTrue(hasattr(m, "store_evolution"), "evolution persistence is not implemented")
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            incoming, target = root / "incoming", root / "target"
            report = self.fixture()
            report_path = m.store(incoming, report)
            before = report_path.read_bytes()
            snapshot = self.snapshot(report)
            m.store_snapshots(incoming, snapshot)
            value = self.evolution(report, snapshot)
            path = m.store_evolution(incoming, value)
            self.assertEqual(m.store_evolution(incoming, value), path)
            m.merge_results(source, incoming, target)
            history = m.read_json(target / "sample_repo/index.json")["history"]
            self.assertEqual(history[0]["skill_id"], "develop")
            self.assertEqual(history[0]["evolution_skills"][0]["skill_key"], "skillops:develop")
            self.assertEqual(history[0]["skill_evolution"], "123-1/skill-evolution.json")
            output = root / "site"
            m.build(source, target, output)
            self.assertEqual((output / "results/sample_repo/123-1/report.json").read_bytes(), before)
            self.assertEqual(m.load_evolution(output / "results"), m.load_evolution(incoming))
            changed = copy.deepcopy(value)
            changed["records"]["identities"][0]["display_name"] = "Renamed"
            with self.assertRaises(m.RuntimeFailure):
                m.store_evolution(incoming, changed)
            conflict = copy.deepcopy(snapshot)
            conflict["base"] = {"content": "changed", "sha256": sha256(b"changed").hexdigest()}
            with self.assertRaises(m.RuntimeFailure):
                m.validate_evolution(value, report, conflict)
            path.unlink()
            path.symlink_to(target / "sample_repo/123-1/skill-evolution.json")
            with self.assertRaises(m.RuntimeFailure):
                m.load_evolution(incoming)

    def test_large_evolution_roundtrips_without_raising_other_result_limits(self):
        m = self.module()
        source = Path(__file__).resolve().parents[1]
        report = self.fixture()
        data = self.evolution(report, files={"SKILL.md": b"x" * m.LIMIT})
        self.assertGreater(len(m.encoded(data)), m.LIMIT)
        self.assertLess(len(m.encoded(data)), 2 * m.LIMIT)
        with tempfile.TemporaryDirectory() as folder:
            incoming, target, site = (Path(folder) / name for name in ("incoming", "target", "site"))
            m.store(incoming, report)
            try:
                path = m.store_evolution(incoming, data)
            except m.RuntimeFailure as error:
                self.fail(f"The complete bundle is below the dedicated 2 MiB bound: {error.code}")
            m.store_evolution(incoming, data)
            m.merge_results(source, incoming, target)
            m.merge_results(source, incoming, target)
            m.build(source, target, site)
            self.assertEqual(m.load_evolution(site / "results"), m.load_evolution(incoming))
            relative = path.relative_to(incoming)
            self.assertEqual((site / "results" / relative).read_bytes(), path.read_bytes())
            with self.assertRaises(m.RuntimeFailure):
                m.atomic_json(Path(folder) / "report.json", {"padding": "x" * m.LIMIT})
            with self.assertRaises(m.RuntimeFailure):
                m.read_json(path)

    def test_lifecycle_only_index_and_invalid_merge_are_non_mutating(self):
        m = self.module()
        self.assertTrue(hasattr(m, "store_evolution"), "lifecycle-only selection is not implemented")
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            incoming, target = Path(temp) / "incoming", Path(temp) / "target"
            report = self.fixture()
            m.store(incoming, report)
            value = self.evolution(report)
            m.store_evolution(incoming, value)
            m.reindex(source, incoming)
            row = m.read_json(incoming / "sample_repo/index.json")["history"][0]
            self.assertNotIn("skill_id", row)
            self.assertEqual(row["evolution_skills"][0]["skill_key"], "skillops:develop")
            # A conflicting existing snapshot must be detected before copying incoming reports.
            m.store(target, report)
            m.store_snapshots(target, self.snapshot(report))
            newer = {**report, "run_id": "124-1"}
            m.store(incoming, newer)
            with self.assertRaises(m.RuntimeFailure):
                m.merge_results(source, incoming, target)
            self.assertFalse((target / "sample_repo/124-1/report.json").exists())

    def archived_evolution_results(self, root):
        from test_evolution_records import archive, BASE_RUN, CANDIDATE_RUN, COMPARISON_RUN
        m = self.module()
        source, results = root / "runs", root / "results"
        source.mkdir()
        archive(source)
        for run, name in ((BASE_RUN, "report.json"), (CANDIDATE_RUN, "candidate.json"),
                          (COMPARISON_RUN, "comparison.json")):
            path = source / run / name
            original = json.loads(path.read_text())
            original.update(created_at="2026-09-16T00:00:00Z", fingerprint="c" * 64)
            if name == "report.json":
                original["aggregate"] = {}
            path.write_text(json.dumps(original))
        candidate_path = source / CANDIDATE_RUN / "candidate.json"
        candidate = json.loads(candidate_path.read_text())
        candidate["baseline_sha256"] = sha256((source / BASE_RUN / "report.json").read_bytes()).hexdigest()
        candidate_path.write_text(json.dumps(candidate))
        m.import_history(source, results, "sample_repo")
        m.import_skill_snapshots(source, results, "sample_repo", CANDIDATE_RUN, "develop")
        return source, results

    def test_incremental_candidates_preserve_partial_or_complete_baseline_evidence(self):
        from test_evolution_records import BASE_RUN, CANDIDATE_RUN
        m = self.module()
        for complete in (False, True):
            with self.subTest(complete=complete), tempfile.TemporaryDirectory() as temp:
                source, results = self.archived_evolution_results(Path(temp))
                baseline_folder = results / "sample_repo" / ("import-" + BASE_RUN)
                baseline_report = m.read_json(baseline_folder / "report.json")
                if complete:
                    snapshot = m.read_json(baseline_folder / "skill-snapshots.json")
                    full = self.evolution(baseline_report, snapshot, {
                        "SKILL.md": snapshot["base"]["content"].encode(),
                        "references/original.txt": b"retained original reference\n",
                    })
                    m.store_evolution(results, full)
                try:
                    m.import_skill_evolution(source, results, "sample_repo", CANDIDATE_RUN,
                                             "skillops:develop", "develop")
                    before = {path: path.read_bytes() for path in results.glob("*/*/*.json")}
                    second = "20260916T000000Z-abcdef123456"
                    original = m.read_json(source / CANDIDATE_RUN / "candidate.json")
                    content = (source / CANDIDATE_RUN / "SKILL.md").read_bytes() + b"Second candidate.\n"
                    original.update(run_id=second, skill_sha256=sha256(content).hexdigest())
                    (source / second).mkdir()
                    (source / second / "candidate.json").write_text(json.dumps(original))
                    (source / second / "SKILL.md").write_bytes(content)
                    (source / second / "base-SKILL.md").write_bytes(
                        (source / CANDIDATE_RUN / "base-SKILL.md").read_bytes())
                    m.import_history(source, results, "sample_repo")
                    m.import_skill_snapshots(source, results, "sample_repo", second, "develop")
                    count = m.import_skill_evolution(source, results, "sample_repo", second,
                                                      "skillops:develop", "develop")
                    self.assertEqual(count, 2)
                    self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))
                    baseline = m.read_json(baseline_folder / "skill-evolution.json")
                    added = m.read_json(results / "sample_repo" / ("import-" + second) / "skill-evolution.json")
                    self.assertEqual(added["records"]["generations"][0]["baseline_ref"]["run_id"], BASE_RUN)
                    if complete:
                        self.assertNotEqual(baseline["bindings"][0]["base_version_id"],
                                            added["bindings"][0]["base_version_id"])
                        self.assertEqual(baseline, full)
                    retry = {path: path.read_bytes() for path in results.glob("*/*/*.json")}
                    self.assertEqual(m.import_skill_evolution(source, results, "sample_repo", second,
                                                               "skillops:develop", "develop"), 2)
                    self.assertTrue(all(path.read_bytes() == raw for path, raw in retry.items()))
                except m.RuntimeFailure as error:
                    self.fail(f"Compatible incremental import rejected: {error.code}")

    def test_baseline_reuse_rejects_conflicts_before_any_writes(self):
        from test_evolution_records import BASE_RUN, CANDIDATE_RUN
        m = self.module()
        for conflict in ("skill_key", "legacy_id", "null_base", "candidate", "content"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as temp:
                source, results = self.archived_evolution_results(Path(temp))
                m.import_skill_evolution(source, results, "sample_repo", CANDIDATE_RUN,
                                         "skillops:develop", "develop")
                folder = results / "sample_repo" / ("import-" + BASE_RUN)
                value = m.read_json(folder / "skill-evolution.json")
                if conflict == "skill_key":
                    for items in value["records"].values():
                        for item in items:
                            if "skill_key" in item:
                                item["skill_key"] = "other:develop"
                    value["bindings"][0]["skill_key"] = "other:develop"
                elif conflict == "legacy_id":
                    value["bindings"][0]["legacy_skill_id"] = "other"
                elif conflict == "null_base":
                    value["bindings"][0]["base_version_id"] = None
                elif conflict == "candidate":
                    value["bindings"][0]["candidate_version_id"] = value["bindings"][0]["base_version_id"]
                else:
                    snapshot = m.read_json(folder / "skill-snapshots.json")
                    snapshot["base"] = {"content": "other source", "sha256": sha256(b"other source").hexdigest()}
                    value = self.evolution(m.read_json(folder / "report.json"), snapshot)
                    (folder / "skill-snapshots.json").write_bytes(m.encoded(snapshot))
                (folder / "skill-evolution.json").write_bytes(m.encoded(value))
                before = {path: path.read_bytes() for path in results.glob("*/*/*.json")}
                with self.assertRaises(m.RuntimeFailure):
                    m.import_skill_evolution(source, results, "sample_repo", CANDIDATE_RUN,
                                             "skillops:develop", "develop")
                self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))

    def test_reviewed_evolution_import_keeps_per_record_time_and_raw_commitments(self):
        m = self.module()
        self.assertTrue(hasattr(m, "import_skill_evolution"), "historical evolution import is not implemented")
        from test_evolution_records import BASE_RUN, CANDIDATE_RUN, COMPARISON_RUN
        with tempfile.TemporaryDirectory() as temp:
            source, results = self.archived_evolution_results(Path(temp))
            before = {path: path.read_bytes() for path in results.glob("*/*/report.json")}
            count = m.import_skill_evolution(source, results, "sample_repo", CANDIDATE_RUN,
                                              "skillops:develop", "develop")
            self.assertEqual(count, 3)
            values = m.load_evolution(results)
            baseline = values[("sample_repo", "import-" + BASE_RUN)]
            self.assertEqual(baseline["records"]["generations"], [])
            self.assertIsNone(baseline["bindings"][0]["candidate_version_id"])
            comparison = values[("sample_repo", "import-" + COMPARISON_RUN)]
            self.assertEqual(comparison["records"]["comparisons"][0]["decision"], "rejected")
            self.assertEqual(comparison["records"]["generations"][0]["observed_failure_count"], 0)
            self.assertEqual(comparison["records"]["adoptions"][0]["state"], "unknown")
            self.assertNotIn("PRIVATE", json.dumps(list(values.values())))
            self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))

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
