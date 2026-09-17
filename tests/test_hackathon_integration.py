"""Real providers/validators; only model transport and container boundaries are simulated."""

from contextlib import chdir, contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from copilot_runtime import RuntimeFailure, verify_staged_version
from hackathon_fixtures import digest, fixture
import project_checks
import project_evaluation as runner
import project_results as results
import skill_guide
import skill_pipeline
import skillops


BODY = ("# Develop\nRead the request and source. Preserve tests and safety rules.\n"
        "Consult [notes](notes.txt) when needed. Verify outputs.\n")


class OfflineTransport:
    def __init__(self, root):
        self.project = Path(root)
        self.private = self.project / ".skillops-private"
        self.private.mkdir(mode=0o700, exist_ok=True)
        self.cli, self.env = "/offline/copilot", {}
        self.calls = []
        self.generated = 0

    @contextmanager
    def locked(self):
        yield

    def invoke(self, prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
        self.calls.append((role, prompt))
        if role == "judge":
            rubric_text, evidence_text = prompt.split("RUBRIC:\n", 1)[1].split("\nSKILL_EVIDENCE:\n", 1)
            rubric, evidence = json.loads(rubric_text), json.loads(evidence_text)
            applicability = evidence["static"]["applicability"]
            score = 3 if "Attempt 2." in prompt else 2
            value = {name: {"status": "not_applicable" if applicability.get(name) == "not_applicable" else "pass",
                            "score": None if applicability.get(name) == "not_applicable" else score,
                            "rationale": "Offline model transport fixture."} for name in rubric["dimensions"]}
        elif role == "generator":
            self.generated += 1
            value = {"instructions": BODY + f"\nAttempt {self.generated}.",
                     "addressed_findings": ["workflow_clarity"], "hypothesis": "Offline fixture hypothesis."}
        else:
            assert role == "developer"
            observed = verify_staged_version(expected_skill, kwargs["expected_version"])
            return {"content": json.dumps({"files": {"app.py": "value = 1\n"}}),
                    "staged_version_id": observed, "skill_version_verified": True, "skill_activated": True,
                    "elapsed_seconds": 1, "usage": {"nano_aiu": {"value": 10}}}
        return {"content": json.dumps(value), "elapsed_seconds": 1, "usage": {"nano_aiu": {"value": 10}}}


class ReplayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "projects").mkdir()
        data = fixture(self.root / "projects")
        self.project = data["project"]
        skill = self.project / "skills/develop/SKILL.md"
        skill.write_text("---\nname: develop\ndescription: Implement source changes safely when development is requested.\n---\n" + BODY)
        with (self.project / "tests/test_app.py").open("a") as stream:
            stream.write("\nHIDDEN_CONFIRMATION_SENTINEL = 'never send this to a model'\n")
        (self.root / "eval").mkdir()
        shutil.copyfile(Path(runner.__file__).parent / "eval/skill-guide-rubric.json",
                        self.root / "eval/skill-guide-rubric.json")
        self.rubric = results.read_json(self.root / "eval/skill-guide-rubric.json")
        for args in (("init", "-q"), ("add", "--", "projects", "eval"),
                     ("-c", "user.name=Offline fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "Offline fixture source")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        self.work = data["work_item"]
        self.work.update(source_commit=self.commit, project_tree_sha256=results.tree_hash(self.project))
        self.work["checks"].update(
            plan_sha256=project_checks.discover(self.project)["sha256"],
            protected_sha256=project_checks.protected_digest(self.project, project_checks.protected_files(self.project)),
            required_case_ids=["python-tests:development", "python-tests:stable"])
        self.work["input_sha256"] = digest({k: v for k, v in self.work.items() if k != "input_sha256"})
        self.work_path = self.root / "development.json"
        self.work_path.write_bytes(results.encoded(self.work))
        self.key = skill_pipeline.skill_key("sample_repo", "skills/develop", [])
        self.output = self.root / "results"
        self.images = data["images"]
        self.raw = OfflineTransport(self.root)
        self.budget = {"calls": 0, "max_calls": 30, "max_seconds": 120, "deadline": time.monotonic() + 120,
                       "max_ai_credits": 30}
        self.runtime = runner.BudgetRuntime(self.raw, self.budget)
        self.runtime.execution_mode = "offline_test"
        self.check_inputs = []
        self.enterContext(patch.object(project_checks, "container_capture", side_effect=self.container))

    def container(self, source, controls, image, argv, **kwargs):
        content = (Path(source) / "app.py").read_text()
        self.check_inputs.append(content)
        failed = content != "value = 1\n"
        cases = [{"id": "development", "status": "failed" if failed else "passed"},
                 {"id": "stable", "status": "passed"},
                 {"id": "hidden-confirmation", "status": "passed"}]
        return subprocess.CompletedProcess([], 0, json.dumps({
            "status": "failed" if failed else "completed", "cases": cases}), "")

    def prepare(self, work=None, name="prepare"):
        return skill_pipeline.prepare_replay(
            self.runtime, "offline-model", self.project, skill_guide.discover(self.project)[0], self.key,
            self.rubric, self.images, self.runtime.private / name,
            work_item=work or self.work, deadline=self.budget["deadline"])

    def api(self, module, name):
        value = getattr(module, name, None)
        self.assertTrue(callable(value), f"Missing integration API: {module.__name__}.{name}")
        return value

    def test_actual_provider_common_validators_and_shared_persistence_callback(self):
        context = self.prepare()
        generation, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate-1", deadline=self.budget["deadline"])
        row, captures = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / "evaluate-1",
            deadline=self.budget["deadline"])
        self.assertEqual(row["decision"]["status"], "not_improved")
        before = results.encoded(row)
        persist = self.api(runner, "persist_replay")
        reference = persist(self.output, row, captures, generation, context["reference"], execution_mode="offline_test")
        self.assertEqual(set(reference), {"project_id", "run_id", "path", "sha256"})
        self.assertEqual(reference["path"], "replay-evaluation.json")
        self.assertEqual(results.encoded(row), before)
        packet = skill_pipeline.development_feedback(
            self.runtime, context, row, source_round_id="100-1-r1")
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.development_feedback(self.runtime, context, deepcopy(row), source_round_id="100-1-r1")
        generation2, candidate2 = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", candidate, packet, self.runtime.private / "generate-2",
            deadline=self.budget["deadline"])
        row2, captures2 = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate2, self.runtime.private / "evaluate-2",
            deadline=self.budget["deadline"])
        self.assertEqual(row2["decision"]["status"], "improved")
        reference2 = persist(self.output, row2, captures2, generation2, context["reference"],
                             execution_mode="offline_test")
        self.assertNotEqual(reference["run_id"], reference2["run_id"])
        self.assertEqual(generation2["parent_version_id"], candidate[0]["version_id"])
        self.assertEqual(row2["base_version_id"], context["original"][0]["version_id"])
        self.assertEqual(len(results.load_replays(self.output)), 2)
        self.assertEqual(self.budget["calls"], 9)
        self.assertEqual(self.budget["max_seconds"], 120)
        final = deepcopy(self.work)
        final.update(task_id="final-task", split="confirmation", request="A separate final request.")
        final["input_sha256"] = digest({k: v for k, v in final.items() if k != "input_sha256"})
        with self.assertRaises(RuntimeFailure) as raised:
            self.prepare(final, "confirmation")
        self.assertEqual(raised.exception.code, "confirmation_isolation_unverified")
        self.assertEqual(self.budget["calls"], 9)
        self.assertEqual((self.project / "app.py").read_text(), "value = 0\n")
        self.assertEqual(results.tree_hash(self.project), self.work["project_tree_sha256"])
        for role, prompt in self.raw.calls:
            self.assertNotIn("HIDDEN_CONFIRMATION_SENTINEL", prompt)
            self.assertNotIn("hidden-confirmation", prompt)
            if role == "developer":
                self.assertIn(self.work["request"], prompt)
        self.assertFalse((self.root / ".skillops").exists())

    def run_one(self):
        run = self.api(runner, "run_replay")
        @contextmanager
        def prepared(project, images, **kwargs):
            yield images
        with patch.object(runner, "resolve_images", return_value=self.images), patch.object(
                project_checks, "prepared_images", side_effect=prepared):
            return run(
                self.root, project_id="sample_repo", skill_key=self.key, work_item=self.work_path,
                output=self.output, model="offline-model", execution_mode="offline_test",
                policy={"enabled": True, "authenticated": True, "budget": self.budget},
                runtime_factory=lambda root: self.raw)

    def test_one_replay_adapter_uses_real_provider_and_records_unverified_confirmation(self):
        self.budget["calls"] = 7
        output = self.run_one()
        self.assertFalse(output["approval_eligible"])
        self.assertEqual(output["confirmation_status"], "not_run")
        self.assertEqual(output["confirmation_reason"], "confirmation_isolation_unverified")
        self.assertEqual(len(results.load_replays(self.output)), 1)
        self.assertEqual(self.budget["calls"], 12)
        self.assertEqual(output["execution_mode"], "offline_test")
        self.assertEqual(output["budget"]["max_seconds"], 120)

    def test_exhausted_shared_budget_never_fabricates_completed_evaluation(self):
        self.budget["max_calls"] = 2
        output = self.run_one()
        self.assertEqual(output["status"], "unverified")
        self.assertEqual(self.budget["calls"], 2)
        self.assertEqual(len(self.raw.calls), 2)
        report = results.load_reports(self.output)[0]
        self.assertEqual(report["execution"]["status"], "blocked")
        self.assertFalse(output["approval_eligible"])
        replay = next(iter(results.load_replays(self.output).values()))
        self.assertEqual(replay["evaluation"]["decision"]["status"], "unverified")

    def test_sidecar_io_failure_cannot_return_a_persisted_reference(self):
        writer = results.atomic_json
        def interrupted(path, data, *args, **kwargs):
            if Path(path).name == "replay-evaluation.json":
                raise OSError("Offline fixture interrupted sidecar write")
            return writer(path, data, *args, **kwargs)
        with patch.object(results, "atomic_json", side_effect=interrupted), self.assertRaises(OSError):
            self.run_one()
        self.assertEqual(results.load_replays(self.output), {})
        self.assertEqual(results.load_reports(self.output)[0]["execution"]["status"], "blocked")

    def test_replay_cli_blocks_without_live_opt_in_and_never_constructs_runtime(self):
        args = ["skillops.py", "replay", "--project", "sample_repo", "--skill-key", self.key,
                "--work-item", str(self.work_path), "--results", str(self.output)]
        enabled = {"SKILLOPS_LIVE_EVALUATION_ENABLED": "true"}
        authenticated = {**enabled, "COPILOT_GITHUB_TOKEN": "offline-test-only"}
        bounded = {**authenticated, "SKILLOPS_MAX_INVOCATIONS": "20", "SKILLOPS_MAX_SECONDS": "120",
                   "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "30"}
        cases = [(args, bounded, "live_disabled"), (args + ["--live"], {}, "live_disabled"),
                 (args + ["--live"], enabled, "missing_auth"),
                 (args + ["--live"], authenticated, "missing_limits"),
                 (args + ["--live"], {**bounded, "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": ""}, "missing_limits")]
        for argv, environment, code in cases:
            with self.subTest(code=code, argv=argv), patch.object(skillops.sys, "argv", argv), patch.dict(
                    runner.os.environ, environment, clear=True), patch.object(
                    runner, "CopilotRuntime", side_effect=AssertionError("Must not initialize live transport")), patch.object(
                    skillops, "CopilotRuntime", side_effect=AssertionError("Must not initialize legacy transport")), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(skillops.main(), 2)
                self.assertEqual(json.loads(stderr.getvalue())["code"], code)
        self.assertFalse(self.output.exists())

    def test_replay_storage_rejects_tampering_before_writing_and_preserves_existing_bytes(self):
        persist = self.api(runner, "persist_replay")
        context = self.prepare()
        generation, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=self.budget["deadline"])
        row, captures = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / "evaluate",
            deadline=self.budget["deadline"])
        bad = deepcopy(row)
        bad["decision"]["status"] = "improved"
        with self.assertRaises(RuntimeFailure):
            persist(self.output, bad, captures, generation, context["reference"], execution_mode="offline_test")
        self.assertFalse(self.output.exists())
        with chdir(self.root):
            ref = persist(Path("results"), row, captures, generation, context["reference"],
                          execution_mode="offline_test", run_id="120-1")
        path = self.output / ref["project_id"] / ref["run_id"] / "replay-evaluation.json"
        before = path.read_bytes()
        altered = results.read_json(path)
        altered["evaluation"]["decision"]["status"] = "improved"
        with self.assertRaises(RuntimeFailure):
            self.api(results, "store_replay")(self.output, altered)
        self.assertEqual(path.read_bytes(), before)

    def test_sample_label_cannot_hide_provider_source_identity(self):
        self.runtime.execution_mode = "sample"
        context = self.prepare()
        generation, candidate = skill_pipeline.generate_candidate(
            self.runtime, "offline-model", context["original"], context["feedback"],
            self.runtime.private / "generate", deadline=self.budget["deadline"])
        row, captures = skill_pipeline.evaluate_candidate(
            self.runtime, "offline-model", context, candidate, self.runtime.private / "evaluate",
            deadline=self.budget["deadline"])
        self.assertEqual(context["reference"]["source_commit"], self.commit)
        with self.assertRaises(RuntimeFailure):
            runner.persist_replay(self.output, row, captures, generation, context["reference"], execution_mode="sample")
        self.assertFalse(self.output.exists())
