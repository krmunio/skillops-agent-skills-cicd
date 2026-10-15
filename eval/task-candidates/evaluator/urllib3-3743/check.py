"""New evaluator checks, not upstream tests. No real sleeping or HTTP calls."""

from email.utils import formatdate
from unittest.mock import patch

import pytest

from urllib3.exceptions import InvalidHeader
from urllib3.response import HTTPResponse
from urllib3.util.retry import Retry


def test_numeric_cap_and_clone():
    retry = Retry()
    assert retry.parse_retry_after("9999999999") == 21600
    for value in (0, 1, 21599, 21600, 21601):
        assert retry.parse_retry_after(str(value)) == min(value, 21600)
    custom = Retry(total=3, retry_after_max=7)
    for clone in (custom, custom.new(), custom.increment(method="GET")):
        assert clone.parse_retry_after("9999999999") == 7
        assert clone.parse_retry_after("6") == 6


def test_http_date_cap():
    now = 1700000000
    with patch("urllib3.util.retry.time.time", return_value=now):
        assert Retry().parse_retry_after(formatdate(now + 999999, usegmt=True)) == 21600
        assert Retry().parse_retry_after(formatdate(now - 1, usegmt=True)) == 0
        assert Retry(retry_after_max=7).parse_retry_after(formatdate(now + 10, usegmt=True)) == 7


def test_sleep_receives_bounded_duration():
    response = HTTPResponse(status=503, headers={"Retry-After": "9999999999"})
    with patch("urllib3.util.retry.time.sleep") as sleep:
        assert Retry().sleep_for_retry(response) is True
        sleep.assert_called_once_with(21600)


def test_existing_header_semantics():
    retry = Retry()
    assert retry.parse_retry_after(" 42 ") == 42
    assert retry.parse_retry_after("0") == 0
    with pytest.raises(InvalidHeader):
        retry.parse_retry_after("not-a-date")
    with patch("urllib3.util.retry.time.sleep") as sleep:
        for headers in ({}, {"Retry-After": "0"}):
            assert retry.sleep_for_retry(HTTPResponse(status=503, headers=headers)) is False
        sleep.assert_not_called()
