import json
import base64
from copy import deepcopy
import os
from hashlib import sha256
from pathlib import Path
import tempfile
import time
import unittest
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import evolution_records as evolution
import project_checks
import project_results
import project_evaluation
import skill_assessments
import skill_guide
import skill_pipeline
import copilot_runtime
from copilot_runtime import RuntimeFailure, capture as real_capture
from project_checks import execute as real_execute_checks
from skill_guide import evaluate_bundle as real_evaluate_bundle


class ReplayTests(unittest.TestCase):
    """Real replay/validation integration; only external model, Git and checker I/O is doubled."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        skill = self.project / "skills/develop/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: develop\ndescription: Develop safely.\n---\nCheck code.\n")
        (skill.parent / "reference.txt").write_text("Keep this companion.\n")
        (self.project / "api.py").write_text("VALUE = 0\n")
        (self.project / "tests").mkdir()
        (self.project / "tests/test_api.py").write_text("HIDDEN_CONFIRMATION_SENTINEL = 1\n")
        self.bundle = skill_guide.discover(self.project)[0]
        self.runtime = Mock(project=self.root, cli="/offline/copilot", env={}, execution_mode="offline_test")
        self.runtime.private = self.root / "private"
        self.runtime.private.mkdir()
        self.images = {"python": "sha256:" + "a" * 64}
        self.rubric = {"dimensions": ["workflow_clarity"]}
        self.plan = project_checks.discover(self.project)
        self.work = {
            "schema_version": 1, "task_id": "task", "project_id": "project",
            "source_commit": "a" * 40, "project_tree_sha256": project_results.tree_hash(self.project),
            "request": "Implement VALUE = 1 for the recorded development request.",
            "split": "development", "sources": {"api.py": sha256(b"VALUE = 0\n").hexdigest()},
            "checks": {"plan_sha256": self.plan["sha256"],
                       "protected_sha256": project_checks.protected_digest(
                           self.project, project_checks.protected_files(self.project)),
                       "required_case_ids": ["task"], "required_gate_ids": []},
        }
        self.work["input_sha256"] = self.hash(self.work)
        self.prompts, self.check_inputs = [], []
        self.runtime.invoke.side_effect = self.invoke
        for target, name, implementation in (
            (skill_pipeline, "capture", lambda *a, **k: CompletedProcess([], 0, "a" * 40 + "\n", "")),
            (skill_guide, "evaluate_bundle", self.quality),
            (project_checks, "execute", self.check),
        ):
            self.enterContext(patch.object(target, name, side_effect=implementation, create=True))

    @staticmethod
    def hash(value):
        return sha256(project_results.encoded(value)).hexdigest()

    def quality(self, runtime, model, bundle, rubric, artifact):
        return {"status": "pass", "static": {"findings": []},
                "judge": {"dimensions": {"workflow_clarity": {"score": 2}}}}

    def check(self, project, plan, images, **kwargs):
        self.assertEqual(kwargs["deadline"], 9999999999)
        self.check_inputs.append((Path(project) / "api.py").read_text())
        return {"plan_sha256": plan["sha256"], "environment_sha256": project_checks.digest(images),
                "protected_sha256": self.work["checks"]["protected_sha256"], "status": "completed",
                "cases": [{"id": "task", "status": "passed"},
                          {"id": "confirmation-secret-case", "status": "passed"}],
                "gates": [], "elapsed_seconds": 1}

    def invoke(self, prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
        self.prompts.append((role, prompt))
        self.assertNotIn("HIDDEN_CONFIRMATION_SENTINEL", prompt)
        self.assertNotIn("confirmation-secret-case", prompt)
        if role == "generator":
            return {"content": json.dumps({
                "instructions": "Check code, retain behavior, and verify the request. Attempt "
                                + str(sum(role == "generator" for role, _ in self.prompts)) + ".",
                "addressed_findings": ["workflow_clarity"], "hypothesis": "May clarify the workflow."})}
        from copilot_runtime import verify_staged_version
        self.assertEqual(role, "developer")
        self.assertEqual(verify_staged_version(expected_skill, kwargs["expected_version"]),
                         kwargs["expected_version"]["version_id"])
        self.assertFalse((Path(workdir) / "tests").exists())
        self.assertFalse((Path(workdir) / "api.py").exists())
        return {"content": json.dumps({"files": {"api.py": "VALUE = 1\n"}}),
                "skill_version_verified": True, "skill_activated": True,
                "staged_version_id": kwargs["expected_version"]["version_id"],
                "elapsed_seconds": 2, "usage": {"nano_aiu": {"value": 123}}}

    def prepare(self, name="prepare", work=None):
        return skill_pipeline.prepare_replay(
            self.runtime, "offline-model", self.project, self.bundle, "skillops:develop",
            self.rubric, self.images, self.runtime.private / name,
            work_item=work or self.work, deadline=9999999999)

    def candidate(self, context):
        files = skill_pipeline.candidate_files(context["original"][1], {
            "instructions": "Check the requested behavior and preserve existing behavior.",
            "addressed_findings": [], "hypothesis": "Offline candidate fixture.",
        })
        return evolution.capture_version(files, capture_scope="complete_bundle", complete_inventory=list(files))

    def test_replay_freezes_reference_and_repeats_real_request_on_pristine_source(self):
        context = self.prepare()
        self.assertEqual(context["reference"]["original_version_id"], context["original"][0]["version_id"])
        self.assertEqual(context["feedback"]["checks"]["cases"], [{"id": "task", "status": "passed"}])
        generation, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=9999999999)
        self.assertEqual(generation["feedback_sha256"], self.hash(context["feedback"]))
        self.assertEqual(generation["parent_version_id"], context["original"][0]["version_id"])
        before = deepcopy(context)
        for number in (1, 2):
            row, captures = skill_pipeline.evaluate_candidate(
                self.runtime, "offline-model", context, candidate, self.runtime.private / f"round-{number}",
                deadline=9999999999)
            self.assertEqual(row["base_version_id"], context["original"][0]["version_id"])
            self.assertEqual(row["candidate_version_id"], candidate[0]["version_id"])
            self.assertEqual(captures, [context["original"], candidate])
            self.assertEqual(set(row), set("skill_key source_path base_version_id candidate_version_id work "
                                          "reference_sha256 quality applications checks decision errors".split()))
            for arm in ("base", "candidate"):
                self.assertEqual(row["applications"][arm]["work_sha256"], self.work["input_sha256"])
                self.assertEqual(row["applications"][arm]["task_outcome"], "satisfied")
                self.assertEqual(row["applications"][arm]["measurement"],
                                 {"cost_nano_aiu": 123, "elapsed_seconds": 2})
            self.assertNotIn(self.work["request"], json.dumps(row))
        developers = [prompt for role, prompt in self.prompts if role == "developer"]
        self.assertEqual(len(developers), 4)
        for prompt in developers:
            self.assertIn(self.work["request"], prompt)
            self.assertIn("VALUE = 0", prompt)
        self.assertEqual(self.check_inputs, ["VALUE = 0\n", *(["VALUE = 1\n"] * 4)])
        self.assertEqual(context, before)
        self.assertEqual((self.project / "api.py").read_text(), "VALUE = 0\n")

    def test_different_requests_reach_developer_with_different_hashes(self):
        first = self.prepare()
        changed = deepcopy(self.work)
        changed["request"] = "Implement a second distinct development request."
        changed["input_sha256"] = self.hash({k: v for k, v in changed.items() if k != "input_sha256"})
        second = self.prepare("second", changed)
        self.assertNotEqual(first["reference"]["input_sha256"], second["reference"]["input_sha256"])
        skill_pipeline.evaluate_candidate(self.runtime, "offline-model", second, self.candidate(second),
                                          self.runtime.private / "second-run", deadline=9999999999)
        self.assertIn(changed["request"], self.prompts[-1][1])

    def test_input_version_and_frozen_context_mismatches_stop_before_application(self):
        context = self.prepare()
        for field in ("input_sha256", "original_version_id", "environment_sha256", "policy_sha256"):
            changed = deepcopy(context)
            changed["reference"][field] = "b" * 64
            with self.subTest(field=field), self.assertRaises(RuntimeFailure):
                skill_pipeline.evaluate_candidate(self.runtime, "offline-model", changed, context["original"],
                                                  self.runtime.private / field, deadline=9999999999)
        version, files = deepcopy(context["original"])
        files["reference.txt"] = b"Tampered companion."
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.evaluate_candidate(self.runtime, "offline-model", context, (version, files),
                                              self.runtime.private / "bad-capture", deadline=9999999999)
        (self.project / "api.py").write_text("changed outside replay\n")
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.evaluate_candidate(self.runtime, "offline-model", context, context["original"],
                                              self.runtime.private / "changed-source", deadline=9999999999)
        self.assertFalse(any(role == "developer" for role, _ in self.prompts))

    def test_confirmation_and_unretained_feedback_never_reach_generator(self):
        work = deepcopy(self.work)
        work["split"] = "confirmation"
        work["input_sha256"] = self.hash({k: v for k, v in work.items() if k != "input_sha256"})
        with self.assertRaises(RuntimeFailure) as caught:
            self.prepare(work=work)
        self.assertEqual(caught.exception.code, "confirmation_isolation_unverified")
        self.assertEqual(self.prompts, [])
        context = self.prepare()
        feedback = deepcopy(context["feedback"])
        feedback["quality"]["dimensions"][0]["score"] = 4
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.generate_candidate(self.runtime, "offline-model", context["original"], feedback,
                                              self.runtime.private / "forged-feedback", deadline=9999999999)
        self.assertEqual(self.prompts, [])

    def test_development_feedback_requires_retained_row_and_single_round_binding(self):
        context = self.prepare()
        generation, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=9999999999)
        row, _ = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / "evaluate",
            deadline=9999999999)
        round_id = "local-20260917T130000Z-aaaaaaaaaaaa-r1"
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.development_feedback(self.runtime, context, {**row, "errors": [{"stage": "work",
                                                "code": "forged"}]}, source_round_id=round_id)
        packet = skill_pipeline.development_feedback(self.runtime, context, row, source_round_id=round_id)
        self.assertEqual(packet["decision"], row["decision"])
        self.assertEqual(packet["checks"], {"cases": [{"id": "task", "status": "passed"}], "gates": []})
        self.assertNotIn("elapsed_seconds", packet["checks"])
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.development_feedback(self.runtime, context, row,
                                                source_round_id=round_id[:-1] + "2")
        calls = len(self.prompts)
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.generate_candidate(self.runtime, "offline-model", context["original"], packet,
                                              self.runtime.private / "wrong-parent", deadline=9999999999)
        self.assertEqual(len(self.prompts), calls)

    def test_changed_mode_scope_and_retained_evidence_are_rejected(self):
        context = self.prepare()
        for change in ("mode", "case", "test_context"):
            mutated = deepcopy(context)
            if change == "mode":
                self.runtime.execution_mode = "live"
            elif change == "case":
                mutated["feedback_scope"]["case_ids"].append("confirmation-secret-case")
            else:
                mutated["feedback_scope"]["test_context_paths"] = ["tests/test_api.py"]
            with self.subTest(change=change), self.assertRaises(RuntimeFailure):
                skill_pipeline.development_feedback(self.runtime, mutated, None, source_round_id=None)
            self.runtime.execution_mode = "offline_test"
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.generate_candidate(self.runtime, "different-model", context["original"],
                                              context["feedback"], self.runtime.private / "wrong-model",
                                              deadline=9999999999)
        self.assertEqual(self.prompts, [])

    def test_unset_execution_mode_is_not_inferred(self):
        self.runtime.execution_mode = None
        with self.assertRaises(RuntimeFailure):
            self.prepare()
        self.assertEqual(self.prompts, [])

    def test_retained_candidate_files_cannot_change_before_feedback_or_generation(self):
        context = self.prepare()
        _, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=9999999999)
        row, _ = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / "evaluate",
            deadline=9999999999)
        path = self.runtime.private / "evaluate/versions/candidate/reference.txt"
        path.write_text("altered retained companion")
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.development_feedback(self.runtime, context, row,
                                                source_round_id="local-20260917T130000Z-aaaaaaaaaaaa-r1")

    def test_absent_activation_and_post_invocation_bundle_mutation_block_receipt(self):
        for kind in ("activation", "mutation"):
            context = self.prepare(kind)
            def invoke(*args, **kwargs):
                result = self.invoke(*args, **kwargs)
                if kind == "activation":
                    result.pop("skill_activated")
                else:
                    (Path(args[5]).parent / "reference.txt").write_text("changed by execution")
                return result
            self.runtime.invoke.side_effect = invoke
            row, _ = skill_pipeline.evaluate_candidate(
                self.runtime, "offline-model", context, self.candidate(context),
                self.runtime.private / (kind + "-run"), deadline=9999999999)
            self.assertIsNone(row["applications"]["base"])
            self.assertTrue(row["errors"])
            self.runtime.invoke.side_effect = self.invoke

    def test_feedback_is_revalidated_after_invocation_before_generation_is_returned(self):
        context = self.prepare()
        original_invoke = self.invoke
        def invoke(*args, **kwargs):
            result = original_invoke(*args, **kwargs)
            path = next((self.runtime.private / "prepare").glob("feedback-*.json"))
            path.write_text("{}\n")
            return result
        self.runtime.invoke.side_effect = invoke
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.generate_candidate(
                self.runtime, "offline-model", context["original"], context["feedback"],
                self.runtime.private / "generate", deadline=9999999999)

    def test_two_round_generation_changes_parent_but_not_original_comparison(self):
        context = self.prepare()
        first_generation, first = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate-1", deadline=9999999999)
        first_row, _ = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, first, self.runtime.private / "evaluate-1",
            deadline=9999999999)
        feedback = skill_pipeline.development_feedback(
            self.runtime, context, first_row, source_round_id="local-20260917T130000Z-aaaaaaaaaaaa-r1")
        second_generation, second = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", first, feedback, self.runtime.private / "generate-2",
            deadline=9999999999)
        second_row, _ = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, second, self.runtime.private / "evaluate-2",
            deadline=9999999999)
        self.assertEqual(first_generation["parent_version_id"], context["original"][0]["version_id"])
        self.assertEqual(second_generation["parent_version_id"], first[0]["version_id"])
        self.assertNotEqual(first[0]["version_id"], second[0]["version_id"])
        self.assertEqual(first_row["base_version_id"], second_row["base_version_id"])
        self.assertEqual(second_row["reference_sha256"], context["reference"]["reference_sha256"])
        self.assertEqual(self.check_inputs, ["VALUE = 0\n", *(["VALUE = 1\n"] * 4)])

    def test_stale_bundle_inventory_is_rejected_before_quality_or_model_calls(self):
        (self.project / "skills/develop/new-resource.txt").write_text("new companion")
        self.work["project_tree_sha256"] = project_results.tree_hash(self.project)
        self.work["input_sha256"] = self.hash({k: v for k, v in self.work.items() if k != "input_sha256"})
        with self.assertRaises(RuntimeFailure):
            self.prepare()
        skill_guide.evaluate_bundle.assert_not_called()
        self.runtime.invoke.assert_not_called()

    def test_candidate_quality_cannot_change_pinned_quality_context(self):
        context = self.prepare()
        quality = deepcopy(context["reference"]["base_quality"])
        quality["context_sha256"] = "b" * 64
        with patch.object(skill_pipeline, "assess_quality", return_value=quality), self.assertRaises(RuntimeFailure):
            skill_pipeline.evaluate_candidate(
                self.runtime, "offline-model", context, self.candidate(context),
                self.runtime.private / "quality-drift", deadline=9999999999)
        self.assertEqual(self.prompts, [])

    def test_private_artifact_rejects_parent_traversal_without_creating_output(self):
        with self.assertRaises(RuntimeFailure):
            skill_pipeline._private_artifact(self.runtime, self.runtime.private / "../escaped")
        self.assertFalse((self.root / "escaped").exists())

    def test_reference_policy_hash_uses_exact_accepted_contract_expression(self):
        from candidates import POLICY
        context = self.prepare()
        self.assertEqual(context["reference"]["policy_sha256"], self.hash({"policy": POLICY, "rule": "replay-v1"}))

    def test_caller_mutation_during_qualification_cannot_replace_evaluated_capture(self):
        context = self.prepare()
        _, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=9999999999)
        expected = deepcopy(candidate)
        def mutate(stage, status, code=None):
            if stage == "qualification" and status == "completed":
                candidate[1]["SKILL.md"] += b"Unevaluated replacement.\n"
                manifest, _ = evolution.capture_version(
                    candidate[1], capture_scope="complete_bundle", complete_inventory=list(candidate[1]))
                candidate[0].clear()
                candidate[0].update(manifest)
        row, captures = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / "evaluate",
            deadline=9999999999, progress=mutate)
        self.assertEqual(row["candidate_version_id"], expected[0]["version_id"])
        self.assertEqual(captures[1], expected)
        self.assertEqual((self.runtime.private / "evaluate/versions/candidate/SKILL.md").read_bytes(),
                         expected[1]["SKILL.md"])

    def test_protected_output_and_operational_failures_are_not_success(self):
        for code in ("protected", "unsupported_dependencies", "skill_version_mismatch"):
            context = self.prepare(code)
            invoke = self.invoke
            def fail(prompt, model, role, workdir, artifact, *args, **kwargs):
                if code == "unsupported_dependencies":
                    raise RuntimeFailure(code, "Offline operational failure.")
                receipt = invoke(prompt, model, role, workdir, artifact, *args, **kwargs)
                if code == "protected":
                    receipt["content"] = json.dumps({"files": {"tests/test_api.py": "weakened"}})
                else:
                    receipt["staged_version_id"] = "sha256:" + "b" * 64
                return receipt
            self.runtime.invoke.side_effect = fail
            row, _ = skill_pipeline.evaluate_candidate(self.runtime, "offline-model", context, self.candidate(context),
                                                       self.runtime.private / (code + "-run"), deadline=9999999999)
            self.assertEqual(row["decision"]["status"], "unverified")
            self.assertTrue(row["errors"])
            self.assertIsNone(row["applications"]["candidate"])
            self.assertIn("HIDDEN_CONFIRMATION_SENTINEL", (self.project / "tests/test_api.py").read_text())
            self.runtime.invoke.side_effect = self.invoke

    def test_missing_real_provider_and_expired_deadline_block_before_model_calls(self):
        with patch.object(skill_assessments, "validate_work_item", None):
            with self.assertRaises(RuntimeFailure) as caught:
                self.prepare()
            self.assertEqual(caught.exception.code, "replay_provider_missing")
        with self.assertRaises(RuntimeFailure) as caught:
            skill_pipeline.prepare_replay(self.runtime, "offline-model", self.project, self.bundle,
                                          "skillops:develop", self.rubric, self.images,
                                          self.runtime.private / "expired", work_item=self.work, deadline=0)
        self.assertEqual(caught.exception.code, "time_limit")
        self.assertEqual(self.prompts, [])

    def test_unchanged_candidate_is_rejected_before_paid_quality_or_application(self):
        context = self.prepare()
        skill_guide.evaluate_bundle.reset_mock()
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.evaluate_candidate(
                self.runtime, "offline-model", context, context["original"],
                self.runtime.private / "unchanged", deadline=9999999999)
        skill_guide.evaluate_bundle.assert_not_called()
        self.runtime.invoke.assert_not_called()

    def test_real_common_decision_and_feedback_block_excluded_regression(self):
        context = self.prepare()
        candidate = self.candidate(context)
        original_check = self.check
        def regress(project, plan, images, **kwargs):
            observed = original_check(project, plan, images, **kwargs)
            if Path(project).parent.name == "candidate":
                observed["cases"][1]["status"] = "failed"
                observed["status"] = "failed"
            return observed
        with patch.object(project_checks, "execute", side_effect=regress):
            row, _ = skill_pipeline.evaluate_candidate(
                self.runtime, "offline-model", context, candidate,
                self.runtime.private / "regression", deadline=9999999999)
        self.assertEqual(row["decision"]["status"], "rejected")
        self.assertIn("test:confirmation-secret-case", row["decision"]["regression"]["regressions"])
        before = project_results.encoded(row)
        with self.assertRaises(RuntimeFailure) as error:
            skill_pipeline.development_feedback(
                self.runtime, context, row, source_round_id="local-20260917T130000Z-aaaaaaaaaaaa-r1")
        self.assertEqual(error.exception.code, "confirmation_isolation_unverified")
        self.assertEqual(project_results.encoded(row), before)
        self.assertFalse(any(role == "generator" for role, _ in self.prompts))

    def test_real_feedback_does_not_disclose_excluded_status_or_suite_timing(self):
        context = self.prepare()
        packet = context["feedback"]
        changed_check = self.check
        def different_hidden_observation(*args, **kwargs):
            observed = changed_check(*args, **kwargs)
            observed["cases"][1]["status"] = "failed"
            observed["status"], observed["elapsed_seconds"] = "failed", 12345
            return observed
        # Separate experiments cannot share one issuance hash with different retained references.
        self.runtime = Mock(project=self.root, cli="/offline/copilot", env={}, execution_mode="offline_test",
                            private=self.runtime.private)
        with patch.object(project_checks, "execute", side_effect=different_hidden_observation):
            other = self.prepare("hidden-difference")
        self.assertNotEqual(context["reference"]["reference_sha256"], other["reference"]["reference_sha256"])
        self.assertEqual(project_results.encoded(packet), project_results.encoded(other["feedback"]))
        self.assertEqual(other["reference"]["original_checks"]["elapsed_seconds"], 12345)
        self.assertEqual(other["reference"]["original_checks"]["status"], "failed")

    def test_real_public_validator_and_reader_accept_actual_provider_evidence(self):
        context = self.prepare()
        generation, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=9999999999)
        row, captures = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate,
            self.runtime.private / "evaluate", deadline=9999999999)
        ref = context["reference"]
        report = {
            "schema_version": 1, "project_id": ref["project_id"], "run_id": "101-1",
            "created_at": "2026-09-17T13:00:00+00:00", "origin": "local", "purpose": "project_assessment",
            "source_commit": ref["source_commit"], "project_tree_sha256": ref["project_tree_sha256"],
            "evaluator_sha256": ref["evaluator_sha256"], "source_report_sha256": None, "source_schema_version": None,
            "guide": project_results.axis("completed", "evaluation_completed"),
            "execution": project_results.axis("completed", "evaluation_completed"),
        }
        common = {"schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
                  "report_sha256": self.hash(report)}
        records = evolution.empty_records()
        records["identities"] = [{"skill_key": row["skill_key"], "display_name": "develop"}]
        records["sources"] = [{"skill_key": row["skill_key"], "project_id": report["project_id"],
                               "kind": "workspace", "scope": "project", "path": row["source_path"],
                               "observed_at": report["created_at"], "evidence_ref": None}]
        records["versions"] = [version for version, _ in captures]
        records["skill_versions"] = [{"skill_key": row["skill_key"], "version_id": version["version_id"]}
                                    for version, _ in captures]
        lifecycle = {**common, "records": records,
                     "bindings": [{"skill_key": row["skill_key"],
                                   "base_version_id": row["base_version_id"],
                                   "candidate_version_id": row["candidate_version_id"], "legacy_skill_id": None}],
                     "file_contents": [{"version_id": version["version_id"], "path": name,
                                        "encoding": "base64", "data": base64.b64encode(raw).decode("ascii")}
                                       for version, files in captures for name, raw in files.items()]}
        wrapper = {**common, "execution_mode": "offline_test", "reference": ref,
                   "generation": generation, "evaluation": row}
        self.assertIs(skill_assessments.validate_replay(wrapper, report=report, lifecycle=lifecycle), wrapper)
        # Fixture-only assembly: this does not implement the session-1 persistence adapter.
        results = self.runtime.private / "test-results"
        for name, payload in (("report.json", report), ("skill-evolution.json", lifecycle),
                              ("replay-evaluation.json", wrapper)):
            project_results.atomic_json(results / report["project_id"] / report["run_id"] / name,
                                        payload, immutable=True)
        self.assertEqual(project_results.load_replays(results), {("project", "101-1"): wrapper})
        self.assertEqual(row["decision"]["status"], "not_improved")

    def test_actual_git_budget_guide_checker_and_shared_validators_across_two_rounds(self):
        """Only model transport and Docker transport are doubled; no live/container run."""
        for argv in (
            ["git", "init", "--quiet", str(self.root)],
            ["git", "-C", str(self.root), "add", "project"],
            ["git", "-C", str(self.root), "-c", "user.name=Offline Test",
             "-c", "user.email=offline@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "offline fixture"],
        ):
            result = real_capture(argv)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.work["source_commit"] = real_capture(["git", "-C", str(self.root), "rev-parse", "HEAD"]).stdout.strip()
        self.work["checks"]["required_case_ids"] = ["python-tests:task"]
        self.work["input_sha256"] = self.hash({k: v for k, v in self.work.items() if k != "input_sha256"})
        budget = {"calls": 0, "max_calls": 9, "max_seconds": 120, "deadline": time.monotonic() + 120}
        transport = self.runtime
        self.runtime = project_evaluation.BudgetRuntime(transport, budget)
        self.runtime.execution_mode = "offline_test"
        checker_inputs = []
        def docker(source, controls, image, argv, **kwargs):
            checker_inputs.append((Path(source) / "api.py").read_text())
            return CompletedProcess([], 0, json.dumps({
                "status": "completed", "cases": [{"id": "task", "status": "passed"},
                                                {"id": "confirmation-secret-case", "status": "passed"}]}), "")
        judge_calls = []
        def invoke(prompt, model, role, *args, **kwargs):
            self.assertNotIn("HIDDEN_CONFIRMATION_SENTINEL", prompt)
            self.assertNotIn("confirmation-secret-case", prompt)
            if role == "judge":
                judge_calls.append(prompt)
                return {"content": json.dumps({"workflow_clarity": {
                    "status": "pass", "score": 3 if len(judge_calls) == 3 else 2,
                    "rationale": "Offline synthetic model response; not a measured quality claim."}})}
            return self.invoke(prompt, model, role, *args, **kwargs)
        transport.invoke.side_effect = invoke
        with patch.object(skill_pipeline, "capture", side_effect=real_capture), patch.object(
                skill_guide, "evaluate_bundle", side_effect=real_evaluate_bundle), patch.object(
                project_checks, "execute", side_effect=real_execute_checks), patch.object(
                project_checks, "container_capture", side_effect=docker):
            context = skill_pipeline.prepare_replay(
                self.runtime, "offline-model", self.project, self.bundle, "skillops:develop",
                self.rubric, self.images, self.runtime.private / "real-prepare",
                work_item=self.work, deadline=budget["deadline"])
            parent, packet, rows = context["original"], context["feedback"], []
            for number in (1, 2):
                generation, candidate = skill_pipeline.generate_candidate(
                    self.runtime, "offline-model", parent, packet,
                    self.runtime.private / f"real-generate-{number}", deadline=budget["deadline"])
                self.assertEqual(generation["parent_version_id"], parent[0]["version_id"])
                row, captures = skill_pipeline.evaluate_candidate(
                    self.runtime, "offline-model", context, candidate,
                    self.runtime.private / f"real-evaluate-{number}", deadline=budget["deadline"])
                rows.append(row)
                self.assertEqual(captures[0], context["original"])
                self.assertEqual(row["decision"]["status"], "not_improved" if number == 1 else "improved")
                packet = skill_pipeline.development_feedback(
                    self.runtime, context, row, source_round_id=f"local-20260917T130000Z-aaaaaaaaaaaa-r{number}")
                parent = candidate
            self.assertEqual(budget["calls"], 9)
            self.assertEqual(project_evaluation.budget_limits(budget),
                             {"max_invocations": 9, "max_seconds": 120, "max_ai_credits_per_session": None})
            with self.assertRaises(RuntimeFailure) as error:
                skill_pipeline.generate_candidate(
                    self.runtime, "offline-model", parent, packet,
                    self.runtime.private / "budget-stop", deadline=budget["deadline"])
            self.assertEqual(error.exception.code, "call_limit")
            self.assertEqual(budget["calls"], 9)
            confirmation = deepcopy(self.work)
            confirmation["split"], confirmation["task_id"] = "confirmation", "confirmation-task"
            confirmation["input_sha256"] = self.hash({k: v for k, v in confirmation.items() if k != "input_sha256"})
            with self.assertRaises(RuntimeFailure) as error:
                skill_pipeline.prepare_replay(
                    self.runtime, "offline-model", self.project, self.bundle, "skillops:develop",
                    self.rubric, self.images, self.runtime.private / "confirmation",
                    work_item=confirmation, deadline=budget["deadline"])
            self.assertEqual(error.exception.code, "confirmation_isolation_unverified")
            self.assertEqual(budget["calls"], 9)
        self.assertEqual(len(judge_calls), 3)
        self.assertEqual(checker_inputs, ["VALUE = 0\n", *(["VALUE = 1\n"] * 4)])
        self.assertEqual(rows[0]["reference_sha256"], rows[1]["reference_sha256"])
        self.assertEqual(rows[0]["base_version_id"], rows[1]["base_version_id"])
        self.assertEqual((self.project / "api.py").read_text(), "VALUE = 0\n")


class ConfirmationTests(unittest.TestCase):
    """Real provider/validators with explicitly simulated external isolation/model/check transport."""

    hash = staticmethod(ReplayTests.hash)
    candidate = ReplayTests.candidate
    invoke = ReplayTests.invoke

    def setUp(self):
        ReplayTests.setUp(self)
        self.deadline = time.monotonic() + 120
        self.transport = self.runtime
        self.capability_active = True
        self.transport.require_confirmation_isolation.side_effect = self.require_simulated_capability
        self.runtime = project_evaluation.BudgetRuntime(self.transport, {
            "calls": 0, "max_calls": 20, "max_seconds": 120, "deadline": self.deadline,
            "max_ai_credits": 100,
        })
        self.runtime.execution_mode = "offline_test"
        self.final = deepcopy(self.work)
        self.final.update(task_id="final-task", split="confirmation", request="FINAL_REQUEST_SENTINEL: verify VALUE.")
        self.final["checks"]["required_case_ids"] = ["confirmation-secret-case"]
        self.final["input_sha256"] = self.hash({k: v for k, v in self.final.items() if k != "input_sha256"})
        self.disclosure = self.make_disclosure()
        self.quality_calls = 0

    def api(self, name):
        function = getattr(skill_pipeline, name, None)
        self.assertTrue(callable(function), "Missing accepted provider API: " + name)
        return function

    def require_simulated_capability(self):
        if not self.capability_active:
            raise RuntimeFailure("confirmation_isolation_unverified", "Offline simulated capability expired.")

    def quality(self, *args):
        self.quality_calls += 1
        result = ReplayTests.quality(self, *args)
        result["judge"]["dimensions"]["workflow_clarity"]["score"] = 2 if self.quality_calls == 1 else 3
        return result

    def check(self, project, plan, images, **kwargs):
        self.assertEqual(kwargs["deadline"], self.deadline)
        return ReplayTests.check(self, project, plan, images, deadline=9999999999)

    def make_disclosure(self):
        visible = set(self.work["sources"]) | set(self.final["sources"]) | {
            self.bundle["path"] + "/" + item["path"] for item in self.bundle["files"]}
        inventory = {p.relative_to(self.project).as_posix(): sha256(p.read_bytes()).hexdigest()
                     for p in self.project.rglob("*") if p.is_file()}
        disclosure = {
            "schema_version": 1, "development_input_sha256": self.work["input_sha256"],
            "confirmation_input_sha256": self.final["input_sha256"],
            "model_visible_files": {p: raw for p, raw in inventory.items() if p in visible},
            "checker_only_files": {p: raw for p, raw in inventory.items() if p not in visible},
        }
        return {**disclosure, "disclosure_sha256": self.hash(disclosure)}

    def register(self, name="registration"):
        return self.api("register_confirmation")(
            self.runtime, "offline-model", self.project, self.bundle, "skillops:develop",
            self.rubric, self.images, self.runtime.private / name,
            development_work_item=self.work, confirmation_work_item=self.final,
            disclosure=self.disclosure, deadline=self.deadline)

    def prepare(self, name="prepare"):
        return skill_pipeline.prepare_replay(
            self.runtime, "offline-model", self.project, self.bundle, "skillops:develop",
            self.rubric, self.images, self.runtime.private / name, work_item=self.work, deadline=self.deadline)

    def evaluate(self, context, candidate, name="development"):
        return skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / name, deadline=self.deadline)

    def selected(self):
        registration = self.register()
        context = self.prepare()
        candidate = self.candidate(context)
        row, _ = self.evaluate(context, candidate)
        self.assertEqual(row["decision"]["status"], "improved")
        return registration, context, candidate, row

    def confirm(self, registration, context, candidate, name="confirmation"):
        return self.api("prepare_confirmation")(
            self.runtime, "offline-model", context, candidate, self.runtime.private / name,
            registration_id=registration, deadline=self.deadline)

    def assert_code(self, code, function):
        with self.assertRaises(RuntimeFailure) as error:
            function()
        self.assertEqual(error.exception.code, code)

    def test_registration_is_pre_model_private_and_requires_capability(self):
        self.api("register_confirmation")
        self.capability_active = False
        self.assert_code("confirmation_isolation_unverified", self.register)
        self.assertEqual(self.quality_calls, 0)
        self.assertEqual(self.prompts, [])
        self.capability_active = True
        registration = self.register()
        self.assertIsInstance(registration, str)
        self.assertEqual(self.runtime.budget["calls"], 0)
        self.assertFalse((self.runtime.private / "confirmation-exposures").exists())
        self.assertTrue(list((self.runtime.private / "registration").rglob("*.json")))

    def test_late_registration_and_wrong_runtime_are_rejected(self):
        self.api("register_confirmation")
        context = self.prepare()
        self.assert_code("confirmation_not_registered", self.register)
        self.assert_code("confirmation_not_registered",
                         lambda: self.confirm("unissued", context, self.candidate(context)))

    def test_registration_binds_budget_images_mode_and_private_bytes(self):
        self.api("register_confirmation")
        self.register()
        original = deepcopy(self.runtime.budget)
        self.runtime.budget["max_calls"] += 1
        self.assert_code("confirmation_inputs_changed", self.prepare)
        self.runtime.budget.update(original)
        self.images["python"] = "sha256:" + "b" * 64
        self.assert_code("confirmation_inputs_changed", self.prepare)
        self.images["python"] = "sha256:" + "a" * 64
        self.runtime.execution_mode = "live"
        self.assert_code("confirmation_inputs_changed", self.prepare)
        self.runtime.execution_mode = "offline_test"
        record = next((self.runtime.private / "registration").rglob("*.json"))
        record.write_bytes(b"{}\n")
        self.assert_code("confirmation_inputs_changed", self.prepare)
        self.assertEqual(self.quality_calls, 0)

    def test_capability_loss_during_original_checks_stops_before_baseline_judge(self):
        self.register()
        check = self.check

        def revoke(*args, **kwargs):
            result = check(*args, **kwargs)
            self.capability_active = False
            return result

        with patch.object(project_checks, "execute", side_effect=revoke):
            self.assert_code("confirmation_isolation_unverified", self.prepare)
        self.assertEqual(self.quality_calls, 0)

    def test_disclosure_rejects_hidden_request_in_a_complete_skill_companion(self):
        self.api("register_confirmation")
        resource = self.project / self.bundle["path"] / "reference.txt"
        resource.write_text(self.final["request"])
        self.bundle = skill_guide.discover(self.project)[0]
        for work in (self.work, self.final):
            work["project_tree_sha256"] = project_results.tree_hash(self.project)
            work["input_sha256"] = self.hash({k: v for k, v in work.items() if k != "input_sha256"})
        self.disclosure = self.make_disclosure()
        self.assert_code("confirmation_isolation_unverified", self.register)
        self.assertEqual(self.quality_calls, 0)

    def test_hidden_companions_and_wrong_splits_cannot_be_declared_visible(self):
        self.api("register_confirmation")
        original_final, original_disclosure = deepcopy((self.final, self.disclosure))
        for mutation in ("split", "overlapping-case", "companion", "test"):
            self.final, self.disclosure = deepcopy((original_final, original_disclosure))
            if mutation == "split":
                self.final["split"] = "development"
            elif mutation == "overlapping-case":
                self.final["checks"]["required_case_ids"] = self.work["checks"]["required_case_ids"]
            else:
                source, target, path = (
                    ("model_visible_files", "checker_only_files", "skills/develop/reference.txt")
                    if mutation == "companion" else
                    ("checker_only_files", "model_visible_files", "tests/test_api.py"))
                self.disclosure[target][path] = self.disclosure[source].pop(path)
            self.final["input_sha256"] = self.hash({k: v for k, v in self.final.items() if k != "input_sha256"})
            self.disclosure["confirmation_input_sha256"] = self.final["input_sha256"]
            self.disclosure["disclosure_sha256"] = self.hash({
                k: v for k, v in self.disclosure.items() if k != "disclosure_sha256"})
            with self.subTest(mutation=mutation):
                self.assert_code("confirmation_isolation_unverified", self.register)
        self.assertEqual(self.quality_calls, 0)

    def test_final_selection_and_development_reference_cannot_drift(self):
        registration, context, candidate, _ = self.selected()
        budget, raw = self.runtime.budget, self.runtime.runtime
        self.runtime.budget = deepcopy(budget)
        self.assert_code("confirmation_inputs_changed", lambda: self.confirm(registration, context, candidate))
        self.runtime.budget = budget
        self.runtime.runtime = Mock(wraps=raw, private=raw.private)
        self.assert_code("confirmation_inputs_changed", lambda: self.confirm(registration, context, candidate))
        self.runtime.runtime = raw
        final = self.confirm(registration, context, candidate)
        files = skill_pipeline.candidate_files(candidate[1], {
            "instructions": "A different final candidate.", "addressed_findings": [], "hypothesis": "Not selected."})
        wrong = evolution.capture_version(files, capture_scope="complete_bundle", complete_inventory=list(files))
        self.assert_code("confirmation_candidate_mismatch", lambda: self.evaluate(final, wrong, "wrong-selection"))
        self.assertIsNone(final["feedback"])

    def test_registered_generation_cannot_replace_the_shared_deadline(self):
        self.register()
        context = self.prepare()
        self.assert_code("confirmation_inputs_changed", lambda: skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "extended-deadline", deadline=self.deadline + 10))
        self.assertEqual(self.prompts, [])

    def test_unretained_and_mutated_selections_never_consume_exposure(self):
        self.api("register_confirmation")
        registration = self.register()
        context = self.prepare()
        candidate = self.candidate(context)
        self.assert_code("confirmation_candidate_mismatch",
                         lambda: self.confirm(registration, context, candidate))
        row, _ = self.evaluate(context, candidate)
        row["errors"].append({"stage": "candidate", "code": "forged"})
        self.assert_code("confirmation_candidate_mismatch",
                         lambda: self.confirm(registration, context, candidate))
        self.assertFalse((self.runtime.private / "confirmation-exposures").exists())

    def test_final_context_keeps_reference_and_closes_feedback_before_exposure(self):
        registration, context, candidate, row = self.selected()
        packet = skill_pipeline.development_feedback(
            self.runtime, context, row, source_round_id="local-20260918T060000Z-aaaaaaaaaaaa-r1")
        before = deepcopy(context)
        final = self.confirm(registration, context, candidate)
        self.assertEqual(set(final), set(context))
        self.assertIsNone(final["feedback"])
        self.assertEqual(context, before)
        allowed = {"input_sha256", "reference_sha256", "original_checks"}
        self.assertEqual({k: v for k, v in final["reference"].items() if k not in allowed},
                         {k: v for k, v in context["reference"].items() if k not in allowed})
        self.assertEqual(self.quality_calls, 2, "Confirmation must not rejudge baseline quality")
        for ctx, evaluation in ((context, row), (final, None)):
            self.assert_code("development_closed", lambda: skill_pipeline.development_feedback(
                self.runtime, ctx, evaluation, source_round_id=None))
        for parent, feedback in ((context["original"], context["feedback"]), (candidate, packet)):
            self.assert_code("development_closed", lambda: skill_pipeline.generate_candidate(
                self.runtime, "offline-model", parent, feedback, self.runtime.private / "closed",
                deadline=self.deadline))
        self.assertTrue(all("FINAL_REQUEST_SENTINEL" not in prompt for _, prompt in self.prompts))
        marker = self.runtime.private / "confirmation-exposures" / (self.final["input_sha256"] + ".json")
        self.assertTrue(marker.is_file())
        confirmed, captures = self.evaluate(final, candidate, "final-evaluation")
        self.assertEqual(confirmed["work"]["split"], "confirmation")
        self.assertEqual(captures, [context["original"], candidate])
        self.assertEqual(confirmed["decision"]["status"], "improved")
        for prompt in [p for role, p in self.prompts if role == "developer"][-2:]:
            self.assertIn(self.final["request"], prompt)
        self.assertEqual(self.check_inputs, ["VALUE = 0\n", "VALUE = 1\n", "VALUE = 1\n",
                                             "VALUE = 0\n", "VALUE = 1\n", "VALUE = 1\n"])
        self.assert_code("confirmation_already_used",
                         lambda: self.evaluate(final, candidate, "repeat-evaluation"))
        self.assert_code("confirmation_already_used",
                         lambda: self.confirm(registration, context, candidate, "repeat-prepare"))
        stored = project_evaluation.persist_replay(
            self.root / "output", confirmed, captures, None, final["reference"],
            execution_mode="offline_test", run_id="local-20260918T060000Z-bbbbbbbbbbbb")
        self.assertEqual(project_results.load_replays(self.root / "output")[
            ("project", stored["run_id"])]["evaluation"], confirmed)

    def test_capability_loss_before_and_during_confirmation_stays_unverified(self):
        registration, context, candidate, _ = self.selected()
        self.capability_active = False
        self.assert_code("confirmation_isolation_unverified",
                         lambda: self.confirm(registration, context, candidate))
        self.capability_active = True
        final = self.confirm(registration, context, candidate)
        original = self.transport.invoke.side_effect

        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.capability_active = False
            return result

        self.transport.invoke.side_effect = revoke
        self.assert_code("confirmation_isolation_unverified",
                         lambda: self.evaluate(final, candidate, "expired-boundary"))
        self.capability_active = True
        self.assert_code("confirmation_already_used",
                         lambda: self.evaluate(final, candidate, "retry-boundary"))

    def test_admission_failure_keeps_marker_and_closes_old_feedback(self):
        registration, context, candidate, _ = self.selected()
        with patch.object(project_checks, "execute", side_effect=RuntimeFailure("check_runtime_error", "offline")):
            self.assert_code("check_runtime_error", lambda: self.confirm(registration, context, candidate))
        self.assert_code("confirmation_already_used",
                         lambda: self.confirm(registration, context, candidate, "retry"))
        self.assert_code("development_closed", lambda: skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "late-generator", deadline=self.deadline))

    def test_marker_bytes_are_rechecked_before_final_models(self):
        registration, context, candidate, _ = self.selected()
        final = self.confirm(registration, context, candidate)
        marker = self.runtime.private / "confirmation-exposures" / (self.final["input_sha256"] + ".json")
        marker.write_text("{}\n")
        calls = len(self.prompts)
        self.assert_code("confirmation_inputs_changed",
                         lambda: self.evaluate(final, candidate, "tampered-marker"))
        self.assertEqual(len(self.prompts), calls)

    def test_consumed_task_blocks_a_new_runtime_before_development(self):
        registration, context, candidate, _ = self.selected()
        self.confirm(registration, context, candidate)
        self.runtime = project_evaluation.BudgetRuntime(self.transport, {
            "calls": 0, "max_calls": 20, "max_seconds": 120, "deadline": self.deadline,
            "max_ai_credits": 100,
        })
        self.runtime.execution_mode = "offline_test"
        self.quality_calls = 0
        self.assert_code("confirmation_not_registered",
                         lambda: self.confirm(registration, context, candidate, "foreign-runtime"))
        self.assert_code("confirmation_already_used", lambda: self.register("other-registration"))
        self.assertEqual(self.quality_calls, 0)

    def test_final_operational_failure_is_unverified_and_not_retryable(self):
        registration, context, candidate, _ = self.selected()
        final = self.confirm(registration, context, candidate)
        self.transport.invoke.side_effect = RuntimeFailure("timeout", "Offline model transport failure.")
        row, _ = self.evaluate(final, candidate, "failed-final")
        self.assertEqual(row["decision"]["status"], "unverified")
        self.assertEqual(row["errors"], [{"stage": "base_application", "code": "timeout"}])
        self.assert_code("confirmation_already_used",
                         lambda: self.evaluate(final, candidate, "failed-final-retry"))

    def test_observed_final_task_failure_is_rejected_not_hidden(self):
        registration, context, candidate, _ = self.selected()
        final = self.confirm(registration, context, candidate)
        calls, check = [], self.check

        def failed_candidate(*args, **kwargs):
            result = check(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                result["cases"][1]["status"] = "failed"
                result["status"] = "failed"
            return result

        with patch.object(project_checks, "execute", side_effect=failed_candidate):
            row, _ = self.evaluate(final, candidate, "rejected-final")
        self.assertEqual(row["decision"]["status"], "rejected")
        self.assertEqual(row["applications"]["candidate"]["task_outcome"], "not_satisfied")
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["checks"]["candidate"]["cases"][1]["status"], "failed")

    def test_next_use_executes_once_without_generation_quality_or_approval(self):
        execute = self.api("execute_work")
        captured = skill_pipeline.captured_files(self.project, self.bundle)
        for number in (1, 2):
            receipt, checks = execute(
                self.runtime, "offline-model", self.project, captured, self.images,
                self.runtime.private / f"use-{number}", work_item=self.work, deadline=self.deadline)
            self.assertEqual(receipt["version_id"], captured[0]["version_id"])
            self.assertEqual(receipt["staged_version_id"], captured[0]["version_id"])
            self.assertEqual(receipt["task_outcome"], "satisfied")
            self.assertEqual(checks["status"], "completed")
        self.assertEqual(self.quality_calls, 0)
        self.assertEqual(self.runtime.budget["calls"], 2)
        self.assertEqual((self.project / "api.py").read_text(), "VALUE = 0\n")
        self.assertFalse((self.runtime.private / "confirmation-exposures").exists())
        self.assert_code("confirmation_isolation_unverified", lambda: execute(
            self.runtime, "offline-model", self.project, captured, self.images,
            self.runtime.private / "bad-use", work_item=self.final, deadline=self.deadline))

    def test_next_use_protected_output_and_activation_fail_closed(self):
        execute = self.api("execute_work")
        captured = skill_pipeline.captured_files(self.project, self.bundle)
        original = self.transport.invoke.side_effect

        def forbidden(*args, **kwargs):
            result = original(*args, **kwargs)
            result["content"] = json.dumps({"files": {"tests/test_api.py": "pass\n"}})
            return result

        self.transport.invoke.side_effect = forbidden
        with self.assertRaises(RuntimeFailure):
            execute(self.runtime, "offline-model", self.project, captured, self.images,
                    self.runtime.private / "protected", work_item=self.work, deadline=self.deadline)
        self.assertEqual(self.check_inputs, [])
        self.assertIn("HIDDEN_CONFIRMATION_SENTINEL", (self.project / "tests/test_api.py").read_text())
        self.transport.invoke.side_effect = lambda *args, **kwargs: {
            **original(*args, **kwargs), "skill_activated": False}
        self.assert_code("skill_version_mismatch", lambda: execute(
            self.runtime, "offline-model", self.project, captured, self.images,
            self.runtime.private / "inactive", work_item=self.work, deadline=self.deadline))
        self.assertEqual(self.check_inputs, [])

    @unittest.skipUnless(os.environ.get("SKILLOPS_ISOLATION_TESTS") == "1",
                         "Requires real non-model Docker/native CLI isolation probes.")
    def test_real_capability_confines_all_registered_provider_roles(self):
        """Actual runtime/guide/issuance; only billable model and checker transport are simulated."""
        raw = copilot_runtime.CopilotRuntime(self.root, inherited={"PATH": os.environ["PATH"]})
        budget = {"calls": 0, "max_calls": 5, "max_seconds": 120,
                  "deadline": self.deadline, "max_ai_credits": 100}
        self.runtime = project_evaluation.BudgetRuntime(raw, budget)
        self.runtime.execution_mode = "offline_test"
        fixture = json.loads((Path(__file__).parent / "fixtures/cli-contract.json").read_text())
        external_capture = copilot_runtime.capture
        roles = []

        def model_transport(argv, **kwargs):
            if len(argv) < 2 or argv[1] != "run" or "-p" not in argv:
                return external_capture(argv, **kwargs)
            prompt = argv[argv.index("-p") + 1]
            role = ("generator" if prompt.startswith("Improve only") else
                    "developer" if prompt.startswith("Invoke /") else "judge")
            roles.append(role)
            for sentinel in ("FINAL_REQUEST_SENTINEL", "HIDDEN_CONFIRMATION_SENTINEL", "confirmation-secret-case"):
                self.assertNotIn(sentinel, prompt)
            sandbox = copilot_runtime._SANDBOX_CALLS[raw]
            forbidden = [str(self.project), str(raw.private), str(raw.home),
                         str(self.root / "confirmation-request.json")]
            for path in sandbox["work"].rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"HIDDEN_CONFIRMATION_SENTINEL", path.read_bytes())
                    self.assertNotIn(b"FINAL_REQUEST_SENTINEL", path.read_bytes())
            if role != "developer":
                self.assertEqual(list(sandbox["work"].iterdir()), [])
            probe = raw._container_capture(
                ["/usr/local/bin/node", "-e",
                 f"const fs=require('fs');for(const p of {json.dumps(forbidden)})"
                 "{if(fs.existsSync(p))process.exit(3);}"],
                state=sandbox["state"], workspace=sandbox["work"], home=sandbox["home"],
                output=sandbox["output"], timeout=10)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            if role == "developer":
                content = {"files": {"api.py": "VALUE = 1\n"}}
            elif role == "generator":
                content = {"instructions": "Apply the visible request carefully.", "addressed_findings": [],
                           "hypothesis": "Synthetic offline response."}
            else:
                content = {"workflow_clarity": {"status": "pass", "score": 3, "rationale": "Offline fixture."}}
            events = deepcopy(fixture["developer" if role == "developer" else "judge"])
            for event in events:
                if event["type"] == "assistant.message":
                    event["data"]["content"] = json.dumps(content)
            (sandbox["output"] / "usage.json").write_text(json.dumps({
                "currentModel": "gpt-6-astra", "totalNanoAiu": 100}))
            return CompletedProcess(argv, 0, "\n".join(map(json.dumps, events)), "")

        (self.root / "confirmation-request.json").write_text(self.final["request"])
        with raw.locked(), raw.confirmation_isolation(deadline=self.deadline), \
                patch.object(copilot_runtime, "capture", side_effect=model_transport), \
                patch.object(skill_guide, "evaluate_bundle", side_effect=real_evaluate_bundle):
            registration = self.api("register_confirmation")(
                self.runtime, "gpt-6-astra", self.project, self.bundle, "skillops:develop",
                self.rubric, self.images, raw.private / "register",
                development_work_item=self.work, confirmation_work_item=self.final,
                disclosure=self.disclosure, deadline=self.deadline)
            context = skill_pipeline.prepare_replay(
                self.runtime, "gpt-6-astra", self.project, self.bundle, "skillops:develop",
                self.rubric, self.images, raw.private / "prepare", work_item=self.work, deadline=self.deadline)
            generation, candidate = skill_pipeline.generate_candidate(
                self.runtime, "gpt-6-astra", context["original"], context["feedback"],
                raw.private / "generation", deadline=self.deadline)
            row, captures = skill_pipeline.evaluate_candidate(
                self.runtime, "gpt-6-astra", context, candidate, raw.private / "evaluation", deadline=self.deadline)
            self.assertTrue(registration)
            self.assertEqual(row["errors"], [])
            self.assertEqual(captures, [context["original"], candidate])
            self.assertEqual(generation["parent_version_id"], context["original"][0]["version_id"])
            for arm in ("base", "candidate"):
                self.assertTrue(row["applications"][arm]["activated"])
                self.assertEqual(row["applications"][arm]["task_outcome"], "satisfied")
        self.assertEqual(roles, ["judge", "generator", "judge", "developer", "developer"])
        self.assertEqual(budget["calls"], 5)
        self.assertFalse((raw.private / "confirmation-exposures").exists())


class SkillPipelineTests(unittest.TestCase):
    def test_all_project_b_bundles_fit_with_maximum_synthetic_candidate_bodies(self):
        """Capacity check only: fixture scores are not model evaluation evidence."""
        import copy
        import project_results
        from test_skill_assessments import fixture
        project = Path(__file__).resolve().parents[1] / "projects/project-b"
        report, _, template = fixture()
        report["project_id"] = "project-b"
        evaluated = []
        for bundle in skill_guide.discover(project):
            original, files = skill_pipeline.captured_files(project, bundle)
            candidate = skill_pipeline.candidate_files(files, {
                "instructions": "x" * 16000, "addressed_findings": [],
                "hypothesis": "Synthetic capacity check only.",
            })
            version, _ = evolution.capture_version(
                candidate, capture_scope="complete_bundle", complete_inventory=list(candidate))
            row = copy.deepcopy(template["skills"][0])
            row.update(skill_key=skill_pipeline.skill_key("project-b", bundle["path"], []),
                       source_path=bundle["path"], base_version_id=original["version_id"],
                       candidate_version_id=version["version_id"])
            for arm, captured in (("base", original), ("candidate", version)):
                row["applications"][arm].update(
                    version_id=captured["version_id"], staged_version_id=captured["version_id"])
            row["decision"] = skill_assessments.decide(row)
            evaluated.append((row, [(original, files), (version, candidate)]))
        self.assertEqual(len(evaluated), 14)
        with patch.object(project_results, "EVOLUTION_LIMIT", project_results.LIMIT), self.assertRaises(RuntimeFailure):
            skill_pipeline.attachments(report, evaluated)
        lifecycle, details = skill_pipeline.attachments(report, evaluated)
        self.assertGreater(len(project_results.encoded(lifecycle)), project_results.LIMIT)
        self.assertLessEqual(len(project_results.encoded(lifecycle)), project_results.EVOLUTION_LIMIT)
        with tempfile.TemporaryDirectory() as output:
            project_results.store(output, report)
            project_results.store_evolution(output, lifecycle)
            project_results.store_assessments(output, details)
            self.assertEqual(len(project_results.load_assessments(output)[("project-b", "123-1")]["skills"]), 14)

    def test_actual_skill_versions_drive_paired_outputs_and_regression_decision(self):
        for regression, check_error in ((False, None), (True, None), (False, "unsupported_dependencies")):
            with self.subTest(regression=regression, check_error=check_error), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                project = root / "project"
                path = project / ".github/skills/develop/SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text("---\nname: develop\ndescription: Repair code and validate it.\n---\nCheck code.\n")
                (project / "api.py").write_text("VALUE = 0\n")
                (project / "tests").mkdir()
                (project / "tests/test_api.py").write_text(
                    "import unittest\nfrom api import VALUE\n"
                    "class Api(unittest.TestCase):\n"
                    "    def test_bug(self): self.assertEqual(VALUE, 1)\n"
                    "    def test_stable(self): self.assertLessEqual(VALUE, 1)\n")
                bundle = skill_guide.discover(project)[0]
                runtime = Mock(project=root, cli="/test-only/copilot", env={})
                runtime.private = root / "private"
                runtime.private.mkdir()
                applications = []
                progress = []

                def invoke(prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
                    if role == "generator":
                        return {"content": json.dumps({
                            "instructions": "Check code and preserve every pre-existing behavior.",
                            "addressed_findings": ["workflow_clarity"],
                            "hypothesis": "Explicit preservation guidance may improve verification.",
                        })}
                    self.assertEqual(role, "developer")
                    self.assertEqual(kwargs["skill_name"], "develop")
                    source = Path(expected_skill).read_text()
                    applications.append(source)
                    candidate = "preserve every" in source
                    content = "VALUE = 2\n" if candidate and regression else "VALUE = 1\n"
                    from copilot_runtime import verify_staged_version
                    version_id = verify_staged_version(expected_skill, kwargs["expected_version"])
                    return {"content": json.dumps({"files": {"api.py": content}}),
                            "skill_version_verified": True, "staged_version_id": version_id,
                            "elapsed_seconds": 2, "usage": {"nano_aiu": {"value": 123}}}

                runtime.invoke.side_effect = invoke
                calls = []
                def quality(runtime, model, selected, rubric, artifact):
                    calls.append(selected)
                    return {"status": "pass", "static": {"findings": []},
                            "judge": {"dimensions": {"workflow_clarity": {
                                "score": 3 if len(calls) == 2 else 2, "status": "pass", "rationale": "fixture",
                            }}}}

                actual_check = project_checks.execute
                images = {"python": "sha256:" + "a" * 64}
                if os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1":
                    images["python"] = project_checks.capture(
                        ["docker", "image", "inspect", "python:3.12-slim", "--format", "{{.Id}}"]).stdout.strip()
                def check(project, plan, images, **kwargs):
                    self.assertEqual(kwargs.get("deadline"), 9999999999)
                    if os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1":
                        return actual_check(project, plan, images, **kwargs)
                    value = (project / "api.py").read_text()
                    statuses = {"stable": "failed" if "2" in value else "passed",
                                "bug": "failed" if "0" in value else "passed"}
                    return {
                        "plan_sha256": plan["sha256"], "environment_sha256": "a" * 64,
                        "protected_sha256": "b" * 64, "elapsed_seconds": 1,
                        "status": "failed" if "failed" in statuses.values() else "completed",
                        "cases": [{"id": key, "status": status} for key, status in statuses.items()], "gates": [],
                    }
                with patch.object(skill_pipeline.skill_guide, "evaluate_bundle", side_effect=quality), patch.object(
                        skill_pipeline.project_checks, "execute", side_effect=check):
                    assessed, captures = skill_pipeline.evaluate_skill(
                        runtime, "gpt-6-astra", project, bundle, "skillops:develop",
                        {"dimensions": ["workflow_clarity"]}, images, root / "run",
                        deadline=9999999999, check_error=check_error,
                        progress=lambda *event: progress.append(event))
                starts = [event[0] for event in progress if event[1] == "started"]
                ends = [event[0] for event in progress if event[1] != "started"]
                self.assertEqual(starts, ends)
                for stage in ("base_quality", "generation", "candidate_quality", "qualification"):
                    self.assertIn(stage, starts)
                self.assertEqual(assessed["decision"]["status"],
                                 "unverified" if check_error else "rejected" if regression else "improved")
                self.assertEqual(len(calls), 2)
                self.assertEqual(sum(call.args[2] == "generator" for call in runtime.invoke.call_args_list), 1)
                self.assertEqual(len(captures), 2)
                self.assertEqual(len(applications), 0 if check_error else 2)
                if not check_error:
                    self.assertNotEqual(applications[0], applications[1])
                self.assertEqual((project / "api.py").read_text(), "VALUE = 0\n")
                self.assertNotIn("preserve every", path.read_text())
                if not check_error:
                    self.assertEqual(assessed["applications"]["candidate"]["measurement"],
                                     {"cost_nano_aiu": 123, "elapsed_seconds": 2})
                else:
                    self.assertIn({"stage": "preparation", "code": check_error}, assessed["errors"])
                self.assertEqual(json.loads((root / "run/assessment.json").read_text()), assessed)
                skill_assessments.validate_skill(assessed)

    def test_proposal_cannot_edit_protected_tests_or_escape_the_project(self):
        for name in ("tests/test_api.py", "../escape.py", "/absolute.py", "package.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "api.py").write_text("original")
                with self.assertRaises(RuntimeFailure):
                    skill_pipeline.apply_output(root, {"files": {name: "replaced"}},
                                                {"api.py": "original"})
                self.assertEqual((root / "api.py").read_text(), "original")

    def test_candidate_keeps_frontmatter_and_resource_bytes(self):
        original = {"SKILL.md": b"---\nname: review\ndescription: Review code.\n---\nOld.\n",
                    "references/checks.md": b"Do not change this resource.\n"}
        changed = skill_pipeline.candidate_files(original, {
            "instructions": "Review changed behavior and retained tests.",
            "addressed_findings": [], "hypothesis": "Clearer instructions.",
        })
        self.assertEqual(changed["references/checks.md"], original["references/checks.md"])
        self.assertEqual(changed["SKILL.md"].split(b"\n---\n")[0],
                         original["SKILL.md"].split(b"\n---\n")[0])
        version, _ = evolution.capture_version(changed, capture_scope="complete_bundle",
                                                complete_inventory=list(changed))
        self.assertEqual(version["capture_scope"], "complete_bundle")

    def test_assembled_results_bind_every_skill_version_to_immutable_report(self):
        from test_skill_assessments import fixture
        import project_results
        report, lifecycle, assessment = fixture()
        captures = []
        for version in lifecycle["records"]["versions"]:
            files = {item["path"]: base64.b64decode(item["data"]) for item in lifecycle["file_contents"]
                     if item["version_id"] == version["version_id"]}
            captures.append((version, files))
        data, details = skill_pipeline.attachments(report, [(assessment["skills"][0], captures)])
        project_results.validate_evolution(data, report)
        skill_assessments.validate(details, report, data)
        self.assertEqual(len(data["records"]["versions"]), 2)
        self.assertEqual(data["bindings"][0]["skill_key"], assessment["skills"][0]["skill_key"])
        self.assertEqual(data["records"]["sources"][0]["path"], ".github/skills/develop")

    def test_prior_validated_source_association_reuses_identity_without_name_guessing(self):
        prior = [{"project_id": "sample_repo", "skills": [
            {"source_path": ".github/skills/review", "skill_key": "auto:existing"}]}]
        self.assertEqual(skill_pipeline.skill_key("sample_repo", ".github/skills/review", prior), "auto:existing")
        self.assertNotEqual(skill_pipeline.skill_key("sample_repo", ".claude/skills/review", prior), "auto:existing")
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.skill_key("sample_repo", "../outside", prior)

    def test_same_project_path_without_history_always_produces_the_same_skill_key(self):
        expected = "path:" + sha256(b"sample_repo\n.github/skills/develop").hexdigest()[:24]
        first = skill_pipeline.skill_key("sample_repo", ".github/skills/develop", [])
        second = skill_pipeline.skill_key("sample_repo", ".github/skills/develop", [])
        self.assertEqual(first, second)
        self.assertEqual(first, expected)
        self.assertTrue(evolution.matches(evolution.SKILL_KEY, first))

    def test_different_paths_and_projects_produce_different_skill_keys_without_history(self):
        inputs = [("sample_repo", ".github/skills/develop"), ("sample_repo", ".claude/skills/develop"),
                  ("project-a", ".github/skills/develop"), ("sample_repo", ".github/skills/review")]
        keys = {skill_pipeline.skill_key(project_id, path, []) for project_id, path in inputs}
        self.assertEqual(len(keys), len(inputs))

    def test_existing_auto_and_registered_history_keys_are_preserved_without_cross_project_aliasing(self):
        for existing in ("auto:existing", "skillops:develop"):
            with self.subTest(existing=existing):
                prior = [{"project_id": "sample_repo", "skills": [
                    {"source_path": ".github/skills/develop", "skill_key": existing}]}]
                before = json.dumps(prior, sort_keys=True)
                self.assertEqual(skill_pipeline.skill_key("sample_repo", ".github/skills/develop", prior), existing)
                self.assertEqual(skill_pipeline.skill_key("project-a", ".github/skills/develop", prior),
                                 skill_pipeline.skill_key("project-a", ".github/skills/develop", []))
                self.assertEqual(json.dumps(prior, sort_keys=True), before)

    def test_deterministic_key_rejects_invalid_project_ids_and_noncanonical_paths(self):
        for project_id, path in (("bad\nproject", "skills/develop"), ("../project", "skills/develop"),
                                 ("sample_repo", "../outside"), ("sample_repo", "skills\\develop")):
            with self.subTest(project_id=project_id, path=path), self.assertRaises(RuntimeFailure):
                skill_pipeline.skill_key(project_id, path, [])


if __name__ == "__main__":
    unittest.main()
