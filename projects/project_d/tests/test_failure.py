from itertools import combinations
import unittest

from failure import BackendError, Job, finish_failure, finish_retry


class MemoryBackend:
    def __init__(self, faults=None):
        self.faults = faults or {}
        self.events = []
        self.errors = []
        self.saved = {}
        self.released = False

    def _event(self, operation):
        self.events.append(operation)
        if operation in self.faults:
            raise self.faults[operation]

    def persist_failed(self, job):
        self._event("persist_failed")
        self.saved[job.id] = ("failed", job.retries)

    def persist_retry(self, job):
        self._event("persist_retry")
        self.saved[job.id] = ("queued", job.retries + 1)

    def notify(self, job):
        self._event("notify")

    def enqueue(self, job):
        self._event("enqueue")

    def release(self, job):
        self._event("release")
        self.released = True

    def record_error(self, operation, error):
        self.events.append("log:" + operation)
        self.errors.append((operation, error))


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.job = Job("job-1", retries=2)

    def backend(self, *faults):
        return MemoryBackend({name: BackendError(name) for name in faults})

    def test_commit_failure_blocks_side_effects(self):
        backend = self.backend("persist_failed")
        result = finish_failure(self.job, backend)
        self.assertEqual(result, {"status": "persistence_failed", "errors": ["persist_failed"]})
        self.assertEqual(backend.events, ["persist_failed", "log:persist_failed"])
        self.assertEqual(backend.saved, {})
        self.assertFalse(backend.released)
        self.assertIs(backend.errors[0][1], backend.faults["persist_failed"])

    def test_dependency_error_does_not_skip_release(self):
        backend = self.backend("enqueue")
        result = finish_failure(self.job, backend)
        self.assertEqual(result, {"status": "failed", "errors": ["enqueue"]})
        self.assertTrue(backend.released)
        self.assertEqual(backend.events,
                         ["persist_failed", "notify", "enqueue", "log:enqueue", "release"])

    def test_error_order(self):
        backend = self.backend("notify", "enqueue", "release")
        result = finish_failure(self.job, backend)
        self.assertEqual(result, {"status": "failed", "errors": ["notify", "enqueue", "release"]})
        self.assertEqual([name for name, _ in backend.errors], result["errors"])
        self.assertEqual(backend.saved[self.job.id], ("failed", 2))

    def test_exhausted_retry_uses_failure_rules(self):
        backend = self.backend()
        self.assertEqual(finish_retry(self.job, backend, max_retries=2),
                         {"status": "failed", "errors": []})
        self.assertEqual(backend.events, ["persist_failed", "notify", "enqueue", "release"])

    def test_exhausted_retry_with_faults(self):
        for operation in ("persist_failed", "notify", "enqueue", "release"):
            with self.subTest(operation=operation):
                backend = self.backend(operation)
                result = finish_retry(self.job, backend, max_retries=1)
                expected = "persistence_failed" if operation == "persist_failed" else "failed"
                self.assertEqual(result, {"status": expected, "errors": [operation]})
                if operation not in ("persist_failed", "release"):
                    self.assertTrue(backend.released)

    def test_input_job_unchanged(self):
        backend = self.backend()
        finish_retry(self.job, backend, max_retries=3)
        self.assertEqual(self.job, Job("job-1", retries=2))
        self.assertEqual(backend.saved[self.job.id], ("queued", 3))

    def test_invalid_retry_limit(self):
        for limit in (True, False, -1, 2.0, "2", None):
            with self.subTest(limit=limit):
                backend = self.backend()
                with self.assertRaises(ValueError):
                    finish_retry(self.job, backend, max_retries=limit)
                self.assertEqual(backend.events, [])

    def test_notification_error_isolated(self):
        backend = self.backend("notify")
        result = finish_failure(self.job, backend)
        self.assertEqual(result, {"status": "failed", "errors": ["notify"]})
        self.assertEqual(backend.events,
                         ["persist_failed", "notify", "log:notify", "enqueue", "release"])

    def test_optional_dependencies(self):
        backend = self.backend("enqueue")
        self.assertEqual(finish_failure(self.job, backend, enqueue_dependents=False),
                         {"status": "failed", "errors": []})
        self.assertEqual(backend.events, ["persist_failed", "notify", "release"])

    def test_partial_side_effect_matrix(self):
        operations = ("persist_failed", "notify", "enqueue", "release")
        for size in range(len(operations) + 1):
            for faults in combinations(operations, size):
                for stopped, dependents in ((False, True), (True, True), (False, False), (True, False)):
                    with self.subTest(faults=faults, stopped=stopped, dependents=dependents):
                        backend = self.backend(*faults)
                        result = finish_failure(self.job, backend, stopped=stopped,
                                                enqueue_dependents=dependents)
                        if "persist_failed" in faults:
                            active = ["persist_failed"]
                            status = "persistence_failed"
                        else:
                            active = (["persist_failed"] + ([] if stopped else ["notify"])
                                      + (["enqueue"] if dependents else []) + ["release"])
                            status = "failed"
                        errors = [name for name in active if name in faults]
                        events = []
                        for name in active:
                            events.append(name)
                            if name in faults:
                                events.append("log:" + name)
                        self.assertEqual(result, {"status": status, "errors": errors})
                        self.assertEqual(backend.events, events)
                        self.assertEqual([name for name, _ in backend.errors], errors)
                        self.assertEqual(backend.released,
                                         "persist_failed" not in faults and "release" not in faults)

    def test_release_error_reported(self):
        backend = self.backend("release")
        self.assertEqual(finish_failure(self.job, backend),
                         {"status": "failed", "errors": ["release"]})
        self.assertEqual(backend.events[-2:], ["release", "log:release"])

    def test_retry_commit_failure(self):
        backend = self.backend("persist_retry")
        self.assertEqual(finish_retry(self.job, backend, max_retries=3),
                         {"status": "persistence_failed", "errors": ["persist_retry"]})
        self.assertEqual(backend.events, ["persist_retry", "log:persist_retry"])
        self.assertEqual(backend.saved, {})

    def test_retry_scheduled_without_terminal_actions(self):
        backend = self.backend()
        self.assertEqual(finish_retry(self.job, backend, max_retries=3),
                         {"status": "retry_scheduled", "errors": []})
        self.assertEqual(backend.events, ["persist_retry", "release"])
        self.assertEqual(backend.saved[self.job.id], ("queued", 3))

    def test_retry_slot_error(self):
        backend = self.backend("release")
        self.assertEqual(finish_retry(self.job, backend, max_retries=3),
                         {"status": "retry_scheduled", "errors": ["release"]})
        self.assertEqual(backend.events, ["persist_retry", "release", "log:release"])

    def test_stopped_suppresses_notification(self):
        backend = self.backend("notify")
        self.assertEqual(finish_failure(self.job, backend, stopped=True),
                         {"status": "failed", "errors": []})
        self.assertEqual(backend.events, ["persist_failed", "enqueue", "release"])

    def test_successful_failure_order(self):
        backend = self.backend()
        self.assertEqual(finish_failure(self.job, backend), {"status": "failed", "errors": []})
        self.assertEqual(backend.events, ["persist_failed", "notify", "enqueue", "release"])

    def test_unexpected_error_propagates(self):
        for operation in ("persist_failed", "notify", "enqueue", "release"):
            with self.subTest(operation=operation):
                backend = MemoryBackend({operation: ValueError("programming error")})
                with self.assertRaises(ValueError):
                    finish_failure(self.job, backend)
                self.assertEqual(backend.errors, [])

    def test_zero_retry_limit(self):
        backend = self.backend()
        self.assertEqual(finish_retry(Job("new"), backend, max_retries=0),
                         {"status": "failed", "errors": []})
        self.assertEqual(backend.events, ["persist_failed", "notify", "enqueue", "release"])
