"""Export a completed, saturated original-Skill screen without private content."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import math
from pathlib import Path
import sys

from copilot_runtime import RuntimeFailure
import gepa_search
import project_checks
import project_results as results
import skill_assessments
from reviewed_publication import git


def committed_identity(root, commit, project_id):
    """Read pinned Git blobs, not today's evaluator or executable historical code."""
    results.require(results.matches(r"[a-f0-9]{40}", commit)
                    and results.matches(results.ID, project_id), "invalid_baseline_identity")
    entries = {}
    prefix = f"projects/{project_id}/"
    for item in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if not item:
            continue
        metadata, raw_name = item.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8")
        evaluator = (name in results.CORE or name.startswith(("eval/", "skills/"))
                     and "__pycache__" not in Path(name).parts and not name.endswith(".pyc"))
        if not evaluator and not name.startswith(prefix):
            continue
        results.require(mode in ("100644", "100755") and kind == "blob", "unsafe_baseline_source")
        entries[name] = sha256(git(root, "cat-file", "blob", oid, limit=results.LIMIT)).hexdigest()
    project = [[name[len(prefix):], digest] for name, digest in sorted(entries.items())
               if name.startswith(prefix)]
    results.require(bool(project), "missing_baseline_project")
    paths = set(results.CORE) | {name for name in entries if name.startswith(("eval/", "skills/"))}
    evaluator = [[name, entries.get(name)] for name in sorted(paths)]
    return sha256(results.encoded(project)).hexdigest(), sha256(results.encoded(evaluator)).hexdigest()


def _measurement(receipt, key, unit):
    usage = receipt.get("usage", {})
    results.require(isinstance(usage, dict), "invalid_baseline_usage")
    if key not in usage:
        return None
    metric = usage[key]
    value = metric["value"]
    results.require(metric["unit"] == unit and (value is None or
                    type(value) is int and value >= 0), "invalid_baseline_usage")
    return value


def project_report(summary, work, receipt, *, tree_hash, evaluator_hash, created_at, digest):
    results.require(summary["status"] == "completed" and summary["execution_mode"] == "live"
                    and summary["eligible_for_gepa"] is False
                    and summary["next_action"] == "stop_baseline_saturated"
                    and summary["confirmation_status"] == "not_run"
                    and summary["approval_eligible"] is False
                    and type(summary["model_calls_attempted"]) is int
                    and summary["model_calls_attempted"] == 1, "unsupported_baseline_screen")
    results.require(work["schema_version"] == 1 and type(work["schema_version"]) is int
                    and work["split"] == "development", "invalid_baseline_work")
    results.require(work["input_sha256"] == sha256(results.encoded(
        {key: value for key, value in work.items() if key != "input_sha256"})).hexdigest(),
        "baseline_work_hash_mismatch")
    results.require(all(summary[key] == work[key] for key in
                        ("project_id", "task_id", "source_commit", "input_sha256"))
                    and work["project_tree_sha256"] == tree_hash, "baseline_identity_mismatch")
    results.require(receipt["status"] == "completed" and type(receipt["returncode"]) is int
                    and receipt["returncode"] == 0 and receipt["role"] == "developer"
                    and receipt["requested_model"] == summary["model"]
                    and receipt["skill_activated"] is True
                    and receipt["skill_version_verified"] is True, "unverified_baseline_receipt")
    application = summary["application"]
    version = summary["original_version_id"]
    results.require(results.matches(r"sha256:[a-f0-9]{64}", version)
                    and receipt["staged_version_id"] == application["version_id"]
                    == application["staged_version_id"] == version
                    and application["activated"] is True
                    and application["work_sha256"] == work["input_sha256"]
                    and application["task_outcome"] == "satisfied", "baseline_application_mismatch")
    observation = project_checks.validate_observation(summary["checks"])
    skill_assessments._work_checks(work["checks"])
    results.require(observation["plan_sha256"] == work["checks"]["plan_sha256"]
                    and observation["protected_sha256"] == work["checks"]["protected_sha256"],
                    "baseline_checks_mismatch")
    scores = gepa_search.case_scores(observation, work)
    results.require(scores and all(scores) and isinstance(summary["required_scores"], list)
                    and all(type(value) is int for value in summary["required_scores"])
                    and summary["required_scores"] == scores
                    and type(summary["required_total"]) is int
                    and type(summary["required_passed"]) is int
                    and summary["required_total"] == summary["required_passed"] == len(scores),
                    "baseline_score_mismatch")
    elapsed = summary["elapsed_seconds"]
    measured = receipt["elapsed_seconds"]
    results.require(all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                        for value in (elapsed, measured)) and elapsed >= measured,
                    "invalid_baseline_time")
    cost = _measurement(receipt, "nano_aiu", "nano_aiu")
    results.require(type(application["measurement"]["cost_nano_aiu"]) is type(cost)
                    and application["measurement"]["cost_nano_aiu"] == cost
                    and type(application["measurement"]["elapsed_seconds"]) in (int, float)
                    and application["measurement"]["elapsed_seconds"] == measured,
                    "baseline_measurement_mismatch")
    stamp = datetime.fromisoformat(created_at)
    return results.validate({
        "schema_version": 1, "project_id": work["project_id"],
        "run_id": "local-" + stamp.strftime("%Y%m%dT%H%M%SZ") + "-" + digest[:12],
        "created_at": created_at, "origin": "local", "purpose": "baseline",
        "source_commit": work["source_commit"], "project_tree_sha256": tree_hash,
        "evaluator_sha256": evaluator_hash, "source_report_sha256": digest,
        "source_schema_version": None,
        "guide": results.axis("not_assessed", "baseline_screening_only"),
        "execution": results.axis("completed", "baseline_saturated", {
            "requested": 1, "attempted": 1, "evaluation_completed": 1,
            "correctness_successes": 1, "cli_invocations": 1,
            "required_cases": len(scores), "passed_cases": sum(scores),
            "base_cost_nano_aiu": cost, "base_elapsed_seconds": measured,
            "total_elapsed_seconds": elapsed,
            "input_tokens": _measurement(receipt, "input_tokens", "tokens"),
            "output_tokens": _measurement(receipt, "output_tokens", "tokens"),
        }),
    })


def export(root, summary_path, work_path, receipt_path, output):
    summary_path = results.safe_path(summary_path)
    raw = results.read_bytes(summary_path)
    summary = results.read_json(summary_path)
    tree, evaluator = committed_identity(root, summary["source_commit"], summary["project_id"])
    row = project_report(
        summary, results.read_json(work_path), results.read_json(receipt_path),
        tree_hash=tree, evaluator_hash=evaluator,
        created_at=datetime.fromtimestamp(summary_path.stat().st_mtime, timezone.utc).isoformat(),
        digest=sha256(raw).hexdigest())
    results.store(output, row)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--summary", required=True)
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--results", required=True)
    args = parser.parse_args(argv)
    try:
        root = results.safe_path(args.root)
        row = export(root, root / args.summary, root / args.work_item, root / args.receipt,
                     root / args.results)
        print(results.encoded({"status": "exported", "project_id": row["project_id"],
                              "run_id": row["run_id"], "model_calls": 0,
                              "deployed": False}).decode(), end="")
        return 0
    except (RuntimeFailure, OSError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "invalid_baseline_input"
        print(f"Baseline export failed: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
