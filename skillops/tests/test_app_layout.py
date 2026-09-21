"""Repository/application boundary contracts; no model execution."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "skillops"


class AppLayoutTests(unittest.TestCase):
    def test_entrypoints_are_discoverable_from_both_documented_directories(self):
        for cwd in (ROOT, APP):
            for name in ("skillops.py", "project_results.py", "project_evaluation.py",
                         "project_samples.py", "reviewed_publication.py"):
                with self.subTest(cwd=cwd, entrypoint=name):
                    entrypoint = APP / name
                    self.assertTrue(entrypoint.is_file(), str(entrypoint))
                    command = [sys.executable, str(entrypoint.relative_to(cwd)), "--help"]
                    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertIn("usage:", completed.stdout)

    def test_catalog_identity_is_independent_of_invocation_directory(self):
        self.assertTrue((APP / "project_results.py").is_file())
        catalogs = []
        for cwd in (ROOT, APP):
            completed = subprocess.run(
                [sys.executable, str((APP / "project_results.py").relative_to(cwd)), "catalog"],
                cwd=cwd, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            catalogs.append(json.loads(completed.stdout))
        self.assertEqual(catalogs[0], catalogs[1])
        self.assertIn("project-a", [row["id"] for row in catalogs[0]])

    def test_cli_private_operations_receive_repository_root(self):
        import skillops
        for cwd in (ROOT, APP):
            self.assertTrue(cwd.is_dir())
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.object(sys, "argv", ["skillops.py", "repositories"]), \
                        patch("repositories.list_repositories", return_value=[]) as listing, \
                        redirect_stdout(io.StringIO()):
                    skillops.main()
                listing.assert_called_once_with(ROOT)
            finally:
                os.chdir(previous)

    def test_runtime_material_and_fingerprint_paths(self):
        import project_results
        import copilot_runtime
        self.assertEqual(Path(copilot_runtime.__file__).resolve().parent, APP)
        self.assertTrue((APP / "package-lock.json").is_file())
        self.assertTrue((APP / "requirements-evaluator.txt").is_file())
        self.assertTrue((APP / "project_templates").is_dir())
        self.assertTrue((APP / "dashboard/index.html").is_file())
        for name in project_results.CORE:
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertTrue((ROOT / "eval/skill-guide-rubric.json").is_file())
        self.assertFalse((APP / ".skillops").exists())
        self.assertFalse((APP / ".skillops-private").exists())

    def test_default_results_are_repository_relative_not_cwd_relative(self):
        import project_results
        self.assertTrue(APP.is_dir())
        previous = Path.cwd()
        try:
            os.chdir(APP)
            for arguments, expected in ((["index"], ROOT / "results"),
                                        (["index", "--results", "custom"], Path("custom"))):
                with patch.object(sys, "argv", ["project_results.py", *arguments]), \
                        patch.object(project_results, "reindex", return_value={}) as reindex, \
                        redirect_stdout(io.StringIO()):
                    project_results.main()
                reindex.assert_called_once_with(ROOT, expected)
        finally:
            os.chdir(previous)
