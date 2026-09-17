import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from copilot_runtime import RuntimeFailure
import evolution_records as evolution
import project_results as results
import skill_assessments as assessments
from test_project_checks import observation


def fixture(*, legacy=False):
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
            **({} if legacy else {"measurement": {"cost_nano_aiu": 1000000000, "elapsed_seconds": 100}}),
        } for index, arm in enumerate(("base", "candidate"))},
        "checks": {"original": observation(failing, status="failed"),
                   "base": observation(deepcopy(passing)), "candidate": observation(deepcopy(passing))},
        "errors": [],
    }
    skill["decision"] = {"status": "improved", "reasons": [], "regression": {
        "status": "passed", "reasons": [], "regressions": []}} if legacy else assessments.decide(skill)
    envelope = {
        "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
        "report_sha256": lifecycle["report_sha256"], "skills": [skill],
    }
    return report, lifecycle, envelope


def decision_cases():
    cases = []
    for name, base_cost, candidate_cost, base_time, candidate_time in (
        ("unchanged", 1e9, 1e9, 100, 100),
        ("cost-boundary", 1e9, 1.05e9, 100, 100),
        ("time-boundary", 1e9, 1e9, 100, 105),
        ("cost-regression", 1e9, 1.16e9, 100, 100),
        ("time-regression", 1e9, 1e9, 100, 132),
        ("decimal-boundary", 0.1, 0.105, 1, 1.05),
        ("just-over-boundary", 1, 1.0500000000000003, 100, 100),
        ("large-measurements", 1e300, 1.06e300, 1e300, 1.05e300),
        ("small-measurements", 1e-300, 1.06e-300, 1e-300, 1.05e-300),
        ("measured-zero", 0, 0, 0, 0),
        ("zero-base-cost", 0, 1, 100, 100),
        ("zero-base-time", 1, 1, 0, 1),
        ("missing-base-cost", None, 1e9, 100, 100),
        ("missing-candidate-cost", 1e9, None, 100, 100),
        ("missing-base-time", 1e9, 1e9, None, 100),
        ("missing-candidate-time", 1e9, 1e9, 100, None),
        ("missing-and-regressed", 1e9, 1.16e9, None, 100),
    ):
        row = fixture()[2]["skills"][0]
        row["applications"]["base"]["measurement"] = {"cost_nano_aiu": base_cost, "elapsed_seconds": base_time}
        row["applications"]["candidate"]["measurement"] = {"cost_nano_aiu": candidate_cost, "elapsed_seconds": candidate_time}
        cases.append({"name": name, "row": row, "expected": assessments.decide(row), "legacy": False})
    for name in ("missing-measurement", "missing-application", "quality-regression", "project-regression", "equal-quality"):
        row = fixture()[2]["skills"][0]
        if name == "missing-measurement":
            row["applications"]["base"].pop("measurement")
        elif name == "missing-application":
            row["applications"]["candidate"] = None
        elif name == "quality-regression":
            row["quality"]["candidate"]["dimensions"][0]["score"] = 1
            row["applications"]["candidate"]["measurement"]["elapsed_seconds"] = 132
        elif name == "project-regression":
            row["checks"]["candidate"]["cases"][0]["status"] = "failed"
            row["checks"]["candidate"]["status"] = "failed"
            row["applications"]["candidate"]["measurement"]["cost_nano_aiu"] = 1.16e9
        else:
            row["quality"]["candidate"] = deepcopy(row["quality"]["base"])
        cases.append({"name": name, "row": row, "expected": assessments.decide(row), "legacy": False})
    for measured in (False, True):
        report, lifecycle, data = fixture(legacy=True)
        row = data["skills"][0]
        if measured:
            row["applications"]["base"]["measurement"] = {"cost_nano_aiu": 1e9, "elapsed_seconds": 100}
            row["applications"]["candidate"]["measurement"] = {"cost_nano_aiu": 1.16e9, "elapsed_seconds": 132}
        assessments.validate(data, report, lifecycle)
        cases.append({"name": f"legacy-{measured}", "row": row, "expected": row["decision"], "legacy": True})
        cases.append({"name": f"reevaluate-legacy-{measured}", "row": row,
                      "expected": assessments.decide(row), "legacy": False})
    return cases


class SkillAssessmentTests(unittest.TestCase):
    def test_qualified_candidate_requires_quality_task_and_regression_evidence(self):
        report, lifecycle, data = fixture()
        self.assertEqual(data["skills"][0]["decision"]["status"], "improved")
        self.assertEqual(data["skills"][0]["decision"]["policy_version"], assessments.POLICY["version"])
        self.assertEqual(assessments.validate(data, report, lifecycle), data)

    def test_cost_or_time_regression_above_policy_cap_is_unverified_not_rejected(self):
        for metric, candidate in (("cost_nano_aiu", 1.16e9), ("elapsed_seconds", 132)):
            with self.subTest(metric=metric):
                row = fixture()[2]["skills"][0]
                row["applications"]["candidate"]["measurement"][metric] = candidate
                decision = assessments.decide(row)
                self.assertEqual(decision["status"], "unverified")
                self.assertEqual(decision["reasons"], ["efficiency_regression"])
                self.assertEqual(decision["regression"]["status"], "passed")
                self.assertEqual(decision["policy_version"], assessments.POLICY["version"])

    def test_efficiency_boundaries_are_exact_and_zero_measurements_are_not_missing(self):
        for metric in ("cost_nano_aiu", "elapsed_seconds"):
            for before, after, expected in ((100, 105, "improved"), (0.1, 0.105, "improved"),
                                            (1, 1.05, "improved"), (100, 84, "improved"),
                                            (0, 0, "improved"), (0, 1, "unverified"),
                                            (1, 1.0500000000000003, "unverified"),
                                            (1e300, 1.06e300, "unverified"),
                                            (1e-300, 1.06e-300, "unverified")):
                with self.subTest(metric=metric, before=before, after=after):
                    row = fixture()[2]["skills"][0]
                    row["applications"]["base"]["measurement"][metric] = before
                    row["applications"]["candidate"]["measurement"][metric] = after
                    decision = assessments.decide(row)
                    self.assertEqual(decision["status"], expected)
                    self.assertNotIn("efficiency_unverified", decision["reasons"])

    def test_missing_efficiency_measurements_block_improvement_without_inventing_zero(self):
        for arm in ("base", "candidate"):
            for metric in ("cost_nano_aiu", "elapsed_seconds", "measurement", "application"):
                with self.subTest(arm=arm, metric=metric):
                    row = fixture()[2]["skills"][0]
                    if metric == "application":
                        row["applications"][arm] = None
                    elif metric == "measurement":
                        row["applications"][arm].pop("measurement")
                    else:
                        row["applications"][arm]["measurement"][metric] = None
                    decision = assessments.decide(row)
                    self.assertEqual(decision["status"], "unverified")
                    self.assertIn("efficiency_unverified", decision["reasons"])
                    self.assertNotIn("efficiency_regression", decision["reasons"])

    def test_known_efficiency_regression_and_missing_measurement_are_both_reported(self):
        row = fixture()[2]["skills"][0]
        row["applications"]["candidate"]["measurement"] = {"cost_nano_aiu": 1.16e9, "elapsed_seconds": None}
        decision = assessments.decide(row)
        self.assertEqual(decision["status"], "unverified")
        self.assertEqual(decision["reasons"], ["efficiency_regression", "efficiency_unverified"])

    def test_efficiency_policy_does_not_overrule_confirmed_quality_or_project_rejection(self):
        for gate in ("quality", "project"):
            with self.subTest(gate=gate):
                row = fixture()[2]["skills"][0]
                row["applications"]["candidate"]["measurement"]["elapsed_seconds"] = 132
                if gate == "quality":
                    row["quality"]["candidate"]["dimensions"][0]["score"] = 1
                else:
                    row["checks"]["candidate"]["cases"][0]["status"] = "failed"
                    row["checks"]["candidate"]["status"] = "failed"
                decision = assessments.decide(row)
                self.assertEqual(decision["status"], "rejected")
                self.assertIn("efficiency_regression", decision["reasons"])
                self.assertIn(f"{gate}_regression", decision["reasons"])

    def test_applied_efficiency_limit_and_version_come_from_the_existing_candidate_policy(self):
        row = fixture()[2]["skills"][0]
        row["applications"]["candidate"]["measurement"]["cost_nano_aiu"] = 1.16e9
        with patch.dict(assessments.POLICY, {"version": 2, "maximum_efficiency_regression_percent": 20}):
            decision = assessments.decide(row)
            self.assertEqual(decision["status"], "improved")
            self.assertEqual(decision["policy_version"], 2)
        self.assertEqual(assessments.decide(row)["status"], "unverified")

    def test_versioned_assessments_reject_forged_efficiency_verdicts_and_unsupported_policy_versions(self):
        report, lifecycle, data = fixture()
        row = data["skills"][0]
        row["applications"]["candidate"]["measurement"]["elapsed_seconds"] = 132
        with self.assertRaises(RuntimeFailure):
            assessments.validate(data, report, lifecycle)
        row["decision"] = assessments.decide(row)
        self.assertEqual(assessments.validate(data, report, lifecycle), data)
        for version in (None, True, 1.0, "1", 0, 2, -1):
            with self.subTest(version=version):
                changed = deepcopy(data)
                changed["skills"][0]["decision"]["policy_version"] = version
                with self.assertRaises(RuntimeFailure):
                    assessments.validate(changed, report, lifecycle)
        changed = deepcopy(data)
        changed["skills"][0]["decision"]["reasons"] = []
        with self.assertRaises(RuntimeFailure):
            assessments.validate(changed, report, lifecycle)

    def test_invalid_measurements_remain_invalid_instead_of_becoming_missing_or_zero(self):
        for value in (True, -1, "100", float("inf"), float("nan")):
            with self.subTest(value=value):
                row = fixture()[2]["skills"][0]
                row["applications"]["base"]["measurement"]["cost_nano_aiu"] = value
                with self.assertRaises(RuntimeFailure):
                    assessments.decide(row)

    def test_legacy_assessments_keep_original_decisions_but_new_evaluation_always_applies_current_policy(self):
        report, lifecycle, data = fixture(legacy=True)
        original = deepcopy(data)
        self.assertEqual(assessments.validate(data, report, lifecycle), original)
        row = data["skills"][0]
        decision = assessments.decide(row)
        self.assertEqual(decision["status"], "unverified")
        self.assertEqual(decision["reasons"], ["efficiency_unverified"])
        self.assertEqual(decision["policy_version"], assessments.POLICY["version"])
        self.assertEqual(data, original)
        row["applications"]["base"]["measurement"] = {"cost_nano_aiu": 1e9, "elapsed_seconds": 100}
        row["applications"]["candidate"]["measurement"] = {"cost_nano_aiu": 1.16e9, "elapsed_seconds": 132}
        self.assertEqual(assessments.validate(data, report, lifecycle)["skills"][0]["decision"]["status"], "improved")
        self.assertEqual(assessments.decide(row)["reasons"], ["efficiency_regression"])
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            incoming, target, site = (Path(folder) / name for name in ("incoming", "target", "site"))
            results.store(incoming, report)
            results.store_evolution(incoming, lifecycle)
            path = results.store_assessments(incoming, data)
            original_bytes = path.read_bytes()
            results.merge_results(source, incoming, target)
            results.build(source, target, site)
            self.assertEqual((site / "results/sample_repo/123-1/skill-assessments.json").read_bytes(), original_bytes)

    @unittest.skipUnless(shutil.which("node"), "Node is required for the Python/JavaScript decision parity check")
    def test_python_and_javascript_decisions_agree_on_current_policy_and_legacy_evidence(self):
        cases = decision_cases()
        script = (
            'import { decide } from "./dashboard/assessments.js";'
            'let input = ""; for await (const chunk of process.stdin) input += chunk;'
            'process.stdout.write(JSON.stringify(JSON.parse(input).map(item =>'
            'decide(item.row, item.legacy ? null : undefined))));'
        )
        result = subprocess.run([shutil.which("node"), "--experimental-default-type=module", "--input-type=module", "-e", script],
                                input=json.dumps(cases, allow_nan=False), text=True, capture_output=True, timeout=20,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        self.assertEqual(len(actual), len(cases))
        for case, decision in zip(cases, actual):
            with self.subTest(case=case["name"]):
                self.assertEqual(decision, case["expected"])

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
