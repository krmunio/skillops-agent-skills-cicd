"""Bounded development-feedback search; never approval, activation or public storage.

Contract revision 1.2: c194ea8f43f0a64b98d96e5adababbffccc6d49e.
Uses the real replay provider and shared budget/decision validators. Retained
evaluation objects stay unchanged for private feedback issuance. Confirmation
is still blocked by the provider with confirmation_isolation_unverified.
"""

from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import time

from copilot_runtime import MIN_AI_CREDITS, RuntimeFailure
import evolution_records as evolution
from evolution_records import exact, matches, require
from evaluation_reporting import run_stage
from evaluation_telemetry import Recorder
import project_results
import skill_assessments
import skill_pipeline


def _context_inputs(context):
    exact(context, "reference work_item original source_project project plan images rubric sources "
          "execution_mode feedback_scope feedback")
    return context["reference"], context["original"], context["work_item"], context["execution_mode"]


def _authorized_limits(budget):
    from project_evaluation import budget_limits
    return budget_limits(budget)


def _development_feedback(runtime, context, evaluation, source_round_id):
    if evaluation is None:
        require(source_round_id is None, "invalid_feedback_round")
        return context["feedback"]
    return skill_pipeline.development_feedback(runtime, context, evaluation, source_round_id=source_round_id)


def _digest(value):
    return sha256(project_results.encoded(value)).hexdigest()


def _capture(value):
    require(isinstance(value, (tuple, list)) and len(value) == 2, "invalid_capture")
    version, files = value
    evolution.validate_version(version)
    require(version["capture_scope"] == "complete_bundle", "incomplete_capture")
    actual, copied = evolution.capture_version(
        files, capture_scope="complete_bundle", complete_inventory=list(files))
    require(actual == version, "skill_inputs_changed")
    return actual, copied


def _stop(code):
    if code in ("call_limit", "time_limit", "credit_limit", "cancelled"):
        return code
    if code == "unchanged_candidate":
        return "no_change"
    if code in ("input_changed", "inputs_changed", "skill_inputs_changed", "replay_inputs_changed",
                "work_inputs_changed", "check_inputs_changed", "quality_inputs_changed",
                "execution_mode_changed", "skill_version_mismatch", "protected_skill_changed"):
        return "input_changed"
    return "runtime_error"


def _budget_stop(budget):
    if budget["calls"] >= budget["max_calls"]:
        return "call_limit"
    if time.monotonic() >= budget["deadline"]:
        return "time_limit"
    return None


def _validate_budget(runtime, budget, limits):
    exact(limits, "max_invocations max_seconds max_ai_credits_per_session")
    require(type(limits["max_invocations"]) is int and 1 <= limits["max_invocations"] <= 1000
            and type(limits["max_seconds"]) is int and 1 <= limits["max_seconds"] <= 7200,
            "invalid_budget")
    credit = limits["max_ai_credits_per_session"]
    require(credit is None or (type(credit) in (int, float) and math.isfinite(credit)
                              and credit >= MIN_AI_CREDITS), "invalid_budget")
    require(runtime.budget is budget and type(budget.get("max_calls")) is int
            and budget["max_calls"] == limits["max_invocations"]
            and budget.get("max_seconds") == limits["max_seconds"]
            and budget.get("max_ai_credits") == credit, "budget_identity_mismatch")
    require(type(budget.get("calls")) is int and 0 <= budget["calls"] <= budget["max_calls"]
            and type(budget.get("deadline")) in (int, float)
            and math.isfinite(budget["deadline"]), "invalid_budget")


def _measured(runtime, path, function, stage=None):
    # Each phase has its own existing Recorder; the shared counter/deadline never reset.
    previous = runtime.recorder
    recorder = Recorder()
    runtime.recorder = recorder
    try:
        return (run_stage(recorder, stage, lambda: function(recorder))
                if stage else function(recorder))
    finally:
        runtime.recorder = previous
        try:
            path.write_bytes(project_results.encoded(recorder.stages))
        except OSError as error:
            raise RuntimeFailure("iteration_evidence_write_failed",
                                 "Invocation evidence could not be retained; the cycle is incomplete.") from error


def _failure(folder, error):
    if isinstance(error, RuntimeFailure) and error.code == "iteration_evidence_write_failed":
        raise error
    code = ("cancelled" if isinstance(error, KeyboardInterrupt) else
            error.code if isinstance(error, RuntimeFailure) else "io_error")
    require(matches(r"[a-z0-9_]{1,128}", code), "invalid_error")
    (folder / "failure.json").write_bytes(project_results.encoded({"code": code}))
    return _stop(code)


def _feedback(runtime, context, previous, source_round_id, input_sha256):
    # The provider owns explicit evidence visibility; never infer it from names.
    packet = _development_feedback(runtime, context, previous, source_round_id)
    exact(packet, "schema_version input_sha256 source_round_id quality checks application decision")
    require(type(packet["schema_version"]) is int and packet["schema_version"] == 1,
            "invalid_feedback")
    require(packet["input_sha256"] == input_sha256 and packet["source_round_id"] == source_round_id,
            "invalid_feedback")
    require(packet["quality"] is not None and packet["checks"] is not None, "feedback_unverified")
    skill_assessments.quality(packet["quality"])
    exact(packet["checks"], "cases gates")
    if previous is None:
        require(packet["application"] is None and packet["decision"] is None, "invalid_feedback")
    else:
        require(packet["application"] == previous["applications"]["candidate"]
                and packet["quality"] == previous["quality"]["candidate"]
                and packet["decision"] == previous["decision"], "invalid_feedback")
    require(len(project_results.encoded(packet)) <= project_results.LIMIT, "feedback_limit")
    return deepcopy(packet)


def _evaluation(row, captures, reference, original, candidate, work):
    exact(row, "skill_key source_path base_version_id candidate_version_id work reference_sha256 "
          "quality applications checks decision errors")
    require(row["skill_key"] == reference["skill_key"] and row["source_path"] == reference["source_path"]
            and row["reference_sha256"] == reference["reference_sha256"]
            and row["base_version_id"] == original[0]["version_id"]
            and row["candidate_version_id"] == candidate[0]["version_id"], "inputs_changed")
    expected_work = {key: work[key] for key in ("task_id", "input_sha256", "split", "checks")}
    require(row["work"] == {**expected_work, "provenance": "recorded"}, "inputs_changed")
    require(row["quality"]["base"] == reference["base_quality"]
            and row["checks"]["original"] == reference["original_checks"], "inputs_changed")
    loaded = {}
    for item in captures:
        captured = _capture(item)
        require(captured[0]["version_id"] not in loaded, "duplicate_capture")
        loaded[captured[0]["version_id"]] = captured
    require(loaded.get(original[0]["version_id"]) == original
            and loaded.get(candidate[0]["version_id"]) == candidate, "skill_inputs_changed")
    decide = getattr(skill_assessments, "decide_replay", None)
    require(callable(decide), "replay_decision_provider_missing")
    decision = decide(deepcopy(row), work_item=deepcopy(work))
    exact(decision, "policy_id status reasons regression")
    require(decision["policy_id"] == "replay-v1" and decision["status"] in
            ("improved", "not_improved", "rejected", "unverified")
            and row["decision"] == decision, "invalid_replay_decision")
    return row, deepcopy(captures)


def _persist(persist_round, evaluation, captures, generation, reference, seen):
    # Persistence errors deliberately escape: retained runs are not a finished cycle.
    value = persist_round(deepcopy(evaluation), deepcopy(captures), deepcopy(generation), deepcopy(reference))
    exact(value, "project_id run_id path sha256")
    require(value["project_id"] == reference["project_id"]
            and matches(project_results.RUN, value["run_id"])
            and value["run_id"] not in seen and value["path"] == "replay-evaluation.json"
            and matches(evolution.DIGEST, value["sha256"]), "invalid_round_reference")
    seen.add(value["run_id"])
    return deepcopy(value)


def run_cycle(runtime, model, context, artifact, *, cycle_id, max_rounds=1,
              budget, confirmation_context, persist_round):
    """Return an unbound terminal payload, with durable round references only.

    The caller owns preparation, the live budget and final report binding.
    Provider/persistence failures must never be replaced by successful test data.
    """
    require(type(max_rounds) is int and 1 <= max_rounds <= 10, "invalid_round_limit")
    require(matches(project_results.RUN, cycle_id), "invalid_cycle_id")
    require(isinstance(budget, dict) and runtime.budget is budget, "budget_identity_mismatch")
    require(callable(persist_round) and (confirmation_context is None or callable(confirmation_context)),
            "invalid_cycle_callback")
    limits = _authorized_limits(budget)
    _validate_budget(runtime, budget, limits)
    limits = deepcopy(limits)
    reference, original, work, execution_mode = _context_inputs(context)
    reference, work, original = deepcopy(reference), deepcopy(work), _capture(original)
    require(work["split"] == "development" and reference["input_sha256"] == work["input_sha256"]
            and reference["original_version_id"] == original[0]["version_id"], "inputs_changed")
    require(reference["reference_sha256"] == _digest({
        key: value for key, value in reference.items() if key != "reference_sha256"}), "inputs_changed")
    require(execution_mode in ("live", "offline_test", "sample"), "invalid_execution_mode")
    for name in ("generate_candidate", "evaluate_candidate"):
        require(callable(getattr(skill_pipeline, name, None)), "replay_provider_missing")
    artifact = Path(artifact)
    require(not any(path.is_symlink() for path in (artifact, *artifact.parents))
            and artifact.resolve().is_relative_to(Path(runtime.private).resolve()), "unsafe_artifact")
    artifact.mkdir(parents=True, exist_ok=False)
    deadline, last_calls = budget["deadline"], budget["calls"]

    def check_inputs():
        nonlocal last_calls
        current_reference, current_original, current_work, current_mode = _context_inputs(context)
        require(current_reference == reference and _capture(current_original) == original
                and current_work == work and current_mode == execution_mode, "inputs_changed")
        require(runtime.budget is budget and budget["deadline"] == deadline
                and budget["max_calls"] == limits["max_invocations"]
                and budget["max_seconds"] == limits["max_seconds"]
                and budget.get("max_ai_credits") == limits["max_ai_credits_per_session"]
                and type(budget["calls"]) is int and last_calls <= budget["calls"] <= budget["max_calls"],
                "inputs_changed")
        last_calls = budget["calls"]

    def admit_stage():
        check_inputs()
        reason = _budget_stop(budget)
        if reason:
            raise RuntimeFailure(reason, "The shared authorized budget is exhausted.")

    cycle = {
        "schema_version": 1, "project_id": reference["project_id"], "run_id": cycle_id,
        "execution_mode": execution_mode, "cycle_id": cycle_id, "skill_key": reference["skill_key"],
        "source_path": reference["source_path"], "input_sha256": work["input_sha256"],
        "reference_sha256": reference["reference_sha256"], "original_version_id": original[0]["version_id"],
        "max_rounds": max_rounds, "budget": deepcopy(limits), "rounds": [], "stop_reason": None,
        "selected_candidate_version_id": None, "confirmation_ref": None, "confirmation_status": "not_run",
    }
    parent, previous, source_round_id = original, None, None
    seen = {cycle_id}
    for number in range(1, max_rounds + 1):
        reason = _budget_stop(budget)
        if reason:
            cycle["stop_reason"] = reason
            break
        folder = artifact / f"r{number}"
        folder.mkdir()
        try:
            packet = _feedback(runtime, context, previous, source_round_id, work["input_sha256"])
            admit_stage()
        except (RuntimeFailure, OSError, KeyboardInterrupt) as error:
            cycle["stop_reason"] = _failure(folder, error)
            break
        (folder / "feedback.json").write_bytes(project_results.encoded(packet))
        try:
            admit_stage()
        except (RuntimeFailure, OSError, KeyboardInterrupt) as error:
            cycle["stop_reason"] = _failure(folder, error)
            break
        round_row = {
            "round_id": f"{cycle_id}-r{number}", "round_number": number, "run_id": None,
            "parent_version_id": parent[0]["version_id"], "candidate_version_id": None,
            "input_sha256": work["input_sha256"], "reference_sha256": reference["reference_sha256"],
            "feedback_source_round_id": source_round_id, "feedback_sha256": _digest(packet),
            "evaluation_ref": None, "decision": None, "stop_reason": None,
        }
        cycle["rounds"].append(round_row)
        evaluated = None
        try:
            generation, candidate = _measured(
                runtime, folder / "generation-metrics.json", lambda progress: skill_pipeline.generate_candidate(
                    runtime, model, deepcopy(parent), deepcopy(packet), folder / "generation", deadline=deadline),
                "generation")
            check_inputs()
            candidate = _capture(candidate)
            require(candidate[0]["version_id"] != parent[0]["version_id"], "unchanged_candidate")
            exact(generation, "parent_version_id feedback_sha256 addressed_findings hypothesis")
            require(generation["parent_version_id"] == parent[0]["version_id"]
                    and generation["feedback_sha256"] == round_row["feedback_sha256"], "invalid_generation")
            findings = generation["addressed_findings"]
            require(isinstance(findings, list) and len(findings) <= 128, "invalid_generation")
            for identifier in findings:
                skill_assessments.text(identifier, 160)
            known = {item["id"] for field in ("dimensions", "findings") for item in packet["quality"][field]}
            require(len(findings) == len(set(findings)) and set(findings) <= known, "invalid_generation")
            skill_assessments.text(generation["hypothesis"])
            round_row["candidate_version_id"] = candidate[0]["version_id"]
            admit_stage()
            evaluated, captures = _measured(
                runtime, folder / "evaluation-metrics.json", lambda progress: skill_pipeline.evaluate_candidate(
                    runtime, model, context, deepcopy(candidate), folder / "evaluation",
                    deadline=deadline, progress=progress))
            check_inputs()
            require(time.monotonic() < deadline, "time_limit")
            evaluated, captures = _evaluation(evaluated, captures, reference, original, candidate, work)
        except (RuntimeFailure, OSError, KeyboardInterrupt) as error:
            reason = _failure(folder, error)
            evaluated = None
        else:
            saved = _persist(persist_round, evaluated, captures, generation, reference, seen)
            round_row.update(run_id=saved["run_id"], evaluation_ref=saved, decision=evaluated["decision"])
            if evaluated["errors"]:
                reason = _stop(evaluated["errors"][0]["code"])
            elif evaluated["decision"]["status"] == "improved":
                reason = "improved"
                cycle["selected_candidate_version_id"] = candidate[0]["version_id"]
            elif evaluated["decision"]["status"] == "unverified":
                reason = "evaluation_unverified"
            else:
                reason = _budget_stop(budget) or ("max_rounds" if number == max_rounds else None)
        round_row["stop_reason"] = reason
        (folder / "round.json").write_bytes(project_results.encoded(round_row))
        if reason:
            cycle["stop_reason"] = reason
            break
        parent, previous, source_round_id = candidate, evaluated, round_row["round_id"]
    if cycle["rounds"] and cycle["rounds"][-1]["stop_reason"] is None:
        # Finalize the cycle boundary without rewriting an already stored evaluation.
        cycle["rounds"][-1]["stop_reason"] = cycle["stop_reason"]
    if cycle["selected_candidate_version_id"] is not None and confirmation_context is not None:
        selected = _capture(candidate)
        folder = artifact / "confirmation"
        folder.mkdir()
        cycle["confirmation_status"] = "unverified"
        try:
            admit_stage()
            final_context = _measured(runtime, folder / "preparation-metrics.json",
                                      lambda progress: confirmation_context(), "work")
            check_inputs()
            final_reference, final_original, final_work, final_mode = _context_inputs(final_context)
            final_reference, final_work = deepcopy(final_reference), deepcopy(final_work)
            require(_capture(final_original) == original and final_mode == execution_mode, "inputs_changed")
            require(final_work["split"] == "confirmation" and final_work["task_id"] != work["task_id"]
                    and final_work["input_sha256"] != work["input_sha256"]
                    and not set(final_work["checks"]["required_case_ids"]) & set(work["checks"]["required_case_ids"]),
                    "confirmation_isolation_unverified")
            require(final_reference["input_sha256"] == final_work["input_sha256"]
                    and final_reference["reference_sha256"] == _digest({
                        key: value for key, value in final_reference.items() if key != "reference_sha256"}),
                    "inputs_changed")
            allowed = {"input_sha256", "reference_sha256", "original_checks"}
            require({key: value for key, value in final_reference.items() if key not in allowed}
                    == {key: value for key, value in reference.items() if key not in allowed}, "inputs_changed")
            admit_stage()
            confirmed, captures = _measured(
                runtime, folder / "evaluation-metrics.json", lambda progress: skill_pipeline.evaluate_candidate(
                    runtime, model, final_context, deepcopy(selected), folder / "evaluation",
                    deadline=deadline, progress=progress))
            check_inputs()
            current_reference, current_original, current_work, current_mode = _context_inputs(final_context)
            require(current_reference == final_reference and _capture(current_original) == original
                    and current_work == final_work and current_mode == final_mode, "inputs_changed")
            require(time.monotonic() < deadline, "time_limit")
            confirmed, captures = _evaluation(confirmed, captures, final_reference, original, selected, final_work)
        except (RuntimeFailure, OSError, KeyboardInterrupt) as error:
            _failure(folder, error)
        else:
            cycle["confirmation_ref"] = _persist(persist_round, confirmed, captures, None, final_reference, seen)
            if not confirmed["errors"]:
                cycle["confirmation_status"] = {
                    "improved": "passed", "not_improved": "passed",
                    "rejected": "failed", "unverified": "unverified",
                }[confirmed["decision"]["status"]]
    return cycle
