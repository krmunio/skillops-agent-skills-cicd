"""Local repository bindings and read-only deployment eligibility."""

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from copilot_runtime import RuntimeFailure, strict_json
from evaluation import FAMILIES, canonical, fingerprint, input_hashes


EVALUATION_SET = "issue-management-v2"
EVALUATION_FILES = ("eval/tasks.json", "eval/fixed_checks.py", "eval/rubric.json", "eval/calibration.json")
LIMIT = 2 * 1024 * 1024
IDENTIFIER = r"[a-z0-9]+(?:-[a-z0-9]+)*"
RUN_ID = r"\d{8}T\d{6}Z-[0-9a-f]{12}"
HASH = r"[0-9a-f]{64}"


def require(condition, code, message):
    if not condition:
        raise RuntimeFailure(code, message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def read_file(path, limit=LIMIT):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)),
            "unsafe_path", "Repository inputs must not traverse symbolic links.")
    require(path.is_file(), "missing_input", "Required ordinary input file is missing.")
    try:
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
    except OSError as error:
        raise RuntimeFailure("io_error", "Cannot read repository input.") from error
    require(len(value) <= limit, "input_limit", "Repository input exceeds its size limit.")
    return value


def text(value):
    try:
        return value.decode("utf-8")
    except UnicodeError as error:
        raise RuntimeFailure("invalid_encoding", "Repository inputs must be UTF-8.") from error


def utf8(value):
    try:
        return value.encode("utf-8")
    except UnicodeError as error:
        raise RuntimeFailure("invalid_encoding", "Registry text must be valid Unicode.") from error


def _validate(data):
    require(isinstance(data, dict) and set(data) == {"schema_version", "repositories", "skills", "comparisons"}
            and type(data["schema_version"]) is int and data["schema_version"] == 1,
            "invalid_registry", "Unsupported registry schema.")
    require(all(isinstance(data[key], dict) for key in ("repositories", "skills", "comparisons")),
            "invalid_registry", "Registry collections must be objects.")
    for digest, content in data["skills"].items():
        require(matches(HASH, digest) and isinstance(content, str) and 0 < len(utf8(content)) <= 65536
                and sha256(utf8(content)).hexdigest() == digest,
                "invalid_registry", "Pinned skill content does not match its digest.")
    paths = set()
    for identifier, row in data["repositories"].items():
        require(matches(IDENTIFIER, identifier) and isinstance(row, dict)
                and set(row) == {"path", "skill_id", "skill_sha256", "evaluation_set"},
                "invalid_registry", "Invalid repository registration.")
        path = row["path"]
        require(isinstance(path, str) and 0 < len(utf8(path)) <= 4096 and "\0" not in path
                and Path(path).is_absolute() and str(Path(os.path.abspath(path))) == path and path not in paths,
                "invalid_registry", "Registered paths must be unique normalized absolute paths.")
        paths.add(path)
        require(row["skill_id"] == "develop" and row["evaluation_set"] == EVALUATION_SET
                and isinstance(row["skill_sha256"], str) and row["skill_sha256"] in data["skills"],
                "invalid_registry", "Unknown skill, pin or evaluation adapter.")
    require(all(matches(RUN_ID, key) and matches(HASH, value) for key, value in data["comparisons"].items()),
            "invalid_registry", "Invalid comparison receipt.")
    return data


@contextmanager
def _state(root):
    descriptor = None
    try:
        folder = Path(root) / ".skillops"
        require(not any(p.is_symlink() for p in (folder, *folder.parents)), "unsafe_registry", "Registry path is unsafe.")
        folder.mkdir(mode=0o700, exist_ok=True)
        info = folder.stat()
        require(folder.is_dir() and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
                "unsafe_registry", "Registry directory must be owner-only.")
        lock = folder / "lock"
        require(not lock.is_symlink() and (not lock.exists() or lock.is_file()),
                "unsafe_registry", "Registry lock must be an ordinary file.")
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
                "unsafe_registry", "Registry lock must be owner-only.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeFailure("registry_busy", "Another registry operation holds the lock.") from error
        path = folder / "registry.json"
        require(not path.is_symlink(), "unsafe_registry", "Registry must not be a symbolic link.")
        if path.exists():
            info = path.stat()
            require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
                    "unsafe_registry", "Registry file must be owner-only.")
            data = _validate(strict_json(text(read_file(path))))
        else:
            data = {"schema_version": 1, "repositories": {}, "skills": {}, "comparisons": {}}
        yield folder, data
    except OSError as error:
        raise RuntimeFailure("registry_io", "Registry I/O failed; no successful update is reported.") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write(folder, data):
    _validate(data)
    content = (json.dumps(data, indent=2, allow_nan=False) + "\n").encode()
    require(len(content) <= LIMIT, "registry_limit", "Registry exceeds its size limit.")
    descriptor, name = tempfile.mkstemp(prefix=".registry-", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, folder / "registry.json")
    finally:
        Path(name).unlink(missing_ok=True)


def _snapshot(root, identifier, row, skill):
    """Use one normalized project root for seed reads and snapshot identity."""
    base = Path(os.path.abspath(row["path"] if row else root))
    seeds = {family: text(read_file(base / (Path(spec["seed"]).name if row else spec["seed"]), 128 * 1024))
             for family, spec in FAMILIES.items()}
    binding = None
    if row:
        binding = {
            "repository_id": identifier, "skill_id": row["skill_id"], "skill_sha256": sha256(skill).hexdigest(),
            "evaluation_set": row["evaluation_set"],
            "evaluation_sha256": sha256(canonical({
                name: sha256(read_file(Path(root) / name)).hexdigest() for name in EVALUATION_FILES
            }).encode()).hexdigest(),
            "source_sha256": {family: sha256(seed.encode()).hexdigest() for family, seed in seeds.items()},
        }
    return {
        "binding": binding,
        "skill": skill,
        "seeds": seeds,
        "project_root": str(base),
    }


def register(root, identifier, path, skill="develop", evaluation_set=EVALUATION_SET):
    root = Path(root)
    require(matches(IDENTIFIER, identifier), "invalid_repository_id", "Use a lowercase repository identifier.")
    require(skill == "develop" and evaluation_set == EVALUATION_SET,
            "unsupported_adapter", "Only develop with issue-management-v2 is currently supported.")
    require(isinstance(path, (str, Path)) and "\0" not in str(path) and len(utf8(str(path))) <= 4096,
            "invalid_repository_path", "Use a valid local project path.")
    target = Path(path)
    target = Path(os.path.abspath(target if target.is_absolute() else root / target))
    content = text(read_file(root / "skills/develop/SKILL.md", 65536))
    require(content.startswith("---\n") and "\n---\n" in content, "invalid_skill", "Skill frontmatter is required.")
    digest = sha256(content.encode()).hexdigest()
    row = {"path": str(target), "skill_id": skill, "skill_sha256": digest, "evaluation_set": evaluation_set}
    snapshot = _snapshot(root, identifier, row, content.encode())
    with _state(root) as (folder, data):
        require(identifier not in data["repositories"]
                and all(item["path"] != str(target) for item in data["repositories"].values()),
                "repository_exists", "Repository ID or local path is already registered; pins cannot be overwritten.")
        data["skills"][digest] = content
        data["repositories"][identifier] = row
        _write(folder, data)
    return snapshot["binding"]


def list_repositories(root):
    with _state(root) as (_, data):
        return [{"repository_id": identifier, **row} for identifier, row in sorted(data["repositories"].items())]


def active_version(root, *, project_id, skill_key):
    """Read verified local use only; a legacy entrypoint pin is never Active."""
    import skill_approvals

    skill_approvals._identity(project_id, skill_key)
    with _state(root) as (folder, _):
        environment = skill_approvals._environment(folder)
        active = skill_approvals._active(folder, environment)
        return skill_approvals._active_version(folder, active, project_id, skill_key)


def resolve(root, repository=None):
    root = Path(root)
    if repository is None:
        return _snapshot(root, None, None, read_file(root / "skills/develop/SKILL.md", 65536))
    require(matches(IDENTIFIER, repository), "invalid_repository_id", "Invalid repository identifier.")
    with _state(root) as (_, data):
        require(repository in data["repositories"], "unknown_repository", "Register the target project first.")
        row = data["repositories"][repository]
        return _snapshot(root, repository, row, data["skills"][row["skill_sha256"]].encode())


def assert_snapshot(root, snapshot):
    binding = snapshot["binding"]
    current = resolve(root, binding["repository_id"] if binding else None)
    require(current == snapshot, "inputs_changed", "Repository sources, evaluation set or pinned skill changed.")


def calibration_commitment(root, path, context):
    from candidates import artifact_json, read_artifact
    from skillops import calibration_passed

    root, path = Path(root), Path(path)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise RuntimeFailure("invalid_calibration", "Calibration must be a local run artifact.") from error
    require(len(relative.parts) == 3 and relative.parts[0] == "runs" and relative.name == "calibration.json",
            "invalid_calibration", "Calibration must identify one exact run.")
    identifier = relative.parts[1]
    raw = read_artifact(root, identifier, "calibration.json")
    report = artifact_json(raw)
    require(type(report.get("schema_version")) is int and report["schema_version"] == 2
            and report.get("run_id") == identifier and report.get("purpose") == "calibration"
            and report.get("status") == "completed" and report.get("passed") is True
            and report.get("context") == context and report.get("fingerprint") == fingerprint(root, context)
            and report.get("input_sha256") == input_hashes(root) and calibration_passed(report.get("results")),
            "invalid_calibration", "Completed, current, matching calibration evidence is required.")
    for row in report["results"]:
        execution = row.get("execution")
        fixed = execution.get("fixed") if isinstance(execution, dict) else None
        require(isinstance(fixed, dict) and type(fixed.get("all_passed")) is bool
                and type(fixed.get("passed")) is int and type(fixed.get("total")) is int
                and 0 <= fixed["passed"] <= fixed["total"] and fixed["total"] > 0
                and fixed["all_passed"] == (fixed["passed"] == fixed["total"])
                and fixed["all_passed"] == (row["id"] == "known_good"),
                "invalid_calibration", "Control execution does not establish the expected good/bad outcome.")
    return sha256(raw).hexdigest()


def seal_comparison(root, repository, identifier, expected_digest):
    from candidates import artifact_json, read_artifact

    raw = read_artifact(Path(root), identifier, "comparison.json")
    require(matches(HASH, expected_digest) and sha256(raw).hexdigest() == expected_digest,
            "changed_comparison", "Stored comparison differs from the evaluator-produced bytes.")
    report = artifact_json(raw)
    snapshot = resolve(root, repository)
    context = report.get("context")
    require(report.get("status") == "completed" and report.get("purpose") == "comparison"
            and report.get("run_id") == identifier and isinstance(context, dict)
            and context.get("repository") == snapshot["binding"]
            and report.get("fingerprint") == fingerprint(Path(root), context)
            and report.get("input_sha256") == input_hashes(Path(root)),
            "invalid_comparison", "Only a completed comparison for the current binding can be recorded.")
    require(isinstance(report.get("calibration"), str)
            and calibration_commitment(root, report["calibration"], context) == report.get("calibration_sha256"),
            "changed_calibration", "Consumed calibration changed before comparison recording.")
    with _state(root) as (folder, data):
        require(identifier not in data["comparisons"], "receipt_exists", "Comparison receipt already exists.")
        data["comparisons"][identifier] = expected_digest
        _write(folder, data)


def eligibility(root, repository, identifier):
    from candidates import artifact_json, decide, read_artifact, static_hashes
    from skillops import load_tasks

    root = Path(root)
    result = {"decision": "blocked", "repository_id": repository, "comparison_run": identifier,
              "deployment": False, "reasons": []}
    try:
        snapshot = resolve(root, repository)
        raw = read_artifact(root, identifier, "comparison.json")
        with _state(root) as (_, data):
            require(data["comparisons"].get(identifier) == sha256(raw).hexdigest(),
                    "untrusted_comparison", "Comparison is unrecorded or its bytes changed.")
        report = artifact_json(raw)
        context = report.get("context")
        require(type(report.get("schema_version")) is int and report["schema_version"] == 2
                and report.get("purpose") == "comparison" and report.get("run_id") == identifier
                and report.get("status") == "completed" and isinstance(context, dict)
                and context.get("repository") == snapshot["binding"],
                "incompatible_comparison", "Comparison does not match this repository binding.")
        identity = fingerprint(root, context)
        require(report.get("fingerprint") == identity and report.get("input_sha256") == input_hashes(root),
                "stale_comparison", "Evaluator inputs changed after comparison.")
        require(isinstance(report.get("calibration"), str)
                and calibration_commitment(root, report["calibration"], context) == report.get("calibration_sha256"),
                "changed_calibration", "Consumed calibration bytes changed.")
        candidate_id = report.get("candidate_run")
        candidate_raw = read_artifact(root, candidate_id, "candidate.json")
        candidate = artifact_json(candidate_raw)
        skills = {arm: read_artifact(root, candidate_id, name)
                  for arm, name in (("base", "base-SKILL.md"), ("candidate", "SKILL.md"))}
        hashes = {arm: sha256(value).hexdigest() for arm, value in skills.items()}
        require(report.get("candidate_sha256") == sha256(candidate_raw).hexdigest()
                and candidate.get("purpose") == "candidate" and candidate.get("status") == "completed"
                and candidate.get("run_id") == candidate_id and candidate.get("model") == context.get("model")
                and candidate.get("repository") == snapshot["binding"]
                and candidate.get("static_sha256") == static_hashes(root)
                and candidate.get("base_skill_sha256") == hashes["base"]
                and candidate.get("skill_sha256") == hashes["candidate"]
                and skills["base"] == snapshot["skill"] and report.get("skill_sha256") == hashes,
                "changed_candidate", "Candidate identity or skill snapshots changed.")
        tasks = load_tasks(root)["tasks"]
        pairs = report.get("pairs")
        require(isinstance(pairs, list) and len(pairs) == len(tasks),
                "invalid_comparison", "Every registered task must have one pair.")
        for task, pair in zip(tasks, pairs):
            require(isinstance(pair, dict) and all(pair.get(key) == task[key] for key in ("id", "family", "split")),
                    "invalid_comparison", "Comparison task identities do not match the evaluation set.")
            for arm in ("base", "candidate"):
                row = pair.get(arm)
                require(isinstance(row, dict) and all(row.get(key) == task[key] for key in ("id", "family", "split"))
                        and row.get("status") == "completed" and row.get("attempted") is True
                        and row.get("skill_activated") is True and row.get("skill_sha256") == hashes[arm]
                        and row.get("seed_sha256") == snapshot["binding"]["source_sha256"][task["family"]],
                        "invalid_comparison", "Pair lacks matching source, skill or activation evidence.")
        decision = decide(pairs, len(tasks))
        require(decision == report.get("decision"), "changed_decision", "Stored decision differs from recomputed policy.")
        assert_snapshot(root, snapshot)
        require(fingerprint(root, context) == identity, "inputs_changed", "Evaluator changed during eligibility check.")
        result.update(decision=decision["decision"], reasons=decision["reasons"])
    except RuntimeFailure as error:
        result.update(error={"code": error.code, "message": str(error)}, reasons=[str(error)])
    except OSError as error:
        result.update(error={"code": "io_error", "message": error.strerror}, reasons=["Repository I/O failed."])
    return result, {"eligible_for_canary": 0, "rejected": 1, "blocked": 2}[result["decision"]]
