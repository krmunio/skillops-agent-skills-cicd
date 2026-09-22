# Optional GEPA search

SkillOps integrates the official [GEPA](https://github.com/gepa-ai/gepa)
optimizer, pinned to core version 0.1.4. This is an opt-in alternative to the
existing sequential development iteration, not a replacement for its evaluation
policy or human approval gate.

## Install and invoke

From the repository root, activate the virtual environment used for SkillOps:

```bash
python3 -m pip --isolated install --only-binary=:all: --require-hashes \
  --index-url https://pypi.org/simple -r skillops/requirements-gepa.txt
python3 skillops/skillops.py iterate --help
```

The following command runs models and project checks. Use it only after separately
authorizing an invocation limit, wall-time limit and per-session Credit cap,
setting the existing `SKILLOPS_*` live environment controls, and authenticating
the dedicated Copilot profile. See [operations](OPERATIONS.md).

```bash
python3 skillops/skillops.py iterate \
  --project sample_repo \
  --skill-key "$SKILL_KEY" \
  --work-item "$DEVELOPMENT_WORK_ITEM" \
  --results .skillops-private/gepa-results \
  --optimizer gepa --max-rounds 3 --live
```

`SKILL_KEY` is the discovered version-linked skill identity.
`DEVELOPMENT_WORK_ITEM` is a validated recorded WorkItem JSON path, not a free-form
prompt. The WorkItem must name 2–128 required case IDs from the same frozen project.
No new evaluation-set catalog or multiple-WorkItem scheduler is introduced.

For independent final confirmation, also supply the existing
`--confirmation-work-item` and `--confirmation-disclosure` arguments. Both are
registered before development; unsupported runtime isolation fails closed.
The default optimizer remains `sequential`. Missing or mismatched GEPA versions
fail explicitly; there is no fallback that masquerades as GEPA.

## What GEPA selects

- A candidate is the Skill instruction body. Frontmatter and companion resources
  remain unchanged, and all comparisons use the original immutable bundle.
- Each required development case is an instance: passed = 1, failed = 0.
  A failed mandatory gate gives zero on all instances. Missing, skipped, blocked
  or errored required checks stop the search as unverified rather than becoming
  zero-valued measurements.
- The original Skill is actually applied once for its seed score. Project checks
  before skill application are not substituted for this measurement.
- GEPA selects parents using its official instance-level Pareto strategy. The
  retained frontier records all pool candidates tied for best on each case.
  This is not a custom quality/cost/time Pareto algorithm.
- Proposals use SkillOps' issued development feedback and Copilot invocation
  path via GEPA's custom-proposer interface. The default external reflection
  provider is not used.
- Full-case minibatches and `improvement_or_equal` acceptance allow complementary
  candidates with equal aggregate outcomes to remain available. Recommendation
  uses GEPA's best aggregate score; ties prefer the earlier pool entry, including
  the original. No extra quality score breaks ties invisibly.

Train and validation both use these development cases. The record explicitly
labels this `development_reuse`. It is not an independent test set, a statistical
benchmark or evidence of generalization. The separately preregistered final task
is never supplied to GEPA or to development reflection.

Instruction quality, recorded usage and elapsed time remain in replay evidence.
They are not added to the GEPA score. A recommended candidate advances to
confirmation only if its unchanged SkillOps replay decision is `improved`;
task failures, regressions and existing efficiency constraints cannot be offset
by a favorable search score.

## Bounds, caching and failures

`--max-rounds` bounds proposals (1–10), not population generations. All model
calls, including seed application and reflection, share the original invocation
counter, deadline and per-session Credit cap. The cap is not a claimed aggregate
cost limit. The metric-request bound is `case_count * (1 + 3 * max_rounds)`.

One complete replay is cached per candidate within this process, and its case
scores are reused for repeated GEPA requests. A cache hit is not a fresh model
trial. Original-versus-candidate receipts, model measurements and fixed checks
remain retained separately. Stochastic repeat trials are outside this initial
integration.

GEPA 0.1.4 internally retries some reflection exceptions. SkillOps latches
provider failures so those retries cannot launch additional model calls, and
reports the original failure instead of a successful search. Existing completed
evaluations are preserved. Storage failures propagate, rather than returning
success-shaped references. Duplicate/no-change proposals stop explicitly.
Resume, candidate merging, external telemetry and automatic activation are off.

## Evidence and approval

Sequential cycles retain schema version 1. GEPA cycles use version 2 and add
an `optimizer` object containing the library version, strategy, fixed seed,
development scope, frozen reference, seed application, case scores, candidate
pool, frontier and recommendation. Each proposal still has its parent,
feedback binding and replay-evaluation reference, including proposals that GEPA
does not accept into its pool.

Python and dashboard validators verify score vectors against retained checks,
recommendations against the ordered pool, and parents against prior evaluations
and accepted pool membership.
Search completion is `search_complete`, not `improved`. The recommendation may be
the original or a candidate that is not eligible for confirmation.

The existing confirmation provider freezes the selected capture and closes
development before exposing final work. Existing local `approval-preflight`,
interactive `approve`, and separately authorized `run-approved` remain distinct.
Offline search and confirmation tests can never create approval-eligible evidence.
The dashboard shows the pool and per-case winners, but has no approval action.

## Verification boundary

CI installs the hash-pinned core optimizer and exercises the actual GEPA engine,
real SkillOps replay/storage validators, and dashboard evidence loading with
offline model transport. This validates integration, not live skill improvement.
For focused local verification:

```bash
PYTHONPATH=skillops:skillops/tests python3 -m unittest test_gepa_search test_skill_iterations -v
cd skillops
npm run test:dashboard -- --grep GEPA
```

A performance claim requires separately authorized live experiments comparing
sequential and GEPA search on the same tasks and budget over repeated runs.
