# SkillOps roadmap

**Let every skill evolve, but adopt only validated improvements.**

SkillOps separates evidence-driven skill mutation from project adoption. A candidate
is a proposed change, not a proven improvement. Evaluation can recommend adoption;
project policy and approval determine whether it is applied.

This is a planning snapshot dated September 16, 2026, not a list of completed
features. Use linked implementation issues and merged code as delivery evidence.
See the [README](../README.md) for current commands and
[CONTRIBUTING](../CONTRIBUTING.md) before claiming work.

## Starting point

The current baseline provides isolated Python coding-task evaluation, fixed tests,
an independent calibrated judge, candidate generation, fresh old/new comparisons,
quality/cost/time decisions, repository registration, immutable initial skill pins
and read-only eligibility.

It does not yet provide active skill installation, canary/promotion/rollback,
bounded iterative evolution, PR-triggered live model evaluation or an integrated
live-result dashboard. The initial adapter is the Python issue-management example,
not arbitrary repositories or languages.

### Evaluation work in development

An Anthropic-inspired **skill-authoring guide assessment** is being developed.
Its current design discovers skill bundles, performs structural checks and
uses a separate rubric judge for writing quality and resource organization.
Results are report-only: they do not replace task evaluation, prove project
fitness, certify compliance or authorize deployment.

This work is distinct from **repeated-trial reliability evaluation**, which remains
separate planned work. Do not treat guide assessment as delivering a trial-count
option, variance statistics or proven trigger-selection accuracy.

Before integrating either capability, agree the report contract, failure
semantics, input identity and usage accounting with its contributor. Do not
duplicate the ongoing implementation.

### Planned guide-assessment dashboard panel

A dedicated panel will show actual static findings, rubric scores and rationales,
applicable-criterion denominators, and why a criterion is not applicable.
Summarize the project with pass/review/blocked skill counts, not an average score.
Show the evaluated skill identity, assessment time and recorded rubric/source
metadata; distinguish guide recommendations from SkillOps-specific checks.

Missing results must display **Not evaluated**, not a fabricated score, pass or
not-applicable status. This reference-based assessment is implemented by SkillOps,
not an Anthropic certification. Keep it separate from task quality and adoption.
The panel is planned, not integrated; it needs the reviewed producer report
contract but does not need to wait for the complete PR/rollout dashboard.

## Delivery work

### Multi-project evaluation foundation

The next architecture places reviewed project source snapshots under `projects/`,
including `projects/sample_repo/`. GitHub Actions will assess changed projects and
retain per-project histories under the sibling `results/` directory. The dashboard
will read those results rather than hardcoded examples or a separate Blob archive.

```text
projects/<project>/
results/<project>/<evaluation-run>/report.json
results/<project>/index.json
results/index.json
dashboard/
```

Common skill-authoring assessment applies where a project contains skills.
Execution-based evaluation requires a configured, supported adapter; a missing
adapter means **Configuration required**, not a successful evaluation.
Keep these two assessment results separate. The initial execution adapter still
targets the Python issue-management sample, not arbitrary project test commands.

Plan automatic evaluation for trusted `main` changes and offline validation for
PRs. Persist only public-safe results, retain source/evaluator identity, and chain
dashboard deployment after result publication without a result-triggered loop.
Git-tracked results in this public repository are public, not private storage.
The catalog, sample migration, result writer, gated workflow, viewer and hosting
template are implemented as foundations. Shared guide integration, live authentication/budgets
and the complete model-backed Actions-to-dashboard path still require activation and verification.

| Capability | Deliverable and acceptance evidence | Dependencies |
|---|---|---|
| Project fitness and evaluation data | Versioned project criteria; development/validation/final-heldout roles; missing required evidence blocks adoption | Existing evaluator and repository binding |
| Controlled skill adoption | Exact approved skill loaded on selected targets; canary, promotion and restoration of the previous version; rejected/stale evidence leaves active selection unchanged | Fitness contract and existing eligibility |
| Structured execution evidence | Bounded development observations with provenance; operational failures separated from quality failures; invalid records rejected explicitly | Fitness/data contract |
| Evidence-based mutation | APO-style observation, hypothesis, instruction change and expected effect; immutable parent/candidate lineage; no active-pin change | Structured evidence |
| Comparison and evaluation extensions | Fresh matched comparisons with reasoned decisions; separate authoring diagnostics; separately scoped repeated trials with complete denominators | Fitness contract; mutation for generated candidates; extensions can build on the existing evaluator |
| Bounded self-evolution | Persisted candidate/round/call/cost/time budgets and stopping rules; selection on validation; frozen candidate for final assessment | Evidence, mutation and comparison; controlled adoption for deployed feedback |
| PR evaluation and orchestration | Exact-head skill changes trigger authorized evaluation; authored skills are candidates without mandatory regeneration; stale results cannot be adopted | Comparison and adoption boundaries |
| Dashboard and lifecycle demonstration | Actual guide findings/provenance, lineage, diffs, metrics, decisions and active versions; missing results stay explicit | Guide panel depends on its report contract; complete lifecycle depends on evaluation and adoption workflows |

Controlled rollout remains before PR automation before dashboard completion.
Evidence/mutation work can progress alongside rollout once shared contracts are
agreed. Iterative search must wait for dataset isolation and enforceable budgets.

## First tasks to make ready

1. Track the ongoing authoring-guide assessment in its existing workstream.
   Confirm its report-only boundary and final reviewed contract before integration.
2. Define the project fitness/data contract using the current policy and adapter.
3. Define approval and actual skill activation, including stale-evidence rejection,
   conflict handling and rollback verification on the next execution.
4. Prepare safe development feedback for the existing single-call generator.

Each ready task needs an owner, scope, acceptance checks, dependencies and a linked
PR. Do not assign work merely because an agent session exists.

## Evaluation and governance rules

- Keep authoring quality, observed task quality and adoption authorization distinct.
- The evolving skill cannot change its grader, evaluation dataset, thresholds,
  approval policy or permissions to make itself pass.
- Generate from permitted development evidence, select using validation, and freeze
  the candidate before final heldout assessment. Do not tune against final results.
- Preserve passing regressions. Do not weaken the original skill or invent failures.
- Count all authorized optimization work, including unsuccessful candidates,
  calibration and repeated trials. Missing usage is unknown, not zero.
- Report rejected, blocked and insufficient-evidence outcomes honestly. A successful
  pipeline does not require a winning candidate.
- Treat guide recommendations as contextual diagnostics, not universal certification.
  Broader security/policy claims require named, implemented checks.
- Restore the previous skill for future runs on rollback; do not imply that
  previously generated or merged code is automatically reverted.

## How the team tracks delivery

Use this document for direction and dependencies, issues for execution scope and
ownership, and PRs for implementation evidence. If a Project board is used, it
should display those same issues rather than introduce a duplicate backlog.

Start with one feature issue per capability and only the next actionable
sub-issues. Check existing issues and PRs before creating new ones. Keep technical
decisions in reviewed repository documents and link them from issues.

Suggested workflow: Backlog -> Ready -> In progress -> In review -> Done.
Record blocking dependencies explicitly. Done requires the agreed review, merge
and verification evidence, not only a local completion message.

Publishing this document does not create issues, configure a board or assign
contributors automatically.

## Beyond the first loop

Additional adapters, optimization of descriptions/reference loading/workflow
configuration, richer policy checks and larger candidate search are separate
increments. The first optimization target remains the activated development
skill body. No new training infrastructure is required for that first slice.
