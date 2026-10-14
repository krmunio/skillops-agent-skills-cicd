"""Dataset preparation never executes project code or invokes a model."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from copilot_runtime import CopilotRuntime, RuntimeFailure
import project_results as results
import project_evaluation
import project_samples
import skill_assessments


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("evaluation_datasets"),
                             "Dataset preparation and validation are not implemented")
        self.datasets = importlib.import_module("evaluation_datasets")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "projects/project_c"
        (self.project / "tests").mkdir(parents=True)
        self.spec = {"schema_version": 1, "dataset_id": "queue-v1", "project_id": "project_c",
                     "origin": "synthetic", "tasks": []}
        for split, family in (("development", "selection"), ("validation", "tags"),
                              ("confirmation", "retry")):
            (self.project / f"{family}.py").write_text("value = 0\n")
            (self.project / f"tests/test_{family}.py").write_text(
                "import unittest\nraise AssertionError('must not import project tests')\n"
                "class Checks(unittest.TestCase):\n"
                "    def test_first(self): pass\n"
                "    def test_second(self): pass\n")
            self.spec["tasks"].append({
                "task_id": family, "family": family, "split": split,
                "request": f"Repair {family} without changing protected tests.",
                "sources": [f"{family}.py"],
                "required_case_ids": [f"python-tests:test_{family}.Checks.test_first",
                                      f"python-tests:test_{family}.Checks.test_second"],
            })
        self.spec_path = self.root / "spec.json"
        self.spec_path.write_bytes(results.encoded(self.spec))
        self.output = self.root / ".skillops-private/datasets/queue.json"
        for arguments in (("init", "-q"), ("add", "."), ("-c", "user.name=Dataset fixture",
                          "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture")):
            self.git(*arguments)

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.root), *arguments], check=True, capture_output=True)

    def prepare(self):
        self.spec_path.write_bytes(results.encoded(self.spec))
        return self.datasets.prepare(self.root, self.spec_path, self.output)

    def test_prepare_pins_three_splits_without_executing_project(self):
        with patch("copilot_runtime.CopilotRuntime.invoke", side_effect=AssertionError("No model")):
            data = self.prepare()
        self.assertEqual(data["source_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual(data["project_tree_sha256"], results.tree_hash(self.project))
        self.assertEqual(data["spec_sha256"], sha256(results.read_bytes(self.spec_path)).hexdigest())
        self.assertEqual([task["split"] for task in data["tasks"]],
                         ["development", "validation", "confirmation"])
        self.assertEqual(self.datasets.validate(self.root, data), data)
        self.assertEqual(results.read_json(self.output), data)
        self.assertFalse(list(self.project.rglob("SKILL.md")))
        self.assertFalse((self.root / "results").exists())

    def test_development_export_is_a_real_work_item_only(self):
        data = self.prepare()
        output = self.root / ".skillops-private/work-items/selection.json"
        work = self.datasets.export_development(self.root, self.output, "selection", output)
        self.assertEqual(work["split"], "development")
        skill_assessments.validate_work_item(work, project=self.project, source_commit=data["source_commit"])
        for task_id in ("tags", "retry", "missing"):
            with self.subTest(task=task_id), self.assertRaises(RuntimeFailure):
                self.datasets.export_development(self.root, self.output, task_id, output.with_name(task_id + ".json"))
            self.assertFalse(output.with_name(task_id + ".json").exists())

    def test_preparation_keeps_the_private_store_compatible_with_runtime(self):
        self.prepare()
        with patch("copilot_runtime.shutil.which", return_value="/offline/copilot"):
            runtime = CopilotRuntime(self.root, inherited={})
        self.assertEqual(runtime.private, self.root / ".skillops-private")
        self.assertEqual((runtime.private / ".owner").read_text(), "skillops-profile-v1\n")

    def test_exported_work_can_be_retained_by_the_live_runner(self):
        self.prepare()
        output = self.root / ".skillops-private/work-items/development.json"
        work = self.datasets.export_development(self.root, self.output, "selection", output)
        try:
            retained = project_evaluation.retain_work_item(self.root, work)
        except RuntimeFailure as error:
            self.fail(f"Export must preserve runner-compatible private permissions: {error.code}")
        self.assertEqual(results.read_json(retained), work)
        self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)

    def test_preparation_rejects_an_unowned_private_directory(self):
        private = self.root / ".skillops-private"
        private.mkdir(mode=0o700)
        with self.assertRaises(RuntimeFailure) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "unsafe_profile")
        self.assertFalse((private / ".owner").exists())
        self.assertFalse(self.output.exists())

    def test_rejects_missing_split_duplicate_ids_and_cross_split_leakage(self):
        changes = (
            lambda s: s["tasks"].pop(),
            lambda s: s["tasks"][1].update(task_id="selection"),
            lambda s: s["tasks"][1].update(family="selection"),
            lambda s: s["tasks"][1].update(request="  REPAIR selection without changing protected tests. "),
            lambda s: s["tasks"][1].update(sources=["selection.py"]),
            lambda s: s["tasks"][1].update(required_case_ids=s["tasks"][0]["required_case_ids"]),
            lambda s: s["tasks"][0].update(required_case_ids=["python-tests:nonexistent"]),
            lambda s: s["tasks"][0].update(sources=["tests/test_selection.py"]),
            lambda s: s["tasks"][0].update(split="heldout"),
            lambda s: s.update(schema_version=True),
            lambda s: s.update(tasks=[]),
            lambda s: s["tasks"][0].update(unexpected=True),
        )
        original = deepcopy(self.spec)
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.spec = deepcopy(original)
                change(self.spec)
                with self.assertRaises(RuntimeFailure):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_rejects_tampering_source_drift_and_foreign_project(self):
        data = self.prepare()
        changed = deepcopy(data)
        changed["tasks"][0]["request"] = "Altered request"
        with self.assertRaises(RuntimeFailure):
            self.datasets.validate(self.root, changed)
        changed = deepcopy(data)
        changed["project_id"] = "other"
        with self.assertRaises(RuntimeFailure):
            self.datasets.validate(self.root, changed)
        (self.project / "selection.py").write_text("value = 1\n")
        with self.assertRaises(RuntimeFailure):
            self.datasets.validate(self.root, data)

    def test_rejects_untracked_inputs_and_unsafe_or_existing_output(self):
        (self.project / "untracked.py").write_text("value = 1\n")
        with self.assertRaises(RuntimeFailure):
            self.prepare()
        (self.project / "untracked.py").unlink()
        with self.assertRaises(RuntimeFailure):
            self.datasets.prepare(self.root, self.spec_path, self.root / "results/dataset.json")
        self.prepare()
        before = self.output.read_bytes()
        with self.assertRaises(RuntimeFailure):
            self.prepare()
        self.assertEqual(self.output.read_bytes(), before)
        link = self.root / ".skillops-private/linked"
        link.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(RuntimeFailure):
            self.datasets.prepare(self.root, self.spec_path, link / "dataset.json")
        self.assertFalse((self.project / "dataset.json").exists())

    def test_cli_reports_counts_not_requests_and_errors_are_nonzero(self):
        with redirect_stdout(io.StringIO()) as stdout:
            code = self.datasets.main(["--root", str(self.root), "prepare",
                                       "--spec", str(self.spec_path), "--output", str(self.output)])
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["splits"], {"development": 1, "validation": 1, "confirmation": 1})
        self.assertEqual(summary["model_calls"], 0)
        self.assertFalse(summary["approval_eligible"])
        self.assertEqual(summary["confirmation_status"], "not_run")
        self.assertNotIn("Repair", stdout.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
            code = self.datasets.main(["--root", str(self.root), "validate",
                                       "--dataset", str(self.root / "missing.json")])
        self.assertEqual(code, 2)
        self.assertIn("Dataset preparation failed", stderr.getvalue())

    def test_committed_project_c_recipe_prepares_only_its_target(self):
        repository = Path(__file__).resolve().parents[2]
        sample = repository / "projects/project_c"
        recipe = repository / "eval/datasets/project_c.json"
        self.assertTrue(sample.is_dir(), "The skill-free project_c sample is missing")
        self.assertTrue(recipe.is_file(), "The project_c dataset recipe is missing")
        shutil.rmtree(self.project)
        shutil.copytree(sample, self.project, ignore=shutil.ignore_patterns("__pycache__"))
        self.spec = results.read_json(recipe)
        self.git("add", "projects")
        self.git("-c", "user.name=Dataset fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Project C")
        other = self.root / "projects/untouched"
        other.mkdir()
        (other / "unsafe").symlink_to(self.root)
        data = self.prepare()
        self.assertEqual(data["project_id"], "project_c")
        self.assertEqual(data["dataset_id"], "project-c-queue-v2")
        self.assertEqual(data["tasks"][0]["task_id"], "queue-selection-v2")
        _, available = self.datasets._cases(self.project)
        selected = set(data["tasks"][0]["checks"]["required_case_ids"])
        self.assertEqual(len(selected), 12)
        self.assertEqual(selected, {case for case in available if ":test_selection." in case})
        self.assertFalse(list(self.project.rglob("SKILL.md")))

    def test_demo_defects_are_failed_assertions_not_runtime_errors(self):
        sample = Path(__file__).resolve().parents[2] / "projects/project_c"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project_c"
            shutil.copytree(sample, project, ignore=shutil.ignore_patterns("__pycache__"))
            run = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
                                 cwd=project, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("Ran 18 tests", run.stderr)
        self.assertIn("FAILED (failures=9)", run.stderr)
        self.assertNotIn("ERROR:", run.stderr)

    def test_skill_onboarding_requires_a_new_committed_dataset(self):
        data = self.prepare()
        template = (Path(__file__).resolve().parents[1]
                    / "project_templates/project-c-development/SKILL.md")
        added = project_samples.ensure_skill(self.project, template)
        self.assertEqual(added["action"], "added")
        with self.assertRaises(RuntimeFailure):
            self.datasets.validate(self.root, data)
        self.git("add", "projects")
        self.git("-c", "user.name=Dataset fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Add unvalidated Skill")
        with self.assertRaises(RuntimeFailure):
            self.datasets.validate(self.root, data)
        output = self.output.with_name("with-skill.json")
        updated = self.datasets.prepare(self.root, self.spec_path, output)
        self.assertNotEqual(updated["project_tree_sha256"], data["project_tree_sha256"])
        self.assertEqual(updated["tasks"], data["tasks"])

    def test_v2_selection_contract_is_satisfiable_without_changing_tests(self):
        sample = Path(__file__).resolve().parents[2] / "projects/project_c"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project_c"
            shutil.copytree(sample, project, ignore=shutil.ignore_patterns("__pycache__"))
            (project / "selection.py").write_text(
                "def select_tasks(tasks, status=None, limit=2):\n"
                "    if type(limit) is not int or limit <= 0:\n"
                "        raise ValueError('limit must be a positive integer')\n"
                "    first = {}\n"
                "    for task in tasks:\n"
                "        if status is None or task['status'] == status:\n"
                "            first.setdefault(task['id'], task)\n"
                "    return sorted(first.values(), key=lambda t: -t.get('priority', 0))[:limit]\n")
            run = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover",
                 "-s", "tests", "-p", "test_selection.py"],
                cwd=project, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Ran 12 tests", run.stderr)


if __name__ == "__main__":
    unittest.main()
