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
