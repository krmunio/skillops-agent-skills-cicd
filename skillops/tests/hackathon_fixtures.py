"""Offline-only shared replay/cycle fixtures; never imported by production."""

import base64
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import shutil

from candidates import POLICY
import evolution_records as evolution
import project_checks
import project_results as results


def digest(value):
    return sha256(results.encoded(value)).hexdigest()


def fixture(root):
    project = Path(root) / "sample_repo"
    project.mkdir()
    (project / "app.py").write_text("value = 0\n")
    (project / "tests").mkdir()
    (project / "tests/test_app.py").write_text(
        "import unittest\nclass TestApp(unittest.TestCase):\n"
        "    def test_development(self): self.assertTrue(True)\n"
        "    def test_confirmation(self): self.assertTrue(True)\n"
        "    def test_stable(self): self.assertTrue(True)\n")
    bundle = project / "skills/develop"
    bundle.mkdir(parents=True)
    (bundle / "SKILL.md").write_text("---\nname: develop\n---\nCheck changes.\n")
    (bundle / "notes.txt").write_text("Keep companion files.\n")
    plan = project_checks.discover(project)
    protected = project_checks.protected_digest(project, project_checks.protected_files(project))
    tree = results.tree_hash(project)
    work = {
        "schema_version": 1, "task_id": "develop-task", "project_id": "sample_repo",
        "source_commit": "a" * 40, "project_tree_sha256": tree, "request": "Set value to one.",
        "split": "development", "sources": {"app.py": sha256((project / "app.py").read_bytes()).hexdigest()},
        "checks": {"plan_sha256": plan["sha256"], "protected_sha256": protected,
                   "required_case_ids": ["development"], "required_gate_ids": []},
    }
    work["input_sha256"] = digest(work)
    final_work = deepcopy(work)
    final_work.update(task_id="confirm-task", request="Confirm a distinct behavior.", split="confirmation")
    final_work["checks"]["required_case_ids"] = ["confirmation"]
    final_work["input_sha256"] = digest({k: v for k, v in final_work.items() if k != "input_sha256"})
    captures = []
    for body in ("Check changes.", "Check changes carefully.", "Check changes and boundaries."):
        files = {"SKILL.md": f"---\nname: develop\n---\n{body}\n".encode(),
                 "notes.txt": b"Keep companion files.\n"}
        captures.append(evolution.capture_version(files, capture_scope="complete_bundle",
                                                  complete_inventory=sorted(files)))

    def report(run):
        return {
            "schema_version": 1, "project_id": "sample_repo", "run_id": run,
            "created_at": "2026-09-17T12:00:00+00:00", "origin": "local",
            "purpose": "project_assessment", "source_commit": "a" * 40,
            "project_tree_sha256": tree, "evaluator_sha256": "b" * 64,
            "source_report_sha256": None, "source_schema_version": None,
            "guide": results.axis("completed", "evaluation_completed"),
            "execution": results.axis("completed", "evaluation_completed"),
        }

    rubric = {"fixture": "offline guide"}
    images = {"python": "sha256:" + "c" * 64}
    observation = {
        "plan_sha256": plan["sha256"], "environment_sha256": project_checks.digest(images),
        "protected_sha256": protected, "status": "completed",
        "cases": [{"id": name, "status": "passed"} for name in ("confirmation", "development", "stable")],
        "gates": [], "elapsed_seconds": 1,
    }
    quality = {"status": "completed", "rubric_sha256": project_checks.digest(rubric), "context_sha256": "e" * 64,
               "dimensions": [{"id": "clarity", "score": 2}], "findings": []}
    evaluations = {}
    for index, (run, candidate_index, item, generated) in enumerate((
        ("101-1", 1, work, True), ("102-1", 2, work, True), ("103-1", 2, final_work, False),
    )):
        row = report(run)
        original, candidate = captures[0], captures[candidate_index]
        versions = [original[0]["version_id"], candidate[0]["version_id"]]
        reference = {
            "schema_version": 1, "project_id": "sample_repo", "skill_key": "skillops:develop",
            "source_path": "skills/develop", "source_commit": row["source_commit"],
            "project_tree_sha256": tree, "input_sha256": item["input_sha256"],
            "original_version_id": versions[0], "rubric_sha256": quality["rubric_sha256"],
            "quality_context_sha256": quality["context_sha256"], "evaluator_sha256": row["evaluator_sha256"],
            "policy_sha256": digest({"policy": POLICY, "rule": "replay-v1"}),
            "plan_sha256": plan["sha256"], "environment_sha256": observation["environment_sha256"],
            "protected_sha256": protected, "original_checks": deepcopy(observation),
            "base_quality": deepcopy(quality),
        }
        reference["reference_sha256"] = digest(reference)
        improved = index > 0
        evaluation = {
            "skill_key": reference["skill_key"], "source_path": reference["source_path"],
            "base_version_id": versions[0], "candidate_version_id": versions[1],
            "work": {"task_id": item["task_id"], "input_sha256": item["input_sha256"],
                     "split": item["split"], "provenance": "recorded", "checks": deepcopy(item["checks"])},
            "reference_sha256": reference["reference_sha256"],
            "quality": {"base": deepcopy(quality), "candidate": deepcopy(quality)},
            "applications": {arm: {
                "version_id": versions[i], "staged_version_id": versions[i],
                "work_sha256": item["input_sha256"], "output_sha256": str(i + 1) * 64,
                "activated": True, "changed": True, "task_outcome": "satisfied",
                "measurement": {"cost_nano_aiu": 100, "elapsed_seconds": 10},
            } for i, arm in enumerate(("base", "candidate"))},
            "checks": {arm: deepcopy(observation) for arm in ("original", "base", "candidate")},
            "errors": [],
            "decision": {"policy_id": "replay-v1", "status": "improved" if improved else "not_improved",
                         "reasons": [], "regression": {"status": "passed", "reasons": [], "regressions": []}},
        }
        evaluation["quality"]["candidate"]["dimensions"][0]["score"] = 3 if improved else 2
        records = evolution.empty_records()
        records["identities"] = [{"skill_key": reference["skill_key"], "display_name": "develop"}]
        records["sources"] = [{
            "skill_key": reference["skill_key"], "project_id": "sample_repo", "kind": "workspace",
            "scope": "project", "path": reference["source_path"], "observed_at": row["created_at"],
            "evidence_ref": None,
        }]
        records["versions"] = [deepcopy(capture[0]) for capture in (original, candidate)]
        records["skill_versions"] = [{"skill_key": reference["skill_key"], "version_id": v} for v in versions]
        lifecycle = {
            "schema_version": 1, "project_id": "sample_repo", "run_id": run,
            "report_sha256": digest(row), "records": records,
            "bindings": [{"skill_key": reference["skill_key"], "base_version_id": versions[0],
                          "candidate_version_id": versions[1], "legacy_skill_id": None}],
            "file_contents": [
                {"version_id": capture[0]["version_id"], "path": path, "encoding": "base64",
                 "data": base64.b64encode(raw).decode()}
                for capture in (original, candidate) for path, raw in capture[1].items()
            ],
        }
        replay = {
            "schema_version": 1, "project_id": "sample_repo", "run_id": run, "report_sha256": digest(row),
            "execution_mode": "offline_test", "reference": reference,
            "generation": {"parent_version_id": captures[index][0]["version_id"],
                           "feedback_sha256": str(index + 4) * 64,
                           "addressed_findings": ["clarity"], "hypothesis": "Offline fixture hypothesis."}
            if generated else None,
            "evaluation": evaluation,
        }
        evaluations[("sample_repo", run)] = {"report": row, "lifecycle": lifecycle, "replay": replay}
    inputs = {"work_item": work, "evaluations": evaluations}
    for run, packet in (("101-1", feedback(inputs)), ("102-1", feedback(inputs, run="101-1"))):
        evaluations[("sample_repo", run)]["replay"]["generation"]["feedback_sha256"] = digest(packet)
    cycle_report = report("100-1")
    reference = evaluations[("sample_repo", "101-1")]["replay"]["reference"]

    def artifact(run):
        return {"project_id": "sample_repo", "run_id": run, "path": "replay-evaluation.json",
                "sha256": digest(evaluations[("sample_repo", run)]["replay"])}

    rounds = []
    for i, run in enumerate(("101-1", "102-1"), 1):
        replay = evaluations[("sample_repo", run)]["replay"]
        rounds.append({
            "round_id": f"100-1-r{i}", "round_number": i, "run_id": run,
            "parent_version_id": replay["generation"]["parent_version_id"],
            "candidate_version_id": replay["evaluation"]["candidate_version_id"],
            "input_sha256": work["input_sha256"], "reference_sha256": reference["reference_sha256"],
            "feedback_source_round_id": None if i == 1 else "100-1-r1",
            "feedback_sha256": replay["generation"]["feedback_sha256"],
            "evaluation_ref": artifact(run), "decision": deepcopy(replay["evaluation"]["decision"]),
            "stop_reason": None if i == 1 else "improved",
        })
    cycle = {
        "schema_version": 1, "project_id": "sample_repo", "run_id": "100-1",
        "report_sha256": digest(cycle_report), "execution_mode": "offline_test", "cycle_id": "100-1",
        "skill_key": reference["skill_key"], "source_path": reference["source_path"],
        "input_sha256": work["input_sha256"], "reference_sha256": reference["reference_sha256"],
        "original_version_id": captures[0][0]["version_id"], "max_rounds": 2,
        "budget": {"max_invocations": 30, "max_seconds": 600, "max_ai_credits_per_session": 30},
        "rounds": rounds, "stop_reason": "improved", "selected_candidate_version_id": captures[2][0]["version_id"],
        "confirmation_ref": artifact("103-1"), "confirmation_status": "passed",
    }
    return {"project": project, "work_item": work, "confirmation_work_item": final_work,
            "rubric": rubric, "images": images,
            "captures": captures, "evaluations": evaluations, "cycle_report": cycle_report, "cycle": cycle}


def context(data):
    frozen = data["project"].parent / "private" / "sample_repo"
    shutil.copytree(data["project"], frozen)
    work = deepcopy(data["work_item"])
    return {
        "reference": deepcopy(data["evaluations"][("sample_repo", "101-1")]["replay"]["reference"]),
        "work_item": work, "original": deepcopy(data["captures"][0]), "source_project": data["project"],
        "project": frozen, "plan": project_checks.discover(frozen), "images": deepcopy(data["images"]),
        "rubric": deepcopy(data["rubric"]), "sources": {"app.py": (frozen / "app.py").read_text()},
        "execution_mode": "offline_test",
        "feedback_scope": {"input_sha256": work["input_sha256"], "case_ids": ["development"],
                           "gate_ids": [], "test_context_paths": []},
        "feedback": None,
    }


def feedback(data, *, run=None):
    row = data["evaluations"][("sample_repo", run or "101-1")]["replay"]["evaluation"]
    initial = run is None
    check = row["checks"]["original" if initial else "candidate"]
    return {
        "schema_version": 1, "input_sha256": data["work_item"]["input_sha256"],
        "source_round_id": None if initial else f"100-1-r{1 if run == '101-1' else 2}",
        "quality": deepcopy(row["quality"]["base" if initial else "candidate"]),
        "checks": {"cases": [deepcopy(case) for case in check["cases"] if case["id"] == "development"],
                   "gates": []},
        "application": None if initial else deepcopy(row["applications"]["candidate"]),
        "decision": None if initial else deepcopy(row["decision"]),
    }


def write_results(root, data):
    """Write explicit offline fixtures, not production persistence substitutes."""
    root = Path(root)
    for item in data["evaluations"].values():
        report = item["report"]
        folder = root / report["project_id"] / report["run_id"]
        folder.mkdir(parents=True)
        for name, value in (("report.json", report), ("skill-evolution.json", item["lifecycle"]),
                            ("replay-evaluation.json", item["replay"])):
            (folder / name).write_bytes(results.encoded(value))
    folder = root / "sample_repo" / data["cycle"]["run_id"]
    folder.mkdir(parents=True)
    (folder / "report.json").write_bytes(results.encoded(data["cycle_report"]))
    (folder / "cycle.json").write_bytes(results.encoded(data["cycle"]))
    return root
