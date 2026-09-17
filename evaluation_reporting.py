"""Actions presentation of existing evaluation stages and validated public evidence."""

import argparse
from html import escape
import json
from pathlib import Path
import re
import sys

from copilot_runtime import RuntimeFailure
import project_results as results


STAGES = {
    "discovery": "Project / check discovery",
    "original_checks": "Project / untouched source checks",
    "base_quality": "Baseline / original / Anthropic",
    "work": "Improvement / existing failure selection",
    "generation": "Improvement / APO-inspired candidate generation",
    "candidate_quality": "Baseline / candidate / Anthropic",
    "base_application": "Project / original Skill application and checks",
    "candidate_application": "Project / candidate Skill application and checks",
    "qualification": "Baseline / APO-inspired evidence and final qualification",
}


def label(value):
    text = escape("".join(char if char.isprintable() else " " for char in str(value)))
    for char in ("|", "`", "[", "]"):
        text = text.replace(char, f"&#{ord(char)};")
    return text


def progress(project, skill, stage, status, code=None):
    title = STAGES[stage]
    if status == "started":
        title = f"{label(project)} / {label(skill)} / {title}".replace("%", "%25")
        print(f"::group::{title}", flush=True)
        return
    event = {"stage": stage, "execution": status}
    if code is not None:
        event["reason"] = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,128}", code) else "invalid_error_code"
    print(json.dumps(event), flush=True)
    print("::endgroup::", flush=True)


def run_stage(observer, stage, function):
    if observer is None:
        return function()
    observer(stage, "started")
    status, code = "failed", None
    try:
        value = function()
        status = "completed"
        return value
    except (RuntimeFailure, OSError) as error:
        status = "blocked"
        code = error.code if isinstance(error, RuntimeFailure) else "io_error"
        raise
    finally:
        observer(stage, status, code)


def check_status(value):
    if value is None:
        return "not evaluated"
    passed = sum(case["status"] == "passed" for case in value["cases"])
    failed = sum(case["status"] in ("failed", "error") for case in value["cases"])
    other = len(value["cases"]) - passed - failed
    gates = ", ".join(f'{gate["id"]}: {gate["status"]}' for gate in value["gates"])
    return f'{value["status"]}: {passed} passed, {failed} failed, {other} other' + (f"; gates: {gates}" if gates else "")


def report_lines(report, assessment):
    lines = [
        f'### {label(report["project_id"])}',
        f'Run: {label(report["run_id"])}',
        f'Guide: {label(report["guide"]["status"])} / {label(report["guide"]["reason_code"])}. '
        f'Project: {label(report["execution"]["status"])} / {label(report["execution"]["reason_code"])}.',
    ]
    if assessment is None:
        return lines + ["No per-Skill assessment recorded; baseline results are not inferred.", ""]
    for row in assessment["skills"]:
        lines += [
            f'#### {label(row["source_path"])}',
            "| Evaluation | Original | Candidate |",
            "| --- | --- | --- |",
        ]
        original, candidate = row["quality"]["base"], row["quality"]["candidate"]
        before_status = original["status"] if original else "not evaluated"
        after_status = candidate["status"] if candidate else "not evaluated"
        lines.append(f"| Baseline / Anthropic | {before_status} | {after_status} |")
        after = {item["id"]: item["score"] for item in candidate["dimensions"]} if candidate else {}
        for dimension in original["dimensions"] if original else []:
            before_score = dimension["score"] if dimension["score"] is not None else "not applicable"
            after_score = after.get(dimension["id"], "not evaluated")
            after_score = "not applicable" if after_score is None else after_score
            lines.append(f'| {label(dimension["id"])} | {before_score} | {after_score} |')
        generation = row["generation"]
        known = {item["id"] for item in original["findings"] + original["dimensions"]} if original else set()
        addressed = set(generation["addressed_findings"])
        evidence = "not evaluated"
        if generation["status"] == "generated":
            evidence = "evidence linked; hypothesis unverified" if addressed and addressed <= known else "no supported evidence linkage"
        lines += [
            f'| Baseline / APO-inspired evidence | {"findings recorded" if original else "not evaluated"} | {evidence} |',
            f'| Candidate generation | not applicable | {label(generation["status"])} |',
            f'| Project / untouched source | {label(check_status(row["checks"]["original"]))} | not applicable |',
            f'| Project / Skill application | {label(check_status(row["checks"]["base"]))} | '
            f'{label(check_status(row["checks"]["candidate"]))} |',
            f'| Project / regression comparison | reference | {label(row["decision"]["regression"]["status"])} |',
            f'| Final qualification | reference | {label(row["decision"]["status"])} |',
            "",
            f'Original version: {label(row["base_version_id"])}',
            f'Candidate version: {label(row["candidate_version_id"] or "not generated")}',
        ]
        reasons = [f'{error["stage"]}: {error["code"]}' for error in row["errors"]]
        reasons += row["decision"]["reasons"]
        if reasons:
            lines.append("Reasons: " + "; ".join(label(reason) for reason in reasons))
        lines.append("")
    return lines


def summary(root, run_id):
    results.require(results.matches(results.RUN, run_id), "invalid_run")
    reports = results.load_reports(root)
    assessments = results.load_assessments(root, reports)
    selected = [report for report in reports if report["run_id"] == run_id]
    results.require(bool(selected), "unknown_run")
    lines = [
        "## SkillOps evaluation stages",
        "Baseline assessment and project execution are separate evidence categories.",
        "No independent APO score is produced. Recorded hypotheses are not proof of improvement.",
        "Completed log groups mean the stage executed, not that its assessment passed.",
        "",
    ]
    size = sum(len(line.encode("utf-8")) + 1 for line in lines)
    for report in selected:
        for line in report_lines(report, assessments.get((report["project_id"], report["run_id"]))):
            size += len(line.encode("utf-8")) + 1
            if size > 900000:
                return "\n".join(lines + ["", "Summary truncated; full results remain in the public results artifact."]) + "\n"
            lines.append(line)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        print(summary(args.results, args.run_id), end="")
        return 0
    except (RuntimeFailure, OSError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "io_error"
        print(f"## SkillOps evaluation summary unavailable\n\nReason: {label(code)}")
        print(json.dumps({"status": "blocked", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
