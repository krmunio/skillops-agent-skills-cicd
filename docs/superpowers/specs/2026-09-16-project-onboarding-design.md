# Project samples and skill onboarding

Revised September 16, 2026, against upstream `0fceb1e69b7c915e13625f5f173a88796d8c1452`
(PR #6). This revision awaits review and replaces the earlier proposal to build a
second evaluator, report schema, workflow, and results branch.

## Reuse the merged foundation

The [project-evaluation contract](../../PROJECT-EVALUATION.md) is authoritative.
The merged implementation already supplies:

- `projects/sample_repo/`, project discovery, bounded tree identities, and profiles.
- `project_evaluation.py` with independent guide/execution states and live gates.
- `project_results.py` with immutable reports, validation, indices, historical
  imports, and allowlisted dashboard builds.
- The project workflow, the `evaluation-results` data-branch writer, a static
  dashboard, and the Azure Static Web Apps resource definition.

Do not create `projectops.py`, another report schema, a second publisher/workflow,
`skillops-results`, or replacement dashboard/infrastructure for this work.

## Actual evaluation boundaries

The shared skill-authoring guide producer is still not integrated. The current
assessment records `blocked / guide_integration_pending` when project skills exist,
and `not_assessed / no_skills` otherwise. Neither is a completed guide assessment.

Execution currently supports only `issue-management-v2`. Projects without a
profile adapter receive `configuration_required / no_adapter`. The existing
registration path pins the common root `skills/develop/SKILL.md`; it does not select
an arbitrary project-local skill. Adding a local skill therefore does not prove
that live execution exercised it. Per-project skill selection and additional task
adapters need a separately reviewed integration with the evaluator contributor.

Missing live authorization, authentication, or call/time limits remain blocked.
Do not enable billable calls, assign a passing score, or reinterpret an unconfigured
adapter as a successful test. Initial skill addition, authoring assessment, task
quality, and adoption authorization stay separate.

## Focused implementation scope

Prepare reproducible samples and their skills, then exercise the existing report
contract. Keep the migrated built-in source files and frozen evaluation data intact.

| Project | Source revision | Skill treatment | Current expected execution state |
| --- | --- | --- | --- |
| `sample_repo` | Existing `projects/sample_repo/` | Copy the existing `develop` skill into the project if absent; preserve its bytes and seeded source defects. | `blocked / live_disabled` without authorized live settings. |
| `project-a` | `dbader/schedule` at `82a43db1b938d8fdf60103bd41f329e06c8d3651` (`1.2.2`) | Add a scheduling-development draft informed by the actual source and test conventions. | `configuration_required / no_adapter`; do not introduce a pretend adapter. |
| `project-b` | `obra/superpowers` at `b36e0829c6d0140e93cfef2ca599b1b07d4a7797` (`v6.3.0`) | Preserve its existing skills. | `configuration_required / no_adapter`. |

All three projects with skills currently have `guide_integration_pending`. This is
an expected, visible integration dependency, not evidence of poor skill quality.

### Safe source preparation

Add a focused import/preparation helper rather than another evaluation engine.
Use public GitHub archives pinned to full commit IDs and preserve ordinary source
files and upstream license files. Store reviewed provenance inside each project:
repository, source commit, export policy, original source hashes, omissions, and
added-skill identity. The existing catalog hash binds this metadata and actual
source/skill bytes to reports; do not add incompatible public report fields.

Never run repository setup scripts, hooks, or imported instructions while importing.
Do not embed `.git` directories or submodules. Omit symlinks without following them
and record the omissions, including Superpowers' `AGENTS.md` symlink. Reject unsafe
paths, Windows device names, duplicate/case-colliding members, special files,
excessive archive expansion, and the merged catalog's file/count/size limits.
Never replace an existing project directory or traverse a symlink/junction.

### Supply missing skills

Find existing bundles in the roots already supported by the merged evaluator:
`.github/skills`, `.claude/skills`, and `skills`. Existing content, even malformed
content, must not be silently overwritten. When no skill exists, require a selected
project-appropriate draft and place it under `.github/skills/<name>/SKILL.md`.
Selected sample profiles supply the matching draft; an unknown project without a
skill or selected draft requires input rather than receiving a generic invention.

Bootstrap skills are unvalidated drafts. Do not describe them as proven behavioral
improvements. Validate only the preparation helper's required metadata and file
integrity; do not duplicate the pending guide rubric/assessment implementation.
Evaluation itself stays read-only and never silently repairs or creates skills.

### Reports and CI verification

Use the existing assessment, validation, store, and reindex operations. Preserve
`results/<project>/<run-id>/report.json`, project-level indices, the root index,
existing run-ID formats, and the dashboard's current schema. Keep repository/tree
and evaluator identities, independent axis states, and fixed failure codes. Never
publish credentials, absolute local paths, skill bodies, or raw invocation logs.

Add fixture/onboarding regression tests to the existing standard-library suite.
Cover no-skill insertion, existing-skill preservation, repeated preparation,
source provenance, safe extraction, and actual catalog/assessment/report/index
integration with model calls disabled. If needed, extend the existing read-only
contract job to retain these explicitly non-model sample reports; do not add a
parallel evaluation workflow or give PRs publication/model credentials.

The current workstation lacks the supported Linux/Docker environment. Use portable
helper tests locally and the existing Linux CI for the evaluator/report integration
and original regression suite. Green contract tests do not imply completed guide
or live task evaluations.

## Operational prerequisites, not replacement features

The inspected main run `35064353388` produced a real public report with
`guide: not_assessed / no_skills` and `execution: blocked / live_disabled`.
Its persistence job failed because `evaluation-results` did not exist, and deploy
was skipped. The existing baseline validation succeeded for the same main commit.

Use the existing data-branch design if result persistence is initialized; do not
create the superseded `skillops-results` branch. Branch initialization, enabling
billable model settings, connecting the guide producer, adding execution adapters,
and deploying Azure content are distinct actions. Do not silently enable or claim
completion of any of them as a side effect of importing samples.

## Acceptance and delivery

- Two real, pinned external snapshots with preserved licenses and provenance.
- A missing skill is supplied intentionally; existing upstream skills are intact.
- Existing catalog/report/index/dashboard contracts accept the prepared samples.
- Reports preserve blocked/unconfigured states and do not claim that a project-local
  skill was behaviorally evaluated by the common-skill execution adapter.
- Regression evidence distinguishes offline contracts from real model execution.
- English shared documentation and synchronized user-facing README commands.
- Commit/push only the focused work to the existing isolated feature branch after
  review. Do not merge, force-push, provision Azure, or claim someone else's review.
