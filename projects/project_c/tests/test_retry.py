import unittest

from retry import retry_delay


class RetryTests(unittest.TestCase):
    def test_exponential_delay(self):
        self.assertEqual([retry_delay(attempt, base=2) for attempt in (1, 2, 3, 4)], [2, 4, 8, 16])

    def test_cap(self):
        self.assertEqual(retry_delay(8, base=2, cap=10), 10)

    def test_invalid_attempt(self):
        for attempt in (True, 0, -1, 1.5, "2"):
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                retry_delay(attempt)
