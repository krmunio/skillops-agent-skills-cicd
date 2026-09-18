"""Project orchestration with explicit live-evaluation gates and public-only output."""

import argparse
import base64
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from uuid import uuid4

from copilot_runtime import MIN_AI_CREDITS, CopilotRuntime, RuntimeFailure, capture, strict_json
import project_results as results
import repositories
import project_checks
import skill_guide
import skill_pipeline
import skill_assessments
import evolution_records as evolution
import evaluation_reporting
import evaluation_telemetry


class BudgetRuntime:
    def __init__(self, runtime, budget):
        self.runtime = runtime
        self.budget = budget
        self.recorder = None

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def invoke(self, *args, **kwargs):
        if self.budget["calls"] >= self.budget["max_calls"]:
            raise RuntimeFailure("call_limit", "The authorized model-call limit was reached.")
        remaining = self.budget["deadline"] - time.monotonic()
        if remaining <= 0:
            raise RuntimeFailure("time_limit", "The authorized time window was reached.")
        self.budget["calls"] += 1
        kwargs["timeout"] = min(180, remaining)
        if "max_ai_credits" in self.budget:
            kwargs["max_ai_credits"] = self.budget["max_ai_credits"]
        if self.recorder is not None:
            return self.recorder.invoke(self.runtime, *args, **kwargs)
        return self.runtime.invoke(*args, **kwargs)


def assess(root, project, run_id, source_commit, policy, runtime_factory=CopilotRuntime):
    return assess_with_details(root, project, run_id, source_commit, policy, runtime_factory=runtime_factory)[0]


def resolve_images(project):
    plan = project_checks.discover(project)
    languages = {"python" if check["runner"] in ("pytest", "unittest") else "node" for check in plan["checks"]}
    if plan["gates"]:
        languages.add("node")
    images = {}
    for language in sorted(languages):
        tag = "python:3.12-slim" if language == "python" else "node:22-slim"
        result = capture(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], timeout=15)
        identity = result.stdout.strip()
        results.require(not result.returncode and results.matches(r"sha256:[a-f0-9]{64}", identity),
                        "missing_check_image")
        images[language] = identity
    return images


def assess_with_details(root, project, run_id, source_commit, policy, *, runtime_factory=CopilotRuntime, history=(),
                        telemetry=None):
    root = Path(root)
    folder = root / "projects" / project["id"]
    row = {
        "schema_version": 1, "project_id": project["id"], "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "origin": "github_actions",
        "purpose": "project_assessment", "source_commit": source_commit,
        "project_tree_sha256": project["tree_sha256"], "evaluator_sha256": results.evaluator_hash(root),
        "source_report_sha256": None, "source_schema_version": None,
        "guide": results.axis("not_assessed", "no_skills"),
        "execution": results.axis("configuration_required", "no_adapter"),
    }
    if project["error"]:
        row["guide"] = results.axis("blocked", "unsafe_project")
        row["execution"] = results.axis("blocked", "unsafe_project")
        return results.validate(row), None, None
    if any(next((folder / name).rglob("SKILL.md"), None) is not None
           for name in (".github/skills", ".claude/skills", "skills")):
        row["guide"] = results.axis("blocked", "guide_integration_pending")
    adapter = project["adapter"]
    if adapter not in (None, repositories.EVALUATION_SET):
        row["execution"] = results.axis("configuration_required", "unsupported_adapter")
        return results.validate(row), None, None
    reason = None
    if policy.get("enabled") is not True:
        reason = "live_disabled"
    elif policy.get("authenticated") is not True:
        reason = "missing_auth"
    elif not isinstance(policy.get("budget"), dict):
        reason = "missing_limits"
    if reason:
        row["execution"] = results.axis("blocked", reason)
        return results.validate(row), None, None
    budget = policy["budget"]
    before = budget["calls"]
    try:
        bundles = skill_guide.discover(folder)
        if not bundles:
            images = resolve_images(folder)
            preparation_seconds = min(180, budget["deadline"] - time.monotonic())
            results.require(preparation_seconds > 0, "time_limit")
            with project_checks.prepared_images(folder, images, timeout=preparation_seconds) as prepared:
                checked = project_checks.execute(
                    folder, project_checks.discover(folder), prepared, deadline=budget["deadline"])
            row["execution"] = results.axis(
                checked["status"], "evaluation_completed" if checked["status"] == "completed" else "evaluation_failed",
                {"requested": len(checked["cases"]),
                 "correctness_successes": sum(case["status"] == "passed" for case in checked["cases"]),
                 "cli_invocations": 0},
            )
            return results.validate(row), None, None
        raw_runtime = runtime_factory(root)
        runtime = BudgetRuntime(raw_runtime, budget)
        rubric = results.read_json(root / "eval/skill-guide-rubric.json")
        evaluated, failures = [], []
        with raw_runtime.locked(), ExitStack() as setup:
            check_error = None
            try:
                images = resolve_images(folder)
                preparation_seconds = min(180, budget["deadline"] - time.monotonic())
                results.require(preparation_seconds > 0, "time_limit")
                prepared = setup.enter_context(
                    project_checks.prepared_images(folder, images, timeout=preparation_seconds))
            except (RuntimeFailure, OSError) as error:
                check_error = error.code if isinstance(error, RuntimeFailure) else "io_error"
                prepared = {}
            for bundle in bundles:
                key = skill_pipeline.skill_key(project["id"], bundle["path"], history)
                identifier = sha256(key.encode()).hexdigest()
                artifact = runtime.private / "assessments" / run_id / project["id"] / identifier
                observer = (partial(evaluation_reporting.progress, project["id"], bundle["path"])
                            if policy.get("progress") else None)
                if telemetry is not None:
                    runtime.recorder = evaluation_telemetry.Recorder(observer)
                    telemetry.append({"skill_key": key, "source_path": bundle["path"],
                                      "stages": runtime.recorder.stages})
                    observer = runtime.recorder
                try:
                    evaluated.append(skill_pipeline.evaluate_skill(
                        runtime, "gpt-6-astra", folder, bundle, key, rubric, prepared, artifact,
                        deadline=budget["deadline"], check_error=check_error,
                        progress=observer))
                except (RuntimeFailure, OSError) as error:
                    failure = {"source_path": bundle["path"],
                               "code": error.code if isinstance(error, RuntimeFailure) else "io_error"}
                    failures.append(failure)
                    artifact.mkdir(parents=True, exist_ok=True)
                    (artifact / "failure.json").write_bytes(results.encoded(failure))
        assessments = [item for item, _ in evaluated]
        complete_quality = not failures and all(
            all(item["quality"][arm] is not None and item["quality"][arm]["status"] == "completed"
                for arm in ("base", "candidate")) for item in assessments)
        row["guide"] = results.axis(
            "completed" if complete_quality else "blocked",
            "evaluation_completed" if complete_quality else "evaluation_failed",
            {"skills": len(bundles), "errors": len(failures)},
        )
        incomplete = failures or any(item["errors"] or item["decision"]["status"] == "unverified" for item in assessments)
        rejected = any(item["decision"]["status"] == "rejected" for item in assessments)
        row["execution"] = results.axis(
            "blocked" if incomplete else "completed", "assessment_unverified" if incomplete else "evaluation_completed",
            {"cli_invocations": budget["calls"] - before}, "rejected" if rejected else None,
        )
        lifecycle, details = skill_pipeline.attachments(results.validate(row), evaluated) if evaluated else (None, None)
        return row, lifecycle, details
    except (RuntimeFailure, OSError) as error:
        reason = error.code if isinstance(error, RuntimeFailure) and error.code in results.REASONS else "runtime_error"
        row["execution"] = results.axis("blocked", reason, {"cli_invocations": budget["calls"] - before})
    return results.validate(row), None, None


def policy_from_environment():
    policy = {
        "enabled": os.environ.get("SKILLOPS_LIVE_EVALUATION_ENABLED") == "true",
        "authenticated": bool(os.environ.get("COPILOT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")),
        "progress": os.environ.get("SKILLOPS_ACTIONS_PROGRESS") == "true",
    }
    try:
        calls = int(os.environ.get("SKILLOPS_MAX_INVOCATIONS", ""))
        seconds = int(os.environ.get("SKILLOPS_MAX_SECONDS", ""))
    except ValueError:
        return policy
    if 0 < calls <= 1000 and 0 < seconds <= 7200:
        policy["budget"] = {"calls": 0, "max_calls": calls, "max_seconds": seconds,
                            "deadline": time.monotonic() + seconds}
        credit = os.environ.get("SKILLOPS_MAX_AI_CREDITS_PER_SESSION")
        if credit is not None:
            try:
                credit = float(credit)
            except ValueError:
                policy.pop("budget")
                return policy
            if not math.isfinite(credit) or credit < MIN_AI_CREDITS:
                policy.pop("budget")
                return policy
            policy["budget"]["max_ai_credits"] = credit
    return policy


def budget_limits(budget):
    results.require(isinstance(budget, dict)
                    and type(budget.get("max_calls")) is int and 0 < budget["max_calls"] <= 1000
                    and type(budget.get("max_seconds")) is int and 0 < budget["max_seconds"] <= 7200,
                    "missing_limits")
    credit = budget.get("max_ai_credits")
    results.require(credit is None or (type(credit) in (int, float) and math.isfinite(credit)
                                      and credit >= MIN_AI_CREDITS), "missing_limits")
    return {"max_invocations": budget["max_calls"], "max_seconds": budget["max_seconds"],
            "max_ai_credits_per_session": credit}


def _replay_run_id(execution_mode):
    prefix = "sample-" if execution_mode == "sample" else "local-"
    return prefix + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]


def _replay_report(reference, run_id, execution_mode, *, complete_quality=False):
    origin = "github_actions" if execution_mode == "live" and os.environ.get("GITHUB_ACTIONS") == "true" else "local"
    return {
        "schema_version": 1, "project_id": reference["project_id"], "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "origin": "sample" if execution_mode == "sample" else origin,
        "purpose": "project_assessment", "source_commit": reference["source_commit"],
        "project_tree_sha256": reference["project_tree_sha256"], "evaluator_sha256": reference["evaluator_sha256"],
        "source_report_sha256": None, "source_schema_version": None,
        "guide": results.axis("completed" if complete_quality else "blocked",
                              "evaluation_completed" if complete_quality else "assessment_unverified"),
        "execution": results.axis("blocked", "assessment_unverified"),
    }


def persist_replay(output, evaluation, captures, generation, reference, *, execution_mode, run_id=None):
    """Shared persist_round callback; never mutate the provider's retained evaluation."""
    output = results.safe_path(output)
    run_id = run_id or _replay_run_id(execution_mode)
    complete_quality = all(evaluation["quality"][arm] is not None
                           and evaluation["quality"][arm]["status"] == "completed" for arm in ("base", "candidate"))
    report = _replay_report(reference, run_id, execution_mode, complete_quality=complete_quality)
    common = {"schema_version": 1, "project_id": report["project_id"], "run_id": run_id,
              "report_sha256": sha256(results.encoded(report)).hexdigest()}
    key = evaluation["skill_key"]
    records = evolution.empty_records()
    records["identities"] = [{"skill_key": key, "display_name": Path(evaluation["source_path"]).name}]
    records["sources"] = [{
        "skill_key": key, "project_id": report["project_id"], "kind": "workspace", "scope": "project",
        "path": evaluation["source_path"], "observed_at": report["created_at"], "evidence_ref": None,
    }]
    versions, contents = {}, {}
    for version, files in captures:
        identifier = version["version_id"]
        results.require(identifier not in versions or versions[identifier] == version, "capture_mismatch")
        versions[identifier] = version
        for name, raw in files.items():
            item = {"version_id": identifier, "path": name, "encoding": "base64",
                    "data": base64.b64encode(raw).decode("ascii")}
            results.require((identifier, name) not in contents or contents[(identifier, name)] == item,
                            "capture_mismatch")
            contents[(identifier, name)] = item
    records["versions"] = list(versions.values())
    records["skill_versions"] = [{"skill_key": key, "version_id": identifier} for identifier in versions]
    lifecycle = {**common, "records": records, "bindings": [{
        "skill_key": key, "base_version_id": evaluation["base_version_id"],
        "candidate_version_id": evaluation["candidate_version_id"], "legacy_skill_id": None,
    }], "file_contents": list(contents.values())}
    replay = {**common, "execution_mode": execution_mode, "reference": reference,
              "generation": generation, "evaluation": evaluation}
    results.validate_evolution(lifecycle, report)
    skill_assessments.validate_replay(replay, report=report, lifecycle=lifecycle)
    results.require(len(results.encoded(replay)) <= results.LIMIT, "output_limit")
    results.store(output, report)
    results.store_evolution(output, lifecycle)
    path = results.store_replay(output, replay)
    return {"project_id": report["project_id"], "run_id": run_id,
            "path": path.name,
            "sha256": sha256(results.read_bytes(path)).hexdigest()}


def persist_cycle(output, cycle, reference):
    """Bind and store a terminal loop payload; propagate every storage/verification failure."""
    output = results.safe_path(output)
    results.require(all(cycle[key] == reference[key] for key in (
        "project_id", "skill_key", "source_path", "input_sha256", "reference_sha256", "original_version_id")),
        "cycle_reference_mismatch")
    report = _replay_report(reference, cycle["run_id"], cycle["execution_mode"])
    bound = {**cycle, "report_sha256": sha256(results.encoded(report)).hexdigest()}
    results.validate_cycle(bound, report=report, evaluations=results.load_replay_evidence(output))
    results.store(output, report)
    path = results.store_cycle(output, bound)
    stored = results.load_cycles(output)[(cycle["project_id"], cycle["cycle_id"])]
    results.require(results.encoded(stored) == results.encoded(bound), "cycle_evidence_mismatch")
    return {"project_id": cycle["project_id"], "run_id": cycle["run_id"], "path": path.name,
            "sha256": sha256(results.encoded(stored)).hexdigest()}


def persist_adoption(output, *, approval, receipt=None, reviewed=False):
    """Project reviewed local observations, never private authority or fabricated task metrics."""
    import skill_approvals

    results.require(reviewed is True, "publication_review_required")
    evolution.exact(approval, skill_approvals.APPROVAL_FIELDS)
    output = results.safe_path(output)
    reports = {(row["project_id"], row["run_id"]): row for row in results.load_reports(output)}
    cycles = results.load_cycles(output)
    key = (approval["project_id"], approval["cycle_id"])
    results.require(key in cycles and cycles[key]["execution_mode"] == "live", "cycle_not_approvable")
    public_approval = {field: approval[field] for field in results.APPROVAL_PUBLIC_FIELDS.split()
                       if field not in ("approved_by", "trust_scope")}
    public_approval.update(approved_by="local_operator", trust_scope="local_environment")
    executions = []
    if receipt is None:
        report = {**reports[key], "run_id": _replay_run_id("live"), "origin": "local",
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "guide": results.axis("not_assessed", "adoption_observation"),
                  "execution": results.axis("not_assessed", "adoption_observation")}
    else:
        evolution.exact(receipt, skill_approvals.EXECUTION_FIELDS)
        results.require(receipt["approval_sha256"] == sha256(results.encoded(approval)).hexdigest()
                        and all(receipt[field] == approval[field] for field in (
                            "approval_id", "environment_id", "previous_active_version_id",
                            "previous_active_execution_sha256")), "execution_binding_mismatch")
        execution_key = (receipt["project_id"], receipt["run_id"])
        results.require(execution_key in reports, "missing_execution_report")
        report = reports[execution_key]
        executions = [{field: receipt[field] for field in results.EXECUTION_PUBLIC_FIELDS.split()}]
    data = {
        "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
        "report_sha256": sha256(results.encoded(report)).hexdigest(), "execution_mode": "live",
        "approvals": [public_approval], "executions": executions,
    }
    results.validate_adoption(data, report=report, reports=reports, cycles=cycles,
                             adoptions=results.load_adoptions(output), evaluations=results.load_replays(output))
    results.store(output, report)
    path = results.store_adoption(output, data)
    stored = results.load_adoptions(output)[(data["project_id"], data["run_id"])]
    results.require(stored == data, "adoption_evidence_mismatch")
    return {"project_id": data["project_id"], "run_id": data["run_id"], "path": path.name,
            "sha256": sha256(results.read_bytes(path)).hexdigest()}


def retain_work_item(root, work):
    """Retain a validated private input immutably, before any model exposure."""
    root = results.safe_path(root)
    results.require(isinstance(work, dict), "invalid_work_item")
    results.require(results.matches(results.ID, work.get("project_id")), "invalid_project_id")
    skill_assessments.validate_work_item(
        work, project=root / "projects" / work["project_id"], source_commit=work.get("source_commit"))
    folder = root / ".skillops-private"
    for directory in (folder, folder / "work-items", folder / "work-items" / work["task_id"]):
        results.safe_path(directory)
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.stat()
        results.require(directory.is_dir() and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
                        "unsafe_work_store")
        descriptor = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    path = directory / (work["input_sha256"] + ".json")
    results.atomic_json(path, work, immutable=True)
    info = path.stat()
    results.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_mode & 0o077 == 0 and info.st_nlink == 1, "unsafe_work_store")
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    results.require(results.read_bytes(path) == results.encoded(work), "work_input_changed")
    return path


@contextmanager
def _replay_session(root, *, project_id, skill_key, work_item, output, model, execution_mode, policy,
                    runtime_factory=None, confirmation_work_item=None):
    """Keep preparation, image lifetime, lock and budget shared across either execution path."""
    results.require(policy.get("enabled") is True, "live_disabled")
    results.require(policy.get("authenticated") is True, "missing_auth")
    budget = policy.get("budget")
    limits = budget_limits(budget)
    results.require(limits["max_ai_credits_per_session"] is not None, "missing_limits")
    results.require(type(budget.get("calls")) is int and 0 <= budget["calls"] <= budget["max_calls"]
                    and type(budget.get("deadline")) in (int, float) and math.isfinite(budget["deadline"]),
                    "missing_limits")
    remaining = budget["deadline"] - time.monotonic()
    results.require(remaining > 0, "time_limit")
    results.require(remaining <= limits["max_seconds"], "missing_limits")
    results.require(budget["calls"] < budget["max_calls"], "call_limit")
    results.require(execution_mode in ("live", "offline_test"), "invalid_execution_mode")
    root, output = results.safe_path(root), results.safe_path(output)
    results.require(results.matches(results.ID, project_id), "invalid_project_id")
    project = root / "projects" / project_id
    results.require(not output.is_relative_to(project), "unsafe_path")
    work = results.read_json(work_item)
    results.require(isinstance(work, dict), "invalid_work_item")
    skill_assessments.validate_work_item(work, project=project, source_commit=work.get("source_commit"))
    results.require(work["project_id"] == project_id, "work_project_mismatch")
    results.require(work["split"] == "development", "confirmation_isolation_unverified")
    final = None
    if confirmation_work_item is not None:
        final = results.read_json(confirmation_work_item)
        skill_assessments.validate_work_item(final, project=project, source_commit=work["source_commit"])
        results.require(final["split"] == "confirmation" and final["task_id"] != work["task_id"]
                        and final["input_sha256"] != work["input_sha256"]
                        and not set(final["checks"]["required_case_ids"]) & set(work["checks"]["required_case_ids"]),
                        "confirmation_isolation_unverified")
    history = list(results.load_assessments(output).values())
    history.extend({"project_id": row["project_id"], "skills": [row["evaluation"]]}
                   for row in results.load_replays(output).values())
    selected = [bundle for bundle in skill_guide.discover(project)
                if skill_pipeline.skill_key(project_id, bundle["path"], history) == skill_key]
    results.require(len(selected) == 1, "unknown_skill")
    rubric = results.read_json(root / "eval/skill-guide-rubric.json")
    raw_runtime = (runtime_factory or CopilotRuntime)(root)
    runtime = BudgetRuntime(raw_runtime, budget)
    runtime.execution_mode = execution_mode
    artifact = runtime.private / "replays" / uuid4().hex
    with raw_runtime.locked():
        retain_work_item(root, work)
        if final is not None:
            retain_work_item(root, final)
        images = resolve_images(project)
        remaining = min(180, budget["deadline"] - time.monotonic())
        results.require(remaining > 0, "time_limit")
        with project_checks.prepared_images(project, images, timeout=remaining) as prepared:
            context = skill_pipeline.prepare_replay(
                runtime, model, project, selected[0], skill_key, rubric, prepared, artifact / "prepare",
                work_item=work, deadline=budget["deadline"])
            confirmation = None if final is None else partial(
                skill_pipeline.prepare_replay, runtime, model, project, selected[0], skill_key, rubric, prepared,
                artifact / "confirmation-prepare", work_item=final, deadline=budget["deadline"])
            yield runtime, context, artifact, confirmation


def run_replay(root, *, project_id, skill_key, work_item, output, model, execution_mode, policy,
               runtime_factory=None):
    """One opt-in development replay, using the same providers and persistence as iteration."""
    with _replay_session(
            root, project_id=project_id, skill_key=skill_key, work_item=work_item, output=output,
            model=model, execution_mode=execution_mode, policy=policy, runtime_factory=runtime_factory
    ) as (runtime, context, artifact, _):
        budget = runtime.budget
        generation, candidate = skill_pipeline.generate_candidate(
            runtime, model, context["original"], context["feedback"], artifact / "generation",
            deadline=budget["deadline"])
        row, captures = skill_pipeline.evaluate_candidate(
            runtime, model, context, candidate, artifact / "evaluation", deadline=budget["deadline"])
        evidence = persist_replay(output, row, captures, generation, context["reference"],
                                  execution_mode=execution_mode)
    return {"status": row["decision"]["status"], "execution_mode": execution_mode, "evaluation_ref": evidence,
            "candidate_version_id": candidate[0]["version_id"], "budget": budget_limits(budget), "calls": budget["calls"],
            "approval_eligible": False, "confirmation_status": "not_run",
            "confirmation_reason": "confirmation_isolation_unverified"}


def run_iterations(root, *, project_id, skill_key, work_item, output, model, execution_mode, policy,
                   max_rounds=1, confirmation_work_item=None, runtime_factory=None, cycle_id=None):
    """Connect the delivered loop; do not duplicate its attempt or failure semantics."""
    import skill_iterations
    results.require(type(max_rounds) is int and 1 <= max_rounds <= 10, "invalid_round_limit")
    cycle_id = _replay_run_id(execution_mode) if cycle_id is None else cycle_id
    results.require(results.matches(results.RUN, cycle_id), "invalid_cycle_id")
    with _replay_session(
            root, project_id=project_id, skill_key=skill_key, work_item=work_item, output=output,
            model=model, execution_mode=execution_mode, policy=policy, runtime_factory=runtime_factory,
            confirmation_work_item=confirmation_work_item
    ) as (runtime, context, artifact, confirmation):
        cycle = skill_iterations.run_cycle(
            runtime, model, context, artifact / "cycle", cycle_id=cycle_id, max_rounds=max_rounds,
            budget=runtime.budget, confirmation_context=confirmation,
            persist_round=partial(persist_replay, output, execution_mode=execution_mode))
        evidence = persist_cycle(output, cycle, context["reference"])
    return {"status": cycle["stop_reason"], "execution_mode": execution_mode, "cycle_id": cycle_id,
            "cycle_ref": evidence, "rounds": len(cycle["rounds"]), "budget": cycle["budget"],
            "calls": runtime.budget["calls"], "selected_candidate_version_id": cycle["selected_candidate_version_id"],
            "approval_eligible": False, "confirmation_status": cycle["confirmation_status"]}


def run_recorded_iterations(root, *, project_id, skill_key, work_id, max_rounds, output,
                            source_commit, cycle_id, live, policy, runtime_factory=None):
    """Select private recorded work by public ID; never put requests in dispatch arguments."""
    results.require(live is True and policy.get("enabled") is True, "live_disabled")
    results.require(policy.get("authenticated") is True, "missing_auth")
    results.require(results.matches(results.ID, work_id) and results.matches(results.ID, project_id)
                    and evolution.matches(evolution.SKILL_KEY, skill_key)
                    and type(max_rounds) is int and 1 <= max_rounds <= 10, "invalid_work_selection")
    budget_limits(policy.get("budget"))
    private = os.environ.pop("SKILLOPS_RECORDED_WORK_ITEMS", None)
    results.require(isinstance(private, str) and 0 < len(private.encode("utf-8")) <= results.LIMIT,
                    "missing_private_work")
    choices = strict_json(private)
    results.require(isinstance(choices, dict) and work_id in choices, "unknown_private_work")
    selected = choices[work_id]
    evolution.exact(selected, "work_item")
    work = selected["work_item"]
    skill_assessments.validate_work_item(
        work, project=results.safe_path(root) / "projects" / project_id, source_commit=source_commit)
    results.require(work["task_id"] == work_id and work["project_id"] == project_id
                    and work["split"] == "development", "work_selection_mismatch")
    raw_runtime = (runtime_factory or CopilotRuntime)(root)
    with raw_runtime.locked():
        path = retain_work_item(root, work)
    return run_iterations(
        root, project_id=project_id, skill_key=skill_key, work_item=path, output=output,
        model="gpt-6-astra", execution_mode="live", policy=policy, max_rounds=max_rounds,
        cycle_id=cycle_id, runtime_factory=lambda root: raw_runtime)


def changed_projects(root, projects, before, source_commit):
    results.require(results.matches(r"[a-f0-9]{40}", before)
                    and results.matches(r"[a-f0-9]{40}", source_commit), "invalid_change_revision")
    identity = capture(["git", "rev-parse", "--show-toplevel", "HEAD"], cwd=root, timeout=30, limit=65536)
    lines = identity.stdout.splitlines()
    results.require(not identity.returncode and len(lines) == 2
                    and Path(lines[0]).resolve() == Path(root).resolve()
                    and lines[1] == source_commit, "change_source_mismatch")
    if before == "0" * 40:
        return projects
    diff = capture([
        "git", "diff", "--name-only", "--no-renames", "--no-ext-diff", "--no-textconv",
        "-z", before, source_commit, "--",
    ], cwd=root, timeout=30)
    results.require(not diff.returncode, "change_detection_failed")
    affected = set()
    shared = {"project_profiles.json", "package.json", "package-lock.json",
              ".github/workflows/project-evaluation.yml"}
    for name in filter(None, diff.stdout.split("\0")):
        if (name in shared or name.startswith(("eval/", "skills/"))
                or ("/" not in name and name.endswith(".py"))):
            return projects
        parts = name.split("/")
        if len(parts) >= 3 and parts[0] == "projects":
            affected.add(parts[1])
    return [project for project in projects if project["id"] in affected]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-commit", required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--project", help="Evaluate only this catalog project; omitted means the entire catalog.")
    selection.add_argument("--changed-since", help="Evaluate projects affected since this full Git commit.")
    parser.add_argument("--history", type=Path, help="Previously validated results, used for stable Skill identity.")
    parser.add_argument("--work-id", help="Select one private recorded development task; never pass its request.")
    parser.add_argument("--skill-key", help="Exact Skill for recorded-work iteration.")
    parser.add_argument("--max-rounds", type=int, choices=range(1, 11))
    parser.add_argument("--live", action="store_true", help="Separate live opt-in required for recorded-work dispatch.")
    args = parser.parse_args()
    try:
        results.require(results.matches(results.RUN, args.run_id))
        results.require(results.matches(r"[a-f0-9]{40}", args.source_commit))
        projects = results.catalog(args.root)
        if args.project is not None:
            projects = [project for project in projects if project["id"] == args.project]
            results.require(bool(projects), "unknown_project")
        elif args.changed_since is not None:
            projects = changed_projects(args.root, projects, args.changed_since, args.source_commit)
        policy = policy_from_environment()
        if any(value is not None for value in (args.work_id, args.skill_key, args.max_rounds)):
            results.require(args.project is not None and args.work_id is not None and args.skill_key is not None
                            and args.max_rounds is not None and args.changed_since is None, "invalid_work_selection")
            report = run_recorded_iterations(
                args.root, project_id=args.project, skill_key=args.skill_key, work_id=args.work_id,
                max_rounds=args.max_rounds, output=args.output, source_commit=args.source_commit,
                cycle_id=args.run_id, live=args.live, policy=policy)
            results.reindex(args.root, args.output)
            print(json.dumps(report))
            return 0 if report["status"] in ("improved", "max_rounds", "no_change") else 2
        if policy["progress"] and os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"selected_projects={len(projects)}\n")
        if not projects:
            results.reindex(args.root, args.output)
            print(json.dumps({"status": "not_assessed", "reason": "no_changed_projects"}))
            return 0
        prior = {}
        if args.history is not None:
            rows = results.load_reports(args.history)
            assessments = results.load_assessments(args.history, rows)
            for item in sorted(rows, key=lambda row: (row["created_at"], row["run_id"]), reverse=True):
                key = (item["project_id"], item["run_id"])
                if item["origin"] != "sample" and key in assessments:
                    prior.setdefault(item["project_id"], []).append(assessments[key])
        failures = 0
        for project in projects:
            telemetry = []
            row, lifecycle, assessment = assess_with_details(
                args.root, project, args.run_id, args.source_commit, policy, history=prior.get(project["id"], ()),
                telemetry=telemetry)
            results.store(args.output, row)
            if lifecycle is not None:
                results.store_evolution(args.output, lifecycle)
            if assessment is not None:
                results.store_assessments(args.output, assessment)
            if telemetry:
                results.store_telemetry(args.output, evaluation_telemetry.bind(row, assessment, telemetry))
            failures += any(row[part]["status"] in ("blocked", "failed") for part in ("guide", "execution"))
            print(json.dumps({"project": project["id"], "guide": row["guide"]["status"],
                              "execution": row["execution"]["status"]}))
        results.reindex(args.root, args.output)
        return 2 if failures else 0
    except (RuntimeFailure, OSError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "io_error"
        print(json.dumps({"status": "blocked", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
