from copy import deepcopy
import base64
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from copilot_runtime import RuntimeFailure
from hackathon_fixtures import context, digest, feedback, fixture, write_results
import project_evaluation
import project_results as results
import skill_assessments as assessments
import evolution_records as evolution


class CommonContractsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture(self.root)

    def api(self, module, name):
        value = getattr(module, name, None)
        self.assertTrue(callable(value), f"Missing shared API: {module.__name__}.{name}")
        return value

    def test_work_item_binds_request_sources_checks_and_project(self):
        validate = self.api(assessments, "validate_work_item")
        data = self.data
        self.assertEqual(validate(data["work_item"], project=data["project"], source_commit="a" * 40),
                         data["work_item"])
        for field, value in (("request", "Different request"), ("source_commit", "b" * 40),
                             ("project_id", "wrong"), ("schema_version", True)):
            with self.subTest(field=field):
                work = deepcopy(data["work_item"])
                work[field] = value
                with self.assertRaises(RuntimeFailure):
                    validate(work, project=data["project"], source_commit="a" * 40)
        (data["project"] / "app.py").write_text("value = 2\n")
        with self.assertRaises(RuntimeFailure):
            validate(data["work_item"], project=data["project"], source_commit="a" * 40)

    def test_work_rejects_self_hashed_protected_and_unsafe_paths(self):
        validate = self.api(assessments, "validate_work_item")
        for path in ("tests/test_app.py", "skills/develop/SKILL.md", "../app.py", "/app.py"):
            with self.subTest(path=path):
                work = deepcopy(self.data["work_item"])
                work["sources"] = {path: "a" * 64}
                work["input_sha256"] = digest({k: v for k, v in work.items() if k != "input_sha256"})
                with self.assertRaises(RuntimeFailure):
                    validate(work, project=self.data["project"], source_commit="a" * 40)

    def test_work_source_rejects_evaluator_and_embedded_skill_files(self):
        validate = self.api(assessments, "validate_work_item")
        project = self.data["project"]
        for name in ("evaluation.py", "plugin/tool.py"):
            path = project / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("value = 1\n")
            if name.startswith("plugin"):
                (path.parent / "SKILL.md").write_text("A bundled tool.\n")
            work = deepcopy(self.data["work_item"])
            work["project_tree_sha256"] = results.tree_hash(project)
            from hashlib import sha256
            work["sources"] = {name: sha256(path.read_bytes()).hexdigest()}
            work["input_sha256"] = digest({k: v for k, v in work.items() if k != "input_sha256"})
            with self.subTest(name=name), self.assertRaises(RuntimeFailure):
                validate(work, project=project, source_commit="a" * 40)

    def test_replay_validates_frozen_reference_complete_versions_and_decision(self):
        validate = self.api(assessments, "validate_replay")
        for item in self.data["evaluations"].values():
            self.assertEqual(validate(item["replay"], report=item["report"], lifecycle=item["lifecycle"]),
                             item["replay"])
        item = self.data["evaluations"][("sample_repo", "102-1")]
        mutations = [
            lambda r: r.update(report_sha256="0" * 64),
            lambda r: r["reference"].update(original_version_id=r["evaluation"]["candidate_version_id"]),
            lambda r: r["evaluation"]["work"].update(input_sha256="0" * 64),
            lambda r: r["evaluation"]["decision"].update(status="not_improved"),
            lambda r: r["evaluation"]["applications"]["candidate"].update(staged_version_id="sha256:" + "0" * 64),
            lambda r: r["evaluation"]["checks"]["candidate"].update(protected_sha256="0" * 64),
            lambda r: r["evaluation"]["quality"]["candidate"].update(rubric_sha256="0" * 64),
            lambda r: r.update(execution_mode="mock"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                replay = deepcopy(item["replay"])
                mutate(replay)
                with self.assertRaises(RuntimeFailure):
                    validate(replay, report=item["report"], lifecycle=item["lifecycle"])

    def test_confirmation_disclosure_binds_complete_inventory_without_certifying_isolation(self):
        validate = self.api(assessments, "validate_confirmation_disclosure")
        data = self.data
        project = data["project"]
        development = data["work_item"]
        confirmation = deepcopy(development)
        confirmation.update(task_id="final-task", split="confirmation",
                            request="A distinct final request without development exposure.")
        confirmation["checks"]["required_case_ids"] = ["confirmation"]
        confirmation["input_sha256"] = digest({k: v for k, v in confirmation.items() if k != "input_sha256"})
        files = {p.relative_to(project).as_posix(): sha256(p.read_bytes()).hexdigest()
                 for p in project.rglob("*") if p.is_file()}
        visible = set(development["sources"]) | set(confirmation["sources"]) | {
            "skills/develop/SKILL.md", "skills/develop/notes.txt"}
        disclosure = {
            "schema_version": 1, "development_input_sha256": development["input_sha256"],
            "confirmation_input_sha256": confirmation["input_sha256"],
            "model_visible_files": {k: v for k, v in files.items() if k in visible},
            "checker_only_files": {k: v for k, v in files.items() if k not in visible},
        }
        disclosure["disclosure_sha256"] = digest(disclosure)
        original = evolution.capture_version(
            {p.name: p.read_bytes() for p in (project / "skills/develop").iterdir()},
            capture_scope="complete_bundle", complete_inventory=["SKILL.md", "notes.txt"])
        options = dict(project=project, development_work_item=development,
                       confirmation_work_item=confirmation, original=original, source_path="skills/develop")
        self.assertEqual(validate(disclosure, **options), disclosure)
        for change in (
            lambda d: d.update(schema_version=True),
            lambda d: d.update(confirmation_input_sha256=development["input_sha256"]),
            lambda d: d["model_visible_files"].update(d["checker_only_files"]),
            lambda d: d["checker_only_files"].clear(),
            lambda d: d["model_visible_files"].update({"app.py": "0" * 64}),
            lambda d: d["model_visible_files"].pop("skills/develop/notes.txt"),
            lambda d: d["model_visible_files"].update({"../outside": "0" * 64}),
            lambda d: d.update(isolated=True),
        ):
            value = deepcopy(disclosure)
            change(value)
            value["disclosure_sha256"] = digest({k: v for k, v in value.items() if k != "disclosure_sha256"})
            with self.subTest(change=change), self.assertRaises(RuntimeFailure):
                validate(value, **options)
        wrong = deepcopy(confirmation)
        wrong["checks"]["required_case_ids"] = development["checks"]["required_case_ids"]
        wrong["input_sha256"] = digest({k: v for k, v in wrong.items() if k != "input_sha256"})
        with self.assertRaises(RuntimeFailure):
            validate(disclosure, **{**options, "confirmation_work_item": wrong})

        for request in ("Keep companion files.", development["request"]):
            wrong = deepcopy(confirmation)
            wrong["request"] = request
            wrong["input_sha256"] = digest({k: v for k, v in wrong.items() if k != "input_sha256"})
            value = deepcopy(disclosure)
            value["confirmation_input_sha256"] = wrong["input_sha256"]
            value["disclosure_sha256"] = digest({k: v for k, v in value.items() if k != "disclosure_sha256"})
            with self.subTest(exposed_request=request), self.assertRaises(RuntimeFailure):
                validate(value, **{**options, "confirmation_work_item": wrong})
        (project / "unexpected.txt").write_text("Changed after disclosure review.")
        with self.assertRaises(RuntimeFailure):
            validate(disclosure, **options)

    def test_replay_decision_accepts_recorded_task_without_original_failure(self):
        decide = self.api(assessments, "decide_replay")
        row = deepcopy(self.data["evaluations"][("sample_repo", "102-1")]["replay"]["evaluation"])
        self.assertEqual(decide(row, work_item=self.data["work_item"])["status"], "improved")
        row["applications"]["candidate"]["measurement"]["cost_nano_aiu"] = None
        self.assertEqual(decide(row, work_item=self.data["work_item"])["status"], "unverified")
        row["checks"]["candidate"]["cases"][1]["status"] = "failed"
        row["checks"]["candidate"]["status"] = "failed"
        self.assertEqual(decide(row, work_item=self.data["work_item"])["status"], "rejected")

    def test_candidate_cannot_change_companions_even_with_valid_new_capture(self):
        validate = self.api(assessments, "validate_replay")
        item = deepcopy(self.data["evaluations"][("sample_repo", "102-1")])
        lifecycle, replay = item["lifecycle"], item["replay"]
        old = replay["evaluation"]["candidate_version_id"]
        files = dict(self.data["captures"][2][1])
        files["notes.txt"] = b"Changed companion.\n"
        version, retained = evolution.capture_version(files, capture_scope="complete_bundle",
                                                       complete_inventory=sorted(files))
        new = version["version_id"]
        lifecycle["records"]["versions"] = [v for v in lifecycle["records"]["versions"] if v["version_id"] != old] + [version]
        for link in lifecycle["records"]["skill_versions"]:
            if link["version_id"] == old:
                link["version_id"] = new
        lifecycle["bindings"][0]["candidate_version_id"] = new
        lifecycle["file_contents"] = [v for v in lifecycle["file_contents"] if v["version_id"] != old] + [
            {"version_id": new, "path": path, "encoding": "base64", "data": base64.b64encode(raw).decode()}
            for path, raw in retained.items()]
        replay["evaluation"]["candidate_version_id"] = new
        replay["evaluation"]["applications"]["candidate"].update(version_id=new, staged_version_id=new)
        results.validate_evolution(lifecycle, item["report"])
        with self.assertRaises(RuntimeFailure):
            validate(replay, report=item["report"], lifecycle=lifecycle)

    def test_sample_replay_has_no_invented_source_identity_or_live_mode(self):
        validate = self.api(assessments, "validate_replay")
        item = deepcopy(self.data["evaluations"][("sample_repo", "102-1")])
        run = "sample-20260917T120000Z-0123456789ab"
        item["report"].update(origin="sample", run_id=run, source_commit=None,
                              project_tree_sha256=None, evaluator_sha256=None)
        for key in ("lifecycle", "replay"):
            item[key].update(run_id=run, report_sha256=digest(item["report"]))
        replay = item["replay"]
        replay["execution_mode"] = "sample"
        replay["reference"].update(source_commit=None, project_tree_sha256=None, evaluator_sha256=None)
        replay["reference"]["reference_sha256"] = digest({k: v for k, v in replay["reference"].items()
                                                         if k != "reference_sha256"})
        replay["evaluation"]["reference_sha256"] = replay["reference"]["reference_sha256"]
        self.assertIs(validate(replay, report=item["report"], lifecycle=item["lifecycle"]), replay)
        replay["execution_mode"] = "live"
        with self.assertRaises(RuntimeFailure):
            validate(replay, report=item["report"], lifecycle=item["lifecycle"])

    def test_cycle_validates_n2_lineage_and_confirmation(self):
        validate = self.api(results, "validate_cycle")
        data = self.data
        self.assertEqual(validate(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"]),
                         data["cycle"])
        mutations = [
            lambda c: c.update(max_rounds=True),
            lambda c: c.update(max_rounds=1),
            lambda c: c["rounds"][1].update(parent_version_id=c["original_version_id"]),
            lambda c: c["rounds"][1].update(feedback_source_round_id=None),
            lambda c: c["rounds"][1].update(input_sha256="0" * 64),
            lambda c: c["rounds"][1]["evaluation_ref"].update(sha256="0" * 64),
            lambda c: c["rounds"][0].update(stop_reason="max_rounds"),
            lambda c: c.update(selected_candidate_version_id=c["rounds"][0]["candidate_version_id"]),
            lambda c: c.update(confirmation_ref=c["rounds"][1]["evaluation_ref"]),
            lambda c: c.update(confirmation_status="not_run"),
            lambda c: c["budget"].update(max_seconds=0),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                cycle = deepcopy(data["cycle"])
                mutate(cycle)
                with self.assertRaises(RuntimeFailure):
                    validate(cycle, report=data["cycle_report"], evaluations=data["evaluations"])

    def test_cycle_rejects_transitive_tampering_even_with_rehashed_reference(self):
        validate = self.api(results, "validate_cycle")
        data = deepcopy(self.data)
        replay = data["evaluations"][("sample_repo", "103-1")]["replay"]
        replay["execution_mode"] = "live"
        data["cycle"]["confirmation_ref"]["sha256"] = digest(replay)
        with self.assertRaises(RuntimeFailure):
            validate(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"])

    def test_started_unsaved_attempt_has_null_run_reference_and_decision(self):
        cycle = deepcopy(self.data["cycle"])
        cycle.update(stop_reason="time_limit", selected_candidate_version_id=None,
                     confirmation_ref=None, confirmation_status="not_run")
        cycle["rounds"][-1].update(run_id=None, evaluation_ref=None, decision=None, stop_reason="time_limit")
        self.assertIs(results.validate_cycle(cycle, report=self.data["cycle_report"],
                                              evaluations=self.data["evaluations"]), cycle)
        for mutate in (
            lambda c: c["rounds"][-1].update(run_id="999-1"),
            lambda c: c["rounds"][-1].update(decision=self.data["cycle"]["rounds"][-1]["decision"]),
            lambda c: c["rounds"][-1].update(evaluation_ref=self.data["cycle"]["rounds"][-1]["evaluation_ref"]),
            lambda c: c.update(stop_reason="max_rounds"),
        ):
            with self.subTest(mutation=mutate):
                invalid = deepcopy(cycle)
                mutate(invalid)
                with self.assertRaises(RuntimeFailure):
                    results.validate_cycle(invalid, report=self.data["cycle_report"],
                                           evaluations=self.data["evaluations"])

    def test_unsaved_attempt_cannot_be_followed_by_another_round(self):
        cycle = deepcopy(self.data["cycle"])
        cycle["rounds"][0].update(run_id=None, evaluation_ref=None, decision=None)
        with self.assertRaises(RuntimeFailure):
            results.validate_cycle(cycle, report=self.data["cycle_report"], evaluations=self.data["evaluations"])

    def test_no_admitted_attempt_has_no_placeholder_round(self):
        cycle = deepcopy(self.data["cycle"])
        cycle.update(rounds=[], stop_reason="call_limit", selected_candidate_version_id=None,
                     confirmation_ref=None, confirmation_status="not_run")
        self.assertIs(results.validate_cycle(cycle, report=self.data["cycle_report"], evaluations={}), cycle)
        cycle["stop_reason"] = "no_change"
        with self.assertRaises(RuntimeFailure):
            results.validate_cycle(cycle, report=self.data["cycle_report"], evaluations={})

    def test_saved_round_cannot_lose_its_run_id(self):
        cycle = deepcopy(self.data["cycle"])
        cycle["rounds"][0]["run_id"] = None
        with self.assertRaises(RuntimeFailure):
            results.validate_cycle(cycle, report=self.data["cycle_report"], evaluations=self.data["evaluations"])

    def test_cycle_rejects_task_criteria_drift_with_unchanged_input_hash(self):
        validate = self.api(results, "validate_cycle")
        for key, value in (("task_id", "forged"), ("checks", {
            **self.data["work_item"]["checks"], "required_case_ids": ["stable"],
        })):
            with self.subTest(key=key):
                data = deepcopy(self.data)
                replay = data["evaluations"][("sample_repo", "102-1")]["replay"]
                replay["evaluation"]["work"][key] = value
                data["cycle"]["rounds"][1]["evaluation_ref"]["sha256"] = digest(replay)
                with self.assertRaises(RuntimeFailure):
                    validate(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"])

    def test_cycle_cannot_continue_after_rejected_evaluation_with_stage_error(self):
        validate = self.api(results, "validate_cycle")
        data = self.data
        replay = data["evaluations"][("sample_repo", "101-1")]["replay"]
        row = replay["evaluation"]
        row["checks"]["candidate"]["cases"][2]["status"] = "failed"
        row["checks"]["candidate"]["status"] = "failed"
        row["errors"] = [{"stage": "candidate_quality", "code": "runtime_error"}]
        row["decision"] = assessments.decide_replay(row, work_item=data["work_item"])
        data["cycle"]["rounds"][0]["decision"] = deepcopy(row["decision"])
        data["cycle"]["rounds"][0]["evaluation_ref"]["sha256"] = digest(replay)
        with self.assertRaises(RuntimeFailure):
            validate(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"])

    def test_blocked_confirmation_has_no_artifact_and_never_passes(self):
        validate = self.api(results, "validate_cycle")
        data = self.data
        data["cycle"].update(confirmation_ref=None, confirmation_status="unverified")
        self.assertEqual(validate(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"]),
                         data["cycle"])
        data["cycle"]["confirmation_status"] = "passed"
        with self.assertRaises(RuntimeFailure):
            validate(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"])

    def test_feedback_semantics_bind_source_evaluation_parent_and_scope(self):
        validate = self.api(assessments, "validate_development_feedback")
        ctx = context(self.data)
        packet = feedback(self.data)
        self.assertIs(validate(packet, context=ctx, evaluation=None, parent=ctx["original"],
                               source_round_id=None), packet)
        row = self.data["evaluations"][("sample_repo", "102-1")]["replay"]["evaluation"]
        packet = feedback(self.data, run="102-1")
        self.assertEqual(validate(packet, context=ctx, evaluation=row, parent=self.data["captures"][2],
                                  source_round_id="100-1-r2"), packet)
        for mutate in (
            lambda p: p.update(input_sha256="0" * 64),
            lambda p: p["quality"]["dimensions"][0].update(score=4),
            lambda p: p["checks"]["cases"].append({"id": "confirmation", "status": "passed"}),
            lambda p: p["application"].update(output_sha256="0" * 64),
            lambda p: p.update(source_round_id="100-1-r1"),
            lambda p: p["decision"].update(status="not_improved"),
        ):
            with self.subTest(mutation=mutate):
                forged = deepcopy(packet)
                mutate(forged)
                with self.assertRaises(RuntimeFailure):
                    validate(forged, context=ctx, evaluation=row, parent=self.data["captures"][2],
                             source_round_id="100-1-r2")
        with self.assertRaises(RuntimeFailure):
            validate(packet, context=ctx, evaluation=row, parent=self.data["captures"][1],
                     source_round_id="100-1-r2")

    def test_feedback_rejects_confirmation_or_widened_context_without_invoking(self):
        validate = self.api(assessments, "validate_development_feedback")
        ctx = context(self.data)
        for mutate in (
            lambda c: c["feedback_scope"]["case_ids"].append("confirmation"),
            lambda c: c["feedback_scope"]["test_context_paths"].append("tests/test_app.py"),
            lambda c: c["work_item"].update(split="confirmation"),
            lambda c: c.update(execution_mode=True),
            lambda c: c["original"][1].update({"notes.txt": b"tampered"}),
        ):
            with self.subTest(mutation=mutate):
                forged = deepcopy(ctx)
                mutate(forged)
                with self.assertRaises(RuntimeFailure):
                    validate(feedback(self.data), context=forged, evaluation=None,
                             parent=ctx["original"], source_round_id=None)

    def test_hidden_failure_cannot_be_removed_from_feedback_decision(self):
        validate = self.api(assessments, "validate_development_feedback")
        decide = self.api(assessments, "decide_replay")
        ctx = context(self.data)
        row = deepcopy(self.data["evaluations"][("sample_repo", "102-1")]["replay"]["evaluation"])
        row["checks"]["candidate"]["cases"][0]["status"] = "failed"
        row["checks"]["candidate"]["status"] = "failed"
        row["decision"] = decide(row, work_item=self.data["work_item"])
        packet = feedback(self.data, run="102-1")
        packet["decision"] = deepcopy(row["decision"])
        with self.assertRaises(RuntimeFailure) as raised:
            validate(packet, context=ctx, evaluation=row, parent=self.data["captures"][2],
                     source_round_id="100-1-r2")
        self.assertEqual(raised.exception.code, "confirmation_isolation_unverified")

    def test_feedback_requires_exact_numeric_bytes_not_only_python_equality(self):
        validate = self.api(assessments, "validate_development_feedback")
        ctx, packet = context(self.data), feedback(self.data)
        packet["quality"]["dimensions"][0]["score"] = 2.0
        with self.assertRaises(RuntimeFailure):
            validate(packet, context=ctx, evaluation=None, parent=ctx["original"], source_round_id=None)

    def test_changing_hidden_timings_does_not_enter_initial_feedback(self):
        validate = self.api(assessments, "validate_development_feedback")
        ctx, packet = context(self.data), feedback(self.data)
        ctx["reference"]["original_checks"]["elapsed_seconds"] = 999
        ctx["reference"]["reference_sha256"] = digest({k: v for k, v in ctx["reference"].items()
                                                       if k != "reference_sha256"})
        self.assertIs(validate(packet, context=ctx, evaluation=None, parent=ctx["original"],
                               source_round_id=None), packet)

    def test_loaders_return_validated_mappings_and_reject_orphans(self):
        load_replays = self.api(results, "load_replays")
        load_cycles = self.api(results, "load_cycles")
        directory = write_results(self.root / "results", self.data)
        self.assertEqual(len(load_replays(directory)), 3)
        self.assertEqual(load_cycles(directory)[("sample_repo", "100-1")], self.data["cycle"])
        (directory / "sample_repo/101-1/report.json").unlink()
        for loader in (load_replays, load_cycles):
            with self.assertRaises(RuntimeFailure):
                loader(directory)

    def test_missing_sidecars_are_not_invented_for_legacy_results(self):
        load_replays = self.api(results, "load_replays")
        load_cycles = self.api(results, "load_cycles")
        directory = self.root / "legacy"
        results.store(directory, self.data["cycle_report"])
        before = (directory / "sample_repo/100-1/report.json").read_bytes()
        self.assertEqual(load_replays(directory), {})
        self.assertEqual(load_cycles(directory), {})
        self.assertEqual((directory / "sample_repo/100-1/report.json").read_bytes(), before)

    def test_results_validate_command_rejects_invalid_new_sidecar(self):
        directory = write_results(self.root / "results", self.data)
        path = directory / "sample_repo/100-1/cycle.json"
        cycle = deepcopy(self.data["cycle"])
        cycle["rounds"][1]["feedback_source_round_id"] = None
        path.write_bytes(results.encoded(cycle))
        completed = subprocess.run([sys.executable, results.__file__, "validate", "--results", str(directory)],
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)

    def test_budget_preserves_authorized_duration_after_time_elapses(self):
        limits = self.api(project_evaluation, "budget_limits")
        with patch.dict(project_evaluation.os.environ, {
            "SKILLOPS_MAX_INVOCATIONS": "30", "SKILLOPS_MAX_SECONDS": "600",
            "SKILLOPS_MAX_AI_CREDITS_PER_SESSION": "30",
        }, clear=True), patch.object(project_evaluation.time, "monotonic", return_value=100):
            budget = project_evaluation.policy_from_environment()["budget"]
        self.assertEqual(budget["max_seconds"], 600)
        with patch.object(project_evaluation.time, "monotonic", return_value=699):
            self.assertEqual(limits(budget), self.data["cycle"]["budget"])
        del budget["max_seconds"]
        with self.assertRaises(RuntimeFailure):
            limits(budget)

    def test_fixture_feedback_hashes_bind_actual_previous_round_packets(self):
        for run, packet in (("101-1", feedback(self.data)), ("102-1", feedback(self.data, run="101-1"))):
            self.assertEqual(self.data["evaluations"][("sample_repo", run)]["replay"]["generation"]["feedback_sha256"],
                             digest(packet))

    def test_unwired_adoption_publisher_must_not_silently_drop_evidence(self):
        directory = write_results(self.root / "results", self.data)
        results.atomic_json(directory / "sample_repo/100-1/adoption.json", {"schema_version": 1})
        root = Path(results.__file__).parent
        for operation in ("merge", "build"):
            with self.subTest(operation=operation):
                with self.assertRaises(RuntimeFailure) as raised:
                    if operation == "merge":
                        results.merge_results(root, directory, self.root / "merged")
                    else:
                        results.build(root, directory, self.root / "site")
                self.assertEqual(raised.exception.code, "adoption_publication_pending")
