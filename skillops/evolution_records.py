"""Offline, explicitly registered Skill history. Records confer no deployment authority."""

from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from candidates import artifact_json
from copilot_runtime import RuntimeFailure
from repositories import _validate as validate_registry, read_file


SKILL_KEY = r"[a-z0-9][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,63}"
PROJECT_ID = r"[a-z0-9][a-z0-9_-]{0,63}"
RUN_ID = r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}"
DIGEST = r"[a-f0-9]{64}"
VERSION_ID = "sha256:" + DIGEST
FILE_LIMIT = 2 * 1024 * 1024
CAPTURE_LIMIT = 8 * 1024 * 1024
COLLECTIONS = ("identities", "sources", "versions", "skill_versions",
               "generations", "comparisons", "adoptions")


def require(condition, code="invalid_evolution_record"):
    if not condition:
        raise RuntimeFailure(code, "Skill evolution evidence failed contract validation.")


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def exact(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields.split()))


def choice(value, values):
    require(isinstance(value, str) and value in values)


def timestamp(value):
    if value is None:
        return
    require(isinstance(value, str) and len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.utcoffset() == timedelta(0))
    except ValueError as error:
        raise RuntimeFailure("invalid_evolution_timestamp", "Expected a UTC evidence timestamp.") from error


def relative_path(value):
    require(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 1024)
    require(not any(ord(char) < 32 or ord(char) == 127 for char in value))
    require("\\" not in value and ":" not in value and not value.startswith("~"))
    require(all(part not in ("", ".", "..") for part in value.split("/")))
    return value


def version_digest(scope, files):
    payload = {"domain": "skillops.captured-version", "schema_version": 1,
               "capture_scope": scope, "entrypoint": "SKILL.md", "files": files}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    return "sha256:" + sha256(raw).hexdigest()


def validate_version(version):
    exact(version, "version_id capture_scope entrypoint files")
    choice(version["capture_scope"], ("entrypoint_only", "complete_bundle"))
    require(version["entrypoint"] == "SKILL.md")
    files = version["files"]
    require(isinstance(files, list) and 1 <= len(files) <= 256)
    paths, total = [], 0
    for item in files:
        exact(item, "path sha256 bytes")
        paths.append(relative_path(item["path"]))
        require(matches(DIGEST, item["sha256"]))
        require(type(item["bytes"]) is int and 0 <= item["bytes"] <= FILE_LIMIT)
        total += item["bytes"]
    require(total <= CAPTURE_LIMIT and paths == sorted(set(paths)) and "SKILL.md" in paths)
    if version["capture_scope"] == "entrypoint_only":
        require(paths == ["SKILL.md"])
    require(version["version_id"] == version_digest(version["capture_scope"], files))
    return version


def capture_version(files, *, capture_scope, complete_inventory=None):
    """Capture supplied bytes; complete scope requires an explicit whole inventory."""
    require(isinstance(files, dict) and 1 <= len(files) <= 256)
    for path, raw in files.items():
        relative_path(path)
        require(isinstance(raw, bytes) and len(raw) <= FILE_LIMIT)
    if capture_scope == "complete_bundle":
        require(isinstance(complete_inventory, (list, tuple)) and len(complete_inventory) == len(files))
        for path in complete_inventory:
            relative_path(path)
        require(len(set(complete_inventory)) == len(complete_inventory) and set(complete_inventory) == set(files))
    manifest = [{"path": path, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
                for path, raw in sorted(files.items())]
    version = {"version_id": version_digest(capture_scope, manifest), "capture_scope": capture_scope,
               "entrypoint": "SKILL.md", "files": manifest}
    return validate_version(version), dict(files)


def empty_records():
    return {key: [] for key in COLLECTIONS}


def evidence(kind, run_id, digest, verified):
    value = {"kind": kind, "run_id": run_id, "artifact_sha256": digest,
             "availability": "verified" if verified else "referenced_only"}
    validate_ref(value, kind)
    return value


def validate_ref(value, kind=None):
    exact(value, "kind run_id artifact_sha256 availability")
    choice(value["kind"], ("baseline", "candidate", "comparison"))
    require(kind is None or value["kind"] == kind)
    require(matches(RUN_ID, value["run_id"]))
    choice(value["availability"], ("verified", "referenced_only"))
    require(value["artifact_sha256"] is None or matches(DIGEST, value["artifact_sha256"]))
    if value["availability"] == "verified":
        require(value["artifact_sha256"] is not None)


def entrypoint_hash(version):
    return next(item["sha256"] for item in version["files"] if item["path"] == "SKILL.md")


def validate_public_records(rows):
    exact(rows, " ".join(COLLECTIONS))
    require(all(isinstance(rows[key], list) and len(rows[key]) <= 4096 for key in COLLECTIONS))
    identities, versions, associations = {}, {}, set()
    for item in rows["identities"]:
        exact(item, "skill_key display_name")
        key, name = item["skill_key"], item["display_name"]
        require(matches(SKILL_KEY, key) and key not in identities)
        require(name is None or (isinstance(name, str) and 0 < len(name) <= 160
                                and not any(ord(char) < 32 for char in name)))
        identities[key] = item
    for item in rows["versions"]:
        validate_version(item)
        require(item["version_id"] not in versions)
        versions[item["version_id"]] = item
    for item in rows["skill_versions"]:
        exact(item, "skill_key version_id")
        require(matches(SKILL_KEY, item["skill_key"]) and matches(VERSION_ID, item["version_id"]))
        pair = (item["skill_key"], item["version_id"])
        require(pair[0] in identities and pair[1] in versions and pair not in associations)
        associations.add(pair)
    require(set(versions) == {version for _, version in associations})

    def captured(item, field, digest):
        value = item[field]
        if value is not None:
            require(matches(VERSION_ID, value) and (item["skill_key"], value) in associations)
            require(entrypoint_hash(versions[value]) == digest, "evolution_entrypoint_mismatch")

    for item in rows["sources"]:
        exact(item, "skill_key project_id kind scope path observed_at evidence_ref")
        require(matches(SKILL_KEY, item["skill_key"]) and item["skill_key"] in identities)
        require(matches(PROJECT_ID, item["project_id"]))
        choice(item["kind"], ("workspace", "run_archive"))
        choice(item["scope"], ("project", "shared", "personal", "plugin", "unknown"))
        if item["path"] is not None:
            relative_path(item["path"])
        timestamp(item["observed_at"])
        if item["evidence_ref"] is not None:
            validate_ref(item["evidence_ref"])
    generations, comparisons = {}, set()
    common = "skill_key candidate_ref base_entrypoint_sha256 candidate_entrypoint_sha256 base_version_id candidate_version_id"
    for collection, extra in (
        ("generations", "baseline_ref observed_failure_count hypothesis_kind hypothesis_basis"),
        ("comparisons", "comparison_ref decision status"),
    ):
        for item in rows[collection]:
            exact(item, common + " " + extra)
            require(matches(SKILL_KEY, item["skill_key"]) and item["skill_key"] in identities)
            validate_ref(item["candidate_ref"], "candidate")
            for arm in ("base", "candidate"):
                digest = item[f"{arm}_entrypoint_sha256"]
                require(matches(DIGEST, digest))
                captured(item, f"{arm}_version_id", digest)
            key = (item["skill_key"], item["candidate_ref"]["run_id"])
            if collection == "generations":
                require(key not in generations and item["candidate_ref"]["availability"] == "verified")
                generations[key] = item
                validate_ref(item["baseline_ref"], "baseline")
                count = item["observed_failure_count"]
                require(count is None or (type(count) is int and count >= 0))
                choice(item["hypothesis_kind"], ("unknown", "efficiency", "quality"))
                choice(item["hypothesis_basis"], ("unavailable", "operator_reviewed"))
                require((item["hypothesis_kind"] == "unknown") == (item["hypothesis_basis"] == "unavailable"))
            else:
                validate_ref(item["comparison_ref"], "comparison")
                require(item["comparison_ref"]["availability"] == "verified")
                comparison_key = (item["skill_key"], item["comparison_ref"]["run_id"])
                require(comparison_key not in comparisons)
                comparisons.add(comparison_key)
                choice(item["decision"], ("blocked", "rejected", "eligible_for_canary"))
                choice(item["status"], ("blocked", "completed"))
                require(item["status"] != "blocked" or item["decision"] == "blocked")
                if key in generations:
                    parent = generations[key]
                    for arm in ("base", "candidate"):
                        require(item[f"{arm}_entrypoint_sha256"] == parent[f"{arm}_entrypoint_sha256"])
                        if item[f"{arm}_version_id"] is not None and parent[f"{arm}_version_id"] is not None:
                            require(item[f"{arm}_version_id"] == parent[f"{arm}_version_id"])
                    if item["candidate_ref"]["artifact_sha256"] is not None:
                        require(item["candidate_ref"]["artifact_sha256"] == parent["candidate_ref"]["artifact_sha256"])
    for item in rows["adoptions"]:
        exact(item, "skill_key project_id state observed_at entrypoint_sha256 version_id evidence_kind registry_sha256")
        require(matches(SKILL_KEY, item["skill_key"]) and item["skill_key"] in identities)
        require(matches(PROJECT_ID, item["project_id"]))
        timestamp(item["observed_at"])
        choice(item["state"], ("unknown", "entrypoint_pin_observed"))
        if item["state"] == "unknown":
            require(item["evidence_kind"] == "none" and all(item[key] is None for key in
                    ("observed_at", "entrypoint_sha256", "version_id", "registry_sha256")))
        else:
            require(item["evidence_kind"] == "registry_snapshot" and item["observed_at"] is not None)
            require(matches(DIGEST, item["entrypoint_sha256"]) and matches(DIGEST, item["registry_sha256"]))
            captured(item, "version_id", item["entrypoint_sha256"])
            if item["version_id"] is not None:
                require(versions[item["version_id"]]["capture_scope"] == "entrypoint_only")
    return rows


def archived(root, run_id, name, *, optional=False):
    require(matches(RUN_ID, run_id))
    require(name in ("candidate.json", "comparison.json", "report.json", "SKILL.md", "base-SKILL.md"))
    path = (Path(root) / run_id / name).absolute()
    require(not any(part.is_symlink() for part in (path, *path.parents)), "unsafe_evolution_path")
    if optional and not path.exists():
        return None
    return read_file(path, limit=FILE_LIMIT)


def artifact(raw, run_id, purpose, statuses):
    data = artifact_json(raw)
    require(type(data.get("schema_version")) is int and data["schema_version"] in (1, 2))
    require(data.get("run_id") == run_id and data.get("purpose") == purpose)
    choice(data.get("status"), statuses)
    return data


def import_candidate(root, candidate_run, *, skill_key):
    """Read an explicit archive directory; never call generation/evaluation engines."""
    require(matches(SKILL_KEY, skill_key))
    raw = archived(root, candidate_run, "candidate.json")
    data = artifact(raw, candidate_run, "candidate", ("completed",))
    baseline_id, baseline_hash = data.get("baseline_run"), data.get("baseline_sha256")
    require(matches(RUN_ID, baseline_id) and matches(DIGEST, baseline_hash))
    captures, retained, version_ids = {}, {}, {}
    for arm, name, key in (("base", "base-SKILL.md", "base_skill_sha256"),
                           ("candidate", "SKILL.md", "skill_sha256")):
        content = archived(root, candidate_run, name)
        require(matches(DIGEST, data.get(key)) and sha256(content).hexdigest() == data[key],
                "evolution_entrypoint_mismatch")
        version, files = capture_version({"SKILL.md": content}, capture_scope="entrypoint_only")
        version_ids[arm] = version["version_id"]
        captures[version["version_id"]] = version
        retained[version["version_id"]] = files
    baseline_raw = archived(root, baseline_id, "report.json", optional=True)
    if baseline_raw is not None:
        require(sha256(baseline_raw).hexdigest() == baseline_hash, "evolution_baseline_mismatch")
        baseline = artifact(baseline_raw, baseline_id, "baseline",
                            ("completed", "completed_with_errors", "blocked", "failed"))
        require(baseline.get("skill_sha256") == data["base_skill_sha256"], "evolution_baseline_mismatch")
    failures = data.get("observed_failures")
    require(failures is None or isinstance(failures, list))
    generation = {
        "skill_key": skill_key, "candidate_ref": evidence("candidate", candidate_run, sha256(raw).hexdigest(), True),
        "baseline_ref": evidence("baseline", baseline_id, baseline_hash, baseline_raw is not None),
        "base_entrypoint_sha256": data["base_skill_sha256"], "candidate_entrypoint_sha256": data["skill_sha256"],
        "base_version_id": version_ids["base"], "candidate_version_id": version_ids["candidate"],
        "observed_failure_count": len(failures) if failures is not None else None,
        "hypothesis_kind": "unknown", "hypothesis_basis": "unavailable",
    }
    return generation, list(captures.values()), retained


def import_comparison(root, comparison_run, *, skill_key, candidate_record):
    require(matches(SKILL_KEY, skill_key) and candidate_record.get("skill_key") == skill_key)
    raw = archived(root, comparison_run, "comparison.json")
    data = artifact(raw, comparison_run, "comparison", ("completed", "blocked"))
    candidate_id = candidate_record["candidate_ref"]["run_id"]
    require(data.get("candidate_run") == candidate_id, "evolution_candidate_mismatch")
    candidate_raw = archived(root, candidate_id, "candidate.json")
    require(sha256(candidate_raw).hexdigest() == candidate_record["candidate_ref"]["artifact_sha256"],
            "evolution_candidate_mismatch")
    # Re-read source commitments rather than accepting a caller-fabricated generation link.
    saved, _, _ = import_candidate(root, candidate_id, skill_key=skill_key)
    for key in ("skill_key", "candidate_ref", "base_entrypoint_sha256", "candidate_entrypoint_sha256",
                "base_version_id", "candidate_version_id"):
        require(saved[key] == candidate_record[key], "evolution_candidate_mismatch")
    for key in ("kind", "run_id", "artifact_sha256"):
        require(saved["baseline_ref"][key] == candidate_record["baseline_ref"][key],
                "evolution_baseline_mismatch")
    commitment = data.get("candidate_sha256")
    if "candidate_sha256" in data:
        require(matches(DIGEST, commitment) and commitment == sha256(candidate_raw).hexdigest(),
                "evolution_candidate_mismatch")
    require(data.get("skill_sha256") == {
        arm: candidate_record[f"{arm}_entrypoint_sha256"] for arm in ("base", "candidate")},
        "evolution_comparison_mismatch")
    decision = data.get("decision")
    require(isinstance(decision, dict))
    choice(decision.get("decision"), ("blocked", "rejected", "eligible_for_canary"))
    require(data["status"] != "blocked" or decision["decision"] == "blocked")
    result = {key: candidate_record[key] for key in (
        "skill_key", "base_entrypoint_sha256", "candidate_entrypoint_sha256",
        "base_version_id", "candidate_version_id")}
    result.update(comparison_ref=evidence("comparison", comparison_run, sha256(raw).hexdigest(), True),
                  candidate_ref=evidence("candidate", candidate_id, commitment, commitment is not None),
                  decision=decision["decision"], status=data["status"])
    return result


def observe_registry(raw, *, project_id, repository_id, expected_engine_skill_id, skill_key, observed_at):
    require(matches(PROJECT_ID, project_id) and matches(SKILL_KEY, skill_key))
    require(matches(r"[a-z0-9][a-z0-9-]{0,63}", repository_id))
    require(expected_engine_skill_id == "develop")
    timestamp(observed_at)
    result = {"skill_key": skill_key, "project_id": project_id, "state": "unknown",
              "observed_at": None, "entrypoint_sha256": None, "version_id": None,
              "evidence_kind": "none", "registry_sha256": None}
    if raw is None:
        return result
    require(isinstance(raw, bytes) and len(raw) <= CAPTURE_LIMIT and observed_at is not None)
    data = validate_registry(artifact_json(raw))
    row = data["repositories"].get(repository_id)
    require(row is not None and row["skill_id"] == expected_engine_skill_id, "evolution_registry_mismatch")
    result.update(state="entrypoint_pin_observed", observed_at=observed_at,
                  entrypoint_sha256=row["skill_sha256"], evidence_kind="registry_snapshot",
                  registry_sha256=sha256(raw).hexdigest())
    return result
