"""Owner-local approval bindings, observed use and a separate Active pointer.

The CLI owns human confirmation/noninteractive rejection. The execution adapter
owns fresh run IDs, budget authorization, runtime activation and before/after
inventory/evidence checks. A receipt asserts observed loading, not task success.
These functions never invoke a model, rewrite a Skill, or import public authority.
"""

import base64
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import pwd
import socket
import stat
from uuid import uuid4

from copilot_runtime import RuntimeFailure, capture
import evolution_records as evolution
import project_checks
import project_results
import repositories
import skill_assessments
import skill_guide
import skill_pipeline


APPROVAL_FIELDS = (
    "schema_version approval_id project_id skill_key source_path source_commit "
    "project_tree_sha256 candidate_version_id cycle_id evidence_sha256 approved_by "
    "approved_at environment_id previous_active_version_id previous_active_execution_sha256"
)
EXECUTION_FIELDS = (
    "schema_version execution_id run_id project_id skill_key approval_id approval_sha256 "
    "evidence_sha256 environment_id work_input_sha256 approved_version_id loaded_version_id "
    "skill_version_verified observed_at status reason_code previous_active_version_id previous_active_execution_sha256"
)


def _require(condition, code="invalid_approval"):
    repositories.require(condition, code, "Local approval/use validation failed; no successful selection is reported.")


def _digest(data):
    return sha256(project_results.encoded(data)).hexdigest()


def _version(value, *, nullable=False):
    _require((nullable and value is None) or evolution.matches(evolution.VERSION_ID, value))


def _pair(version_id, execution_sha256):
    _version(version_id, nullable=True)
    _require((version_id is None and execution_sha256 is None)
             or (version_id is not None and evolution.matches(evolution.DIGEST, execution_sha256)),
             "invalid_active_pair")
    return {"version_id": version_id, "execution_sha256": execution_sha256}


def _previous(record):
    return _pair(record["previous_active_version_id"], record["previous_active_execution_sha256"])


def _identity(project_id, skill_key):
    _require(project_results.matches(project_results.ID, project_id)
             and evolution.matches(evolution.SKILL_KEY, skill_key), "invalid_approval_target")


def _local_only():
    _require(not any(os.environ.get(name) for name in ("CI", "GITHUB_ACTIONS")), "local_approval_only")


def _operator():
    return f"uid:{os.getuid()}:{pwd.getpwuid(os.getuid()).pw_name}"


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory(path, *, create=False):
    path = project_results.safe_path(path)
    if create and not path.exists():
        path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
    _require(path.is_dir(), "unsafe_approval_store")
    info = path.stat()
    _require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, "unsafe_approval_store")
    return path


def _read(path):
    _directory(path.parent)
    project_results.safe_path(path)
    _require(path.is_file(), "missing_local_record")
    info = path.stat()
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and info.st_mode & 0o077 == 0 and info.st_nlink == 1, "unsafe_approval_store")
    value = project_results.read_json(path)
    _require(project_results.read_bytes(path) == project_results.encoded(value), "changed_local_record")
    return value


def _write(path, value, *, immutable=True):
    _directory(path.parent, create=True)
    if path.exists() or path.is_symlink():
        existing = _read(path)
        _require(not immutable or existing == value, "immutable_local_record")
    project_results.atomic_json(path, value, immutable=immutable)
    _fsync_directory(path.parent)
    _require(_read(path) == value, "local_write_unverified")


def _environment(folder, *, create=False):
    path = folder / "environment.json"
    info = folder.stat()
    binding = {
        "root": str(folder.resolve()), "device": info.st_dev, "inode": info.st_ino,
        "uid": os.getuid(), "hostname": socket.gethostname(),
    }
    if not path.exists() and not path.is_symlink():
        _require(not any((folder / name).exists() or (folder / name).is_symlink()
                         for name in ("approvals", "executions", "active.json")), "missing_local_environment")
        if not create:
            return None
        value = {"schema_version": 1, "environment_id": "env-" + uuid4().hex, **binding}
        _write(path, value)
        _fsync_directory(folder.parent)
    value = _read(path)
    evolution.exact(value, "schema_version environment_id root device inode uid hostname")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and project_results.matches(project_results.ID, value["environment_id"]), "invalid_local_environment")
    _require(all(type(value[key]) is type(expected) and value[key] == expected
                 for key, expected in binding.items()), "local_environment_mismatch")
    _fsync_directory(folder)
    _fsync_directory(folder.parent)
    return value["environment_id"]


def _approval(folder, environment, approval_id):
    _require(project_results.matches(project_results.ID, approval_id))
    value = _read(folder / "approvals" / (approval_id + ".json"))
    evolution.exact(value, APPROVAL_FIELDS)
    _identity(value["project_id"], value["skill_key"])
    evolution.relative_path(value["source_path"])
    _version(value["candidate_version_id"])
    _previous(value)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["approval_id"] == approval_id and value["environment_id"] == environment
             and value["approved_by"] == _operator(), "approval_binding_mismatch")
    _require(project_results.matches(project_results.RUN, value["cycle_id"])
             and evolution.matches(r"[a-f0-9]{40}", value["source_commit"])
             and all(evolution.matches(evolution.DIGEST, value[key])
                     for key in ("project_tree_sha256", "evidence_sha256")))
    _require(value["approved_at"] is not None)
    evolution.timestamp(value["approved_at"])
    binding = {key: item for key, item in value.items() if key not in ("approval_id", "approved_at")}
    _require(approval_id == "approval-" + _digest(binding)[:48], "approval_binding_mismatch")
    _fsync_directory(folder / "approvals")
    return value


def _execution(value, approval):
    evolution.exact(value, EXECUTION_FIELDS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "invalid_execution")
    _require(project_results.matches(project_results.ID, value["execution_id"])
             and project_results.matches(project_results.RUN, value["run_id"])
             and value["run_id"].startswith("local-") and value["run_id"] != approval["cycle_id"],
             "invalid_execution")
    _require(all(value[key] == approval[key] for key in
                 ("project_id", "skill_key", "approval_id", "evidence_sha256",
                  "environment_id", "previous_active_version_id", "previous_active_execution_sha256"))
             and value["approved_version_id"] == approval["candidate_version_id"]
             and value["approval_sha256"] == _digest(approval), "execution_binding_mismatch")
    _require(evolution.matches(evolution.DIGEST, value["work_input_sha256"]), "invalid_execution")
    _version(value["loaded_version_id"], nullable=True)
    _require(type(value["skill_version_verified"]) is bool
             and value["status"] in ("verified", "failed", "blocked"), "invalid_execution")
    _require(value["observed_at"] is not None, "invalid_execution")
    evolution.timestamp(value["observed_at"])
    _require(datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
             >= datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00")), "invalid_execution")
    if value["status"] == "verified":
        _require(value["skill_version_verified"] is True
                 and value["loaded_version_id"] == value["approved_version_id"], "unverified_skill_use")
        _require(value["reason_code"] is None or project_results.matches(project_results.ID, value["reason_code"]))
    else:
        _require(value["skill_version_verified"] is False
                 and project_results.matches(project_results.ID, value["reason_code"]), "invalid_execution")
    return value


def _active(folder, environment):
    path = folder / "active.json"
    if not path.exists() and not path.is_symlink():
        return {"schema_version": 1, "environment_id": environment, "selections": {}}
    value = _read(path)
    evolution.exact(value, "schema_version environment_id selections")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and environment is not None and value["environment_id"] == environment
             and isinstance(value["selections"], dict), "invalid_active")
    for project_id, skills in value["selections"].items():
        _require(isinstance(skills, dict) and skills, "invalid_active")
        for skill_key, pointer in skills.items():
            _identity(project_id, skill_key)
            evolution.exact(pointer, "execution_id execution_sha256")
            _require(project_results.matches(project_results.ID, pointer["execution_id"])
                     and evolution.matches(evolution.DIGEST, pointer["execution_sha256"]), "invalid_active")
            receipt = _read(folder / "executions" / (pointer["execution_id"] + ".json"))
            _require(_digest(receipt) == pointer["execution_sha256"], "changed_active_receipt")
            _require(isinstance(receipt, dict) and "approval_id" in receipt, "invalid_active")
            approval = _approval(folder, environment, receipt["approval_id"])
            _execution(receipt, approval)
            _require(receipt["execution_id"] == pointer["execution_id"] and receipt["status"] == "verified"
                     and receipt["project_id"] == project_id and receipt["skill_key"] == skill_key, "invalid_active")
    # A visible replace is not durable until its directory has been synchronized.
    if value["selections"]:
        _fsync_directory(folder / "executions")
    _fsync_directory(folder)
    return value


def _active_snapshot(folder, state, project_id, skill_key):
    pointer = state["selections"].get(project_id, {}).get(skill_key)
    if pointer is None:
        return _pair(None, None)
    receipt = _read(folder / "executions" / (pointer["execution_id"] + ".json"))
    _require(_digest(receipt) == pointer["execution_sha256"], "changed_active_receipt")
    return _pair(receipt["loaded_version_id"], pointer["execution_sha256"])


def _source(root, project_id, reference):
    root = project_results.safe_path(root)
    project = project_results.safe_path(root / "projects" / project_id)
    _require(project.is_dir(), "approval_target_missing")
    identity = capture(["git", "rev-parse", "--show-toplevel", "HEAD"], cwd=root, timeout=30, limit=65536)
    lines = identity.stdout.strip().splitlines()
    _require(identity.returncode == 0 and len(lines) == 2 and Path(lines[0]).resolve() == root.resolve()
             and lines[1] == reference["source_commit"], "approval_source_changed")
    _require(project_results.tree_hash(project) == reference["project_tree_sha256"], "approval_source_changed")
    return project


def _candidate(lifecycle, skill_key, version_id):
    bindings = [row for row in lifecycle["bindings"] if row["skill_key"] == skill_key]
    _require(len(bindings) == 1 and bindings[0]["candidate_version_id"] == version_id, "approval_candidate_mismatch")
    versions = [row for row in lifecycle["records"]["versions"] if row["version_id"] == version_id]
    _require(len(versions) == 1 and versions[0]["capture_scope"] == "complete_bundle", "approval_bundle_incomplete")
    files = {row["path"]: base64.b64decode(row["data"], validate=True)
             for row in lifecycle["file_contents"] if row["version_id"] == version_id}
    captured = evolution.capture_version(files, capture_scope="complete_bundle", complete_inventory=list(files))
    _require(captured[0] == versions[0], "approval_candidate_mismatch")
    return captured


def _evidence(root, *, project_id, skill_key, candidate_version_id, cycle_id, evidence_sha256, results):
    # Shared providers are mandatory; absence is a blocked handoff, never a validator substitute.
    _require(all(callable(getattr(module, name, None)) for module, name in (
        (project_results, "load_cycles"), (project_results, "load_replay_evidence"),
        (skill_assessments, "validate_work_item"))), "approval_validation_unavailable")
    _identity(project_id, skill_key)
    _version(candidate_version_id)
    _require(project_results.matches(project_results.RUN, cycle_id)
             and evolution.matches(evolution.DIGEST, evidence_sha256))
    results = project_results.safe_path(results)
    raw_cycle = project_results.read_bytes(results / project_id / cycle_id / "cycle.json")
    _require(sha256(raw_cycle).hexdigest() == evidence_sha256, "approval_evidence_changed")
    reports = project_results.load_reports(results)
    report_map = {(row["project_id"], row["run_id"]): row for row in reports}
    evaluations = project_results.load_replay_evidence(results, rows=reports)
    cycles = project_results.load_cycles(results, rows=reports)
    cycle = cycles.get((project_id, cycle_id))
    _require(cycle is not None and raw_cycle == project_results.encoded(cycle), "approval_evidence_changed")
    _require(cycle["execution_mode"] == "live" and cycle["confirmation_status"] == "passed"
             and cycle["selected_candidate_version_id"] == candidate_version_id
             and cycle["skill_key"] == skill_key and cycle["confirmation_ref"] is not None, "cycle_not_approvable")
    evolution.relative_path(cycle["source_path"])
    observed = {}

    def retain(path, value, limit=project_results.LIMIT):
        raw = project_results.read_bytes(path, limit)
        _require(raw == project_results.encoded(value), "approval_evidence_changed")
        observed[path] = (sha256(raw).hexdigest(), limit)

    retain(results / project_id / cycle_id / "cycle.json", cycle)
    _require((project_id, cycle_id) in report_map, "missing_approval_evidence")
    retain(results / project_id / cycle_id / "report.json", report_map[(project_id, cycle_id)])
    references = [row["evaluation_ref"] for row in cycle["rounds"] if row["evaluation_ref"] is not None]
    _require(references, "cycle_not_approvable")
    selected = None
    development = False
    source = None
    for ref in [*references, cycle["confirmation_ref"]]:
        evolution.exact(ref, "project_id run_id path sha256")
        _require(ref["project_id"] == project_id and ref["path"] == "replay-evaluation.json"
                 and project_results.matches(project_results.RUN, ref["run_id"]), "invalid_approval_reference")
        key = (project_id, ref["run_id"])
        _require(key in evaluations, "missing_approval_evidence")
        item = evaluations[key]
        replay, report, lifecycle = item["replay"], item["report"], item["lifecycle"]
        folder = results / project_id / ref["run_id"]
        for filename, value, limit in (("replay-evaluation.json", replay, project_results.LIMIT),
                                       ("report.json", report, project_results.LIMIT),
                                       ("skill-evolution.json", lifecycle, project_results.EVOLUTION_LIMIT)):
            retain(folder / filename, value, limit)
        _require(_digest(replay) == ref["sha256"] and replay["execution_mode"] == "live"
                 and report["origin"] not in ("sample", "historical_import"), "approval_evidence_changed")
        reference, row = replay["reference"], replay["evaluation"]
        _require(reference["project_id"] == project_id and reference["skill_key"] == skill_key
                 and reference["source_path"] == cycle["source_path"]
                 and reference["original_version_id"] == cycle["original_version_id"], "approval_target_mismatch")
        project = _source(root, project_id, reference)
        _require(reference["evaluator_sha256"] == project_results.evaluator_hash(root)
                 and reference["rubric_sha256"] == project_checks.digest(
                     project_results.read_json(Path(root) / "eval/skill-guide-rubric.json")), "approval_evaluator_changed")
        bundle = [item for item in skill_guide.discover(project) if item["path"] == cycle["source_path"]]
        _require(len(bundle) == 1 and skill_pipeline.captured_files(project, bundle[0])[0]["version_id"]
                 == cycle["original_version_id"], "approval_source_changed")
        work = row["work"]
        _require(project_results.matches(project_results.ID, work["task_id"])
                 and evolution.matches(evolution.DIGEST, work["input_sha256"]), "invalid_approval_work")
        work_path = (Path(root) / ".skillops-private/work-items" / work["task_id"]
                     / (work["input_sha256"] + ".json"))
        private = project_results.read_json(work_path)
        private = skill_assessments.validate_work_item(private, project=project, source_commit=reference["source_commit"])
        retain(work_path, private)
        _require(private["input_sha256"] == _digest({k: v for k, v in private.items() if k != "input_sha256"}),
                 "approval_work_changed")
        _require(work == {**{key: private[key] for key in ("task_id", "input_sha256", "split", "checks")},
                          "provenance": "recorded"} and private["project_id"] == project_id
                 and private["input_sha256"] == reference["input_sha256"], "approval_work_changed")
        confirmation = ref == cycle["confirmation_ref"]
        _require(private["split"] == ("confirmation" if confirmation else "development"), "approval_work_changed")
        if row["candidate_version_id"] == candidate_version_id:
            captured = _candidate(lifecycle, skill_key, candidate_version_id)
            _require(selected is None or selected == captured, "approval_candidate_mismatch")
            selected = captured
            if not confirmation:
                development |= row["decision"]["status"] == "improved"
        if confirmation:
            _require(row["candidate_version_id"] == candidate_version_id and replay["generation"] is None
                     and row["decision"]["status"] in ("improved", "not_improved"), "confirmation_not_verified")
        source = reference
    _require(selected is not None and development, "cycle_not_approvable")
    for path, (expected, limit) in observed.items():
        _require(sha256(project_results.read_bytes(path, limit)).hexdigest() == expected, "approval_evidence_changed")
    _source(root, project_id, source)
    _require(project_results.evaluator_hash(root) == source["evaluator_sha256"], "approval_evaluator_changed")
    return {"source_path": cycle["source_path"], "source_commit": source["source_commit"],
            "project_tree_sha256": source["project_tree_sha256"]}, selected


def approve(root, *, project_id, skill_key, candidate_version_id, cycle_id,
            evidence_sha256, expected_active_version_id, expected_active_execution_sha256, results):
    """Persist an exact local binding after the CLI's separate human confirmation."""
    _local_only()
    _identity(project_id, skill_key)
    expected = _pair(expected_active_version_id, expected_active_execution_sha256)
    with repositories._state(root) as (folder, _):
        environment = _environment(folder, create=True)
        active = _active(folder, environment)
        _require(_active_snapshot(folder, active, project_id, skill_key) == expected, "active_conflict")
        source, _ = _evidence(root, project_id=project_id, skill_key=skill_key,
                              candidate_version_id=candidate_version_id, cycle_id=cycle_id,
                              evidence_sha256=evidence_sha256, results=results)
        _require(_environment(folder) == environment, "local_environment_mismatch")
        _require(_active(folder, environment) == active, "active_conflict")
        binding = {
            "schema_version": 1, "project_id": project_id, "skill_key": skill_key, **source,
            "candidate_version_id": candidate_version_id, "cycle_id": cycle_id, "evidence_sha256": evidence_sha256,
            "approved_by": _operator(), "environment_id": environment,
            "previous_active_version_id": expected_active_version_id,
            "previous_active_execution_sha256": expected_active_execution_sha256,
        }
        identifier = "approval-" + _digest(binding)[:48]
        path = folder / "approvals" / (identifier + ".json")
        if path.exists() or path.is_symlink():
            return _approval(folder, environment, identifier)
        value = {**binding, "approval_id": identifier, "approved_at": datetime.now(timezone.utc).isoformat()}
        _write(path, value)
        _require(_environment(folder) == environment, "local_environment_mismatch")
        return _approval(folder, environment, identifier)


def resolve_approved(root, *, project_id, skill_key, approval_id, candidate_version_id, evidence_sha256, results):
    """Resolve one named approval and recaptured bundle; never record use or fall back."""
    _local_only()
    _identity(project_id, skill_key)
    with repositories._state(root) as (folder, _):
        environment = _environment(folder)
        approved = _approval(folder, environment, approval_id)
        _require(all(approved[key] == value for key, value in (
            ("project_id", project_id), ("skill_key", skill_key), ("candidate_version_id", candidate_version_id),
            ("evidence_sha256", evidence_sha256))), "approval_binding_mismatch")
        active = _active(folder, environment)
        _require(_active_snapshot(folder, active, project_id, skill_key) == _previous(approved), "active_conflict")
        source, candidate = _evidence(root, project_id=project_id, skill_key=skill_key,
                                       candidate_version_id=candidate_version_id, cycle_id=approved["cycle_id"],
                                       evidence_sha256=evidence_sha256, results=results)
        _require(all(approved[key] == value for key, value in source.items()), "approval_source_changed")
        _require(_environment(folder) == environment, "local_environment_mismatch")
        _require(_approval(folder, environment, approval_id) == approved, "approval_binding_mismatch")
        _require(_active(folder, environment) == active, "active_conflict")
        return approved, candidate


def record_execution(root, *, approval, receipt):
    """Accept only adapter-verified receipts; durable use precedes an Active CAS.

    The adapter must recheck evidence and the staged inventory after invocation.
    No runtime trust can be inferred from a model's JSON or a public projection.
    """
    _local_only()
    with repositories._state(root) as (folder, _):
        environment = _environment(folder)
        _require(isinstance(approval, dict) and "approval_id" in approval)
        saved = _approval(folder, environment, approval["approval_id"])
        _require(saved == approval, "approval_binding_mismatch")
        _execution(receipt, saved)
        _source(root, saved["project_id"], saved)
        active = _active(folder, environment)
        project_id, skill_key = saved["project_id"], saved["skill_key"]
        pointer = {"execution_id": receipt["execution_id"], "execution_sha256": _digest(receipt)}
        path = folder / "executions" / (receipt["execution_id"] + ".json")
        if path.exists() or path.is_symlink():
            _require(_read(path) == receipt, "immutable_local_record")
            _require(active["selections"].get(project_id, {}).get(skill_key) == pointer, "active_conflict")
            return receipt
        _require(_active_snapshot(folder, active, project_id, skill_key) == _previous(saved), "active_conflict")
        if (folder / "executions").exists():
            for existing in (folder / "executions").glob("*.json"):
                prior = _read(existing)
                evolution.exact(prior, EXECUTION_FIELDS)
                _execution(prior, _approval(folder, environment, prior["approval_id"]))
                _require(existing.stem == prior["execution_id"], "invalid_execution")
                _require(prior["run_id"] != receipt["run_id"], "execution_run_exists")
        _write(path, receipt)
        _require(_environment(folder) == environment, "local_environment_mismatch")
        _require(_approval(folder, environment, saved["approval_id"]) == saved, "approval_binding_mismatch")
        _source(root, saved["project_id"], saved)
        if receipt["status"] == "verified":
            # ponytail: one registry lock serializes all Skills; shard only if contention matters.
            _require(_active(folder, environment) == active, "active_conflict")
            active["selections"].setdefault(project_id, {})[skill_key] = pointer
            _write(folder / "active.json", active, immutable=False)
            _require(_environment(folder) == environment, "local_environment_mismatch")
            _require(_active_snapshot(folder, _active(folder, environment), project_id, skill_key)
                     == _pair(receipt["loaded_version_id"], _digest(receipt)), "active_write_unverified")
        return receipt
