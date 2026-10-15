"""Project one original-only quality evaluation into bounded dashboard metrics."""

import argparse
from datetime import datetime
from hashlib import sha256
import math
from pathlib import Path
import sys
import tempfile

from baseline_results import _measurement, committed_identity
from copilot_runtime import RuntimeFailure, strict_json
import evolution_records as evolution
import project_results as results
from reviewed_publication import git
import skill_guide


def project_report(summary, result, receipt, rubric, *, digest, tree_hash, evaluator_hash, version_id):
    results.require(type(summary["schema_version"]) is int and summary["schema_version"] == 1
                    and summary["purpose"] == "original_skill_quality" and summary["execution_mode"] == "live"
                    and type(summary["model_calls"]) is int and summary["model_calls"] == 1
                    and all(summary[key] is False for key in ("candidate_generated", "gepa_executed", "approved")),
                    "unsupported_quality_screen")
    results.require(summary["original_version_id"] == version_id
                    and summary["project_tree_sha256"] == tree_hash and summary["evaluator_sha256"] == evaluator_hash
                    and summary["rubric_sha256"] == sha256(results.encoded(rubric)).hexdigest(),
                    "quality_identity_mismatch")
    results.require(receipt["status"] == "completed" and type(receipt["returncode"]) is int
                    and receipt["returncode"] == 0 and receipt["role"] == "judge"
                    and receipt["requested_model"] == summary["model"]
                    and type(result["judge_calls"]) is int and result["judge_calls"] == 1,
                    "unverified_quality_receipt")
    judged = skill_guide.validate_judge(strict_json(receipt["content"]), rubric, result["static"]["applicability"])
    results.require(judged == result["judge"] == summary["judge"]
                    and result["static"]["findings"] == summary["static_findings"]
                    and receipt["usage"] == summary["usage"], "quality_evidence_mismatch")
    dimensions = judged["dimensions"]
    results.require(set(dimensions) == set(results.QUALITY_DIMENSIONS), "unsupported_quality_rubric")
    findings = result["static"]["findings"]
    status = "blocked" if any(row["severity"] == "error" for row in findings) else (
        "review" if findings or any(row["status"] == "review" or
                                   row["score"] is not None and row["score"] < 3
                                   for row in dimensions.values()) else "pass")
    results.require(result["status"] == summary["status"] == status, "quality_status_mismatch")
    elapsed, measured = summary["elapsed_seconds"], receipt["elapsed_seconds"]
    results.require(all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                        for value in (elapsed, measured)) and elapsed >= measured, "invalid_quality_time")
    stamp = datetime.fromisoformat(summary["created_at"])
    return results.validate({
        "schema_version": 1, "project_id": summary["project_id"],
        "run_id": "local-" + stamp.strftime("%Y%m%dT%H%M%SZ") + "-" + digest[:12],
        "created_at": summary["created_at"], "origin": "local", "purpose": "project_assessment",
        "source_commit": summary["source_commit"], "project_tree_sha256": tree_hash,
        "evaluator_sha256": evaluator_hash, "source_report_sha256": digest, "source_schema_version": 1,
        "guide": results.axis("completed", "quality_only", {
            "skills": 1, "cli_invocations": 1, "mean_judge_score": judged["score"],
            "judge_score_denominator": judged["score_denominator"],
            **{key: int(status == key) for key in ("pass", "review", "blocked")},
            "static_errors": sum(row["severity"] == "error" for row in findings),
            "static_warnings": sum(row["severity"] == "warning" for row in findings),
            **{"quality_" + key: dimensions[key]["score"] for key in results.QUALITY_DIMENSIONS},
            "base_cost_nano_aiu": _measurement(receipt, "nano_aiu", "nano_aiu"),
            "input_tokens": _measurement(receipt, "input_tokens", "tokens"),
            "output_tokens": _measurement(receipt, "output_tokens", "tokens"),
            "total_elapsed_seconds": elapsed,
        }),
        "execution": results.axis("not_assessed", "quality_only"),
    })


def export(root, summary_path, output):
    summary_path = results.safe_path(summary_path)
    summary = results.read_json(summary_path)
    result = results.read_json(summary_path.parent / "result.json")
    receipt = results.read_json(summary_path.parent / "judge-1.json")
    for name, key in (("result.json", "result_sha256"), ("judge-1.json", "receipt_sha256")):
        results.require(sha256(results.read_bytes(summary_path.parent / name)).hexdigest() == summary[key],
                        "quality_artifact_hash_mismatch")
    commit, project_id = summary["source_commit"], summary["project_id"]
    tree_hash, evaluator_hash = committed_identity(root, commit, project_id)
    rubric = strict_json(git(root, "cat-file", "blob", commit + ":eval/skill-guide-rubric.json").decode())
    evolution.relative_path(result["path"])
    prefix = f"projects/{project_id}/{result['path']}/"
    files = {}
    for entry in git(root, "ls-tree", "-rz", commit, "--", prefix).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        name = path.decode("utf-8")
        results.require(name.startswith(prefix) and mode in ("100644", "100755") and kind == "blob",
                        "unsafe_quality_source")
        files[name[len(prefix):]] = git(root, "cat-file", "blob", oid, limit=results.LIMIT)
        results.require(len(files) <= 256 and sum(map(len, files.values())) <= skill_guide.FILE_LIMIT,
                        "quality_source_limit")
    version, _ = evolution.capture_version(files, capture_scope="complete_bundle", complete_inventory=list(files))
    with tempfile.TemporaryDirectory(prefix="skillops-quality-source-") as folder:
        project = Path(folder)
        for name, raw in files.items():
            evolution.relative_path(name)
            path = project / result["path"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        bundles = skill_guide.discover(project)
        results.require(len(bundles) == 1 and bundles[0]["sha256"] == result["bundle_sha256"],
                        "quality_bundle_mismatch")
        static = skill_guide.static_assessment(bundles[0])
        work = skill_guide.judge_batches(rubric, static, bundles[0])
        results.require(static == result["static"] and len(work) == 1
                        and receipt["prompt"] == skill_guide.judge_prompt(rubric, *work[0], 1, 1),
                        "quality_prompt_mismatch")
    row = project_report(summary, result, receipt, rubric, digest=sha256(results.read_bytes(summary_path)).hexdigest(),
                         tree_hash=tree_hash, evaluator_hash=evaluator_hash, version_id=version["version_id"])
    results.store(output, row)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        row = export(args.root, args.root / args.summary, args.root / args.results)
        print(results.encoded({"status": "exported", "run_id": row["run_id"], "model_calls": 0,
                              "deployed": False}).decode(), end="")
        return 0
    except (RuntimeFailure, OSError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "invalid_quality_input"
        print(f"Quality export failed: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
