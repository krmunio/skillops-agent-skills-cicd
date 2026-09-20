"""Public, version-bound Skill assessments; qualification never changes adoption."""

from decimal import Decimal
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path

from candidates import POLICY
import evolution_records as evolution
from evolution_records import exact, matches, require, DIGEST, VERSION_ID
import project_checks


TEXT_BYTES = 4096
FINDING_BYTES = 160
MAX_FINDINGS = 128


def text(value, limit=TEXT_BYTES, *, candidate_field=None, canonical_body=False):
    require(candidate_field in (None, "instructions", "hypothesis", "addressed_finding"),
            "invalid_candidate_field")
    reason = None
    if not isinstance(value, str):
        reason = "type"
    elif not value:
        reason = "empty"
    elif canonical_body and any(ord(char) < 32 and char not in "\n\t" for char in value):
        reason = "control_character"
    elif canonical_body and not value.strip():
        reason = "empty"
    elif len((value.strip() if canonical_body else value).encode("utf-8")) > limit:
        reason = "byte_limit"
    elif any(ord(char) < 32 and char not in "\n\t" for char in value):
        reason = "control_character"
    require(reason is None, f"candidate_{candidate_field}_{reason}" if candidate_field else "invalid_skill_assessment")


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


def _decide(row, policy, *, work_checks=None):
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
    if work_checks is None:
        # Legacy generated work must repair a pre-existing failure.
        if (check_id is None or populations["original"].get(check_id) not in ("failed", "error")
                or populations["candidate"].get(check_id) != "passed"):
            reasons.add("task_unverified")
    else:
        for collection, required in (("cases", "required_case_ids"), ("gates", "required_gate_ids")):
            observed = {item["id"]: item["status"] for item in
                        (row["checks"]["candidate"] or {}).get(collection, [])}
            if any(observed.get(key) != "passed" for key in work_checks[required]):
                reasons.add("task_unverified")
            if any(observed.get(key) in ("failed", "error") for key in work_checks[required]):
                rejected = True
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


def _hash(value):
    from project_results import encoded
    return sha256(encoded(value)).hexdigest()


def _ids(values):
    require(isinstance(values, list) and len(values) <= 10000, "invalid_work_item")
    for value in values:
        text(value, 1024)
    require(values == sorted(set(values)), "invalid_work_item")


def _work_checks(checks):
    exact(checks, "plan_sha256 protected_sha256 required_case_ids required_gate_ids")
    for key in ("plan_sha256", "protected_sha256"):
        require(matches(DIGEST, checks[key]), "invalid_work_item")
    for key in ("required_case_ids", "required_gate_ids"):
        _ids(checks[key])
    require(bool(checks["required_case_ids"]), "invalid_work_item")


def validate_work_item(data, *, project, source_commit):
    from project_results import CORE, safe_path, tree_hash
    from repositories import read_file
    exact(data, "schema_version task_id project_id source_commit project_tree_sha256 request split sources checks input_sha256")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_work_item")
    for key in ("task_id", "project_id"):
        require(matches(evolution.PROJECT_ID, data[key]), "invalid_work_item")
    require(matches(r"[a-f0-9]{40}", source_commit) and data["source_commit"] == source_commit, "work_source_mismatch")
    for key in ("project_tree_sha256", "input_sha256"):
        require(matches(DIGEST, data[key]), "invalid_work_item")
    require(data["split"] in ("development", "confirmation"), "invalid_work_item")
    text(data["request"], 16384)
    require(bool(data["request"].strip()), "invalid_work_item")
    _work_checks(data["checks"])
    project = safe_path(project)
    require(project.is_dir() and project.name == data["project_id"], "work_project_mismatch")
    require(tree_hash(project) == data["project_tree_sha256"], "work_source_mismatch")
    plan = project_checks.discover(project)
    protected = project_checks.protected_files(project)
    checks = data["checks"]
    require(plan["status"] in ("supported", "partial") and checks["plan_sha256"] == plan["sha256"]
            and checks["protected_sha256"] == project_checks.protected_digest(project, protected)
            and checks["required_gate_ids"] == sorted(item["id"] for item in plan["gates"]),
            "work_checks_mismatch")
    sources = data["sources"]
    require(isinstance(sources, dict) and 1 <= len(sources) <= 16, "invalid_work_item")
    bundles = [path.parent for path in project.rglob("SKILL.md")]
    total = 0
    for name, digest in sources.items():
        evolution.relative_path(name)
        path = Path(name)
        require(name not in protected and name not in CORE
                and not any((project / name).is_relative_to(bundle) for bundle in bundles)
                and not any(part.startswith(".") or part in ("skills", "eval", "fixtures", "tests", "test", "__tests__")
                                                 for part in path.parts)
                and not path.name.startswith(("test", "conftest", "setup."))
                and not path.stem.endswith("_test")
                and path.suffix in (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
                and matches(DIGEST, digest), "unsafe_work_source")
        raw = read_file(project / name, 65536)
        require(sha256(raw).hexdigest() == digest, "work_source_mismatch")
        try:
            raw.decode("utf-8")
        except UnicodeError as error:
            from copilot_runtime import RuntimeFailure
            raise RuntimeFailure("invalid_encoding", "Work sources must be UTF-8.") from error
        total += len(raw)
    require(total <= 65536, "work_input_limit")
    require(data["input_sha256"] == _hash({key: value for key, value in data.items() if key != "input_sha256"}),
            "work_hash_mismatch")
    return data


def validate_confirmation_disclosure(data, *, project, development_work_item,
                                     confirmation_work_item, original, source_path):
    """Validate reviewed disclosure bytes; this neither issues nor proves runtime isolation."""
    import project_results as results
    exact(data, "schema_version development_input_sha256 confirmation_input_sha256 "
                "model_visible_files checker_only_files disclosure_sha256")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1,
            "confirmation_isolation_unverified")
    project = results.safe_path(project)
    development, confirmation = development_work_item, confirmation_work_item
    require(isinstance(development, dict) and isinstance(confirmation, dict), "invalid_work_item")
    for work in (development, confirmation):
        validate_work_item(work, project=project, source_commit=development.get("source_commit"))
    require(development["split"] == "development" and confirmation["split"] == "confirmation"
            and development["task_id"] != confirmation["task_id"]
            and development["input_sha256"] != confirmation["input_sha256"]
            and development["request"].strip() != confirmation["request"].strip()
            and not set(development["checks"]["required_case_ids"]) & set(confirmation["checks"]["required_case_ids"]),
            "confirmation_isolation_unverified")
    require(data["development_input_sha256"] == development["input_sha256"]
            and data["confirmation_input_sha256"] == confirmation["input_sha256"]
            and data["disclosure_sha256"] == _hash({k: v for k, v in data.items() if k != "disclosure_sha256"}),
            "confirmation_inputs_changed")
    require(len(results.encoded(data)) <= results.LIMIT, "output_limit")
    _capture(original)
    evolution.relative_path(source_path)
    bundle = project / source_path
    require(bundle.is_dir(), "replay_capture_mismatch")
    actual_bundle = {p.relative_to(bundle).as_posix(): results.read_bytes(p)
                     for p in sorted(bundle.rglob("*")) if p.is_file()}
    require(actual_bundle == original[1], "replay_capture_mismatch")
    visible = set(development["sources"]) | set(confirmation["sources"]) | {
        (Path(source_path) / name).as_posix() for name in original[1]}
    maps = (data["model_visible_files"], data["checker_only_files"])
    for mapping in maps:
        require(isinstance(mapping, dict), "confirmation_isolation_unverified")
        for name, digest in mapping.items():
            evolution.relative_path(name)
            require(matches(DIGEST, digest), "confirmation_isolation_unverified")
    require(set(maps[0]) == visible and not set(maps[0]) & set(maps[1]),
            "confirmation_isolation_unverified")
    inventory = {p.relative_to(project).as_posix(): sha256(results.read_bytes(p)).hexdigest()
                 for p in sorted(project.rglob("*")) if p.is_file()}
    require({**maps[0], **maps[1]} == inventory, "confirmation_inputs_changed")
    request = confirmation["request"].strip().encode("utf-8")
    require(request not in development["request"].encode("utf-8")
            and all(request not in results.read_bytes(project / name) for name in visible),
            "confirmation_isolation_unverified")
    return data


def _replay_row(row):
    exact(row, "skill_key source_path base_version_id candidate_version_id work reference_sha256 "
               "quality applications checks errors" + (" decision" if "decision" in row else ""))
    work = row["work"]
    exact(work, "task_id input_sha256 split provenance checks")
    require(matches(evolution.PROJECT_ID, work["task_id"]) and matches(DIGEST, work["input_sha256"])
            and work["split"] in ("development", "confirmation") and work["provenance"] == "recorded",
            "invalid_replay_work")
    _work_checks(work["checks"])
    require(matches(DIGEST, row["reference_sha256"]) and matches(VERSION_ID, row["candidate_version_id"]),
            "invalid_replay")
    # Adapt structure only; no observation or historical decision is rewritten.
    legacy = {key: value for key, value in row.items() if key not in ("decision", "reference_sha256", "work")}
    legacy["work"] = {"sha256": work["input_sha256"], "provenance": "generated", "check_id": None}
    legacy["generation"] = {"status": "generated", "addressed_findings": [], "hypothesis": None}
    validate_skill(legacy)
    return legacy


def _replay_decision(evaluation):
    decision = _decide(_replay_row(evaluation), POLICY, work_checks=evaluation["work"]["checks"])
    return {"policy_id": "replay-v1", **{key: value for key, value in decision.items() if key != "policy_version"}}


def decide_replay(evaluation, *, work_item):
    require(isinstance(work_item, dict), "invalid_work_item")
    work = evaluation.get("work") if isinstance(evaluation, dict) else None
    require(isinstance(work, dict), "invalid_replay_work")
    require(all(work.get(key) == work_item.get(key) for key in ("task_id", "input_sha256", "split", "checks")),
            "replay_work_mismatch")
    return _replay_decision(evaluation)


def validate_replay(data, *, report, lifecycle):
    import project_results as results
    exact(data, "schema_version project_id run_id report_sha256 execution_mode reference generation evaluation")
    results.validate(report)
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_replay")
    require(data["project_id"] == report["project_id"] and data["run_id"] == report["run_id"]
            and data["report_sha256"] == _hash(report), "replay_report_mismatch")
    require(data["execution_mode"] in ("live", "offline_test", "sample"), "invalid_execution_mode")
    require((data["execution_mode"] == "sample") == (report["origin"] == "sample"), "replay_origin_mismatch")
    require(report["purpose"] == "project_assessment", "replay_report_mismatch")
    results.validate_evolution(lifecycle, report)
    row, ref = data["evaluation"], data["reference"]
    _replay_row(row)
    exact(ref, "schema_version project_id skill_key source_path source_commit project_tree_sha256 "
               "input_sha256 original_version_id rubric_sha256 quality_context_sha256 evaluator_sha256 "
               "policy_sha256 plan_sha256 environment_sha256 protected_sha256 original_checks base_quality reference_sha256")
    require(type(ref["schema_version"]) is int and ref["schema_version"] == 1, "invalid_replay_reference")
    for key in ("project_id", "source_commit", "project_tree_sha256", "evaluator_sha256"):
        require(ref[key] == report[key], "replay_reference_mismatch")
    sample = data["execution_mode"] == "sample"
    require(ref["source_commit"] is None if sample else matches(r"[a-f0-9]{40}", ref["source_commit"]),
            "invalid_replay_reference")
    for key in ("skill_key", "source_path"):
        require(ref[key] == row[key], "replay_reference_mismatch")
    for key in ("project_tree_sha256", "input_sha256", "rubric_sha256", "quality_context_sha256",
                "evaluator_sha256", "policy_sha256", "plan_sha256", "environment_sha256",
                "protected_sha256", "reference_sha256"):
        require(ref[key] is None if sample and key in ("project_tree_sha256", "evaluator_sha256")
                else matches(DIGEST, ref[key]), "invalid_replay_reference")
    require(ref["reference_sha256"] == _hash({k: v for k, v in ref.items() if k != "reference_sha256"})
            and row["reference_sha256"] == ref["reference_sha256"], "replay_reference_mismatch")
    require(ref["policy_sha256"] == _hash({"policy": POLICY, "rule": "replay-v1"}), "unsupported_replay_policy")
    require(ref["original_version_id"] == row["base_version_id"]
            and row["work"]["input_sha256"] == ref["input_sha256"], "replay_reference_mismatch")
    require(ref["base_quality"] == row["quality"]["base"] and ref["original_checks"] == row["checks"]["original"],
            "replay_reference_mismatch")
    for quality_row in row["quality"].values():
        if quality_row is not None:
            require(quality_row["rubric_sha256"] == ref["rubric_sha256"]
                    and quality_row["context_sha256"] == ref["quality_context_sha256"], "replay_quality_mismatch")
    for check in row["checks"].values():
        if check is not None:
            require(all(check[key] == ref[key] for key in ("plan_sha256", "environment_sha256", "protected_sha256")),
                    "replay_checks_mismatch")
    work = row["work"]
    require(all(work["checks"][key] == ref[key] for key in ("plan_sha256", "protected_sha256")), "replay_checks_mismatch")
    if ref["original_checks"] is not None:
        require(set(work["checks"]["required_case_ids"]) <= {case["id"] for case in ref["original_checks"]["cases"]}
                and set(work["checks"]["required_gate_ids"]) == {gate["id"] for gate in ref["original_checks"]["gates"]},
                "replay_checks_mismatch")
    for arm, application in row["applications"].items():
        if application is not None:
            require(application["version_id"] == application["staged_version_id"] == row[f"{arm}_version_id"]
                    and application["work_sha256"] == ref["input_sha256"], "replay_application_mismatch")
    bindings = [binding for binding in lifecycle["bindings"] if binding["skill_key"] == row["skill_key"]]
    require(len(bindings) == 1 and all(bindings[0][key] == row[key] for key in ("base_version_id", "candidate_version_id")),
            "replay_version_mismatch")
    versions = {version["version_id"]: version for version in lifecycle["records"]["versions"]}
    for arm in ("base", "candidate"):
        require(versions[row[f"{arm}_version_id"]]["capture_scope"] == "complete_bundle", "replay_incomplete_capture")
    inventories = [{item["path"]: item for item in versions[row[f"{arm}_version_id"]]["files"]}
                   for arm in ("base", "candidate")]
    require(inventories[0].keys() == inventories[1].keys()
            and all(inventories[0][name] == inventories[1][name] for name in inventories[0] if name != "SKILL.md"),
            "replay_companion_changed")
    sources = [source for source in lifecycle["records"]["sources"] if source["skill_key"] == row["skill_key"]]
    require(bool(sources) and all(source["path"] == row["source_path"] for source in sources), "replay_source_mismatch")
    generation = data["generation"]
    if generation is not None:
        exact(generation, "parent_version_id feedback_sha256 addressed_findings hypothesis")
        require(matches(VERSION_ID, generation["parent_version_id"]) and matches(DIGEST, generation["feedback_sha256"])
                and work["split"] == "development", "invalid_replay_generation")
        require(isinstance(generation["addressed_findings"], list) and len(generation["addressed_findings"]) <= 128,
                "invalid_replay_generation")
        for identifier in generation["addressed_findings"]:
            text(identifier, 160)
        require(len(set(generation["addressed_findings"])) == len(generation["addressed_findings"]), "invalid_replay_generation")
        text(generation["hypothesis"])
    else:
        require(work["split"] == "confirmation", "invalid_replay_generation")
    require(isinstance(row.get("decision"), dict) and row["decision"] == _replay_decision(row), "replay_decision_mismatch")
    require(len(results.encoded(data)) <= results.LIMIT, "output_limit")
    return data


def _capture(value):
    require(isinstance(value, tuple) and len(value) == 2, "invalid_replay_capture")
    manifest, files = value
    evolution.validate_version(manifest)
    require(manifest["capture_scope"] == "complete_bundle" and isinstance(files, dict), "invalid_replay_capture")
    observed, _ = evolution.capture_version(files, capture_scope="complete_bundle", complete_inventory=list(files))
    require(observed == manifest, "replay_capture_mismatch")
    return manifest["version_id"]


def validate_development_feedback(packet, *, context, evaluation, parent, source_round_id):
    """Validate semantics only; the provider must separately verify private issuance."""
    import project_results as results
    exact(context, "reference work_item original source_project project plan images rubric sources execution_mode feedback_scope feedback")
    require(context["execution_mode"] in ("live", "offline_test", "sample"), "invalid_execution_mode")
    work, ref, scope = context["work_item"], context["reference"], context["feedback_scope"]
    require(isinstance(work, dict) and work.get("split") == "development", "confirmation_isolation_unverified")
    require(isinstance(ref, dict), "invalid_replay_reference")
    require(isinstance(context["source_project"], Path) and isinstance(context["project"], Path)
            and context["source_project"].is_absolute() and context["project"].is_absolute()
            and context["source_project"] != context["project"], "invalid_replay_context")
    validate_work_item(work, project=context["source_project"], source_commit=work.get("source_commit"))
    require(results.tree_hash(results.safe_path(context["project"])) == work["project_tree_sha256"], "work_source_mismatch")
    require(all(ref.get(key) == work[key] for key in ("project_id", "source_commit", "project_tree_sha256", "input_sha256")),
            "replay_reference_mismatch")
    require(ref.get("reference_sha256") == _hash({k: v for k, v in ref.items() if k != "reference_sha256"}),
            "replay_reference_mismatch")
    require(ref.get("original_version_id") == _capture(context["original"]), "replay_capture_mismatch")
    evolution.relative_path(ref.get("source_path"))
    bundle = context["source_project"] / ref["source_path"]
    files = {path.relative_to(bundle).as_posix(): results.read_bytes(path)
             for path in sorted(bundle.rglob("*")) if path.is_file()}
    require(files == context["original"][1], "replay_capture_mismatch")
    require(context["plan"] == project_checks.discover(context["project"])
            and context["plan"]["sha256"] == ref.get("plan_sha256"), "replay_checks_mismatch")
    require(isinstance(context["images"], dict) and bool(context["images"])
            and all(language in ("python", "node") and matches(VERSION_ID, image)
                    for language, image in context["images"].items())
            and project_checks.digest(context["images"]) == ref.get("environment_sha256"), "replay_checks_mismatch")
    require(isinstance(context["rubric"], dict)
            and project_checks.digest(context["rubric"]) == ref.get("rubric_sha256"), "replay_quality_mismatch")
    require(isinstance(context["sources"], dict) and set(context["sources"]) == set(work["sources"]),
            "work_source_mismatch")
    for path, content in context["sources"].items():
        require(isinstance(content, str) and sha256(content.encode("utf-8")).hexdigest() == work["sources"][path],
                "work_source_mismatch")
    exact(scope, "input_sha256 case_ids gate_ids test_context_paths")
    require(scope == {"input_sha256": work["input_sha256"], "case_ids": work["checks"]["required_case_ids"],
                      "gate_ids": work["checks"]["required_gate_ids"], "test_context_paths": []},
            "confirmation_isolation_unverified")
    require(isinstance(ref.get("original_checks"), dict) and isinstance(ref.get("base_quality"), dict),
            "feedback_source_unverified")
    project_checks.validate_observation(ref["original_checks"])
    quality(ref["base_quality"])
    require(all(ref["original_checks"][key] == ref.get(key) for key in
                ("plan_sha256", "protected_sha256", "environment_sha256"))
            and ref["base_quality"]["rubric_sha256"] == ref.get("rubric_sha256")
            and ref["base_quality"]["context_sha256"] == ref.get("quality_context_sha256"),
            "replay_reference_mismatch")
    for collection, visible in (("cases", "case_ids"), ("gates", "gate_ids")):
        require(set(scope[visible]) <= {item["id"] for item in ref["original_checks"][collection]},
                "confirmation_isolation_unverified")

    def visible(check):
        return {collection: [deepcopy(item) for item in check[collection] if item["id"] in scope[allowed]]
                for collection, allowed in (("cases", "case_ids"), ("gates", "gate_ids"))}

    parent_id = _capture(parent)
    if evaluation is None:
        require(source_round_id is None and parent_id == ref["original_version_id"], "feedback_parent_mismatch")
        expected_quality, check = ref["base_quality"], ref["original_checks"]
        application, decision = None, None
    else:
        require(matches(r"(?:" + results.RUN + r")-r(?:[1-9]|10)", source_round_id), "invalid_feedback_round")
        _replay_row(evaluation)
        require(evaluation["work"] == {"task_id": work["task_id"], "input_sha256": work["input_sha256"],
                                     "split": "development", "provenance": "recorded", "checks": work["checks"]}
                and evaluation["reference_sha256"] == ref["reference_sha256"]
                and evaluation["skill_key"] == ref["skill_key"] and evaluation["source_path"] == ref["source_path"]
                and evaluation["base_version_id"] == ref["original_version_id"]
                and evaluation["candidate_version_id"] == parent_id, "feedback_parent_mismatch")
        require(evaluation["quality"]["base"] == ref["base_quality"]
                and evaluation["checks"]["original"] == ref["original_checks"]
                and evaluation.get("decision") == _replay_decision(evaluation), "feedback_source_mismatch")
        projected = deepcopy(evaluation)
        for arm, observed in evaluation["checks"].items():
            require(observed is not None and observed["status"] != "blocked", "feedback_source_unverified")
            require(all(observed[key] == ref[key] for key in ("plan_sha256", "protected_sha256", "environment_sha256")),
                    "replay_checks_mismatch")
            filtered = visible(observed)
            projected["checks"][arm].update(filtered)
            projected["checks"][arm]["status"] = (
                "failed" if any(item["status"] in ("failed", "error") for items in filtered.values() for item in items)
                else "completed")
        require(_replay_decision(projected) == evaluation["decision"], "confirmation_isolation_unverified")
        expected_quality = evaluation["quality"]["candidate"]
        check = evaluation["checks"]["candidate"]
        application, decision = evaluation["applications"]["candidate"], evaluation["decision"]
        require(expected_quality is not None and application is not None
                and application["version_id"] == application["staged_version_id"] == parent_id
                and application["work_sha256"] == work["input_sha256"], "feedback_source_unverified")
    exact(packet, "schema_version input_sha256 source_round_id quality checks application decision")
    require(type(packet["schema_version"]) is int and packet["schema_version"] == 1, "invalid_feedback")
    expected = {"schema_version": 1, "input_sha256": work["input_sha256"], "source_round_id": source_round_id,
                "quality": expected_quality, "checks": visible(check), "application": application, "decision": decision}
    require(packet == expected and results.encoded(packet) == results.encoded(expected), "feedback_source_mismatch")
    require(len(results.encoded(packet)) <= results.LIMIT, "output_limit")
    return packet
