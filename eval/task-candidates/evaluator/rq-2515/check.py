"""Upstream regression (unchanged method body) and two upstream controls.

Source: rq/rq f114a649c57555bed3100b7b1e00fb8996d5d1d5,
tests/test_queue.py. BSD-2-Clause; see ../../licenses/rq-LICENSE.
Only imports/class wrapper and control method aliases are adapted.
"""

from rq import Queue
from rq.job import Job, JobStatus
from tests import RQTestCase
from tests.fixtures import say_hello
from tests import test_queue


class TestOneShot(RQTestCase):
    test_enqueue_many_internal_pipeline = test_queue.TestQueue.test_enqueue_many_internal_pipeline
    test_enqueue_many_with_passed_pipeline = test_queue.TestQueue.test_enqueue_many_with_passed_pipeline

    def test_enqueue_many_with_generator(self):
        """A one-shot iterable preserves jobs with and without dependencies."""
        q = Queue(connection=self.connection)
        parent = q.enqueue(say_hello)
        job_datas = [
            Queue.prepare_data(say_hello, job_id='dependent', depends_on=parent),
            Queue.prepare_data(say_hello, job_id='independent'),
        ]

        jobs = q.enqueue_many(job_data for job_data in job_datas)

        self.assertEqual([job.id for job in jobs], ['independent', 'dependent'])
        self.assertEqual(q.job_ids, [parent.id, 'independent'])
        self.assertEqual(Job.fetch('dependent', connection=self.connection).get_status(), JobStatus.DEFERRED)
        self.assertEqual(q.deferred_job_registry.get_job_ids(), ['dependent'])
