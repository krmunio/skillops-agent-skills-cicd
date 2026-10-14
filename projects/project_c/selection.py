"""Intentionally incomplete task selection for the SkillOps demonstration."""


def select_tasks(tasks, status=None, limit=2):
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    selected = tasks[:limit]
    return [task for task in selected if status is None or task["status"] == status]
