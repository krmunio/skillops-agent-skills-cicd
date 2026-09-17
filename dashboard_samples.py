"""Generate example results for layout development, never measured execution records."""

import argparse
from hashlib import sha256
from pathlib import Path

import evolution_records as evolution
import project_results as results
import skill_assessments
from skill_pipeline import attachments


PROJECTS = ("project-a", "project-b")
DIMENSIONS = (
    "trigger_description", "workflow_clarity", "generalization", "instruction_quality",
    "principle_of_lack_of_surprise", "progressive_disclosure", "resource_organization",
)
SCENARIOS = (
    ("improved", 4, 4, 5, 80, 40,
     "Record the expected behavior before editing. Verify the changed and unchanged paths before reporting."),
    ("unchanged", 3, 5, 5, 100, 50,
     "Summarize the workflow using a concise checklist while preserving the verification steps."),
    ("regressed", 2, 5, 3, 120, 65,
     "Repeat the same inspection twice and omit the final unchanged-path check."),
)


def digest(value):
    return sha256(results.encoded(value)).hexdigest()


def observation(identifier, passed, seconds):
    return {
        "plan_sha256": digest(["example-plan", identifier]),
        "environment_sha256": digest(["example-environment", identifier]),
        "protected_sha256": digest(["example-protected", identifier]),
        "status": "completed" if passed == 5 else "failed",
        "cases": [{"id": f"workflow::case_{i}", "status": "passed" if i <= passed else "failed"}
                  for i in range(1, 6)],
        "gates": [{"id": "consistency", "status": "passed"}],
        "elapsed_seconds": seconds,
    }


def skill_example(identifier, detected, scenario):
    name, score, base_passed, passed, cost, seconds, change = scenario
    title = detected["display_name"]
    original = (
        f"---\nname: {title}\ndescription: Guide {title} tasks with explicit checks and scoped changes.\n---\n\n"
        f"# {title}\n\nRead the requested outcome and inspect the affected files.\n"
        "Preserve unrelated behavior. Verify the result before reporting completion.\n"
    )
    candidate = original + f"\n## Verification workflow\n\n{change}\n"
    captures = [evolution.capture_version({"SKILL.md": text.encode()}, capture_scope="entrypoint_only")
                for text in (original, candidate)]
    versions = [entry[0]["version_id"] for entry in captures]
    qualities = []
    for value in (3, score):
        qualities.append({
            "status": "completed", "rubric_sha256": digest(["example-rubric", DIMENSIONS]),
            "context_sha256": digest(["example-context", identifier, detected["source_path"]]),
            "dimensions": [{"id": key, "score": None if i >= 5 else value if i < 2 else 3}
                           for i, key in enumerate(DIMENSIONS)],
            "findings": [],
        })
    work_hash = digest(["example-work", identifier, detected["source_path"]])
    row = {
        "skill_key": detected["skill_key"], "source_path": detected["source_path"],
        "base_version_id": versions[0], "candidate_version_id": versions[1],
        "quality": dict(zip(("base", "candidate"), qualities)),
        "generation": {
            "status": "generated", "addressed_findings": ["trigger_description", "workflow_clarity"],
            "hypothesis": "명확한 사용 조건과 검증 순서를 지침에 명시하면 누락과 반복 작업을 줄일 수 있습니다.",
        },
        "work": {"sha256": work_hash, "provenance": "generated", "check_id": "workflow::case_5"},
        "applications": {},
        "checks": {"original": observation(identifier, 4, 50),
                   "base": observation(identifier, base_passed, 50),
                   "candidate": observation(identifier, passed, seconds)},
        "errors": [],
    }
    for i, (arm, correct, arm_cost, arm_seconds) in enumerate(
        (("base", base_passed, 100, 50), ("candidate", passed, cost, seconds))
    ):
        row["applications"][arm] = {
            "version_id": versions[i], "staged_version_id": versions[i], "work_sha256": work_hash,
            "output_sha256": digest(["example-output", identifier, detected["source_path"], name, arm]),
            "activated": True, "changed": True, "task_outcome": "satisfied" if correct == 5 else "not_satisfied",
            "measurement": {"cost_nano_aiu": arm_cost * 1000000000, "elapsed_seconds": arm_seconds},
        }
    row["decision"] = skill_assessments.decide(row)
    return row, captures


def seed(root, output):
    root, output = Path(root), Path(output)
    index = results.reindex(root, output)
    count = 0
    for project in index["projects"]:
        if project["id"] not in PROJECTS:
            continue
        results.require(project["detected_skills"] and not project["skill_discovery_error"], "missing_sample_skills")
        for ordinal, scenario in enumerate(SCENARIOS, 1):
            evaluated = [skill_example(project["id"], skill, scenario) for skill in project["detected_skills"]]
            total = len(evaluated)
            _, _, base_passed, passed, cost, seconds, _ = scenario
            minute = 4 - ordinal
            report = {
                "schema_version": 1, "project_id": project["id"],
                "run_id": f"sample-20260917T11{minute:02d}00Z-{digest([project['id'], ordinal, [row for row, _ in evaluated]])[:12]}",
                "created_at": f"2026-09-17T11:{minute:02d}:00+00:00", "origin": "sample", "purpose": "project_assessment",
                "source_commit": None, "project_tree_sha256": None, "evaluator_sha256": None,
                "source_report_sha256": None, "source_schema_version": None,
                "guide": results.axis("completed", "evaluation_completed", {"skills": total, "cli_invocations": 0}),
                "execution": results.axis("completed" if passed == 5 else "failed", "evaluation_completed", {
                    "base_requested": total * 5, "candidate_requested": total * 5,
                    "base_correctness_successes": total * base_passed, "candidate_correctness_successes": total * passed,
                    "base_judge_score": base_passed * 20, "candidate_judge_score": passed * 20,
                    "base_cost_nano_aiu": total * 100000000000,
                    "candidate_cost_nano_aiu": total * cost * 1000000000,
                    "base_elapsed_seconds": total * 50, "candidate_elapsed_seconds": total * seconds,
                }),
            }
            lifecycle, assessment = attachments(report, evaluated)
            results.store(output, report)
            results.store_evolution(output, lifecycle)
            results.store_assessments(output, assessment)
            count += 1
    results.reindex(root, output)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    print(f"Stored {seed(args.root, args.results)} example result rounds.")
