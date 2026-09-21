"""Real providers/validators; only model transport and container boundaries are simulated."""

from contextlib import chdir, contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
from publication_fixtures import dashboard_fixture
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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
ORIGINAL_BODY = BODY + "Inspect all relevant source and test context before proposing a change.\n"


class OfflineTransport:
    def __init__(self, root):
        self.project = Path(root)
        self.private = self.project / ".skillops-private"
        self.private.mkdir(mode=0o700, exist_ok=True)
        self.cli, self.env = "/offline/copilot", {}
        self.calls = []
        self.generated = 0
        self.scores = (2, 3)
        self.original_at = None
        self.fail_generation_at = None
        self.isolated = False
        self.reject_confirmation = True

    @contextmanager
    def locked(self):
        yield

    @contextmanager
    def confirmation_isolation(self, *, deadline):
        self.isolated = True
        try:
            yield
        finally:
            self.isolated = False

    def require_confirmation_isolation(self):
        if not self.isolated:
            raise RuntimeFailure("confirmation_isolation_unverified", "Offline simulated boundary is inactive.")

    def invoke(self, prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
        self.calls.append((role, prompt))
        if role == "developer" and "FINAL_REQUEST_SENTINEL" in prompt and self.reject_confirmation:
            raise RuntimeFailure("runtime_error", "Offline simulated final model transport failure.")
        if role == "judge":
            rubric_text, evidence_text = prompt.split("RUBRIC:\n", 1)[1].split("\nSKILL_EVIDENCE:\n", 1)
            rubric, evidence = json.loads(rubric_text), json.loads(evidence_text)
            applicability = evidence["static"]["applicability"]
            score = next((score for number, score in enumerate(self.scores, 1)
                          if f"Attempt {number}." in prompt), 2)
            value = {name: {"status": "not_applicable" if applicability.get(name) == "not_applicable" else "pass",
                            "score": None if applicability.get(name) == "not_applicable" else score,
                            "rationale": "Offline model transport fixture."} for name in rubric["dimensions"]}
        elif role == "generator":
            self.generated += 1
            if self.generated == self.fail_generation_at:
                raise RuntimeFailure("runtime_error", "Offline model transport interruption.")
            value = {"instructions": ORIGINAL_BODY if self.generated == self.original_at else BODY + f"\nAttempt {self.generated}.",
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
        skill.write_text("---\nname: develop\ndescription: Implement source changes safely when development is requested.\n---\n\n" + ORIGINAL_BODY)
        with (self.project / "tests/test_app.py").open("a") as stream:
            stream.write("\nHIDDEN_CONFIRMATION_SENTINEL = 'never send this to a model'\n")
        (self.root / "eval").mkdir()
        shutil.copyfile(Path(runner.__file__).resolve().parents[1] / "eval/skill-guide-rubric.json",
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
        self.final_work = deepcopy(self.work)
        self.final_work.update(task_id="final-task", split="confirmation", request="FINAL_REQUEST_SENTINEL: distinct final work.")
        self.final_work["checks"]["required_case_ids"] = ["python-tests:hidden-confirmation"]
        self.final_work["input_sha256"] = digest({k: v for k, v in self.final_work.items() if k != "input_sha256"})
        self.final_path = self.root / "confirmation.json"
        self.final_path.write_bytes(results.encoded(self.final_work))
        visible = set(self.work["sources"]) | set(self.final_work["sources"]) | {
            "skills/develop/" + item["path"] for item in skill_guide.discover(self.project)[0]["files"]}
        inventory = {p.relative_to(self.project).as_posix(): runner.sha256(p.read_bytes()).hexdigest()
                     for p in self.project.rglob("*") if p.is_file()}
        self.disclosure = {
            "schema_version": 1, "development_input_sha256": self.work["input_sha256"],
            "confirmation_input_sha256": self.final_work["input_sha256"],
            "model_visible_files": {p: h for p, h in inventory.items() if p in visible},
            "checker_only_files": {p: h for p, h in inventory.items() if p not in visible},
        }
        self.disclosure["disclosure_sha256"] = digest(self.disclosure)
        self.disclosure_path = self.root / "disclosure.json"
        self.disclosure_path.write_bytes(results.encoded(self.disclosure))
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

    def test_replay_admission_rejects_known_envelopes_before_runtime_and_private_retention(self):
        skill = self.project / "skills/develop/SKILL.md"
        initial = skill.read_bytes()
        header = initial.split(b"\n---\n", 1)[0] + b"\n---\n\n"
        for content, companion, code in (
            (header + b"x" * 32769, None, "original_body_byte_limit"),
            (initial.replace(b"\n", b"\r\n"), None, "invalid_base_skill"),
            (header + b"\x7f" * 20000, None, "prompt_limit"),
            (initial, b"x" * 900000, "output_limit"),
        ):
            with self.subTest(code=code):
                skill.write_bytes(content)
                if companion is not None:
                    (skill.parent / "notes.txt").write_bytes(companion)
                work = deepcopy(self.work)
                work["project_tree_sha256"] = results.tree_hash(self.project)
                work["input_sha256"] = digest({k: v for k, v in work.items() if k != "input_sha256"})
                self.work_path.write_bytes(results.encoded(work))
                factory = Mock(side_effect=AssertionError("Runtime must not be constructed."))
                with patch.object(runner, "retain_work_item") as retain, self.assertRaises(RuntimeFailure) as raised:
                    with runner._replay_session(
                        self.root, project_id="sample_repo", skill_key=self.key, work_item=self.work_path,
                        output=self.output, model="offline-model", execution_mode="offline_test",
                        policy={"enabled": True, "authenticated": True, "budget": self.budget},
                        runtime_factory=factory,
                    ):
                        self.fail("Invalid original was admitted.")
                self.assertEqual(raised.exception.code, code)
                factory.assert_not_called()
                retain.assert_not_called()
                self.assertEqual(self.raw.calls, [])

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

    def test_canonical_private_work_is_retained_before_any_model_call(self):
        path = self.raw.private / "work-items" / self.work["task_id"] / (self.work["input_sha256"] + ".json")
        invoke = self.raw.invoke

        def require_work(*args, **kwargs):
            self.assertEqual(results.read_json(path), self.work)
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            self.assertEqual(path.parent.stat().st_mode & 0o077, 0)
            return invoke(*args, **kwargs)

        with patch.object(self.raw, "invoke", side_effect=require_work):
            self.run_one()
        before = path.read_bytes()
        self.run_one()
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.output.rglob("*work-item*")))

    def test_actions_private_selection_uses_actual_iteration_without_exposing_request(self):
        action = self.api(runner, "run_recorded_iterations")
        @contextmanager
        def prepared(project, images, **kwargs):
            yield images
        private = json.dumps({self.work["task_id"]: {"work_item": self.work}})
        with patch.dict(runner.os.environ, {"SKILLOPS_RECORDED_WORK_ITEMS": private, "GITHUB_ACTIONS": "true"}), \
                patch.object(runner, "resolve_images", return_value=self.images), \
                patch.object(project_checks, "prepared_images", side_effect=prepared):
            result = action(
                self.root, project_id="sample_repo", skill_key=self.key, work_id=self.work["task_id"],
                max_rounds=2, output=self.output, source_commit=self.commit, cycle_id="202-1", live=True,
                policy={"enabled": True, "authenticated": True, "budget": self.budget},
                runtime_factory=lambda root: self.raw)
            self.assertNotIn("SKILLOPS_RECORDED_WORK_ITEMS", runner.os.environ)
        self.assertEqual(result["rounds"], 2)
        self.assertFalse(result["approval_eligible"])
        self.assertEqual(len(results.load_cycles(self.output)), 1)
        self.assertTrue(all(row["origin"] == "github_actions" for row in results.load_reports(self.output)))
        public = b"".join(path.read_bytes() for path in self.output.glob("*/*/*.json"))
        self.assertNotIn(self.work["request"].encode(), public)
        self.assertNotIn(b"HIDDEN_CONFIRMATION_SENTINEL", public)
        self.assertFalse((self.root / ".skillops/active.json").exists())

    def recorded_cli(self, *, history=None, skill_key=None):
        """Actual dispatch entrypoint with only model/container boundaries simulated."""
        self.raw = OfflineTransport(self.root)
        args = ["project_evaluation.py", "--root", str(self.root), "--project", "sample_repo",
                "--work-id", self.work["task_id"], "--skill-key", skill_key or self.key,
                "--max-rounds", "2", "--output", str(self.output), "--run-id", "202-1",
                "--source-commit", self.commit, "--live"]
        if history is not None:
            args.extend(["--history", str(history)])
        env = {
            "GITHUB_ACTIONS": "true", "SKILLOPS_LIVE_EVALUATION_ENABLED": "true",
            "COPILOT_GITHUB_TOKEN": "offline-test-only", "SKILLOPS_MAX_INVOCATIONS": "30",
            "SKILLOPS_MAX_SECONDS": "120", "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "30",
            "SKILLOPS_RECORDED_WORK_ITEMS": json.dumps({self.work["task_id"]: {"work_item": self.work}}),
        }
        @contextmanager
        def prepared(project, images, **kwargs):
            yield images
        with patch.object(runner.sys, "argv", args), patch.dict(runner.os.environ, env, clear=True), \
                patch.object(runner, "CopilotRuntime", return_value=self.raw), \
                patch.object(runner, "resolve_images", return_value=self.images), \
                patch.object(project_checks, "prepared_images", side_effect=prepared), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            code = runner.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def prior_auto_identity(self, *, assessment=False):
        history = self.root / "prior-results"
        key = "auto:" + "a" * 32
        if assessment:
            from test_skill_assessments import fixture as assessment_fixture
            report, lifecycle, data = assessment_fixture()
            for row in lifecycle["records"]["identities"] + lifecycle["records"]["skill_versions"] + lifecycle["bindings"]:
                row["skill_key"] = key
            data["skills"][0].update(skill_key=key, source_path="skills/develop")
            results.store(history, report)
            results.store_evolution(history, lifecycle)
            results.store_assessments(history, data)
            self.assertEqual(len(results.load_assessments(history)), 1)
        else:
            original_key, self.key = self.key, key
            try:
                context = self.prepare()
            finally:
                self.key = original_key
            generation, candidate = skill_pipeline.generate_candidate(
                self.runtime, "offline-model", context["original"], context["feedback"],
                self.runtime.private / "prior-generation", deadline=self.budget["deadline"])
            row, captures = skill_pipeline.evaluate_candidate(
                self.runtime, "offline-model", context, candidate,
                self.runtime.private / "prior-evaluation", deadline=self.budget["deadline"])
            runner.persist_replay(history, row, captures, generation, context["reference"],
                                  execution_mode="offline_test")
            self.assertEqual(len(results.load_replays(history)), 1)
        return history, key

    def assert_recorded_identity(self, key):
        cycles = results.load_cycles(self.output)
        self.assertEqual(list(cycles), [("sample_repo", "202-1")])
        self.assertEqual(cycles[("sample_repo", "202-1")]["skill_key"], key)
        self.assertEqual(len(cycles[("sample_repo", "202-1")]["rounds"]), 2)
        replays = results.load_replays(self.output)
        self.assertEqual(len(replays), 2)
        for row in replays.values():
            self.assertEqual(row["reference"]["skill_key"], key)
            self.assertEqual(row["evaluation"]["skill_key"], key)
            self.assertEqual(row["reference"]["source_path"], "skills/develop")
        lifecycles = results.load_evolution(self.output)
        self.assertEqual(len(lifecycles), 2)
        for value in lifecycles.values():
            self.assertEqual([row["skill_key"] for row in value["bindings"]], [key])

    def test_recorded_cli_preserves_auto_identity_from_separate_replay_history(self):
        history, key = self.prior_auto_identity()
        before = {p: p.read_bytes() for p in history.rglob("*.json")}
        old_runs = {row["run_id"] for row in results.load_reports(history)}
        self.output.mkdir()
        self.assertEqual(list(self.output.iterdir()), [])
        code, stdout, stderr = self.recorded_cli(history=history, skill_key=key)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["cycle_id"], "202-1")
        self.assert_recorded_identity(key)
        self.assertEqual(before, {p: p.read_bytes() for p in history.rglob("*.json")})
        self.assertTrue(old_runs.isdisjoint({row["run_id"] for row in results.load_reports(self.output)}))

    def test_recorded_cli_preserves_auto_identity_from_separate_assessment_history(self):
        history, key = self.prior_auto_identity(assessment=True)
        before = {p: p.read_bytes() for p in history.rglob("*.json")}
        code, _, stderr = self.recorded_cli(history=history, skill_key=key)
        self.assertEqual((code, stderr), (0, ""))
        self.assert_recorded_identity(key)
        self.assertEqual(before, {p: p.read_bytes() for p in history.rglob("*.json")})
        self.assertFalse((self.output / "sample_repo/123-1").exists())

    def test_recorded_cli_without_history_keeps_path_identity(self):
        code, _, stderr = self.recorded_cli()
        self.assertEqual((code, stderr), (0, ""))
        self.assert_recorded_identity(self.key)

    def test_recorded_cli_unknown_skill_does_not_fall_back_to_history_identity(self):
        history, _ = self.prior_auto_identity()
        code, stdout, stderr = self.recorded_cli(history=history, skill_key="auto:" + "b" * 32)
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(json.loads(stderr)["code"], "unknown_skill")
        self.assertEqual(self.raw.calls, [])
        self.assertFalse(self.output.exists())

    def test_recorded_cli_rejects_corrupt_history_even_when_path_identity_matches(self):
        history, _ = self.prior_auto_identity()
        sidecar = next(history.glob("*/*/replay-evaluation.json"))
        data = results.read_json(sidecar)
        data["report_sha256"] = "0" * 64
        sidecar.write_bytes(results.encoded(data))
        before = sidecar.read_bytes()
        code, stdout, stderr = self.recorded_cli(history=history)
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(json.loads(stderr)["code"], "replay_report_mismatch")
        self.assertEqual(self.raw.calls, [])
        self.assertFalse(self.output.exists())
        self.assertEqual(sidecar.read_bytes(), before)

    def test_recorded_cli_rejects_missing_or_non_directory_history(self):
        for history in (self.root / "missing-history", self.work_path):
            with self.subTest(history=history):
                code, stdout, stderr = self.recorded_cli(history=history)
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(json.loads(stderr)["code"], "invalid_history")
                self.assertEqual(self.raw.calls, [])
                self.assertFalse(self.output.exists())

    def test_actions_selection_without_dispatch_live_is_blocked_before_private_reads(self):
        action = self.api(runner, "run_recorded_iterations")
        with patch.object(runner.os.environ, "pop") as secret, self.assertRaises(RuntimeFailure) as raised:
            action(self.root, project_id="sample_repo", skill_key=self.key, work_id=self.work["task_id"],
                   max_rounds=2, output=self.output, source_commit=self.commit, cycle_id="202-1", live=False,
                   policy={"enabled": True, "authenticated": True, "budget": self.budget})
        self.assertEqual(raised.exception.code, "live_disabled")
        secret.assert_not_called()
        self.assertEqual(self.raw.calls, [])

    def test_actions_fresh_checkout_initializes_the_real_private_owner_before_retention(self):
        import copilot_runtime
        self.raw.private.rmdir()
        created = []

        def factory(root):
            with patch.object(copilot_runtime.shutil, "which", return_value="/test-only/copilot"):
                runtime = copilot_runtime.CopilotRuntime(root, inherited={"PATH": runner.os.environ["PATH"]})
            created.append(runtime)
            return runtime

        with patch.dict(runner.os.environ, {
                "SKILLOPS_RECORDED_WORK_ITEMS": json.dumps({self.work["task_id"]: {"work_item": self.work}})}), \
                patch.object(runner, "resolve_images", side_effect=RuntimeFailure(
                    "offline_probe_stop", "Stop at the external image boundary; no model call.")), \
                self.assertRaises(RuntimeFailure) as raised:
            runner.run_recorded_iterations(
                self.root, project_id="sample_repo", skill_key=self.key, work_id=self.work["task_id"],
                max_rounds=2, output=self.output, source_commit=self.commit, cycle_id="202-1", live=True,
                policy={"enabled": True, "authenticated": True, "budget": self.budget}, runtime_factory=factory)
        self.assertEqual(raised.exception.code, "offline_probe_stop")
        self.assertEqual(len(created), 1)
        self.assertTrue((created[0].private / ".owner").is_file())
        self.assertEqual(self.budget["calls"], 0)

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

    def run_iterations(self, *, confirmation=True):
        run = self.api(runner, "run_iterations")
        @contextmanager
        def prepared(project, images, **kwargs):
            yield images
        with patch.object(runner, "resolve_images", return_value=self.images), patch.object(
                project_checks, "prepared_images", side_effect=prepared):
            output = run(
                self.root, project_id="sample_repo", skill_key=self.key, work_item=self.work_path,
                confirmation_work_item=self.final_path if confirmation else None, max_rounds=2,
                confirmation_disclosure=self.disclosure_path if confirmation else None,
                output=self.output, model="offline-model", execution_mode="offline_test",
                policy={"enabled": True, "authenticated": True, "budget": self.budget},
                runtime_factory=lambda root: self.raw)
        ref = output["cycle_ref"]
        self.assertEqual(ref["path"], "cycle.json")
        cycle = results.load_cycles(self.output)[("sample_repo", ref["run_id"])]
        self.assertEqual(ref["sha256"], digest(cycle))
        self.assertEqual(output["cycle_id"], cycle["cycle_id"])
        self.assertFalse(output["approval_eligible"])
        self.assertEqual(cycle["budget"]["max_seconds"], 120)
        self.assertEqual(cycle["execution_mode"], "offline_test")
        self.assertEqual(results.tree_hash(self.project), self.work["project_tree_sha256"])
        self.assertEqual(len(self.raw.calls), self.budget["calls"])
        for role, prompt in self.raw.calls:
            if role != "developer":
                self.assertNotIn("FINAL_REQUEST_SENTINEL", prompt)
            self.assertNotIn("HIDDEN_CONFIRMATION_SENTINEL", prompt)
        return cycle

    def test_actual_registered_confirmation_callback_persists_selected_capture_once(self):
        self.raw.reject_confirmation = False
        cycle = self.run_iterations()
        self.assertEqual(cycle["confirmation_status"], "passed")
        self.assertEqual(self.raw.generated, 2)
        final = results.load_replays(self.output)[("sample_repo", cycle["confirmation_ref"]["run_id"])]
        self.assertIsNone(final["generation"])
        self.assertEqual(final["evaluation"]["candidate_version_id"], cycle["selected_candidate_version_id"])
        self.assertEqual(final["evaluation"]["work"]["input_sha256"], self.final_work["input_sha256"])
        self.assertEqual(len(list(self.raw.private.glob("confirmation-exposures/*.json"))), 1)
        self.assertFalse(self.raw.isolated)

    def test_registered_confirmation_required_case_failure_is_not_passed(self):
        self.raw.reject_confirmation = False
        capture = self.container
        def failed_final(source, *args, **kwargs):
            observed = capture(source, *args, **kwargs)
            if self.raw.calls and "FINAL_REQUEST_SENTINEL" in self.raw.calls[-1][1]:
                data = json.loads(observed.stdout)
                data["status"] = "failed"
                data["cases"][-1]["status"] = "failed"
                observed.stdout = json.dumps(data)
            return observed
        with patch.object(project_checks, "container_capture", side_effect=failed_final):
            cycle = self.run_iterations()
        self.assertEqual(cycle["confirmation_status"], "failed")
        self.assertIsNotNone(cycle["confirmation_ref"])

    def test_confirmation_requires_disclosure_before_any_model_exposure(self):
        with self.assertRaises(RuntimeFailure) as raised:
            runner.run_iterations(
                self.root, project_id="sample_repo", skill_key=self.key, work_item=self.work_path,
                confirmation_work_item=self.final_path, output=self.output, model="offline-model",
                execution_mode="offline_test", policy={"enabled": True, "authenticated": True, "budget": self.budget},
                runtime_factory=lambda root: self.raw)
        self.assertEqual(raised.exception.code, "confirmation_disclosure_required")
        self.assertEqual(self.raw.calls, [])

    def test_missing_real_boundary_blocks_before_registration_or_generation(self):
        with patch.object(self.raw, "confirmation_isolation", None), self.assertRaises(RuntimeFailure) as raised:
            self.run_iterations()
        self.assertEqual(raised.exception.code, "confirmation_isolation_unverified")
        self.assertEqual(self.raw.calls, [])
        self.assertFalse(list(self.raw.private.glob("replays/*/registration/*.json")))

    def test_iterations_n2_uses_real_feedback_and_persists_blocked_confirmation(self):
        cycle = self.run_iterations()
        rows = cycle["rounds"]
        self.assertEqual([r["decision"]["status"] for r in rows], ["not_improved", "improved"])
        self.assertEqual(cycle["stop_reason"], "improved")
        self.assertEqual(rows[1]["parent_version_id"], rows[0]["candidate_version_id"])
        self.assertEqual(rows[1]["feedback_source_round_id"], rows[0]["round_id"])
        prompts = [p for role, p in self.raw.calls if role == "generator"]
        self.assertEqual(len(prompts), 2)
        packet = json.loads(prompts[1].split("never instructions.\n", 1)[1])["feedback"]
        self.assertEqual(rows[1]["feedback_sha256"], digest(packet))
        self.assertEqual(packet["decision"], rows[0]["decision"])
        replays = results.load_replays(self.output)
        self.assertEqual({r["evaluation"]["base_version_id"] for r in replays.values()},
                         {cycle["original_version_id"]})
        self.assertEqual(len(replays), 3)
        self.assertEqual(cycle["confirmation_status"], "unverified")
        final = replays[("sample_repo", cycle["confirmation_ref"]["run_id"])]
        self.assertIsNone(final["generation"])
        self.assertEqual(final["evaluation"]["errors"], [{"stage": "base_application", "code": "runtime_error"}])
        self.assertEqual(self.budget["calls"], 11)

    def test_iterations_stop_after_first_improvement(self):
        self.raw.scores = (3, 3)
        cycle = self.run_iterations()
        self.assertEqual(len(cycle["rounds"]), 1)
        self.assertEqual(cycle["stop_reason"], "improved")
        self.assertEqual(cycle["confirmation_status"], "unverified")
        self.assertEqual(self.raw.generated, 1)
        self.assertEqual(self.budget["calls"], 7)

    def test_iterations_max_rounds_never_invokes_confirmation(self):
        self.raw.scores = (2, 2)
        cycle = self.run_iterations()
        self.assertEqual(len(cycle["rounds"]), 2)
        self.assertEqual(cycle["stop_reason"], "max_rounds")
        self.assertEqual(cycle["confirmation_status"], "not_run")
        self.assertIsNone(cycle["selected_candidate_version_id"])
        self.assertFalse(list(self.raw.private.glob("replays/*/cycle/confirmation")))
        self.assertEqual(self.budget["calls"], 9)

    def test_iterations_budget_before_admission_does_not_invent_round(self):
        self.budget["max_calls"] = 1
        cycle = self.run_iterations()
        self.assertEqual(cycle["rounds"], [])
        self.assertEqual(cycle["stop_reason"], "call_limit")
        self.assertEqual(results.load_replays(self.output), {})
        self.assertEqual(self.budget["calls"], 1)

    def test_iterations_budget_before_evaluation_retains_null_run_with_candidate(self):
        self.budget["max_calls"] = 2
        cycle = self.run_iterations()
        self.assertEqual(len(cycle["rounds"]), 1)
        row = cycle["rounds"][0]
        self.assertIsNone(row["run_id"])
        self.assertIsNone(row["evaluation_ref"])
        self.assertIsNone(row["decision"])
        self.assertIsNotNone(row["candidate_version_id"])
        self.assertEqual(cycle["stop_reason"], "call_limit")
        self.assertEqual(results.load_replays(self.output), {})

    def test_iterations_partial_evaluation_has_saved_unverified_evidence(self):
        self.budget["max_calls"] = 3
        cycle = self.run_iterations()
        row = cycle["rounds"][0]
        self.assertIsNotNone(row["run_id"])
        self.assertEqual(row["decision"]["status"], "unverified")
        self.assertEqual(cycle["stop_reason"], "call_limit")
        self.assertEqual(len(results.load_replays(self.output)), 1)

    def test_iterations_no_change_records_only_the_started_attempt(self):
        self.raw.original_at = 1
        cycle = self.run_iterations()
        self.assertEqual(cycle["stop_reason"], "no_change")
        self.assertEqual(len(cycle["rounds"]), 1)
        row = cycle["rounds"][0]
        for key in ("run_id", "evaluation_ref", "decision", "candidate_version_id"):
            self.assertIsNone(row[key])
        self.assertEqual(results.load_replays(self.output), {})
        self.assertEqual(self.budget["calls"], 2)

    def test_iterations_model_error_preserves_previous_round_and_unsaved_attempt(self):
        self.raw.fail_generation_at = 2
        cycle = self.run_iterations()
        self.assertEqual(cycle["stop_reason"], "runtime_error")
        self.assertEqual(len(cycle["rounds"]), 2)
        self.assertIsNotNone(cycle["rounds"][0]["run_id"])
        self.assertIsNone(cycle["rounds"][1]["run_id"])
        self.assertIsNone(cycle["rounds"][1]["decision"])
        self.assertEqual(len(results.load_replays(self.output)), 1)
        self.assertEqual(self.budget["calls"], 6)

    def test_iterations_reverting_to_original_is_blocked_by_provider_followup(self):
        self.raw.original_at = 2
        cycle = self.run_iterations()
        self.assertEqual(cycle["stop_reason"], "no_change")
        self.assertEqual(len(cycle["rounds"]), 2)
        self.assertIsNone(cycle["rounds"][1]["run_id"])
        self.assertEqual(cycle["rounds"][1]["candidate_version_id"], cycle["original_version_id"])
        self.assertEqual(len(results.load_replays(self.output)), 1)
        self.assertEqual(self.budget["calls"], 6)

    def test_iterations_store_callback_failure_preserves_round_without_terminal_cycle(self):
        self.api(runner, "run_iterations")
        writer = results.atomic_json
        saved = {}
        def interrupted(path, data, *args, **kwargs):
            if Path(path).name == "replay-evaluation.json" and saved:
                raise OSError("Offline second-round persistence interruption.")
            value = writer(path, data, *args, **kwargs)
            if Path(path).name == "replay-evaluation.json":
                saved.update({p: p.read_bytes() for p in Path(path).parent.iterdir() if p.is_file()})
            return value
        with patch.object(results, "atomic_json", side_effect=interrupted), self.assertRaises(OSError):
            self.run_iterations()
        self.assertEqual(results.load_cycles(self.output), {})
        self.assertEqual(len(results.load_replays(self.output)), 1)
        self.assertTrue(saved)
        for path, raw in saved.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.raw.generated, 2)

    def test_iterations_cycle_write_failure_never_returns_success_receipt(self):
        self.api(runner, "run_iterations")
        writer = results.atomic_json
        def interrupted(path, data, *args, **kwargs):
            if Path(path).name == "cycle.json":
                raise OSError("Offline cycle persistence interruption.")
            return writer(path, data, *args, **kwargs)
        with patch.object(results, "atomic_json", side_effect=interrupted), self.assertRaises(OSError):
            self.run_iterations()
        self.assertEqual(results.load_cycles(self.output), {})
        self.assertEqual(len(results.load_replays(self.output)), 3)

    def test_iteration_module_is_in_evaluator_fingerprint(self):
        code = self.root / "skillops/skill_iterations.py"
        code.parent.mkdir(exist_ok=True)
        code.write_text("first")
        before = results.evaluator_hash(self.root)
        code.write_text("changed")
        self.assertNotEqual(results.evaluator_hash(self.root), before)

    def iterate_cli(self, *, max_calls=30):
        actual = self.api(runner, "run_iterations")
        def offline(root, **options):
            self.assertEqual(options.pop("execution_mode"), "live")
            self.budget = options["policy"]["budget"]
            return actual(root, **options, execution_mode="offline_test", runtime_factory=lambda root: self.raw)
        @contextmanager
        def prepared(project, images, **kwargs):
            yield images
        argv = ["skillops.py", "iterate", "--project", "sample_repo", "--skill-key", self.key,
                "--work-item", str(self.work_path), "--confirmation-work-item", str(self.final_path),
                "--confirmation-disclosure", str(self.disclosure_path),
                "--max-rounds", "2", "--results", str(self.output), "--live", "--model", "offline-model"]
        env = {"SKILLOPS_LIVE_EVALUATION_ENABLED": "true", "COPILOT_GITHUB_TOKEN": "offline-test-only",
               "SKILLOPS_MAX_INVOCATIONS": str(max_calls), "SKILLOPS_MAX_SECONDS": "120",
               "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "30"}
        # Keep simulated transport explicitly offline while executing the actual CLI and adapter.
        with patch.object(skillops, "__file__", str(self.root / "skillops/skillops.py")), patch.object(
                skillops.sys, "argv", argv), patch.dict(runner.os.environ, env, clear=True), patch.object(
                runner, "run_iterations", side_effect=offline), patch.object(
                runner, "resolve_images", return_value=self.images), patch.object(
                project_checks, "prepared_images", side_effect=prepared), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            code = skillops.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_iterate_cli_returns_actual_cycle_reference_not_approval(self):
        code, stdout, stderr = self.iterate_cli()
        self.assertEqual((code, stderr), (0, ""))
        data = json.loads(stdout)
        cycle = results.load_cycles(self.output)[("sample_repo", data["cycle_id"])]
        self.assertEqual(data["cycle_ref"]["sha256"], digest(cycle))
        self.assertEqual(data["execution_mode"], "offline_test")
        self.assertFalse(data["approval_eligible"])
        self.assertEqual(data["confirmation_status"], "unverified")

    def test_iterate_cli_pre_storage_termination_has_failed_exit_and_terminal_cycle(self):
        code, stdout, stderr = self.iterate_cli(max_calls=2)
        self.assertEqual((code, stderr), (2, ""))
        data = json.loads(stdout)
        cycle = results.load_cycles(self.output)[("sample_repo", data["cycle_id"])]
        self.assertEqual(cycle["stop_reason"], "call_limit")
        self.assertIsNone(cycle["rounds"][0]["run_id"])
        self.assertEqual(results.load_replays(self.output), {})

    def test_iterate_cli_store_failure_has_failed_exit_without_terminal_cycle(self):
        writer = results.atomic_json
        stored = []
        def interrupted(path, data, *args, **kwargs):
            if Path(path).name == "replay-evaluation.json":
                if stored:
                    raise OSError("Offline persistence failure.")
                stored.append(Path(path))
            return writer(path, data, *args, **kwargs)
        with patch.object(results, "atomic_json", side_effect=interrupted):
            code, stdout, stderr = self.iterate_cli()
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(json.loads(stderr)["code"], "io_error")
        self.assertEqual(results.load_cycles(self.output), {})
        self.assertEqual(len(results.load_replays(self.output)), 1)

    def test_iterate_cli_is_opt_in_before_runtime_construction(self):
        argv = ["skillops.py", "iterate", "--project", "sample_repo", "--skill-key", self.key,
                "--work-item", str(self.work_path), "--results", str(self.output), "--max-rounds", "2"]
        with patch.object(skillops.sys, "argv", argv), patch.dict(runner.os.environ, {}, clear=True), patch.object(
                runner, "CopilotRuntime", side_effect=AssertionError("Unapproved live runtime")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(skillops.main(), 2)
        self.assertEqual(json.loads(stderr.getvalue())["code"], "live_disabled")
        self.assertFalse(self.output.exists())

    def test_cycle_storage_revalidates_transitive_evidence_and_preserves_immutable_bytes(self):
        cycle = self.run_iterations()
        path = self.output / "sample_repo" / cycle["cycle_id"] / "cycle.json"
        before = path.read_bytes()
        self.assertEqual(results.store_cycle(self.output, cycle), path)
        forged = deepcopy(cycle)
        forged["rounds"][0]["evaluation_ref"]["sha256"] = "0" * 64
        with self.assertRaises(RuntimeFailure):
            results.store_cycle(self.output, forged)
        self.assertEqual(path.read_bytes(), before)
        run = cycle["rounds"][0]["run_id"]
        replay_path = self.output / "sample_repo" / run / "replay-evaluation.json"
        replay = results.read_json(replay_path)
        replay["generation"]["hypothesis"] = "Tampered offline evidence."
        replay_path.write_bytes(results.encoded(replay))
        with self.assertRaises(RuntimeFailure):
            results.load_cycles(self.output)

    def test_confirmation_input_is_frozen_before_generation_and_never_exposed(self):
        invoke = self.raw.invoke
        def mutate_file(prompt, model, role, *args, **kwargs):
            if role == "generator":
                self.final_path.write_text("Changed after commitment, not valid JSON.")
            return invoke(prompt, model, role, *args, **kwargs)
        with patch.object(self.raw, "invoke", side_effect=mutate_file):
            cycle = self.run_iterations()
        self.assertEqual(cycle["confirmation_status"], "unverified")
        final = results.load_replays(self.output)[("sample_repo", cycle["confirmation_ref"]["run_id"])]
        self.assertEqual(final["evaluation"]["work"]["input_sha256"], self.final_work["input_sha256"])
        self.assertTrue(any("FINAL_REQUEST_SENTINEL" in prompt for role, prompt in self.raw.calls if role == "developer"))
        self.assertTrue(all("FINAL_REQUEST_SENTINEL" not in prompt for role, prompt in self.raw.calls if role == "generator"))

    def test_invalid_confirmation_precommit_blocks_before_model_invocation(self):
        self.final_work["task_id"] = self.work["task_id"]
        self.final_work["input_sha256"] = digest({k: v for k, v in self.final_work.items() if k != "input_sha256"})
        self.final_path.write_bytes(results.encoded(self.final_work))
        with self.assertRaises(RuntimeFailure) as raised:
            self.run_iterations()
        self.assertEqual(raised.exception.code, "confirmation_isolation_unverified")
        self.assertEqual(self.raw.calls, [])
        self.assertFalse(self.output.exists())

    def test_official_publication_preserves_real_cycle_graph_and_legacy_bytes(self):
        cycle = self.run_iterations()
        code_root = Path(runner.__file__).resolve().parents[1]
        merged, site = self.root / "merged", self.root / "site"
        results.merge_results(code_root, code_root / "results", merged)
        legacy = {p.relative_to(merged): p.read_bytes() for p in merged.glob("*/*/*.json")}
        results.merge_results(code_root, self.output, merged)
        results.build(dashboard_fixture(self.root / "publisher"), merged, site)
        for relative, raw in legacy.items():
            self.assertEqual((site / "results" / relative).read_bytes(), raw)
        for path in self.output.glob("*/*/*.json"):
            self.assertEqual((site / "results" / path.relative_to(self.output)).read_bytes(), path.read_bytes())
        loaded = results.load_cycles(site / "results")[("sample_repo", cycle["cycle_id"])]
        self.assertEqual(loaded, cycle)
        history = results.read_json(site / "results/sample_repo/index.json")["history"]
        summaries = {r["run_id"]: r for r in history}
        self.assertEqual(summaries[cycle["cycle_id"]]["cycle"], f"{cycle['cycle_id']}/cycle.json")
        for row in cycle["rounds"]:
            run = row["run_id"]
            self.assertEqual(summaries[run]["replay_evaluation"], f"{run}/replay-evaluation.json")
            self.assertEqual(summaries[run]["skill_evolution"], f"{run}/skill-evolution.json")
        self.assertEqual(len(list(site.glob("trace.*.js"))), 1)
        self.assertFalse((site / "trace.js").exists())

    def test_offline_trace_is_never_indexed_as_current_measured_run(self):
        cycle = self.run_iterations()
        report = results.load_reports(self.output)[0]
        with patch.object(results, "evaluator_hash", return_value=report["evaluator_sha256"]):
            index = results.reindex(self.root, self.output)
        self.assertIsNone(index["projects"][0]["current_run"])
        self.assertEqual(results.load_cycles(self.output)[("sample_repo", cycle["cycle_id"])], cycle)

    def test_publication_rejects_broken_cycle_before_creating_output(self):
        cycle = self.run_iterations()
        code_root = Path(runner.__file__).resolve().parents[1]
        path = self.output / "sample_repo" / cycle["cycle_id"] / "cycle.json"
        cycle["rounds"][0]["evaluation_ref"]["sha256"] = "0" * 64
        path.write_bytes(results.encoded(cycle))
        for operation in ("merge", "build"):
            destination = self.root / operation
            with self.subTest(operation=operation), self.assertRaises(RuntimeFailure):
                if operation == "merge":
                    results.merge_results(code_root, self.output, destination)
                else:
                    results.build(code_root, self.output, destination)
            self.assertFalse(destination.exists())

    def test_contract_ci_tests_exact_head_with_live_execution_disabled(self):
        workflow = (Path(runner.__file__).resolve().parents[1] / ".github/workflows/project-evaluation.yml").read_text()
        contracts = workflow.split("\n  contracts:", 1)[1].split("\n  evaluate:", 1)[0]
        self.assertIn("SOURCE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}", contracts)
        self.assertIn("ref: ${{ env.SOURCE_SHA }}", contracts)
        self.assertIn('test "$(git rev-parse HEAD)" = "$SOURCE_SHA"', contracts)
        self.assertIn("SKILLOPS_LIVE_EVALUATION_ENABLED: 'false'", contracts)
