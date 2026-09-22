"""Real GEPA search with offline SkillOps providers; not measured model improvement."""

from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from copilot_runtime import RuntimeFailure
import project_results
import skill_pipeline
import test_skill_iterations as fixtures
import test_hackathon_integration as integration
import project_evaluation


@unittest.skipUnless(importlib.util.find_spec("gepa"), "Install requirements-gepa.txt for real optimizer tests")
class GEPASearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IterationTests("test_previous_measured_feedback_changes_parent_not_original_comparison")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.work["checks"]["required_case_ids"] = ["repair", "other"]
        f.candidates.append(fixtures.capture(b"Candidate three."))
        f.statuses = ["rejected", "rejected", "improved"]
        self.scores = [[1, 0], [0, 1], [1, 1]]
        self.seed_application = {
            "version_id": f.original[0]["version_id"], "staged_version_id": f.original[0]["version_id"],
            "work_sha256": f.work["input_sha256"], "output_sha256": "c" * 64,
            "activated": True, "changed": True, "task_outcome": "not_satisfied",
            "measurement": {"cost_nano_aiu": 10, "elapsed_seconds": 1},
        }
        self.seed_checks = self.checks([0, 0])
        self.seed = f.stack.enter_context(patch.object(
            skill_pipeline, "_replay_application", return_value=(self.seed_application, self.seed_checks)))
        original_evaluate = f.evaluate

        def evaluate(runtime, model, context, candidate, artifact, **kwargs):
            row, captures = original_evaluate(runtime, model, context, candidate, artifact, **kwargs)
            if context is f.context:
                index = next(i for i, item in enumerate(f.candidates) if item == candidate)
                row["checks"]["base"] = deepcopy(self.seed_checks)
                row["checks"]["candidate"] = self.checks(self.scores[index])
                row["applications"]["candidate"]["task_outcome"] = (
                    "satisfied" if all(self.scores[index]) else "not_satisfied")
            return row, captures
        f.evaluate_mock.side_effect = evaluate

    def checks(self, scores):
        value = self.fixture.observation("passed" if all(scores) else "failed")
        value["cases"] = [{"id": case, "status": "passed" if score else "failed"}
                          for case, score in zip(("repair", "other"), scores)]
        return value

    def run_search(self, **overrides):
        self.assertIsNotNone(importlib.util.find_spec("gepa_search"), "GEPA integration is not implemented")
        import gepa_search
        f = self.fixture
        arguments = dict(cycle_id=f.cycle_id, max_rounds=3, budget=f.budget,
                         confirmation_context=None, persist_round=f.persist)
        arguments.update(overrides)
        return gepa_search.run_cycle(f.runtime, "test-only-model", f.context, f.artifact, **arguments)

    def test_real_optimizer_preserves_complementary_pool_and_selects_best(self):
        result = self.run_search()
        f = self.fixture
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["stop_reason"], "search_complete")
        self.assertEqual(result["selected_candidate_version_id"], f.candidates[2][0]["version_id"])
        optimizer = result["optimizer"]
        self.assertEqual(optimizer["name"], "gepa")
        self.assertEqual(optimizer["version"], "0.1.4")
        self.assertEqual(optimizer["validation_scope"], "development_reuse")
        self.assertEqual(optimizer["pool"], [f.original[0]["version_id"]] +
                         [item[0]["version_id"] for item in f.candidates])
        self.assertEqual(optimizer["scores"], [[0, 0], *self.scores])
        self.assertEqual(optimizer["frontier"], {"repair": [1, 3], "other": [2, 3]})
        self.assertEqual(len(f.saved), 3)
        self.assertEqual(len(f.evaluated), 3)
        self.seed.assert_called_once()
        self.assertEqual(result["confirmation_status"], "not_run")
        self.assertTrue((f.artifact / "optimizer.json").is_file())

    def test_quality_improvement_with_identical_execution_does_not_replace_seed(self):
        self.fixture.statuses = ["improved"]
        self.scores[0] = [0, 0]
        result = self.run_search(max_rounds=1)
        self.assertIsNone(result["selected_candidate_version_id"])
        self.assertEqual(result["optimizer"]["recommended_version_id"], self.fixture.original[0]["version_id"])
        self.assertEqual(result["rounds"][0]["decision"]["status"], "improved")

    def test_missing_case_is_unverified_not_zero_or_success(self):
        self.seed_checks["cases"].pop()
        result = self.run_search()
        self.assertEqual(result["stop_reason"], "evaluation_unverified")
        self.assertIsNone(result["selected_candidate_version_id"])
        self.assertEqual(self.fixture.generated, [])

    def test_failure_retains_completed_candidates_without_selecting_or_confirming(self):
        f = self.fixture
        original_generate = f.generate
        def fail_second(*args, **kwargs):
            if f.generated:
                raise RuntimeFailure("credit_limit", "Offline limit")
            return original_generate(*args, **kwargs)
        f.generate_mock.side_effect = fail_second
        result = self.run_search(confirmation_context=f.confirmation_factory)
        self.assertEqual(result["stop_reason"], "credit_limit")
        self.assertEqual(len(f.saved), 1)
        self.assertEqual(len(result["rounds"]), 2)
        self.assertIsNone(result["selected_candidate_version_id"])
        f.confirmation_factory.assert_not_called()

    def test_storage_failure_is_not_a_successful_search(self):
        self.fixture.store_error = OSError("Offline storage failure")
        with self.assertRaises(OSError):
            self.run_search()

    def test_runtime_storage_failure_propagates_without_terminal_cycle(self):
        self.fixture.store_error = RuntimeFailure("storage_conflict", "Offline immutable collision")
        with self.assertRaises(RuntimeFailure) as raised:
            self.run_search()
        self.assertEqual(raised.exception.code, "storage_conflict")

    def test_duplicate_proposal_stops_without_replaying_or_claiming_improvement(self):
        self.fixture.candidates[1] = self.fixture.candidates[0]
        result = self.run_search()
        self.assertEqual(result["stop_reason"], "no_change")
        self.assertEqual(len(self.fixture.saved), 1)
        self.assertIsNone(result["selected_candidate_version_id"])

    def test_shared_budget_stops_generation_and_does_not_reset_counter(self):
        f = self.fixture
        f.budget["max_calls"] = f.limits["max_invocations"] = 1
        result = self.run_search()
        self.assertEqual(result["stop_reason"], "call_limit")
        self.assertEqual(f.budget["calls"], 1)
        self.assertEqual(len(f.generated), 1)
        self.assertEqual(len(f.evaluated), 0)

    def test_erred_and_skipped_required_cases_remain_unverified(self):
        for state in ("error", "skipped"):
            with self.subTest(state=state):
                self.seed_checks["cases"][0]["status"] = state
                self.fixture.artifact = self.fixture.root / state
                result = self.run_search()
                self.assertEqual(result["stop_reason"], "evaluation_unverified")
                self.assertEqual(self.fixture.generated, [])

    def test_confirmation_receives_only_frozen_selected_capture(self):
        result = self.run_search(confirmation_context=self.fixture.confirmation_factory)
        self.fixture.confirmation_factory.assert_called_once_with(self.fixture.candidates[2])
        self.assertEqual(result["confirmation_status"], "passed")
        self.assertEqual(len(self.fixture.generated), 3)


@unittest.skipUnless(importlib.util.find_spec("gepa"), "Install requirements-gepa.txt for real optimizer tests")
class GEPAIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = integration.ReplayIntegrationTests("test_actual_provider_common_validators_and_shared_persistence_callback")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        f.raw.scores = (3, 4)
        f.raw.reject_confirmation = False
        invoke = f.raw.invoke
        def transport(prompt, model, role, workdir, artifact, expected_skill=None, **kwargs):
            value = invoke(prompt, model, role, workdir, artifact, expected_skill, **kwargs)
            if role == "developer" and "Attempt " not in Path(expected_skill).read_text():
                value["content"] = json.dumps({"files": {"app.py": "value = 2\n"}})
            return value
        f.raw.invoke = transport
        self.enterContext(patch.object(project_evaluation, "resolve_images", return_value=f.images))
        self.enterContext(patch.object(project_evaluation.project_checks, "prepared_images",
                                      side_effect=lambda *a, **kw: nullcontext(f.images)))

    def run_search(self, confirmation=True):
        f = self.f
        return project_evaluation.run_iterations(
            f.root, project_id="sample_repo", skill_key=f.key, work_item=f.work_path,
            output=f.output, model="offline-model", execution_mode="offline_test",
            policy={"enabled": True, "authenticated": True, "budget": f.budget},
            max_rounds=2, optimizer="gepa", runtime_factory=lambda root: f.raw,
            confirmation_work_item=f.final_path if confirmation else None,
            confirmation_disclosure=f.disclosure_path if confirmation else None)

    def test_real_providers_persist_search_and_isolated_confirmation(self):
        report = self.run_search()
        self.assertEqual(report["status"], "search_complete")
        self.assertEqual(report["confirmation_status"], "passed")
        self.assertFalse(report["approval_eligible"])
        cycles = project_results.load_cycles(self.f.output)
        self.assertEqual(len(cycles), 1)
        cycle = next(iter(cycles.values()))
        self.assertEqual(cycle["schema_version"], 2)
        self.assertEqual(cycle["optimizer"]["scores"], [[0, 1], [1, 1], [1, 1]])
        self.assertEqual(cycle["selected_candidate_version_id"], cycle["rounds"][0]["candidate_version_id"])
        generators = [prompt for role, prompt in self.f.raw.calls if role == "generator"]
        self.assertEqual(len(generators), 2)
        for prompt in generators:
            self.assertNotIn("FINAL_REQUEST_SENTINEL", prompt)
            self.assertNotIn("hidden-confirmation", prompt)
        self.assertEqual(self.f.raw.calls[-1][0], "developer")
        self.assertFalse((self.f.root / ".skillops-private/active.json").exists())

    def test_rehashed_cycle_cannot_forge_scores_parent_or_selection(self):
        self.run_search(confirmation=False)
        f = self.f
        cycle = next(iter(project_results.load_cycles(f.output).values()))
        reports = {(r["project_id"], r["run_id"]): r for r in project_results.load_reports(f.output)}
        evidence = project_results.load_replay_evidence(f.output)
        mutations = [
            lambda row: row["optimizer"]["scores"][0].__setitem__(0, 1),
            lambda row: row["optimizer"].__setitem__("recommended_version_id", row["original_version_id"]),
            lambda row: row["rounds"][1].__setitem__("parent_version_id", "sha256:" + "0" * 64),
            lambda row: row["optimizer"].__setitem__("validation_scope", "held_out"),
            lambda row: row["optimizer"]["frontier"].__setitem__("python-tests:development", [0]),
            lambda row: row["optimizer"]["pool"].__setitem__(slice(1, 3), row["optimizer"]["pool"][1:3][::-1]),
        ]
        for mutate in mutations:
            value = deepcopy(cycle)
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(RuntimeFailure):
                project_results.validate_cycle(
                    value, report=reports[(cycle["project_id"], cycle["run_id"])], evaluations=evidence)

    def test_provider_failure_persists_partial_pool_with_no_approval_eligibility(self):
        self.f.raw.fail_generation_at = 2
        receipt = self.run_search()
        self.assertEqual(receipt["status"], "runtime_error")
        self.assertFalse(receipt["approval_eligible"])
        self.assertEqual(self.f.raw.generated, 2)
        cycle = next(iter(project_results.load_cycles(self.f.output).values()))
        self.assertEqual(len(cycle["optimizer"]["pool"]), 2)
        self.assertEqual(len(cycle["rounds"]), 2)
        self.assertIsNone(cycle["rounds"][-1]["evaluation_ref"])
        self.assertIsNone(cycle["selected_candidate_version_id"])
        self.assertFalse(any("FINAL_REQUEST_SENTINEL" in prompt for _, prompt in self.f.raw.calls))

    def test_missing_optional_dependency_fails_before_model_runtime(self):
        import gepa_search
        with patch.object(gepa_search, "version", side_effect=gepa_search.PackageNotFoundError), \
                self.assertRaises(RuntimeFailure) as raised:
            self.run_search()
        self.assertEqual(raised.exception.code, "gepa_not_installed")
        self.assertEqual(self.f.raw.calls, [])


def dashboard_fixture():
    """Build public response fixtures using real search/storage; transport stays offline."""
    test = GEPAIntegrationTests("test_real_providers_persist_search_and_isolated_confirmation")
    test.setUp()
    try:
        receipt = test.run_search()
        f = test.f
        project_results.reindex(f.root, f.output)
        history = project_results.read_json(f.output / "sample_repo/index.json")["history"]
        items = []
        for summary in history:
            folder = f.output / "sample_repo" / summary["run_id"]
            item = {"summary": summary, "report": project_results.read_json(folder / "report.json"),
                    "raw": {"report": (folder / "report.json").read_text()}}
            for key, name in (("envelope", "skill-evolution.json"), ("replay", "replay-evaluation.json"),
                              ("cycle", "cycle.json")):
                if (folder / name).exists():
                    item[key] = project_results.read_json(folder / name)
                    item["raw"][key] = (folder / name).read_text()
            items.append(item)
        cycle = next(item for item in items if item["report"]["run_id"] == receipt["cycle_id"])
        return {"items": items, "cycle": cycle, "key": f.key}
    finally:
        test.doCleanups()


if __name__ == "__main__":
    unittest.main()
