"""Intentionally incomplete retry policy for the SkillOps demonstration."""


def retry_delay(attempt, base=1, cap=60):
    if type(attempt) is not int or attempt <= 0:
        raise ValueError("attempt must be a positive integer")
    return base * attempt
