"""Offline orchestration checks; provider doubles are not measured Skill outcomes."""

from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from copilot_runtime import RuntimeFailure
import evolution_records as evolution
from evaluation_reporting import run_stage
import project_evaluation
import project_results
import skill_assessments
import skill_pipeline


def digest(value):
    return sha256(project_results.encoded(value)).hexdigest()


def capture(body):
    return evolution.capture_version(
        {"SKILL.md": b"---\nname: develop\ndescription: Develop.\n---\n\n" + body + b"\n",
         "references/rules.md": b"Keep protected tests unchanged.\n"},
        capture_scope="complete_bundle", complete_inventory=["SKILL.md", "references/rules.md"])


class IterationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("skill_iterations"),
                             "The bounded feedback-cycle module has not been implemented.")
        import skill_iterations
        self.module = skill_iterations
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.artifact = self.root / "cycle"
        self.original = capture(b"Original instructions.")
        self.candidates = [capture(b"Candidate one."), capture(b"Candidate two.")]
        self.cycle_id = "local-20260917T130000Z-123456789abc"
        self.budget = {"calls": 0, "max_calls": 100, "deadline": time.monotonic() + 120}
        self.limits = {"max_invocations": 100, "max_seconds": 120,
                       "max_ai_credits_per_session": None}
        self.raw = Mock(private=self.root, env={})
        self.raw.invoke.return_value = {"usage": {}, "content": "{}"}
        self.runtime = project_evaluation.BudgetRuntime(self.raw, self.budget)
        self.work = {
            "task_id": "development-task", "input_sha256": "1" * 64, "split": "development",
            "checks": {"plan_sha256": "2" * 64, "protected_sha256": "3" * 64,
                       "required_case_ids": ["repair"], "required_gate_ids": []},
        }
        self.reference = {
            "schema_version": 1, "project_id": "project-a", "skill_key": "path:develop",
            "source_path": ".github/skills/develop", "source_commit": "a" * 40,
            "project_tree_sha256": "4" * 64, "input_sha256": self.work["input_sha256"],
            "original_version_id": self.original[0]["version_id"],
            "rubric_sha256": "5" * 64, "quality_context_sha256": "6" * 64,
            "evaluator_sha256": "7" * 64, "policy_sha256": "8" * 64,
            "plan_sha256": "2" * 64, "environment_sha256": "9" * 64,
            "protected_sha256": "3" * 64,
            "original_checks": self.observation("failed"),
            "base_quality": self.quality(2),
        }
        self.reference["reference_sha256"] = digest(self.reference)
        # Opaque contexts deliberately do not establish a private provider schema.
        self.context = object()
        self.confirmation = object()
        self.confirm_work = deepcopy(self.work)
        self.confirm_work.update(task_id="confirmation-task", input_sha256="b" * 64, split="confirmation")
        self.confirm_work["checks"]["required_case_ids"] = ["other-case"]
        self.confirm_reference = deepcopy(self.reference)
        self.confirm_reference["input_sha256"] = self.confirm_work["input_sha256"]
        self.confirm_reference["original_checks"] = self.observation("passed", "other-case")
        self.confirm_reference["reference_sha256"] = digest({
            key: value for key, value in self.confirm_reference.items() if key != "reference_sha256"})
        self.statuses = ["not_improved", "improved"]
        self.confirm_status = "not_improved"
        self.generated, self.evaluated, self.saved = [], [], []
        self.store_error = None
        self.generation_error = None
        self.evaluation_error = None
        self.confirmation_factory = Mock(return_value=self.confirmation)
        self.stack.enter_context(patch.object(
            self.module, "_context_inputs", side_effect=self.context_inputs))
        self.stack.enter_context(patch.object(
            self.module, "_authorized_limits", side_effect=lambda budget: self.authorized_limits(budget)))
        self.project_feedback = self.stack.enter_context(patch.object(
            self.module, "_development_feedback", side_effect=self.feedback))
        self.generate_mock = self.stack.enter_context(patch.object(
            skill_pipeline, "generate_candidate", side_effect=self.generate, create=True))
        self.evaluate_mock = self.stack.enter_context(patch.object(
            skill_pipeline, "evaluate_candidate", side_effect=self.evaluate, create=True))
        self.stack.enter_context(patch.object(
            skill_assessments, "decide_replay",
            side_effect=lambda row, *, work_item: deepcopy(row["decision"]), create=True))

    def quality(self, score):
        return {"status": "completed", "rubric_sha256": "5" * 64, "context_sha256": "6" * 64,
                "dimensions": [{"id": "clarity", "score": score}], "findings": []}

    def observation(self, status, case="repair"):
        return {"status": "completed" if status == "passed" else "failed",
                "plan_sha256": "2" * 64, "protected_sha256": "3" * 64,
                "environment_sha256": "9" * 64, "elapsed_seconds": 1,
                "cases": [{"id": case, "status": status}], "gates": []}

    def context_inputs(self, context):
        self.assertTrue(context is self.context or context is self.confirmation)
        if context is self.context:
            return self.reference, self.original, self.work, "offline_test"
        return self.confirm_reference, self.original, self.confirm_work, "offline_test"

    def authorized_limits(self, budget):
        self.assertIs(budget, self.budget)
        return deepcopy(self.limits)

    def feedback(self, context, evaluation, source_round_id):
        self.assertIs(context, self.context)
        return {"schema_version": 1, "input_sha256": self.work["input_sha256"],
                "source_round_id": source_round_id,
                "quality": deepcopy(evaluation["quality"]["candidate"] if evaluation else self.reference["base_quality"]),
                "checks": deepcopy(evaluation["checks"]["candidate"] if evaluation else self.reference["original_checks"]),
                "application": deepcopy(evaluation["applications"]["candidate"]) if evaluation else None,
                "decision": deepcopy(evaluation["decision"]) if evaluation else None}

    def generate(self, runtime, model, parent, feedback, artifact, *, deadline):
        self.assertIs(runtime, self.runtime)
        self.assertEqual(deadline, self.budget["deadline"])
        self.generated.append((deepcopy(parent), deepcopy(feedback)))
        runtime.invoke("test-only-generation", model, "generator", self.root, artifact)
        if self.generation_error:
            raise self.generation_error
        candidate = deepcopy(self.candidates[len(self.generated) - 1])
        return {"parent_version_id": parent[0]["version_id"], "feedback_sha256": digest(feedback),
                "addressed_findings": ["clarity"], "hypothesis": "Offline test hypothesis."}, candidate

    def evaluate(self, runtime, model, context, candidate, artifact, *, deadline, progress=None):
        self.assertIs(runtime, self.runtime)
        self.assertEqual(deadline, self.budget["deadline"])
        self.evaluated.append((context, deepcopy(candidate)))
        run_stage(progress, "candidate_quality", lambda: runtime.invoke(
            "test-only-evaluation", model, "judge", self.root, artifact))
        if self.evaluation_error:
            raise self.evaluation_error
        reference, original, work, _ = self.context_inputs(context)
        status = self.confirm_status if context is self.confirmation else self.statuses[len(self.generated) - 1]
        application = lambda version: {
            "version_id": version, "staged_version_id": version, "work_sha256": work["input_sha256"],
            "output_sha256": "c" * 64, "activated": True, "changed": True, "task_outcome": "satisfied",
            "measurement": {"cost_nano_aiu": 10, "elapsed_seconds": 1}}
        row = {
            "skill_key": reference["skill_key"], "source_path": reference["source_path"],
            "base_version_id": original[0]["version_id"], "candidate_version_id": candidate[0]["version_id"],
            "work": {**deepcopy(work), "provenance": "recorded"},
            "reference_sha256": reference["reference_sha256"],
            "quality": {"base": deepcopy(reference["base_quality"]),
                        "candidate": self.quality(3 if status == "improved" else 2)},
            "applications": {"base": application(original[0]["version_id"]),
                             "candidate": application(candidate[0]["version_id"])},
            "checks": {"original": deepcopy(reference["original_checks"]),
                       "base": self.observation("passed", work["checks"]["required_case_ids"][0]),
                       "candidate": self.observation("passed", work["checks"]["required_case_ids"][0])},
            "decision": {"policy_id": "replay-v1", "status": status,
                         "reasons": ["observed_rejection"] if status == "rejected" else [],
                         "regression": {"status": "passed", "reasons": []}},
            "errors": [],
        }
        return row, [deepcopy(original), deepcopy(candidate)]

    def persist(self, evaluation, captures, generation, reference):
        if self.store_error:
            raise self.store_error
        saved = deepcopy((evaluation, captures, generation, reference))
        self.saved.append(saved)
        return {"project_id": "project-a",
                "run_id": f"local-20260917T130001Z-{len(self.saved):012x}",
                "path": "replay-evaluation.json", "sha256": digest(saved[0])}

    def run_cycle(self, **kwargs):
        arguments = {"cycle_id": self.cycle_id, "max_rounds": 2, "budget": self.budget,
                     "confirmation_context": self.confirmation_factory, "persist_round": self.persist}
        arguments.update(kwargs)
        return self.module.run_cycle(self.runtime, "test-only-model", self.context, self.artifact, **arguments)

    def test_previous_measured_feedback_changes_parent_not_original_comparison(self):
        for first in ("not_improved", "rejected"):
            with self.subTest(first=first):
                self.statuses[0] = first
                result = self.run_cycle(confirmation_context=None)
                self.assertEqual(len(self.generated), 2)
                self.assertEqual(self.generated[0][0], self.original)
                self.assertEqual(self.generated[1][0], self.candidates[0])
                feedback = self.generated[1][1]
                prior = self.saved[0][0]
                self.assertEqual(feedback["quality"], prior["quality"]["candidate"])
                self.assertEqual(feedback["checks"], prior["checks"]["candidate"])
                self.assertEqual(feedback["application"], prior["applications"]["candidate"])
                self.assertEqual(feedback["decision"], prior["decision"])
                self.assertEqual(feedback["source_round_id"], self.cycle_id + "-r1")
                self.assertEqual(result["rounds"][1]["feedback_sha256"], digest(feedback))
                self.assertEqual(result["rounds"][1]["feedback_source_round_id"], self.cycle_id + "-r1")
                for evaluation, _, _, reference in self.saved:
                    self.assertEqual(evaluation["base_version_id"], self.original[0]["version_id"])
                    self.assertEqual(evaluation["work"]["input_sha256"], self.work["input_sha256"])
                    self.assertEqual(reference, self.reference)
                self.assertEqual(result["stop_reason"], "improved")
                self.assertEqual(result["confirmation_status"], "not_run")
                self.assertNotIn("report_sha256", result)
                self.artifact = self.root / "other-cycle"
                self.generated.clear()
                self.evaluated.clear()
                self.saved.clear()

    def test_early_improvement_stops_before_second_generation(self):
        self.statuses = ["improved"]
        result = self.run_cycle(confirmation_context=None)
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(result["stop_reason"], "improved")
        self.assertEqual(result["selected_candidate_version_id"], self.candidates[0][0]["version_id"])

    def test_last_round_non_improvement_stops_at_n(self):
        self.statuses = ["rejected", "not_improved"]
        result = self.run_cycle()
        self.assertEqual(len(self.generated), 2)
        self.assertEqual(result["stop_reason"], "max_rounds")
        self.assertEqual(result["rounds"][0]["stop_reason"], None)
        self.assertEqual(result["rounds"][1]["stop_reason"], "max_rounds")
        self.assertIsNone(result["selected_candidate_version_id"])
        self.confirmation_factory.assert_not_called()

    def test_default_n_is_one_and_invalid_limits_are_rejected(self):
        arguments = {"cycle_id": self.cycle_id, "budget": self.budget,
                     "confirmation_context": None, "persist_round": self.persist}
        result = self.module.run_cycle(self.runtime, "test-only-model", self.context, self.artifact, **arguments)
        self.assertEqual(result["max_rounds"], 1)
        for value in (True, 0, 11, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(RuntimeFailure):
                self.run_cycle(max_rounds=value)

    def test_no_change_and_stage_errors_beat_round_limit(self):
        for code, expected in (("unchanged_candidate", "no_change"), ("call_limit", "call_limit"),
                               ("time_limit", "time_limit"), ("credit_limit", "credit_limit"),
                               ("skill_inputs_changed", "input_changed"), ("cli_failed", "runtime_error")):
            with self.subTest(code=code):
                self.artifact = self.root / code
                self.generation_error = RuntimeFailure(code, "Test-only failure.")
                result = self.run_cycle(max_rounds=1)
                self.assertEqual(result["stop_reason"], expected)
                self.assertEqual(len(result["rounds"]), 1)
                self.assertIsNone(result["rounds"][0]["candidate_version_id"])
                self.assertIsNone(result["rounds"][0]["evaluation_ref"])
                self.assertIsNone(result["rounds"][0]["decision"])
                self.assertEqual(self.budget["calls"], len(self.generated))
                self.assertEqual(self.saved, [])
                self.confirmation_factory.assert_not_called()

    def test_unverified_evaluation_is_not_retried(self):
        self.statuses = ["unverified"]
        result = self.run_cycle()
        self.assertEqual(result["stop_reason"], "evaluation_unverified")
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(len(self.generated), 1)

    def test_confirmation_once_uses_frozen_selected_bytes_and_never_becomes_feedback(self):
        result = self.run_cycle()
        self.confirmation_factory.assert_called_once_with()
        self.assertEqual(len(self.generated), 2)
        self.assertEqual(len(self.evaluated), 3)
        self.assertEqual(self.evaluated[-1], (self.confirmation, self.candidates[1]))
        self.assertEqual(result["confirmation_status"], "passed")
        self.assertEqual(result["confirmation_ref"]["run_id"], f"local-20260917T130001Z-{3:012x}")
        self.assertIsNone(self.saved[-1][2])
        self.assertEqual(self.saved[-1][3], self.confirm_reference)
        self.assertEqual(self.project_feedback.call_count, 2)
        for _, packet in self.generated:
            self.assertNotIn("other-case", json.dumps(packet))
            self.assertNotIn(self.confirm_work["input_sha256"], json.dumps(packet))

    def test_confirmation_rejected_or_unverified_never_restarts_search(self):
        self.statuses = ["improved"]
        for status, expected in (("rejected", "failed"), ("unverified", "unverified")):
            with self.subTest(status=status):
                self.confirm_status = status
                self.artifact = self.root / status
                result = self.run_cycle()
                self.assertEqual(result["confirmation_status"], expected)
                self.assertEqual(result["stop_reason"], "improved")
                self.assertEqual(len(result["rounds"]), 1)
                self.assertEqual(len(self.generated), 1)
                self.generated.clear()
                self.evaluated.clear()
                self.saved.clear()

    def test_confirmation_rejects_unfrozen_reference_and_overlapping_tasks(self):
        self.statuses = ["improved"]
        changes = [
            lambda: self.confirm_work.update(task_id=self.work["task_id"]),
            lambda: self.confirm_work["checks"].update(required_case_ids=["repair"]),
            lambda: self.confirm_reference.update(evaluator_sha256="e" * 64),
            lambda: self.confirm_reference.update(environment_sha256="f" * 64),
            lambda: self.confirm_work.update(split="development"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                before = deepcopy((self.confirm_reference, self.confirm_work))
                change()
                self.confirm_reference["reference_sha256"] = digest({
                    key: value for key, value in self.confirm_reference.items() if key != "reference_sha256"})
                self.artifact = self.root / f"bad-confirmation-{index}"
                result = self.run_cycle()
                self.assertEqual(result["confirmation_status"], "unverified")
                self.assertIsNone(result["confirmation_ref"])
                self.assertEqual(len(self.evaluated), 1)
                self.confirm_reference, self.confirm_work = before
                self.generated.clear()
                self.evaluated.clear()
                self.saved.clear()

    def test_confirm_isolation_failure_is_explicit_and_does_not_generate_again(self):
        self.statuses = ["improved"]
        self.confirmation_factory.side_effect = RuntimeFailure(
            "confirmation_isolation_unverified", "Test-only withheld context failure.")
        result = self.run_cycle()
        self.assertEqual(result["confirmation_status"], "unverified")
        self.assertIsNone(result["confirmation_ref"])
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(len(self.saved), 1)
        self.assertIn("confirmation_isolation_unverified",
                      (self.artifact / "confirmation/failure.json").read_text())

    def test_prepare_generate_evaluate_and_confirmation_share_one_live_budget(self):
        self.raw.invoke("offline-preparation-marker")
        self.runtime.invoke("test-only-initial-preparation")
        self.assertEqual(self.budget["calls"], 1)
        def prepare_confirmation():
            self.assertIs(self.runtime.budget, self.budget)
            self.runtime.invoke("test-only-confirmation-preparation")
            return self.confirmation
        self.confirmation_factory.side_effect = prepare_confirmation
        result = self.run_cycle()
        self.assertEqual(self.budget["calls"], 7)
        self.assertEqual(result["budget"], self.limits)
        self.assertEqual(result["confirmation_status"], "passed")

    def test_call_budget_consumed_by_preparation_prevents_extra_attempts(self):
        self.budget["max_calls"] = self.limits["max_invocations"] = 2
        self.runtime.invoke("test-only-preparation")
        result = self.run_cycle(max_rounds=1)
        self.assertEqual(result["stop_reason"], "call_limit")
        self.assertEqual(self.budget["calls"], 2)
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(result["rounds"][0]["candidate_version_id"], self.candidates[0][0]["version_id"])
        self.assertEqual(self.saved, [])

    def test_exhausted_budget_before_generation_does_not_invent_an_attempt(self):
        for reason in ("call_limit", "time_limit"):
            with self.subTest(reason=reason):
                self.artifact = self.root / reason
                self.budget["calls"] = self.budget["max_calls"] if reason == "call_limit" else 0
                self.budget["deadline"] = time.monotonic() - 1 if reason == "time_limit" else time.monotonic() + 120
                result = self.run_cycle()
                self.assertEqual(result["stop_reason"], reason)
                self.assertEqual(result["rounds"], [])
                self.assertEqual(self.generated, [])

    def test_budget_exhaustion_blocks_confirmation_without_erasing_development_success(self):
        self.statuses = ["improved"]
        self.budget["max_calls"] = self.limits["max_invocations"] = 2
        result = self.run_cycle()
        self.assertEqual(result["stop_reason"], "improved")
        self.assertEqual(result["confirmation_status"], "unverified")
        self.confirmation_factory.assert_not_called()
        self.assertIsNone(result["confirmation_ref"])

    def test_failed_invocation_retains_usage_unknown_is_not_zero_and_recorder_is_restored(self):
        error = RuntimeFailure("credit_limit", "Actual test-double runtime limit.")
        error.usage = {"nano_aiu": {"value": 123}}
        self.raw.invoke.side_effect = error
        original_recorder = self.runtime.recorder
        result = self.run_cycle()
        self.assertEqual(result["stop_reason"], "credit_limit")
        self.assertEqual(self.budget["calls"], 1)
        self.assertIs(self.runtime.recorder, original_recorder)
        stages = json.loads((self.artifact / "r1/generation-metrics.json").read_text())
        call = stages["generation"]["invocations"][0]
        self.assertEqual(call["status"], "failed")
        self.assertEqual(call["usage"]["nano_aiu"], 123)
        self.assertIn(None, call["usage"].values())

    def test_per_session_credit_cap_is_forwarded_not_added_up(self):
        self.budget["max_ai_credits"] = self.limits["max_ai_credits_per_session"] = 30
        self.raw.invoke.return_value = {"usage": {"nano_aiu": {"value": 10 ** 20}}}
        result = self.run_cycle()
        self.assertEqual(result["stop_reason"], "improved")
        self.assertEqual(result["budget"]["max_ai_credits_per_session"], 30)
        for invocation in self.raw.invoke.call_args_list:
            self.assertEqual(invocation.kwargs["max_ai_credits"], 30)

    def test_changed_work_input_and_budget_reset_stop_before_evaluation(self):
        for name in ("work", "budget"):
            with self.subTest(name=name):
                self.artifact = self.root / name
                self.budget["calls"] = 0
                old_work = deepcopy(self.work)
                def changed(*args, **kwargs):
                    output = self.generate(*args, **kwargs)
                    if name == "work":
                        self.work["input_sha256"] = "f" * 64
                    else:
                        self.budget["max_calls"] += 1
                    return output
                self.generate_mock.side_effect = changed
                result = self.run_cycle()
                self.assertEqual(result["stop_reason"], "input_changed")
                self.assertEqual(self.evaluated, [])
                self.work = old_work
                self.budget["max_calls"] = self.limits["max_invocations"]
                self.generated.clear()

    def test_evaluation_error_and_cancellation_keep_attempt_but_not_completed_decision(self):
        for error, expected in ((OSError("test-only I/O error"), "runtime_error"),
                                (RuntimeFailure("time_limit", "Test-only expiration."), "time_limit"),
                                (KeyboardInterrupt(), "cancelled")):
            with self.subTest(expected=expected):
                self.artifact = self.root / expected
                self.evaluation_error = error
                try:
                    result = self.run_cycle(max_rounds=1)
                except KeyboardInterrupt:
                    self.fail("Cancellation must retain the attempt with an explicit cancelled stop.")
                self.assertEqual(result["stop_reason"], expected)
                self.assertEqual(len(result["rounds"]), 1)
                self.assertEqual(result["rounds"][0]["candidate_version_id"], self.candidates[0][0]["version_id"])
                self.assertIsNone(result["rounds"][0]["decision"])
                self.assertEqual(self.saved, [])
                self.generated.clear()
                self.evaluated.clear()

    def test_persistence_failure_is_raised_not_returned_as_terminal_cycle(self):
        self.store_error = OSError("Test-only storage unavailable.")
        with self.assertRaises(OSError):
            self.run_cycle()
        self.confirmation_factory.assert_not_called()
        self.assertFalse((self.artifact / "cycle.json").exists())
        self.assertFalse((self.artifact / "r1/round.json").exists())

    def test_confirmation_storage_failure_also_escapes(self):
        self.statuses = ["improved"]
        def store(evaluation, captures, generation, reference):
            if generation is None:
                raise RuntimeFailure("storage_conflict", "Test-only immutable storage conflict.")
            return self.persist(evaluation, captures, generation, reference)
        with self.assertRaises(RuntimeFailure):
            self.run_cycle(persist_round=store)
        self.assertEqual(len(self.saved), 1)

    def test_invalid_storage_reference_and_duplicate_run_are_rejected(self):
        def duplicate(evaluation, captures, generation, reference):
            stored = self.persist(evaluation, captures, generation, reference)
            stored["run_id"] = self.cycle_id
            return stored
        with self.assertRaises(RuntimeFailure):
            self.run_cycle(persist_round=duplicate)

    def test_public_payload_has_only_contract_fields_and_no_adoption_side_effects(self):
        marker = self.root / "active.json"
        marker.write_text('{"test-only-active": "unchanged"}\n')
        with patch.object(Path, "unlink", side_effect=AssertionError("Unexpected deletion")):
            result = self.run_cycle()
        self.assertEqual(marker.read_text(), '{"test-only-active": "unchanged"}\n')
        self.assertEqual(set(result), set(
            "schema_version project_id run_id execution_mode cycle_id skill_key source_path input_sha256 "
            "reference_sha256 original_version_id max_rounds budget rounds stop_reason selected_candidate_version_id "
            "confirmation_ref confirmation_status".split()))
        self.assertEqual(set(result["rounds"][0]), set(
            "round_id round_number run_id parent_version_id candidate_version_id input_sha256 reference_sha256 "
            "feedback_source_round_id feedback_sha256 evaluation_ref decision stop_reason".split()))

    def test_missing_providers_fail_closed_without_production_doubles(self):
        self.stack.close()
        for function, args, code in (
            (self.module._context_inputs, (self.context,), "replay_context_provider_missing"),
            (self.module._authorized_limits, (self.budget,), "authorized_limits_provider_missing"),
            (self.module._development_feedback, (self.context, None, None), "feedback_provider_missing"),
        ):
            with self.subTest(code=code), self.assertRaises(RuntimeFailure) as caught:
                function(*args)
            self.assertEqual(caught.exception.code, code)

    def test_authorized_budget_bounds_and_identity_are_validated_without_model_calls(self):
        for field, values in (("max_invocations", (True, 0, 1001, 1.5)),
                              ("max_seconds", (True, 0, 7201, float("inf"))),
                              ("max_ai_credits_per_session", (True, -1, 29, float("nan")))):
            for value in values:
                with self.subTest(field=field, value=value):
                    before = deepcopy(self.limits)
                    self.limits[field] = value
                    with self.assertRaises(RuntimeFailure):
                        self.run_cycle()
                    self.limits = before
        with self.assertRaises(RuntimeFailure):
            self.run_cycle(budget=dict(self.budget))
        self.assertEqual(self.generated, [])
        self.assertEqual(self.budget["calls"], 0)

    def test_actual_elapsed_time_exhaustion_after_generation_beats_n(self):
        clock = [100.0]
        self.budget["deadline"] = 120.0
        def slow_generation(*args, **kwargs):
            result = self.generate(*args, **kwargs)
            clock[0] = 121.0
            return result
        self.generate_mock.side_effect = slow_generation
        with patch("time.monotonic", side_effect=lambda: clock[0]):
            result = self.run_cycle(max_rounds=1)
        self.assertEqual(result["stop_reason"], "time_limit")
        self.assertEqual(result["rounds"][0]["candidate_version_id"], self.candidates[0][0]["version_id"])
        self.assertEqual(self.evaluated, [])
        self.assertEqual(self.budget["calls"], 1)

    def test_completed_error_evidence_beats_an_improved_label_and_is_retained(self):
        self.statuses = ["improved"]
        def failed(*args, **kwargs):
            row, captures = self.evaluate(*args, **kwargs)
            row["errors"] = [{"stage": "candidate_application", "code": "time_limit"}]
            row["applications"]["candidate"]["measurement"]["cost_nano_aiu"] = None
            return row, captures
        self.evaluate_mock.side_effect = failed
        result = self.run_cycle(max_rounds=1)
        self.assertEqual(result["stop_reason"], "time_limit")
        self.assertIsNone(result["selected_candidate_version_id"])
        self.assertEqual(len(self.saved), 1)
        self.assertIsNone(self.saved[0][0]["applications"]["candidate"]["measurement"]["cost_nano_aiu"])

    def test_changed_reference_after_evaluation_is_not_persisted(self):
        def changed(*args, **kwargs):
            evaluated = self.evaluate(*args, **kwargs)
            self.reference["evaluator_sha256"] = "d" * 64
            return evaluated
        self.evaluate_mock.side_effect = changed
        result = self.run_cycle()
        self.assertEqual(result["stop_reason"], "input_changed")
        self.assertEqual(self.saved, [])

    def test_shared_decision_must_recompute_the_improvement(self):
        self.statuses = ["improved"]
        with patch.object(skill_assessments, "decide_replay", return_value={
            "policy_id": "replay-v1", "status": "unverified", "reasons": [], "regression": {}}):
            result = self.run_cycle()
        self.assertEqual(result["stop_reason"], "runtime_error")
        self.assertIsNone(result["selected_candidate_version_id"])
        self.assertEqual(self.saved, [])

    def test_generation_must_bind_the_actual_parent_and_feedback(self):
        for field in ("parent_version_id", "feedback_sha256"):
            with self.subTest(field=field):
                self.artifact = self.root / field
                self.generated.clear()
                def forged(*args, **kwargs):
                    generation, candidate = self.generate(*args, **kwargs)
                    generation[field] = "e" * 64
                    return generation, candidate
                self.generate_mock.side_effect = forged
                result = self.run_cycle()
                self.assertEqual(result["stop_reason"], "runtime_error")
                self.assertEqual(self.evaluated, [])

    def test_projection_is_a_provider_boundary_not_a_name_based_filter(self):
        self.statuses = ["not_improved", "not_improved"]
        def evaluate_with_protected_case(*args, **kwargs):
            row, captures = self.evaluate(*args, **kwargs)
            row["checks"]["candidate"]["cases"].append({"id": "ordinary-name", "status": "failed"})
            row["decision"]["reasons"].append("private-diagnostic")
            return row, captures
        self.evaluate_mock.side_effect = evaluate_with_protected_case
        def project(context, evaluation, source_round_id):
            packet = self.feedback(context, evaluation, source_round_id)
            if evaluation:
                # Test-only visibility rule, not an agreed production projection API.
                packet["checks"]["cases"] = packet["checks"]["cases"][:1]
                packet["decision"]["reasons"] = []
            return packet
        self.project_feedback.side_effect = project
        result = self.run_cycle()
        self.assertNotIn("ordinary-name", json.dumps(self.generated[1][1]))
        self.assertNotIn("private-diagnostic", json.dumps(self.generated[1][1]))
        self.assertIn("ordinary-name", json.dumps(self.saved[0][0]))
        self.assertEqual(result["rounds"][1]["feedback_sha256"], digest(self.generated[1][1]))

    def test_private_evidence_write_failure_cannot_return_a_completed_cycle(self):
        real_write = Path.write_bytes
        def fail_metrics(path, data):
            if path.name == "generation-metrics.json":
                raise OSError("Test-only disk full.")
            return real_write(path, data)
        with patch.object(Path, "write_bytes", fail_metrics), self.assertRaises(RuntimeFailure) as caught:
            self.run_cycle()
        self.assertEqual(caught.exception.code, "iteration_evidence_write_failed")
        self.assertEqual(self.saved, [])

    def test_round_storage_failure_keeps_previous_run_without_a_completed_cycle(self):
        def fail_second(evaluation, captures, generation, reference):
            if self.saved:
                raise OSError("Test-only second store failed.")
            return self.persist(evaluation, captures, generation, reference)
        with self.assertRaises(OSError):
            self.run_cycle(persist_round=fail_second)
        self.assertEqual(len(self.saved), 1)
        self.assertTrue((self.artifact / "r1/round.json").is_file())
        self.assertFalse((self.artifact / "r2/round.json").exists())
        self.assertFalse((self.artifact / "cycle.json").exists())

    def test_symlink_and_public_artifact_locations_are_not_written(self):
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        for path in (self.root / "link/run", self.root.parent / "outside-private"):
            with self.subTest(path=path), self.assertRaises(RuntimeFailure):
                self.artifact = path
                self.run_cycle()
        self.assertEqual(self.generated, [])


if __name__ == "__main__":
    unittest.main()
