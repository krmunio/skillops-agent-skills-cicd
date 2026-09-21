---
name: schedule-development
description: Use when changing Python Schedule job timing, cancellation, tags, or scheduler regression tests.
---

# Schedule development

This is an unvalidated onboarding draft, not evidence of improved model behavior.

Read the relevant implementation in `schedule/__init__.py` and neighboring tests
in `test_schedule.py`. Preserve the public API and keep the change scoped to the
requested scheduling behavior.

Use an independent `Scheduler` or clear the shared default scheduler between
tests so jobs and tags cannot leak across cases. Follow the existing
`mock_datetime` approach for deterministic due times; do not use real sleeps to
prove scheduling behavior.

Check the boundaries affected by the change: due-job ordering, next-run time,
tag filtering, cancellation, and callbacks returning `CancelJob`. Distinguish
`run_pending` from `run_all`; do not assume that missed intervals are replayed.

Timezone tests depend on platform timezone controls, and relevant cases use
optional `pytz`. The pinned upstream suite calls `time.tzset` during import, so
do not treat it as a portable Windows test command. Use an explicitly authorized
Linux test environment for the upstream suite rather than changing its tests to
hide missing dependencies.

Report the exact checks executed, selected versus full-suite coverage, and skips
or environment blockers. Source preparation and metadata checks do not establish
that upstream tests or model-backed skill evaluations passed.
