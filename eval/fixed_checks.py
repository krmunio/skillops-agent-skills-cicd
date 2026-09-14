"""Host-owned input and expected-value checks; never run candidate code here."""

from copy import deepcopy

from copilot_runtime import RuntimeFailure


def _listing_cases():
    rows = [
        {"id": number, "title": f"Issue {number}", "status": "open" if number % 2 else "closed"}
        for number in range(1, 6)
    ]
    definitions = [
        ("first-page", "pagination", {}, [rows[0], rows[1]]),
        ("second-page", "pagination", {"page": 2}, [rows[2], rows[3]]),
        ("final-page", "pagination", {"page": 3}, [rows[4]]),
        ("size-one-first", "pagination", {"page_size": 1}, [rows[0]]),
        ("size-one-second", "pagination", {"page_size": 1, "page": 2}, [rows[1]]),
        ("size-three-first", "pagination", {"page_size": 3}, [rows[0], rows[1], rows[2]]),
        ("size-three-second", "pagination", {"page_size": 3, "page": 2}, [rows[3], rows[4]]),
        ("filter-first", "filter", {"status": "open"}, [rows[0], rows[2]]),
        ("filter-second", "filter", {"status": "open", "page": 2}, [rows[4]]),
        ("filter-closed", "filter", {"status": "closed"}, [rows[1], rows[3]]),
        ("filter-size-one-second", "filter", {"status": "open", "page_size": 1, "page": 2}, [rows[2]]),
        ("filter-size-three-first", "filter", {"status": "open", "page_size": 3}, [rows[0], rows[2], rows[4]]),
        ("unknown-filter", "filter", {"status": "missing"}, []),
        ("empty-filter", "filter", {"status": ""}, []),
        ("beyond-end", "boundary", {"page": 10}, []),
        ("empty-input", "boundary", {"issues": []}, []),
    ]
    result = [
        {"id": name, "category": category, "kwargs": {"issues": deepcopy(rows), **deepcopy(args)},
         "expected": deepcopy(expected), "exception": None}
        for name, category, args, expected in definitions
    ]
    for key in ("page", "page_size"):
        for index, value in enumerate((0, -1, True, 1.5, "1")):
            result.append({
                "id": f"invalid-{key}-{index}", "category": "validation",
                "kwargs": {"issues": deepcopy(rows), key: value},
                "expected": None, "exception": "ValueError",
            })
    return result


def _case(name, category, kwargs, expected=None, exception=None):
    return {"id": name, "category": category, "kwargs": deepcopy(kwargs),
            "expected": deepcopy(expected), "exception": exception}


def _label_cases():
    values = [
        ("mixed", ["  Bug ", "bug", "HELP Wanted", "help  wanted"], ["bug", "help wanted"]),
        ("empty", [], []),
        ("order", ["Z", "a", "B"], ["z", "a", "b"]),
        ("blanks", ["", " ", "\t\n"], []),
        ("internal-space", ["needs   review", " needs\tReview "], ["needs review"]),
        ("casefold", ["Stra\u00dfe", "STRASSE"], ["strasse"]),
        ("accented-case", ["\u00c9tat", "\u00e9tat"], ["\u00e9tat"]),
        ("single", ["One"], ["one"]),
    ]
    result = [_case(name, "normalization", {"labels": labels}, expected)
              for name, labels, expected in values]
    for index, value in enumerate((None, 7, True, [], {})):
        result.append(_case(f"invalid-entry-{index}", "validation", {"labels": ["ok", value]}, exception="ValueError"))
    for index, value in enumerate((None, "bug", {}, 7)):
        result.append(_case(f"invalid-container-{index}", "validation", {"labels": value}, exception="ValueError"))
    return result


def _update_cases():
    rows = [
        {"id": 1, "title": "One", "status": "open"},
        {"id": 2, "title": "Two", "status": "closed"},
        {"id": 3, "title": "Three", "status": "open"},
    ]
    valid = [
        ("status", {"issue_id": 2, "changes": {"status": "open"}},
         [rows[0], {**rows[1], "status": "open"}, rows[2]]),
        ("title", {"changes": {"title": "Renamed"}}, [{**rows[0], "title": "Renamed"}, rows[1], rows[2]]),
        ("both", {"issue_id": 3, "changes": {"title": "Fixed", "status": "closed"}},
         [rows[0], rows[1], {**rows[2], "title": "Fixed", "status": "closed"}]),
        ("empty-patch", {"changes": {}}, rows),
        ("order", {"issues": [rows[2], rows[0], rows[1]], "changes": {"status": "closed"}},
         [rows[2], {**rows[0], "status": "closed"}, rows[1]]),
        ("title-whitespace", {"changes": {"title": " New "}}, [{**rows[0], "title": " New "}, rows[1], rows[2]]),
    ]
    defaults = {"issues": rows, "issue_id": 1, "changes": {"status": "closed"}}
    result = [_case(name, "update", {**defaults, **kwargs}, expected) for name, kwargs, expected in valid]
    invalid = [
        ("unknown-id", {"issue_id": 99}),
        ("forbidden-id", {"changes": {"id": 4}}),
        ("unknown-key", {"changes": {"priority": "high"}}),
        ("bad-status", {"changes": {"status": "pending"}}),
        ("null-status", {"changes": {"status": None}}),
        ("empty-title", {"changes": {"title": ""}}),
        ("blank-title", {"changes": {"title": " "}}),
        ("nonstring-title", {"changes": {"title": 7}}),
        ("boolean-id", {"issue_id": True}),
        ("zero-id", {"issue_id": 0}),
        ("string-id", {"issue_id": "1"}),
        ("null-patch", {"changes": None}),
        ("list-patch", {"changes": []}),
        ("atomic-invalid", {"changes": {"title": "Valid", "status": "pending"}}),
        ("empty-issues", {"issues": []}),
        ("float-id", {"issue_id": 1.0}),
    ]
    result.extend(_case(name, "validation", {**defaults, **kwargs}, exception="ValueError") for name, kwargs in invalid)
    return result


def cases(family="listing"):
    builders = {"listing": _listing_cases, "labels": _label_cases, "updates": _update_cases}
    if not isinstance(family, str) or family not in builders:
        raise RuntimeFailure("invalid_family", "Unknown protected-check family.")
    return builders[family]()
