from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import candidates
import evaluation
import skillops
from copilot_runtime import CopilotRuntime, RuntimeFailure
from test_candidates import observation


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("repositories"), "Repository binding implementation is missing.")
        self.r = importlib.import_module("repositories")
        self.original = Path(__file__).resolve().parents[1]
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        for name in (*evaluation.FINGERPRINT_FILES, "skills/develop/SKILL.md"):
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.original / name, dest)
        self.target = self.root / "project-a"
        shutil.copytree(self.root / "projects/sample_repo", self.target)
        self.enterContext(patch("copilot_runtime.shutil.which", return_value="/test-only/copilot"))
        self.base = (self.root / "skills/develop/SKILL.md").read_bytes()
        self.catalog = skillops.load_tasks(self.root)
        self.context = {"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}

    def register(self, identifier="a", path=None):
        return self.r.register(self.root, identifier, path or self.target)

    def calibration(self):
        binding = self.r.resolve(self.root, "a")["binding"]
        context = {**self.context, "repository": binding}
        identifier = "20260915T000000Z-111111111111"
        path = self.root / "runs" / identifier / "calibration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for family in evaluation.FAMILIES:
            for name in ("known_good", "known_bad", "instruction_in_data"):
                good = name == "known_good"
                row = observation(score=4 if good else 1)
                row["execution"]["fixed"].update(all_passed=good, passed=1 if good else 0)
                rows.append({**row, "family": family, "id": name})
        data = {
            "schema_version": 2, "run_id": identifier, "purpose": "calibration",
            "status": "completed", "passed": True, "results": rows, "context": context,
            "fingerprint": evaluation.fingerprint(self.root, context),
            "input_sha256": evaluation.input_hashes(self.root),
        }
        skillops.write_json(path, data)
        return path, data

    def candidate(self, runtime):
        snap = self.r.resolve(self.root, "a")
        identifier = "20260915T000000Z-222222222222"
        folder = self.root / "runs" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 2, "purpose": "baseline", "status": "completed",
            "context": {**self.context, "repository": snap["binding"]},
            "fingerprint": "historical", "input_sha256": evaluation.input_hashes(self.root),
            "skill_sha256": sha256(snap["skill"]).hexdigest(),
            "tasks": [{**observation(), **task} for task in self.catalog["tasks"]],
        }
        skillops.write_json(folder / "report.json", report)
        value = {"instructions": "Check requirements, fix the cause, test boundaries, and review honestly.",
                 "rationale": "Synthetic generator transport fixture, not measured improvement."}
        with patch.object(runtime, "invoke", return_value={"content": json.dumps(value), "usage": {}}) as invoke:
            result, code = candidates.propose(runtime, "gpt-6-astra", identifier)
        self.assertEqual(code, 0)
        self.assertEqual(invoke.call_count, 1)
        return result["run_id"]

    def execute(self, runtime, model, task, contract, seed, skill, rubric, context, identity, directory, cost=80):
        return {
            **observation(cost=cost if directory.name == "candidate" else 100), **task,
            "skill_sha256": sha256(skill).hexdigest(), "seed_sha256": sha256(seed.encode()).hexdigest(),
        }

    def comparison(self, cost=80, effect=None):
        self.register()
        runtime = CopilotRuntime(self.root)
        cal_path, cal = self.calibration()
        candidate = self.candidate(runtime)
        callback = effect or (lambda *args: self.execute(*args, cost=cost))
        with patch.object(candidates, "context_for", return_value=self.context), patch.object(
            candidates, "evaluate_task", side_effect=callback
        ) as execute:
            result, code = candidates.compare(runtime, "gpt-6-astra", self.root, candidate, repository="a")
        return result, code, cal_path, execute

    def test_registration_persists_immutable_pin_and_current_sources(self):
        self.register()
        snap = self.r.resolve(self.root, "a")
        self.assertEqual(snap["skill"], self.base)
        self.assertEqual(snap["seeds"]["listing"], (self.target / "issues.py").read_text())
        (self.root / "skills/develop/SKILL.md").write_text("changed mutable engine skill")
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)
        self.assertEqual(len(self.r.list_repositories(self.root)), 1)
        self.assertEqual(snap["binding"]["repository_id"], "a")
        self.assertEqual(snap["binding"]["evaluation_set"], "issue-management-v2")
        self.assertEqual(snap["project_root"], str(self.target))
        self.assertEqual(self.r.resolve(self.root)["project_root"], str(self.root))

    def test_active_is_separate_from_immutable_legacy_registration(self):
        self.register()
        before = (self.root / ".skillops/registry.json").read_bytes()
        self.assertTrue(callable(getattr(self.r, "active_version", None)), "Active reader is missing.")
        self.assertIsNone(self.r.active_version(self.root, project_id="a", skill_key="skillops:develop"))
        self.assertEqual((self.root / ".skillops/registry.json").read_bytes(), before)
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)

    def test_active_snapshot_does_not_infer_a_pair_from_legacy_pin(self):
        self.register()
        before = (self.root / ".skillops/registry.json").read_bytes()
        self.assertTrue(callable(getattr(self.r, "active_snapshot", None)), "Atomic Active snapshot is missing.")
        self.assertEqual(self.r.active_snapshot(self.root, project_id="a", skill_key="skillops:develop"),
                         {"version_id": None, "execution_sha256": None})
        self.assertEqual((self.root / ".skillops/registry.json").read_bytes(), before)

    def test_snapshot_is_stable_across_relative_and_absolute_roots(self):
        self.register()
        relative_root = Path(os.path.relpath(self.root))
        for repository, project_root in ((None, self.root), ("a", self.target)):
            for root in (relative_root, self.root / "project-a" / ".."):
                with self.subTest(repository=repository, root=root):
                    absolute = self.r.resolve(self.root, repository)
                    snapshot = self.r.resolve(root, repository)
                    try:
                        self.r.assert_snapshot(self.root, snapshot)
                        self.r.assert_snapshot(root, absolute)
                    except RuntimeFailure as error:
                        self.fail(f"Equivalent root expressions raised {error.code}.")
                    self.assertEqual(snapshot, absolute)
                    self.assertEqual(snapshot["project_root"], os.path.abspath(project_root))

    def test_invalid_duplicate_and_unsupported_registration_preserves_state(self):
        self.register()
        before = (self.root / ".skillops/registry.json").read_bytes()
        for identifier, path, options in (
            ("a", self.target, {}), ("b", self.target, {}), ("../escape", self.target, {}),
            ("b", self.target, {"skill": "other"}), ("b", self.target, {"evaluation_set": "unknown"}),
            ("b", self.root / "missing", {}),
        ):
            with self.subTest(identifier=identifier, options=options), self.assertRaises(RuntimeFailure):
                self.r.register(self.root, identifier, path, **options)
        self.assertEqual((self.root / ".skillops/registry.json").read_bytes(), before)

    def test_registry_rejects_symlinks_corruption_and_atomic_write_failure(self):
        self.register()
        store = self.root / ".skillops/registry.json"
        before = store.read_bytes()
        second = self.root / "project-b"
        shutil.copytree(self.target, second)
        with patch("repositories.os.replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises((RuntimeFailure, OSError)):
                self.register("b", second)
        self.assertEqual(store.read_bytes(), before)
        for data in (b'{"schema_version":1,"schema_version":1}', b'{}', b'x' * (2 * 1024 * 1024 + 1)):
            store.write_bytes(data)
            with self.assertRaises(RuntimeFailure):
                self.r.resolve(self.root, "a")
        store.write_bytes(before)
        source = self.target / "issues.py"
        source.unlink()
        source.symlink_to(self.root / "projects/sample_repo/issues.py")
        with self.assertRaises(RuntimeFailure):
            self.r.resolve(self.root, "a")
        store.unlink()
        store.symlink_to(self.root / "eval/tasks.json")
        with self.assertRaises(RuntimeFailure):
            self.r.list_repositories(self.root)

    def test_registry_busy_is_explicit_and_preserves_pin(self):
        import fcntl
        self.register()
        with (self.root / ".skillops/lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(RuntimeFailure) as caught:
                self.r.resolve(self.root, "a")
            self.assertEqual(caught.exception.code, "registry_busy")
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)

    def test_offline_commands_never_construct_copilot(self):
        for args, expected in (
            (["register", "--repository", "a", "--path", str(self.target)], 0),
            (["repositories"], 0),
            (["eligibility", "--repository", "a", "--comparison", "20260915T000000Z-999999999999"], 2),
            (["register", "--repository", "a", "--path", str(self.target)], 2),
            (["register", "--repository", "b", "--path", str(self.target), "--skill", "unknown"], 2),
        ):
            with patch.object(skillops, "__file__", str(self.root / "skillops.py")), patch(
                "sys.argv", ["skillops.py", *args]
            ), patch.object(skillops, "CopilotRuntime") as runtime, patch("builtins.print"):
                code = skillops.main()
            self.assertEqual(code, expected)
            runtime.assert_not_called()
        with patch("sys.argv", ["skillops.py", "doctor"]), patch.object(
            skillops, "CopilotRuntime", side_effect=RuntimeFailure("missing_cli", "Synthetic missing CLI.")
        ), patch("builtins.print"):
            self.assertEqual(skillops.main(), 2)

    def test_bound_baseline_uses_target_bytes_and_pinned_skill(self):
        self.register()
        runtime = CopilotRuntime(self.root)
        (self.target / "issues.py").write_text((self.target / "issues.py").read_text() + "\n# target-only marker\n")
        self.calibration()
        with patch.object(skillops, "context_for", return_value=self.context), patch.object(
            skillops, "evaluate_task", side_effect=self.execute
        ) as execute:
            result, code = skillops.baseline(runtime, "gpt-6-astra", self.root, repository="a")
        self.assertEqual(code, 0)
        self.assertEqual(execute.call_count, 5)
        self.assertIn("target-only marker", execute.call_args_list[0].args[4])
        self.assertTrue(all(call.args[5] == self.base for call in execute.call_args_list))
        report = json.loads((self.root / result["report"]).with_suffix(".json").read_text())
        self.assertEqual(report["context"]["repository"], self.r.resolve(self.root, "a")["binding"])

    def test_bound_calibration_keeps_evaluator_controls_and_binding(self):
        self.register()
        runtime = CopilotRuntime(self.root)
        def run(source, image, family):
            good = source.parent.name == "known_good"
            return {"fixed": {"all_passed": good, "passed": 1 if good else 0, "total": 1},
                    "generated": {"passed": True}}
        def judge(runtime, prompt, model, artifact):
            score = 4 if artifact.parent.name == "known_good" else 1
            return {"content": json.dumps(observation(score=score)["judge"]["dimensions"]), "usage": {}}
        with patch.object(skillops, "context_for", return_value=self.context), patch.object(
            skillops, "execute", side_effect=run
        ), patch.object(skillops, "judge_call", side_effect=judge) as calls:
            summary, code = skillops.calibrate(runtime, "gpt-6-astra", self.root, repository="a")
        self.assertEqual(code, 0)
        self.assertEqual(calls.call_count, 9)
        data = json.loads((self.root / summary["artifact"]).read_text())
        self.assertEqual(data["context"]["repository"], self.r.resolve(self.root, "a")["binding"])

    def test_bound_generation_comparison_and_eligibility_leave_pin_unchanged(self):
        summary, code, _, execute = self.comparison()
        self.assertEqual(code, 0)
        self.assertEqual(execute.call_count, 10)
        before = (self.root / ".skillops/registry.json").read_bytes()
        verdict, code = self.r.eligibility(self.root, "a", summary["run_id"])
        self.assertEqual((code, verdict["decision"]), (0, "eligible_for_canary"))
        self.assertEqual((self.root / ".skillops/registry.json").read_bytes(), before)
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)

    def test_rejected_result_is_not_deployment_eligibility(self):
        summary, code, _, _ = self.comparison(cost=100)
        self.assertEqual(code, 1)
        verdict, code = self.r.eligibility(self.root, "a", summary["run_id"])
        self.assertEqual((code, verdict["decision"]), (1, "rejected"))
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)

    def test_cross_repository_and_source_or_evaluator_drift_block(self):
        summary, _, _, _ = self.comparison()
        second = self.root / "project-b"
        shutil.copytree(self.target, second)
        self.register("b", second)
        self.assertEqual(self.r.eligibility(self.root, "b", summary["run_id"])[1], 2)
        for path in (self.target / "issues.py", self.root / "eval/rubric.json", self.root / "evaluation.py"):
            before = path.read_bytes()
            path.write_bytes(before + b"\n")
            self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
            path.write_bytes(before)
        store = self.root / ".skillops/registry.json"
        before = store.read_bytes()
        data = json.loads(before)
        content = self.base.decode() + "\n"
        digest = sha256(content.encode()).hexdigest()
        data["skills"][digest] = content
        data["repositories"]["a"]["skill_sha256"] = digest
        store.write_text(json.dumps(data))
        self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
        store.write_bytes(before)

    def test_modified_comparison_or_candidate_artifact_blocks(self):
        summary, _, _, _ = self.comparison()
        path = self.root / summary["artifact"]
        report = json.loads(path.read_text())
        for file in (path, self.root / "runs" / report["candidate_run"] / "SKILL.md",
                     self.root / "runs" / report["candidate_run"] / "candidate.json"):
            before = file.read_bytes()
            file.write_bytes(before + b"\n")
            self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
            file.write_bytes(before)

    def test_calibration_mutations_and_deletion_cannot_substitute_evidence(self):
        summary, _, path, _ = self.comparison()
        original = path.read_bytes()
        data = json.loads(original)
        variants = []
        value = deepcopy(data)
        value["results"][0]["judge"] = observation(score=3)["judge"]
        variants.append(value)
        value = deepcopy(data)
        value["results"][0]["execution"]["fixed"]["all_passed"] = False
        variants.append(value)
        variants.append({**data, "status": "blocked"})
        for value in variants:
            skillops.write_json(path, value)
            self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
        replacement = path.parent.parent / "20260915T000000Z-333333333333" / "calibration.json"
        replacement.parent.mkdir()
        replacement.write_bytes(original)
        path.unlink()
        self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
        path.write_bytes(original)
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)

    def test_calibration_drift_during_comparison_blocks_final_seal(self):
        def execute(*args):
            path = self.root / "runs/20260915T000000Z-111111111111/calibration.json"
            path.write_bytes(path.read_bytes() + b"\n")
            return self.execute(*args)
        summary, code, _, _ = self.comparison(effect=execute)
        self.assertEqual(code, 2)
        self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)

    def test_receipt_failure_persists_blocked_comparison(self):
        with patch("repositories.seal_comparison", side_effect=RuntimeFailure("registry_busy", "Synthetic lock failure.")):
            summary, code, _, _ = self.comparison()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads((self.root / summary["artifact"]).read_text())["status"], "blocked")
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)

    def test_source_drift_during_baseline_is_blocked(self):
        self.register()
        self.calibration()
        runtime = CopilotRuntime(self.root)
        def execute(*args):
            path = self.target / "issues.py"
            path.write_text(path.read_text() + "\n")
            return self.execute(*args)
        with patch.object(skillops, "context_for", return_value=self.context), patch.object(
            skillops, "evaluate_task", side_effect=execute
        ):
            summary, code = skillops.baseline(runtime, "gpt-6-astra", self.root, repository="a")
        self.assertEqual(code, 2)

    def test_corrupt_unicode_pin_and_symbolic_lock_fail_explicitly(self):
        self.register()
        path = self.root / ".skillops/registry.json"
        before = path.read_bytes()
        data = json.loads(before)
        digest = next(iter(data["skills"]))
        data["skills"][digest] = "\ud800"
        path.write_text(json.dumps(data))
        with self.assertRaises((RuntimeFailure, UnicodeError)) as caught:
            self.r.resolve(self.root, "a")
        self.assertIsInstance(caught.exception, RuntimeFailure)
        path.write_bytes(before)
        lock = self.root / ".skillops/lock"
        lock.unlink()
        lock.symlink_to(self.root / "eval/tasks.json")
        with self.assertRaises((RuntimeFailure, OSError)) as caught:
            self.r.resolve(self.root, "a")
        self.assertIsInstance(caught.exception, RuntimeFailure)

    def test_invalid_calibration_blocks_before_any_comparison_call(self):
        self.register()
        runtime = CopilotRuntime(self.root)
        path, original = self.calibration()
        candidate = self.candidate(runtime)
        variants = [{**original, "status": "blocked"}]
        data = deepcopy(original)
        data["results"][1]["execution"]["fixed"].update(all_passed=True, passed=1)
        variants.append(data)
        for data in variants:
            skillops.write_json(path, data)
            with patch.object(candidates, "context_for", return_value=self.context), patch.object(
                candidates, "evaluate_task"
            ) as execute:
                _, code = candidates.compare(runtime, "gpt-6-astra", self.root, candidate, repository="a")
            self.assertEqual(code, 2)
            execute.assert_not_called()

    def test_receipt_does_not_replace_policy_recomputation(self):
        real_decide = candidates.decide
        def incorrect_decision(pairs, requested):
            result = real_decide(pairs, requested)
            result["decision"] = "eligible_for_canary"
            return result
        with patch.object(candidates, "decide", side_effect=incorrect_decision):
            summary, code, _, _ = self.comparison(cost=100)
        self.assertEqual(code, 0)
        verdict, code = self.r.eligibility(self.root, "a", summary["run_id"])
        self.assertEqual(code, 2)
        self.assertEqual(verdict["error"]["code"], "changed_decision")

    def test_report_replacement_before_sealing_cannot_gain_a_receipt(self):
        original_seal = self.r.seal_comparison
        registry_before = []

        def substitute(*args):
            root, repository, identifier = args[:3]
            registry_before.append((root / ".skillops/registry.json").read_bytes())
            path = root / "runs" / identifier / "comparison.json"
            report = json.loads(path.read_text())
            self.assertEqual(report["decision"]["decision"], "rejected")
            for pair in report["pairs"]:
                for role in ("developer_usage", "judge_usage"):
                    pair["candidate"][role]["nano_aiu"]["value"] = 40
            report["decision"] = candidates.decide(report["pairs"], len(report["pairs"]))
            self.assertEqual(report["decision"]["decision"], "eligible_for_canary")
            skillops.write_json(path, report)
            return original_seal(*args)

        with patch.object(self.r, "seal_comparison", side_effect=substitute):
            summary, code, _, _ = self.comparison(cost=100)
        self.assertEqual(code, 2, "A substituted report must not be sealed as evaluator-produced evidence.")
        stored = json.loads((self.root / summary["artifact"]).read_text())
        self.assertEqual(stored["status"], "blocked")
        self.assertEqual(stored["error"]["code"], "changed_comparison")
        self.assertEqual((self.root / ".skillops/registry.json").read_bytes(), registry_before[0])
        self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
        self.assertEqual(self.r.resolve(self.root, "a")["skill"], self.base)
