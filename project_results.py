"""Bounded public results, project identity and static dashboard builds."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

from copilot_runtime import RuntimeFailure, strict_json


ID = r"[a-z0-9][a-z0-9_-]{0,63}"
RUN = r"(?:[0-9]+-[0-9]+|(?:import-|local-)?[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12})"
LIMIT = 1024 * 1024
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
    "call_limit", "time_limit",
}
STATES = {"completed", "failed", "blocked", "not_assessed", "configuration_required"}
DECISIONS = {None, "rejected", "eligible_for_canary", "blocked", "calibration_passed", "calibration_failed"}
CORE = (
    "evaluation.py", "copilot_runtime.py", "skillops.py", "candidates.py", "repositories.py",
    "project_results.py", "project_evaluation.py", "project_profiles.json", "skill_guide.py",
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


def read_json(path):
    try:
        return strict_json(read_bytes(path).decode("utf-8"))
    except UnicodeError as error:
        raise RuntimeFailure("invalid_encoding", "Expected UTF-8 JSON.") from error


def encoded(data):
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def atomic_json(path, data, immutable=False):
    path = safe_path(path)
    payload = encoded(data)
    require(len(payload) <= LIMIT, "output_limit")
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
                require(read_bytes(path) == payload, "immutable_conflict")
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
    require(row["origin"] in ("github_actions", "historical_import", "local"))
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
    if row["origin"] == "historical_import":
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


def reindex(root, results):
    current = {row["id"]: row for row in catalog(root)}
    fingerprint = evaluator_hash(root)
    grouped = {}
    for row in load_reports(results):
        grouped.setdefault(row["project_id"], []).append(row)
    entries = []
    for identifier in sorted(set(current) | set(grouped)):
        rows = sorted(grouped.get(identifier, []), key=lambda row: (row["created_at"], row["run_id"]), reverse=True)
        active = current.get(identifier)
        matching = [row for row in rows if active and not active["error"]
                    and row["origin"] != "historical_import"
                    and row["project_tree_sha256"] == active["tree_sha256"]
                    and row["evaluator_sha256"] == fingerprint]
        entry = {
            "id": identifier, "state": "removed" if active is None else ("blocked" if active["error"] else "active"),
            "history_count": len(rows), "current_run": matching[0]["run_id"] if matching else None,
            "index": f"{identifier}/index.json",
        }
        summaries = [{
            "run_id": row["run_id"], "created_at": row["created_at"], "origin": row["origin"],
            "purpose": row["purpose"], "guide_status": row["guide"]["status"],
            "execution_status": row["execution"]["status"], "report": f"{row['run_id']}/report.json",
        } for row in rows]
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


def build(root, results, output):
    root, output = Path(root), safe_path(output)
    require(not output.exists(), "output_exists")
    rows = load_reports(results)
    output.mkdir(parents=True)
    for name in ("index.html", "styles.css", "app.js", "staticwebapp.config.json"):
        raw = read_bytes(root / "dashboard" / name)
        (output / name).write_bytes(raw)
    for row in rows:
        store(output / "results", row)
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
    legacy = sub.add_parser("import-history")
    legacy.add_argument("--source", type=Path, required=True)
    legacy.add_argument("--results", type=Path, default=Path("results"))
    legacy.add_argument("--project", required=True)
    site = sub.add_parser("build")
    site.add_argument("--results", type=Path, default=Path("results"))
    site.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            value = catalog(args.root)
        elif args.command == "validate":
            value = {"validated": len(load_reports(args.results))}
        elif args.command == "import-history":
            value = {"imported": import_history(args.source, args.results, args.project)}
            reindex(args.root, args.results)
        elif args.command == "merge":
            rows = load_reports(args.incoming)
            for row in rows:
                store(args.results, row)
            value = reindex(args.root, args.results)
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
