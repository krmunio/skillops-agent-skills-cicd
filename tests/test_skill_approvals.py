"""Local-state tests only; provider doubles below are NOT live evaluation evidence."""

import base64
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from copilot_runtime import RuntimeFailure
import evolution_records as evolution
import project_checks
import project_evaluation
import project_results as results
import repositories
import skill_assessments
import skill_guide
import skill_pipeline
from test_skill_assessments import fixture


def digest(value):
    return sha256(results.encoded(value)).hexdigest()


class ApprovalTests(unittest.TestCase):
    """State-machine unit tests with synthetic evidence-provider doubles.

    Real common-provider and replay integration is exercised separately below;
    no positive transition here establishes live or confirmation evidence.
    """
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("skill_approvals"),
                             "Local approval implementation is missing.")
        self.a = importlib.import_module("skill_approvals")
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "projects/sample_repo"
        self.source_path = ".github/skills/develop"
        bundle = self.project / self.source_path
        bundle.mkdir(parents=True)
        (bundle / "SKILL.md").write_text("---\nname: develop\ndescription: Test fixture.\n---\nCheck changes.\n")
        (bundle / "reference.txt").write_text("Protected companion fixture.\n")
        (self.project / "app.py").write_text("VALUE = 1\n")
        (self.root / "eval").mkdir()
        original = Path(__file__).resolve().parents[1]
        shutil.copyfile(original / "eval/skill-guide-rubric.json", self.root / "eval/skill-guide-rubric.json")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "projects", "eval"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Offline fixture"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        self.base = skill_pipeline.captured_files(self.project, skill_guide.discover(self.project)[0])
        files = {**self.base[1], "SKILL.md": self.base[1]["SKILL.md"] + b"Check boundaries.\n"}
        self.candidate = evolution.capture_version(files, capture_scope="complete_bundle",
                                                   complete_inventory=list(files))
        self.output = self.root / "results"
        self.key = "skillops:develop"
        self.cycle_id = "local-20260917T120000Z-111111111111"
        self.replays = {}
        self.work_items = []
        self.providers = []
        for index, split in enumerate(("development", "confirmation"), 1):
            run_id = f"local-20260917T12000{index}Z-{str(index) * 12}"
            report, lifecycle, assessment = fixture()
            report.update(run_id=run_id, origin="local", source_commit=self.commit,
                          project_tree_sha256=results.tree_hash(self.project),
                          evaluator_sha256=results.evaluator_hash(self.root))
            lifecycle.update(run_id=run_id, report_sha256=digest(report))
            lifecycle["records"]["versions"] = [self.base[0], self.candidate[0]]
            lifecycle["records"]["skill_versions"] = [
                {"skill_key": self.key, "version_id": capture[0]["version_id"]}
                for capture in (self.base, self.candidate)]
            lifecycle["bindings"][0].update(base_version_id=self.base[0]["version_id"],
                                              candidate_version_id=self.candidate[0]["version_id"])
            lifecycle["file_contents"] = [
                {"version_id": version["version_id"], "path": name, "encoding": "base64",
                 "data": base64.b64encode(raw).decode()}
                for version, files in (self.base, self.candidate) for name, raw in files.items()]
            results.store(self.output, report)
            results.store_evolution(self.output, lifecycle)
            work = {
                "schema_version": 1, "task_id": f"task-{split}", "project_id": "sample_repo",
                "source_commit": self.commit, "project_tree_sha256": report["project_tree_sha256"],
                "request": f"Offline {split} provider fixture, not measured work.",
                "split": split, "sources": {"app.py": sha256((self.project / "app.py").read_bytes()).hexdigest()},
                "checks": {"plan_sha256": "1" * 64, "protected_sha256": "2" * 64,
                           "required_case_ids": [f"case-{split}"], "required_gate_ids": []},
            }
            work["input_sha256"] = digest(work)
            self.work_items.append(work)
            path = self.root / ".skillops-private/work-items" / work["task_id"] / (work["input_sha256"] + ".json")
            path.parent.mkdir(parents=True)
            path.write_bytes(results.encoded(work))
            row = assessment["skills"][0]
            row.pop("generation")
            row.update(base_version_id=self.base[0]["version_id"],
                       candidate_version_id=self.candidate[0]["version_id"])
            row["work"] = {key: work[key] for key in ("task_id", "input_sha256", "split", "checks")}
            row["work"]["provenance"] = "recorded"
            reference = {
                "schema_version": 1, "project_id": "sample_repo", "skill_key": self.key,
                "source_path": self.source_path, "source_commit": self.commit,
                "project_tree_sha256": report["project_tree_sha256"], "input_sha256": work["input_sha256"],
                "original_version_id": self.base[0]["version_id"],
                "rubric_sha256": project_checks.digest(results.read_json(self.root / "eval/skill-guide-rubric.json")),
                "evaluator_sha256": report["evaluator_sha256"],
            }
            reference["reference_sha256"] = digest(reference)
            row["reference_sha256"] = reference["reference_sha256"]
            for arm, capture in (("base", self.base), ("candidate", self.candidate)):
                row["applications"][arm].update(version_id=capture[0]["version_id"],
                                                staged_version_id=capture[0]["version_id"],
                                                work_sha256=work["input_sha256"])
            row["decision"]["policy_id"] = "replay-v1"
            replay = {
                "schema_version": 1, "project_id": "sample_repo", "run_id": run_id,
                "report_sha256": digest(report), "execution_mode": "live",
                "reference": reference, "generation": None if split == "confirmation" else {},
                "evaluation": row,
            }
            self.replays[("sample_repo", run_id)] = replay
            results.atomic_json(self.output / "sample_repo" / run_id / "replay-evaluation.json", replay)
        development, confirmation = self.replays.values()
        self.cycle = {
            "schema_version": 1, "project_id": "sample_repo", "run_id": self.cycle_id,
            "cycle_id": self.cycle_id, "execution_mode": "live", "skill_key": self.key,
            "source_path": self.source_path, "input_sha256": self.work_items[0]["input_sha256"],
            "reference_sha256": development["reference"]["reference_sha256"],
            "original_version_id": self.base[0]["version_id"], "max_rounds": 1,
            "budget": {"max_invocations": 10, "max_seconds": 100, "max_ai_credits_per_session": None},
            "rounds": [{"evaluation_ref": self.ref(development), "candidate_version_id": self.candidate[0]["version_id"]}],
            "stop_reason": "improved", "selected_candidate_version_id": self.candidate[0]["version_id"],
            "confirmation_ref": self.ref(confirmation), "confirmation_status": "passed",
        }
        report = deepcopy(report)
        report["run_id"] = self.cycle_id
        results.store(self.output, report)
        self.cycle["report_sha256"] = digest(report)
        self.save_cycle()
        # Only the evidence providers are doubled for local state-machine tests. Real report,
        # evolution, capture, byte hashes, Git identity and local I/O are exercised.
        for module, name, callback in (
            (results, "load_cycles", lambda *a, **kw: {("sample_repo", self.cycle_id): deepcopy(self.cycle)}),
            (results, "load_replays", lambda *a, **kw: deepcopy(self.replays)),
            (skill_assessments, "validate_work_item", lambda data, **kw: data),
        ):
            provider = patch.object(module, name, side_effect=callback, create=True)
            self.providers.append(provider)
            self.enterContext(provider)
        self.enterContext(patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}))
        self.original_bytes = {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()}

    def ref(self, replay):
        return {"project_id": "sample_repo", "run_id": replay["run_id"],
                "path": "replay-evaluation.json", "sha256": digest(replay)}

    def save_cycle(self):
        self.cycle_path = self.output / "sample_repo" / self.cycle_id / "cycle.json"
        results.atomic_json(self.cycle_path, self.cycle)
        self.evidence = sha256(self.cycle_path.read_bytes()).hexdigest()

    def approve(self, **changes):
        args = dict(project_id="sample_repo", skill_key=self.key,
                    candidate_version_id=self.candidate[0]["version_id"], cycle_id=self.cycle_id,
                    evidence_sha256=self.evidence, expected_active_version_id=None,
                    expected_active_execution_sha256=None, results=self.output)
        return self.a.approve(self.root, **{**args, **changes})

    def resolve(self, approval, **changes):
        args = {key: approval[key] for key in
                ("project_id", "skill_key", "approval_id", "candidate_version_id", "evidence_sha256")}
        return self.a.resolve_approved(self.root, results=self.output, **{**args, **changes})

    def receipt(self, approval, **changes):
        row = {
            "schema_version": 1, "execution_id": "execution-fixture",
            "run_id": "local-20260917T130000Z-444444444444", "project_id": "sample_repo",
            "skill_key": self.key, "approval_id": approval["approval_id"], "approval_sha256": digest(approval),
            "evidence_sha256": approval["evidence_sha256"], "environment_id": approval["environment_id"],
            "work_input_sha256": "9" * 64, "approved_version_id": approval["candidate_version_id"],
            "loaded_version_id": approval["candidate_version_id"], "skill_version_verified": True,
            "observed_at": datetime.now(timezone.utc).isoformat(), "status": "verified", "reason_code": None,
            "previous_active_version_id": approval["previous_active_version_id"],
            "previous_active_execution_sha256": approval["previous_active_execution_sha256"],
        }
        return {**row, **changes}

    def active(self):
        return repositories.active_version(self.root, project_id="sample_repo", skill_key=self.key)

    def snapshot(self):
        self.assertTrue(callable(getattr(repositories, "active_snapshot", None)), "Atomic Active snapshot is missing.")
        return repositories.active_snapshot(self.root, project_id="sample_repo", skill_key=self.key)

    def preflight(self, **changes):
        self.assertTrue(callable(getattr(self.a, "preflight", None)), "Read-only approval preflight is missing.")
        args = dict(project_id="sample_repo", skill_key=self.key,
                    candidate_version_id=self.candidate[0]["version_id"], cycle_id=self.cycle_id,
                    evidence_sha256=self.evidence, results=self.output)
        return self.a.preflight(self.root, **{**args, **changes})

    def test_preflight_first_use_shares_evidence_without_writes_or_fsync(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        open_file = os.open
        def guarded_open(path, flags, *args, **kwargs):
            if path != os.devnull:
                self.assertFalse(flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_TRUNC))
            return open_file(path, flags, *args, **kwargs)
        with patch.object(self.a, "_evidence", wraps=self.a._evidence) as evidence, \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch.object(os, "open", side_effect=guarded_open), \
                patch.object(os, "fsync", side_effect=AssertionError("fsync")), \
                patch.object(self.a, "_write", side_effect=AssertionError("write")), \
                patch.object(results, "atomic_json", side_effect=AssertionError("atomic_json")), \
                patch.object(self.a, "approve", side_effect=AssertionError("approve")):
            report = self.preflight()
        self.assertEqual(report["status"], "eligible", report)
        evidence.assert_called_once()
        self.assertEqual(report["active"], {"state": "absent", "version_id": None, "execution_sha256": None})
        self.assertEqual(report["store_state"], "absent")
        self.assertEqual(report["environment_state"], "uninitialized")
        self.assertEqual(report["binding"]["source_commit"], self.commit)
        self.assertFalse((self.root / ".skillops").exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_preflight_distinguishes_store_environment_and_target_absence(self):
        folder = self.root / ".skillops"
        folder.mkdir(mode=0o700)
        self.assertEqual(self.preflight()["status"], "eligible")
        self.assertEqual(list(folder.iterdir()), [])
        repositories.list_repositories(self.root)
        report = self.preflight()
        self.assertEqual(report["status"], "eligible", report)
        self.assertEqual(report["store_state"], "present")
        self.assertEqual(report["environment_state"], "uninitialized")
        self.assertFalse((folder / "environment.json").exists())
        self.approve()
        report = self.preflight()
        self.assertEqual(report["status"], "eligible", report)
        self.assertEqual(report["environment_state"], "initialized")
        self.assertEqual(report["active"]["state"], "absent")
        self.assertFalse((folder / "active.json").exists())

    def test_preflight_present_active_returns_verified_pair_without_durability_calls(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        before = {p: p.read_bytes() for p in (self.root / ".skillops").rglob("*") if p.is_file()}
        with patch.object(os, "fsync", side_effect=AssertionError("fsync")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch.object(self.a, "_write", side_effect=AssertionError("write")):
            report = self.preflight()
        self.assertEqual(report["status"], "eligible", report)
        self.assertEqual(report["active"], {"state": "present", "version_id": approved["candidate_version_id"],
                                           "execution_sha256": digest(receipt)})
        self.assertEqual(before, {p: p.read_bytes() for p in (self.root / ".skillops").rglob("*") if p.is_file()})
        (self.root / ".skillops/executions" / (receipt["execution_id"] + ".json")).unlink()
        report = self.preflight()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["active"], {"state": "unknown"})

    def test_preflight_partial_environment_state_and_symlinks_are_not_absence(self):
        repositories.list_repositories(self.root)
        folder = self.root / ".skillops"
        for name in ("approvals", "executions", "active.json"):
            path = folder / name
            path.symlink_to(folder / "missing")
            with self.subTest(name=name):
                report = self.preflight()
                self.assertEqual(report["status"], "blocked")
                self.assertIn("missing_local_environment", report["blockers"])
                self.assertEqual(report["active"], {"state": "unknown"})
            path.unlink()
        environment = folder / "environment.json"
        environment.symlink_to(folder / "missing")
        self.assertEqual(self.preflight()["status"], "blocked")

    def test_preflight_inaccessible_environment_is_not_first_use(self):
        repositories.list_repositories(self.root)
        environment = self.root / ".skillops/environment.json"
        original = Path.lstat
        def inaccessible(path, *args, **kwargs):
            if path == environment:
                raise PermissionError("Synthetic permission denial")
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", inaccessible):
            report = self.preflight()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["active"], {"state": "unknown"})
        self.assertTrue(report["blockers"])
        self.assertFalse(environment.exists())

    def test_preflight_denies_bad_offline_and_sample_passed_evidence_without_initialization(self):
        for mode in ("offline_test", "sample"):
            self.cycle["execution_mode"] = mode
            self.save_cycle()
            with self.subTest(mode=mode):
                report = self.preflight()
                self.assertEqual(report["status"], "blocked")
                self.assertIn("cycle_not_approvable", report["blockers"])
                self.assertNotIn("approval_argv", report)
        self.cycle["execution_mode"] = "live"
        self.save_cycle()
        for changes in ({"evidence_sha256": "0" * 64},
                        {"candidate_version_id": self.base[0]["version_id"]}):
            self.assertEqual(self.preflight(**changes)["status"], "blocked")
        with patch.object(results, "load_cycles", None):
            report = self.preflight()
        self.assertIn("approval_validation_unavailable", report["blockers"])
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_unknown_identity_and_ci_fail_closed_without_identity_disclosure(self):
        with patch.object(self.a, "_operator", side_effect=KeyError("private-operator-value")):
            report = self.preflight()
        self.assertEqual(report["blockers"], ["local_identity_unavailable"])
        self.assertNotIn("private-operator-value", json.dumps(report))
        with patch.dict(os.environ, {"CI": "true"}):
            report = self.preflight()
        self.assertEqual(report["blockers"], ["local_approval_only"])
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_blocks_observed_store_and_active_changes(self):
        validate = self.a._evidence
        def initialize_during_evidence(*args, **kwargs):
            result = validate(*args, **kwargs)
            repositories.list_repositories(self.root)
            return result
        with patch.object(self.a, "_evidence", side_effect=initialize_during_evidence):
            report = self.preflight()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("registry_changed", report["blockers"])
        approved = self.approve()
        receipt = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        def remove_pointer(*args, **kwargs):
            result = validate(*args, **kwargs)
            (self.root / ".skillops/active.json").unlink()
            return result
        with patch.object(self.a, "_evidence", side_effect=remove_pointer):
            report = self.preflight()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("active_conflict", report["blockers"])
        self.assertEqual(report["active"], {"state": "unknown"})

    def test_preflight_is_not_reservation_and_approve_revalidates_evidence_and_pair(self):
        observation = self.preflight()
        self.assertEqual(observation["status"], "eligible")
        original = self.cycle_path.read_bytes()
        self.cycle_path.write_bytes(original + b"\n")
        with self.assertRaises(RuntimeFailure) as caught:
            self.approve()
        self.assertEqual(caught.exception.code, "approval_evidence_changed")
        self.cycle_path.write_bytes(original)
        approved = self.approve()
        receipt = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        with self.assertRaises(RuntimeFailure) as caught:
            self.approve()
        self.assertEqual(caught.exception.code, "active_conflict")
        observation = self.preflight()
        prior = observation["active"]
        second = self.approve(expected_active_version_id=prior["version_id"],
                              expected_active_execution_sha256=prior["execution_sha256"])
        second_receipt = self.receipt(second, execution_id="execution-second",
                                     run_id="local-20260917T140000Z-555555555555")
        self.a.record_execution(self.root, approval=second, receipt=second_receipt)
        with self.assertRaises(RuntimeFailure) as caught:
            self.approve(expected_active_version_id=prior["version_id"],
                         expected_active_execution_sha256=prior["execution_sha256"])
        self.assertEqual(caught.exception.code, "active_conflict")

    def test_preflight_rejects_source_evaluator_private_work_and_malformed_evidence(self):
        work = self.work_items[0]
        paths = (self.project / "app.py", self.root / "eval/skill-guide-rubric.json",
                 self.root / ".skillops-private/work-items" / work["task_id"] / (work["input_sha256"] + ".json"))
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.subTest(path=path.name):
                report = self.preflight()
                self.assertEqual(report["status"], "blocked")
                self.assertTrue(report["blockers"])
                self.assertEqual(report["active"], {"state": "unknown"})
            path.write_bytes(original)
        self.cycle_path.write_bytes(b"not json")
        self.assertEqual(self.preflight()["status"], "blocked")
        self.assertFalse((self.root / ".skillops").exists())

    def test_preflight_busy_lock_and_unsafe_environment_are_blocked_without_changes(self):
        import fcntl
        self.approve()
        folder = self.root / ".skillops"
        with (folder / "lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = self.preflight()
        self.assertEqual(report["blockers"], ["registry_busy"])
        environment = folder / "environment.json"
        environment.chmod(0o644)
        report = self.preflight()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("unsafe_approval_store", report["blockers"])
        self.assertFalse((folder / "active.json").exists())

    def test_preflight_detects_environment_and_operator_changes_during_evidence(self):
        self.approve()
        validate = self.a._evidence
        path = self.root / ".skillops/environment.json"
        def change_environment(*args, **kwargs):
            result = validate(*args, **kwargs)
            value = results.read_json(path)
            value["environment_id"] = "env-changed"
            path.write_bytes(results.encoded(value))
            return result
        with patch.object(self.a, "_evidence", side_effect=change_environment):
            report = self.preflight()
        self.assertEqual(report["blockers"], ["local_environment_mismatch"])
        with patch.object(self.a, "_operator", side_effect=["first-private-value", "second-private-value"]):
            report = self.preflight()
        self.assertEqual(report["blockers"], ["local_identity_changed"])
        self.assertNotIn("private-value", json.dumps(report))

    def test_readonly_environment_cannot_opt_into_initialization(self):
        with self.assertRaises(RuntimeFailure) as caught:
            self.a._environment(self.root / ".skillops", create=True, durable=False)
        self.assertEqual(caught.exception.code, "readonly_approval_store")
        self.assertFalse((self.root / ".skillops").exists())

    def test_atomic_active_snapshot_and_required_hash_signature(self):
        import inspect
        self.assertIn("expected_active_execution_sha256", inspect.signature(self.a.approve).parameters)
        self.assertEqual(self.snapshot(), {"version_id": None, "execution_sha256": None})
        approved = self.approve()
        self.assertIsNone(approved["previous_active_execution_sha256"])
        receipt = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        self.assertEqual(self.snapshot(), {"version_id": approved["candidate_version_id"],
                                          "execution_sha256": digest(receipt)})

    def test_snapshot_cannot_pair_changed_receipt_bytes_with_an_old_hash(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        path = self.root / ".skillops/executions" / (receipt["execution_id"] + ".json")
        sync = self.a._fsync_directory
        directory_reads = 0
        def change_after_validation(folder):
            nonlocal directory_reads
            sync(folder)
            if Path(folder) == self.root / ".skillops":
                directory_reads += 1
                if directory_reads == 2:
                    changed = {**receipt, "loaded_version_id": self.base[0]["version_id"]}
                    path.write_bytes(results.encoded(changed))
        with patch.object(self.a, "_fsync_directory", side_effect=change_after_validation):
            with self.assertRaises(RuntimeFailure):
                self.snapshot()

    def test_approval_and_resolution_do_not_activate_or_modify_sources(self):
        self.assertIsNone(self.active())
        approved = self.approve()
        self.assertEqual(self.resolve(approved), (approved, self.candidate))
        self.assertIsNone(self.active())
        self.assertFalse((self.root / ".skillops/executions").exists())
        self.assertEqual(self.original_bytes, {p: p.read_bytes() for p in self.original_bytes})
        self.assertFalse((self.root / ".skillops/registry.json").exists())

    def test_identical_approval_is_immutable_and_idempotent(self):
        approved = self.approve()
        self.assertEqual(self.approve(), approved)
        path = self.root / ".skillops/approvals" / (approved["approval_id"] + ".json")
        self.assertEqual(path.read_bytes(), results.encoded(approved))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(path.parent.glob("*.json"))), 1)

    def test_missing_common_provider_fails_closed(self):
        with patch.object(results, "load_cycles", None):
            with self.assertRaises(RuntimeFailure) as caught:
                self.approve()
        self.assertEqual(caught.exception.code, "approval_validation_unavailable")
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

    def test_unapproved_and_public_approval_cannot_resolve(self):
        with self.assertRaises(RuntimeFailure):
            self.resolve(dict(project_id="sample_repo", skill_key=self.key, approval_id="made-up",
                              candidate_version_id=self.candidate[0]["version_id"], evidence_sha256=self.evidence))
        with self.assertRaises(RuntimeFailure):
            self.a.record_execution(self.root, approval={"approved": True}, receipt={})

    def test_ci_cannot_create_or_consume_approval(self):
        approved = self.approve()
        for name in ("CI", "GITHUB_ACTIONS"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "true"}):
                for action in (self.approve, lambda: self.resolve(approved),
                               lambda: self.a.record_execution(self.root, approval=approved,
                                                               receipt=self.receipt(approved))):
                    with self.assertRaises(RuntimeFailure):
                        action()
        self.assertIsNone(self.active())

    def test_stale_active_and_wrong_selection_are_rejected(self):
        with self.assertRaises(RuntimeFailure):
            self.approve(expected_active_version_id=self.base[0]["version_id"])
        approved = self.approve()
        for changes in ({"project_id": "other"}, {"skill_key": "other:skill"},
                        {"candidate_version_id": self.base[0]["version_id"]},
                        {"evidence_sha256": "0" * 64}, {"approval_id": "../escape"}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeFailure):
                self.resolve(approved, **changes)

    def test_changed_cycle_source_private_work_and_evaluator_are_rejected(self):
        approved = self.approve()
        work = self.work_items[0]
        work_path = self.root / ".skillops-private/work-items" / work["task_id"] / (work["input_sha256"] + ".json")
        for path in (self.cycle_path, self.project / "app.py", self.project / self.source_path / "reference.txt",
                     work_path, self.root / "eval/skill-guide-rubric.json"):
            original = path.read_bytes()
            with self.subTest(path=path.name):
                path.write_bytes(original + b"\n")
                with self.assertRaises(RuntimeFailure):
                    self.resolve(approved)
                path.write_bytes(original)

    def test_nonlive_or_unconfirmed_cycle_is_not_approvable(self):
        original = deepcopy(self.cycle)
        for changes in ({"execution_mode": "sample"}, {"execution_mode": "offline_test"},
                        {"confirmation_status": "not_run"}, {"confirmation_status": "failed"},
                        {"selected_candidate_version_id": None}, {"confirmation_ref": None}):
            self.cycle = {**deepcopy(original), **changes}
            self.save_cycle()
            with self.subTest(changes=changes), self.assertRaises(RuntimeFailure):
                self.approve()

    def test_changed_reference_bytes_and_incomplete_bundle_are_rejected(self):
        approved = self.approve()
        replay = next(iter(self.replays.values()))
        for filename in ("replay-evaluation.json", "report.json", "skill-evolution.json"):
            path = self.output / "sample_repo" / replay["run_id"] / filename
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.subTest(filename=filename), self.assertRaises(RuntimeFailure):
                self.resolve(approved)
            path.write_bytes(original)
        path = self.output / "sample_repo" / replay["run_id"] / "skill-evolution.json"
        data = results.read_json(path, results.EVOLUTION_LIMIT)
        data["file_contents"].pop()
        path.write_bytes(results.encoded(data))
        with self.assertRaises(RuntimeFailure):
            self.resolve(approved)

    def test_environment_copy_and_changed_identity_do_not_transfer_authority(self):
        approved = self.approve()
        other = self.root / "copied"
        shutil.copytree(self.root / ".skillops", other / ".skillops")
        with self.assertRaises(RuntimeFailure):
            self.a.resolve_approved(other, project_id="sample_repo", skill_key=self.key,
                                    approval_id=approved["approval_id"],
                                    candidate_version_id=approved["candidate_version_id"],
                                    evidence_sha256=approved["evidence_sha256"], results=self.output)
        path = self.root / ".skillops/environment.json"
        data = results.read_json(path)
        data["environment_id"] = "different-environment"
        path.write_bytes(results.encoded(data))
        with self.assertRaises(RuntimeFailure):
            self.resolve(approved)

    def test_verified_use_persists_before_active_and_repeats_idempotently(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        self.assertEqual(self.a.record_execution(self.root, approval=approved, receipt=receipt), receipt)
        self.assertEqual(self.active(), self.candidate[0]["version_id"])
        self.assertEqual(self.a.record_execution(self.root, approval=approved, receipt=receipt), receipt)
        self.assertEqual(self.original_bytes, {p: p.read_bytes() for p in self.original_bytes})
        with self.assertRaises(RuntimeFailure):
            self.approve()
        with self.assertRaises(RuntimeFailure):
            self.resolve(approved)

    def test_missing_activation_or_binding_mismatch_cannot_activate(self):
        approved = self.approve()
        for changes in ({"skill_version_verified": False}, {"skill_version_verified": 1},
                        {"loaded_version_id": None}, {"loaded_version_id": self.base[0]["version_id"]},
                        {"approval_sha256": "0" * 64}, {"evidence_sha256": "0" * 64},
                        {"environment_id": "another"}, {"project_id": "another"},
                        {"approved_version_id": self.base[0]["version_id"]},
                        {"previous_active_version_id": self.base[0]["version_id"]},
                        {"observed_at": None}, {"run_id": self.cycle_id}, {"approved": True}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved, **changes))
        self.assertIsNone(self.active())
        self.assertFalse(list((self.root / ".skillops/executions").glob("*.json")))

    def test_failed_use_is_recorded_but_never_active(self):
        approved = self.approve()
        receipt = self.receipt(approved, status="failed", skill_version_verified=False,
                               loaded_version_id=None, reason_code="activation_missing")
        self.assertEqual(self.a.record_execution(self.root, approval=approved, receipt=receipt), receipt)
        self.assertIsNone(self.active())

    def test_failed_receipt_retry_cannot_claim_a_recorded_active_selection(self):
        approved = self.approve()
        receipt = self.receipt(approved, status="failed", skill_version_verified=False,
                               loaded_version_id=None, reason_code="activation_missing")
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        with self.assertRaises(RuntimeFailure) as caught:
            self.a.record_execution(self.root, approval=approved, receipt=receipt)
        self.assertEqual(caught.exception.code, "active_conflict")
        self.assertIsNone(self.active())

    def test_changed_cycle_report_bytes_block_approval(self):
        path = self.cycle_path.parent / "report.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(RuntimeFailure):
            self.approve()

    def test_previously_checked_evidence_drift_during_final_work_validation_blocks(self):
        first = next(iter(self.replays.values()))
        path = self.output / "sample_repo" / first["run_id"] / "replay-evaluation.json"
        def drift(data, **kwargs):
            if data["split"] == "confirmation":
                path.write_bytes(path.read_bytes() + b"\n")
            return data
        with patch.object(skill_assessments, "validate_work_item", side_effect=drift):
            with self.assertRaises(RuntimeFailure):
                self.approve()
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

    def test_new_private_directory_parent_is_retried_after_failed_durability(self):
        approved = self.approve()
        sync = self.a._fsync_directory
        failed = False
        def fail_after_directory_creation(path):
            nonlocal failed
            if Path(path) == self.root / ".skillops" and (Path(path) / "executions").is_dir() and not failed:
                failed = True
                raise OSError("Synthetic parent-directory durability failure")
            sync(path)
        with patch.object(self.a, "_fsync_directory", side_effect=fail_after_directory_creation):
            with self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))
        self.assertIsNone(self.active())
        self.assertFalse((self.root / ".skillops/executions/execution-fixture.json").exists())
        self.assertEqual(self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))["status"],
                         "verified")

    def test_corrupt_previous_execution_is_an_explicit_error_not_an_unchecked_lookup(self):
        approved = self.approve()
        folder = self.root / ".skillops/executions"
        folder.mkdir(mode=0o700)
        path = folder / "unknown-record.json"
        results.atomic_json(path, {})
        before = path.read_bytes()
        with self.assertRaises(RuntimeFailure):
            self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((folder / "execution-fixture.json").exists())
        self.assertIsNone(self.active())

    def test_receipt_and_pointer_write_failures_do_not_claim_success(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        atomic = results.atomic_json
        for name in ("execution-fixture.json", "active.json"):
            def fail(path, *args, **kwargs):
                if Path(path).name == name:
                    raise OSError("Synthetic persistence failure")
                return atomic(path, *args, **kwargs)
            with self.subTest(name=name), patch.object(results, "atomic_json", side_effect=fail):
                with self.assertRaises(RuntimeFailure):
                    self.a.record_execution(self.root, approval=approved, receipt=receipt)
            self.assertIsNone(self.active())
        # An orphan verified receipt is evidence only, never a retry/recovery rule.
        with self.assertRaises(RuntimeFailure):
            self.a.record_execution(self.root, approval=approved, receipt=receipt)
        self.assertIsNone(self.active())

    def test_post_replace_fsync_failure_cannot_be_acknowledged_by_retry(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        sync = self.a._fsync_directory
        def fail(path):
            if Path(path) == self.root / ".skillops" and (Path(path) / "active.json").exists():
                raise OSError("Synthetic directory fsync failure after replace")
            sync(path)
        with patch.object(self.a, "_fsync_directory", side_effect=fail):
            with self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=receipt)
            self.assertTrue((self.root / ".skillops/active.json").is_file())
            with self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=receipt)
            with self.assertRaises(RuntimeFailure):
                self.active()
        # A retry may acknowledge an existing matching pointer only after syncing it.
        self.assertEqual(self.a.record_execution(self.root, approval=approved, receipt=receipt), receipt)

    def test_environment_change_during_evidence_validation_blocks_approval_and_resolution(self):
        environment = self.root / ".skillops/environment.json"
        changes = 0
        def change_environment(*args, **kwargs):
            nonlocal changes
            changes += 1
            data = results.read_json(environment)
            data["environment_id"] = f"replaced-environment-{changes}"
            environment.write_bytes(results.encoded(data))
            return deepcopy(self.replays)
        with patch.object(results, "load_replays", side_effect=change_environment):
            with self.assertRaises(RuntimeFailure):
                self.approve()
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))
        approved = self.approve()
        with patch.object(results, "load_replays", side_effect=change_environment):
            with self.assertRaises(RuntimeFailure):
                self.resolve(approved)

    def test_environment_removed_after_receipt_blocks_pointer_write(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        sync = self.a._fsync_directory
        def remove_environment(path):
            sync(path)
            if Path(path) == self.root / ".skillops/executions":
                (self.root / ".skillops/environment.json").unlink(missing_ok=True)
        with patch.object(self.a, "_fsync_directory", side_effect=remove_environment):
            with self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=receipt)
        self.assertTrue((self.root / ".skillops/executions/execution-fixture.json").exists())
        self.assertFalse((self.root / ".skillops/active.json").exists())

    def test_same_version_different_receipt_conflicts_at_all_three_boundaries(self):
        approved = self.approve()
        first = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=first)
        args = {"expected_active_version_id": approved["candidate_version_id"],
                "expected_active_execution_sha256": digest(first)}
        pending = self.approve(**args)
        self.assertEqual(self.approve(**args), pending)
        self.assertEqual(self.resolve(pending)[0], pending)
        second = self.receipt(pending, execution_id="second-use",
                              run_id="local-20260917T140000Z-555555555555")
        self.a.record_execution(self.root, approval=pending, receipt=second)
        current = self.snapshot()
        self.assertEqual(current, {"version_id": approved["candidate_version_id"], "execution_sha256": digest(second)})
        third = self.receipt(pending, execution_id="third-use",
                             run_id="local-20260917T150000Z-666666666666")
        for action in (lambda: self.approve(**args), lambda: self.resolve(pending),
                       lambda: self.a.record_execution(self.root, approval=pending, receipt=third),
                       lambda: self.a.record_execution(self.root, approval=approved, receipt=first)):
            with self.assertRaises(RuntimeFailure) as caught:
                action()
            self.assertEqual(caught.exception.code, "active_conflict")
            self.assertEqual(self.snapshot(), current)
        self.assertEqual(self.a.record_execution(self.root, approval=pending, receipt=second), second)
        self.assertFalse((self.root / ".skillops/executions/third-use.json").exists())

    def synthetic_pending(self, template, version):
        """Seed only a CAS-unit-test private approval, not evaluated/live authority."""
        current = self.snapshot()
        binding = {key: value for key, value in template.items() if key not in ("approval_id", "approved_at")}
        binding.update(candidate_version_id=version, previous_active_version_id=current["version_id"],
                       previous_active_execution_sha256=current["execution_sha256"])
        pending = {**binding, "approval_id": "approval-" + digest(binding)[:48],
                   "approved_at": datetime.now(timezone.utc).isoformat()}
        results.atomic_json(self.root / ".skillops/approvals" / (pending["approval_id"] + ".json"), pending)
        return pending

    def test_aba_change_cannot_reuse_an_unused_pending_approval(self):
        approved = self.approve()
        first = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=first)
        observed = self.preflight()["active"]
        pending = self.approve(expected_active_version_id=approved["candidate_version_id"],
                               expected_active_execution_sha256=digest(first))
        other = self.synthetic_pending(approved, self.base[0]["version_id"])
        self.a.record_execution(self.root, approval=other, receipt=self.receipt(
            other, execution_id="aba-b", run_id="local-20260917T160000Z-777777777777"))
        back = self.synthetic_pending(approved, approved["candidate_version_id"])
        last = self.receipt(back, execution_id="aba-a", run_id="local-20260917T170000Z-888888888888")
        self.a.record_execution(self.root, approval=back, receipt=last)
        self.assertEqual(self.active(), approved["candidate_version_id"])
        self.assertNotEqual(self.snapshot()["execution_sha256"], digest(first))
        for action in (lambda: self.approve(expected_active_version_id=observed["version_id"],
                                           expected_active_execution_sha256=observed["execution_sha256"]),
                       lambda: self.resolve(pending),
                       lambda: self.a.record_execution(self.root, approval=pending, receipt=self.receipt(
                           pending, execution_id="stale-aba", run_id="local-20260917T180000Z-999999999999"))):
            with self.assertRaises(RuntimeFailure) as caught:
                action()
            self.assertEqual(caught.exception.code, "active_conflict")
        self.assertEqual(self.snapshot()["execution_sha256"], digest(last))

    def test_half_null_invalid_and_missing_predecessor_hashes_are_rejected(self):
        for version, value in ((None, "0" * 64), (self.base[0]["version_id"], None),
                               (self.base[0]["version_id"], True), (self.base[0]["version_id"], "bad")):
            with self.subTest(version=version, digest=value), self.assertRaises(RuntimeFailure):
                self.approve(expected_active_version_id=version, expected_active_execution_sha256=value)
        approved = self.approve()
        receipt = self.receipt(approved, previous_active_execution_sha256="0" * 64)
        with self.assertRaises(RuntimeFailure):
            self.a.record_execution(self.root, approval=approved, receipt=receipt)
        receipt.pop("previous_active_execution_sha256")
        with self.assertRaises(RuntimeFailure):
            self.a.record_execution(self.root, approval=approved, receipt=receipt)
        path = self.root / ".skillops/approvals" / (approved["approval_id"] + ".json")
        old = dict(approved)
        old.pop("previous_active_execution_sha256")
        path.write_bytes(results.encoded(old))
        with self.assertRaises(RuntimeFailure):
            self.resolve(approved)

    def test_receipt_directory_sync_failure_leaves_active_unset(self):
        approved = self.approve()
        sync = self.a._fsync_directory
        def fail(path):
            if Path(path) == self.root / ".skillops/executions":
                raise OSError("Synthetic receipt durability failure")
            sync(path)
        with patch.object(self.a, "_fsync_directory", side_effect=fail):
            with self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))
        self.assertIsNone(self.active())

    def test_actual_pointer_replace_failure_preserves_originals(self):
        approved = self.approve()
        replace = os.replace
        def fail(source, target):
            if Path(target) == self.root / ".skillops/active.json":
                raise OSError("Synthetic pointer replacement failure")
            return replace(source, target)
        with patch("project_results.os.replace", side_effect=fail):
            with self.assertRaises(RuntimeFailure):
                self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))
        self.assertIsNone(self.active())
        self.assertEqual(self.original_bytes, {path: path.read_bytes() for path in self.original_bytes})

    def test_approval_storage_failure_never_returns_an_approval(self):
        atomic = results.atomic_json
        def fail(path, *args, **kwargs):
            if Path(path).parent.name == "approvals":
                raise OSError("Synthetic approval storage failure")
            return atomic(path, *args, **kwargs)
        with patch.object(results, "atomic_json", side_effect=fail):
            with self.assertRaises(RuntimeFailure):
                self.approve()
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))
        self.assertIsNone(self.active())

    def test_environment_sync_failure_does_not_become_success_on_retries(self):
        sync = self.a._fsync_directory
        def fail(path):
            if Path(path) == self.root / ".skillops":
                raise OSError("Synthetic environment durability failure")
            sync(path)
        with patch.object(self.a, "_fsync_directory", side_effect=fail):
            for attempt in range(3):
                with self.subTest(attempt=attempt), self.assertRaises(RuntimeFailure):
                    self.approve()

    def test_active_reader_rejects_modified_or_missing_receipt(self):
        approved = self.approve()
        receipt = self.receipt(approved)
        self.a.record_execution(self.root, approval=approved, receipt=receipt)
        path = self.root / ".skillops/executions/execution-fixture.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(RuntimeFailure):
            self.active()
        path.unlink()
        with self.assertRaises(RuntimeFailure):
            self.active()

    def test_competing_execution_cannot_overwrite_active(self):
        approved = self.approve()
        self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))
        competing = self.receipt(approved, execution_id="another-execution",
                                 run_id="local-20260917T140000Z-555555555555")
        with self.assertRaises(RuntimeFailure):
            self.a.record_execution(self.root, approval=approved, receipt=competing)
        self.assertFalse((self.root / ".skillops/executions/another-execution.json").exists())
        self.assertEqual(self.active(), approved["candidate_version_id"])

    def test_registry_lock_blocks_approval_resolution_and_use(self):
        approved = self.approve()
        with repositories._state(self.root):
            for action in (self.approve, lambda: self.resolve(approved),
                           lambda: self.a.record_execution(self.root, approval=approved,
                                                           receipt=self.receipt(approved))):
                with self.assertRaises(RuntimeFailure) as caught:
                    action()
                self.assertEqual(caught.exception.code, "registry_busy")

    def test_private_store_rejects_symlinks_and_world_readable_files(self):
        approved = self.approve()
        path = self.root / ".skillops/approvals" / (approved["approval_id"] + ".json")
        path.chmod(0o644)
        with self.assertRaises(RuntimeFailure):
            self.resolve(approved)
        path.chmod(0o600)
        path.unlink()
        path.symlink_to(self.cycle_path)
        with self.assertRaises(RuntimeFailure):
            self.resolve(approved)


class ApprovalIntegrationTests(unittest.TestCase):
    """Real validators/loaders/provider; only model and container transports are synthetic."""

    def setUp(self):
        from hackathon_fixtures import fixture as replay_fixture
        import skill_approvals
        self.a = skill_approvals
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "projects").mkdir()
        self.data = replay_fixture(self.root / "projects")
        self.project = self.data["project"]
        self.key = "skillops:develop"
        self.output = self.root / "results"
        self.enterContext(patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}))

    def approve_cycle(self, cycle):
        path = self.output / cycle["project_id"] / cycle["cycle_id"] / "cycle.json"
        return self.a.approve(
            self.root, project_id=cycle["project_id"], skill_key=cycle["skill_key"],
            candidate_version_id=cycle["selected_candidate_version_id"], cycle_id=cycle["cycle_id"],
            evidence_sha256=sha256(path.read_bytes()).hexdigest(),
            expected_active_version_id=None, expected_active_execution_sha256=None, results=self.output)

    def development_results(self):
        from hackathon_fixtures import write_results
        self.data["evaluations"].pop(("sample_repo", "103-1"))
        self.data["cycle"].update(confirmation_ref=None, confirmation_status="unverified")
        write_results(self.output, self.data)
        return self.data["cycle"]

    def synthetic_live_contract(self):
        """Construct NEW synthetic live-shaped inputs for validator branch coverage.

        No provider output is relabeled, no live execution occurs, and nothing
        leaves this temporary test repository. Confirmation is fabricated test
        input, not proof that the actual provider supports confirmation.
        """
        (self.root / "eval").mkdir()
        results.atomic_json(self.root / "eval/skill-guide-rubric.json", self.data["rubric"])
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "projects", "eval"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Synthetic contract test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Synthetic authorization-contract fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        works = {}
        self.private_paths = []
        for original in (self.data["work_item"], self.data["confirmation_work_item"]):
            work = {**deepcopy(original), "source_commit": commit}
            work["input_sha256"] = digest({k: v for k, v in work.items() if k != "input_sha256"})
            skill_assessments.validate_work_item(work, project=self.project, source_commit=commit)
            path = self.root / ".skillops-private/work-items" / work["task_id"] / (work["input_sha256"] + ".json")
            results.atomic_json(path, work, immutable=True)
            self.private_paths.append(path)
            works[work["split"]] = work
        refs, replays, previous = {}, {}, None
        captures = {version["version_id"]: (version, files) for version, files in self.data["captures"]}
        for key, original in self.data["evaluations"].items():
            replay = deepcopy(original["replay"])
            row, reference = replay["evaluation"], replay["reference"]
            work = works[row["work"]["split"]]
            reference.update(source_commit=commit, input_sha256=work["input_sha256"],
                             evaluator_sha256=results.evaluator_hash(self.root))
            reference["reference_sha256"] = digest({k: v for k, v in reference.items() if k != "reference_sha256"})
            row["reference_sha256"] = reference["reference_sha256"]
            row["work"].update({k: work[k] for k in ("task_id", "input_sha256", "split", "checks")})
            for application in row["applications"].values():
                application["work_sha256"] = work["input_sha256"]
            generation = replay["generation"]
            if generation is not None:
                source = previous["evaluation"] if previous else row
                arm = "candidate" if previous else "base"
                checks = source["checks"]["candidate" if previous else "original"]
                packet = {
                    "schema_version": 1, "input_sha256": work["input_sha256"],
                    "source_round_id": "100-1-r1" if previous else None,
                    "quality": source["quality"][arm],
                    "checks": {"cases": [case for case in checks["cases"]
                                        if case["id"] in work["checks"]["required_case_ids"]],
                               "gates": checks["gates"]},
                    "application": source["applications"]["candidate"] if previous else None,
                    "decision": source["decision"] if previous else None,
                }
                generation["feedback_sha256"] = digest(packet)
                generation["hypothesis"] = "Synthetic contract input only, not a model observation."
            selected = [captures[row[f"{arm}_version_id"]] for arm in ("base", "candidate")]
            refs[key[1]] = project_evaluation.persist_replay(
                self.output, row, selected, generation, reference, execution_mode="live", run_id=key[1])
            replays[key[1]] = replay
            previous = replay
        cycle = deepcopy(self.data["cycle"])
        cycle.pop("report_sha256")
        cycle.update(execution_mode="live", input_sha256=works["development"]["input_sha256"],
                     reference_sha256=replays["101-1"]["reference"]["reference_sha256"],
                     confirmation_ref=refs["103-1"])
        for row in cycle["rounds"]:
            replay = replays[row["run_id"]]
            row.update(input_sha256=cycle["input_sha256"], reference_sha256=cycle["reference_sha256"],
                       feedback_sha256=replay["generation"]["feedback_sha256"],
                       evaluation_ref=refs[row["run_id"]])
        project_evaluation.persist_cycle(self.output, cycle, replays["101-1"]["reference"])
        return results.load_cycles(self.output)[("sample_repo", cycle["cycle_id"])]

    def resolve_cycle(self, approved):
        return self.a.resolve_approved(self.root, results=self.output, **{
            key: approved[key] for key in ("project_id", "skill_key", "approval_id",
                                           "candidate_version_id", "evidence_sha256")})

    def test_synthetic_positive_approval_through_actual_common_storage_and_validation(self):
        cycle = self.synthetic_live_contract()
        original = results.tree_hash(self.project)
        evidence = {p: p.read_bytes() for p in self.output.rglob("*.json")}
        approval = self.approve_cycle(cycle)
        self.assertEqual(self.approve_cycle(cycle), approval)
        loaded, candidate = self.resolve_cycle(approval)
        self.assertEqual(loaded, approval)
        self.assertEqual(candidate, self.data["captures"][2])
        self.assertEqual(candidate[0]["capture_scope"], "complete_bundle")
        self.assertIsNone(repositories.active_version(self.root, project_id="sample_repo", skill_key="skillops:develop"))
        receipt = ApprovalTests.receipt(self, approval)
        self.assertEqual(self.a.record_execution(self.root, approval=approval, receipt=receipt), receipt)
        self.assertEqual(repositories.active_snapshot(self.root, project_id="sample_repo", skill_key="skillops:develop"),
                         {"version_id": candidate[0]["version_id"], "execution_sha256": digest(receipt)})
        self.assertEqual(results.tree_hash(self.project), original)
        self.assertEqual(evidence, {p: p.read_bytes() for p in evidence})
        self.assertFalse((self.root / ".skillops/registry.json").exists())
        self.assertFalse(list(self.output.rglob("adoption.json")))

    def test_real_validators_reject_malformed_or_missing_private_work(self):
        cycle = self.synthetic_live_contract()
        for path in self.private_paths:
            original = path.read_bytes()
            for invalid in ({}, {"input_sha256": "0" * 64}, None):
                with self.subTest(split=path.parent.name, invalid=invalid):
                    if invalid is None:
                        path.unlink()
                    else:
                        path.write_bytes(results.encoded(invalid))
                    with self.assertRaises(RuntimeFailure):
                        self.approve_cycle(cycle)
                    path.write_bytes(original)
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

    def test_full_graph_and_private_work_mutations_reject_resolved_synthetic_approval(self):
        cycle = self.synthetic_live_contract()
        approval = self.approve_cycle(cycle)
        candidates = [
            *(self.output / "sample_repo" / run / filename
              for run in ("101-1", "102-1", "103-1")
              for filename in ("report.json", "skill-evolution.json", "replay-evaluation.json")),
            self.output / "sample_repo" / cycle["cycle_id"] / "report.json",
            self.output / "sample_repo" / cycle["cycle_id"] / "cycle.json",
            *self.private_paths, self.project / "skills/develop/notes.txt", self.project / "app.py",
            self.root / "eval/skill-guide-rubric.json",
        ]
        for path in candidates:
            original = path.read_bytes()
            with self.subTest(path=path.relative_to(self.root)):
                path.write_bytes(original + b"\n")
                with self.assertRaises(RuntimeFailure):
                    self.resolve_cycle(approval)
                path.write_bytes(original)
        self.assertIsNone(repositories.active_version(self.root, project_id="sample_repo", skill_key="skillops:develop"))

    def test_real_common_loaders_keep_development_evidence_unapprovable(self):
        cycle = self.development_results()
        self.assertEqual(len(results.load_replays(self.output)), 2)
        self.assertEqual(results.load_cycles(self.output)[("sample_repo", cycle["cycle_id"])], cycle)
        self.assertEqual(skill_assessments.validate_work_item(
            self.data["work_item"], project=self.project, source_commit=self.data["work_item"]["source_commit"]),
            self.data["work_item"])
        with self.assertRaises(RuntimeFailure) as caught:
            self.approve_cycle(cycle)
        self.assertEqual(caught.exception.code, "cycle_not_approvable")
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))
        self.assertEqual(repositories.active_snapshot(self.root, project_id="sample_repo", skill_key="skillops:develop"),
                         {"version_id": None, "execution_sha256": None})

    def test_real_common_loader_rejects_forged_confirmation_without_an_artifact(self):
        cycle = self.development_results()
        cycle["confirmation_status"] = "passed"
        path = self.output / "sample_repo" / cycle["cycle_id"] / "cycle.json"
        path.write_bytes(results.encoded(cycle))
        for action in (lambda: results.load_cycles(self.output), lambda: self.approve_cycle(cycle)):
            with self.assertRaises(RuntimeFailure) as caught:
                action()
            self.assertEqual(caught.exception.code, "cycle_confirmation_mismatch")
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))

    def test_real_replay_provider_loaders_and_approval_preserve_confirmation_block(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "projects"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Offline integration fixture"], check=True)
        work = deepcopy(self.data["work_item"])
        work["source_commit"] = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        work["input_sha256"] = digest({k: v for k, v in work.items() if k != "input_sha256"})
        skill_assessments.validate_work_item(work, project=self.project, source_commit=work["source_commit"])
        runtime = Mock(project=self.root, cli="/offline/copilot", env={}, execution_mode="offline_test")
        runtime.private = self.root / ".skillops-private"
        runtime.private.mkdir(mode=0o700)
        work_path = runtime.private / "work-items" / work["task_id"] / (work["input_sha256"] + ".json")
        results.atomic_json(work_path, work, immutable=True)
        quality_calls = []

        def quality(*args):
            quality_calls.append(args)
            return {"status": "pass", "static": {"findings": []},
                    "judge": {"dimensions": {"clarity": {"score": 2 if len(quality_calls) == 1 else 3}}}}

        def check(project, plan, images, **kwargs):
            return {
                "plan_sha256": plan["sha256"], "environment_sha256": project_checks.digest(images),
                "protected_sha256": project_checks.protected_digest(project, project_checks.protected_files(project)),
                "status": "completed", "cases": [
                    {"id": name, "status": "passed"} for name in ("confirmation", "development", "stable")],
                "gates": [], "elapsed_seconds": 1,
            }

        def invoke(prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
            self.assertNotIn("def test_confirmation", prompt)
            if role == "generator":
                return {"content": json.dumps({
                    "instructions": "Check changes and boundaries.", "addressed_findings": ["clarity"],
                    "hypothesis": "Offline transport fixture, not measured improvement.",
                })}
            from copilot_runtime import verify_staged_version
            self.assertEqual(role, "developer")
            self.assertIn(work["request"], prompt)
            version = verify_staged_version(expected_skill, kwargs["expected_version"])
            return {"content": json.dumps({"files": {"app.py": "value = 1\n"}}),
                    "skill_version_verified": True, "skill_activated": True, "staged_version_id": version,
                    "elapsed_seconds": 2, "usage": {"nano_aiu": {"value": 123}}}

        runtime.invoke.side_effect = invoke
        bundle = skill_guide.discover(self.project)[0]
        cycle_id = "local-20260917T200000Z-aaaaaaaaaaaa"
        run_id = "local-20260917T200001Z-bbbbbbbbbbbb"
        before = results.tree_hash(self.project)
        # No shared validation, decision, feedback, loading or Git function is patched.
        with patch.object(skill_guide, "evaluate_bundle", side_effect=quality), patch.object(
                project_checks, "execute", side_effect=check):
            context = skill_pipeline.prepare_replay(
                runtime, "offline-model", self.project, bundle, "skillops:develop",
                self.data["rubric"], self.data["images"], runtime.private / "prepare",
                work_item=work, deadline=9999999999)
            generation, candidate = skill_pipeline.generate_candidate(
                runtime, "offline-model", context["original"], context["feedback"],
                runtime.private / "generate", deadline=9999999999)
            row, captures = skill_pipeline.evaluate_candidate(
                runtime, "offline-model", context, candidate, runtime.private / "evaluate", deadline=9999999999)
            row_bytes = results.encoded(row)
            feedback = skill_pipeline.development_feedback(
                runtime, context, row, source_round_id=cycle_id + "-r1")
            self.assertEqual(results.encoded(row), row_bytes)
            self.assertEqual(feedback["source_round_id"], cycle_id + "-r1")
            confirmation = deepcopy(work)
            confirmation.update(task_id="confirmation-task", split="confirmation", request="Confirm a distinct task.")
            confirmation["checks"]["required_case_ids"] = ["confirmation"]
            confirmation["input_sha256"] = digest({k: v for k, v in confirmation.items() if k != "input_sha256"})
            calls = runtime.invoke.call_count
            with self.assertRaises(RuntimeFailure) as caught:
                skill_pipeline.prepare_replay(
                    runtime, "offline-model", self.project, bundle, "skillops:develop",
                    self.data["rubric"], self.data["images"], runtime.private / "confirmation",
                    work_item=confirmation, deadline=9999999999)
            self.assertEqual(caught.exception.code, "confirmation_isolation_unverified")
            self.assertEqual(runtime.invoke.call_count, calls)
        self.assertEqual(row["decision"]["status"], "improved")
        self.assertEqual(context["execution_mode"], "offline_test")
        self.assertEqual(results.tree_hash(self.project), before)
        evaluation_ref = project_evaluation.persist_replay(
            self.output, row, captures, generation, context["reference"], execution_mode="offline_test", run_id=run_id)
        self.assertEqual(results.encoded(row), row_bytes)
        cycle = {
            **deepcopy(self.data["cycle"]), "run_id": cycle_id, "cycle_id": cycle_id,
            "input_sha256": work["input_sha256"],
            "reference_sha256": context["reference"]["reference_sha256"],
            "original_version_id": row["base_version_id"], "max_rounds": 1,
            "selected_candidate_version_id": row["candidate_version_id"],
            "confirmation_ref": None, "confirmation_status": "unverified",
            "rounds": [{
                "round_id": cycle_id + "-r1", "round_number": 1, "run_id": run_id,
                "parent_version_id": generation["parent_version_id"], "candidate_version_id": row["candidate_version_id"],
                "input_sha256": work["input_sha256"], "reference_sha256": row["reference_sha256"],
                "feedback_source_round_id": None, "feedback_sha256": generation["feedback_sha256"],
                "evaluation_ref": evaluation_ref,
                "decision": row["decision"], "stop_reason": "improved",
            }],
        }
        cycle.pop("report_sha256")
        project_evaluation.persist_cycle(self.output, cycle, context["reference"])
        self.assertEqual(results.load_replays(self.output)[("sample_repo", run_id)]["evaluation"], row)
        self.assertEqual(results.load_cycles(self.output)[("sample_repo", cycle_id)]["confirmation_status"], "unverified")
        with self.assertRaises(RuntimeFailure) as caught:
            self.approve_cycle(cycle)
        self.assertEqual(caught.exception.code, "cycle_not_approvable")
        self.assertFalse(list((self.root / ".skillops/approvals").glob("*.json")))
        self.assertIsNone(repositories.active_version(self.root, project_id="sample_repo", skill_key="skillops:develop"))


class ProducedCycleApprovalTests(unittest.TestCase):
    def test_actual_iteration_adapter_n2_storage_and_approval_rejection(self):
        import skill_approvals
        import test_hackathon_integration as integration

        # Reuse the integration owner's temporary source and external transport
        # fixture, not a second implementation of the loop or runtime adapter.
        producer = integration.ReplayIntegrationTests()
        self.addCleanup(producer.doCleanups)
        producer.setUp()
        original = results.tree_hash(producer.project)
        cycle = producer.run_iterations()
        self.assertEqual(cycle["execution_mode"], "offline_test")
        self.assertEqual([row["decision"]["status"] for row in cycle["rounds"]], ["not_improved", "improved"])
        self.assertEqual(cycle["confirmation_status"], "unverified")
        self.assertIsNotNone(cycle["confirmation_ref"])
        self.assertEqual(producer.budget["calls"], 11)
        path = producer.output / "sample_repo" / cycle["cycle_id"] / "cycle.json"
        artifacts = {path: path.read_bytes() for path in producer.output.rglob("*.json")}
        with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}), self.assertRaises(RuntimeFailure) as caught:
            skill_approvals.approve(
                producer.root, project_id="sample_repo", skill_key=producer.key,
                candidate_version_id=cycle["selected_candidate_version_id"], cycle_id=cycle["cycle_id"],
                evidence_sha256=sha256(path.read_bytes()).hexdigest(), expected_active_version_id=None,
                expected_active_execution_sha256=None, results=producer.output)
        self.assertEqual(caught.exception.code, "cycle_not_approvable")
        self.assertFalse(list((producer.root / ".skillops/approvals").glob("*.json")))
        self.assertEqual(repositories.active_snapshot(producer.root, project_id="sample_repo", skill_key=producer.key),
                         {"version_id": None, "execution_sha256": None})
        self.assertEqual(results.tree_hash(producer.project), original)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, artifacts)


if __name__ == "__main__":
    unittest.main()
