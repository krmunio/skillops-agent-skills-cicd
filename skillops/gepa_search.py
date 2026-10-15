"""Optional official GEPA search; development scores never confer adoption authority."""

from copy import deepcopy
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
import logging
import math
from types import SimpleNamespace

from copilot_runtime import RuntimeFailure
from evolution_records import exact, matches, require, DIGEST
import project_checks
import project_results
import skill_iterations as iterations
import skill_pipeline
import skill_assessments


GEPA_VERSION = "0.1.4"


def optimizer():
    try:
        installed = version("gepa")
    except PackageNotFoundError as error:
        raise RuntimeFailure("gepa_not_installed", "Install skillops/requirements-gepa.txt.") from error
    require(installed == GEPA_VERSION, "unsupported_gepa_version")
    import gepa
    return gepa


def case_scores(observation, work):
    """Binary observed outcomes, with mandatory gates; unknowns are not failed cases."""
    require(observation is not None and observation["status"] in ("completed", "failed"),
            "evaluation_unverified")
    values = {}
    for field, required in (("cases", "required_case_ids"), ("gates", "required_gate_ids")):
        rows = {row["id"]: row["status"] for row in observation[field]}
        require(len(rows) == len(observation[field]), "evaluation_unverified")
        require(all(rows.get(key) in ("passed", "failed") for key in work["checks"][required]),
                "evaluation_unverified")
        values[field] = [int(rows[key] == "passed") for key in work["checks"][required]]
    return values["cases"] if all(values["gates"]) else [0] * len(values["cases"])


def _retain_failure(function):
    # GEPA 0.1.4 retries/swallows proposer errors even with raise_on_exception=True.
    @wraps(function)
    def guarded(self, *args, **kwargs):
        if self.failure is not None:
            raise self.failure
        try:
            return function(self, *args, **kwargs)
        except (RuntimeFailure, OSError) as error:
            self.failure = error
            raise
    return guarded


class SearchAdapter:
    def __init__(self, runtime, model, context, artifact, cycle, persist_round, check_inputs,
                 validation_context=None):
        self.runtime, self.model, self.context = runtime, model, context
        self.artifact, self.cycle = artifact, cycle
        self.persist_round, self.check_inputs = persist_round, check_inputs
        self.deadline = runtime.budget["deadline"]
        self.reference, self.original, self.work, _ = iterations._context_inputs(context)
        self.cases = self.work["checks"]["required_case_ids"]
        self.validation_context = validation_context
        self.validation_cases = ([] if validation_context is None else
                                 validation_context["work_item"]["checks"]["required_case_ids"])
        self.validation_scores = {}
        self.seed = {"instructions": skill_pipeline.original_body(self.original[1])}
        self.captures = {self.seed["instructions"]: self.original}
        self.records = {}
        self.seed_observation = None
        self.seed_application = None
        self.seed_scores = None
        self.seen = {cycle["cycle_id"]}
        self.metric_requests = 0
        self.state = None
        self.failure = None
        self.persistence_failed = False

    def admit(self):
        if self.failure is not None:
            raise self.failure
        self.check_inputs()
        reason = iterations._budget_stop(self.runtime.budget)
        if reason:
            raise RuntimeFailure(reason, "The shared authorized budget is exhausted.")

    def stopped(self, state):
        self.state = state
        self.check_inputs()
        return (self.failure is not None or len(self.cycle["rounds"]) >= self.cycle["max_rounds"]
                or iterations._budget_stop(self.runtime.budget) is not None)

    @_retain_failure
    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        self.admit()
        exact(candidate, "instructions")
        body = candidate["instructions"]
        validation = bool(self.validation_context is not None and batch
                          and all(case in self.validation_cases for case in batch))
        require(body in self.captures and (validation or all(case in self.cases for case in batch)),
                "unknown_gepa_candidate")
        require(self.metric_requests + len(batch) <= self.cycle["optimizer"]["max_metric_calls"],
                "metric_limit")
        self.metric_requests += len(batch)
        captured = self.captures[body]
        if body == self.seed["instructions"]:
            if self.seed_scores is None:
                self.seed_application, self.seed_observation = iterations._measured(
                    self.runtime, self.artifact / "seed-metrics.json",
                    lambda progress: skill_pipeline._replay_application(
                        self.runtime, self.model, self.context, captured, self.artifact / "seed",
                        deadline=self.deadline), "base_application")
                self.check_inputs()
                self.seed_scores = case_scores(self.seed_observation, self.work)
                project_results.atomic_json(
                    self.artifact / "seed.json",
                    {"application": self.seed_application, "checks": self.seed_observation}, immutable=True)
            scores, record = self.seed_scores, None
        else:
            record = self.records[body]
            if record["evaluation"] is None:
                folder = self.artifact / f"r{record['round']['round_number']}"
                row, captures = iterations._measured(
                    self.runtime, folder / "evaluation-metrics.json",
                    lambda progress: skill_pipeline.evaluate_candidate(
                        self.runtime, self.model, self.context, captured, folder / "evaluation",
                        deadline=self.deadline, progress=progress))
                self.check_inputs()
                row, captures = iterations._evaluation(
                    row, captures, self.reference, self.original, captured, self.work)
                # Persist before inspecting the score; incomplete results remain evidence.
                try:
                    saved = iterations._persist(
                        self.persist_round, row, captures, record["generation"], self.reference, self.seen)
                except (RuntimeFailure, OSError):
                    self.persistence_failed = True
                    raise
                record["evaluation"] = row
                record["round"].update(run_id=saved["run_id"], evaluation_ref=saved, decision=row["decision"])
                if row["errors"]:
                    raise RuntimeFailure(row["errors"][0]["code"], "Candidate evaluation is incomplete.")
                require(all(value is not None for group in ("quality", "checks", "applications")
                            for value in row[group].values()), "evaluation_unverified")
                for arm in ("base", "candidate"):
                    receipt = row["applications"][arm]
                    require(receipt["activated"] and receipt["version_id"] == receipt["staged_version_id"]
                            == row[f"{arm}_version_id"] and receipt["work_sha256"] == self.work["input_sha256"],
                            "evaluation_unverified")
                record["scores"] = case_scores(row["checks"]["candidate"], self.work)
            scores = record["scores"]
        cases = self.cases
        if validation:
            if body not in self.validation_scores:
                name = "seed" if record is None else f"r{record['round']['round_number']}"
                application, observed = iterations._measured(
                    self.runtime, self.artifact / (name + "-validation-metrics.json"),
                    lambda progress: skill_pipeline._replay_application(
                        self.runtime, self.model, self.validation_context, captured,
                        self.artifact / (name + "-validation"), deadline=self.deadline), "candidate_application")
                self.check_inputs()
                evidence = {"application": application, "checks": observed}
                project_results.atomic_json(
                    self.artifact / (name + "-validation.json"), evidence, immutable=True)
                self.cycle["optimizer"]["validation"]["evaluations"].append(evidence)
                self.validation_scores[body] = case_scores(observed, self.validation_context["work_item"])
            scores, cases = self.validation_scores[body], self.validation_cases
        indexes = [cases.index(case) for case in batch]
        outputs = [{"version_id": captured[0]["version_id"], "case_id": case} for case in batch]
        # Only private issuance tokens and visible development feedback reach the proposer.
        # GEPA's batch fallback requests traces even for valset scoring; never issue them there.
        traces = ([{"body": body, "case_id": case} for case in batch]
                  if capture_traces and not validation else None)
        return EvaluationBatch(outputs=outputs, scores=[scores[index] for index in indexes], trajectories=traces)

    @_retain_failure
    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        require(components_to_update == ["instructions"] and eval_batch.trajectories
                and all(row["body"] == candidate["instructions"] and row["case_id"] in self.cases
                        for row in eval_batch.trajectories),
                "invalid_gepa_feedback")
        body = candidate["instructions"]
        record = self.records.get(body)
        feedback = iterations._development_feedback(
            self.runtime, self.context, None if record is None else record["evaluation"],
            None if record is None else record["round"]["round_id"])
        return {"instructions": [feedback]}

    @_retain_failure
    def propose_new_texts(self, candidate, reflective_dataset, components_to_update):
        self.admit()
        require(components_to_update == ["instructions"], "invalid_gepa_component")
        require(len(self.cycle["rounds"]) < self.cycle["max_rounds"], "proposal_limit")
        body = candidate["instructions"]
        parent = self.captures[body]
        packet = reflective_dataset["instructions"][0]
        number = len(self.cycle["rounds"]) + 1
        folder = self.artifact / f"r{number}"
        folder.mkdir()
        round_row = {
            "round_id": f"{self.cycle['cycle_id']}-r{number}", "round_number": number, "run_id": None,
            "parent_version_id": parent[0]["version_id"], "candidate_version_id": None,
            "input_sha256": self.work["input_sha256"], "reference_sha256": self.reference["reference_sha256"],
            "feedback_source_round_id": packet["source_round_id"],
            "feedback_sha256": iterations._digest(packet), "evaluation_ref": None, "decision": None,
            "stop_reason": None,
        }
        self.cycle["rounds"].append(round_row)
        project_results.atomic_json(folder / "feedback.json", packet, immutable=True)
        generation, captured = iterations._measured(
            self.runtime, folder / "generation-metrics.json",
            lambda progress: skill_pipeline.generate_candidate(
                self.runtime, self.model, parent, packet, folder / "generation", deadline=self.deadline),
            "generation")
        self.check_inputs()
        captured = iterations._capture(captured)
        require(generation["parent_version_id"] == parent[0]["version_id"]
                and generation["feedback_sha256"] == round_row["feedback_sha256"], "invalid_generation")
        new_body = skill_pipeline.original_body(captured[1])
        require(new_body not in self.captures, "unchanged_candidate")
        round_row["candidate_version_id"] = captured[0]["version_id"]
        self.captures[new_body] = captured
        self.records[new_body] = {"round": round_row, "generation": generation, "evaluation": None, "scores": None}
        return {"instructions": new_body}


def run_cycle(runtime, model, context, artifact, *, cycle_id, max_rounds=1,
              budget, confirmation_context, persist_round, validation_context=None, validation_dataset_sha256=None):
    gepa = optimizer()
    require(type(max_rounds) is int and 1 <= max_rounds <= 10, "invalid_round_limit")
    limits = iterations._authorized_limits(budget)
    iterations._validate_budget(runtime, budget, limits)
    reference, original, work, mode = iterations._context_inputs(context)
    require(work["split"] == "development" and 2 <= len(work["checks"]["required_case_ids"]) <= 128,
            "gepa_requires_development_cases")
    original = iterations._capture(original)
    frozen = deepcopy((reference, original, work, mode))
    require((validation_context is None) == (validation_dataset_sha256 is None), "validation_selection_required")
    validation_frozen = deepcopy(validation_context)
    if validation_context is not None:
        require(matches(DIGEST, validation_dataset_sha256), "invalid_validation_dataset")
        skill_pipeline._check_replay(runtime, validation_context, model)
        skill_assessments.validate_validation_pair(
            work, validation_context["work_item"], project=context["source_project"], original=original)
        require(all(validation_context[key] == context[key] for key in
                    ("original", "source_project", "plan", "images", "rubric", "execution_mode")),
                "validation_inputs_changed")
    deadline, last_calls = budget["deadline"], budget["calls"]
    search_finished = False
    artifact = skill_pipeline._private_artifact(runtime, artifact)

    def check_inputs():
        nonlocal last_calls
        require(iterations._context_inputs(context) == frozen, "inputs_changed")
        if validation_context is not None:
            require(validation_context == validation_frozen, "validation_inputs_changed")
            skill_pipeline._require_isolation(runtime)
            if not search_finished:
                skill_pipeline._check_replay(runtime, validation_context, model)
        require(runtime.budget is budget and budget["deadline"] == deadline
                and iterations._authorized_limits(budget) == limits
                and last_calls <= budget["calls"] <= budget["max_calls"], "inputs_changed")
        last_calls = budget["calls"]

    training_ids = list(work["checks"]["required_case_ids"])
    case_ids = (training_ids if validation_context is None else
                list(validation_context["work_item"]["checks"]["required_case_ids"]))
    require(len(case_ids) <= 128, "invalid_optimizer_cases")
    metric_limit = (len(training_ids) * (1 + 3 * max_rounds)
                    + (len(case_ids) * (1 + max_rounds) if validation_context is not None else 0))
    cycle = {
        "schema_version": 2 if validation_context is None else 3,
        "project_id": reference["project_id"], "run_id": cycle_id,
        "execution_mode": mode, "cycle_id": cycle_id, "skill_key": reference["skill_key"],
        "source_path": reference["source_path"], "input_sha256": work["input_sha256"],
        "reference_sha256": reference["reference_sha256"], "original_version_id": original[0]["version_id"],
        "max_rounds": max_rounds, "budget": limits, "rounds": [], "stop_reason": None,
        "selected_candidate_version_id": None, "confirmation_ref": None, "confirmation_status": "not_run",
        "optimizer": {
            "name": "gepa", "version": GEPA_VERSION, "strategy": "pareto", "seed": 0,
            "validation_scope": "development_reuse" if validation_context is None else "independent",
            "case_ids": case_ids,
            "reference": deepcopy(reference), "work_checks": deepcopy(work["checks"]),
            "max_metric_calls": metric_limit, "metric_requests": 0,
            "pool": [], "scores": [], "frontier": {}, "recommended_version_id": None,
            "seed_evaluation": None,
        },
    }
    if validation_context is not None:
        cycle["optimizer"]["validation"] = {
            "dataset_sha256": validation_dataset_sha256,
            "work": {key: deepcopy(validation_context["work_item"][key]) for key in
                     ("schema_version", "project_id", "task_id", "split", "source_commit",
                      "project_tree_sha256", "input_sha256", "checks")},
            "evaluations": [],
        }
    adapter = SearchAdapter(runtime, model, context, artifact, cycle, persist_round, check_inputs, validation_context)
    result = None
    try:
        result = gepa.optimize(
            seed_candidate=adapter.seed, trainset=training_ids, valset=case_ids, adapter=adapter,
            candidate_selection_strategy="pareto", frontier_type="instance",
            reflection_minibatch_size=len(training_ids), skip_perfect_score=False,
            acceptance_criterion="improvement_or_equal", use_merge=False,
            max_metric_calls=cycle["optimizer"]["max_metric_calls"], stop_callbacks=adapter.stopped,
            seed=0, raise_on_exception=True, logger=SimpleNamespace(log=logging.getLogger(__name__).info),
        )
        if adapter.failure is not None:
            raise adapter.failure
        check_inputs()
        cycle["stop_reason"] = iterations._budget_stop(budget) or "search_complete"
    except RuntimeFailure as error:
        if adapter.persistence_failed or error.code in ("iteration_evidence_write_failed", "invalid_round_reference"):
            raise
        cycle["stop_reason"] = ("evaluation_unverified" if error.code == "evaluation_unverified"
                                else iterations._stop(error.code))
        project_results.atomic_json(artifact / "failure.json", {"code": error.code}, immutable=True)
    except KeyboardInterrupt:
        cycle["stop_reason"] = "cancelled"
        project_results.atomic_json(artifact / "failure.json", {"code": "cancelled"}, immutable=True)
    if result is None and adapter.state is not None:
        from gepa.core.result import GEPAResult
        result = GEPAResult.from_state(adapter.state)
    meta = cycle["optimizer"]
    meta["metric_requests"] = adapter.metric_requests
    if adapter.seed_observation is not None:
        meta["seed_evaluation"] = {"application": adapter.seed_application, "checks": adapter.seed_observation}
    if result is not None:
        meta["pool"] = [adapter.captures[item["instructions"]][0]["version_id"] for item in result.candidates]
        meta["scores"] = [[int(row[index]) for index in range(len(case_ids))] for row in result.val_subscores]
        meta["frontier"] = {case: sorted(result.per_val_instance_best_candidates[index])
                            for index, case in enumerate(case_ids)}
        meta["recommended_version_id"] = meta["pool"][result.best_idx]
        body = result.best_candidate["instructions"]
        record = adapter.records.get(body)
        if (cycle["stop_reason"] == "search_complete" and record is not None
                and record["evaluation"] is not None and record["evaluation"]["decision"]["status"] == "improved"):
            cycle["selected_candidate_version_id"] = meta["recommended_version_id"]
    if cycle["rounds"]:
        cycle["rounds"][-1]["stop_reason"] = cycle["stop_reason"]
        for row in cycle["rounds"]:
            project_results.atomic_json(
                artifact / f"r{row['round_number']}" / "round.json", row, immutable=True)
    project_results.atomic_json(artifact / "optimizer.json", meta, immutable=True)
    selected = next((capture for capture in adapter.captures.values()
                     if capture[0]["version_id"] == cycle["selected_candidate_version_id"]), None)
    search_finished = True
    iterations.confirm_selected(
        runtime, model, context, artifact, cycle, selected, budget=budget,
        confirmation_context=confirmation_context, persist_round=persist_round,
        seen=adapter.seen, check_inputs=check_inputs)
    return cycle


def validate_optimizer(cycle, evaluated_rounds, original_ref):
    """Validate public search evidence without importing the optional optimizer."""
    meta = cycle["optimizer"]
    independent = cycle["schema_version"] == 3
    exact(meta, "name version strategy seed validation_scope case_ids reference work_checks "
          "max_metric_calls metric_requests pool scores frontier recommended_version_id seed_evaluation"
          + (" validation" if independent else ""))
    require(meta["name"] == "gepa" and meta["version"] == GEPA_VERSION
            and meta["strategy"] == "pareto" and type(meta["seed"]) is int and meta["seed"] == 0
            and meta["validation_scope"] == ("independent" if independent else "development_reuse"), "invalid_optimizer")
    cases = meta["case_ids"]
    require(isinstance(cases, list) and (1 if independent else 2) <= len(cases) <= 128
            and all(isinstance(c, str) for c in cases)
            and len(set(cases)) == len(cases), "invalid_optimizer_cases")
    checks, ref = meta["work_checks"], meta["reference"]
    skill_assessments._work_checks(checks)
    require(2 <= len(checks["required_case_ids"]) <= 128, "optimizer_work_mismatch")
    require(independent or checks["required_case_ids"] == cases, "optimizer_work_mismatch")
    require(isinstance(ref, dict) and ref.get("reference_sha256") == cycle["reference_sha256"]
            and ref["reference_sha256"] == iterations._digest(
                {key: value for key, value in ref.items() if key != "reference_sha256"})
            and all(ref.get(key) == cycle[key] for key in (
                "project_id", "skill_key", "source_path", "input_sha256", "original_version_id")),
            "optimizer_reference_mismatch")
    require(original_ref is None or ref == original_ref, "optimizer_reference_mismatch")
    limit = (len(checks["required_case_ids"]) * (1 + 3 * cycle["max_rounds"])
             + (len(cases) * (1 + cycle["max_rounds"]) if independent else 0))
    require(type(meta["max_metric_calls"]) is int and meta["max_metric_calls"] == limit
            and type(meta["metric_requests"]) is int
            and 0 <= meta["metric_requests"] <= meta["max_metric_calls"], "invalid_optimizer_budget")
    pool, scores = meta["pool"], meta["scores"]
    require(isinstance(pool, list) and isinstance(scores, list) and len(pool) == len(scores)
            and len(pool) <= len(evaluated_rounds) + 1 and all(isinstance(v, str) for v in pool)
            and len(set(pool)) == len(pool), "invalid_optimizer_pool")
    by_version = {}
    for row in evaluated_rounds.values():
        require(row["work"]["checks"] == checks, "optimizer_work_mismatch")
        require(row["candidate_version_id"] not in by_version, "duplicate_optimizer_candidate")
        by_version[row["candidate_version_id"]] = row
    validation_scores = (validate_validation(meta, cycle, by_version) if independent else None)
    if not pool:
        require(scores == [] and meta["frontier"] == {} and meta["recommended_version_id"] is None
                and cycle["selected_candidate_version_id"] is None
                and cycle["stop_reason"] != "search_complete", "optimizer_selection_mismatch")
        return None
    require(pool[0] == cycle["original_version_id"] and all(v in by_version for v in pool[1:]),
            "optimizer_pool_mismatch")
    require(pool[1:] == [identifier for identifier in by_version if identifier in pool],
            "optimizer_pool_order_mismatch")
    for round_row in cycle["rounds"]:
        parent, candidate = round_row["parent_version_id"], round_row["candidate_version_id"]
        require(parent in pool and (candidate not in pool or pool.index(parent) < pool.index(candidate)),
                "optimizer_parent_mismatch")
    seed = meta["seed_evaluation"]
    exact(seed, "application checks")
    project_checks.validate_observation(seed["checks"])
    require(all(seed["checks"][key] == ref[key] for key in
                ("plan_sha256", "protected_sha256", "environment_sha256")), "optimizer_seed_mismatch")
    receipt = seed["application"]
    exact(receipt, "version_id staged_version_id work_sha256 output_sha256 activated changed task_outcome measurement")
    require(receipt["version_id"] == receipt["staged_version_id"] == pool[0]
            and receipt["work_sha256"] == cycle["input_sha256"] and receipt["activated"] is True
            and type(receipt["changed"]) is bool and matches(DIGEST, receipt["output_sha256"])
            and receipt["task_outcome"] == project_checks.replay_outcome(seed["checks"], checks),
            "optimizer_seed_mismatch")
    exact(receipt["measurement"], "cost_nano_aiu elapsed_seconds")
    require(all(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0)
                for value in receipt["measurement"].values()), "optimizer_seed_mismatch")
    expected = [case_scores(seed["checks"], {"checks": checks})]
    for identifier in pool[1:]:
        row = by_version[identifier]
        require(not row["errors"] and all(
            item is not None for group in ("quality", "checks", "applications") for item in row[group].values()),
            "optimizer_incomplete_candidate")
        expected.append(case_scores(row["checks"]["candidate"], row["work"]))
    if independent:
        require(set(pool) <= set(validation_scores), "optimizer_validation_missing")
        expected = [validation_scores[identifier] for identifier in pool]
    require(all(isinstance(row, list) and len(row) == len(cases)
                and all(type(score) is int and score in (0, 1) for score in row) for row in scores)
            and scores == expected, "optimizer_scores_mismatch")
    frontier = {case: [i for i, row in enumerate(scores) if row[j] == max(s[j] for s in scores)]
                for j, case in enumerate(cases)}
    require(meta["frontier"] == frontier, "optimizer_frontier_mismatch")
    best = max(range(len(pool)), key=lambda index: sum(scores[index]))
    recommended = pool[best]
    require(meta["recommended_version_id"] == recommended, "optimizer_selection_mismatch")
    row = by_version.get(recommended)
    selected = (recommended if cycle["stop_reason"] == "search_complete" and row is not None
                and row["decision"]["status"] == "improved" else None)
    require(cycle["selected_candidate_version_id"] == selected, "optimizer_selection_mismatch")
    return selected


def validate_validation(meta, cycle, by_version):
    """Recompute selection scores from bound receipts, not optimizer claims."""
    validation, ref = meta["validation"], meta["reference"]
    exact(validation, "dataset_sha256 work evaluations")
    require(matches(DIGEST, validation["dataset_sha256"]), "invalid_validation_dataset")
    work = validation["work"]
    exact(work, "schema_version project_id task_id split source_commit project_tree_sha256 input_sha256 checks")
    require(type(work["schema_version"]) is int and work["schema_version"] == 2
            and work["split"] == "validation" and matches(project_results.ID, work["task_id"])
            and matches(DIGEST, work["input_sha256"]) and work["input_sha256"] != cycle["input_sha256"]
            and all(work[key] == ref[key] for key in ("project_id", "source_commit", "project_tree_sha256")),
            "optimizer_validation_mismatch")
    skill_assessments._work_checks(work["checks"])
    require(work["checks"]["required_case_ids"] == meta["case_ids"]
            and not set(meta["case_ids"]) & set(meta["work_checks"]["required_case_ids"])
            and all(work["checks"][key] == meta["work_checks"][key] for key in
                    ("plan_sha256", "protected_sha256", "required_gate_ids"))
            and all(work["task_id"] != row["work"]["task_id"] for row in by_version.values()),
            "optimizer_validation_mismatch")
    rows, scores = validation["evaluations"], {}
    require(isinstance(rows, list) and len(rows) <= len(by_version) + 1, "invalid_validation_evidence")
    for row in rows:
        exact(row, "application checks")
        observation, receipt = row["checks"], row["application"]
        project_checks.validate_observation(observation)
        require(all(observation[key] == ref[key] for key in
                    ("plan_sha256", "protected_sha256", "environment_sha256")), "optimizer_validation_mismatch")
        exact(receipt, "version_id staged_version_id work_sha256 output_sha256 activated changed task_outcome measurement")
        identifier = receipt["version_id"]
        require(isinstance(identifier, str) and identifier not in scores
                and identifier in {cycle["original_version_id"], *by_version}
                and identifier == receipt["staged_version_id"] and receipt["activated"] is True
                and receipt["work_sha256"] == work["input_sha256"]
                and matches(DIGEST, receipt["output_sha256"]) and type(receipt["changed"]) is bool
                and receipt["task_outcome"] == project_checks.replay_outcome(observation, work["checks"]),
                "optimizer_validation_mismatch")
        exact(receipt["measurement"], "cost_nano_aiu elapsed_seconds")
        require(all(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0)
                    for value in receipt["measurement"].values()), "optimizer_validation_mismatch")
        # Failed/unknown observations are retained but can never supply a selectable score.
        try:
            scores[identifier] = case_scores(observation, work)
        except RuntimeFailure as error:
            require(error.code == "evaluation_unverified" and cycle["stop_reason"] != "search_complete",
                    "optimizer_validation_mismatch")
            scores[identifier] = None
    return scores
