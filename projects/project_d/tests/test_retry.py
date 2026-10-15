import unittest

from retry import retry_after


class RetryTests(unittest.TestCase):
    def test_date_cap(self):
        self.assertEqual(retry_after("Thu, 01 Jan 1970 00:02:00 GMT", now=0, cap=30), 30)

    def test_digit_length_bound(self):
        self.assertEqual(retry_after("9" * 100, now=0, cap=60), 60)
        with self.assertRaises(ValueError):
            retry_after("9" * 101, now=0)

    def test_invalid_input(self):
        for value in (None, "", "-1", "1.5", "tomorrow"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                retry_after(value, now=0)

    def test_invalid_limits(self):
        for now, cap in ((True, 10), (0, -1), (0, False), (float("nan"), 10), (0, float("inf"))):
            with self.subTest(now=now, cap=cap), self.assertRaises(ValueError):
                retry_after("1", now=now, cap=cap)

    def test_numeric_cap(self):
        self.assertEqual(retry_after("9999999999", now=0, cap=60), 60)
        self.assertEqual(retry_after(" 2 ", now=0, cap=60), 2)

    def test_past_date(self):
        self.assertEqual(retry_after("Thu, 01 Jan 1970 00:00:00 GMT", now=10), 0)

    def test_zero_cap(self):
        self.assertEqual(retry_after("12", now=0, cap=0), 0)
