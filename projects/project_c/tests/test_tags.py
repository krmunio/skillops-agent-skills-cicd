import unittest

from tags import normalize_tags


class TagTests(unittest.TestCase):
    def test_canonical_order(self):
        self.assertEqual(normalize_tags([" Beta ", "ALPHA", "beta", ""]), ["beta", "alpha"])

    def test_input_is_unchanged(self):
        tags = ["B", "A", "B"]
        normalize_tags(tags)
        self.assertEqual(tags, ["B", "A", "B"])

    def test_invalid_input(self):
        for tags in ("tag", None, ["valid", 3]):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                normalize_tags(tags)
