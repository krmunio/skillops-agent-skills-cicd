from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import evaluation
from copilot_runtime import CopilotRuntime, RuntimeFailure, usage_metrics


def observation(cost=100, elapsed=100, score=4):
    dimensions = {name: {"score": score, "rationale": "Synthetic test evidence."} for name in evaluation.DIMENSIONS}
    return {
        "status": "completed", "attempted": True, "skill_activated": True,
        "execution": {
            "fixed": {"all_passed": True, "passed": 1, "total": 1},
            "generated": {"passed": True, "tests_run": 1},
        },
        "judge": evaluation.validate_judge(dimensions),
        "developer_usage": usage_metrics({"totalNanoAiu": cost / 2}, "gpt-6-astra"),
        "judge_usage": usage_metrics({"totalNanoAiu": cost / 2}, "gpt-6-astra"),
        "elapsed_seconds": elapsed,
    }


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("candidates"), "Candidate workflow module is missing.")
        self.c = importlib.import_module("candidates")
        self.root = Path(__file__).resolve().parents[1]
        self.catalog = json.loads((self.root / "eval/tasks.json").read_text())
        self.base = (self.root / "skills/develop/SKILL.md").read_bytes()
        self.enterContext(patch("copilot_runtime.shutil.which", return_value="/test-only/copilot"))

    def historical(self):
        return {
            "schema_version": 2, "purpose": "baseline", "status": "completed",
            "context": {"model": "gpt-6-astra"}, "fingerprint": "historical",
            "input_sha256": evaluation.input_hashes(self.root),
            "skill_sha256": sha256(self.base).hexdigest(),
            "tasks": [{**observation(), **task} for task in self.catalog["tasks"]],
        }

    def pair(self, base=None, candidate=None):
        return {"id": "test-task", "base": base or observation(), "candidate": candidate or observation()}

    def test_packet_selects_current_development_only_and_has_no_invented_failures(self):
        report = self.historical()
        report["aggregate"] = {"secret": "HELDOUT_SENTINEL"}
        report["tasks"][-1]["judge"] = {"secret": "HELDOUT_SENTINEL"}
        packet = self.c.development_packet(self.root, report, "gpt-6-astra")
        self.assertEqual(len(packet["development"]), 4)
        self.assertEqual(packet["failures"], [])
        self.assertNotIn("HELDOUT_SENTINEL", json.dumps(packet))
        self.assertNotIn(self.catalog["tasks"][-1]["id"], json.dumps(packet))
        self.assertNotIn(self.catalog["families"]["updates"]["contract"], json.dumps(packet))

    def test_packet_rejects_duplicate_mislabeled_incompatible_or_missing_rows(self):
        original = self.historical()
        variants = []
        duplicate = deepcopy(original)
        duplicate["tasks"][-1] = deepcopy(duplicate["tasks"][0])
        variants.append(duplicate)
        changed = deepcopy(original)
        changed["tasks"][-1]["split"] = "development"
        variants.append(changed)
        changed = deepcopy(original)
        changed["input_sha256"]["eval/fixed_checks.py"] = "wrong"
        variants.append(changed)
        variants.extend([{**original, "skill_sha256": "wrong"}, {**original, "tasks": original["tasks"][:-1]}])
        for value in variants:
            with self.subTest(value=value), self.assertRaises(RuntimeFailure):
                self.c.development_packet(self.root, value, "gpt-6-astra")

    def test_historical_runner_changes_are_not_fresh_comparison_evidence(self):
        report = self.historical()
        report["input_sha256"]["copilot_runtime.py"] = "previous-runtime"
        self.assertEqual(len(self.c.development_packet(self.root, report, "gpt-6-astra")["development"]), 4)
        report["tasks"][0].update(status="generation_error", error={"code": "timeout"}, attempted=True)
        packet = self.c.development_packet(self.root, report, "gpt-6-astra")
        self.assertEqual(len(packet["failures"]), 1)

    def test_candidate_preserves_frontmatter_and_rejects_invalid_or_task_specific_output(self):
        value = {"instructions": "Check the contract. Correct the cause, test boundaries, and review without inventing results.",
                 "rationale": "A concise reusable checklist is an efficiency hypothesis."}
        skill = self.c.validate_candidate(value, self.base, self.catalog)
        self.assertEqual(skill.split(b"\n---\n")[0], self.base.split(b"\n---\n")[0])
        self.assertNotEqual(skill, self.base)
        for invalid in ({}, {**value, "extra": True}, {**value, "instructions": ""},
                        {**value, "instructions": "```python\npass\n```"},
                        {**value, "instructions": "Call list_issues directly."},
                        {**value, "instructions": "\0"}, {**value, "instructions": "x" * 9000}):
            with self.subTest(value=invalid), self.assertRaises(RuntimeFailure):
                self.c.validate_candidate(invalid, self.base, self.catalog)

    def test_artifact_reads_reject_traversal_symlinks_and_oversize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for identifier in ("../outside", "/tmp/file", "invalid"):
                with self.assertRaises(RuntimeFailure):
                    self.c.read_artifact(root, identifier, "candidate.json")
            run_id = "20260914T000000Z-0123456789ab"
            folder = root / "runs" / run_id
            folder.mkdir(parents=True)
            target = folder / "candidate.json"
            target.symlink_to(self.root / "README.md")
            with self.assertRaises(RuntimeFailure):
                self.c.read_artifact(root, run_id, "candidate.json")
            target.unlink()
            target.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            with self.assertRaises(RuntimeFailure):
                self.c.read_artifact(root, run_id, "candidate.json")

    def test_gate_exact_efficiency_boundaries_and_equal_quality_rejection(self):
        for cost, elapsed, expected in ((90, 105, "eligible_for_canary"), (90.001, 100, "rejected"),
                                        (90, 105.001, "rejected"), (100, 100, "rejected"),
                                        (105, 90, "eligible_for_canary")):
            with self.subTest(cost=cost, elapsed=elapsed):
                decision = self.c.decide([self.pair(candidate=observation(cost, elapsed))], 1)
                self.assertEqual(decision["decision"], expected)

    def test_gate_quality_only_improvement_and_mixed_regression(self):
        base = observation(score=3)
        self.assertEqual(self.c.decide([self.pair(base=base)], 1)["decision"], "eligible_for_canary")
        for suite in ("fixed", "generated"):
            base = observation()
            base["execution"][suite]["all_passed" if suite == "fixed" else "passed"] = False
            self.assertEqual(self.c.decide([self.pair(base=base)], 1)["decision"], "eligible_for_canary")
        candidate = observation(cost=50)
        candidate["judge"]["dimensions"]["review_quality"]["score"] = 2
        candidate["judge"] = evaluation.validate_judge(candidate["judge"]["dimensions"])
        self.assertEqual(self.c.decide([self.pair(base=observation(score=3), candidate=candidate)], 1)["decision"], "rejected")

    def test_gate_blocks_incomplete_duplicate_and_invalid_metrics(self):
        self.assertEqual(self.c.decide([], 1)["decision"], "blocked")
        pair = self.pair()
        self.assertEqual(self.c.decide([pair, pair], 2)["decision"], "blocked")
        pair["candidate"]["status"] = "generation_error"
        self.assertEqual(self.c.decide([pair], 1)["decision"], "blocked")
        pair = self.pair()
        pair["candidate"]["execution"]["fixed"]["all_passed"] = False
        self.assertEqual(self.c.decide([pair], 1)["decision"], "rejected")
        pair = self.pair(candidate=observation(cost=0))
        self.assertEqual(self.c.decide([pair], 1)["decision"], "blocked")
        for value in (None, True, -1, math.inf, 10**400, -(10**400)):
            pair = self.pair()
            pair["candidate"]["developer_usage"]["nano_aiu"]["value"] = value
            self.assertEqual(self.c.decide([pair], 1)["decision"], "blocked")

    def prepare(self, root):
        for name in (*evaluation.FINGERPRINT_FILES, "skills/develop/SKILL.md"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / name, target)
        runtime = CopilotRuntime(root)
        run_id = "20260914T000000Z-0123456789ab"
        folder = root / "runs" / run_id
        folder.mkdir(parents=True)
        (folder / "report.json").write_text(json.dumps(self.historical()))
        return runtime, run_id

    def test_propose_is_llm_authored_and_compare_pairs_are_fresh_and_continue_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, baseline_id = self.prepare(root)
            value = {"instructions": "Read the contract, fix the cause, cover boundaries, and report only supported claims.",
                     "rationale": "Test a shorter reusable checklist."}
            def generate(prompt, model, role, work, artifact, expected_skill=None):
                self.assertEqual(role, "generator")
                self.assertIsNone(expected_skill)
                self.assertNotIn(self.catalog["tasks"][-1]["id"], prompt)
                return {"content": json.dumps(value), "usage": usage_metrics(None, model)}
            with patch.object(runtime, "invoke", side_effect=generate) as invoke:
                summary, code = self.c.propose(runtime, "gpt-6-astra", baseline_id)
            self.assertEqual(code, 0)
            self.assertEqual(invoke.call_count, 1)
            candidate_id = summary["run_id"]
            self.assertEqual((root / "skills/develop/SKILL.md").read_bytes(), self.base)
            context = {"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}
            with patch.object(self.c, "context_for", return_value=context), patch.object(
                self.c, "find_calibration", return_value=None
            ), patch.object(self.c, "evaluate_task") as execute:
                blocked, exit_code = self.c.compare(runtime, "gpt-6-astra", root, candidate_id)
                self.assertEqual(exit_code, 2)
                execute.assert_not_called()
            calls = []
            def execute(runtime, model, task, contract, seed, skill, rubric, context, identity, directory):
                calls.append((task["id"], directory.name, skill))
                row = {**observation(), **task}
                if len(calls) == 1:
                    row["status"] = "generation_error"
                return row
            with patch.object(self.c, "context_for", return_value=context), patch.object(
                self.c, "find_calibration", return_value={"path": str(root / "runs/control/calibration.json")}
            ), patch.object(self.c, "evaluate_task", side_effect=execute):
                report, code = self.c.compare(runtime, "gpt-6-astra", root, candidate_id)
            self.assertEqual(code, 2)
            self.assertEqual(len(calls), 10)
            self.assertEqual([arm for _, arm, _ in calls], [
                "base", "candidate", "candidate", "base", "base", "candidate", "candidate", "base", "base", "candidate",
            ])
            self.assertTrue(all(skill == self.base for _, arm, skill in calls if arm == "base"))
            stored = json.loads((root / report["artifact"]).read_text())
            self.assertEqual(stored["arms"]["base"]["requested"], 5)
            self.assertEqual(stored["decision"]["decision"], "blocked")
            (root / "runs" / candidate_id / "SKILL.md").write_text("tampered")
            with patch.object(self.c, "evaluate_task") as execute:
                report, code = self.c.compare(runtime, "gpt-6-astra", root, candidate_id)
                self.assertEqual(code, 2)
                execute.assert_not_called()

    def test_invalid_generation_prerequisite_and_response_leave_terminal_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, baseline_id = self.prepare(root)
            with patch.object(runtime, "invoke") as invoke:
                summary, code = self.c.propose(runtime, "gpt-6-astra", "../invalid")
                self.assertEqual(code, 2)
                invoke.assert_not_called()
                self.assertEqual(json.loads((root / summary["artifact"]).read_text())["status"], "blocked")
            with patch.object(runtime, "invoke", return_value={"content": '{"instructions":""}', "usage": {}}):
                summary, code = self.c.propose(runtime, "gpt-6-astra", baseline_id)
                self.assertEqual(code, 2)
                self.assertFalse((root / "runs" / summary["run_id"] / "SKILL.md").exists())
                self.assertEqual((root / "skills/develop/SKILL.md").read_bytes(), self.base)

    def test_oversized_integer_feedback_writes_blocked_record_without_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, baseline_id = self.prepare(root)
            path = root / "runs" / baseline_id / "report.json"
            original = json.loads(path.read_text())
            for value in (10**400, -(10**400)):
                original["tasks"][0]["developer_usage"]["nano_aiu"]["value"] = value
                path.write_text(json.dumps(original))
                with self.subTest(value=value), patch.object(runtime, "invoke") as invoke:
                    try:
                        summary, code = self.c.propose(runtime, "gpt-6-astra", baseline_id)
                    except OverflowError:
                        self.fail("Oversized measurement escaped the structured blocked-result path.")
                    self.assertEqual(code, 2)
                    invoke.assert_not_called()
                    stored = json.loads((root / summary["artifact"]).read_text())
                    self.assertEqual(stored["status"], "blocked")
                    self.assertEqual(stored["error"]["code"], "invalid_metric")
                    self.assertFalse((root / "runs" / summary["run_id"] / "SKILL.md").exists())
            self.assertEqual((root / "skills/develop/SKILL.md").read_bytes(), self.base)
