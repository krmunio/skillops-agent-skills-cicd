"""Independent validation using the real optimizer/providers and offline transport only."""

from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from copilot_runtime import RuntimeFailure
import evaluation_datasets as datasets
import project_checks
import project_evaluation as runner
import project_results as results
import skill_pipeline
import test_hackathon_integration as integration


@unittest.skipUnless(importlib.util.find_spec("gepa"), "Install requirements-gepa.txt")
class IndependentValidationTests(unittest.TestCase):
    def setUp(self):
        self.f = integration.ReplayIntegrationTests(
            "test_actual_provider_common_validators_and_shared_persistence_callback")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        for name in ("validation", "confirmation"):
            (f.project / (name + ".py")).write_text("value = 0\n")
        cases = ("development", "development_second", "stable", "validation_first", "validation_second", "confirmation")
        (f.project / "tests/test_app.py").write_text(
            "import unittest\nclass TestApp(unittest.TestCase):\n" +
            "".join(f"    def test_{name}(self): self.assertTrue(True)\n" for name in cases) +
            "\nVALIDATION_ANSWER_SENTINEL = 'checker only'\n")
        for args in (("add", "projects"), ("-c", "user.name=Offline fixture",
                     "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Independent inputs")):
            subprocess.run(["git", "-C", str(f.root), *args], check=True, capture_output=True)
        recipe = {"schema_version": 1, "dataset_id": "independent-v1", "project_id": "sample_repo",
                  "origin": "synthetic", "tasks": []}
        for split, source, names, request in (
            ("development", "app.py", cases[:3], "DEVELOPMENT_REQUEST_SENTINEL"),
            ("validation", "validation.py", cases[3:5], "VALIDATION_REQUEST_SENTINEL"),
            ("confirmation", "confirmation.py", cases[5:], "FINAL_REQUEST_SENTINEL"),
        ):
            recipe["tasks"].append({
                "task_id": split + "-task", "family": split, "split": split, "request": request,
                "sources": [source], "required_case_ids": [
                    "python-tests:test_app.TestApp.test_" + name for name in names],
            })
        spec = f.root / "recipe.json"
        spec.write_bytes(results.encoded(recipe))
        self.dataset_path = f.raw.private / "datasets/input.json"
        # The fixture transport deliberately does not establish the real private-profile marker.
        (f.raw.private / ".owner").write_text("skillops-profile-v1\n")
        self.dataset = datasets.prepare(f.root, spec, self.dataset_path)
        f.work = datasets._work_item(self.dataset, self.dataset["tasks"][0])
        f.work_path.write_bytes(results.encoded(f.work))
        self.validation = datasets._work_item(self.dataset, self.dataset["tasks"][1])
        f.final_work = datasets._work_item(self.dataset, self.dataset["tasks"][2])
        f.final_path.write_bytes(results.encoded(f.final_work))
        f.disclosure.update(development_input_sha256=f.work["input_sha256"],
                            confirmation_input_sha256=f.final_work["input_sha256"])
        visible = set(f.work["sources"]) | set(self.validation["sources"]) | set(f.final_work["sources"]) | {
            "skills/develop/SKILL.md", "skills/develop/notes.txt"}
        inventory = {p.relative_to(f.project).as_posix(): runner.sha256(p.read_bytes()).hexdigest()
                     for p in f.project.rglob("*") if p.is_file()}
        f.disclosure.update(model_visible_files={p: h for p, h in inventory.items() if p in visible},
                            checker_only_files={p: h for p, h in inventory.items() if p not in visible})
        f.disclosure["disclosure_sha256"] = datasets._hash({
            k: v for k, v in f.disclosure.items() if k != "disclosure_sha256"})
        f.disclosure_path.write_bytes(results.encoded(f.disclosure))
        f.raw.scores, f.raw.reject_confirmation = (3, 4), False
        self.divergent_development = False
        self.applications = []
        invoke = f.raw.invoke

        def transport(prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
            self.assertTrue(f.raw.isolated, "Every model invocation must use the verified boundary")
            self.assertEqual(kwargs["max_ai_credits"], 30)
            self.assertGreater(kwargs["timeout"], 0)
            result = invoke(prompt, model, role, workdir, artifact, expected_skill, **kwargs)
            if role == "developer":
                body = Path(expected_skill).read_text()
                number = 2 if "Attempt 2." in body else 1 if "Attempt 1." in body else 0
                if "VALIDATION_REQUEST_SENTINEL" in prompt:
                    source, value = "validation.py", number
                elif "FINAL_REQUEST_SENTINEL" in prompt:
                    source, value = "confirmation.py", 1
                else:
                    source = "app.py"
                    value = (number if self.divergent_development else 1) if number else -1
                self.applications.append((source, number, prompt))
                result["content"] = json.dumps({"files": {source: f"value = {value}\n"}})
            return result

        f.raw.invoke = transport
        self.enterContext(patch.object(project_checks, "container_capture", side_effect=self.container))
        self.enterContext(patch.object(runner, "resolve_images", return_value=f.images))
        self.enterContext(patch.object(project_checks, "prepared_images",
                                      side_effect=lambda *a, **kw: nullcontext(f.images)))

    def container(self, source, controls, image, argv, **kwargs):
        source = Path(source)
        dev = (source / "app.py").read_text()
        val = (source / "validation.py").read_text()
        final = (source / "confirmation.py").read_text()
        # Every arm starts at the original source, never a development patch.
        if val != "value = 0\n":
            self.assertEqual(dev, "value = 0\n")
            self.assertEqual(final, "value = 0\n")
        elif dev != "value = 0\n":
            self.assertEqual(final, "value = 0\n")
        values = {
            "development": dev in ("value = 1\n", "value = 2\n"),
            "development_second": dev == "value = 2\n" if self.divergent_development else True,
            "stable": True,
            "validation_first": val == "value = 1\n",
            "validation_second": True,
            "confirmation": final == "value = 1\n",
        }
        return subprocess.CompletedProcess([], 0, json.dumps({
            "status": "completed" if all(values.values()) else "failed",
            "cases": [{"id": "test_app.TestApp.test_" + name, "status": "passed" if passed else "failed"}
                      for name, passed in values.items()],
        }), "")

    def run_search(self, confirmation=False, **overrides):
        f = self.f
        options = dict(project_id="sample_repo", skill_key=f.key, work_item=f.work_path,
                       output=f.output, model="offline-model", execution_mode="offline_test",
                       policy={"enabled": True, "authenticated": True, "budget": f.budget},
                       max_rounds=2, optimizer="gepa", runtime_factory=lambda root: f.raw,
                       validation_dataset=self.dataset_path, validation_task="validation-task",
                       confirmation_work_item=f.final_path if confirmation else None,
                       confirmation_disclosure=f.disclosure_path if confirmation else None)
        options.update(overrides)
        return runner.run_iterations(f.root, **options)

    def cycle(self):
        return next(iter(results.load_cycles(self.f.output).values()))

    def test_real_validation_replays_select_first_candidate_without_approval(self):
        receipt = self.run_search()
        cycle = self.cycle()
        self.assertEqual(cycle["schema_version"], 3)
        self.assertEqual(receipt["status"], "search_complete")
        self.assertEqual(cycle["optimizer"]["validation_scope"], "independent")
        self.assertEqual(cycle["optimizer"]["scores"], [[0, 1], [1, 1], [0, 1]])
        self.assertEqual(receipt["selected_candidate_version_id"], cycle["rounds"][0]["candidate_version_id"])
        self.assertFalse(receipt["approval_eligible"])
        self.assertEqual(receipt["confirmation_status"], "not_run")
        self.assertEqual([n for path, n, _ in self.applications if path == "validation.py"], [0, 1, 2])
        self.assertEqual(receipt["calls"], 13)
        evidence = cycle["optimizer"]["validation"]
        self.assertEqual(evidence["work"]["split"], "validation")
        self.assertEqual(evidence["dataset_sha256"], self.dataset["dataset_sha256"])
        for item in evidence["evaluations"]:
            self.assertEqual(item["application"]["work_sha256"], self.validation["input_sha256"])
            self.assertEqual(item["application"]["measurement"], {"cost_nano_aiu": 10, "elapsed_seconds": 1})
        retained = self.f.raw.private / "work-items/validation-task" / (self.validation["input_sha256"] + ".json")
        self.assertEqual(results.read_json(retained), self.validation)

    def test_validation_recommendation_beats_higher_development_score_but_not_qualification(self):
        self.divergent_development = True
        receipt = self.run_search()
        cycle = self.cycle()
        rows = [results.read_json(self.f.output / "sample_repo" / row["run_id"] / "replay-evaluation.json")[
            "evaluation"] for row in cycle["rounds"]]
        import gepa_search
        self.assertEqual([gepa_search.case_scores(row["checks"]["candidate"], self.f.work) for row in rows],
                         [[1, 0, 1], [1, 1, 1]])
        self.assertEqual(cycle["optimizer"]["recommended_version_id"], rows[0]["candidate_version_id"])
        self.assertEqual(cycle["optimizer"]["scores"], [[0, 1], [1, 1], [0, 1]])
        self.assertIsNone(receipt["selected_candidate_version_id"], "Validation cannot override replay qualification")

    def test_validation_never_enters_generation_reflection_or_other_developer_requests(self):
        self.run_search()
        for role, prompt in self.f.raw.calls:
            self.assertNotIn("VALIDATION_ANSWER_SENTINEL", prompt)
            if role != "developer" or "VALIDATION_REQUEST_SENTINEL" not in prompt:
                for sentinel in ("VALIDATION_REQUEST_SENTINEL", "validation_first", "validation_second",
                                 "validation.py", "FINAL_REQUEST_SENTINEL"):
                    self.assertNotIn(sentinel, prompt)
        for path in self.f.raw.private.rglob("feedback.json"):
            self.assertNotIn("validation", path.read_text())
        with self.assertRaises(RuntimeFailure):
            skill_pipeline.development_feedback(
                self.f.runtime, {"work_item": self.validation}, None, source_round_id=None)

    def test_separate_confirmation_remains_required_and_works(self):
        receipt = self.run_search(confirmation=True)
        self.assertEqual(receipt["confirmation_status"], "passed")
        self.assertFalse(receipt["approval_eligible"])
        cycle = self.cycle()
        self.assertIsNotNone(cycle["confirmation_ref"])
        self.assertEqual([n for path, n, _ in self.applications if path == "confirmation.py"], [0, 1])
        self.assertFalse(any("FINAL_REQUEST_SENTINEL" in prompt for role, prompt in self.f.raw.calls
                             if role != "developer"))

    def test_invalid_explicit_inputs_fail_before_runtime(self):
        for options in (
            {"validation_task": None}, {"validation_dataset": None}, {"optimizer": "sequential"},
            {"validation_task": "confirmation-task"}, {"validation_task": "development-task"},
            {"validation_task": "missing"}, {"validation_dataset": self.f.root / "missing.json"},
        ):
            with self.subTest(options=options):
                factory = Mock(side_effect=AssertionError("Invalid inputs must fail before runtime"))
                with self.assertRaises(RuntimeFailure):
                    self.run_search(runtime_factory=factory, **options)
                factory.assert_not_called()
        self.assertEqual(self.f.raw.calls, [])

    def test_foreign_drifted_and_overlapping_datasets_are_rejected(self):
        mutations = [
            lambda data: data.update(project_id="other"),
            lambda data: data.update(source_commit="0" * 40),
            lambda data: data["tasks"][1].update(family="development"),
            lambda data: data["tasks"][1].update(sources=data["tasks"][0]["sources"]),
            lambda data: data["tasks"][1]["checks"].update(
                required_case_ids=data["tasks"][0]["checks"]["required_case_ids"]),
            lambda data: data["tasks"][1].update(request="  development_request_sentinel "),
        ]
        for mutate in mutations:
            data = deepcopy(self.dataset)
            mutate(data)
            data["dataset_sha256"] = datasets._hash({k: v for k, v in data.items() if k != "dataset_sha256"})
            self.dataset_path.write_bytes(results.encoded(data))
            with self.subTest(mutation=mutations.index(mutate)), self.assertRaises(RuntimeFailure):
                self.run_search()
        self.assertEqual(self.f.raw.calls, [])

    def test_unavailable_isolation_never_falls_back(self):
        self.f.raw.confirmation_isolation = None
        with self.assertRaises(RuntimeFailure) as raised:
            self.run_search()
        self.assertEqual(raised.exception.code, "confirmation_isolation_unverified")
        self.assertEqual(self.f.raw.calls, [])

    def test_confirmation_disclosure_must_include_validation_source_exposure(self):
        disclosure = self.f.disclosure
        disclosure["checker_only_files"]["validation.py"] = disclosure["model_visible_files"].pop("validation.py")
        disclosure["disclosure_sha256"] = datasets._hash({
            k: v for k, v in disclosure.items() if k != "disclosure_sha256"})
        self.f.disclosure_path.write_bytes(results.encoded(disclosure))
        with self.assertRaises(RuntimeFailure):
            self.run_search(confirmation=True)
        self.assertEqual(self.f.raw.calls, [])

    def test_validation_request_embedded_in_development_is_rejected_before_models(self):
        self.dataset["tasks"][0]["request"] += " Also expose VALIDATION_REQUEST_SENTINEL."
        self.dataset["dataset_sha256"] = datasets._hash({
            k: v for k, v in self.dataset.items() if k != "dataset_sha256"})
        self.dataset_path.write_bytes(results.encoded(self.dataset))
        self.f.work = datasets._work_item(self.dataset, self.dataset["tasks"][0])
        self.f.work_path.write_bytes(results.encoded(self.f.work))
        with self.assertRaises(RuntimeFailure) as raised:
            self.run_search()
        self.assertEqual(raised.exception.code, "validation_disclosure")
        self.assertEqual(self.f.raw.calls, [])

    def test_validation_trace_requests_never_produce_reflection_trajectories(self):
        import gepa_search
        evaluate = gepa_search.SearchAdapter.evaluate
        observed = []
        def capture(adapter, batch, candidate, capture_traces=False):
            result = evaluate(adapter, batch, candidate, capture_traces)
            if set(batch) <= set(self.validation["checks"]["required_case_ids"]):
                observed.append(capture_traces)
                self.assertIsNone(result.trajectories)
            return result
        with patch.object(gepa_search.SearchAdapter, "evaluate", capture):
            self.run_search()
        self.assertIn(True, observed, "Exercise GEPA's validation batch trace request")

    def test_unknown_validation_case_is_retained_but_never_selected(self):
        container = self.container
        def missing(*args, **kwargs):
            response = container(*args, **kwargs)
            value = json.loads(response.stdout)
            # Keep preparation complete, then simulate missing coverage only on validation applications.
            if self.applications and self.applications[-1][0] == "validation.py":
                value["cases"] = [row for row in value["cases"] if not row["id"].endswith("validation_first")]
            response.stdout = json.dumps(value)
            return response
        with patch.object(project_checks, "container_capture", side_effect=missing):
            receipt = self.run_search()
        self.assertEqual(receipt["status"], "evaluation_unverified")
        self.assertIsNone(receipt["selected_candidate_version_id"])

    def test_validation_calls_share_budget_and_failure_cannot_select(self):
        self.f.budget["max_calls"] = 7
        receipt = self.run_search()
        self.assertEqual(receipt["status"], "call_limit")
        self.assertEqual(receipt["calls"], 7)
        self.assertIsNone(receipt["selected_candidate_version_id"])
        self.assertFalse(receipt["approval_eligible"])
        self.assertEqual(len(self.cycle()["optimizer"]["validation"]["evaluations"]), 1)

    def test_validation_credit_limit_stops_without_fallback_or_extra_proposals(self):
        invoke = self.f.raw.invoke
        def limited(prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
            if role == "developer" and "VALIDATION_REQUEST_SENTINEL" in prompt \
                    and "Attempt 1." in Path(expected_skill).read_text():
                raise RuntimeFailure("credit_limit", "Offline simulated per-session credit cap.")
            return invoke(prompt, model, role, workdir, artifact, expected_skill, **kwargs)
        self.f.raw.invoke = limited
        receipt = self.run_search()
        self.assertEqual(receipt["status"], "credit_limit")
        self.assertEqual(receipt["calls"], 8)
        self.assertEqual(self.f.raw.generated, 1)
        self.assertIsNone(receipt["selected_candidate_version_id"])
        self.assertEqual(len(self.cycle()["optimizer"]["validation"]["evaluations"]), 1)

    def test_rehashed_result_tampering_cannot_change_validation_or_confirmation(self):
        self.run_search()
        cycle = self.cycle()
        report = results.read_json(self.f.output / "sample_repo" / cycle["run_id"] / "report.json")
        evidence = results.load_replay_evidence(self.f.output)
        for mutate in (
            lambda row: row["optimizer"]["scores"][1].__setitem__(0, 0),
            lambda row: row["optimizer"]["validation"]["work"].update(split="confirmation"),
            lambda row: row["optimizer"]["validation"]["work"].update(source_commit="0" * 40),
            lambda row: row["optimizer"]["validation"]["work"].update(
                checks=row["optimizer"]["work_checks"]),
            lambda row: row["optimizer"]["validation"]["evaluations"][1]["application"].update(
                work_sha256=row["input_sha256"]),
            lambda row: row["optimizer"]["validation"]["evaluations"][1]["application"].update(
                version_id=row["original_version_id"]),
            lambda row: row.update(confirmation_status="passed"),
        ):
            tampered = deepcopy(cycle)
            mutate(tampered)
            with self.subTest(mutation=mutate), self.assertRaises(RuntimeFailure):
                results.validate_cycle(tampered, report=report, evaluations=evidence)


if __name__ == "__main__":
    unittest.main()
