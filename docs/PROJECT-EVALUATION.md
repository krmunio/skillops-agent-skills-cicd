# Projects, results and public dashboard

## Current foundation

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
- Positive finite `SKILLOPS_MAX_AI_CREDITS_PER_SESSION`, forwarded to each Copilot CLI session.
- Secret `SKILLOPS_SWA_DEPLOYMENT_TOKEN`: the intended Static Web App's deployment credential.
- An existing writable `evaluation-results` branch for the validated data writer.

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

Existing public v1 reports remain unchanged. Full quality findings, improvement
traces and task-level checks still require a reviewed producer contract; the
viewer reports missing fields explicitly. Reviewed Skill text can now be attached
through the optional immutable sidecar described below, without inventing these
other fields.

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
billable workflows solely because the skeleton's validation passed.
