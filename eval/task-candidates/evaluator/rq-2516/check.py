"""New fault-injection integration checks; PR #2516 added no tests.

Real RQ objects and isolated Redis; only failure boundaries/webhooks are mocked.
API usage follows upstream tests/test_retry.py and tests/test_rate_limit.py.
BSD-2-Clause reference notice: ../../licenses/rq-LICENSE.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from redis import Redis
from redis.exceptions import ConnectionError

from rq import Queue, Retry, Worker
from rq.executions import Execution
from rq.job import JobStatus
from rq.rate_limit import RateLimit, RateLimitRegistry
from rq.registry import StartedJobRegistry
from rq.worker.base import release_slot


@pytest.mark.parametrize("path", ["failure", "retry_exhausted"])
@pytest.mark.parametrize("fault", ["none", "dependents", "persist", "persist_once", "release"])
def test_terminal_failure_contract(path, fault):
    connection = Redis(host="127.0.0.1", port=6379, db=0)
    connection.flushdb()
    queue = Queue(connection=connection)
    job = queue.enqueue("builtins.str", 1, rate_limit=RateLimit(key="contract", concurrency=1))
    worker = Worker([queue], connection=connection)
    worker.register_birth()
    job.started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job.ended_at = job.started_at + timedelta(seconds=1)
    job.number_of_retries = 1
    job.set_status(JobStatus.STARTED)
    with connection.pipeline() as pipeline:
        execution = Execution.create(job, ttl=600, pipeline=pipeline, worker_name=worker.name)
        pipeline.execute()
    worker.execution = execution
    registry = RateLimitRegistry(key="contract", connection=connection)
    assert job.id in registry.get_allowed_job_ids()
    events = []
    real_pipeline = connection.pipeline()
    real_execute = real_pipeline.execute

    def persist():
        events.append("persist")
        if fault == "persist" or (fault == "persist_once" and events.count("persist") == 1):
            # Match redis-py's finally/reset: unsent transaction commands are discarded.
            real_pipeline.reset()
            raise ConnectionError("injected persistence failure")
        return real_execute()

    def dependents(_job):
        events.append("dependents")
        assert job.get_status() == JobStatus.FAILED
        if fault == "dependents":
            raise RuntimeError("injected dependent enqueue failure")

    def release(_job):
        events.append("release")
        assert job.get_status() == JobStatus.FAILED
        if fault == "release":
            raise RuntimeError("injected slot release failure")
        return release_slot(_job)

    with (
        patch.object(connection, "pipeline", return_value=real_pipeline),
        patch.object(real_pipeline, "execute", side_effect=persist),
        patch.object(queue, "enqueue_dependents", side_effect=dependents),
        patch("rq.worker.base.release_slot", side_effect=release),
        patch.object(job, "send_webhooks", side_effect=lambda *a, **kw: events.append("webhook")),
        patch.object(worker.log, "exception") as log_exception,
        patch.object(worker.log, "error") as log_error,
    ):
        if path == "failure":
            worker.handle_job_failure(job, queue, exc_string="injected job failure", execution=execution)
        else:
            worker.handle_job_retry(
                job, queue, Retry(max=1), StartedJobRegistry(connection=connection), execution
            )

    if fault in ("persist", "persist_once"):
        assert not set(events) & {"webhook", "dependents", "release"}, events
        assert job.get_status() == JobStatus.STARTED
        assert job.id in registry.get_allowed_job_ids()
        assert job.id not in queue.failed_job_registry.get_job_ids()
        assert job.latest_result() is None
    else:
        assert job.get_status() == JobStatus.FAILED
        assert job.id in queue.failed_job_registry.get_job_ids()
        assert job.latest_result() is not None
        assert (job.id in registry.get_allowed_job_ids()) is (fault == "release"), f"slot leaked: {events}"
        assert "release" in events, f"slot release was skipped: {events}"
        assert events.index("persist") < events.index("webhook") < events.index("dependents")
    if fault != "none":
        assert log_exception.called or log_error.called, "fault must be logged"
    connection.flushdb()
    connection.close()
