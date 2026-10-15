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

By default, train and validation both use these development cases. The record explicitly
labels this `development_reuse`. It is not an independent test set, a statistical
benchmark or evidence of generalization. The separately preregistered final task
is never supplied to GEPA or to development reflection.

Instruction quality, recorded usage and elapsed time remain in replay evidence.
They are not added to the GEPA score. A recommended candidate advances to
confirmation only if its unchanged SkillOps replay decision is `improved`;
task failures, regressions and existing efficiency constraints cannot be offset
by a favorable search score.

## Independent validation

Explicitly select **one validation task from the same pinned dataset**:

```bash
python3 skillops/evaluation_datasets.py prepare \
  --spec eval/datasets/project_d.json \
  --output .skillops-private/datasets/project-d-pinned.json
python3 skillops/evaluation_datasets.py export-development \
  --dataset .skillops-private/datasets/project-d-pinned.json \
  --task "$DEVELOPMENT_TASK_ID" \
  --output .skillops-private/work-items/project-d-development.json

# LIVE example, not an offline check: requires separate authorization and runtime prerequisites.
python3 skillops/skillops.py iterate \
  --project project_d --skill-key "$SKILL_KEY" \
  --work-item .skillops-private/work-items/project-d-development.json \
  --validation-dataset .skillops-private/datasets/project-d-pinned.json \
  --validation-task "$VALIDATION_TASK_ID" \
  --results .skillops-private/gepa-validation-results \
  --optimizer gepa --max-rounds 3 --live
```

The project_d recipe in this example is supplied by its dataset owner; this feature
does not create or change that project or recipe. Substitute another committed
project/recipe when needed. Commit all project and Skill changes **before**
preparing the dataset. Admission requires the current repository HEAD and project
tree to match the pinned dataset. After integration or source changes, prepare
new private inputs rather than rewriting old evidence.

Both validation options are required together and are GEPA-only. The development
WorkItem must exactly match a development member of that dataset. The selected
task must be validation; confirmation is never relabeled. Dataset validation
rejects cross-project/version drift, duplicate normalized requests/task IDs, and
cross-split family, source-path or case-ID overlap. Invalid explicit inputs fail
before model invocation, with no fallback to development scores.

The internally retained validation WorkItem has `schema_version: 2` and
`split: "validation"`, with the same task/source/check/hash fields as version 1.
Version 1 remains limited to development/confirmation. There is no generic
validation-to-development export. `prepare_replay`, standalone `execute_work`,
and feedback issuance continue to reject validation as development.

GEPA receives development case IDs as its trainset and validation case IDs
(1–128) as its valset. Development scores still govern proposal acceptance;
independent validation scores govern Pareto parent selection, frontier and final
recommendation **within the accepted pool**. The original Skill is included:
ties favor the earlier entry, including the original. A validation winner with
a rejected/unverified development decision is a recommendation only; it does not
advance to confirmation. Validation is reused adaptively for selection, not an
untouched final benchmark.

Every validation Skill application starts from the original frozen project tree,
not a development output. Original and candidates receive the same validation
request, source allowlist, check plan/protected files and pinned prepared images.
Their outputs are applied to separate project copies. Full-project checks still
run; the score uses only validation-required cases and mandatory gates. Each
Skill version's result is cached once within the cycle; it is not a repeated
stochastic trial.

### Isolation and feedback boundary

Independent validation requires the existing verified Docker model-process
boundary from **before the first model call**, even without confirmation.
The pinned Node slim image has no OS CA store. Native CLI invocations receive
`SSL_CERT_FILE` pointing to a per-container PEM bundle built from that same
image's bundled Node public roots. TLS verification remains enabled; no host
certificate directory, credential profile or additional filesystem is mounted.
It uses fresh per-invocation homes/workspaces; only the selected Skill is mounted
for developer calls. Repository/private artifacts, other calls' logs, checker
files and outputs are not mounted into model processes. Unsupported isolation
fails closed; an offline fake capability is not proof of live isolation.

Validation requests/sources go only to validation developer calls. Validation
answers, checker files and detailed observations stay in the checker/private
evidence path. Validation evaluation never issues development feedback or
reflection trajectories, even when GEPA requests traces. Generators receive
only issued development feedback and immutable Skill content. The existing
feedback validator also rejects any development decision that cannot be
reproduced from visible development checks, rather than leaking hidden-check
regression details. Explicit normalized validation-request copies in development
requests/sources or the original Skill bundle are rejected at admission.

These checks do **not** prove semantic independence, prevent an operator from
embedding paraphrased answers in visible inputs, establish network/provider
isolation, or prevent adaptive overfitting through selection scores. Review the
dataset and visible Skill/source content. No real model, live Docker boundary,
or generalization experiment was run for this feature; fake transport/boundary
tests exercise orchestration, prompts, budgets and evidence only.

Confirmation remains preregistered and separately consumed after selection. When
used together, its WorkItem must also be a confirmation member of the same
dataset, and its reviewed disclosure's `model_visible_files` must include
**development + validation + confirmation sources and the original Skill bundle**.
Everything else remains `checker_only_files`. An old disclosure that classifies
validation sources as checker-only is rejected. No validation success creates
confirmation evidence, an approval, an active version or publication.

## Bounds, caching and failures

`--max-rounds` bounds proposals (1–10), not population generations. All model
calls, including seed application and reflection, share the original invocation
counter, deadline and per-session Credit cap. The cap is not a claimed aggregate
cost limit. The default metric-request bound is `case_count * (1 + 3 * max_rounds)`.
Independent validation uses `development_case_count * (1 + 3 * max_rounds) +
validation_case_count * (1 + max_rounds)`. Validation calls share the same model
counter, deadline and per-session Credit cap; no extra budget is created.

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

Sequential cycles retain schema version 1. Development-reuse GEPA cycles use version 2 and add
an `optimizer` object containing the library version, strategy, fixed seed,
development scope, frozen reference, seed application, case scores, candidate
pool, frontier and recommendation. Each proposal still has its parent,
feedback binding and replay-evaluation reference, including proposals that GEPA
does not accept into its pool.

Independent validation uses cycle **version 3**. Its optimizer declares
`validation_scope: "independent"`; `case_ids`, `scores` and `frontier` refer to
validation, while `work_checks`, `reference`, `seed_evaluation` and round replay
references remain development evidence. `optimizer.validation` retains the
dataset digest, validation task/input/source identity and checks, plus each
evaluated Skill's activation/output receipt, observed checks and cost/time
measurement. Raw requests/sources and per-call usage records remain private.
Validators recompute scores and selection from these bound receipts. These are
integrity checks within the existing local trust model, not signed attestations
against an operator rewriting all artifacts.

Readers continue to accept historical cycle versions 1/2 without rewriting their
decisions. Deploy the updated Python and dashboard validators together before
loading v3 evidence; older readers intentionally do not recognize it. Existing
running experiments must finish on their original code and private stores.

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
PYTHONPATH=skillops:skillops/tests python3 -m unittest \
  test_gepa_search test_gepa_validation test_evaluation_datasets test_skill_iterations -v
cd skillops
npm run test:dashboard -- --grep GEPA
```

A performance claim requires separately authorized live experiments comparing
sequential and GEPA search on the same tasks and budget over repeated runs.
