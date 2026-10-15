"""Verify the synthetic contracts without model calls or upstream execution."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import evaluation_datasets
import project_results as results


FAILURE_REPAIR = """from dataclasses import dataclass

class BackendError(RuntimeError):
    pass

@dataclass(frozen=True)
class Job:
    id: str
    retries: int = 0

def attempt(backend, job, operation, errors):
    try:
        getattr(backend, operation)(job)
    except BackendError as error:
        backend.record_error(operation, error)
        errors.append(operation)
        return False
    return True

def finish_failure(job, backend, *, stopped=False, enqueue_dependents=True):
    errors = []
    if not attempt(backend, job, 'persist_failed', errors):
        return {'status': 'persistence_failed', 'errors': errors}
    operations = ([] if stopped else ['notify'])
    operations += ['enqueue'] if enqueue_dependents else []
    for operation in operations + ['release']:
        attempt(backend, job, operation, errors)
    return {'status': 'failed', 'errors': errors}

def finish_retry(job, backend, *, max_retries):
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError('invalid max_retries')
    if job.retries >= max_retries:
        return finish_failure(job, backend)
    errors = []
    if not attempt(backend, job, 'persist_retry', errors):
        return {'status': 'persistence_failed', 'errors': errors}
    attempt(backend, job, 'release', errors)
    return {'status': 'retry_scheduled', 'errors': errors}
"""

ENQUEUE_REPAIR = """from dataclasses import dataclass

@dataclass(frozen=True)
class Item:
    id: str
    dependencies: tuple = ()

def enqueue_many(items, backend):
    ready, deferred = [], []
    for item in items:
        (deferred if item.dependencies else ready).append(item)
    for group, status in ((ready, 'queued'), (deferred, 'deferred')):
        for item in group:
            backend.save(item, status)
    return ready + deferred
"""

RETRY_REPAIR = """from email.utils import parsedate_to_datetime
import math
import re

def retry_after(value, now, cap=60):
    if (type(now) not in (int, float) or not math.isfinite(now)
            or type(cap) not in (int, float) or not math.isfinite(cap) or cap < 0):
        raise ValueError('invalid clock or cap')
    if not isinstance(value, str):
        raise ValueError('value must be a string')
    value = value.strip()
    if re.fullmatch(r'[0-9]+', value):
        if len(value) > 100:
            raise ValueError('delay has too many digits')
        return min(cap, int(value))
    date = parsedate_to_datetime(value)
    if date.tzinfo is None:
        raise ValueError('date needs a timezone')
    return min(cap, max(0, date.timestamp() - now))
"""

CHECK_SCRIPT = """import io, json, unittest
result = unittest.TextTestRunner(stream=io.StringIO()).run(
    unittest.defaultTestLoader.discover('tests'))
print(json.dumps({
    'count': result.testsRun,
    'errors': len(result.errors),
    'failed': sorted({test.id().split(' (')[0] for test, _ in result.failures}),
}))
"""


class ProjectDSampleTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[2]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "projects/project_d"
        shutil.copytree(self.repository / "projects/project_d", self.project,
                        ignore=shutil.ignore_patterns("__pycache__"))

    def checks(self):
        run = subprocess.run([sys.executable, "-B", "-c", CHECK_SCRIPT],
                             cwd=self.project, capture_output=True, text=True, check=True)
        return json.loads(run.stdout)

    def test_recipe_covers_every_case_once_and_exports_only_development(self):
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "Project D fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        path = self.root / ".skillops-private/datasets/project_d.json"
        dataset = evaluation_datasets.prepare(
            self.root, self.repository / "eval/datasets/project_d.json", path)
        _, available = evaluation_datasets._cases(self.project)
        cases = [case for task in dataset["tasks"] for case in task["checks"]["required_case_ids"]]
        self.assertEqual(len(cases), 30)
        self.assertEqual(len(set(cases)), len(cases))
        self.assertEqual(set(cases), available)
        self.assertEqual([len(task["checks"]["required_case_ids"]) for task in dataset["tasks"]],
                         [18, 5, 7])
        work = evaluation_datasets.export_development(
            self.root, path, "failure-lifecycle",
            self.root / ".skillops-private/work-items/development.json")
        self.assertEqual(set(work["sources"]), {"failure.py"})
        self.assertEqual(work["split"], "development")
        self.assertEqual(results.read_json(path), dataset)

    def test_starter_defects_are_assertions_not_execution_errors(self):
        checked = self.checks()
        self.assertEqual(checked["count"], 30)
        self.assertEqual(checked["errors"], 0)
        self.assertEqual(len(checked["failed"]), 16)
        self.assertIn("test_failure.FailureTests.test_dependency_error_does_not_skip_release",
                      checked["failed"])
        self.assertIn("test_enqueue.EnqueueTests.test_generator_matches_list", checked["failed"])
        self.assertIn("test_retry.RetryTests.test_numeric_cap", checked["failed"])

    def test_reference_repairs_satisfy_all_roles_without_test_changes(self):
        protected = {path: path.read_bytes() for path in (self.project / "tests").glob("*.py")}
        for name, content in (("failure.py", FAILURE_REPAIR), ("enqueue.py", ENQUEUE_REPAIR),
                              ("retry.py", RETRY_REPAIR)):
            (self.project / name).write_text(content)
        self.assertEqual(self.checks(), {"count": 30, "errors": 0, "failed": []})
        self.assertTrue(all(path.read_bytes() == raw for path, raw in protected.items()))


if __name__ == "__main__":
    unittest.main()
