"""Public, version-bound Skill assessments; qualification never changes adoption."""

from decimal import Decimal
from hashlib import sha256
import json
import math

from candidates import POLICY
import evolution_records as evolution
from evolution_records import exact, matches, require, DIGEST, VERSION_ID
import project_checks


def text(value, limit=4096):
    require(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= limit
            and not any(ord(char) < 32 and char not in "\n\t" for char in value),
            "invalid_skill_assessment")


def quality(value):
    if value is None:
        return
    exact(value, "status rubric_sha256 context_sha256 dimensions findings")
    require(value["status"] in ("completed", "blocked"), "invalid_skill_assessment")
    for key in ("rubric_sha256", "context_sha256"):
        require(matches(DIGEST, value[key]), "invalid_skill_assessment")
    for name in ("dimensions", "findings"):
        require(isinstance(value[name], list) and len(value[name]) <= 128, "invalid_skill_assessment")
        seen = set()
        for row in value[name]:
            exact(row, "id score" if name == "dimensions" else "id severity message")
            text(row["id"], 160)
            require(row["id"] not in seen, "invalid_skill_assessment")
            seen.add(row["id"])
            if name == "dimensions":
                require(row["score"] is None or (type(row["score"]) is int and 0 <= row["score"] <= 4),
                        "invalid_skill_assessment")
            else:
                require(row["severity"] in ("error", "warning"), "invalid_skill_assessment")
                text(row["message"])


def validate_skill(row):
    expected = ("skill_key source_path base_version_id candidate_version_id quality generation "
                "work applications checks errors")
    exact(row, expected + (" decision" if "decision" in row else ""))
    require(matches(evolution.SKILL_KEY, row["skill_key"]), "invalid_skill_assessment")
    evolution.relative_path(row["source_path"])
    require(matches(VERSION_ID, row["base_version_id"]), "invalid_skill_assessment")
    require(row["candidate_version_id"] is None or matches(VERSION_ID, row["candidate_version_id"]),
            "invalid_skill_assessment")
    exact(row["quality"], "base candidate")
    for value in row["quality"].values():
        quality(value)
    generation = row["generation"]
    exact(generation, "status addressed_findings hypothesis")
    require(generation["status"] in ("generated", "no_change", "failed"), "invalid_skill_assessment")
    findings = generation["addressed_findings"]
    require(isinstance(findings, list) and len(findings) <= 128, "invalid_skill_assessment")
    for identifier in findings:
        text(identifier, 160)
    require(len(findings) == len(set(findings)), "invalid_skill_assessment")
    if generation["hypothesis"] is not None:
        text(generation["hypothesis"])
    if generation["status"] == "generated":
        require(row["candidate_version_id"] is not None
                and row["candidate_version_id"] != row["base_version_id"], "invalid_skill_assessment")
    else:
        require(row["candidate_version_id"] is None, "invalid_skill_assessment")
    work = row["work"]
    if work is not None:
        exact(work, "sha256 provenance check_id")
        require(matches(DIGEST, work["sha256"]) and work["provenance"] == "generated",
                "invalid_skill_assessment")
        if work["check_id"] is not None:
            text(work["check_id"], 1024)
    exact(row["applications"], "base candidate")
    for arm, application in row["applications"].items():
        if application is None:
            continue
        exact(application, "version_id staged_version_id work_sha256 output_sha256 activated changed task_outcome"
              + (" measurement" if "measurement" in application else ""))
        if "measurement" in application:
            exact(application["measurement"], "cost_nano_aiu elapsed_seconds")
            for value in application["measurement"].values():
                require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0),
                        "invalid_skill_assessment")
        for key in ("version_id", "staged_version_id"):
            require(matches(VERSION_ID, application[key]), "invalid_skill_assessment")
        for key in ("work_sha256", "output_sha256"):
            require(matches(DIGEST, application[key]), "invalid_skill_assessment")
        require(type(application["activated"]) is bool and type(application["changed"]) is bool,
                "invalid_skill_assessment")
        require(application["task_outcome"] in ("satisfied", "not_satisfied", "unverified"),
                "invalid_skill_assessment")
        require(row[f"{arm}_version_id"] is not None, "invalid_skill_assessment")
    exact(row["checks"], "original base candidate")
    require(isinstance(row["errors"], list) and len(row["errors"]) <= 16, "invalid_skill_assessment")
    for error in row["errors"]:
        exact(error, "stage code")
        require(error["stage"] in ("preparation", "discovery", "original_checks", "base_quality", "generation", "candidate_quality",
                                  "base_application", "candidate_application", "work"),
                "invalid_skill_assessment")
        require(matches(r"[a-z0-9_]{1,128}", error["code"]), "invalid_skill_assessment")
    for value in row["checks"].values():
        if value is not None:
            project_checks.validate_observation(value)
    if row["candidate_version_id"] is None:
        require(row["quality"]["candidate"] is None and row["applications"]["candidate"] is None
                and row["checks"]["candidate"] is None, "invalid_skill_assessment")
    return row


def decide(row):
    return _decide(row, POLICY)


def _decide(row, policy):
    validate_skill(row)
    regression = project_checks.compare(*(row["checks"][arm] for arm in ("original", "base", "candidate")))
    reasons, rejected = set(), regression["status"] == "rejected"
    if row["errors"]:
        reasons.add("incomplete_stage")
    if regression["status"] != "passed":
        reasons.add("project_regression" if rejected else "regression_unverified")
    base, candidate = row["quality"]["base"], row["quality"]["candidate"]
    improved = False
    if base is None or candidate is None or any(item["status"] != "completed" for item in (base, candidate)):
        reasons.add("quality_unverified")
    elif any(base[key] != candidate[key] for key in ("rubric_sha256", "context_sha256")):
        reasons.add("quality_inputs_mismatch")
    else:
        before = {item["id"]: item["score"] for item in base["dimensions"]}
        after = {item["id"]: item["score"] for item in candidate["dimensions"]}
        if (not before or set(before) != set(after)
                or any((before[key] is None) != (after[key] is None) for key in before)
                or not any(score is not None for score in before.values())):
            reasons.add("quality_coverage_mismatch")
        else:
            rejected |= any(after[key] < score for key, score in before.items() if score is not None)
            improved |= any(after[key] > score for key, score in before.items() if score is not None)
        prior = {item["id"]: item["severity"] for item in base["findings"]}
        current = {item["id"]: item["severity"] for item in candidate["findings"]}
        if any(severity == "error" for severity in current.values()):
            rejected = True
            reasons.add("candidate_quality_error")
        if any(key not in prior or (severity == "error" and prior[key] != "error")
               for key, severity in current.items()):
            rejected = True
            reasons.add("quality_regression")
        improved |= bool(set(prior) - set(current))
        if any(after.get(key) is not None and after[key] < score
               for key, score in before.items() if score is not None and key in after):
            reasons.add("quality_regression")
    generation = row["generation"]
    known = {item["id"] for item in base["findings"]} if base else set()
    if base:
        known.update(item["id"] for item in base["dimensions"])
    if generation["status"] != "generated":
        reasons.add("candidate_unavailable")
    if not set(generation["addressed_findings"]) <= known:
        reasons.add("unsupported_generation_evidence")
    work = row["work"]
    for arm in ("base", "candidate"):
        application = row["applications"][arm]
        if (application is None or work is None or not application["activated"]
                or application["version_id"] != row[f"{arm}_version_id"]
                or application["staged_version_id"] != row[f"{arm}_version_id"]
                or application["work_sha256"] != work["sha256"]):
            reasons.add("activation_unverified")
        elif not application["changed"]:
            reasons.add("no_execution_effect")
        if arm == "candidate" and application is not None and application["task_outcome"] != "satisfied":
            reasons.add("task_unverified")
    check_id = work["check_id"] if work else None
    populations = {
        arm: {case["id"]: case["status"] for case in value["cases"]} if value else {}
        for arm, value in row["checks"].items()
    }
    # A model claim cannot replace an observed repair of a pre-existing failing project check.
    if (check_id is None or populations["original"].get(check_id) not in ("failed", "error")
            or populations["candidate"].get(check_id) != "passed"):
        reasons.add("task_unverified")
    if policy is not None:
        measurements = [(row["applications"][arm] or {}).get("measurement", {}) for arm in ("base", "candidate")]
        limit = Decimal(100) + Decimal(str(policy["maximum_efficiency_regression_percent"]))
        for metric in ("cost_nano_aiu", "elapsed_seconds"):
            before, after = (measurement.get(metric) for measurement in measurements)
            if before is None or after is None:
                reasons.add("efficiency_unverified")
            elif Decimal(str(after)) * 100 > Decimal(str(before)) * limit:
                reasons.add("efficiency_regression")
    status = "rejected" if rejected else "unverified" if reasons else "improved" if improved else "not_improved"
    decision = {"status": status, "reasons": sorted(reasons), "regression": regression}
    if policy is not None:
        decision["policy_version"] = policy["version"]
    return decision


def validate(data, report, lifecycle):
    exact(data, "schema_version project_id run_id report_sha256 skills")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_skill_assessment")
    require(data["project_id"] == report["project_id"] and data["run_id"] == report["run_id"],
            "assessment_report_mismatch")
    raw = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    require(data["report_sha256"] == sha256(raw).hexdigest(), "assessment_report_mismatch")
    require(lifecycle is not None, "missing_assessment_versions")
    require(lifecycle["project_id"] == data["project_id"] and lifecycle["run_id"] == data["run_id"]
            and lifecycle["report_sha256"] == data["report_sha256"], "assessment_version_mismatch")
    bindings = {item["skill_key"]: item for item in lifecycle["bindings"]}
    skills = data["skills"]
    require(isinstance(skills, list) and 1 <= len(skills) <= 128, "invalid_skill_assessment")
    seen = set()
    for row in skills:
        validate_skill(row)
        key = row["skill_key"]
        require(key in bindings and key not in seen, "assessment_version_mismatch")
        seen.add(key)
        require(all(row[name] == bindings[key][name] for name in ("base_version_id", "candidate_version_id")),
                "assessment_version_mismatch")
        decision = row.get("decision")
        require(isinstance(decision, dict), "assessment_decision_mismatch")
        versioned = "policy_version" in decision
        exact(decision, "status reasons regression" + (" policy_version" if versioned else ""))
        if versioned:
            require(type(decision["policy_version"]) is int and decision["policy_version"] == POLICY["version"],
                    "unsupported_assessment_policy")
        require(decision == _decide(row, POLICY if versioned else None), "assessment_decision_mismatch")
    require(len(json.dumps(data, ensure_ascii=True, allow_nan=False).encode()) <= 1024 * 1024,
            "output_limit")
    return data
