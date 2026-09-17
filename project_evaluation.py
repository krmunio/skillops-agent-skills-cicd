"""Project orchestration with explicit live-evaluation gates and public-only output."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
import time

from copilot_runtime import MIN_AI_CREDITS, CopilotRuntime, RuntimeFailure, capture
import project_results as results
import repositories
import project_checks
import skill_guide
import skill_pipeline


class BudgetRuntime:
    def __init__(self, runtime, budget):
        self.runtime = runtime
        self.budget = budget

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def invoke(self, *args, **kwargs):
        if self.budget["calls"] >= self.budget["max_calls"]:
            raise RuntimeFailure("call_limit", "The authorized model-call limit was reached.")
        remaining = self.budget["deadline"] - time.monotonic()
        if remaining <= 0:
            raise RuntimeFailure("time_limit", "The authorized time window was reached.")
        self.budget["calls"] += 1
        kwargs["timeout"] = min(180, remaining)
        if "max_ai_credits" in self.budget:
            kwargs["max_ai_credits"] = self.budget["max_ai_credits"]
        return self.runtime.invoke(*args, **kwargs)


def assess(root, project, run_id, source_commit, policy, runtime_factory=CopilotRuntime):
    return assess_with_details(root, project, run_id, source_commit, policy, runtime_factory=runtime_factory)[0]


def resolve_images(project):
    plan = project_checks.discover(project)
    languages = {"python" if check["runner"] in ("pytest", "unittest") else "node" for check in plan["checks"]}
    if plan["gates"]:
        languages.add("node")
    images = {}
    for language in sorted(languages):
        tag = "python:3.12-slim" if language == "python" else "node:22-slim"
        result = capture(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], timeout=15)
        identity = result.stdout.strip()
        results.require(not result.returncode and results.matches(r"sha256:[a-f0-9]{64}", identity),
                        "missing_check_image")
        images[language] = identity
    return images


def assess_with_details(root, project, run_id, source_commit, policy, *, runtime_factory=CopilotRuntime, history=()):
    root = Path(root)
    folder = root / "projects" / project["id"]
    row = {
        "schema_version": 1, "project_id": project["id"], "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "origin": "github_actions",
        "purpose": "project_assessment", "source_commit": source_commit,
        "project_tree_sha256": project["tree_sha256"], "evaluator_sha256": results.evaluator_hash(root),
        "source_report_sha256": None, "source_schema_version": None,
        "guide": results.axis("not_assessed", "no_skills"),
        "execution": results.axis("configuration_required", "no_adapter"),
    }
    if project["error"]:
        row["guide"] = results.axis("blocked", "unsafe_project")
        row["execution"] = results.axis("blocked", "unsafe_project")
        return results.validate(row), None, None
    if any(next((folder / name).rglob("SKILL.md"), None) is not None
           for name in (".github/skills", ".claude/skills", "skills")):
        row["guide"] = results.axis("blocked", "guide_integration_pending")
    adapter = project["adapter"]
    if adapter not in (None, repositories.EVALUATION_SET):
        row["execution"] = results.axis("configuration_required", "unsupported_adapter")
        return results.validate(row), None, None
    reason = None
    if policy.get("enabled") is not True:
        reason = "live_disabled"
    elif policy.get("authenticated") is not True:
        reason = "missing_auth"
    elif not isinstance(policy.get("budget"), dict):
        reason = "missing_limits"
    if reason:
        row["execution"] = results.axis("blocked", reason)
        return results.validate(row), None, None
    budget = policy["budget"]
    before = budget["calls"]
    try:
        bundles = skill_guide.discover(folder)
        if not bundles:
            images = resolve_images(folder)
            preparation_seconds = min(180, budget["deadline"] - time.monotonic())
            results.require(preparation_seconds > 0, "time_limit")
            with project_checks.prepared_images(folder, images, timeout=preparation_seconds) as prepared:
                checked = project_checks.execute(
                    folder, project_checks.discover(folder), prepared, deadline=budget["deadline"])
            row["execution"] = results.axis(
                checked["status"], "evaluation_completed" if checked["status"] == "completed" else "evaluation_failed",
                {"requested": len(checked["cases"]),
                 "correctness_successes": sum(case["status"] == "passed" for case in checked["cases"]),
                 "cli_invocations": 0},
            )
            return results.validate(row), None, None
        raw_runtime = runtime_factory(root)
        runtime = BudgetRuntime(raw_runtime, budget)
        rubric = results.read_json(root / "eval/skill-guide-rubric.json")
        evaluated, failures = [], []
        with raw_runtime.locked(), ExitStack() as setup:
            check_error = None
            try:
                images = resolve_images(folder)
                preparation_seconds = min(180, budget["deadline"] - time.monotonic())
                results.require(preparation_seconds > 0, "time_limit")
                prepared = setup.enter_context(
                    project_checks.prepared_images(folder, images, timeout=preparation_seconds))
            except (RuntimeFailure, OSError) as error:
                check_error = error.code if isinstance(error, RuntimeFailure) else "io_error"
                prepared = {}
            for bundle in bundles:
                key = skill_pipeline.skill_key(project["id"], bundle["path"], history)
                identifier = sha256(key.encode()).hexdigest()
                artifact = runtime.private / "assessments" / run_id / project["id"] / identifier
                try:
                    evaluated.append(skill_pipeline.evaluate_skill(
                        runtime, "gpt-6-astra", folder, bundle, key, rubric, prepared, artifact,
                        deadline=budget["deadline"], check_error=check_error))
                except (RuntimeFailure, OSError) as error:
                    failure = {"source_path": bundle["path"],
                               "code": error.code if isinstance(error, RuntimeFailure) else "io_error"}
                    failures.append(failure)
                    artifact.mkdir(parents=True, exist_ok=True)
                    (artifact / "failure.json").write_bytes(results.encoded(failure))
        assessments = [item for item, _ in evaluated]
        complete_quality = not failures and all(
            all(item["quality"][arm] is not None and item["quality"][arm]["status"] == "completed"
                for arm in ("base", "candidate")) for item in assessments)
        row["guide"] = results.axis(
            "completed" if complete_quality else "blocked",
            "evaluation_completed" if complete_quality else "evaluation_failed",
            {"skills": len(bundles), "errors": len(failures)},
        )
        incomplete = failures or any(item["errors"] or item["decision"]["status"] == "unverified" for item in assessments)
        rejected = any(item["decision"]["status"] == "rejected" for item in assessments)
        row["execution"] = results.axis(
            "blocked" if incomplete else "completed", "assessment_unverified" if incomplete else "evaluation_completed",
            {"cli_invocations": budget["calls"] - before}, "rejected" if rejected else None,
        )
        lifecycle, details = skill_pipeline.attachments(results.validate(row), evaluated) if evaluated else (None, None)
        return row, lifecycle, details
    except (RuntimeFailure, OSError) as error:
        reason = error.code if isinstance(error, RuntimeFailure) and error.code in results.REASONS else "runtime_error"
        row["execution"] = results.axis("blocked", reason, {"cli_invocations": budget["calls"] - before})
    return results.validate(row), None, None


def policy_from_environment():
    policy = {
        "enabled": os.environ.get("SKILLOPS_LIVE_EVALUATION_ENABLED") == "true",
        "authenticated": bool(os.environ.get("COPILOT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")),
    }
    try:
        calls = int(os.environ.get("SKILLOPS_MAX_INVOCATIONS", ""))
        seconds = int(os.environ.get("SKILLOPS_MAX_SECONDS", ""))
    except ValueError:
        return policy
    if 0 < calls <= 1000 and 0 < seconds <= 1200:
        policy["budget"] = {"calls": 0, "max_calls": calls, "deadline": time.monotonic() + seconds}
        credit = os.environ.get("SKILLOPS_MAX_AI_CREDITS_PER_SESSION")
        if credit is not None:
            try:
                credit = float(credit)
            except ValueError:
                policy.pop("budget")
                return policy
            if not math.isfinite(credit) or credit < MIN_AI_CREDITS:
                policy.pop("budget")
                return policy
            policy["budget"]["max_ai_credits"] = credit
    return policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--project", help="Evaluate only this catalog project; omitted means the entire catalog.")
    parser.add_argument("--history", type=Path, help="Previously validated results, used for stable Skill identity.")
    args = parser.parse_args()
    try:
        results.require(results.matches(results.RUN, args.run_id))
        results.require(results.matches(r"[a-f0-9]{40}", args.source_commit))
        projects = results.catalog(args.root)
        if args.project is not None:
            projects = [project for project in projects if project["id"] == args.project]
            results.require(bool(projects), "unknown_project")
        policy = policy_from_environment()
        prior = {}
        if args.history is not None:
            rows = results.load_reports(args.history)
            assessments = results.load_assessments(args.history, rows)
            for item in sorted(rows, key=lambda row: (row["created_at"], row["run_id"]), reverse=True):
                key = (item["project_id"], item["run_id"])
                if key in assessments:
                    prior.setdefault(item["project_id"], []).append(assessments[key])
        failures = 0
        for project in projects:
            row, lifecycle, assessment = assess_with_details(
                args.root, project, args.run_id, args.source_commit, policy, history=prior.get(project["id"], ()))
            results.store(args.output, row)
            if lifecycle is not None:
                results.store_evolution(args.output, lifecycle)
            if assessment is not None:
                results.store_assessments(args.output, assessment)
            failures += any(row[part]["status"] in ("blocked", "failed") for part in ("guide", "execution"))
            print(json.dumps({"project": project["id"], "guide": row["guide"]["status"],
                              "execution": row["execution"]["status"]}))
        results.reindex(args.root, args.output)
        return 2 if failures else 0
    except (RuntimeFailure, OSError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "io_error"
        print(json.dumps({"status": "blocked", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
