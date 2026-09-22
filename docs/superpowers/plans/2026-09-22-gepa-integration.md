# GEPA Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in official GEPA search with instance-level Pareto parent selection, retained candidate evidence, and the existing separate confirmation and human approval gates.

**Architecture:** Keep the default sequential iteration unchanged. An optional GEPA adapter operates on the required cases of one recorded development WorkItem, reusing SkillOps generation, execution, feedback issuance, immutable captures, and the shared budget. Search scores are development-case outcomes, not independent held-out validation or a weighted blend of instruction quality, time, and usage. The existing separately registered confirmation WorkItem remains outside the optimizer.

**Tech Stack:** Python standard library, optional hash-pinned `gepa==0.1.4`, existing Copilot runtime and Docker checks, unittest, existing dashboard JavaScript.

---

## Scope and invariants

- One project, one skill, one fixed development WorkItem with at least two required cases. GEPA train/validation both use these development cases; the report must disclose this reuse.
- Reuse official GEPA Pareto selection, do not implement a competing genetic algorithm. Disable merging, checkpoint resume, external model providers, and telemetry integrations.
- Cache one complete original-versus-candidate application per candidate within this search only. Each case score is derived from those retained observations; missing/error/skipped checks are never mapped to successful scores.
- Preserve all proposals, including proposals not accepted into GEPA's pool. Preserve pool membership, parent IDs, case scores, final selection, seed and stopping reason.
- GEPA selection recommends a candidate; existing replay correctness/regression decisions remain mandatory for confirmation/adoption. Never change a stored decision to fit search results.
- Final confirmation is registered before development and only exposed after search closes. No automatic approval, activation or deployment.
- Live experiments require separately authorized model-call/time/credit limits; offline tests must not be described as measured model improvement.

## Task 1: Pin and inspect the official optimizer

**Files:** `skillops/requirements-gepa.txt`, `.github/workflows/ci.yml`

- [x] Pin the core wheel and hash without provider extras.
- [x] Inspect the installed `optimize`, adapter, result, and candidate-selector contracts.
- [x] Use a private virtual environment for verification and install the optional dependency in CI.

Run:

```bash
python3 -m venv .venv
.venv/bin/pip install --only-binary=:all: --require-hashes -r skillops/requirements-gepa.txt
```

Expected: the official optimizer imports without a provider key or network call.

## Task 2: Build the adapter test-first

**Files:** create `skillops/gepa_search.py`, `skillops/tests/test_gepa_search.py`

- [x] Write offline tests against the real GEPA package with bounded provider doubles.
- [x] Observe missing-feature failures before implementing the adapter.
- [x] Connect case evaluation to retained replay evidence and generation to issued development feedback.
- [x] Enforce max proposals, metric requests, shared invocation/deadline budget, and explicit failure reporting.
- [x] Test complementary candidates, seed selection, duplicate proposals, missing measurements, budget exhaustion, and provider exceptions.

Run:

```bash
PYTHONPATH=skillops python3 -m unittest discover -s skillops/tests -p 'test_gepa_search.py' -v
```

Expected: actual GEPA selection is exercised; no live model calls.

## Task 3: Persist branching search without weakening existing cycles

**Files:** `skillops/skill_iterations.py`, `skillops/project_results.py`, `skillops/gepa_search.py`

- [x] Add a versioned GEPA cycle contract while preserving version-1 validation.
- [x] Validate every parent against retained earlier evidence, case scores against observed checks, and selected candidate against the retained pool and unchanged replay decision.
- [x] Share existing isolated confirmation handling with the new runner.
- [x] Test forged parent/score/selection records and verify old sequential records still load.

Run:

```bash
PYTHONPATH=skillops python3 -m unittest discover -s skillops/tests -p 'test_skill_iterations.py' -v
PYTHONPATH=skillops python3 -m unittest discover -s skillops/tests -p 'test_gepa_search.py' -v
```

## Task 4: Wire CLI, dashboard and documentation

**Files:** `skillops/skillops.py`, `skillops/project_evaluation.py`, `skillops/dashboard/trace.js`, relevant existing dashboard tests, `README.md`, `README.ko.md`, `docs/GEPA.md`

- [x] Add explicit optimizer selection to `iterate`; preserve sequential defaults and existing permission checks.
- [x] Show optimizer identity and retained selection evidence without treating a Pareto candidate as approved.
- [x] Document optional installation, exact invocation, metric/cache semantics, held-out confirmation boundary, and limitations.
- [x] Ensure runtime identity includes the new evaluator/optimizer code and dependency pin.

## Task 5: Verify and publish

- [x] Run focused Python and dashboard checks, then the offline regression suite.
- [x] Run container checks only when the environment provides the required images; disclose skips.
- [x] Review the diff for unsupported success/approval claims and unrelated changes.

Publication: commit the plan, implementation and documentation on
`feat/gepa-integration`, then push a separate PR with evidence and explicit
live-evaluation limitations. Do not merge.

### Verification record

- Real GEPA and SkillOps provider/storage regression tests pass with offline
  model transport, including partial failures, immutable-storage failures and
  shared call-limit exhaustion.
- Full Python suite with `SKILLOPS_CONTAINER_TESTS=1`: 790 tests run,
  no failures, 5 skipped. Real Docker checks were enabled; additional
  environment-specific tests retain their explicit opt-in/skip conditions.
- Full dashboard browser suite: 188 passed, including actual Python-generated
  schema-v2 evidence and rejection of altered optimizer scores.
- No paid/live model experiment, measured improvement claim or production
  activation. The attempted external reviewer could not start because its
  configured model was unavailable; final review was performed directly.

## Out of scope

Multiple development WorkItems, configurable evaluation-set catalogs, candidate merging, durable optimizer resume, automatic production feedback ingestion, automatic approval/deployment, and claims of superiority over sequential search. A future matched-budget live experiment should compare both paths over repeated runs before making performance claims.
