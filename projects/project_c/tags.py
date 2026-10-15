"""Intentionally incomplete tag normalization for the SkillOps demonstration."""


def normalize_tags(tags):
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    return sorted(set(tags))
