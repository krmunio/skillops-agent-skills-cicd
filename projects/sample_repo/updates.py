"""Issue-update seed with mutation and validation defects."""


def update_issue(issues, issue_id, changes):
    for issue in issues:
        if issue["id"] == issue_id:
            issue.update(changes)
            return issues
    return issues
