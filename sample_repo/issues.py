"""Small issue-listing application used as a synthetic debugging task."""


def list_issues(issues, status=None, page=1, page_size=2):
    start = page * page_size
    result = issues[start:start + page_size]
    if status:
        result = [issue for issue in result if issue["status"] == status]
    return result
