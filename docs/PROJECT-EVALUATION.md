# Projects, results and public dashboard

## Current foundation

- The owner-operated public dashboard was reachable on September 16, 2026.
  The automatic-evaluation changes require their own release and live verification.
- The controlled sample lives in `projects/sample_repo/`; its deliberately defective application
  modules are unchanged. A project-local Skill and conventional tests exercise automatic onboarding.
- `project_profiles.json` retains compatibility with the legacy `issue-management-v2` adapter;
  a profile entry is not required for the automatic path.
- `project_results.py` validates public records, imports reviewed historical summaries,
  appends immutable reports, rebuilds indices and builds an allowlisted static site.
- `project_evaluation.py` emits independent guide/execution states. Live execution
  is disabled until explicit settings and authentication are provided.
- The project workflow validates PRs without model/deployment secrets. Trusted main
  pushes and main-only manual dispatch record assessments or explicit blocked states.
- Results persist on `evaluation-results`; main still requires a reviewed PR and CI.

The shared Skill-guide evaluator, one-candidate improvement pipeline and dashboard are connected.
Original and candidate quality are assessed separately with the same committed rubric and model.
Both versions can then be applied to the same frozen work item, followed by real project checks.
Projects without Skills receive `not_assessed / no_skills` for Skill quality; supported project
checks can still run. Unsupported execution prerequisites remain explicit, not invented scores.
Live model use and deployment require the activation and authorization described below.

## Core CI checks

The repository's `SkillOps validation` workflow is **not** the Anthropic/APO-inspired
Skill baseline assessment. Its existing `offline-and-container` job identity is unchanged
so the required branch check remains stable.

| Stage | Selected checks | Model execution |
| --- | --- | --- |
| Repository validation | Python tests, real isolated-container controls, immutable result contracts | None |
| Project workflow `contracts` | Catalog discovery, blocked-report persistence/validation, workflow trigger and permission contracts | None |
| Project workflow `evaluate` | Original quality, one generated candidate, candidate quality, paired applications and project regression checks | Explicitly enabled trusted-main execution only |
| Project workflow `persist` | Validate and append public results, including unsuccessful evaluations, without rewriting history | None |
| Project workflow `deploy` | Publish validated results using separate deployment credentials | None |

PRs run validation without model or deployment secrets. Changes under `projects/**`,
including a new project or changed Skill resources, match the existing main-push trigger.
Result-branch publication does not retrigger evaluation. Manual dispatch defaults to
`live=false`; its optional project selector limits the current run to one catalog project.
The evaluate job uses one sequential orchestrator and shared invocation/time/credit limits,
not independent copies of the budget in a job matrix.

The existing live-disabled main path deliberately records blocked outcomes and fails
`evaluate`; persistence and publication can still succeed. It is not a successful assessment.
On September 17, 2026, run `35184714110` verified this path; PR run `35184542810`
verified that privileged jobs are skipped; explicitly authorized sample-only run
`35184191312` completed all four stages and published validated original/candidate results.
These observations do not establish successful evaluation of the whole catalog.
Changed-project-only selection and additional automatic-loop controls remain core
implementation follow-ups; advanced evaluation-set selection and dashboard redesign
are outside this CI step.

## Add a project

Add a reviewed ordinary source directory under `projects/<safe-id>/` and commit
its contents. IDs allow lowercase letters, digits, underscores and hyphens.
Preserve source licenses. An embedded `.git` directory or a submodule is not a
supported source snapshot. Do not add credentials, `.env` files or generated data.
Limits: 50 projects, 10,000 files and 128 MiB per project, 1 MiB per input file.

Project-local skills are discovered under `.github/skills`, `.claude/skills` and
`skills`. Root `skills/develop` is not automatically attributed to every project.
No root profile edit or hand-authored task file is required for automatic evaluation.
An existing failed project check supplies the current work criterion. Its identity, editable
source and protected test context are frozen before candidate generation. If every check already
passes, or no suitable work can be derived, quality and candidate results are still retained but
execution effect is **unverified**. This is not replay of a real historical user task.
Remove an existing profile entry when removing its configured project. Historical results remain.

## Reproducible sample preparation

The checked-in snapshots cover both missing-skill insertion and existing-skill
preservation. They are source exports, not nested Git checkouts or submodules.

| Project | Pinned upstream source | Preparation |
| --- | --- | --- |
| `sample_repo` | Existing issue-management seeds | Add a copy of root `skills/develop/SKILL.md`; leave the three deliberately defective source files unchanged. |
| `project-a` | `dbader/schedule`, `1.2.2`, `82a43db1b938d8fdf60103bd41f329e06c8d3651` | Preserve 32 source files and the MIT license; add the selected `schedule-development` draft. |
| `project-b` | `obra/superpowers`, `v6.3.0`, `b36e0829c6d0140e93cfef2ca599b1b07d4a7797` | Preserve 194 ordinary files and existing skills/license; omit and record the upstream `AGENTS.md` symlink. |

Verify the existing snapshots, or prepare a **new, unused** project ID:

```bash
python3 project_samples.py verify --project project-a
python3 project_samples.py verify --project project-b
python3 project_samples.py add-skill --project sample_repo --skill skills/develop/SKILL.md
python3 project_samples.py import --project schedule-demo --repository dbader/schedule \
  --commit 82a43db1b938d8fdf60103bd41f329e06c8d3651 \
  --skill project_templates/schedule-development/SKILL.md
```

The standalone helper uses Python 3.12+ standard-library modules on Windows or
Linux; the existing evaluator still requires Linux. Imports accept public GitHub
`owner/repository` identifiers and full commit IDs, not arbitrary download URLs.
They do not run setup scripts, hooks, tests, or instructions from the imported
repository. Existing project directories are never replaced. An explicit
`{"adapter": null}` profile is optional for the automatic path; do not assign
`issue-management-v2` to an unrelated repository.

`.skillops-source.json` schema 1 records the source commit, original file hashes,
license paths and omitted symlinks. `.skillops-bootstrap.json` schema 2 records
only intentionally added skills and labels them `unvalidated_draft`. Imported
bootstraps bind the exact source-manifest SHA-256; built-in bootstrap-only projects
use a null binding. A deleted or modified bound manifest blocks verification,
and legacy bootstrap metadata is not silently downgraded or upgraded. These
preparation schemas do not change the public report schema.
The helper rejects unsafe paths,
links/junctions, special files, case collisions, metadata collisions and excessive
archive expansion before publishing a new snapshot. Symlinks in the archive are
omitted without following their targets. Compressed downloads are limited to
16 MiB, in addition to the project/input limits above. PAX metadata is bounded and
allowlisted before parsing; the 50-project cap is checked before downloading and
again before publishing a new directory.

Existing skills, including malformed ones, are preserved rather than silently
repaired. A repository with no skill requires an explicitly selected draft;
there is no automatic generic fallback. Only newly supplied template bytes are
normalized from CRLF to LF. Upstream source bytes remain unchanged, and
`.gitattributes` defaults snapshots to byte-preserving storage; imported attribute
rules can override that default, so verify hashes after checkout too. Review all
imported files before staging; public upstream content is not automatically trusted.
`verify` checks recorded file integrity, not source trust or skill effectiveness.

## What sample verification proves

Preparation is separate from assessment. Legacy baseline registration pins the common root
`skills/develop/SKILL.md`. The automatic project path instead discovers local Skills and binds
each applied version to its staged bundle bytes. Adding a local Skill or passing preparation
checks alone does not prove that live execution exercised it.

With model execution disabled, the existing evaluator records:

| Project | Guide | Execution |
| --- | --- | --- |
| `sample_repo` | `blocked / guide_integration_pending` | `blocked / live_disabled` |
| `project-a`, `project-b` | `blocked / guide_integration_pending` | `blocked / live_disabled` |

No scores or adoption decisions are invented for these states. Schedule's full
upstream tests require platform timezone support (`time.tzset`), and some cases
use optional `pytz`; importing it does not execute or certify that suite.

The existing workflow's unprivileged `contracts` job runs the complete Python
regression suite and pinned-image Docker controls, then invokes the existing
project evaluator with `SKILLOPS_LIVE_EVALUATION_ENABLED=false`. It requires the
expected blocked exit code **2**, validates each report/index, and checks that
there is exactly one **new** actual report per catalog project with the checked-out
commit, run ID, project tree and evaluator identities. The output starts from the
checked-in history; all older report and optional sidecar bytes must remain
unchanged, with no missing or unexpected report identities. Rebuilt indices retain
validated sidecar references. Empty output fails.
The distinct `sample-onboarding-results` artifact contains report/index JSON
plus optional validated `skill-snapshots.json`, `skill-evolution.json` and `skill-assessments.json` sidecars,
and is never consumed by the live result publisher. Contract success means
these behaviors were verified, not that guide or model evaluation passed.

Feature-branch manual dispatch can run this contract job without enabling the
main-only evaluation, persistence or Azure deployment jobs. Reviewed actual
artifact bytes may be preserved under `results/<project>/<run-id>/report.json`;
their original evaluated source commit and evaluator fingerprint stay unchanged
in later evidence or integration commits. New evaluator code on main can make old
results non-current; never rewrite their provenance to match it.
Synthetic test reports exist only in temporary directories. This preparation
does not create the data branch, enable paid calls, install upstream skills into
the user's agent, or provision Azure resources.

## Owner dashboard direction

The owner's September 16, 2026 direction separates automatic baseline checks
from project evaluation and keeps the dashboard read-only. PR #8 implements an
evidence-first layout and a separately labeled, opt-in synthetic sample screen.
The real history still uses public v1 reports, with optional validated snapshot
and evolution sidecars now supporting reviewed skill versions and dashboard history.
The new assessment sidecar adds actual project-scoped guide and candidate evidence when available.

Keep these evidence scopes distinct:

- **Baseline validation workflow:** regression tests of the evaluator, container
  controls and historical evidence checks, without live model calls.
- **Baseline model run:** a record with `purpose: baseline`, evaluating a specific
  skill version. A green baseline-validation CI job is not this model run.
- **Project assessment:** a project-scoped report with separate guide/execution
  states, with per-Skill comparisons supplied by validated assessment sidecars.

| Proposed project view | Required evidence and current boundary |
| --- | --- |
| Project, target skill, base/candidate selection | Bind actual Skill identities, versions and bundle hashes to a run; the automatic path selects each supported project-local Skill independently. |
| Skill quality and improvement evidence | Separate Anthropic-inspired static/rubric findings from APO-style hypotheses, changed instructions and re-evaluation. Display measured evidence only when its bound assessment sidecar exists. |
| Project execution | Compare original/base/candidate outputs using existing protected checks and the frozen failed-check repair criterion. Disclose unsupported frameworks and missing work as unverified; legacy five-task results remain separate. |
| Skill changes | Show only reviewed version documents and diffs with recorded generation/parent/adoption metadata. Public v1 reports still reject raw skill bodies; optional validated snapshot/evolution sidecars provide the reviewed, bounded export contract described below. |
| History | Preserve project/run identities, evaluation kind, source/evaluator hashes, timestamps and current/stale/historical distinctions. Do not merge records merely because they share a skill name. |

The screenshot's rejected candidate, 5/5 results and cost/time changes describe
historical issue-management evidence, not the new Schedule or Superpowers samples.
Do not copy those values into new project assessments. Likewise, static validation,
LLM rubric results, an improvement hypothesis, a completed execution and adoption
authorization are separate states; missing evidence must stay explicit.

Sample preparation verifies inputs and report/history contracts. It does not itself run model
evaluation or authorize activation; the automatic pipeline uses the additive evidence contract below.

## Local commands

```bash
python3 project_results.py catalog
python3 project_results.py index --results results
python3 project_results.py validate --results results
python3 project_results.py import-history --source runs --project sample_repo \
  --results .dashboard-public/reviewed-results
python3 project_results.py build --results .dashboard-public/reviewed-results \
  --output .dashboard-public/site
```

Review exported JSON before publishing. Import only run-level baseline, comparison,
candidate and calibration summaries; raw invocation artifacts remain local.
Build output must be a new directory. It contains only allowlisted dashboard assets
and validated results, never project sources or raw logs.

## Actions activation

### Automatic-evaluation support and boundaries

`project_checks.discover` reads Python/Node configuration without importing project code.
Supported test runners are unittest, pytest, `node --test`, direct Jest and `vitest run`.
Declared npm build/lint/typecheck scripts are additional gates, not substitutes for individual
test identities. Opaque test wrappers, shell-compound test commands, pretest/posttest hooks,
yarn/pnpm and unsupported framework configurations remain explicit unsupported states.

Dependencies are prepared once per project from supported manifests, without mounting project
code in the resolver. Python accepts registry requirements and supported PEP 621 dependency groups,
using wheels only. Node accepts supported registry dependencies and npm lockfiles v2/v3, with
install hooks disabled. Local/VCS/URL dependencies, custom registries, npm workspaces/overrides,
Poetry/uv locks and packages requiring install/build hooks are not supported.
Resolution uses a restricted proxy for PyPI and npm's official registries; test/build execution
has no network and receives no model or deployment credentials.

Both arms use the same prepared immutable image, protected tests and configuration. Project code
runs in fresh non-root containers with read-only root, CPU/memory/PID/output limits and finite
deadlines; only explicitly owned containers, networks and image tags are cleaned up. Check and
model execution share the run deadline; dependency preparation is additionally capped at 180
seconds and each check observation at 120 seconds. Cleanup has its own bounded overhead.
Arm order is varied from a run-specific hash rather than always running the baseline first.
Unsupported or failed dependency preparation is recorded as an explicit check-stage error.
It does not prevent independent Skill quality evaluation and candidate generation; execution
remains unverified. In particular, importing Schedule/Superpowers does not add support for
their legacy packaging or shell-based test harnesses.

`skill-assessments.json` is an optional, immutable, report-bound attachment for per-Skill quality,
generation, paired application and project-check observations. It requires matching captured
versions in `skill-evolution.json`. The publisher validates, preserves, indexes and builds these
attachments. The orchestrator produces them and the dashboard renders real quality, hypotheses,
activation, measured application usage and the original/base/candidate test comparison.
An invalid Skill cannot discard valid sibling assessments; failures before a safe version capture
remain private diagnostics plus an explicit public error count.

Qualification is computed from evidence rather than trusting a submitted decision:

- Original and candidate quality use the same rubric and evaluator context. A supported
  improvement cannot add errors or regress an applicable dimension.
- Both applications identify the exact staged version and frozen work input. The runtime can
  verify the staged entrypoint/resources before and after invocation, including same-name Skills.
- Untouched source and both applied outputs use identical check-plan, environment and protected
  input hashes. Comparisons use individual test identities, not only totals.
- Newly failed/skipped/missing tests reject the candidate. An inherited failure is not itself a
  new regression; missing coverage, unavailable required gates or mismatched inputs are unverified.
- Task satisfaction currently requires observing the repair of an identified, pre-existing failed
  project check. A nonempty irrelevant edit, no-op or model self-report is insufficient.

The qualification states are `improved`, `not_improved`, `rejected` and `unverified`, not deployment
states. A passing regression observation means only no regression detected within the checked scope.
Public data contains bounded reviewed summaries and Skill captures, never raw prompts, project
code or container output. Schema validation alone is not a disclosure review.

Before enabling live evaluation, configure repository settings deliberately:

- The trusted evaluate job has `contents: read` and `copilot-requests: write`, and receives its
  built-in `GITHUB_TOKEN`. Its Copilot policy/entitlement must allow the configured `gpt-6-astra`
  model. Optional secret `COPILOT_GITHUB_TOKEN` overrides authentication when deliberately supplied.
- Variable `SKILLOPS_LIVE_EVALUATION_ENABLED=true`: explicit billable-execution opt-in.
- Positive `SKILLOPS_MAX_INVOCATIONS` (maximum 1000) and `SKILLOPS_MAX_SECONDS`
  (maximum 1200), shared across the workflow's sequential project evaluations.
- Finite `SKILLOPS_MAX_AI_CREDITS_PER_SESSION` of at least **30**, forwarded to each Copilot
  CLI session. The pinned CLI 1.0.85 rejects smaller values before model execution. SkillOps
  treats such configuration as invalid limits and blocks evaluation without starting the runtime;
  it never silently raises an authorized limit.
- Secret `SKILLOPS_SWA_DEPLOYMENT_TOKEN`: the intended Static Web App's deployment credential.
- An existing writable `evaluation-results` branch for the validated data writer.

For a bounded first run, manual dispatch accepts an optional `project` catalog ID and a `live`
checkbox (default false). This explicitly authorizes only that dispatch without enabling automatic
billable runs on later pushes; the same configured invocation/time/credit limits are still required.
CLI callers can use `--project <id>`; unknown IDs fail before assessment. Omit the selector to
evaluate the catalog. Automatic main-push evaluation requires the repository enable variable.

On September 16, 2026, PR #7 added 11 reviewed reports to main. A later read-only
check confirmed the existing `evaluation-results` branch and the owner's PR #8
workflow run `35070021220`: its contracts, persistence and deployment succeeded,
while evaluation recorded `live_disabled`. These production steps were performed
by the owner's workflow, not this sample task. A deployed blocked report is not
successful model evaluation. Sample contract checks do not create or write the
data branch, modify activation settings, or deploy to Azure.

The workflow does not change repository settings or silently enable live evaluation. Missing
settings create explicit blocked records without model invocation.
The invocation cap bounds CLI sessions, **not internal model requests or money**.
The remaining time is passed into subprocess deadlines. A CLI credit limit is not a guaranteed
hard currency ceiling. Public cost/time measurements cover the individual Skill-application
sessions only, not the complete quality/generation/check pipeline; absent usage remains null.

Relevant main changes conservatively assess the catalog. A result-only update
does not retrigger evaluation. A blocked assessment makes its evaluation job fail
while the writer can still persist the public blocked result. Raw run directories
are never uploaded as Actions artifacts.

Trusted-main evaluation through persistence is serialized. Validated prior data is fetched inside
that slot to reuse Skill identities, never executed as code. GitHub concurrency can coalesce pending
runs: every intermediate commit is not guaranteed an evaluation, while existing persisted history
is preserved. Candidate rejection does not itself fail the infrastructure job; incomplete evidence
and operational failures remain explicit non-success outcomes.

### Stable Skill identity migration

When no validated assessment already associates a project-local path with a Skill key,
the automatic pipeline derives `path:<24 hex characters>` from the SHA-256 of the
UTF-8 string `<project_id>\n<relative_skill_path>`. Fresh runs on the same project
and path therefore agree even without prior history. Different project IDs and
different relative paths have different inputs; display names do not define identity.

Existing `auto:` keys remain valid history and are not rewritten. A matching
validated assessment from the same project preserves its existing key, including
an explicitly registered `skillops:develop` key. Records from another project do
not establish that association. This change prevents new fallback-key churn; it
does not merge historical `auto:`, `path:` and registered identities by name or
rewrite archived imports. Renames or explicit historical identity reconciliation
remain separate, reviewed migration work.

### Versioned assessment efficiency decisions

New Skill assessments record `decision.policy_version` using `candidates.POLICY["version"]`
(currently `1`) and reuse its `maximum_efficiency_regression_percent` (currently `5`).
If either paired application cost or elapsed time increases by **more than** this cap,
the decision includes `efficiency_regression` and cannot be `improved`: it is
`unverified` unless an existing quality/project regression already requires rejection.
An efficiency increase alone does not reject a candidate whose regression checks passed.
Missing either arm's measurement adds `efficiency_unverified` and also prevents improvement
qualification. These two reasons can coexist when one measured metric regresses and
the other is missing.

Exactly 5% is within the cap. Recorded zeros are measurements, not missing values:
zero to zero does not regress, while zero to a positive value exceeds the cap without
inventing a finite percentage. Decimal comparisons avoid rounding a boundary into a
regression. Quality, activation, work satisfaction and project-check gates remain in force.
This reuses only the existing policy's efficiency-regression cap; it does not claim
that project application measurements have the common benchmark's developer/judge scope
or automatically apply its separate 10% efficiency-improvement rule.

Versionless historical decisions retain their original pre-efficiency semantics and
exact stored bytes through validation, history loading and publication. Their missing
policy version is not backfilled or treated as proof of passing the new cap. Fresh
calls to `skill_assessments.decide` always apply and stamp the current policy, including
when re-evaluating legacy inputs. Unknown or malformed policy versions fail validation;
new versioned verdicts must match recomputation from their measurements.

The viewer's decision validator supports the same current/legacy distinction. Its
decision-only JavaScript change is covered by an explicit Python/JavaScript agreement
test in `tests/dashboard.spec.js`, including boundaries, missing/zero measurements,
rejections and preserved legacy evidence. The Python suite also executes those shared
cases with Node when available, so parity can run before the separate dashboard CI PR
is merged. No dashboard layout, strings or presentation changes are part of this follow-up.

Result writers validate incoming data and use bounded optimistic push retries on
the dedicated branch, with no force push. A serialized deploy job fetches the
newest persisted results after entering its slot and publishes a fresh snapshot.
Pending deploy jobs may coalesce; persisted history is not discarded.
Neither the data branch nor an untrusted PR supplies executed publisher code.

## Dashboard and infrastructure

The viewer loads `results/index.json`, project indices and selected reports.
Historical scope, missing provenance, stale results and unassessed states remain
visible. Guide scores, execution quality and skill adoption are different concepts.
No Anthropic certification or successful deployment of a candidate is claimed.

### Evidence-first layout

Selecting a project shows four sections in order:

1. Skill quality and improvement evidence. Method descriptions explain which
   Anthropic writing-guide and APO-style evidence concepts inform the assessment;
   these descriptions are not claims that an evaluator has run.
2. Project execution. Existing/candidate correctness, Judge, cost and time are
   shown from the selected report. Policy and task details remain unavailable
   when not present in the public record; no thresholds or reasons are invented.
3. Skill changes. As-Is and To-Be are expanded side by side (stacked on mobile).
   Current source files never substitute for historical snapshots. To-Be is a
   candidate, not evidence of adoption or deployment.
4. History. The **Skill 이력** tab lists the project's recorded Skills, unique
   version counts and linked records. Selecting a Skill or its record updates
   the whole detail view. **실행 이력** retains all records, including runs without
   a recorded Skill identity.

Existing public v1 reports remain unchanged. Quality findings, improvement traces and project
checks use the optional immutable assessment contract above. Missing sidecars remain explicit.
Reviewed Skill text uses the snapshot/evolution contracts below, never mutable current files.

### Reviewed archived Skill snapshots

`results/<project>/<run>/skill-snapshots.json` contains exactly schema version,
project/run identity, the canonical public report SHA-256, Skill ID, and base/
optional candidate objects with UTF-8 content and SHA-256. Each text is bounded
to 32 KiB and 400 lines. Extra fields, wrong hashes, wrong report bindings,
orphans and changed same-path retries are rejected. Indices expose Skill metadata
only for validated attachments. Build and merge preserve attachments.

This is an explicit exception for **reviewed Skill instructions**, not permission
to publish raw prompts, logs, generator rationales or arbitrary repository files.
Review the exact archived texts for secrets and private information first:

```bash
python3 project_results.py import-skill-snapshots \
  --source runs --results .dashboard-public/reviewed-results \
  --project sample_repo --candidate-run 20260915T005404Z-611d681b8bf7 \
  --skill-id develop --reviewed
```

This example requires the original local `runs/` archive and matching public
historical reports; a clean public checkout does not contain that private archive.
The opt-in importer verifies the archived candidate's saved instructions and
each original report against recorded hashes. Only compatible baseline,
candidate and comparison records receive attachments. No current-file fallback
or rewriting of original report.json is allowed. Guide and calibration records
without appropriate Skill evidence remain unlinked.

The **샘플 화면 보기** control opens `dashboard/sample-data.json`, a bundled,
invented layout example with two Skills and three records. Its banner, labels,
source panels and history identify synthetic content. It is opt-in and separate
from `results/`, catalog counts, real assessment APIs and candidate adoption.
Switching back to a real project clears sample evidence. The sample is not the
historically evaluated `sample_repo` and is never a result-publisher input.
Do not add private instructions or real raw run artifacts to this example.

The sample demonstrates static/rubric distinctions, conditional exclusions,
observation-to-verification evidence, rejected candidates and missing execution.
Code is rendered as text and diffs are bounded to 400 lines per version. No model
calls or evaluator activation are required to explore it.

`infra/public-dashboard.bicep` defines one public Azure Static Web App using the
approved Standard tier. It creates no VM, Storage account or anonymous write API.
Compile, validate, review what-if and confirm the target before provisioning.
An infrastructure resource is not proof that dashboard content or live evaluation
has been deployed. Verify the actual HTTPS endpoint separately.

## Skill evolution evidence

`skill-evolution.json` is an optional immutable attachment next to each `report.json`.
It does not change report v1 or snapshot v1. Its exact fields are `schema_version`,
`project_id`, `run_id`, `report_sha256`, `records`, `bindings`, and `file_contents`.
The report digest commits to the canonical public report, not the private artifact.

Explicit namespaced keys such as `skillops:develop` identify Skills independently
of display names or source paths. Equal names do not establish common identity.
Versions hash a domain-separated, scope-aware manifest of relative file paths,
SHA-256 and byte lengths. Parent/candidate relationships are separate records:
reverting content can reuse a version ID. `entrypoint_only` is not a whole bundle.
`complete_bundle` requires an explicit full inventory and every original byte,
including scripts, references and binary assets. The pure capture helper does not
discover files or scan home directories.

Private capture bounds are 256 files, 2 MiB/file and 8 MiB total. Public attachments
remain limited to **1 MiB JSON**, including base64 content. Oversize publication
fails; files are never silently dropped or scope relabeled. Loading, indexing,
merging and building verify complete manifest membership and all content hashes.
Review every retained byte for disclosure before using these publishing helpers.
Schema validation is not a secret detector or a disclosure approval.

Each explicit per-run binding names a key and its captured base/candidate versions.
Dual attachments must also explicitly bind the old `skill_id` and agree on both
entrypoint hashes, including candidate absence. Indices add `skill_evolution`
and `evolution_skills`; old snapshot fields remain unchanged. Lifecycle-only runs
need no legacy snapshot. Legacy-only names remain visibly unregistered.

After the reviewed snapshot import, attach saved evolution evidence:

```bash
python3 project_results.py import-skill-evolution \
  --source runs --results .dashboard-public/reviewed-results \
  --project sample_repo --candidate-run 20260915T005404Z-611d681b8bf7 \
  --skill-key skillops:develop --legacy-skill-id develop --reviewed
```

This importer uses saved candidate/baseline/comparison artifacts only. It verifies
original public provenance, source bytes and lineage joins, preserving the recorded
decision instead of rerunning current policy. Legacy comparisons missing a candidate
metadata commitment retain a `referenced_only` reference; a present bad commitment
fails. No private rationale is exposed or converted into an invented hypothesis.
Zero observed failures is zero, not proof of improvement.

Baseline attachments show only the evaluated base, not a future candidate. Archive
storage is labeled separately from an unknown historical definition path. No pin is
inferred from an evaluated base, candidate generation or canary recommendation.
The optional raw-registry observer validates explicitly supplied bytes without
filesystem-writing registry helpers. It proves only a configured **entrypoint pin**
at the recorded observation time, not actual loading or complete-bundle deployment.
Live generation, adoption changes, canary, promotion and rollback are not activated.

Incremental imports reuse a validated baseline attachment only when its explicit
SkillKey/legacy binding and base entrypoint agree. Original paths, display metadata,
capture scope and bytes are not rewritten for a later candidate. A retained complete
baseline and a later entrypoint-only generation may have different version IDs:
their shared entrypoint does not establish full-bundle equivalence. Conflicting
identity/content or malformed existing evidence fails before attachment writes.

The dashboard groups registered Skills by stable key, distinguishes definition
paths from archive locations, and exposes capture scope, version IDs, file selection,
generation/baseline/comparison links and timestamped adoption observations. Evidence
links resolve only to existing project history; unavailable public records stay labeled.
Content is inert text. Binary files show metadata; text beyond 400 lines or 50,000
characters shows a display-limit notice without discarding retained bytes or hiding
the evaluation. Diffs retain the 400-line/100,000-combined-character bound.
Served lifecycle files are checked against the report, index and retained file hashes
using Web Crypto, so production needs HTTPS (loopback preview also supports this).
Historical paths, hypotheses, full-bundle capture and current adoption are not invented.
Selecting an uncaptured identity clears other Skills' source content. File views
distinguish captured bytes, absence proved by a complete inventory, and unrecorded
content. Unrecorded content is never compared as an empty file or counted as an
addition/deletion. Served generation/comparison references, version/hash joins and
the selected run's original-artifact commitment and decision are validated before
rendering; a historical baseline reference does not assert full-version equivalence.

## Validation

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
npm ci
npx playwright install --with-deps chromium
npm run test:dashboard
az bicep build --file infra/public-dashboard.bicep
```

Offline tests do not claim a live model-backed Actions run. Do not merge or enable
billable workflows solely because offline validation passed.
