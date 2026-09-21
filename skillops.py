"""SkillOps command-line entrypoint."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from copilot_runtime import CopilotRuntime, RuntimeFailure, TOKEN_KEYS, capture, strict_json
from evaluation import (
    FAMILIES, apply_proposal, canonical, execute, fingerprint, input_hashes, judge_prompt,
    validate_judge, validate_proposal,
)
import repositories
import skill_guide


def doctor(runtime, workdir):
    version = capture(runtime.command("--version"), env=runtime.env, timeout=15)
    help_result = capture(runtime.command("--help"), env=runtime.env, timeout=15)
    if version.returncode or help_result.returncode:
        raise RuntimeFailure("cli_preflight", "Could not inspect Copilot CLI.")
    required = (
        "--no-custom-instructions", "--available-tools", "--excluded-tools",
        "--output-format", "--usage-output-file", "--disable-builtin-mcps",
    )
    missing = [flag for flag in required if flag not in help_result.stdout]
    if missing:
        raise RuntimeFailure("unsupported_cli", "Required CLI options are missing: " + ", ".join(missing))
    inventory = runtime.configure(workdir)
    docker = shutil.which("docker")
    if docker is None:
        docker_status, image_status = "missing", "unverified"
    else:
        server = capture([docker, "version", "--format", "{{.Server.Version}}"], timeout=15)
        if server.returncode:
            raise RuntimeFailure("docker_unavailable", "Docker daemon is unavailable.")
        docker_status = server.stdout.strip()
        image = capture([docker, "image", "inspect", "python:3.12-slim", "--format", "{{.Id}}"], timeout=15)
        if image.returncode and "No such image" not in image.stderr:
            raise RuntimeFailure("image_inspection", "Could not inspect the official Python image.")
        image_status = image.stdout.strip() if image.returncode == 0 else "missing"
    return {
        "status": "preflight_checked",
        "cli_version": version.stdout.splitlines()[0],
        "judge_inventory": inventory,
        "docker": docker_status,
        "python_image": image_status,
        "authentication": "unverified",
        "live_contract": "unverified",
        "login_command": "python3 skillops.py login",
        "model_calls": 0,
    }


def probe(runtime, workdir, model):
    destination = runtime.private / "probes"
    destination.mkdir(mode=0o700, exist_ok=True)
    artifact = destination / f"{uuid4().hex}-verified.json"
    record = judge_call(runtime,
        "Try to use a tool to read a dummy file called permission-probe.txt. "
        "If no tools are available, return exactly NO_TOOLS.", model, artifact,
    )
    if record["content"].strip() != "NO_TOOLS":
        raise RuntimeFailure("probe_response", "No-tools probe returned an unexpected response.")
    return {
        "status": "probe_verified", "receipt": str(artifact.relative_to(runtime.project)),
        "observed_model": record["observed_model"], "tool_count": 0,
        "baseline_ready": False, "reason": "Calibration is a separate required gate.",
    }, 0


def write_json(path, value):
    payload = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    Path(path).write_bytes(payload)
    return sha256(payload).hexdigest()


def judge_call(runtime, prompt, model, artifact):
    with tempfile.TemporaryDirectory(prefix="judge-", dir=runtime.private) as work:
        return runtime.invoke(prompt, model, "judge", Path(work), artifact)


def new_run(runtime, purpose):
    root = runtime.project / "runs"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RuntimeFailure("unsafe_path", "Run root must be an ordinary directory.")
    root.mkdir(mode=0o700, exist_ok=True)
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    directory = root / identifier
    directory.mkdir(mode=0o700)
    return directory, {
        "schema_version": 2, "run_id": identifier, "purpose": purpose, "source": "synthetic-demo",
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "running", "tasks": [],
        "limitation": "Small non-adversarial synthetic evaluation; no significance, deployment or tamper-resistance claim.",
    }


def load_tasks(root):
    data = strict_json((root / "eval/tasks.json").read_text())
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        raise RuntimeFailure("invalid_tasks", "Task catalog schema version 2 is required.")
    families = data.get("families")
    if not isinstance(families, dict) or set(families) != set(FAMILIES):
        raise RuntimeFailure("invalid_tasks", "Catalog must define exactly the registered families.")
    for value in families.values():
        if not isinstance(value, dict) or set(value) != {"contract"} or not isinstance(value["contract"], str) or not value["contract"].strip():
            raise RuntimeFailure("invalid_tasks", "Each family requires one nonempty public contract.")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise RuntimeFailure("invalid_tasks", "Task list is empty or invalid.")
    identifiers = set()
    family_splits = {}
    for row in tasks:
        if not isinstance(row, dict) or set(row) != {"id", "family", "split", "request"}:
            raise RuntimeFailure("invalid_tasks", "Invalid task entry.")
        if not isinstance(row["id"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["id"]) or row["id"] in identifiers:
            raise RuntimeFailure("invalid_tasks", "Task identities must be unique safe names.")
        if row["split"] not in ("development", "heldout") or not isinstance(row["request"], str) or not row["request"]:
            raise RuntimeFailure("invalid_tasks", "Invalid task split or request.")
        family = row["family"]
        if not isinstance(family, str) or family not in FAMILIES:
            raise RuntimeFailure("invalid_tasks", "Task references an unknown family.")
        if family in family_splits and family_splits[family] != row["split"]:
            raise RuntimeFailure("invalid_tasks", "A family cannot cross development and held-out splits.")
        family_splits[family] = row["split"]
        identifiers.add(row["id"])
    if set(family_splits) != set(FAMILIES) or set(family_splits.values()) != {"development", "heldout"}:
        raise RuntimeFailure("invalid_tasks", "All families and both splits require tasks.")
    return data


def context_for(runtime, model, workdir):
    checked = doctor(runtime, workdir)
    image = checked["python_image"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RuntimeFailure("missing_image", "Pull python:3.12-slim before evaluation.")
    return {
        "model": model, "cli_version": checked["cli_version"], "image": image,
        "role_settings": strict_json((runtime.config / "settings.json").read_text()),
        "judge_inventory": checked["judge_inventory"], "judge_tools": [],
    }


def summarize(rows, requested):
    evaluated = [row for row in rows if row.get("status") == "completed" and "judge" in row]
    attempts = [row for row in rows if row.get("attempted", True)]
    scores = [row["judge"]["score"] for row in evaluated]
    return {
        "requested": requested, "attempted": len(attempts), "evaluation_completed": len(evaluated),
        "errors": sum(row.get("status") != "completed" for row in attempts),
        "blocked": sum(not row.get("attempted", True) for row in rows),
        "correctness_successes": sum(row.get("execution", {}).get("fixed", {}).get("all_passed") is True for row in rows),
        "mean_judge_score": round(sum(scores) / len(scores), 2) if scores else None,
        "judge_score_denominator": len(scores),
    }


def control_group_passed(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows):
        return False
    results = {row["id"]: row for row in rows}
    if set(results) != {"known_good", "known_bad", "instruction_in_data"} or len(rows) != 3:
        return False
    if any(row.get("status") != "completed" or "judge" not in row for row in rows):
        return False
    for row in rows:
        judge = row["judge"]
        if not isinstance(judge, dict) or set(judge) != {"score", "dimensions"}:
            return False
        try:
            if validate_judge(judge["dimensions"]) != judge:
                return False
        except RuntimeFailure:
            return False
    good, bad, injection = [results[name]["judge"] for name in ("known_good", "known_bad", "instruction_in_data")]
    return (
        good["score"] > bad["score"]
        and good["dimensions"]["requirement_fulfillment"]["score"] > bad["dimensions"]["requirement_fulfillment"]["score"]
        and injection["score"] < 100
    )


def calibration_passed(rows):
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("family"), str) or row["family"] not in FAMILIES
        for row in rows
    ):
        return False
    return all(control_group_passed([row for row in rows if row["family"] == family]) for family in FAMILIES)


def load_controls(root):
    data = strict_json((root / "eval/calibration.json").read_text())
    if not isinstance(data, dict) or not isinstance(data.get("families"), dict) or set(data["families"]) != set(FAMILIES):
        raise RuntimeFailure("invalid_fixture", "Calibration must define every registered family.")
    selectors = {"known_good": ("good", "good"), "known_bad": ("seed", "weak"), "instruction_in_data": ("seed", "weak")}
    controls = []
    for family, group in data["families"].items():
        if not isinstance(group, dict) or set(group) != {"good_source", "good_tests", "weak_tests", "fixtures"}:
            raise RuntimeFailure("invalid_fixture", "Invalid family calibration fields.")
        fixtures = group["fixtures"]
        if not isinstance(fixtures, list) or len(fixtures) != 3:
            raise RuntimeFailure("invalid_fixture", "Exactly three controls per family are required.")
        seen = set()
        seed = (root / FAMILIES[family]["seed"]).read_text()
        for fixture in fixtures:
            if not isinstance(fixture, dict) or set(fixture) != {"id", "implementation", "tests", "review"}:
                raise RuntimeFailure("invalid_fixture", "Invalid control fields.")
            identifier = fixture["id"]
            if not isinstance(identifier, str) or identifier not in selectors or identifier in seen:
                raise RuntimeFailure("invalid_fixture", "Control identities must be unique and complete.")
            if (fixture["implementation"], fixture["tests"]) != selectors[identifier]:
                raise RuntimeFailure("invalid_fixture", "Control implementation/test selectors do not match their identity.")
            seen.add(identifier)
            good = identifier == "known_good"
            proposal = validate_proposal({
                "files": {"issues.py": group["good_source"] if good else seed,
                          "test_generated.py": group["good_tests"] if good else group["weak_tests"]},
                "review": fixture["review"],
            })
            controls.append({"family": family, "id": identifier, "seed": seed, "proposal": proposal})
    return controls


def find_calibration(root, expected):
    for path in sorted(Path(root).glob("*/calibration.json"), reverse=True):
        if path.is_symlink():
            raise RuntimeFailure("unsafe_cache", "Calibration cache must not be a symlink.")
        value = strict_json(path.read_text())
        if not isinstance(value, dict):
            raise RuntimeFailure("invalid_cache", "Calibration cache must contain an object.")
        if value.get("fingerprint") == expected and value.get("passed") is True and value.get("purpose") == "calibration":
            if not isinstance(value.get("results"), list) or not calibration_passed(value["results"]):
                raise RuntimeFailure("invalid_cache", "Calibration cache does not satisfy its gate.")
            return {"path": str(path), "fingerprint": expected, "results": value["results"]}
    return None


def render_report(directory, report):
    write_json(directory / "report.json", report)
    summary = report.get("aggregate", {})
    lines = [
        "# SkillOps baseline", "", f"Status: **{report['status']}**",
        "", "Synthetic demo data; actual CLI executions, not historical customer evidence.",
        report["limitation"], "",
        f"Model: `{report.get('context', {}).get('model', 'unverified')}`",
        f"Catalog version: {report.get('catalog_version', 'unverified')}; family results are grouped, not independent repetitions.",
        f"Correctness: {summary.get('correctness_successes', 0)}/{summary.get('requested', 0)} requested tasks.",
        f"Evaluations completed: {summary.get('evaluation_completed', 0)}; errors: {summary.get('errors', 0)}.",
        f"Mean judge score: {summary.get('mean_judge_score')}; denominator: {summary.get('judge_score_denominator', 0)}.",
        "", "| Task | Family | Split | Status | Fixed checks | Judge score | Seconds |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["tasks"]:
        fixed = row.get("execution", {}).get("fixed", {})
        count = f"{fixed['passed']}/{fixed['total']}" if fixed else "unavailable"
        lines.append(
            f"| {row['id']} | {row['family']} | {row['split']} | {row['status']} | {count} | "
            f"{row.get('judge', {}).get('score', 'unavailable')} | {row.get('elapsed_seconds', 'unavailable')} |"
        )
    if "families" in report:
        lines.extend(["", "| Family | Correct / requested | Evaluated | Mean judge |", "|---|---|---|---|"])
        for family, result in report["families"].items():
            lines.append(f"| {family} | {result['correctness_successes']}/{result['requested']} | "
                         f"{result['evaluation_completed']} | {result['mean_judge_score']} |")
    if "anthropic_skill_guide" in report:
        guide = report["anthropic_skill_guide"]
        counts = guide["summary"]
        lines.extend([
            "", "## Anthropic skill guide", "",
            "Report-only: skill assessments do not change coding-task status or promotion eligibility.",
            guide["limitation"], "",
            f"Skills: {counts['skills']}; pass: {counts['pass']}; review: {counts['review']}; blocked: {counts['blocked']}.",
            "", "| Skill | Status | Files | Bytes | Judge calls |", "|---|---|---|---|---|",
        ])
        for row in guide["skills"]:
            path = re.sub(r"([\\`*_\[\]<>|~])", r"\\\1", row["path"]).replace("\r", r"\r").replace("\n", r"\n")
            lines.append(f"| {path} | {row['status']} | {row['file_count']} | "
                         f"{row['bytes']} | {row['judge_calls']} |")
    lines.extend(["", "Per-call usage, units, rationales and errors are in report.json and each task's artifacts.",
                  "Unavailable usage is not zero. No currency estimate or deployment approval is inferred."])
    (directory / "report.md").write_text("\n".join(lines) + "\n")


def calibrate(runtime, model, judge_work, repository=None):
    directory, report = new_run(runtime, "calibration")
    report.update(passed=False, results=[])
    try:
        snapshot = repositories.resolve(runtime.project, repository) if repository is not None else None
        context = context_for(runtime, model, judge_work)
        if snapshot is not None:
            context = {**context, "repository": snapshot["binding"]}
        identity = fingerprint(runtime.project, context)
        report.update(context=context, fingerprint=identity, input_sha256=input_hashes(runtime.project))
        tasks = load_tasks(runtime.project)
        report["catalog_version"] = tasks["schema_version"]
        controls = load_controls(runtime.project)
        rubric = strict_json((runtime.project / "eval/rubric.json").read_text())
        for fixture in controls:
            family, seed, proposal = fixture["family"], fixture["seed"], fixture["proposal"]
            record = {"id": fixture["id"], "family": family, "status": "running"}
            stage = directory / family / fixture["id"]
            stage.mkdir(parents=True)
            source = stage / "source"
            source.mkdir()
            try:
                apply_proposal(source, proposal)
                evidence = execute(source, context["image"], family)
                record["execution"] = evidence
                if fixture["id"] == "known_good" and not evidence["fixed"]["all_passed"]:
                    raise RuntimeFailure("invalid_fixture", "Known-good code did not pass protected checks.")
                if fixture["id"] != "known_good" and evidence["fixed"]["all_passed"]:
                    raise RuntimeFailure("invalid_fixture", "Known-bad code unexpectedly passed all protected checks.")
                invocation = judge_call(runtime,
                    judge_prompt(tasks["families"][family]["contract"], "Fix the supplied implementation against the entire contract.",
                                 seed, proposal, evidence, rubric), model, stage / "judge.json",
                )
                record.update(judge=validate_judge(strict_json(invocation["content"])), usage=invocation["usage"], status="completed")
            except RuntimeFailure as error:
                record.update(status="error", error={"code": error.code, "message": str(error)})
            report["results"].append(record)
            write_json(directory / "calibration.json", report)
        if fingerprint(runtime.project, context) != identity:
            raise RuntimeFailure("inputs_changed", "Evaluation inputs changed during calibration.")
        if snapshot is not None:
            repositories.assert_snapshot(runtime.project, snapshot)
        report["passed"] = calibration_passed(report["results"])
        report["status"] = "completed" if report["passed"] else "failed"
    except RuntimeFailure as error:
        report.update(status="blocked", error={"code": error.code, "message": str(error)})
    write_json(directory / "calibration.json", report)
    return {"status": report["status"], "passed": report["passed"],
            "artifact": str((directory / "calibration.json").relative_to(runtime.project)),
            "scores": {row["family"] + "/" + row["id"]: row.get("judge", {}).get("score")
                       for row in report["results"]}}, 0 if report["passed"] else 2


def git_command(runtime, repo, *args):
    env = {key: value for key, value in runtime.env.items() if key not in TOKEN_KEYS}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    result = capture(["git", "--no-pager", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                     env=env, timeout=15)
    if result.returncode:
        raise RuntimeFailure("git_error", "Disposable repository command failed: " + result.stderr[:1024])
    return result.stdout


def stage_repository(runtime, work, seed, skill_bytes):
    skill = work / ".github/skills/develop/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(skill_bytes)
    repo = work / "repo"
    repo.mkdir()
    (repo / "issues.py").write_text(seed)
    git_command(runtime, repo, "init", "--quiet", "--template=")
    git_command(runtime, repo, "add", "issues.py")
    return repo, skill


def evaluate_task(runtime, model, task, contract, seed, skill_bytes, rubric, context, identity, task_dir):
    family = task["family"]
    row = {"id": task["id"], "family": family, "split": task["split"], "status": "running", "attempted": True,
           "seed_sha256": sha256(seed.encode()).hexdigest(), "skill_sha256": sha256(skill_bytes).hexdigest()}
    task_dir.mkdir()
    started, phase = time.monotonic(), "generation"
    workspace = tempfile.TemporaryDirectory(prefix="developer-", dir=runtime.private)
    try:
        if fingerprint(runtime.project, context) != identity:
            raise RuntimeFailure("inputs_changed", "Evaluation input fingerprint changed.")
        work = Path(workspace.name)
        repo, staged_skill = stage_repository(runtime, work, seed, skill_bytes)
        prompt = (
            "Invoke /develop first. Fix the Python source below, generate executable unittest tests, and review the diff. "
            "You have only the skill tool. The runner will apply and execute your files. "
            "Do not claim you executed tests. Return exactly one JSON object (no Markdown): "
            '{"files":{"issues.py":"full source","test_generated.py":"full unittest source"},'
            '"review":{"summary":"diff-grounded assessment","risks":[]}}.\n'
            + canonical({"request": task["request"], "contract": contract, "source": seed})
        )
        developer = runtime.invoke(prompt, model, "developer", work, task_dir / "developer.json", staged_skill)
        row["developer_usage"] = developer["usage"]
        row["skill_activated"] = developer["skill_activated"]
        phase = "validation"
        proposal = validate_proposal(strict_json(developer["content"]))
        apply_proposal(repo, proposal)
        write_json(task_dir / "proposal.json", proposal)
        git_command(runtime, repo, "add", "--intent-to-add", "test_generated.py")
        diff = git_command(runtime, repo, "diff", "--no-ext-diff", "--no-textconv", "--", "issues.py", "test_generated.py")
        (task_dir / "diff.patch").write_text(diff)
        phase = "execution"
        row["execution"] = execute(repo, context["image"], family)
        phase = "evaluation"
        row["source_sha256"] = {name: sha256(content.encode()).hexdigest() for name, content in proposal["files"].items()}
        judge = judge_call(runtime, judge_prompt(contract, task["request"], seed, proposal, row["execution"], rubric, diff),
                           model, task_dir / "judge.json")
        row.update(judge=validate_judge(strict_json(judge["content"])), judge_usage=judge["usage"], status="completed")
    except RuntimeFailure as error:
        row.update(status="timeout" if error.code == "timeout" else phase + "_error",
                   error={"code": error.code, "message": str(error)})
    except OSError as error:
        row.update(status=phase + "_error", error={"code": "io_error", "message": error.strerror})
    finally:
        workspace.cleanup()
    row["elapsed_seconds"] = round(time.monotonic() - started, 3)
    row["artifacts"] = str(task_dir.relative_to(runtime.project))
    return row


def baseline(runtime, model, judge_work, repository=None):
    directory, report = new_run(runtime, "baseline")
    requested = 0
    tasks = []
    try:
        data = load_tasks(runtime.project)
        tasks, requested = data["tasks"], len(data["tasks"])
        report["catalog_version"] = data["schema_version"]
        snapshot = repositories.resolve(runtime.project, repository)
        context = context_for(runtime, model, judge_work)
        if snapshot["binding"] is not None:
            context = {**context, "repository": snapshot["binding"]}
        identity = fingerprint(runtime.project, context)
        calibration = find_calibration(runtime.project / "runs", identity)
        if calibration is None:
            raise RuntimeFailure("calibration_required", "Run matching calibration successfully before baseline.")
        if snapshot["binding"] is not None:
            report["calibration_sha256"] = repositories.calibration_commitment(runtime.project, calibration["path"], context)
        report.update(context=context, fingerprint=identity, input_sha256=input_hashes(runtime.project),
                      calibration=str(Path(calibration["path"]).relative_to(runtime.project)))
        seeds, skill_bytes = snapshot["seeds"], snapshot["skill"]
        report["skill_sha256"] = sha256(skill_bytes).hexdigest()
        report["seed_sha256_by_family"] = {family: sha256(seed.encode()).hexdigest() for family, seed in seeds.items()}
        rubric = strict_json((runtime.project / "eval/rubric.json").read_text())
        for task in tasks:
            family = task["family"]
            seed, contract = seeds[family], data["families"][family]["contract"]
            task_dir = directory / task["id"]
            row = evaluate_task(runtime, model, task, contract, seed, skill_bytes, rubric, context, identity, task_dir)
            report["tasks"].append(row)
            report["aggregate"] = summarize(report["tasks"], requested)
            render_report(directory, report)
        guide_rubric = strict_json((runtime.project / "eval/skill-guide-rubric.json").read_text())
        guide = skill_guide.evaluate_project(
            runtime, model, snapshot["project_root"], guide_rubric, directory / "anthropic-skill-guide",
        )
        report["anthropic_skill_guide"] = guide
        if fingerprint(runtime.project, context) != identity:
            raise RuntimeFailure("inputs_changed", "Evaluation inputs changed during baseline.")
        repositories.assert_snapshot(runtime.project, snapshot)
        if snapshot["binding"] is not None:
            if repositories.calibration_commitment(runtime.project, calibration["path"], context) != report["calibration_sha256"]:
                raise RuntimeFailure("changed_calibration", "Consumed calibration changed during baseline.")
        report["status"] = "completed" if all(row["status"] == "completed" for row in report["tasks"]) else "completed_with_errors"
    except RuntimeFailure as error:
        report.update(status="blocked", error={"code": error.code, "message": str(error)})
        completed_ids = {row["id"] for row in report["tasks"]}
        report["tasks"].extend({"id": task["id"], "family": task["family"], "split": task["split"], "status": "blocked", "attempted": False}
                               for task in tasks if task["id"] not in completed_ids)
    report["aggregate"] = summarize(report["tasks"], requested)
    report["splits"] = {
        split: summarize([row for row in report["tasks"] if row["split"] == split],
                         sum(task["split"] == split for task in tasks))
        for split in ("development", "heldout")
    }
    report["families"] = {
        family: summarize([row for row in report["tasks"] if row["family"] == family],
                          sum(task["family"] == family for task in tasks))
        for family in FAMILIES
    }
    render_report(directory, report)
    return {"status": report["status"], "report": str((directory / "report.md").relative_to(runtime.project)),
            **report["aggregate"]}, 0 if report["status"] == "completed" else 2


def approve_local(root, args):
    """Human-only boundary; the owner module revalidates evidence and Active under its lock."""
    import evolution_records as evolution
    import project_results
    import skill_approvals

    project_results.require(not any(os.environ.get(key) for key in ("CI", "GITHUB_ACTIONS")), "local_approval_only")
    project_results.require(sys.stdin.isatty() and sys.stderr.isatty(), "interactive_approval_required")
    version = None if args.expected_active_version == "none" else args.expected_active_version
    receipt_hash = None if args.expected_active_execution_sha256 == "none" else args.expected_active_execution_sha256
    project_results.require(
        (version is None and receipt_hash is None)
        or (evolution.matches(evolution.VERSION_ID, version) and evolution.matches(evolution.DIGEST, receipt_hash)),
        "invalid_active_pair")
    project_results.require(evolution.matches(evolution.VERSION_ID, args.candidate_version)
                            and evolution.matches(evolution.DIGEST, args.evidence_sha256)
                            and project_results.matches(project_results.RUN, args.cycle), "invalid_approval")
    observed = repositories.active_snapshot(root, project_id=args.project, skill_key=args.skill_key)
    project_results.require(observed == {"version_id": version, "execution_sha256": receipt_hash}, "active_conflict")
    binding = {
        "project_id": args.project, "skill_key": args.skill_key, "candidate_version_id": args.candidate_version,
        "cycle_id": args.cycle, "evidence_sha256": args.evidence_sha256,
        "previous_active_version_id": version, "previous_active_execution_sha256": receipt_hash,
    }
    print("Review this exact local approval. Approval does not change Active or authorize model execution.",
          file=sys.stderr)
    print(json.dumps(binding, indent=2), file=sys.stderr)
    expected = "approve " + args.candidate_version
    print(f"Type exactly '{expected}' to approve, or anything else to cancel:", file=sys.stderr)
    try:
        answer = input()
    except (EOFError, KeyboardInterrupt) as error:
        raise RuntimeFailure("approval_cancelled", "No approval was recorded.") from error
    project_results.require(answer == expected, "approval_cancelled")
    approved = skill_approvals.approve(
        root, project_id=args.project, skill_key=args.skill_key, candidate_version_id=args.candidate_version,
        cycle_id=args.cycle, evidence_sha256=args.evidence_sha256, expected_active_version_id=version,
        expected_active_execution_sha256=receipt_hash, results=args.results)
    publication = None
    if args.publish_reviewed:
        from project_evaluation import persist_adoption
        try:
            publication = persist_adoption(args.results, approval=approved, reviewed=True)
        except (RuntimeFailure, OSError) as error:
            raise RuntimeFailure(
                "approval_publication_failed",
                f"Private approval {approved['approval_id']} was retained; public projection failed.") from error
    return {"status": "approved", "approval_id": approved["approval_id"],
            "project_id": approved["project_id"], "skill_key": approved["skill_key"],
            "candidate_version_id": approved["candidate_version_id"], "evidence_sha256": approved["evidence_sha256"],
            "approved_at": approved["approved_at"], "active_changed": False, "model_calls": 0,
            "adoption_ref": publication, "publication_status": "stored_locally" if publication else "not_requested"}


def main():
    parser = argparse.ArgumentParser(description="SkillOps skill generation and coding-task evaluation.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Inspect the isolated runtime without model calls.")
    commands.add_parser("login", help="Authenticate the dedicated Copilot profile interactively.")
    probe_parser = commands.add_parser("probe", help="Run an approved, synthetic no-tools CLI probe.")
    probe_parser.add_argument("--model", default="gpt-6-astra")
    for name in ("calibrate", "baseline"):
        command = commands.add_parser(name, help=f"Run the actual {name} workflow.")
        command.add_argument("--model", default="gpt-6-astra")
        command.add_argument("--repository", help="Registered local project ID.")
    for name, argument in (("propose", "baseline"), ("compare", "candidate")):
        command = commands.add_parser(name, help=f"Run the actual {name} workflow.")
        command.add_argument("--model", default="gpt-6-astra")
        command.add_argument(f"--{argument}", required=True, help="Generated run ID (not a path).")
        if name == "compare":
            command.add_argument("--repository", help="Registered local project ID.")
    register_parser = commands.add_parser("register", help="Register a local project and pin develop without model calls.")
    register_parser.add_argument("--repository", required=True)
    register_parser.add_argument("--path", required=True)
    register_parser.add_argument("--skill", default="develop")
    register_parser.add_argument("--evaluation-set", default=repositories.EVALUATION_SET)
    commands.add_parser("repositories", help="List registered projects without model calls.")
    gate_parser = commands.add_parser("eligibility", help="Check recorded comparison eligibility; never deploy.")
    gate_parser.add_argument("--repository", required=True)
    gate_parser.add_argument("--comparison", required=True)
    preflight_parser = commands.add_parser(
        "approval-preflight", help="Read exact local approval eligibility without initializing or authorizing anything.")
    for option in ("project", "skill-key", "candidate-version", "cycle", "evidence-sha256", "results"):
        preflight_parser.add_argument("--" + option, required=True)
    approval_parser = commands.add_parser("approve", help="Separately confirm one exact local candidate; never execute it.")
    for option in ("project", "skill-key", "candidate-version", "cycle", "evidence-sha256",
                   "expected-active-version", "expected-active-execution-sha256", "results"):
        approval_parser.add_argument("--" + option, required=True)
    approval_parser.add_argument("--publish-reviewed", action="store_true",
                                 help="Store an explicitly reviewed public-safe projection locally; never deploy.")
    use_parser = commands.add_parser("run-approved", help="Use one exact local approval with a new separately authorized task.")
    for option in ("project", "skill-key", "approval", "candidate-version", "evidence-sha256", "work-item", "results"):
        use_parser.add_argument("--" + option, required=True)
    use_parser.add_argument("--model", default="gpt-6-astra")
    use_parser.add_argument("--live", action="store_true")
    use_parser.add_argument("--publish-reviewed", action="store_true",
                            help="Store an explicitly reviewed public-safe projection locally; never deploy.")
    for name in ("replay", "iterate"):
        replay_parser = commands.add_parser(name, help="Run explicitly authorized recorded development work; never approve.")
        replay_parser.add_argument("--project", required=True)
        replay_parser.add_argument("--skill-key", required=True)
        replay_parser.add_argument("--work-item", required=True)
        replay_parser.add_argument("--results", required=True)
        replay_parser.add_argument("--model", default="gpt-6-astra")
        replay_parser.add_argument("--live", action="store_true",
                                   help="Opt in; authentication and approved limits are also required.")
        if name == "iterate":
            replay_parser.add_argument("--max-rounds", type=int, choices=range(1, 11), default=1)
            replay_parser.add_argument("--confirmation-work-item", help="Private precommitted confirmation WorkItem JSON.")
            replay_parser.add_argument("--confirmation-disclosure", help="Operator-reviewed private disclosure JSON; requires confirmation WorkItem.")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parent
        if args.command == "approval-preflight":
            import skill_approvals
            report = skill_approvals.preflight(
                root, project_id=args.project, skill_key=args.skill_key, candidate_version_id=args.candidate_version,
                cycle_id=args.cycle, evidence_sha256=args.evidence_sha256, results=args.results)
            if report["status"] == "eligible":
                command = [sys.executable, "-B", str(root / "skillops.py"), "approve"]
                for option, value in (
                    ("project", args.project), ("skill-key", args.skill_key),
                    ("candidate-version", args.candidate_version), ("cycle", args.cycle),
                    ("evidence-sha256", args.evidence_sha256), ("results", report["results_path"]),
                    ("expected-active-version", report["active"]["version_id"]),
                    ("expected-active-execution-sha256", report["active"]["execution_sha256"]),
                ):
                    command.extend(["--" + option, "none" if value is None else value])
                report["approval_argv"] = command
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "eligible" else 2
        if args.command == "approve":
            print(json.dumps(approve_local(root, args), indent=2))
            return 0
        if args.command == "run-approved":
            from project_evaluation import policy_from_environment, run_approved
            policy = policy_from_environment()
            policy["enabled"] = policy["enabled"] and args.live
            report = run_approved(
                root, project_id=args.project, skill_key=args.skill_key, approval_id=args.approval,
                candidate_version_id=args.candidate_version, evidence_sha256=args.evidence_sha256,
                work_item=args.work_item, output=args.results, model=args.model, policy=policy,
                publish_reviewed=args.publish_reviewed)
            print(json.dumps(report, indent=2))
            return 0 if report["task_outcome"] == "satisfied" else 2
        if args.command in ("replay", "iterate"):
            from project_evaluation import policy_from_environment, run_iterations, run_replay
            policy = policy_from_environment()
            policy["enabled"] = policy["enabled"] and args.live
            options = dict(project_id=args.project, skill_key=args.skill_key, work_item=args.work_item,
                           output=args.results, model=args.model, execution_mode="live", policy=policy)
            if args.command == "iterate":
                report = run_iterations(root, **options, max_rounds=args.max_rounds,
                                        confirmation_work_item=args.confirmation_work_item,
                                        confirmation_disclosure=args.confirmation_disclosure)
                exit_code = 0 if report["status"] in ("improved", "max_rounds", "no_change") else 2
            else:
                report = run_replay(root, **options)
                exit_code = 2 if report["status"] == "unverified" else 0
            print(json.dumps(report, indent=2))
            return exit_code
        if args.command in ("register", "repositories", "eligibility"):
            exit_code = 0
            if args.command == "register":
                report = {"status": "registered", "binding": repositories.register(
                    root, args.repository, args.path, args.skill, args.evaluation_set), "model_calls": 0, "deployment": False}
            elif args.command == "repositories":
                report = {"repositories": repositories.list_repositories(root), "model_calls": 0}
            else:
                report, exit_code = repositories.eligibility(root, args.repository, args.comparison)
            print(json.dumps(report, indent=2))
            return exit_code
        runtime = CopilotRuntime(root)
        with runtime.locked():
            if args.command == "login":
                return subprocess.call(runtime.command("login"), env=runtime.env, cwd=runtime.project)
            workdir = runtime.private / "judge-work"
            if workdir.is_symlink():
                raise RuntimeFailure("unsafe_workspace", "Judge workspace must not be a symlink.")
            workdir.mkdir(mode=0o700, exist_ok=True)
            if args.command == "doctor":
                report, exit_code = doctor(runtime, workdir), 0
                (runtime.private / "doctor.json").write_text(json.dumps(report, indent=2) + "\n")
            elif args.command == "probe":
                report, exit_code = probe(runtime, workdir, args.model)
            elif args.command == "calibrate":
                report, exit_code = calibrate(runtime, args.model, workdir, args.repository)
            elif args.command == "baseline":
                report, exit_code = baseline(runtime, args.model, workdir, args.repository)
            else:
                from candidates import compare, propose
                if args.command == "propose":
                    report, exit_code = propose(runtime, args.model, args.baseline)
                else:
                    report, exit_code = compare(runtime, args.model, workdir, args.candidate, args.repository)
            print(json.dumps(report, indent=2))
            return exit_code
    except RuntimeFailure as error:
        print(json.dumps({"status": "blocked", "code": error.code, "message": str(error)}), file=sys.stderr)
        return 2
    except OSError as error:
        print(json.dumps({"status": "blocked", "code": "io_error", "message": error.strerror}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
