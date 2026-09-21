"""Generate auditable offline demo records through production iteration/storage code."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skillops"))

from copilot_runtime import RuntimeFailure
import project_results as results
from test_hackathon_integration import ReplayIntegrationTests


SCENARIOS = {
    "confirmation-passed": "test_actual_registered_confirmation_callback_persists_selected_capture_once",
    "confirmation-failed": "test_registered_confirmation_required_case_failure_is_not_passed",
    "n2-feedback": "test_iterations_n2_uses_real_feedback_and_persists_blocked_confirmation",
    "early-improvement": "test_iterations_stop_after_first_improvement",
    "max-rounds": "test_iterations_max_rounds_never_invokes_confirmation",
    "budget-before-attempt": "test_iterations_budget_before_admission_does_not_invent_round",
    "budget-before-evaluation": "test_iterations_budget_before_evaluation_retains_null_run_with_candidate",
    "budget-partial-evaluation": "test_iterations_partial_evaluation_has_saved_unverified_evidence",
    "no-change": "test_iterations_no_change_records_only_the_started_attempt",
    "model-error": "test_iterations_model_error_preserves_previous_round_and_unsaved_attempt",
    "persistence-error": "test_iterations_store_callback_failure_preserves_round_without_terminal_cycle",
}


def integration_commit():
    completed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=False)
    results.require(completed.returncode == 0, "uncommitted_integration")
    subprocess.run(["git", "ls-files", "--error-unmatch", "--", "tests/export_iteration_evidence.py"],
                   cwd=ROOT, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def export(output):
    commit = integration_commit()
    output = results.safe_path(output)
    results.require(not output.exists(), "output_exists")
    output.mkdir(parents=True)
    manifest = {
        "schema_version": 1, "execution_mode": "offline_test", "integration_sha": commit,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reproduction_command": "SKILLOPS_LIVE_EVALUATION_ENABLED=false python3 tests/export_iteration_evidence.py --output <fresh-directory>",
        "boundaries": "Synthetic input project/model/container transport; actual provider, loop, CLI adapters, validators and storage. No copied result fixtures.",
        "approval_eligible": False, "scenarios": [],
    }
    for name, method in SCENARIOS.items():
        case = ReplayIntegrationTests(method)
        try:
            case.setUp()
            case.output = output / name
            getattr(case, method)()
            cycles = results.load_cycles(case.output)
            replays = results.load_replays(case.output)
            manifest["scenarios"].append({
                "name": name, "results_directory": name, "calls": case.budget["calls"],
                "reports": len(results.load_reports(case.output)),
                "replays": [{"project_id": key[0], "run_id": key[1], "path": "replay-evaluation.json",
                             "sha256": sha256_json(row)} for key, row in replays.items()],
                "cycles": [{
                    "project_id": key[0], "cycle_id": key[1], "path": "cycle.json", "sha256": sha256_json(row),
                    "stop_reason": row["stop_reason"], "confirmation_status": row["confirmation_status"],
                    "rounds": [{"round_id": r["round_id"], "run_id": r["run_id"]} for r in row["rounds"]],
                } for key, row in cycles.items()],
            })
        finally:
            case.doCleanups()
    results.require(integration_commit() == commit, "integration_changed")
    results.atomic_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def sha256_json(value):
    from hashlib import sha256
    return sha256(results.encoded(value)).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New evidence directory; never overwrites existing results.")
    args = parser.parse_args()
    try:
        manifest = export(args.output)
    except (RuntimeFailure, OSError, subprocess.CalledProcessError, AssertionError) as error:
        print(f"Offline evidence export failed: {error}", file=sys.stderr)
        return 2
    print(results.encoded(manifest).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
