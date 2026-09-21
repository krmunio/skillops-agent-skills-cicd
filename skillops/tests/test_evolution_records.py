"""Offline contracts for captured Skill history, not deployment authority."""

import copy
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from copilot_runtime import RuntimeFailure


BASE_RUN = "20260914T072451Z-50354146d951"
CANDIDATE_RUN = "20260915T005404Z-611d681b8bf7"
COMPARISON_RUN = "20260915T010916Z-74058d9807da"
KEY = "skillops:develop"
STAMP = "2026-09-16T00:00:00Z"
BASE = b"---\nname: develop\n---\nRead the task.\n"
CANDIDATE = BASE + b"Check the result.\n"


def digest(raw):
    return sha256(raw).hexdigest()


def archive(root):
    baseline = {"schema_version": 2, "run_id": BASE_RUN, "purpose": "baseline",
                "status": "completed", "skill_sha256": digest(BASE)}
    raw = json.dumps(baseline).encode()
    candidate = {"schema_version": 2, "run_id": CANDIDATE_RUN, "purpose": "candidate",
                 "status": "completed", "baseline_run": BASE_RUN, "baseline_sha256": digest(raw),
                 "base_skill_sha256": digest(BASE), "skill_sha256": digest(CANDIDATE),
                 "observed_failures": [], "rationale": "PRIVATE raw prose"}
    comparison = {"schema_version": 2, "run_id": COMPARISON_RUN, "purpose": "comparison",
                  "status": "completed", "candidate_run": CANDIDATE_RUN,
                  "skill_sha256": {"base": digest(BASE), "candidate": digest(CANDIDATE)},
                  "decision": {"decision": "rejected", "private_reason": "PRIVATE feedback"}}
    for identifier, name, value in (
        (BASE_RUN, "report.json", baseline), (CANDIDATE_RUN, "candidate.json", candidate),
        (COMPARISON_RUN, "comparison.json", comparison),
    ):
        (root / identifier).mkdir()
        (root / identifier / name).write_text(json.dumps(value))
    (root / CANDIDATE_RUN / "base-SKILL.md").write_bytes(BASE)
    (root / CANDIDATE_RUN / "SKILL.md").write_bytes(CANDIDATE)
    return candidate, comparison


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("evolution_records"),
                             "The offline evolution record contract is not implemented")
        self.e = importlib.import_module("evolution_records")

    def graph(self):
        version, _ = self.e.capture_version({"SKILL.md": BASE}, capture_scope="entrypoint_only")
        rows = self.e.empty_records()
        rows["identities"] = [{"skill_key": KEY, "display_name": "Develop"}]
        rows["versions"] = [version]
        rows["skill_versions"] = [{"skill_key": KEY, "version_id": version["version_id"]}]
        return rows

    def test_scoped_manifest_identity_and_exact_bytes(self):
        files = {"SKILL.md": BASE, "scripts/check.py": b"pass\n", "assets/icon.bin": b"\xff\0"}
        version, retained = self.e.capture_version(files, capture_scope="complete_bundle",
                                                  complete_inventory=list(files))
        same, _ = self.e.capture_version(dict(reversed(list(files.items()))),
                                         capture_scope="complete_bundle", complete_inventory=list(files))
        self.assertEqual(version, same)
        self.assertEqual(retained, files)
        self.assertEqual(version["files"][0]["path"], "SKILL.md")
        for name in files:
            changed = {**files, name: files[name] + b"x"}
            other, _ = self.e.capture_version(changed, capture_scope="complete_bundle",
                                              complete_inventory=list(changed))
            self.assertNotEqual(version["version_id"], other["version_id"])
        partial, _ = self.e.capture_version({"SKILL.md": BASE}, capture_scope="entrypoint_only")
        full, _ = self.e.capture_version({"SKILL.md": BASE}, capture_scope="complete_bundle",
                                         complete_inventory=["SKILL.md"])
        self.assertNotEqual(partial["version_id"], full["version_id"])

    def test_capture_rejects_incomplete_and_unsafe_inventories(self):
        for inventory in (None, [], ["SKILL.md", "missing"], ["SKILL.md", "SKILL.md"]):
            with self.subTest(inventory=inventory), self.assertRaises(RuntimeFailure):
                self.e.capture_version({"SKILL.md": BASE}, capture_scope="complete_bundle",
                                       complete_inventory=inventory)
        for name in ("/etc/file", "../file", "a//b", "./a", "a\\b", "a/../b", "a\x00b", "C:/file"):
            with self.subTest(name=name), self.assertRaises(RuntimeFailure):
                self.e.capture_version({"SKILL.md": BASE, name: b"x"}, capture_scope="complete_bundle",
                                       complete_inventory=["SKILL.md", name])
        with self.assertRaises(RuntimeFailure):
            self.e.capture_version({"SKILL.md": BASE, "extra": b"x"}, capture_scope="entrypoint_only")
        with self.assertRaises(RuntimeFailure):
            self.e.capture_version({"SKILL.md": b"x" * (2 * 1024 * 1024 + 1)},
                                   capture_scope="entrypoint_only")

    def test_identity_does_not_follow_display_name_or_path(self):
        rows = self.graph()
        rows["sources"] = [{"skill_key": KEY, "project_id": "sample_repo", "kind": "workspace",
                            "scope": "project", "path": ".github/skills/develop",
                            "observed_at": STAMP, "evidence_ref": None}]
        original = copy.deepcopy(rows)
        rows["identities"][0]["display_name"] = "Renamed"
        rows["sources"][0]["path"] = ".agents/skills/renamed"
        self.e.validate_public_records(rows)
        self.assertEqual(original["versions"], rows["versions"])
        self.assertEqual(original["identities"][0]["skill_key"], rows["identities"][0]["skill_key"])
        rows["identities"].append({"skill_key": "other:develop", "display_name": "Renamed"})
        self.e.validate_public_records(rows)
        self.assertEqual(len(rows["identities"]), 2)

    def test_public_graph_rejects_private_fields_dangling_links_and_bad_types(self):
        def invalid(change):
            rows = self.graph()
            change(rows)
            with self.assertRaises(RuntimeFailure):
                self.e.validate_public_records(rows)
        invalid(lambda r: r["identities"][0].update(rationale="private"))
        invalid(lambda r: r["identities"].append(dict(r["identities"][0])))
        invalid(lambda r: r["skill_versions"][0].update(skill_key="other:missing"))
        invalid(lambda r: r["versions"][0]["files"][0].update(bytes=True))
        invalid(lambda r: r["versions"][0]["files"][0].update(sha256="f" * 64))
        invalid(lambda r: r["sources"].append({
            "skill_key": KEY, "project_id": "sample_repo", "kind": "workspace", "scope": "personal",
            "path": "/home/person/.skills", "observed_at": STAMP, "evidence_ref": None}))
        self.e.validate_public_records(self.graph())

    def test_saved_lineage_preserves_rejection_and_partial_legacy_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive(root)
            with patch("candidates.propose", side_effect=AssertionError("model")), \
                 patch("candidates.compare", side_effect=AssertionError("evaluation")), \
                 patch("candidates.decide", side_effect=AssertionError("policy")):
                generation, versions, contents = self.e.import_candidate(root, CANDIDATE_RUN, skill_key=KEY)
                comparison = self.e.import_comparison(root, COMPARISON_RUN, skill_key=KEY,
                                                       candidate_record=generation)
            self.assertEqual(generation["observed_failure_count"], 0)
            self.assertEqual(generation["hypothesis_kind"], "unknown")
            self.assertEqual(generation["baseline_ref"]["availability"], "verified")
            self.assertEqual(comparison["decision"], "rejected")
            self.assertEqual(comparison["candidate_ref"]["availability"], "referenced_only")
            self.assertIsNone(comparison["candidate_ref"]["artifact_sha256"])
            rows = self.e.empty_records()
            rows.update(identities=[{"skill_key": KEY, "display_name": "Develop"}], versions=versions,
                        skill_versions=[{"skill_key": KEY, "version_id": v["version_id"]} for v in versions],
                        generations=[generation], comparisons=[comparison])
            self.e.validate_public_records(rows)
            self.assertNotIn("PRIVATE", json.dumps(rows))
            self.assertEqual(contents[generation["base_version_id"]]["SKILL.md"], BASE)
            bad = copy.deepcopy(rows)
            bad["comparisons"][0]["candidate_entrypoint_sha256"] = "f" * 64
            with self.assertRaises(RuntimeFailure):
                self.e.validate_public_records(bad)

    def test_absent_baseline_is_partial_but_corrupt_evidence_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive(root)
            path = root / BASE_RUN / "report.json"
            raw = path.read_bytes()
            path.unlink()
            generation, _, _ = self.e.import_candidate(root, CANDIDATE_RUN, skill_key=KEY)
            self.assertEqual(generation["baseline_ref"]["availability"], "referenced_only")
            path.write_bytes(raw + b" ")
            with self.assertRaises(RuntimeFailure):
                self.e.import_candidate(root, CANDIDATE_RUN, skill_key=KEY)
            path.write_bytes(raw)
            comparison_path = root / COMPARISON_RUN / "comparison.json"
            value = json.loads(comparison_path.read_text())
            for commitment in ("f" * 64, None, "bad"):
                value["candidate_sha256"] = commitment
                comparison_path.write_text(json.dumps(value))
                with self.assertRaises(RuntimeFailure):
                    self.e.import_comparison(root, COMPARISON_RUN, skill_key=KEY,
                                             candidate_record=generation)
            value["candidate_sha256"] = digest((root / CANDIDATE_RUN / "candidate.json").read_bytes())
            comparison_path.write_text(json.dumps(value))
            result = self.e.import_comparison(root, COMPARISON_RUN, skill_key=KEY,
                                              candidate_record=generation)
            self.assertEqual(result["candidate_ref"]["availability"], "verified")
            value["skill_sha256"]["base"] = "f" * 64
            comparison_path.write_text(json.dumps(value))
            with self.assertRaises(RuntimeFailure):
                self.e.import_comparison(root, COMPARISON_RUN, skill_key=KEY, candidate_record=generation)

    def test_unknown_adoption_and_no_write_pin_observation(self):
        kwargs = dict(project_id="sample_repo", repository_id="sample-repo",
                      expected_engine_skill_id="develop", skill_key=KEY, observed_at=STAMP)
        unknown = self.e.observe_registry(None, **kwargs)
        self.assertEqual(unknown["state"], "unknown")
        self.assertIsNone(unknown["version_id"])
        raw = json.dumps({"schema_version": 1, "repositories": {
            "sample-repo": {"path": "/private/project", "skill_id": "develop",
                            "skill_sha256": digest(BASE), "evaluation_set": "issue-management-v2"}},
            "skills": {digest(BASE): BASE.decode()}, "comparisons": {}}).encode()
        with patch("repositories._state", side_effect=AssertionError("filesystem write")):
            result = self.e.observe_registry(raw, **kwargs)
        self.assertEqual(result["state"], "entrypoint_pin_observed")
        self.assertEqual(result["entrypoint_sha256"], digest(BASE))
        self.assertIsNone(result["version_id"])
        self.assertNotIn("/private", json.dumps(result))
        self.assertEqual(result["registry_sha256"], digest(raw))
        with self.assertRaises(RuntimeFailure):
            self.e.observe_registry(raw, **{**kwargs, "repository_id": "missing"})
        with self.assertRaises(RuntimeFailure):
            self.e.observe_registry(b"{}", **kwargs)
        rows = self.graph()
        rows["adoptions"] = [unknown, result]
        self.e.validate_public_records(rows)
        rows["adoptions"][0]["version_id"] = rows["versions"][0]["version_id"]
        with self.assertRaises(RuntimeFailure):
            self.e.validate_public_records(rows)


if __name__ == "__main__":
    unittest.main()
