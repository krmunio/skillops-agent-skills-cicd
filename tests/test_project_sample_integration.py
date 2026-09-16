import base64
import copy
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from project_samples import verify_project


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "project-a": ("dbader/schedule", "82a43db1b938d8fdf60103bd41f329e06c8d3651", 32),
    "project-b": ("obra/superpowers", "b36e0829c6d0140e93cfef2ca599b1b07d4a7797", 194),
}
SAMPLE_HASHES = {
    "issues.py": "66a57d8c01b3db48196d70e51d51453e81ddaf48a39bfdafd4ad53e5bddf0a49",
    "labels.py": "1f94937817fe789301c84c38cf5bd0ee9829b739f86cdc65e4dbc6b147834a98",
    "updates.py": "ef438068ac25206058a83e268b6ffa66362133f09a3a83f21c27558aeda86c97",
}
PROJECT_IDS = {"sample_repo", *SOURCES}


def project_skills(project):
    return sorted(path for folder in (".github/skills", ".claude/skills", "skills")
                  for path in (project / folder).rglob("SKILL.md"))


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_values(item)


def public_history_bytes(output):
    return {path.relative_to(output): path.read_bytes()
            for filename in ("report.json", "skill-snapshots.json", "skill-evolution.json")
            for path in output.glob(f"*/*/{filename}")}


class ProjectSampleFixtureTests(unittest.TestCase):
    def test_public_string_checks_preserve_multiline_content(self):
        self.assertEqual(list(string_values({"axis": [None, {"text": 'first\n"second"'}]})), ['first\n"second"'])

    def test_samples_have_discoverable_skills_without_embedded_git_or_links(self):
        for identifier in sorted(PROJECT_IDS):
            with self.subTest(project=identifier):
                project = ROOT / "projects" / identifier
                self.assertTrue(project.is_dir(), f"Missing prepared sample: {identifier}")
                self.assertTrue(project_skills(project), f"Missing selected skill: {identifier}")
                for path in project.rglob("*"):
                    self.assertNotEqual(path.name.casefold(), ".git")
                    self.assertFalse(path.is_symlink())
                    self.assertFalse(getattr(path, "is_junction", lambda: False)())

    def test_external_snapshots_preserve_pinned_source_hashes_and_licenses(self):
        for identifier, (repository, commit, count) in SOURCES.items():
            with self.subTest(project=identifier):
                project = ROOT / "projects" / identifier
                provenance = json.loads((project / ".skillops-source.json").read_text(encoding="utf-8"))
                self.assertEqual(provenance["schema_version"], 1)
                self.assertEqual((provenance["repository"], provenance["commit"]), (repository, commit))
                self.assertEqual(len(provenance["files"]), count)
                self.assertEqual(verify_project(project)["source_files"], count)
                for relative, digest in provenance["files"].items():
                    self.assertEqual(sha256((project / relative).read_bytes()).hexdigest(), digest, relative)
                self.assertTrue(provenance["license_files"])
                for relative in provenance["license_files"]:
                    self.assertIn(relative, provenance["files"])
                    self.assertIn("MIT", (project / relative).read_text(encoding="utf-8"))

    def test_missing_skills_are_explicit_drafts_and_seed_defects_are_unchanged(self):
        for identifier, template, name in (
            ("sample_repo", "skills/develop/SKILL.md", "develop"),
            ("project-a", "project_templates/schedule-development/SKILL.md", "schedule-development"),
        ):
            with self.subTest(project=identifier):
                project = ROOT / "projects" / identifier
                metadata = json.loads((project / ".skillops-bootstrap.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["schema_version"], 2)
                source = project / ".skillops-source.json"
                binding = sha256(source.read_bytes()).hexdigest() if source.exists() else None
                self.assertEqual(metadata["source_manifest_sha256"], binding)
                self.assertEqual(metadata["state"], "unvalidated_draft")
                self.assertEqual(verify_project(project)["bootstrap"], "unvalidated_draft")
                skill = project / ".github/skills" / name / "SKILL.md"
                self.assertEqual(skill.read_bytes(), (ROOT / template).read_bytes().replace(b"\r\n", b"\n"))
        for name, digest in SAMPLE_HASHES.items():
            self.assertEqual(sha256((ROOT / "projects/sample_repo" / name).read_bytes()).hexdigest(), digest)
        self.assertIn("projects/** -text", (ROOT / ".gitattributes").read_text())

    def test_existing_upstream_skills_are_preserved_not_bootstrapped(self):
        project = ROOT / "projects/project-b"
        provenance = json.loads((project / ".skillops-source.json").read_text(encoding="utf-8"))
        self.assertFalse((project / ".skillops-bootstrap.json").exists())
        self.assertFalse((project / "AGENTS.md").exists())
        self.assertIn("AGENTS.md", json.dumps(provenance["omissions"]))
        skills = project_skills(project)
        self.assertGreater(len(skills), 1)
        for skill in skills:
            relative = skill.relative_to(project).as_posix()
            self.assertEqual(provenance["files"][relative], sha256(skill.read_bytes()).hexdigest())

    def test_profiles_do_not_pretend_external_execution_is_supported(self):
        profiles = json.loads((ROOT / "project_profiles.json").read_text())["projects"]
        self.assertEqual(profiles["sample_repo"], {"adapter": "issue-management-v2"})
        for identifier in SOURCES:
            self.assertIn(identifier, profiles)
            self.assertEqual(profiles[identifier], {"adapter": None})

    def test_contract_job_adds_non_model_reports_without_publication_privileges(self):
        workflow = (ROOT / ".github/workflows/project-evaluation.yml").read_text()
        contracts, privileged = workflow.split("\n  evaluate:", 1)
        self.assertNotIn("secrets.", contracts)
        self.assertNotIn("contents: write", contracts)
        self.assertIn("SKILLOPS_CONTAINER_TESTS: '1'", contracts)
        self.assertIn("-p 'test_*.py'", contracts)
        self.assertIn("python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea", contracts)
        self.assertIn("SKILLOPS_LIVE_EVALUATION_ENABLED: 'false'", contracts)
        self.assertIn("cp -R results ci-sample-results", contracts)
        self.assertIn('test "$assessment_status" -eq 2', contracts)
        self.assertIn("SKILLOPS_SAMPLE_REPORTS_DIR=ci-sample-results", contracts)
        self.assertIn("name: sample-onboarding-results", contracts)
        for filename in ("skill-snapshots.json", "skill-evolution.json"):
            self.assertIn(f"ci-sample-results/*/*/{filename}", contracts)
        self.assertNotIn("sample-onboarding-results", privileged)
        self.assertEqual(privileged.count("github.ref == 'refs/heads/main'"), 3)
        self.assertNotIn("pull_request_target", workflow)


@unittest.skipUnless(os.name == "posix", "The existing evaluator requires Linux file controls")
class ProjectSampleReportTests(unittest.TestCase):
    def setUp(self):
        import project_evaluation
        import project_results
        self.evaluation = project_evaluation
        self.results = project_results
        self.catalog = {row["id"]: row for row in self.results.catalog(ROOT)}
        self.assertTrue(PROJECT_IDS <= self.catalog.keys())
        self.assertTrue(all(row["error"] is None for row in self.catalog.values()))

    def assert_sample_report(self, report, run_id, source_commit):
        self.results.validate(report)
        self.assertEqual(report["run_id"], run_id)
        self.assertEqual(report["source_commit"], source_commit)
        self.assertEqual(report["origin"], "github_actions")
        self.assertEqual(report["purpose"], "project_assessment")
        self.assertEqual(report["project_tree_sha256"], self.catalog[report["project_id"]]["tree_sha256"])
        self.assertEqual(report["evaluator_sha256"], self.results.evaluator_hash(ROOT))
        self.assertIsNone(report["source_report_sha256"])
        self.assertIsNone(report["source_schema_version"])
        strings = list(string_values(report))
        for text in strings:
            self.assertNotIn(str(ROOT), text)
            self.assertNotIn(".skillops-private", text)
        if report["project_id"] not in PROJECT_IDS:
            return
        self.assertEqual(report["guide"], self.results.axis("blocked", "guide_integration_pending"))
        expected = ("blocked", "live_disabled") if report["project_id"] == "sample_repo" else (
            "configuration_required", "no_adapter")
        self.assertEqual(report["execution"], self.results.axis(*expected))
        for skill in project_skills(ROOT / "projects" / report["project_id"]):
            body = skill.read_text(encoding="utf-8")
            self.assertFalse(any(body in text for text in strings))

    def assert_indices(self, output, run_id, history_counts):
        index = self.results.read_json(output / "index.json")
        self.assertEqual(set(index), {"schema_version", "projects"})
        self.assertEqual(index["schema_version"], 1)
        identifiers = set(self.catalog) | set(history_counts)
        self.assertEqual({entry["id"] for entry in index["projects"]}, identifiers)
        self.assertEqual(len(index["projects"]), len(identifiers))
        reports = self.results.load_reports(output)
        snapshots = self.results.load_snapshots(output, reports)
        lifecycles = self.results.load_evolution(output, reports, snapshots)
        for entry in index["projects"]:
            active = entry["id"] in self.catalog
            self.assertEqual(entry, {
                "id": entry["id"], "state": "active" if active else "removed",
                "history_count": history_counts[entry["id"]],
                "current_run": run_id if active else None, "index": f"{entry['id']}/index.json",
            })
            project_index = self.results.read_json(output / entry["index"])
            matching = sorted((report for report in reports if report["project_id"] == entry["id"]),
                              key=lambda report: (report["created_at"], report["run_id"]), reverse=True)
            self.assertEqual(len(matching), history_counts[entry["id"]])
            history = [{
                "run_id": report["run_id"], "created_at": report["created_at"], "origin": report["origin"],
                "purpose": report["purpose"], "guide_status": report["guide"]["status"],
                "execution_status": report["execution"]["status"], "report": f"{report['run_id']}/report.json",
            } for report in matching]
            for summary in history:
                key = (entry["id"], summary["run_id"])
                snapshot = snapshots.get(key)
                if snapshot is not None:
                    summary.update(
                        skill_id=snapshot["skill_id"], skill_snapshots=f"{summary['run_id']}/skill-snapshots.json",
                        base_skill_sha256=snapshot["base"]["sha256"],
                        candidate_skill_sha256=snapshot["candidate"]["sha256"] if snapshot["candidate"] else None,
                    )
                lifecycle = lifecycles.get(key)
                if lifecycle is not None:
                    names = {item["skill_key"]: item["display_name"] for item in lifecycle["records"]["identities"]}
                    summary.update(
                        skill_evolution=f"{summary['run_id']}/skill-evolution.json",
                        evolution_skills=[{
                            "skill_key": binding["skill_key"], "display_name": names[binding["skill_key"]],
                            "base_version_id": binding["base_version_id"],
                            "candidate_version_id": binding["candidate_version_id"],
                        } for binding in lifecycle["bindings"]],
                    )
            self.assertEqual(project_index, {"schema_version": 1, "project": entry, "history": history})

    def store_skill_history(self, output, report, include_snapshots=True, include_evolution=True):
        import evolution_records
        content = "Synthetic skill history for contract tests.\n"
        snapshot = {
            "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
            "report_sha256": sha256(self.results.encoded(report)).hexdigest(), "skill_id": "test-skill",
            "base": {"content": content, "sha256": sha256(content.encode()).hexdigest()}, "candidate": None,
        }
        if include_snapshots:
            self.results.store_snapshots(output, snapshot)
        if include_evolution:
            version, retained = evolution_records.capture_version(
                {"SKILL.md": content.encode()}, capture_scope="entrypoint_only")
            records = evolution_records.empty_records()
            records.update(
                identities=[{"skill_key": "contract:test-skill", "display_name": "Synthetic contract skill"}],
                versions=[version],
                skill_versions=[{"skill_key": "contract:test-skill", "version_id": version["version_id"]}],
            )
            self.results.store_evolution(output, {
                "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
                "report_sha256": snapshot["report_sha256"], "records": records,
                "bindings": [{"skill_key": "contract:test-skill", "base_version_id": version["version_id"],
                              "candidate_version_id": None,
                              "legacy_skill_id": snapshot["skill_id"] if include_snapshots else None}],
                "file_contents": [{"version_id": version["version_id"], "path": relative, "encoding": "base64",
                                   "data": base64.b64encode(raw).decode()} for relative, raw in retained.items()],
            })

    def test_index_checks_support_optional_skill_history_without_accepting_false_links(self):
        for include_snapshots, include_evolution in ((True, False), (False, True), (True, True)):
            with (
                self.subTest(snapshots=include_snapshots, evolution=include_evolution),
                tempfile.TemporaryDirectory() as temporary,
            ):
                output = Path(temporary) / "results"
                for project in self.catalog.values():
                    report = self.evaluation.assess(ROOT, project, "106-1", "a" * 40, {})
                    self.results.store(output, report)
                    if project["id"] == "project-a":
                        self.store_skill_history(output, report, include_snapshots, include_evolution)
                self.results.reindex(ROOT, output)
                counts = {identifier: 1 for identifier in self.catalog}
                self.assert_indices(output, "106-1", counts)
                path = output / "project-a/index.json"
                original = self.results.read_json(path)
                mutations = []
                if include_snapshots:
                    mutations.extend((
                        ("skill_id", "unrelated-skill"), ("skill_snapshots", "other/skill-snapshots.json"),
                        ("base_skill_sha256", "b" * 64), ("candidate_skill_sha256", "b" * 64),
                    ))
                if include_evolution:
                    mutations.extend((("skill_evolution", "other/skill-evolution.json"), ("evolution_skills", [])))
                for field, value in mutations:
                    with self.subTest(field=field):
                        changed = copy.deepcopy(original)
                        changed["history"][0][field] = value
                        self.results.atomic_json(path, changed)
                        with self.assertRaises(AssertionError):
                            self.assert_indices(output, "106-1", counts)

    def test_index_checks_reject_false_statuses_and_unmatched_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            for project in self.catalog.values():
                report = self.evaluation.assess(ROOT, project, "101-1", "a" * 40, {})
                self.results.store(output, report)
            self.results.reindex(ROOT, output)
            counts = {identifier: 1 for identifier in self.catalog}
            self.assert_indices(output, "101-1", counts)
            path = output / "project-a/index.json"
            original = self.results.read_json(path)
            for field, value in (("guide_status", "completed"), ("execution_status", "completed"),
                                 ("origin", "historical_import"), ("report", "other/report.json")):
                with self.subTest(field=field):
                    changed = copy.deepcopy(original)
                    changed["history"][0][field] = value
                    self.results.atomic_json(path, changed)
                    with self.assertRaises(AssertionError):
                        self.assert_indices(output, "101-1", counts)

    def assert_additional_project_report(self, skill_bytes, adapter):
        runtime = Mock(side_effect=AssertionError("Model runtime must not initialize"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identifier = "additional-project"
            skill = root / "projects" / identifier / "skills/custom/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_bytes(skill_bytes)
            self.results.atomic_json(root / "project_profiles.json", {
                "schema_version": 1, "projects": {identifier: {"adapter": adapter}},
            })
            project = self.results.catalog(root)[0]
            self.catalog = {identifier: project}
            report = self.evaluation.assess(root, project, "104-1", "a" * 40, {}, runtime_factory=runtime)
            self.assertEqual(report["guide"], self.results.axis("blocked", "guide_integration_pending"))
            expected = ("blocked", "live_disabled") if adapter else ("configuration_required", "no_adapter")
            self.assertEqual(report["execution"], self.results.axis(*expected))
            with patch(f"{__name__}.ROOT", root):
                self.assert_sample_report(report, "104-1", "a" * 40)
                for field, value in (("source_commit", "b" * 40), ("project_tree_sha256", "b" * 64),
                                     ("evaluator_sha256", "b" * 64), ("run_id", "105-1")):
                    with self.subTest(field=field):
                        changed = copy.deepcopy(report)
                        changed[field] = value
                        with self.assertRaises(AssertionError):
                            self.assert_sample_report(changed, "104-1", "a" * 40)
            self.assertEqual(skill.read_bytes(), skill_bytes)
        runtime.assert_not_called()

    def test_report_assertions_allow_additional_supported_projects(self):
        self.assert_additional_project_report((ROOT / "skills/develop/SKILL.md").read_bytes(),
                                              "issue-management-v2")

    def test_report_assertions_preserve_malformed_additional_skills(self):
        for skill_bytes in (b"\xffmalformed", b"", b"blocked"):
            with self.subTest(skill=skill_bytes):
                self.assert_additional_project_report(skill_bytes, None)

    def test_prepared_samples_use_existing_read_only_report_pipeline(self):
        runtime = Mock(side_effect=AssertionError("Model runtime must not initialize"))
        with tempfile.TemporaryDirectory() as temporary, patch(
            "project_evaluation.repositories.register", side_effect=AssertionError("No live registration")
        ) as register:
            output = Path(temporary) / "results"
            first_reports = {}
            for run_id in ("101-1", "102-1"):
                for project in self.catalog.values():
                    report = self.evaluation.assess(ROOT, project, run_id, "a" * 40, {}, runtime_factory=runtime)
                    self.assert_sample_report(report, run_id, "a" * 40)
                    path = self.results.store(output, report)
                    self.results.store(output, report)
                    if run_id == "101-1":
                        first_reports[path] = path.read_bytes()
                    changed = copy.deepcopy(report)
                    changed["source_commit"] = "b" * 40
                    with self.assertRaises(self.evaluation.RuntimeFailure) as raised:
                        self.results.store(output, changed)
                    self.assertEqual(raised.exception.code, "immutable_conflict")
                self.results.reindex(ROOT, output)
            self.assert_indices(output, "102-1", {identifier: 2 for identifier in self.catalog})
            self.assertEqual(len(self.results.load_reports(output)), len(self.catalog) * 2)
            for path, original in first_reports.items():
                self.assertEqual(path.read_bytes(), original)
            runtime.assert_not_called()
            register.assert_not_called()

    def test_new_assessment_preserves_published_history_bytes_and_indices(self):
        published = ROOT / "results"
        originals = public_history_bytes(published)
        self.assertTrue(originals, "The owner's published history must be retained")
        counts = Counter(report["project_id"] for report in self.results.load_reports(published))
        runtime = Mock(side_effect=AssertionError("Model runtime must not initialize"))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results"
            shutil.copytree(published, output, symlinks=True)
            self.assertEqual(public_history_bytes(output), originals)
            historical = self.evaluation.assess(ROOT, self.catalog["project-a"], "100-1", "b" * 40, {},
                                                runtime_factory=runtime)
            historical["evaluator_sha256"] = "b" * 64
            self.results.store(output, historical)
            self.store_skill_history(output, historical)
            counts["project-a"] += 1
            originals.update(public_history_bytes(output))
            for project in self.catalog.values():
                report = self.evaluation.assess(ROOT, project, "103-1", "a" * 40, {}, runtime_factory=runtime)
                self.assert_sample_report(report, "103-1", "a" * 40)
                self.results.store(output, report)
                counts[project["id"]] += 1
            self.results.reindex(ROOT, output)
            self.assert_indices(output, "103-1", counts)
            for relative, original in originals.items():
                self.assertEqual((output / relative).read_bytes(), original)
            runtime.assert_not_called()

    @unittest.skipUnless(os.environ.get("SKILLOPS_SAMPLE_REPORTS_DIR"), "Requires actual CI CLI output")
    def test_actual_cli_artifact_is_complete_and_matches_the_checked_out_revision(self):
        output = Path(os.environ["SKILLOPS_SAMPLE_REPORTS_DIR"])
        run_id = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        published = ROOT / "results"
        previous = self.results.load_reports(published)
        reports = self.results.load_reports(output)
        current = [report for report in reports if report["run_id"] == run_id]
        self.assertEqual(len(current), len(self.catalog))
        self.assertEqual({report["project_id"] for report in current}, set(self.catalog))
        self.assertEqual(len(reports), len(previous) + len(self.catalog))
        expected = {(report["project_id"], report["run_id"]) for report in previous}
        expected.update((identifier, run_id) for identifier in self.catalog)
        self.assertEqual({(report["project_id"], report["run_id"]) for report in reports}, expected)
        for report in current:
            self.assert_sample_report(report, run_id, commit)
        for relative, original in public_history_bytes(published).items():
            self.assertEqual((output / relative).read_bytes(), original)
        counts = Counter(report["project_id"] for report in previous)
        counts.update(self.catalog.keys())
        self.assert_indices(output, run_id, counts)


if __name__ == "__main__":
    unittest.main()
