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

    def test_descending_priority_before_limit(self):
        tasks = [{"id": 1, "status": "pending", "priority": -2},
                 {"id": 2, "status": "pending", "priority": 8},
                 {"id": 3, "status": "pending", "priority": 3}]
        self.assertEqual(select_tasks(tasks, limit=2), [tasks[1], tasks[2]])

    def test_missing_priority_is_zero(self):
        tasks = [{"id": 1, "status": "pending", "priority": -1},
                 {"id": 2, "status": "pending"},
                 {"id": 3, "status": "pending", "priority": 0},
                 {"id": 4, "status": "pending", "priority": 1}]
        self.assertEqual(select_tasks(tasks, limit=4),
                         [tasks[3], tasks[1], tasks[2], tasks[0]])

    def test_equal_priority_preserves_input_order(self):
        tasks = [{"id": 9, "status": "pending", "priority": 2},
                 {"id": 3, "status": "pending", "priority": 2},
                 {"id": 7, "status": "pending", "priority": 2}]
        self.assertEqual(select_tasks(tasks, limit=2), tasks[:2])

    def test_first_eligible_duplicate_wins_before_sort(self):
        tasks = [{"id": 1, "status": "pending", "priority": 1},
                 {"id": 1, "status": "pending", "priority": 99},
                 {"id": 2, "status": "pending", "priority": 2}]
        self.assertEqual(select_tasks(tasks, limit=3), [tasks[2], tasks[0]])

    def test_filter_precedes_deduplication(self):
        tasks = [{"id": 1, "status": "done", "priority": 99},
                 {"id": 1, "status": "pending", "priority": 1},
                 {"id": 2, "status": "pending", "priority": 0}]
        self.assertEqual(select_tasks(tasks, status="pending", limit=3), tasks[1:])

    def test_limit_applies_after_deduplication(self):
        tasks = [{"id": 1, "status": "pending"},
                 {"id": 1, "status": "pending"},
                 {"id": 2, "status": "pending"},
                 {"id": 3, "status": "pending"}]
        self.assertEqual(select_tasks(tasks, limit=2), [tasks[0], tasks[2]])

    def test_combined_rules_preserve_nested_input(self):
        tasks = [{"id": 1, "status": "done", "priority": 100},
                 {"id": 1, "status": "pending", "priority": 2, "meta": {"tags": ["a"]}},
                 {"id": 2, "status": "pending", "priority": 4},
                 {"id": 1, "status": "pending", "priority": 50},
                 {"id": 3, "status": "pending", "priority": 4},
                 {"id": 4, "status": "pending"}]
        before = deepcopy(tasks)
        self.assertEqual(select_tasks(tasks, status="pending", limit=3),
                         [before[2], before[4], before[1]])
        self.assertEqual(tasks, before)

    def test_empty_and_unmatched_status(self):
        tasks = [{"id": 1, "status": "Pending", "priority": 1}]
        self.assertEqual(select_tasks([], limit=1), [])
        self.assertEqual(select_tasks(tasks, status="pending"), [])
        self.assertEqual(select_tasks(tasks, status=""), [])

    def test_large_limit_and_none_status(self):
        tasks = [{"id": 1, "status": "pending"},
                 {"id": 2, "status": "done"}]
        self.assertEqual(select_tasks(tasks, status=None, limit=100), tasks)
