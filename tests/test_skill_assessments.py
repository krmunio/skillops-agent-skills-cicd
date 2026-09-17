import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from copilot_runtime import RuntimeFailure
import evolution_records as evolution
import project_results as results
import skill_assessments as assessments
from test_project_checks import observation


def fixture():
    report = {
        "schema_version": 1, "project_id": "sample_repo", "run_id": "123-1",
        "created_at": "2026-09-16T21:00:00+00:00", "origin": "github_actions",
        "purpose": "project_assessment", "source_commit": "a" * 40,
        "project_tree_sha256": "b" * 64, "evaluator_sha256": "c" * 64,
        "source_report_sha256": None, "source_schema_version": None,
        "guide": results.axis("completed", "evaluation_completed"),
        "execution": results.axis("completed", "evaluation_completed"),
    }
    records = evolution.empty_records()
    records["identities"] = [{"skill_key": "skillops:develop", "display_name": "develop"}]
    content = []
    versions = []
    for body in ("Check changes.", "Check changes and boundary cases."):
        version, retained = evolution.capture_version(
            {"SKILL.md": f"---\nname: develop\n---\n{body}\n".encode()}, capture_scope="entrypoint_only")
        versions.append(version["version_id"])
        records["versions"].append(version)
        records["skill_versions"].append({"skill_key": "skillops:develop", "version_id": version["version_id"]})
        content.extend({"version_id": version["version_id"], "path": name, "encoding": "base64",
                        "data": base64.b64encode(raw).decode()} for name, raw in retained.items())
    lifecycle = {
        "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
        "report_sha256": sha256(results.encoded(report)).hexdigest(), "records": records,
        "bindings": [{"skill_key": "skillops:develop", "base_version_id": versions[0],
                      "candidate_version_id": versions[1], "legacy_skill_id": None}],
        "file_contents": content,
    }
    passing = [{"id": "stable", "status": "passed"}, {"id": "bug", "status": "passed"}]
    failing = [{"id": "stable", "status": "passed"}, {"id": "bug", "status": "failed"}]
    quality = {
        "status": "completed", "rubric_sha256": "d" * 64, "context_sha256": "e" * 64,
        "dimensions": [{"id": "clarity", "score": 2}],
        "findings": [{"id": "boundaries", "severity": "warning", "message": "Boundary guidance is missing."}],
    }
    better = deepcopy(quality)
    better["dimensions"][0]["score"] = 3
    better["findings"] = []
    skill = {
        "skill_key": "skillops:develop", "source_path": ".github/skills/develop",
        "base_version_id": versions[0], "candidate_version_id": versions[1],
        "quality": {"base": quality, "candidate": better},
        "generation": {"status": "generated", "addressed_findings": ["boundaries"],
                       "hypothesis": "Explicit boundary guidance may improve task completion."},
        "work": {"sha256": "f" * 64, "provenance": "generated", "check_id": "bug"},
        "applications": {arm: {
            "version_id": versions[index], "staged_version_id": versions[index],
            "work_sha256": "f" * 64, "output_sha256": str(index + 1) * 64,
            "activated": True, "changed": True, "task_outcome": "satisfied",
        } for index, arm in enumerate(("base", "candidate"))},
        "checks": {"original": observation(failing, status="failed"),
                   "base": observation(deepcopy(passing)), "candidate": observation(deepcopy(passing))},
        "errors": [],
    }
    skill["decision"] = assessments.decide(skill)
    envelope = {
        "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
        "report_sha256": lifecycle["report_sha256"], "skills": [skill],
    }
    return report, lifecycle, envelope


class SkillAssessmentTests(unittest.TestCase):
    def test_qualified_candidate_requires_quality_task_and_regression_evidence(self):
        report, lifecycle, data = fixture()
        self.assertEqual(data["skills"][0]["decision"]["status"], "improved")
        self.assertEqual(assessments.validate(data, report, lifecycle), data)

    def test_missing_worse_or_incomparable_quality_cannot_qualify(self):
        for change in ("missing", "regressed", "rubric", "applicability"):
            with self.subTest(change=change):
                row = fixture()[2]["skills"][0]
                candidate = row["quality"]["candidate"]
                if change == "missing":
                    row["quality"]["candidate"] = None
                elif change == "regressed":
                    candidate["dimensions"][0]["score"] = 1
                elif change == "rubric":
                    candidate["rubric_sha256"] = "9" * 64
                else:
                    candidate["dimensions"][0]["score"] = None
                self.assertNotEqual(assessments.decide(row)["status"], "improved")

    def test_invented_generation_evidence_does_not_qualify(self):
        row = fixture()[2]["skills"][0]
        row["generation"]["addressed_findings"] = ["invented"]
        self.assertEqual(assessments.decide(row)["status"], "unverified")

    def test_equal_quality_is_not_an_improvement(self):
        row = fixture()[2]["skills"][0]
        row["quality"]["candidate"] = deepcopy(row["quality"]["base"])
        self.assertEqual(assessments.decide(row)["status"], "not_improved")

    def test_nonempty_irrelevant_edit_and_model_claim_are_not_task_evidence(self):
        for change in ("already_passing", "wrong_check", "not_satisfied", "no_op"):
            with self.subTest(change=change):
                row = fixture()[2]["skills"][0]
                if change == "already_passing":
                    row["checks"]["original"] = deepcopy(row["checks"]["base"])
                elif change == "wrong_check":
                    row["work"]["check_id"] = "not collected"
                elif change == "not_satisfied":
                    row["applications"]["candidate"]["task_outcome"] = "not_satisfied"
                else:
                    row["applications"]["candidate"]["changed"] = False
                self.assertNotEqual(assessments.decide(row)["status"], "improved")

    def test_activation_is_bound_to_exact_version_and_work(self):
        for field, value in (("staged_version_id", "sha256:" + "a" * 64),
                             ("activated", False), ("work_sha256", "a" * 64)):
            with self.subTest(field=field):
                row = fixture()[2]["skills"][0]
                row["applications"]["candidate"][field] = value
                self.assertEqual(assessments.decide(row)["status"], "unverified")

    def test_confirmed_regression_survives_missing_quality(self):
        row = fixture()[2]["skills"][0]
        row["quality"]["candidate"] = None
        row["checks"]["candidate"]["cases"][0]["status"] = "failed"
        row["checks"]["candidate"]["status"] = "failed"
        self.assertEqual(assessments.decide(row)["status"], "rejected")

    def test_envelope_rejects_wrong_binding_duplicate_identity_and_forged_decision(self):
        mutations = [
            lambda data: data.update(report_sha256="0" * 64),
            lambda data: data["skills"].append(deepcopy(data["skills"][0])),
            lambda data: data["skills"][0].update(candidate_version_id="sha256:" + "0" * 64),
            lambda data: data["skills"][0]["decision"].update(status="not_improved"),
            lambda data: data["skills"][0].update(source_path="../outside"),
            lambda data: data["skills"][0].update(raw_prompt="not public"),
            lambda data: data["skills"][0]["quality"]["base"]["dimensions"][0].update(score=True),
            lambda data: data["skills"][0]["applications"]["candidate"].update(activated=1),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                report, lifecycle, data = fixture()
                mutation(data)
                with self.assertRaises(RuntimeFailure):
                    assessments.validate(data, report, lifecycle)

    def test_immutable_attachment_survives_store_merge_index_build(self):
        report, lifecycle, data = fixture()
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming, target = root / "incoming", root / "results"
            results.store(incoming, report)
            results.store_evolution(incoming, lifecycle)
            saved = results.store_assessments(incoming, data)
            self.assertEqual(results.store_assessments(incoming, data), saved)
            results.merge_results(source, incoming, target)
            output = root / "site"
            results.build(source, target, output)
            self.assertEqual(results.load_assessments(output / "results")[(report["project_id"], report["run_id"])], data)
            history = json.loads((output / "results/sample_repo/index.json").read_text())["history"]
            self.assertEqual(history[0]["skill_assessments"], "123-1/skill-assessments.json")
            self.assertEqual(saved.read_bytes(), (output / "results/sample_repo/123-1/skill-assessments.json").read_bytes())
            changed = deepcopy(data)
            changed["skills"][0]["generation"]["hypothesis"] = "Changed same-path evidence."
            with self.assertRaises(RuntimeFailure):
                results.store_assessments(incoming, changed)

    def test_orphan_invalid_and_conflicting_attachments_fail_before_publication(self):
        report, lifecycle, data = fixture()
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "incoming/sample_repo/123-1"
            path.mkdir(parents=True)
            (path / "skill-assessments.json").write_bytes(results.encoded(data))
            with self.assertRaises(RuntimeFailure):
                results.load_assessments(root / "incoming")
            results.store(root / "incoming", report)
            with self.assertRaises(RuntimeFailure):
                results.load_assessments(root / "incoming")
            results.store_evolution(root / "incoming", lifecycle)
            data["skills"][0]["decision"]["status"] = "rejected"
            (path / "skill-assessments.json").write_bytes(results.encoded(data))
            with self.assertRaises(RuntimeFailure):
                results.merge_results(source, root / "incoming", root / "target")
            self.assertFalse((root / "target").exists())
            with self.assertRaises(RuntimeFailure):
                results.build(source, root / "incoming", root / "site")
            self.assertFalse((root / "site").exists())


if __name__ == "__main__":
    unittest.main()
