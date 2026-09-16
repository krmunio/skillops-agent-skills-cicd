"""Project orchestration with explicit live-evaluation gates and public-only output."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time

from copilot_runtime import CopilotRuntime, RuntimeFailure
import project_results as results
import repositories
import skillops


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
        return self.runtime.invoke(*args, **kwargs)


def assess(root, project, run_id, source_commit, policy, runtime_factory=CopilotRuntime):
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
        return results.validate(row)
    if any(next((folder / name).rglob("SKILL.md"), None) is not None
           for name in (".github/skills", ".claude/skills", "skills")):
        row["guide"] = results.axis("blocked", "guide_integration_pending")
    adapter = project["adapter"]
    if adapter is None:
        return results.validate(row)
    if adapter != repositories.EVALUATION_SET:
        row["execution"] = results.axis("configuration_required", "unsupported_adapter")
        return results.validate(row)
    reason = None
    if policy.get("enabled") is not True:
        reason = "live_disabled"
    elif policy.get("authenticated") is not True:
        reason = "missing_auth"
    elif not isinstance(policy.get("budget"), dict):
        reason = "missing_limits"
    if reason:
        row["execution"] = results.axis("blocked", reason)
        return results.validate(row)
    budget = policy["budget"]
    before = budget["calls"]
    try:
        runtime = BudgetRuntime(runtime_factory(root), budget)
        identifier = "ci-" + sha256(project["id"].encode()).hexdigest()[:16]
        repositories.register(root, identifier, str(folder), "develop", repositories.EVALUATION_SET)
        with runtime.locked():
            work = runtime.private / "judge-work"
            results.require(not work.is_symlink(), "unsafe_workspace")
            work.mkdir(mode=0o700, exist_ok=True)
            skillops.probe(runtime, work, "gpt-6-astra")
            _, code = skillops.calibrate(runtime, "gpt-6-astra", work, identifier)
            if code:
                raise RuntimeFailure("evaluation_failed", "Calibration did not complete successfully.")
            report, code = skillops.baseline(runtime, "gpt-6-astra", work, identifier)
            metrics = {key: report[key] for key in report if key in results.METRICS}
            metrics["cli_invocations"] = budget["calls"] - before
            row["execution"] = results.axis(
                "completed" if not code else "failed",
                "evaluation_completed" if not code else "evaluation_failed", metrics,
            )
    except (RuntimeFailure, OSError) as error:
        reason = error.code if isinstance(error, RuntimeFailure) and error.code in results.REASONS else "runtime_error"
        row["execution"] = results.axis("blocked", reason, {"cli_invocations": budget["calls"] - before})
    return results.validate(row)


def policy_from_environment():
    policy = {
        "enabled": os.environ.get("SKILLOPS_LIVE_EVALUATION_ENABLED") == "true",
        "authenticated": bool(os.environ.get("COPILOT_GITHUB_TOKEN")),
    }
    try:
        calls = int(os.environ.get("SKILLOPS_MAX_INVOCATIONS", ""))
        seconds = int(os.environ.get("SKILLOPS_MAX_SECONDS", ""))
    except ValueError:
        return policy
    if 0 < calls <= 1000 and 0 < seconds <= 1200:
        policy["budget"] = {"calls": 0, "max_calls": calls, "deadline": time.monotonic() + seconds}
    return policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        results.require(results.matches(results.RUN, args.run_id))
        results.require(results.matches(r"[a-f0-9]{40}", args.source_commit))
        policy = policy_from_environment()
        failures = 0
        for project in results.catalog(args.root):
            row = assess(args.root, project, args.run_id, args.source_commit, policy)
            results.store(args.output, row)
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
