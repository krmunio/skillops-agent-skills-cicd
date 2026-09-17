"""Existing-behavior checks for the intentionally defective demonstration."""

from copy import deepcopy
import unittest

from issues import list_issues
from labels import normalize_labels
from updates import update_issue


class IssueManagementTests(unittest.TestCase):
    def test_empty_issues_remain_empty(self):
        self.assertEqual(list_issues([]), [])

    def test_first_page_preserves_order(self):
        issues = [{"id": value, "status": "open"} for value in range(1, 5)]
        self.assertEqual(list_issues(issues, page=1, page_size=2), issues[:2])

    def test_filter_precedes_pagination(self):
        issues = [{"id": 1, "status": "closed"}, {"id": 2, "status": "open"},
                  {"id": 3, "status": "open"}, {"id": 4, "status": "closed"}]
        self.assertEqual(list_issues(issues, status="open", page=1, page_size=2), issues[1:3])

    def test_label_input_remains_unchanged(self):
        labels = ["Beta", "Alpha", "Beta"]
        original = list(labels)
        normalize_labels(labels)
        self.assertEqual(labels, original)

    def test_labels_normalize_whitespace_without_sorting(self):
        self.assertEqual(normalize_labels(["  Beta ", "ALPHA", " beta", ""]), ["beta", "alpha"])

    def test_update_does_not_mutate_inputs(self):
        issues = [{"id": 1, "title": "Old", "status": "open"}]
        original = deepcopy(issues)
        result = update_issue(issues, 1, {"title": "New"})
        self.assertEqual(result[0]["title"], "New")
        self.assertEqual(issues, original)


if __name__ == "__main__":
    unittest.main()
