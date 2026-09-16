# Projects, results and public dashboard

## Current foundation

- The sample lives in `projects/sample_repo/`; its source bytes are unchanged.
- `project_profiles.json` selects the supported `issue-management-v2` execution adapter.
- `project_results.py` validates public records, imports reviewed historical summaries,
  appends immutable reports, rebuilds indices and builds an allowlisted static site.
- `project_evaluation.py` emits independent guide/execution states. Live execution
  is disabled until explicit settings and authentication are provided.
- The project workflow validates PRs without model/deployment secrets. Trusted main
  pushes and main-only manual dispatch record assessments or explicit blocked states.
- Results persist on `evaluation-results`; main still requires a reviewed PR and CI.

**The shared skill-guide producer is not integrated yet.** Projects with skills
receive `blocked / guide_integration_pending`, not invented scores. Projects
without skills receive `not_assessed / no_skills`. Unsupported execution adapters
receive `configuration_required`; arbitrary project commands are never executed.

## Add a project

Add a reviewed ordinary source directory under `projects/<safe-id>/` and commit
its contents. IDs allow lowercase letters, digits, underscores and hyphens.
Preserve source licenses. An embedded `.git` directory or a submodule is not a
supported source snapshot. Do not add credentials, `.env` files or generated data.
Limits: 50 projects, 10,000 files and 128 MiB per project, 1 MiB per input file.

Project-local skills are discovered under `.github/skills`, `.claude/skills` and
`skills`. Root `skills/develop` is not automatically attributed to every project.
Execution requires an entry in the root-owned profile map:

```json
{"schema_version": 1, "projects": {"sample_repo": {"adapter": "issue-management-v2"}}}
```

The existing adapter expects the issue-management sample contracts; it does not
run arbitrary tests for another language. Update the profile map when removing a
configured project. Historical results are retained.

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

Before enabling live evaluation, configure repository settings deliberately:

- Secret `COPILOT_GITHUB_TOKEN`: an account/token authorized for the configured Copilot model.
- Variable `SKILLOPS_LIVE_EVALUATION_ENABLED=true`: explicit billable-execution opt-in.
- Positive `SKILLOPS_MAX_INVOCATIONS` (maximum 1000) and `SKILLOPS_MAX_SECONDS`
  (maximum 1200), shared across the workflow's sequential project evaluations.
- Secret `SKILLOPS_SWA_DEPLOYMENT_TOKEN`: the intended Static Web App's deployment credential.
- An existing writable `evaluation-results` branch for the validated data writer.

No values are supplied or live evaluation enabled by this skeleton. Missing
settings create explicit blocked records without model invocation.
The invocation cap bounds CLI sessions, **not internal model requests or money**.
The remaining time is passed into the subprocess deadline. Record actual usage;
do not infer a strict monetary cap from these controls.

Relevant main changes conservatively assess the catalog. A result-only update
does not retrigger evaluation. A blocked assessment makes its evaluation job fail
while the writer can still persist the public blocked result. Raw run directories
are never uploaded as Actions artifacts.

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
