from copy import deepcopy
import unittest

from selection import select_tasks


class SelectionTests(unittest.TestCase):
    def test_filter_before_limit(self):
        tasks = [{"id": 1, "status": "done"}, {"id": 2, "status": "pending"},
                 {"id": 3, "status": "pending"}]
        self.assertEqual(select_tasks(tasks, "pending", 2), tasks[1:])

    def test_preserve_input_and_order(self):
        tasks = [{"id": 3, "status": "pending"}, {"id": 1, "status": "done"}]
        before = deepcopy(tasks)
        self.assertEqual(select_tasks(tasks, limit=1), tasks[:1])
        self.assertEqual(tasks, before)

    def test_invalid_limit(self):
        for limit in (True, 0, -1, 1.5, "2"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                select_tasks([], limit=limit)
