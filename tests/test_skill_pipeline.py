import json
import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import evolution_records as evolution
import project_checks
import skill_assessments
import skill_guide
import skill_pipeline
from copilot_runtime import RuntimeFailure


class SkillPipelineTests(unittest.TestCase):
    def test_actual_skill_versions_drive_paired_outputs_and_regression_decision(self):
        for regression, check_error in ((False, None), (True, None), (False, "unsupported_dependencies")):
            with self.subTest(regression=regression, check_error=check_error), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                project = root / "project"
                path = project / ".github/skills/develop/SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text("---\nname: develop\ndescription: Repair code and validate it.\n---\nCheck code.\n")
                (project / "api.py").write_text("VALUE = 0\n")
                (project / "tests").mkdir()
                (project / "tests/test_api.py").write_text(
                    "import unittest\nfrom api import VALUE\n"
                    "class Api(unittest.TestCase):\n"
                    "    def test_bug(self): self.assertEqual(VALUE, 1)\n"
                    "    def test_stable(self): self.assertLessEqual(VALUE, 1)\n")
                bundle = skill_guide.discover(project)[0]
                runtime = Mock(project=root, cli="/test-only/copilot", env={})
                runtime.private = root / "private"
                runtime.private.mkdir()
                applications = []
                progress = []

                def invoke(prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
                    if role == "generator":
                        return {"content": json.dumps({
                            "instructions": "Check code and preserve every pre-existing behavior.",
                            "addressed_findings": ["workflow_clarity"],
                            "hypothesis": "Explicit preservation guidance may improve verification.",
                        })}
                    self.assertEqual(role, "developer")
                    self.assertEqual(kwargs["skill_name"], "develop")
                    source = Path(expected_skill).read_text()
                    applications.append(source)
                    candidate = "preserve every" in source
                    content = "VALUE = 2\n" if candidate and regression else "VALUE = 1\n"
                    from copilot_runtime import verify_staged_version
                    version_id = verify_staged_version(expected_skill, kwargs["expected_version"])
                    return {"content": json.dumps({"files": {"api.py": content}}),
                            "skill_version_verified": True, "staged_version_id": version_id,
                            "elapsed_seconds": 2, "usage": {"nano_aiu": {"value": 123}}}

                runtime.invoke.side_effect = invoke
                calls = []
                def quality(runtime, model, selected, rubric, artifact):
                    calls.append(selected)
                    return {"status": "pass", "static": {"findings": []},
                            "judge": {"dimensions": {"workflow_clarity": {
                                "score": 3 if len(calls) == 2 else 2, "status": "pass", "rationale": "fixture",
                            }}}}

                actual_check = project_checks.execute
                images = {"python": "sha256:" + "a" * 64}
                if os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1":
                    images["python"] = project_checks.capture(
                        ["docker", "image", "inspect", "python:3.12-slim", "--format", "{{.Id}}"]).stdout.strip()
                def check(project, plan, images, **kwargs):
                    self.assertEqual(kwargs.get("deadline"), 9999999999)
                    if os.environ.get("SKILLOPS_CONTAINER_TESTS") == "1":
                        return actual_check(project, plan, images, **kwargs)
                    value = (project / "api.py").read_text()
                    statuses = {"stable": "failed" if "2" in value else "passed",
                                "bug": "failed" if "0" in value else "passed"}
                    return {
                        "plan_sha256": plan["sha256"], "environment_sha256": "a" * 64,
                        "protected_sha256": "b" * 64, "elapsed_seconds": 1,
                        "status": "failed" if "failed" in statuses.values() else "completed",
                        "cases": [{"id": key, "status": status} for key, status in statuses.items()], "gates": [],
                    }
                with patch.object(skill_pipeline.skill_guide, "evaluate_bundle", side_effect=quality), patch.object(
                        skill_pipeline.project_checks, "execute", side_effect=check):
                    assessed, captures = skill_pipeline.evaluate_skill(
                        runtime, "gpt-6-astra", project, bundle, "skillops:develop",
                        {"dimensions": ["workflow_clarity"]}, images, root / "run",
                        deadline=9999999999, check_error=check_error,
                        progress=lambda *event: progress.append(event))
                starts = [event[0] for event in progress if event[1] == "started"]
                ends = [event[0] for event in progress if event[1] != "started"]
                self.assertEqual(starts, ends)
                for stage in ("base_quality", "generation", "candidate_quality", "qualification"):
                    self.assertIn(stage, starts)
                self.assertEqual(assessed["decision"]["status"],
                                 "unverified" if check_error else "rejected" if regression else "improved")
                self.assertEqual(len(calls), 2)
                self.assertEqual(sum(call.args[2] == "generator" for call in runtime.invoke.call_args_list), 1)
                self.assertEqual(len(captures), 2)
                self.assertEqual(len(applications), 0 if check_error else 2)
                if not check_error:
                    self.assertNotEqual(applications[0], applications[1])
                self.assertEqual((project / "api.py").read_text(), "VALUE = 0\n")
                self.assertNotIn("preserve every", path.read_text())
                if not check_error:
                    self.assertEqual(assessed["applications"]["candidate"]["measurement"],
                                     {"cost_nano_aiu": 123, "elapsed_seconds": 2})
                else:
                    self.assertIn({"stage": "preparation", "code": check_error}, assessed["errors"])
                self.assertEqual(json.loads((root / "run/assessment.json").read_text()), assessed)
                skill_assessments.validate_skill(assessed)

    def test_proposal_cannot_edit_protected_tests_or_escape_the_project(self):
        for name in ("tests/test_api.py", "../escape.py", "/absolute.py", "package.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "api.py").write_text("original")
                with self.assertRaises(RuntimeFailure):
                    skill_pipeline.apply_output(root, {"files": {name: "replaced"}},
                                                {"api.py": "original"})
                self.assertEqual((root / "api.py").read_text(), "original")

    def test_candidate_keeps_frontmatter_and_resource_bytes(self):
        original = {"SKILL.md": b"---\nname: review\ndescription: Review code.\n---\nOld.\n",
                    "references/checks.md": b"Do not change this resource.\n"}
        changed = skill_pipeline.candidate_files(original, {
            "instructions": "Review changed behavior and retained tests.",
            "addressed_findings": [], "hypothesis": "Clearer instructions.",
        })
        self.assertEqual(changed["references/checks.md"], original["references/checks.md"])
        self.assertEqual(changed["SKILL.md"].split(b"\n---\n")[0],
                         original["SKILL.md"].split(b"\n---\n")[0])
        version, _ = evolution.capture_version(changed, capture_scope="complete_bundle",
                                                complete_inventory=list(changed))
        self.assertEqual(version["capture_scope"], "complete_bundle")

    def test_assembled_results_bind_every_skill_version_to_immutable_report(self):
        from test_skill_assessments import fixture
        import project_results
        report, lifecycle, assessment = fixture()
        captures = []
        for version in lifecycle["records"]["versions"]:
            files = {item["path"]: base64.b64decode(item["data"]) for item in lifecycle["file_contents"]
                     if item["version_id"] == version["version_id"]}
            captures.append((version, files))
        data, details = skill_pipeline.attachments(report, [(assessment["skills"][0], captures)])
        project_results.validate_evolution(data, report)
        skill_assessments.validate(details, report, data)
        self.assertEqual(len(data["records"]["versions"]), 2)
        self.assertEqual(data["bindings"][0]["skill_key"], assessment["skills"][0]["skill_key"])
        self.assertEqual(data["records"]["sources"][0]["path"], ".github/skills/develop")

    def test_prior_validated_source_association_reuses_identity_without_name_guessing(self):
        prior = [{"skills": [{"source_path": ".github/skills/review", "skill_key": "auto:existing"}]}]
        self.assertEqual(skill_pipeline.skill_key(".github/skills/review", prior), "auto:existing")
        self.assertNotEqual(skill_pipeline.skill_key(".claude/skills/review", prior), "auto:existing")
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.skill_key("../outside", prior)


if __name__ == "__main__":
    unittest.main()
