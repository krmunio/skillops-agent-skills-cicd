"""Bounded public results, project identity and static dashboard builds."""

import argparse
import base64
import binascii
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

from copilot_runtime import RuntimeFailure, strict_json
import evolution_records as evolution
import skill_assessments as assessments
import evaluation_telemetry as telemetry


ID = r"[a-z0-9][a-z0-9_-]{0,63}"
RUN = r"(?:[0-9]+-[0-9]+|(?:import-|local-|sample-)?[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12})"
LIMIT = 1024 * 1024
EVOLUTION_LIMIT = 2 * LIMIT
FIELDS = {
    "schema_version", "project_id", "run_id", "created_at", "origin", "purpose",
    "source_commit", "project_tree_sha256", "evaluator_sha256",
    "source_report_sha256", "source_schema_version", "guide", "execution",
}
COUNTS = {
    "requested", "attempted", "evaluation_completed", "errors", "blocked",
    "correctness_successes", "judge_score_denominator", "skills", "pass", "review",
    "base_correctness_successes", "candidate_correctness_successes", "cli_invocations",
    "controls", "base_requested", "candidate_requested",
}
METRICS = COUNTS | {
    "mean_judge_score", "base_judge_score", "candidate_judge_score",
    "base_cost_nano_aiu", "candidate_cost_nano_aiu",
    "base_elapsed_seconds", "candidate_elapsed_seconds",
    "cost_improvement_percent", "time_improvement_percent",
}
REASONS = {
    "no_skills", "no_adapter", "live_disabled", "missing_auth", "missing_limits",
    "guide_integration_pending", "historical_import", "evaluation_completed",
    "evaluation_failed", "unsupported_adapter", "unsafe_project", "runtime_error",
    "call_limit", "time_limit", "assessment_unverified", "adoption_observation",
}
STATES = {"completed", "failed", "blocked", "not_assessed", "configuration_required"}
DECISIONS = {None, "rejected", "eligible_for_canary", "blocked", "calibration_passed", "calibration_failed"}
CORE = (
    "evaluation.py", "copilot_runtime.py", "skillops.py", "candidates.py", "repositories.py",
    "project_results.py", "project_evaluation.py", "project_profiles.json", "skill_guide.py", "evolution_records.py",
    "skill_assessments.py", "project_checks.py", "skill_pipeline.py", "skill_iterations.py", "skill_approvals.py",
    "evaluation_telemetry.py",
)


def require(condition, code="invalid_public_result"):
    if not condition:
        raise RuntimeFailure(code, "Public result or project contract validation failed.")


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def safe_path(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "unsafe_path")
    return path


def read_bytes(path, limit=LIMIT):
    path = safe_path(path)
    require(path.is_file() and path.stat().st_size <= limit, "invalid_file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "input_limit")
    return raw


def read_json(path, limit=LIMIT):
    try:
        return strict_json(read_bytes(path, limit).decode("utf-8"))
    except UnicodeError as error:
        raise RuntimeFailure("invalid_encoding", "Expected UTF-8 JSON.") from error


def encoded(data):
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def atomic_json(path, data, immutable=False, *, limit=LIMIT):
    path = safe_path(path)
    payload = encoded(data)
    require(len(payload) <= limit, "output_limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".result-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                require(read_bytes(path, limit) == payload, "immutable_conflict")
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def axis(status, reason_code, metrics=None, decision=None):
    return {"status": status, "reason_code": reason_code, "metrics": metrics, "decision": decision}


def validate(row):
    require(isinstance(row, dict) and set(row) == FIELDS)
    require(type(row["schema_version"]) is int and row["schema_version"] == 1)
    require(matches(ID, row["project_id"]) and matches(RUN, row["run_id"]))
    require(row["origin"] in ("github_actions", "historical_import", "local", "sample"))
    require(row["run_id"].startswith("sample-") == (row["origin"] == "sample"))
    require(row["purpose"] in ("project_assessment", "baseline", "comparison", "candidate", "calibration"))
    require(isinstance(row["created_at"], str) and len(row["created_at"]) <= 40)
    try:
        stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        require(stamp.utcoffset() is not None)
    except ValueError as error:
        raise RuntimeFailure("invalid_timestamp", "Result timestamp is invalid.") from error
    for key, length in (("source_commit", 40), ("project_tree_sha256", 64),
                        ("evaluator_sha256", 64), ("source_report_sha256", 64)):
        require(row[key] is None or matches(r"[a-f0-9]{" + str(length) + "}", row[key]))
    require(row["source_schema_version"] is None or
            (type(row["source_schema_version"]) is int and row["source_schema_version"] in (1, 2)))
    if row["origin"] == "sample":
        require(all(row[key] is None for key in (
            "source_commit", "project_tree_sha256", "evaluator_sha256",
            "source_report_sha256", "source_schema_version")))
    elif row["origin"] == "historical_import":
        require(row["source_commit"] is None and row["project_tree_sha256"] is None)
        require(row["source_report_sha256"] is not None and row["source_schema_version"] in (1, 2))
    else:
        require(row["source_commit"] is not None and row["evaluator_sha256"] is not None)
    for name in ("guide", "execution"):
        part = row[name]
        require(isinstance(part, dict) and set(part) == {"status", "reason_code", "metrics", "decision"})
        require(isinstance(part["status"], str) and part["status"] in STATES)
        require(isinstance(part["reason_code"], str) and part["reason_code"] in REASONS)
        require(part["decision"] is None or isinstance(part["decision"], str))
        require(part["decision"] in DECISIONS)
        metrics = part["metrics"]
        if metrics is None:
            continue
        require(isinstance(metrics, dict) and set(metrics) <= METRICS)
        for key, value in metrics.items():
            if value is None:
                continue
            require(type(value) in (int, float) and math.isfinite(value))
            if key in COUNTS:
                require(type(value) is int and value >= 0)
            elif key.endswith("improvement_percent"):
                pass
            else:
                require(value >= 0)
            if key in ("mean_judge_score", "base_judge_score", "candidate_judge_score"):
                require(value <= 100)
        requested = metrics.get("requested")
        if requested is not None:
            for key in ("attempted", "evaluation_completed", "correctness_successes", "judge_score_denominator"):
                require(metrics.get(key) is None or metrics[key] <= requested)
    if row["origin"] == "sample":
        require(all(row[part]["decision"] is None for part in ("guide", "execution")))
        require(all((row[part]["metrics"] or {}).get("cli_invocations", 0) == 0 for part in ("guide", "execution")))
    return row


def profiles(root):
    path = Path(root) / "project_profiles.json"
    if not path.exists():
        return {}
    data = read_json(path)
    require(isinstance(data, dict) and set(data) == {"schema_version", "projects"}, "invalid_profiles")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_profiles")
    require(isinstance(data["projects"], dict), "invalid_profiles")
    for key, value in data["projects"].items():
        require(matches(ID, key) and isinstance(value, dict) and set(value) == {"adapter"}, "invalid_profiles")
        require(value["adapter"] is None or matches(r"[a-z0-9-]{1,80}", value["adapter"]), "invalid_profiles")
    return data["projects"]


def tree_hash(path):
    entries = []
    total = 0
    for item in sorted(path.rglob("*")):
        require(not item.is_symlink() and item.name != ".git", "unsafe_project")
        if item.is_dir():
            continue
        require(item.is_file(), "unsafe_project")
        raw = read_bytes(item)
        total += len(raw)
        require(total <= 128 * LIMIT and len(entries) < 10000, "project_limit")
        entries.append([item.relative_to(path).as_posix(), sha256(raw).hexdigest()])
    return sha256(encoded(entries)).hexdigest()


def catalog(root):
    root = Path(root)
    directory = safe_path(root / "projects")
    require(directory.is_dir(), "missing_projects")
    settings = profiles(root)
    rows = []
    for path in sorted(directory.iterdir()):
        if path.name == "README.md" and path.is_file() and not path.is_symlink():
            continue
        require(matches(ID, path.name), "invalid_project_id")
        row = {"id": path.name, "tree_sha256": None, "adapter": settings.get(path.name, {}).get("adapter"), "error": None}
        try:
            require(path.is_dir() and not path.is_symlink(), "unsafe_project")
            row["tree_sha256"] = tree_hash(path)
        except RuntimeFailure:
            row["error"] = "unsafe_project"
        rows.append(row)
    require(set(settings) <= {row["id"] for row in rows}, "orphan_profile")
    require(len(rows) <= 50, "project_limit")
    return rows


def evaluator_hash(root):
    root = Path(root)
    paths = set(CORE)
    for folder in ("eval", "skills"):
        for path in (root / folder).rglob("*"):
            if path.is_file() or path.is_symlink():
                if "__pycache__" not in path.parts and path.suffix != ".pyc":
                    paths.add(path.relative_to(root).as_posix())
    values = []
    for name in sorted(paths):
        path = root / name
        values.append([name, sha256(read_bytes(path)).hexdigest() if path.exists() or path.is_symlink() else None])
    return sha256(encoded(values)).hexdigest()


def store(results, row):
    validate(row)
    path = Path(results) / row["project_id"] / row["run_id"] / "report.json"
    atomic_json(path, row, immutable=True)
    return path


def load_reports(results):
    results = safe_path(results)
    rows = []
    if not results.exists():
        return rows
    for path in sorted(results.glob("*/*/report.json")):
        row = validate(read_json(path))
        require(path.parent.name == row["run_id"] and path.parent.parent.name == row["project_id"])
        rows.append(row)
        require(len(rows) <= 10000, "history_limit")
    return rows


def validate_snapshots(data, report):
    validate(report)
    require(isinstance(data, dict) and set(data) == {
        "schema_version", "project_id", "run_id", "report_sha256", "skill_id", "base", "candidate",
    }, "invalid_skill_snapshots")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    require(data["project_id"] == report["project_id"] and data["run_id"] == report["run_id"])
    require(data["report_sha256"] == sha256(encoded(report)).hexdigest(), "snapshot_report_mismatch")
    require(matches(ID, data["skill_id"]))
    for arm in ("base", "candidate"):
        snapshot = data[arm]
        if arm == "candidate" and snapshot is None:
            continue
        require(isinstance(snapshot, dict) and set(snapshot) == {"content", "sha256"})
        text = snapshot["content"]
        require(isinstance(text, str) and 0 < len(text.encode("utf-8")) <= 32768
                and len(text.split("\n")) <= 400, "snapshot_size_limit")
        require(sha256(text.encode("utf-8")).hexdigest() == snapshot["sha256"], "snapshot_hash_mismatch")
    return data


def store_snapshots(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")))
    folder = Path(results) / data["project_id"] / data["run_id"]
    report = read_json(folder / "report.json")
    validate_snapshots(data, report)
    counterpart = safe_path(folder / "skill-evolution.json")
    if counterpart.exists():
        validate_evolution(read_json(counterpart, EVOLUTION_LIMIT), report, data)
    path = folder / "skill-snapshots.json"
    atomic_json(path, data, immutable=True)
    return path


def load_snapshots(results, rows=None):
    results = safe_path(results)
    reports = {(row["project_id"], row["run_id"]): row
               for row in (load_reports(results) if rows is None else rows)}
    snapshots = {}
    for path in sorted(results.glob("*/*/skill-snapshots.json")):
        key = (path.parent.parent.name, path.parent.name)
        require(key in reports, "orphan_skill_snapshots")
        snapshots[key] = validate_snapshots(read_json(path), reports[key])
    return snapshots


def merge_results(root, incoming, results):
    rows = load_reports(incoming)
    snapshots = load_snapshots(incoming, rows)
    lifecycles = load_evolution(incoming, rows, snapshots)
    skill_assessments = load_assessments(incoming, rows, lifecycles)
    measurements = load_telemetry(incoming, rows, skill_assessments)
    replays = load_replays(incoming, rows)
    cycles = load_cycles(incoming, rows)
    adoptions = load_adoptions(incoming, rows)
    existing_rows = load_reports(results)
    existing_snapshots = load_snapshots(results, existing_rows)
    existing_lifecycles = load_evolution(results, existing_rows, existing_snapshots)
    existing_assessments = load_assessments(results, existing_rows, existing_lifecycles)
    existing_measurements = load_telemetry(results, existing_rows, existing_assessments)
    existing_replays = load_replays(results, existing_rows)
    existing_cycles = load_cycles(results, existing_rows)
    existing_adoptions = load_adoptions(results, existing_rows)
    merged_rows = {(row["project_id"], row["run_id"]): row for row in existing_rows}
    merged_snapshots = dict(existing_snapshots)
    merged_lifecycles = dict(existing_lifecycles)
    merged_assessments = dict(existing_assessments)
    merged_measurements = dict(existing_measurements)
    merged_replays = dict(existing_replays)
    merged_cycles = dict(existing_cycles)
    merged_adoptions = dict(existing_adoptions)
    for mapping, values, name in (
        (merged_rows, {(row["project_id"], row["run_id"]): row for row in rows}, "report.json"),
        (merged_snapshots, snapshots, "skill-snapshots.json"),
        (merged_lifecycles, lifecycles, "skill-evolution.json"),
        (merged_assessments, skill_assessments, "skill-assessments.json"),
        (merged_measurements, measurements, "stage-metrics.json"),
        (merged_replays, replays, "replay-evaluation.json"),
        (merged_cycles, cycles, "cycle.json"),
        (merged_adoptions, adoptions, "adoption.json"),
    ):
        for key, value in values.items():
            path = safe_path(Path(results) / key[0] / key[1] / name)
            limit = EVOLUTION_LIMIT if name == "skill-evolution.json" else LIMIT
            require(not path.exists() or read_bytes(path, limit) == encoded(value), "immutable_conflict")
            mapping[key] = value
    for key, value in merged_lifecycles.items():
        validate_evolution(value, merged_rows[key], merged_snapshots.get(key))
    for key, value in merged_assessments.items():
        assessments.validate(value, merged_rows[key], merged_lifecycles.get(key))
    for key, value in merged_measurements.items():
        telemetry.validate(value, merged_rows[key], merged_assessments.get(key))
    for key, value in merged_replays.items():
        assessments.validate_replay(value, report=merged_rows[key], lifecycle=merged_lifecycles.get(key))
    evidence = {key: {"report": merged_rows[key], "lifecycle": merged_lifecycles[key], "replay": value}
                for key, value in merged_replays.items()}
    for key, value in merged_cycles.items():
        validate_cycle(value, report=merged_rows[key], evaluations=evidence)
    _validate_adoption_graph(merged_adoptions, reports=merged_rows, cycles=merged_cycles,
                             evaluations=merged_replays)
    for row in rows:
        store(results, row)
    for data in snapshots.values():
        store_snapshots(results, data)
    for data in lifecycles.values():
        store_evolution(results, data)
    for data in skill_assessments.values():
        store_assessments(results, data)
    for data in measurements.values():
        store_telemetry(results, data)
    for data in replays.values():
        store_replay(results, data)
    for data in cycles.values():
        store_cycle(results, data)
    for data in adoptions.values():
        atomic_json(safe_path(results) / data["project_id"] / data["run_id"] / "adoption.json", data, immutable=True)
    return reindex(root, results)


def merge_samples(root, results):
    source = Path(root) / "results"
    for report in load_reports(source):
        if report["origin"] != "sample":
            continue
        folder = source / report["project_id"] / report["run_id"]
        lifecycle = validate_evolution(read_json(folder / "skill-evolution.json", EVOLUTION_LIMIT), report)
        assessment = assessments.validate(read_json(folder / "skill-assessments.json"), report, lifecycle)
        store(results, report)
        store_evolution(results, lifecycle)
        store_assessments(results, assessment)
    return reindex(root, results)


def store_telemetry(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")))
    folder = Path(results) / data["project_id"] / data["run_id"]
    report = validate(read_json(folder / "report.json"))
    path = safe_path(folder / "skill-assessments.json")
    assessment = read_json(path) if path.exists() else None
    if assessment is not None:
        lifecycle = validate_evolution(read_json(folder / "skill-evolution.json", EVOLUTION_LIMIT), report)
        assessments.validate(assessment, report, lifecycle)
    telemetry.validate(data, report, assessment)
    path = folder / "stage-metrics.json"
    atomic_json(path, data, immutable=True)
    return path


def load_telemetry(results, rows=None, skill_assessments=None):
    results = safe_path(results)
    rows = load_reports(results) if rows is None else rows
    reports = {(row["project_id"], row["run_id"]): row for row in rows}
    skill_assessments = load_assessments(results, rows) if skill_assessments is None else skill_assessments
    values = {}
    for path in sorted(results.glob("*/*/stage-metrics.json")):
        key = (path.parent.parent.name, path.parent.name)
        require(key in reports, "orphan_stage_metrics")
        values[key] = telemetry.validate(read_json(path), reports[key], skill_assessments.get(key))
    return values


def store_assessments(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")))
    folder = Path(results) / data["project_id"] / data["run_id"]
    report = validate(read_json(folder / "report.json"))
    snapshot_path = safe_path(folder / "skill-snapshots.json")
    snapshot = read_json(snapshot_path) if snapshot_path.exists() else None
    lifecycle = validate_evolution(read_json(folder / "skill-evolution.json", EVOLUTION_LIMIT), report, snapshot)
    assessments.validate(data, report, lifecycle)
    path = folder / "skill-assessments.json"
    atomic_json(path, data, immutable=True)
    return path


def load_assessments(results, rows=None, lifecycles=None):
    results = safe_path(results)
    rows = load_reports(results) if rows is None else rows
    reports = {(row["project_id"], row["run_id"]): row for row in rows}
    lifecycles = load_evolution(results, rows) if lifecycles is None else lifecycles
    values = {}
    for path in sorted(results.glob("*/*/skill-assessments.json")):
        key = (path.parent.parent.name, path.parent.name)
        require(key in reports, "orphan_skill_assessments")
        values[key] = assessments.validate(read_json(path), reports[key], lifecycles.get(key))
    return values


def store_replay(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")))
    folder = safe_path(results) / data["project_id"] / data["run_id"]
    report = validate(read_json(folder / "report.json"))
    lifecycle = validate_evolution(read_json(folder / "skill-evolution.json", EVOLUTION_LIMIT), report)
    assessments.validate_replay(data, report=report, lifecycle=lifecycle)
    path = folder / "replay-evaluation.json"
    atomic_json(path, data, immutable=True)
    return path


def load_replays(results, rows=None):
    results = safe_path(results)
    rows = load_reports(results) if rows is None else rows
    reports = {(row["project_id"], row["run_id"]): validate(row) for row in rows}
    lifecycles = load_evolution(results, rows)
    values = {}
    for path in sorted(results.glob("*/*/replay-evaluation.json")):
        key = (path.parent.parent.name, path.parent.name)
        require(key in reports and key in lifecycles, "orphan_replay")
        data = read_json(path)
        require(read_bytes(path) == encoded(data), "noncanonical_replay")
        values[key] = assessments.validate_replay(data, report=reports[key], lifecycle=lifecycles[key])
    return values


def validate_cycle(data, *, report, evaluations):
    from copilot_runtime import MIN_AI_CREDITS
    evolution.exact(data, "schema_version project_id run_id report_sha256 execution_mode cycle_id skill_key source_path "
                         "input_sha256 reference_sha256 original_version_id max_rounds budget rounds stop_reason "
                         "selected_candidate_version_id confirmation_ref confirmation_status")
    validate(report)
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_cycle")
    require(data["project_id"] == report["project_id"] and data["run_id"] == report["run_id"] == data["cycle_id"]
            and data["report_sha256"] == sha256(encoded(report)).hexdigest(), "cycle_report_mismatch")
    require(report["purpose"] == "project_assessment", "cycle_report_mismatch")
    require(data["execution_mode"] in ("live", "offline_test", "sample")
            and (data["execution_mode"] == "sample") == (report["origin"] == "sample"), "cycle_mode_mismatch")
    require(matches(evolution.SKILL_KEY, data["skill_key"]), "invalid_cycle")
    evolution.relative_path(data["source_path"])
    for key in ("input_sha256", "reference_sha256"):
        require(matches(evolution.DIGEST, data[key]), "invalid_cycle")
    require(matches(evolution.VERSION_ID, data["original_version_id"]), "invalid_cycle")
    require(type(data["max_rounds"]) is int and 1 <= data["max_rounds"] <= 10, "invalid_cycle")
    budget = data["budget"]
    evolution.exact(budget, "max_invocations max_seconds max_ai_credits_per_session")
    require(type(budget["max_invocations"]) is int and 1 <= budget["max_invocations"] <= 1000
            and type(budget["max_seconds"]) is int and 1 <= budget["max_seconds"] <= 7200, "invalid_cycle_budget")
    credit = budget["max_ai_credits_per_session"]
    require(credit is None or (type(credit) in (int, float) and math.isfinite(credit) and credit >= MIN_AI_CREDITS),
            "invalid_cycle_budget")
    reasons = ("improved", "max_rounds", "no_change", "call_limit", "time_limit", "credit_limit",
               "input_changed", "evaluation_unverified", "runtime_error", "cancelled")
    require(data["stop_reason"] in reasons, "invalid_cycle_stop")
    rounds = data["rounds"]
    require(isinstance(rounds, list) and len(rounds) <= data["max_rounds"]
            and isinstance(evaluations, dict), "invalid_cycle")
    used = {data["cycle_id"]}

    def resolve(ref):
        evolution.exact(ref, "project_id run_id path sha256")
        require(ref["project_id"] == data["project_id"] and matches(RUN, ref["run_id"])
                and ref["run_id"] not in used and ref["path"] == "replay-evaluation.json"
                and matches(evolution.DIGEST, ref["sha256"]), "invalid_cycle_reference")
        key = (ref["project_id"], ref["run_id"])
        require(key in evaluations, "missing_cycle_evaluation")
        item = evaluations[key]
        evolution.exact(item, "report lifecycle replay")
        replay = assessments.validate_replay(item["replay"], report=item["report"], lifecycle=item["lifecycle"])
        require(replay["project_id"] == ref["project_id"] and replay["run_id"] == ref["run_id"]
                and sha256(encoded(replay)).hexdigest() == ref["sha256"], "cycle_evidence_mismatch")
        require(replay["execution_mode"] == data["execution_mode"], "cycle_mode_mismatch")
        frozen = replay["reference"]
        require(all(frozen[key] == report[key] for key in ("project_id", "source_commit", "project_tree_sha256", "evaluator_sha256"))
                and frozen["skill_key"] == data["skill_key"] and frozen["source_path"] == data["source_path"]
                and frozen["original_version_id"] == data["original_version_id"], "cycle_reference_mismatch")
        used.add(ref["run_id"])
        return replay

    parent, previous_id, previous_quality, original_ref = data["original_version_id"], None, None, None
    development_work = None
    selected = None
    for number, row in enumerate(rounds, 1):
        evolution.exact(row, "round_id round_number run_id parent_version_id candidate_version_id input_sha256 "
                             "reference_sha256 feedback_source_round_id feedback_sha256 evaluation_ref decision stop_reason")
        require(type(row["round_number"]) is int and row["round_number"] == number
                and row["round_id"] == f"{data['cycle_id']}-r{number}", "invalid_cycle_round")
        require(row["run_id"] is None if row["evaluation_ref"] is None else
                matches(RUN, row["run_id"]) and row["run_id"] not in used, "invalid_cycle_round")
        require(row["parent_version_id"] == parent and row["feedback_source_round_id"] == previous_id,
                "cycle_lineage_mismatch")
        require(row["input_sha256"] == data["input_sha256"] and row["reference_sha256"] == data["reference_sha256"]
                and matches(evolution.DIGEST, row["feedback_sha256"]), "cycle_reference_mismatch")
        require(row["stop_reason"] is None or row["stop_reason"] in reasons, "invalid_cycle_stop")
        if number < len(rounds):
            require(row["stop_reason"] is None, "cycle_after_stop")
        else:
            require(row["stop_reason"] == data["stop_reason"], "cycle_stop_mismatch")
        if row["evaluation_ref"] is None:
            require(number == len(rounds) and row["decision"] is None
                    and row["stop_reason"] not in (None, "improved", "max_rounds"), "incomplete_cycle_round")
            require(row["candidate_version_id"] is None or matches(evolution.VERSION_ID, row["candidate_version_id"]),
                    "invalid_cycle_round")
            break
        replay = resolve(row["evaluation_ref"])
        evaluation, generation = replay["evaluation"], replay["generation"]
        require(generation is not None and row["run_id"] == replay["run_id"]
                and replay["reference"]["reference_sha256"] == data["reference_sha256"]
                and evaluation["work"]["split"] == "development", "cycle_reference_mismatch")
        if original_ref is None:
            original_ref = replay["reference"]
            previous_quality = original_ref["base_quality"]
            development_work = evaluation["work"]
        require(evaluation["work"] == development_work, "cycle_work_mismatch")
        require(row["candidate_version_id"] == evaluation["candidate_version_id"]
                and row["candidate_version_id"] != parent
                and row["decision"] == evaluation["decision"]
                and generation["parent_version_id"] == parent
                and generation["feedback_sha256"] == row["feedback_sha256"], "cycle_lineage_mismatch")
        known = {item["id"] for name in ("dimensions", "findings") for item in (previous_quality or {}).get(name, [])}
        require(set(generation["addressed_findings"]) <= known, "cycle_feedback_mismatch")
        status = evaluation["decision"]["status"]
        incomplete = evaluation["errors"] or any(
            value is None for group in ("quality", "checks", "applications") for value in evaluation[group].values())
        if incomplete:
            require(number == len(rounds) and row["stop_reason"] in (
                "evaluation_unverified", "runtime_error", "call_limit", "time_limit",
                "credit_limit", "input_changed", "cancelled"), "cycle_after_incomplete_evaluation")
        elif status == "improved":
            require(number == len(rounds) and row["stop_reason"] == "improved", "cycle_after_improvement")
            selected = evaluation["candidate_version_id"]
        elif status == "unverified":
            require(number == len(rounds) and row["stop_reason"] == "evaluation_unverified", "cycle_after_unverified")
        else:
            require(row["stop_reason"] in (None, "max_rounds", "call_limit", "time_limit", "credit_limit",
                                           "input_changed", "runtime_error", "cancelled"), "invalid_cycle_stop")
        parent, previous_id = row["candidate_version_id"], row["round_id"]
        previous_quality = evaluation["quality"]["candidate"]
    if data["stop_reason"] == "max_rounds":
        require(len(rounds) == data["max_rounds"], "cycle_stop_mismatch")
    if not rounds:
        require(data["stop_reason"] not in ("improved", "max_rounds", "no_change"), "cycle_stop_mismatch")
    require(data["selected_candidate_version_id"] == selected
            and (data["stop_reason"] == "improved") == (selected is not None), "cycle_selection_mismatch")
    if data["confirmation_ref"] is None:
        require(data["confirmation_status"] in ("not_run", "unverified"), "cycle_confirmation_mismatch")
    else:
        require(selected is not None and original_ref is not None, "cycle_confirmation_mismatch")
        confirmation = resolve(data["confirmation_ref"])
        evaluation = confirmation["evaluation"]
        require(confirmation["generation"] is None and evaluation["candidate_version_id"] == selected
                and evaluation["work"]["split"] == "confirmation", "cycle_confirmation_mismatch")
        development = evaluations[(data["project_id"], rounds[0]["run_id"])]["replay"]["evaluation"]["work"]
        final = evaluation["work"]
        require(final["input_sha256"] != development["input_sha256"] and final["task_id"] != development["task_id"]
                and not set(final["checks"]["required_case_ids"]) & set(development["checks"]["required_case_ids"]),
                "cycle_confirmation_overlap")
        require(all(confirmation["reference"][key] == original_ref[key] for key in
                    ("rubric_sha256", "quality_context_sha256", "policy_sha256", "plan_sha256",
                     "environment_sha256", "protected_sha256")), "cycle_confirmation_mismatch")
        status = evaluation["decision"]["status"]
        expected = "passed" if status in ("improved", "not_improved") else "failed" if status == "rejected" else "unverified"
        require(data["confirmation_status"] == expected, "cycle_confirmation_mismatch")
    require(len(encoded(data)) <= LIMIT, "output_limit")
    return data


def load_replay_evidence(results, rows=None):
    """Load the transitive report/capture/replay mapping required by cycle validation."""
    rows = load_reports(results) if rows is None else rows
    reports = {(row["project_id"], row["run_id"]): validate(row) for row in rows}
    replays = load_replays(results, rows)
    lifecycles = load_evolution(results, rows)
    return {key: {"report": reports[key], "lifecycle": lifecycles[key], "replay": replay}
            for key, replay in replays.items()}


def store_cycle(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")))
    folder = safe_path(results) / data["project_id"] / data["run_id"]
    report = validate(read_json(folder / "report.json"))
    validate_cycle(data, report=report, evaluations=load_replay_evidence(results))
    path = folder / "cycle.json"
    atomic_json(path, data, immutable=True)
    return path


def load_cycles(results, rows=None):
    results = safe_path(results)
    rows = load_reports(results) if rows is None else rows
    reports = {(row["project_id"], row["run_id"]): validate(row) for row in rows}
    evaluations = load_replay_evidence(results, rows)
    values = {}
    for path in sorted(results.glob("*/*/cycle.json")):
        key = (path.parent.parent.name, path.parent.name)
        require(key in reports, "orphan_cycle")
        data = read_json(path)
        require(read_bytes(path) == encoded(data), "noncanonical_cycle")
        values[key] = validate_cycle(data, report=reports[key], evaluations=evaluations)
    return values


APPROVAL_PUBLIC_FIELDS = (
    "approval_id project_id skill_key candidate_version_id cycle_id evidence_sha256 "
    "approved_at approved_by trust_scope previous_active_version_id"
)
EXECUTION_PUBLIC_FIELDS = (
    "execution_id run_id project_id skill_key approval_id evidence_sha256 work_input_sha256 "
    "approved_version_id loaded_version_id skill_version_verified observed_at status reason_code"
)


def _validate_adoption_graph(values, *, reports, cycles, evaluations=()):
    approvals, executions, runs = {}, {}, {}
    for key, data in values.items():
        evolution.exact(data, "schema_version project_id run_id report_sha256 execution_mode approvals executions")
        require(key in reports, "orphan_adoption")
        require(key not in cycles and key not in evaluations, "adoption_run_reused")
        report = validate(reports[key])
        require(type(data["schema_version"]) is int and data["schema_version"] == 1
                and (data["project_id"], data["run_id"]) == key
                and data["report_sha256"] == sha256(encoded(report)).hexdigest(), "adoption_report_mismatch")
        require(data["execution_mode"] in ("live", "offline_test", "sample")
                and (data["execution_mode"] == "sample") == (report["origin"] == "sample")
                and report["origin"] != "historical_import", "adoption_mode_mismatch")
        require(isinstance(data["approvals"], list) and isinstance(data["executions"], list)
                and 0 < len(data["approvals"]) + len(data["executions"]) <= 256
                and len(encoded(data)) <= LIMIT, "invalid_adoption")
        local = set()
        for row in data["approvals"]:
            evolution.exact(row, APPROVAL_PUBLIC_FIELDS)
            require(matches(ID, row["approval_id"]) and row["approval_id"] not in local
                    and row["project_id"] == key[0] and matches(evolution.SKILL_KEY, row["skill_key"])
                    and matches(evolution.VERSION_ID, row["candidate_version_id"])
                    and matches(RUN, row["cycle_id"]) and matches(evolution.DIGEST, row["evidence_sha256"])
                    and row["approved_by"] == "local_operator" and row["trust_scope"] == "local_environment"
                    and (row["previous_active_version_id"] is None
                         or matches(evolution.VERSION_ID, row["previous_active_version_id"])), "invalid_public_approval")
            local.add(row["approval_id"])
            require(row["approved_at"] is not None, "invalid_public_approval")
            evolution.timestamp(row["approved_at"])
            cycle_key = (key[0], row["cycle_id"])
            require(cycle_key in cycles and cycle_key in reports, "missing_adoption_cycle")
            cycle = cycles[cycle_key]
            require(sha256(encoded(cycle)).hexdigest() == row["evidence_sha256"]
                    and cycle["skill_key"] == row["skill_key"]
                    and cycle["selected_candidate_version_id"] == row["candidate_version_id"]
                    and cycle["confirmation_status"] == "passed" and cycle["confirmation_ref"] is not None
                    and cycle["execution_mode"] == data["execution_mode"], "adoption_evidence_mismatch")
            require(all(report[field] == reports[cycle_key][field]
                        for field in ("source_commit", "project_tree_sha256", "evaluator_sha256")),
                    "adoption_source_mismatch")
            require(datetime.fromisoformat(row["approved_at"].replace("Z", "+00:00"))
                    >= datetime.fromisoformat(reports[cycle_key]["created_at"].replace("Z", "+00:00")),
                    "adoption_time_mismatch")
            bound = (data["execution_mode"], row)
            require(row["approval_id"] not in approvals or approvals[row["approval_id"]] == bound,
                    "conflicting_public_approval")
            approvals[row["approval_id"]] = bound
    for data in values.values():
        local = set()
        for row in data["executions"]:
            evolution.exact(row, EXECUTION_PUBLIC_FIELDS)
            require(matches(ID, row["execution_id"]) and row["execution_id"] not in local
                    and row["project_id"] == data["project_id"] and matches(RUN, row["run_id"])
                    and matches(evolution.SKILL_KEY, row["skill_key"]) and matches(ID, row["approval_id"])
                    and matches(evolution.DIGEST, row["evidence_sha256"])
                    and matches(evolution.DIGEST, row["work_input_sha256"])
                    and matches(evolution.VERSION_ID, row["approved_version_id"])
                    and (row["loaded_version_id"] is None or matches(evolution.VERSION_ID, row["loaded_version_id"]))
                    and type(row["skill_version_verified"]) is bool
                    and row["status"] in ("verified", "failed", "blocked")
                    and (row["reason_code"] is None or matches(r"[a-z0-9_]{1,128}", row["reason_code"])),
                    "invalid_public_execution")
            local.add(row["execution_id"])
            require(row["observed_at"] is not None, "invalid_public_execution")
            evolution.timestamp(row["observed_at"])
            require(row["approval_id"] in approvals, "missing_public_approval")
            mode, approval = approvals[row["approval_id"]]
            require(mode == data["execution_mode"] and all(row[field] == approval[field]
                    for field in ("project_id", "skill_key", "evidence_sha256"))
                    and row["approved_version_id"] == approval["candidate_version_id"],
                    "adoption_binding_mismatch")
            require(datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                    >= datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00")),
                    "adoption_time_mismatch")
            run_key = (row["project_id"], row["run_id"])
            require(run_key in reports and row["run_id"] != approval["cycle_id"], "missing_execution_report")
            require(run_key not in cycles and run_key not in evaluations, "execution_run_reused")
            require(run_key not in runs or runs[run_key] == row["execution_id"], "execution_run_reused")
            runs[run_key] = row["execution_id"]
            source = reports[(row["project_id"], approval["cycle_id"])]
            publication = reports[(data["project_id"], data["run_id"])]
            require(all(reports[run_key][field] == publication[field] == source[field]
                        for field in ("source_commit", "project_tree_sha256", "evaluator_sha256"))
                    and (mode == "sample") == (reports[run_key]["origin"] == "sample")
                    and reports[run_key]["origin"] != "historical_import", "adoption_source_mismatch")
            if row["status"] == "verified":
                require(row["skill_version_verified"] and row["loaded_version_id"] == row["approved_version_id"]
                        and row["reason_code"] is None, "unverified_public_execution")
            else:
                require(not row["skill_version_verified"] and row["reason_code"] is not None,
                        "invalid_public_execution")
            bound = (mode, row)
            require(row["execution_id"] not in executions or executions[row["execution_id"]] == bound,
                    "conflicting_public_execution")
            executions[row["execution_id"]] = bound
    return values


def validate_adoption(data, *, report, reports, cycles, adoptions=None, evaluations=()):
    """Validate a public observation graph; validated cycles confer no local execution authority."""
    validate(report)
    key = (report["project_id"], report["run_id"])
    values = dict(adoptions or {})
    values[key] = data
    _validate_adoption_graph(values, reports={**reports, key: report}, cycles=cycles, evaluations=evaluations)
    return data


def load_adoptions(results, rows=None):
    results = safe_path(results)
    rows = load_reports(results) if rows is None else rows
    reports = {(row["project_id"], row["run_id"]): row for row in rows}
    values = {}
    for path in sorted(results.glob("*/*/adoption.json")):
        data = read_json(path)
        require(read_bytes(path) == encoded(data), "noncanonical_adoption")
        values[(path.parent.parent.name, path.parent.name)] = data
    return _validate_adoption_graph(values, reports=reports, cycles=load_cycles(results, rows),
                                    evaluations=load_replays(results, rows))


def store_adoption(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")),
            "invalid_adoption")
    reports = {(row["project_id"], row["run_id"]): row for row in load_reports(results)}
    key = (data["project_id"], data["run_id"])
    require(key in reports, "orphan_adoption")
    validate_adoption(data, report=reports[key], reports=reports, cycles=load_cycles(results),
                      adoptions=load_adoptions(results), evaluations=load_replays(results))
    path = safe_path(results) / key[0] / key[1] / "adoption.json"
    atomic_json(path, data, immutable=True)
    return path


def validate_evolution(data, report, snapshots=None):
    validate(report)
    evolution.exact(data, "schema_version project_id run_id report_sha256 records bindings file_contents")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    require(data["project_id"] == report["project_id"] and data["run_id"] == report["run_id"])
    require(data["report_sha256"] == sha256(encoded(report)).hexdigest(), "evolution_report_mismatch")
    require(len(encoded(data)) <= EVOLUTION_LIMIT, "output_limit")
    rows = evolution.validate_public_records(data["records"])
    versions = {item["version_id"]: item for item in rows["versions"]}
    identities = {item["skill_key"] for item in rows["identities"]}
    require(identities, "empty_evolution_identity")
    associations = {(item["skill_key"], item["version_id"]) for item in rows["skill_versions"]}
    require(isinstance(data["bindings"], list) and len(data["bindings"]) == len(identities))
    keys, legacy = set(), []
    original_run = report["run_id"].removeprefix("import-").removeprefix("local-")
    for binding in data["bindings"]:
        evolution.exact(binding, "skill_key base_version_id candidate_version_id legacy_skill_id")
        key = binding["skill_key"]
        require(evolution.matches(evolution.SKILL_KEY, key) and key in identities and key not in keys)
        keys.add(key)
        for arm in ("base", "candidate"):
            value = binding[f"{arm}_version_id"]
            require(value is None or (evolution.matches(evolution.VERSION_ID, value) and (key, value) in associations))
        if report["purpose"] == "baseline":
            require(binding["candidate_version_id"] is None, "future_evolution_binding")
        if binding["legacy_skill_id"] is not None:
            require(matches(ID, binding["legacy_skill_id"]))
            legacy.append(binding)
        for collection, ref_name in (("generations", "candidate_ref"), ("comparisons", "comparison_ref")):
            for link in rows[collection]:
                ref = link[ref_name]
                if link["skill_key"] == key and ref["run_id"] == original_run:
                    require(ref["kind"] == report["purpose"], "evolution_evidence_mismatch")
                    for arm in ("base", "candidate"):
                        require(binding[f"{arm}_version_id"] == link[f"{arm}_version_id"],
                                "evolution_binding_mismatch")
                    if report["origin"] == "historical_import":
                        require(ref["artifact_sha256"] == report["source_report_sha256"],
                                "evolution_evidence_mismatch")
                    if collection == "comparisons":
                        require(link["decision"] == report["execution"]["decision"])
    for item in rows["sources"] + rows["adoptions"]:
        require(item["project_id"] == report["project_id"], "evolution_project_mismatch")
    if snapshots is None:
        require(not legacy, "missing_legacy_skill_binding")
    else:
        validate_snapshots(snapshots, report)
        require(len(legacy) == 1 and legacy[0]["legacy_skill_id"] == snapshots["skill_id"],
                "evolution_snapshot_mismatch")
        for arm in ("base", "candidate"):
            version_id = legacy[0][f"{arm}_version_id"]
            snapshot = snapshots[arm]
            require((version_id is None) == (snapshot is None), "evolution_snapshot_mismatch")
            if snapshot is not None:
                require(evolution.entrypoint_hash(versions[version_id]) == snapshot["sha256"],
                        "evolution_snapshot_mismatch")
    expected = {(version["version_id"], item["path"]): item
                for version in versions.values() for item in version["files"]}
    require(isinstance(data["file_contents"], list) and len(data["file_contents"]) == len(expected))
    seen = set()
    for item in data["file_contents"]:
        evolution.exact(item, "version_id path encoding data")
        require(evolution.matches(evolution.VERSION_ID, item["version_id"]))
        evolution.relative_path(item["path"])
        key = (item["version_id"], item["path"])
        require(key in expected and key not in seen and item["encoding"] == "base64")
        require(isinstance(item["data"], str))
        try:
            raw = base64.b64decode(item["data"], validate=True)
        except (binascii.Error, ValueError) as error:
            raise RuntimeFailure("invalid_evolution_content", "Expected canonical base64 content.") from error
        require(base64.b64encode(raw).decode("ascii") == item["data"])
        require(len(raw) == expected[key]["bytes"] and sha256(raw).hexdigest() == expected[key]["sha256"],
                "evolution_content_mismatch")
        seen.add(key)
    return data


def store_evolution(results, data):
    require(isinstance(data, dict) and matches(ID, data.get("project_id")) and matches(RUN, data.get("run_id")))
    folder = Path(results) / data["project_id"] / data["run_id"]
    snapshot_path = safe_path(folder / "skill-snapshots.json")
    snapshots = read_json(snapshot_path) if snapshot_path.exists() else None
    validate_evolution(data, read_json(folder / "report.json"), snapshots)
    path = folder / "skill-evolution.json"
    atomic_json(path, data, immutable=True, limit=EVOLUTION_LIMIT)
    return path


def load_evolution(results, rows=None, snapshots=None):
    results = safe_path(results)
    reports = {(row["project_id"], row["run_id"]): row
               for row in (load_reports(results) if rows is None else rows)}
    snapshots = load_snapshots(results, list(reports.values())) if snapshots is None else snapshots
    values = {}
    for path in sorted(results.glob("*/*/skill-evolution.json")):
        key = (path.parent.parent.name, path.parent.name)
        require(key in reports, "orphan_skill_evolution")
        values[key] = validate_evolution(read_json(path, EVOLUTION_LIMIT), reports[key], snapshots.get(key))
    return values


def evolution_summary(data):
    names = {item["skill_key"]: item["display_name"] for item in data["records"]["identities"]}
    return [{"skill_key": item["skill_key"], "display_name": names[item["skill_key"]],
             "base_version_id": item["base_version_id"], "candidate_version_id": item["candidate_version_id"]}
            for item in data["bindings"]]


def import_skill_evolution(source, results, project, candidate_run, skill_key, legacy_skill_id):
    """Attach reviewed archived entrypoints and evidence; never infer a historical definition path."""
    require(matches(ID, project) and matches(ID, legacy_skill_id))
    require(evolution.matches(evolution.SKILL_KEY, skill_key))
    source = safe_path(source)
    generation, versions, retained = evolution.import_candidate(source, candidate_run, skill_key=skill_key)
    reports = load_reports(results)
    snapshots = load_snapshots(results, reports)
    existing = load_evolution(results, reports, snapshots)
    pending = []
    reused = 0
    for report in reports:
        key = (report["project_id"], report["run_id"])
        snapshot = snapshots.get(key)
        if (key[0] != project or report["origin"] != "historical_import" or
                snapshot is None or snapshot["skill_id"] != legacy_skill_id):
            continue
        name = {"baseline": "report.json", "candidate": "candidate.json",
                "comparison": "comparison.json"}.get(report["purpose"])
        if name is None:
            continue
        original_run = report["run_id"].removeprefix("import-")
        raw = evolution.archived(source, original_run, name)
        require(sha256(raw).hexdigest() == report["source_report_sha256"], "source_report_mismatch")
        original = evolution.artifact(raw, original_run, report["purpose"],
                                      ("completed", "completed_with_errors", "blocked", "failed"))
        comparison = None
        if report["purpose"] == "baseline":
            if original.get("skill_sha256") != generation["base_entrypoint_sha256"]:
                continue
            if key in existing:
                prior = existing[key]
                bindings = [item for item in prior["bindings"] if item["legacy_skill_id"] == legacy_skill_id]
                require(len(bindings) == 1 and bindings[0]["skill_key"] == skill_key,
                        "evolution_baseline_identity_mismatch")
                binding = bindings[0]
                require(binding["base_version_id"] is not None and binding["candidate_version_id"] is None,
                        "evolution_baseline_binding_mismatch")
                version = next(item for item in prior["records"]["versions"]
                               if item["version_id"] == binding["base_version_id"])
                require(evolution.entrypoint_hash(version) == generation["base_entrypoint_sha256"],
                        "evolution_baseline_content_mismatch")
                reused += 1
                continue
        elif report["purpose"] == "candidate":
            if original_run != candidate_run:
                continue
        else:
            if original.get("candidate_run") != candidate_run:
                continue
            comparison = evolution.import_comparison(source, original_run, skill_key=skill_key,
                                                       candidate_record=generation)
        candidate_version = generation["candidate_version_id"] if report["purpose"] != "baseline" else None
        used = {generation["base_version_id"], candidate_version} - {None}
        rows = evolution.empty_records()
        rows.update(
            identities=[{"skill_key": skill_key, "display_name": legacy_skill_id}],
            versions=[version for version in versions if version["version_id"] in used],
            skill_versions=[{"skill_key": skill_key, "version_id": identifier} for identifier in sorted(used)],
            sources=[{"skill_key": skill_key, "project_id": project, "kind": "run_archive", "scope": "unknown",
                      "path": f"runs/{candidate_run}", "observed_at": None,
                      "evidence_ref": evolution.evidence(report["purpose"], original_run, sha256(raw).hexdigest(), True)}],
            generations=[generation] if candidate_version else [],
            comparisons=[comparison] if comparison else [],
            adoptions=[evolution.observe_registry(None, project_id=project, repository_id="unobserved",
                       expected_engine_skill_id="develop", skill_key=skill_key, observed_at=None)],
        )
        data = {"schema_version": 1, "project_id": project, "run_id": report["run_id"],
                "report_sha256": sha256(encoded(report)).hexdigest(), "records": rows,
                "bindings": [{"skill_key": skill_key, "base_version_id": generation["base_version_id"],
                              "candidate_version_id": candidate_version, "legacy_skill_id": legacy_skill_id}],
                "file_contents": [{"version_id": identifier, "path": path, "encoding": "base64",
                                   "data": base64.b64encode(content).decode("ascii")}
                                  for identifier in sorted(used) for path, content in sorted(retained[identifier].items())]}
        pending.append(validate_evolution(data, report, snapshot))
    require(pending or reused, "no_matching_skill_records")
    for data in pending:
        path = safe_path(Path(results) / data["project_id"] / data["run_id"] / "skill-evolution.json")
        require(not path.exists() or read_bytes(path, EVOLUTION_LIMIT) == encoded(data), "immutable_conflict")
    for data in pending:
        store_evolution(results, data)
    return len(pending) + reused


def import_skill_snapshots(source, results, project, candidate_run, skill_id):
    require(matches(ID, project) and matches(ID, skill_id))
    require(matches(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", candidate_run))
    source = safe_path(source)
    candidate = read_json(source / candidate_run / "candidate.json")
    require(candidate.get("run_id") == candidate_run and candidate.get("purpose") == "candidate"
            and candidate.get("status") == "completed", "invalid_candidate")
    bundle = {}
    for arm, name, key in (("base", "base-SKILL.md", "base_skill_sha256"),
                           ("candidate", "SKILL.md", "skill_sha256")):
        raw = read_bytes(source / candidate_run / name, 32768)
        require(sha256(raw).hexdigest() == candidate.get(key), "snapshot_hash_mismatch")
        text = raw.decode("utf-8")
        frontmatter = text.split("---", 2)
        require(len(frontmatter) == 3 and not frontmatter[0].strip()
                and re.search(r"(?m)^name:\s*" + re.escape(skill_id) + r"\s*$", frontmatter[1]),
                "snapshot_skill_mismatch")
        bundle[arm] = {"content": text, "sha256": sha256(raw).hexdigest()}
    pending = []
    for row in load_reports(results):
        if row["project_id"] != project or row["origin"] != "historical_import":
            continue
        name = {"baseline": "report.json", "candidate": "candidate.json",
                "comparison": "comparison.json"}.get(row["purpose"])
        if name is None:
            continue
        original_id = row["run_id"].removeprefix("import-")
        require(matches(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", original_id))
        raw = read_bytes(source / original_id / name)
        require(sha256(raw).hexdigest() == row["source_report_sha256"], "source_report_mismatch")
        original = strict_json(raw.decode("utf-8"))
        require(original.get("run_id") == original_id and original.get("purpose") == row["purpose"])
        if row["purpose"] == "baseline":
            if original.get("skill_sha256") != bundle["base"]["sha256"]:
                continue
            to_be = None
        elif row["purpose"] == "candidate":
            if original_id != candidate_run:
                continue
            to_be = bundle["candidate"]
        else:
            if original.get("candidate_run") != candidate_run:
                continue
            require(original.get("skill_sha256") == {arm: value["sha256"] for arm, value in bundle.items()},
                    "comparison_skill_mismatch")
            to_be = bundle["candidate"]
        data = {"schema_version": 1, "project_id": project, "run_id": row["run_id"],
                "report_sha256": sha256(encoded(row)).hexdigest(), "skill_id": skill_id,
                "base": bundle["base"], "candidate": to_be}
        pending.append(validate_snapshots(data, row))
    require(pending, "no_matching_skill_records")
    for data in pending:
        store_snapshots(results, data)
    return len(pending)


def reindex(root, results):
    import skill_guide
    from skill_pipeline import skill_key

    current = {row["id"]: row for row in catalog(root)}
    fingerprint = evaluator_hash(root)
    grouped = {}
    all_rows = load_reports(results)
    snapshots = load_snapshots(results, all_rows)
    lifecycles = load_evolution(results, all_rows, snapshots)
    skill_assessments = load_assessments(results, all_rows, lifecycles)
    load_telemetry(results, all_rows, skill_assessments)
    replays = load_replays(results, all_rows)
    cycles = load_cycles(results, all_rows)
    adoptions = load_adoptions(results, all_rows)
    non_live = {key for group in (replays, cycles, adoptions) for key, data in group.items()
                if data["execution_mode"] != "live"}
    non_live.update((data["project_id"], row["run_id"]) for data in adoptions.values()
                    if data["execution_mode"] != "live" for row in data["executions"])
    for row in all_rows:
        grouped.setdefault(row["project_id"], []).append(row)
    entries = []
    for identifier in sorted(set(current) | set(grouped)):
        rows = sorted(grouped.get(identifier, []), key=lambda row: (row["created_at"], row["run_id"]), reverse=True)
        active = current.get(identifier)
        matching = [row for row in rows if active and not active["error"]
                    and row["origin"] in ("github_actions", "local")
                    and (row["project_id"], row["run_id"]) not in non_live
                    and row["project_tree_sha256"] == active["tree_sha256"]
                    and row["evaluator_sha256"] == fingerprint]
        entry = {
            "id": identifier, "state": "removed" if active is None else ("blocked" if active["error"] else "active"),
            "history_count": len(rows), "current_run": matching[0]["run_id"] if matching else None,
            "index": f"{identifier}/index.json",
            "detected_skills": [], "skill_discovery_error": None,
        }
        if active and not active["error"]:
            prior = [skill_assessments[(identifier, row["run_id"])] for row in rows
                     if row["origin"] != "sample" and (identifier, row["run_id"]) in skill_assessments]
            try:
                bundles = skill_guide.discover(Path(root) / "projects" / identifier)
                require(len(bundles) <= 256, "skill_inventory_limit")
                entry["detected_skills"] = [{
                    "skill_key": skill_key(identifier, bundle["path"], prior),
                    "display_name": Path(bundle["path"]).name, "source_path": bundle["path"],
                } for bundle in bundles]
            except RuntimeFailure as error:
                entry["skill_discovery_error"] = error.code
        elif active:
            entry["skill_discovery_error"] = active["error"]
        summaries = [{
            "run_id": row["run_id"], "created_at": row["created_at"], "origin": row["origin"],
            "purpose": row["purpose"], "guide_status": row["guide"]["status"],
            "execution_status": row["execution"]["status"], "report": f"{row['run_id']}/report.json",
        } for row in rows]
        for summary in summaries:
            snapshot = snapshots.get((identifier, summary["run_id"]))
            if snapshot is not None:
                summary.update(skill_id=snapshot["skill_id"],
                               skill_snapshots=f"{summary['run_id']}/skill-snapshots.json",
                               base_skill_sha256=snapshot["base"]["sha256"],
                               candidate_skill_sha256=snapshot["candidate"]["sha256"] if snapshot["candidate"] else None)
            lifecycle = lifecycles.get((identifier, summary["run_id"]))
            if lifecycle is not None:
                summary.update(skill_evolution=f"{summary['run_id']}/skill-evolution.json",
                               evolution_skills=evolution_summary(lifecycle))
            if (identifier, summary["run_id"]) in skill_assessments:
                summary["skill_assessments"] = f"{summary['run_id']}/skill-assessments.json"
            for mapping, key, filename in (
                (replays, "replay_evaluation", "replay-evaluation.json"), (cycles, "cycle", "cycle.json"),
                (adoptions, "adoption", "adoption.json"),
            ):
                if (identifier, summary["run_id"]) in mapping:
                    summary[key] = f"{summary['run_id']}/{filename}"
        atomic_json(Path(results) / identifier / "index.json", {"schema_version": 1, "project": entry, "history": summaries})
        entries.append(entry)
    index = {"schema_version": 1, "projects": entries}
    atomic_json(Path(results) / "index.json", index)
    return index


def export_legacy(data, project, digest):
    require(isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] in (1, 2))
    require(matches(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", data.get("run_id")))
    require(data.get("status") in ("completed", "completed_with_errors", "blocked", "failed"))
    purpose = data.get("purpose")
    require(purpose in ("baseline", "comparison", "candidate", "calibration"))
    metrics, decision = None, None
    if purpose == "baseline":
        original = data.get("aggregate")
        require(isinstance(original, dict))
        metrics = {key: original[key] for key in original if key in METRICS}
    elif purpose == "comparison":
        original = data.get("decision")
        require(isinstance(original, dict))
        decision = original.get("decision")
        metrics = {}
        for arm in ("base", "candidate"):
            summary = data.get("arms", {}).get(arm, {})
            for source, target in (("requested", "requested"), ("correctness_successes", "correctness_successes"),
                                   ("mean_judge_score", "judge_score")):
                metrics[f"{arm}_{target}"] = summary.get(source)
        measured = original.get("metrics")
        if measured is not None:
            require(isinstance(measured, dict) and measured.get("cost_unit") == "nano_aiu"
                    and measured.get("time_unit") == "seconds")
            for arm in ("base", "candidate"):
                metrics[f"{arm}_cost_nano_aiu"] = measured[arm].get("cost")
                metrics[f"{arm}_elapsed_seconds"] = measured[arm].get("time")
            for key in ("cost", "time"):
                metrics[f"{key}_improvement_percent"] = measured.get("improvement_percent", {}).get(key)
    elif purpose == "calibration":
        require(type(data.get("passed")) is bool and isinstance(data.get("results"), list))
        decision = "calibration_passed" if data["passed"] else "calibration_failed"
        metrics = {"controls": len(data["results"])}
    state = {"completed": "completed", "completed_with_errors": "failed", "blocked": "blocked", "failed": "failed"}[data["status"]]
    return validate({
        "schema_version": 1, "project_id": project, "run_id": "import-" + data["run_id"],
        "created_at": data.get("created_at"), "origin": "historical_import", "purpose": purpose,
        "source_commit": None, "project_tree_sha256": None, "evaluator_sha256": data.get("fingerprint"),
        "source_report_sha256": digest, "source_schema_version": data["schema_version"],
        "guide": axis("not_assessed", "historical_import"),
        "execution": axis(state, "historical_import", metrics, decision),
    })


def import_history(source, target, project):
    count = 0
    for name in ("report.json", "comparison.json", "candidate.json", "calibration.json"):
        for path in sorted(Path(source).glob("*/" + name)):
            raw = read_bytes(path)
            data = strict_json(raw.decode("utf-8"))
            require(data.get("run_id") == path.parent.name)
            store(target, export_legacy(data, project, sha256(raw).hexdigest()))
            count += 1
    return count


def validate_public_examples(index, rows, lifecycles, cycles):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.hrefs.extend(value for name, value in attrs if name == "href" and value is not None)

    links = Links()
    links.feed(index)
    known = {(row["project_id"], row["run_id"]) for row in rows}
    for href in links.hrefs:
        url = urlsplit(href)
        if url.scheme or url.netloc or url.path not in ("", "/"):
            continue
        query = parse_qs(url.query, keep_blank_values=True)
        if "run" not in query:
            continue
        require(len(query.get("project", [])) == 1 and len(query["run"]) == 1
                and ("skill" not in query or len(query["skill"]) == 1), "invalid_public_example")
        key = (query["project"][0], query["run"][0])
        require(key in known, "public_example_missing")
        if "skill" in query:
            keys = {row["skill_key"] for row in lifecycles.get(key, {}).get("bindings", [])}
            if key in cycles:
                keys.add(cycles[key]["skill_key"])
            require(query["skill"][0] in keys, "public_example_skill_missing")


def build(root, results, output):
    root, output = Path(root), safe_path(output)
    require(not output.exists(), "output_exists")
    rows = load_reports(results)
    snapshots = load_snapshots(results, rows)
    lifecycles = load_evolution(results, rows, snapshots)
    skill_assessments = load_assessments(results, rows, lifecycles)
    measurements = load_telemetry(results, rows, skill_assessments)
    replays = load_replays(results, rows)
    cycles = load_cycles(results, rows)
    adoptions = load_adoptions(results, rows)
    index = read_bytes(root / "dashboard/index.html").decode("utf-8")
    validate_public_examples(index, rows, lifecycles, cycles)
    output.mkdir(parents=True)
    modules = {}

    def rewrite_import(match):
        require(match[2] in modules, "invalid_dashboard_import")
        return f"{match[1]}./{modules[match[2]]}{match[3]}"

    for name in ("views.js", "evolution.js", "assessments.js", "trace.js", "app.js"):
        source = read_bytes(root / "dashboard" / name).decode("utf-8")
        raw = re.sub(r"(?m)^(\s*import\b[^;]*?\bfrom\s*['\"])\./([^'\"]+\.js)(['\"])",
                     rewrite_import, source).encode("utf-8")
        modules[name] = f"{Path(name).stem}.{sha256(raw).hexdigest()[:12]}.js"
        (output / modules[name]).write_bytes(raw)
    require(index.count('src="/app.js"') == 1, "invalid_dashboard_entrypoint")
    (output / "index.html").write_bytes(index.replace('src="/app.js"', f'src="/{modules["app.js"]}"').encode("utf-8"))
    config = read_json(root / "dashboard/staticwebapp.config.json")
    config["routes"].extend({"route": f"/{name}", "headers": {
        "Cache-Control": "public, max-age=31536000, immutable"}} for name in modules.values())
    (output / "staticwebapp.config.json").write_bytes(encoded(config))
    for name in ("styles.css", "sample-data.json"):
        raw = read_bytes(root / "dashboard" / name)
        (output / name).write_bytes(raw)
    for row in rows:
        store(output / "results", row)
    for data in snapshots.values():
        store_snapshots(output / "results", data)
    for data in lifecycles.values():
        store_evolution(output / "results", data)
    for data in skill_assessments.values():
        store_assessments(output / "results", data)
    for data in measurements.values():
        store_telemetry(output / "results", data)
    for data in replays.values():
        store_replay(output / "results", data)
    for data in cycles.values():
        store_cycle(output / "results", data)
    for data in adoptions.values():
        atomic_json(output / "results" / data["project_id"] / data["run_id"] / "adoption.json",
                    data, immutable=True)
    return reindex(root, output / "results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    scan = sub.add_parser("index")
    scan.add_argument("--results", type=Path, default=Path("results"))
    check = sub.add_parser("validate")
    check.add_argument("--results", type=Path, required=True)
    merge = sub.add_parser("merge")
    merge.add_argument("--incoming", type=Path, required=True)
    merge.add_argument("--results", type=Path, required=True)
    samples = sub.add_parser("merge-samples")
    samples.add_argument("--results", type=Path, required=True)
    legacy = sub.add_parser("import-history")
    legacy.add_argument("--source", type=Path, required=True)
    legacy.add_argument("--results", type=Path, default=Path("results"))
    legacy.add_argument("--project", required=True)
    skills = sub.add_parser("import-skill-snapshots")
    skills.add_argument("--source", type=Path, required=True)
    skills.add_argument("--results", type=Path, required=True)
    skills.add_argument("--project", required=True)
    skills.add_argument("--candidate-run", required=True)
    skills.add_argument("--skill-id", required=True)
    skills.add_argument("--reviewed", action="store_true", required=True,
                        help="Confirm the exact archived Skill texts were reviewed for public disclosure.")
    lifecycle = sub.add_parser("import-skill-evolution")
    lifecycle.add_argument("--source", type=Path, required=True)
    lifecycle.add_argument("--results", type=Path, required=True)
    lifecycle.add_argument("--project", required=True)
    lifecycle.add_argument("--candidate-run", required=True)
    lifecycle.add_argument("--skill-key", required=True)
    lifecycle.add_argument("--legacy-skill-id", required=True)
    lifecycle.add_argument("--reviewed", action="store_true", required=True,
                           help="Confirm explicit Skill registration and review of all retained content for disclosure.")
    site = sub.add_parser("build")
    site.add_argument("--results", type=Path, default=Path("results"))
    site.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            value = catalog(args.root)
        elif args.command == "validate":
            value = {"validated": len(load_reports(args.results))}
            load_snapshots(args.results)
            load_evolution(args.results)
            load_assessments(args.results)
            load_telemetry(args.results)
            load_cycles(args.results)
            load_adoptions(args.results)
        elif args.command == "import-history":
            value = {"imported": import_history(args.source, args.results, args.project)}
            reindex(args.root, args.results)
        elif args.command == "merge":
            value = merge_results(args.root, args.incoming, args.results)
        elif args.command == "merge-samples":
            value = merge_samples(args.root, args.results)
        elif args.command == "import-skill-snapshots":
            value = {"attached": import_skill_snapshots(
                args.source, args.results, args.project, args.candidate_run, args.skill_id)}
            reindex(args.root, args.results)
        elif args.command == "import-skill-evolution":
            value = {"attached": import_skill_evolution(
                args.source, args.results, args.project, args.candidate_run, args.skill_key, args.legacy_skill_id)}
            reindex(args.root, args.results)
        elif args.command == "build":
            value = build(args.root, args.results, args.output)
        else:
            value = reindex(args.root, args.results)
        print(json.dumps(value, indent=2))
        return 0
    except (RuntimeFailure, OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "results_io_or_contract"
        print(json.dumps({"status": "blocked", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
