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
from unittest.mock import patch

from copilot_runtime import RuntimeFailure
import evolution_records as evolution
import project_checks
import project_results as results
import repositories
import skill_assessments
import skill_guide
import skill_pipeline
from test_skill_assessments import fixture


def digest(value):
    return sha256(results.encoded(value)).hexdigest()


class ApprovalTests(unittest.TestCase):
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
        # Only the not-yet-delivered common providers are doubled. Real report,
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
                    evidence_sha256=self.evidence, expected_active_version_id=None, results=self.output)
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
        }
        return {**row, **changes}

    def active(self):
        return repositories.active_version(self.root, project_id="sample_repo", skill_key=self.key)

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

    def test_existing_active_approval_is_blocked_until_pointer_revision_contract(self):
        approved = self.approve()
        self.a.record_execution(self.root, approval=approved, receipt=self.receipt(approved))
        with self.assertRaises(RuntimeFailure) as caught:
            self.approve(expected_active_version_id=approved["candidate_version_id"])
        self.assertEqual(caught.exception.code, "active_revision_unavailable")
        self.assertEqual(len(list((self.root / ".skillops/approvals").glob("*.json"))), 1)

    def test_nonnull_predecessor_records_cannot_bypass_revision_block(self):
        approved = self.approve()
        binding = {key: value for key, value in approved.items() if key not in ("approval_id", "approved_at")}
        binding["previous_active_version_id"] = self.candidate[0]["version_id"]
        pending = {**binding, "approval_id": "approval-" + digest(binding)[:48],
                   "approved_at": approved["approved_at"]}
        results.atomic_json(self.root / ".skillops/approvals" / (pending["approval_id"] + ".json"), pending)
        for action in (lambda: self.resolve(pending),
                       lambda: self.a.record_execution(self.root, approval=pending, receipt=self.receipt(pending))):
            with self.assertRaises(RuntimeFailure) as caught:
                action()
            self.assertEqual(caught.exception.code, "active_revision_unavailable")
        self.assertIsNone(self.active())

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


if __name__ == "__main__":
    unittest.main()
