"""Intentionally incomplete failure handling for a synthetic queue.

Backend protocol: persist_failed(job), persist_retry(job), notify(job),
enqueue(job), release(job), and record_error(operation, error).
Persistence operations are atomic: BackendError means nothing was committed.
Other operations can raise BackendError independently. record_error never raises.
persist_retry stores job.retries + 1 without changing the supplied Job.
"""

from dataclasses import dataclass


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    id: str
    retries: int = 0


def finish_failure(job, backend, *, stopped=False, enqueue_dependents=True):
    errors = []
    try:
        backend.persist_failed(job)
        if not stopped:
            backend.notify(job)
        if enqueue_dependents:
            backend.enqueue(job)
        backend.release(job)
    except BackendError as error:
        backend.record_error("finish_failure", error)
        errors.append("finish_failure")
    return {"status": "failed", "errors": errors}


def finish_retry(job, backend, *, max_retries):
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("max_retries must be a nonnegative integer")
    errors = []
    try:
        if job.retries >= max_retries:
            backend.persist_failed(job)
            backend.notify(job)
            backend.enqueue(job)
            backend.release(job)
            return {"status": "failed", "errors": errors}
        backend.persist_retry(job)
        backend.release(job)
    except BackendError as error:
        backend.record_error("finish_retry", error)
        errors.append("finish_retry")
    return {"status": "retry_scheduled", "errors": errors}
