"""Intentionally incomplete Retry-After parsing; this function never sleeps."""

from email.utils import parsedate_to_datetime
import math
import re


def retry_after(value, now, cap=60):
    if (type(now) not in (int, float) or not math.isfinite(now)
            or type(cap) not in (int, float) or not math.isfinite(cap) or cap < 0):
        raise ValueError("now and cap must be finite numbers; cap must be nonnegative")
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    date = parsedate_to_datetime(value)
    if date.tzinfo is None:
        raise ValueError("date must have an explicit timezone")
    return date.timestamp() - now
