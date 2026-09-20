"""Offline regressions for the approved initially-admitted-body policy."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from copilot_runtime import RuntimeFailure
import skill_guide
import skill_pipeline
import test_project_evaluation as evaluation_tests
import test_skill_pipeline as pipeline_tests
import evolution_records as evolution
import project_results


def original(body, newline="\n"):
    return {"SKILL.md": (f"---{newline}name: develop{newline}description: Develop safely."
                         f"{newline}---{newline}{newline}{body}{newline}").encode(),
            "references/checks.md": b"Keep this resource exactly.\n"}


def response(body):
    return {"instructions": body, "hypothesis": "Synthetic hypothesis.", "addressed_findings": []}


class BodyPolicyTests(unittest.TestCase):
    def assert_code(self, code, function):
        with self.assertRaises(RuntimeFailure) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def test_no_floor_and_utf8_boundary_on_original_canonical_body(self):
        base = original("abcde")
        self.assertEqual(skill_pipeline.candidate_files(base, response("xyz12"))["references/checks.md"],
                         base["references/checks.md"])
        self.assert_code("candidate_instructions_byte_limit",
                         lambda: skill_pipeline.candidate_files(base, response("abcdef")))
        base = original("\u00e9" * 4)
        skill_pipeline.candidate_files(base, response("\u00e9" * 3 + "xy"))
        self.assert_code("candidate_instructions_byte_limit",
                         lambda: skill_pipeline.candidate_files(base, response("\u00e9" * 4 + "x")))

    def test_large_original_ceiling_and_invalid_admission(self):
        base = original("x" * 32768)
        files = skill_pipeline.candidate_files(base, response("y" * 32768))
        self.assertEqual(files["SKILL.md"].split(b"\n---\n")[0], base["SKILL.md"].split(b"\n---\n")[0])
        for value, code in ((original("x" * 32769), "original_body_byte_limit"),
                            (original(" \t "), "original_body_empty"),
                            (original("abc", "\r\n"), "invalid_base_skill")):
            with self.subTest(code=code):
                self.assert_code(code, lambda: skill_pipeline.candidate_files(value, response("x")))

    def test_padding_and_final_lf_do_not_inflate_or_shrink_allowance(self):
        base = original("  abcde  \n\t")
        changed = skill_pipeline.candidate_files(base, response(" \txyz12\n"))
        self.assertEqual(skill_guide.frontmatter(changed["SKILL.md"].decode())[1].strip(), "xyz12")
        self.assert_code("candidate_instructions_byte_limit",
                         lambda: skill_pipeline.candidate_files(base, response("xyz123")))
        self.assert_code("unchanged_candidate",
                         lambda: skill_pipeline.candidate_files(original("abcde"), response("abcde\n")))
        self.assert_code("unchanged_candidate",
                         lambda: skill_pipeline.candidate_files(base, response("abcde")))

    def test_raw_control_and_empty_guards_run_before_outer_strip(self):
        for body, code in ((None, "candidate_instructions_type"), ("", "candidate_instructions_empty"),
                           (" \t\n", "candidate_instructions_empty"),
                           ("\rabc", "candidate_instructions_control_character"),
                           ("abc\r\n", "candidate_instructions_control_character"),
                           ("\vabc", "candidate_instructions_control_character")):
            with self.subTest(code=code, body=body):
                self.assert_code(code, lambda: skill_pipeline.candidate_files(original("long enough"), response(body)))

    def test_initial_original_not_shorter_parent_controls_limit(self):
        self.assertIn("baseline", __import__("inspect").signature(skill_pipeline.candidate_files).parameters)
        base = original("abcdefghij")
        parent = skill_pipeline.candidate_files(base, response("short"))
        larger = skill_pipeline.candidate_files(parent, response("1234567890"), baseline=base)
        self.assertEqual(len(skill_guide.frontmatter(larger["SKILL.md"].decode())[1].strip().encode()), 10)
        self.assert_code("candidate_instructions_byte_limit", lambda: skill_pipeline.candidate_files(
            parent, response("12345678901"), baseline=base))
        self.assertEqual(base, original("abcdefghij"))

    def test_generation_prompt_projects_all_existing_response_constraints(self):
        self.assertTrue(hasattr(skill_pipeline, "generation_prompt"))
        base = original("abcdefghij")
        prompt = skill_pipeline.generation_prompt(base, {"quality": {}, "project_checks": None})
        for value in ("10 UTF-8 bytes", "32768", "4096", "128", "160",
                      "LF", "TAB", "frontmatter", "companion", "characters", "tokens", "JSON"):
            self.assertIn(value, prompt)
        self.assertIn("abcdefghij", prompt)

    def test_all_current_originals_are_admitted_without_changing_frontmatter_or_companions(self):
        from test_skill_assessments import fixture
        root = Path(__file__).resolve().parents[1]
        counts = {}
        for project_id in ("project-a", "project-b"):
            project = root / "projects" / project_id
            report, _, _ = fixture()
            report["project_id"] = project_id
            targets = [(bundle, skill_pipeline.skill_key(project_id, bundle["path"], []))
                       for bundle in skill_guide.discover(project)]
            rubric = project_results.read_json(root / "eval/skill-guide-rubric.json")
            skill_pipeline.admit_targets(project, targets, report, rubric)
            counts[project_id] = len(targets)
            for bundle, _ in targets:
                with self.subTest(project=project_id, path=bundle["path"]):
                    _, files = skill_pipeline.captured_files(project, bundle)
                    limit = len(skill_pipeline.original_body(files).encode())
                    candidate = skill_pipeline.candidate_files(files, response("x" * limit))
                    self.assertEqual(candidate["SKILL.md"].split(b"\n---\n", 1)[0],
                                     files["SKILL.md"].split(b"\n---\n", 1)[0])
                    self.assertEqual({k: v for k, v in candidate.items() if k != "SKILL.md"},
                                     {k: v for k, v in files.items() if k != "SKILL.md"})
                    self.assertEqual(len(skill_pipeline.original_body(candidate).encode()), limit)
        self.assertEqual(counts, {"project-a": 1, "project-b": 14})


class AdmissionTests(unittest.TestCase):
    setUp = evaluation_tests.TargetedQualityTests.setUp
    evaluate = evaluation_tests.TargetedQualityTests.evaluate

    def test_invalid_originals_and_known_prompt_overflow_never_construct_runtime(self):
        path = self.project / ".github/skills/develop/SKILL.md"
        for value, code in ((original("x" * 32769), "original_body_byte_limit"),
                            (original(" \t "), "original_body_empty"),
                            (original("abc", "\r\n"), "invalid_base_skill"),
                            (original("\x7f" * 20000), "prompt_limit")):
            path.write_bytes(value["SKILL.md"])
            with self.subTest(code=code):
                with self.assertRaises(RuntimeFailure) as caught:
                    self.evaluate(self.key)
                self.assertEqual(caught.exception.code, code)
        self.factory.assert_not_called()
        self.quality.assert_not_called()
        self.raw.invoke.assert_not_called()

    def test_whole_selected_capture_reservation_fails_before_runtime(self):
        for name in ("develop", "review"):
            resource = self.project / f".github/skills/{name}/references/data.txt"
            resource.parent.mkdir()
            resource.write_text("x" * 400000)
        with self.assertRaises(RuntimeFailure) as caught:
            self.evaluate()
        self.assertEqual(caught.exception.code, "output_limit")
        self.factory.assert_not_called()
        self.quality.assert_not_called()
        self.raw.invoke.assert_not_called()

    def test_known_quality_prompt_overflow_fails_before_runtime(self):
        (self.root / "eval/skill-guide-rubric.json").write_text(json.dumps({
            "dimensions": ["workflow_clarity"], "instructions": "x" * 100001}))
        with self.assertRaises(RuntimeFailure) as caught:
            self.evaluate(self.key)
        self.assertEqual(caught.exception.code, "skill_batch_limit")
        self.factory.assert_not_called()
        self.raw.invoke.assert_not_called()

    def test_unselected_oversized_original_does_not_change_selected_scope(self):
        (self.project / ".github/skills/review/SKILL.md").write_bytes(original("x" * 32769)["SKILL.md"])
        self.raw.invoke.return_value = {"content": json.dumps(response("Changed."))}
        report, _, details, _ = self.evaluate(self.key)
        self.assertEqual(report["guide"]["metrics"]["skills"], 2)
        self.assertEqual(len(details["skills"]), 1)
        self.assertEqual(details["skills"][0]["generation"]["status"], "generated")
        base = (self.project / ".github/skills/develop/SKILL.md").read_text()
        limit = len(skill_guide.frontmatter(base)[1].strip().encode())
        self.assertIn(f"{limit} UTF-8 bytes", self.raw.invoke.call_args.args[0])

    def test_dynamic_generation_prompt_overflow_makes_no_generator_call(self):
        self.quality.return_value["static"]["findings"] = [
            {"check": str(i), "path": "SKILL.md", "severity": "warning", "message": "x" * 4096}
            for i in range(30)]
        _, _, details, _ = self.evaluate(self.key)
        self.assertIn({"stage": "generation", "code": "prompt_limit"}, details["skills"][0]["errors"])
        self.raw.invoke.assert_not_called()

    def test_dynamic_assessment_overflow_is_rejected_before_any_public_write(self):
        self.quality.return_value["static"]["findings"] = [
            {"check": str(i), "path": "SKILL.md", "severity": "warning", "message": "\\" * 4096}
            for i in range(128)]
        output = self.root / "results"
        args = ["project_evaluation.py", "--root", str(self.root), "--output", str(output),
                "--run-id", "999-1", "--source-commit", "a" * 40, "--project", "sample_repo",
                "--assessment-skill-key", self.key]
        assess = self.m.assess_with_details
        errors = io.StringIO()
        with patch.object(self.m.sys, "argv", args), patch.object(
                self.m, "policy_from_environment", return_value=self.policy), patch.object(
                self.m, "assess_with_details", side_effect=lambda *a, **k: assess(
                    *a, **k, runtime_factory=self.factory)), redirect_stdout(io.StringIO()), redirect_stderr(errors):
            self.assertEqual(self.m.main(), 2)
        self.assertTrue(errors.getvalue(), "Oversized evidence must surface output_limit, not be discarded.")
        self.assertEqual(json.loads(errors.getvalue())["code"], "output_limit")
        self.assertFalse(output.exists())
        self.raw.invoke.assert_not_called()

    def test_durable_pretty_json_limit_is_checked_even_when_compact_schema_fits(self):
        self.quality.return_value["static"]["findings"] = [
            {"check": str(i), "path": "SKILL.md", "severity": "warning", "message": "\\" * 4096}
            for i in range(125)]
        _, _, details, _ = self.evaluate(self.key)
        self.assertLessEqual(len(json.dumps(details).encode()), project_results.LIMIT)
        self.assertGreater(len(project_results.encoded(details)), project_results.LIMIT)
        output = self.root / "results"
        args = ["project_evaluation.py", "--root", str(self.root), "--output", str(output),
                "--run-id", "999-1", "--source-commit", "a" * 40, "--project", "sample_repo",
                "--assessment-skill-key", self.key]
        assess = self.m.assess_with_details
        errors = io.StringIO()
        with patch.object(self.m.sys, "argv", args), patch.object(
                self.m, "policy_from_environment", return_value=self.policy), patch.object(
                self.m, "assess_with_details", side_effect=lambda *a, **k: assess(
                    *a, **k, runtime_factory=self.factory)), redirect_stdout(io.StringIO()), redirect_stderr(errors):
            self.assertEqual(self.m.main(), 2)
        self.assertEqual(json.loads(errors.getvalue())["code"], "output_limit")
        self.assertFalse(output.exists())
        self.raw.invoke.assert_not_called()


class ReplayBodyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.case = pipeline_tests.ReplayTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.set_body("Preserve source behavior and validate the recorded request. " * 3)

    def set_body(self, body, newline="\n"):
        case = self.case
        (case.project / "skills/develop/SKILL.md").write_bytes(original(body, newline)["SKILL.md"])
        case.bundle = skill_guide.discover(case.project)[0]
        case.work["project_tree_sha256"] = project_results.tree_hash(case.project)
        case.work["input_sha256"] = case.hash({k: v for k, v in case.work.items() if k != "input_sha256"})

    def test_second_generation_uses_initial_original_and_shared_prompt(self):
        case = self.case
        context = case.prepare()
        limit = len(skill_guide.frontmatter(context["original"][1]["SKILL.md"].decode())[1].strip().encode())
        bodies = iter(("short", "x" * limit))
        def invoke(*args, **kwargs):
            value = case.invoke(*args, **kwargs)
            if args[2] == "generator":
                value["content"] = json.dumps(response(next(bodies)))
            return value
        case.runtime.invoke.side_effect = invoke
        _, first = skill_pipeline.generate_candidate(
            case.runtime, "offline-model", context["original"], context["feedback"],
            case.runtime.private / "first", deadline=9999999999)
        row, _ = skill_pipeline.evaluate_candidate(
            case.runtime, "offline-model", context, first, case.runtime.private / "first-evaluation",
            deadline=9999999999)
        feedback = skill_pipeline.development_feedback(
            case.runtime, context, row, source_round_id="local-20260917T130000Z-aaaaaaaaaaaa-r1")
        _, second = skill_pipeline.generate_candidate(
            case.runtime, "offline-model", first, feedback, case.runtime.private / "second",
            deadline=9999999999)
        self.assertEqual(len(skill_guide.frontmatter(second[1]["SKILL.md"].decode())[1].strip().encode()), limit)
        prompts = [prompt for role, prompt in case.prompts if role == "generator"]
        self.assertEqual(len(prompts), 2)
        for prompt in prompts:
            self.assertIn(f"{limit} UTF-8 bytes", prompt)
            self.assertIn("4096", prompt)
        self.assertNotEqual(second[0]["version_id"], context["original"][0]["version_id"])

    def test_supplied_replay_candidate_cannot_bypass_initial_allowance(self):
        case = self.case
        context = case.prepare()
        baseline = context["original"][1]
        limit = len(skill_guide.frontmatter(baseline["SKILL.md"].decode())[1].strip().encode())
        files = {**baseline, "SKILL.md": baseline["SKILL.md"].split(b"\n---\n", 1)[0]
                 + b"\n---\n\n" + b"x" * (limit + 1) + b"\n"}
        candidate = evolution.capture_version(files, capture_scope="complete_bundle", complete_inventory=list(files))
        skill_guide.evaluate_bundle.reset_mock()
        with self.assertRaises(RuntimeFailure) as caught:
            skill_pipeline.evaluate_candidate(
                case.runtime, "offline-model", context, candidate, case.runtime.private / "oversized",
                deadline=9999999999)
        self.assertEqual(caught.exception.code, "candidate_instructions_byte_limit")
        skill_guide.evaluate_bundle.assert_not_called()
        case.runtime.invoke.assert_not_called()

    def test_replay_preparation_rejects_original_before_quality(self):
        self.set_body("x" * 32769)
        with self.assertRaises(RuntimeFailure) as caught:
            self.case.prepare()
        self.assertEqual(caught.exception.code, "original_body_byte_limit")
        skill_guide.evaluate_bundle.assert_not_called()
        self.case.runtime.invoke.assert_not_called()
