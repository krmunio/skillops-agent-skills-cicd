# Self-Evolving Agent SkillOps

[English](README.md) | [Korean](README.ko.md)

**CI/CD for agent behavior: discover Skill bundles, generate improvement candidates, and verify their effects before adoption.**

Code has CI/CD. Agent instructions should have a controlled change process too.
Self-Evolving Agent SkillOps connects observed problems, proposed instruction changes,
versioned evaluations and project regression evidence. "Self-evolving" means generating and
evaluating candidates, not silently rewriting or deploying the original Skill.

## Improvement loop

```text
Project snapshot
  -> Discover Skill bundles
  -> Assess instructions and collect improvement evidence
  -> Generate one candidate per eligible Skill
  -> Reevaluate the candidate against the original
  -> Check supported project behavior for regressions
  -> Preserve versions, results and decisions
```

A Skill is not necessarily one file or one workflow. Discovery looks for `SKILL.md` under
`.github/skills`, `.claude/skills` and `skills`, including nested directories. Its containing
bundle can include reference documents, scripts and assets. Same-name Skills at different
paths remain distinct; versions capture bundle contents, not only the entrypoint.
Candidate generation currently changes the entrypoint body while preserving its frontmatter
and supporting files.

## Evaluation model

| Area | Question | Evidence |
| --- | --- | --- |
| Skill baseline assessment | Are the instructions clear, and is the proposed improvement grounded? | Anthropic-guide-inspired instruction quality and APO-inspired links between findings, hypotheses, changes and reevaluation. |
| Project evaluation | Does the changed Skill perform useful work without introducing regressions? | Common-task comparisons and actual project checks, with quality, failures, cost and time kept in their recorded scopes. |
| Adoption | Was the candidate actually accepted or applied? | Explicit adoption records; a better score or passing checks alone do not establish adoption. |

The legacy common-task benchmark and discovery-based project checks are separate execution
paths today. The CLI command named `baseline` still runs the legacy original-Skill benchmark;
it does not mean the instruction-quality section in the dashboard design.
Missing checks, unsupported execution, failed stages and unavailable measurements remain explicit.
No suitable verifiable work means execution effect is `unverified`, not an invented success.

### In design, not yet implemented

The dashboard redesign will separate project, Skill-bundle and version/candidate selection,
show adoption and a short summary first, stack Skill baseline assessment above project
evaluation, and keep hashes and detailed evidence behind expandable controls.

Evaluation sets are being designed as selectable add-ons, with Anthropic-guided quality and
APO-based improvement evidence selected by default. Multiple sets can be compared and selected.
This catalog/configuration capability is not implemented yet. Historical results must retain
their original evaluation-set versions; incompatible scores must not be silently combined.

## Collaboration

See the [roadmap](docs/ROADMAP.md) for planned capabilities and dependencies, and
[CONTRIBUTING](CONTRIBUTING.md) for choosing work, coordinating changes and submitting
verification evidence. Planned work is not a claim of implemented functionality.

## Multi-project foundation

Project snapshots now live under `projects/`, including `projects/sample_repo/`.
The owner-operated [public dashboard](https://agreeable-pebble-0ea54a800.6.azurestaticapps.net/)
is available; its screen design is evolving.
See [project evaluation](docs/PROJECT-EVALUATION.md) for the `results/` schema,
gated Actions workflow, data branch and static dashboard. The Anthropic-inspired
guide evaluator in `skill_guide.py` is implemented and used by the baseline runner.
The project path now discovers local Skills, evaluates their quality, generates one candidate,
reevaluates it and compares original/base/candidate project checks. No root adapter registration
is required. Missing supported checks or a suitable work item leaves execution effect unverified.
Model-backed workflow execution still requires explicit settings and authentication.
Actions log groups and the run summary distinguish Anthropic baseline quality,
APO-inspired evidence linkage, and project regression checks. Inspect saved evidence
without model calls using `python3 evaluation_reporting.py --results results --run-id <saved-run-id>`.
These are presentation changes, not independent evaluator jobs or an APO score.
Main pushes select added/changed projects; shared evaluator changes select the catalog.
Each selected Skill gets at most one candidate within the shared run limits. Manual
`--project <id>` selection is unchanged; `--changed-since <full-before-sha>` selects
affected projects from the checked-out `--source-commit`. The two selectors are mutually
exclusive. No affected projects means no model evaluation, not a fabricated pass.
New automatic Skill runs retain report-bound `stage-metrics.json` attachments with
stage completion, admitted CLI attempts, elapsed time and available usage. Unknown
usage remains null; the Actions summary distinguishes partial measurements from
totals and displays the recorded assessment policy. Existing result formats and
dashboard behavior are unchanged.
Manual Actions runs can specify `max_invocations`, `max_seconds` (up to 7200), and
`max_ai_credits` for an explicit `project`. All its Skills share the call/time budget;
Credit limits remain per session. Omitting overrides preserves repository defaults.

The Skill dropdown combines detected inventory and retained history, including
unevaluated Skills. Each of project-a and project-b has three layout-development
rounds (improved, unchanged, regressed) under `results/<project>/sample-<run>/`,
using native report, assessment and evolution contracts. They include original/candidate
text, generation evidence/diffs, baseline quality and project evaluation.
The normal history/viewer has no separate example badge; inspect `origin: sample`
in provenance metadata. These are not measured outcomes and never enter live
evaluation history or current-version qualification. The publisher appends them
with `merge-samples`; static builds only read stored results. No paid calls,
source changes or candidate adoption are performed.

### One recorded development replay

The opt-in adapter evaluates one candidate against a recorded development
WorkItem and fixed original Skill. It does not replace the existing automatic
single-candidate path:

```bash
python3 skillops.py replay --project <project-id> --skill-key <discovered-skill-key> \
  --work-item <private-development-json> --results <replay-results-directory>
```

This command **blocks without model calls** unless `--live` is explicitly added
and the environment provides `SKILLOPS_LIVE_EVALUATION_ENABLED=true`,
authentication (`COPILOT_GITHUB_TOKEN` or `GITHUB_TOKEN`), approved
`SKILLOPS_MAX_INVOCATIONS`, `SKILLOPS_MAX_SECONDS` and
`SKILLOPS_MAX_AI_CREDITS_PER_SESSION`. Obtain separate authorization for the
project, Skill and limits before enabling it. Credit is a per-session soft cap,
not an aggregate monetary guarantee. All stages share one call/time budget.
The project must be under `projects/`; the private WorkItem pins its actual Git
commit, request, source hashes and protected checks. See the
[replay contract](docs/HACKATHON-CONTRACTS.md) for its schema and callback API.

Each run stores immutable `report.json`, complete `skill-evolution.json` and
`replay-evaluation.json`; the CLI returns the exact evidence reference.
Confirmation remains `not_run` with `confirmation_isolation_unverified` and
approval eligibility is always false. Development improvement is not final
confirmation, approval or verified next use. The command never updates Active.
Development iteration is connected by the `iterate` command below.
Approval/next-use CLI and live iteration Actions remain separate.
The official publisher now preserves validated replay/cycle graphs; unsupported
adoption publication remains blocked.
Offline integration tests simulate only model/container boundaries and label
their results `offline_test`, never measured model improvement.

### Bounded development iteration and offline demo

```bash
python3 skillops.py iterate --project <project-id> --skill-key <discovered-skill-key> \
  --work-item <private-development-json> --confirmation-work-item <private-confirmation-json> \
  --max-rounds 2 --results <iteration-results-directory>
```

This uses the actual `run_cycle` module and the same replay persistence callback.
The original remains the comparison baseline; only the parent candidate and
development feedback change. Preparation, every round and optional confirmation
share one runtime and authorized budget. The same `--live`, authentication and
limit gates apply; the example alone makes no model calls. Confirmation is
optional, frozen before generation and still blocked by the isolation guard.
No candidate is approved or made Active.

Each saved evaluation has its own run. A terminal `cycle.json` and aggregate
`report.json` bind those runs; the actual loader revalidates their hashes and
complete captures. An admitted attempt ending before persistence has null
`run_id`, `evaluation_ref` and `decision`; unstarted rounds are not invented.
A persistence callback failure propagates without a terminal cycle or success
receipt, while earlier stored rounds remain unchanged. There is no automatic
retry or recovery. Exit 0 denotes normal development termination, not final
confirmation or adoption; budget/runtime/unverified outcomes return 2.

### Separate local approval

The implemented `approve` command requires a separately operated interactive
terminal, a live cycle with passed confirmation, and the exact complete bundle
and evidence hashes. CI/Actions, piped input and missing predecessor fields are
rejected. Both `none` values mean explicitly no prior Active, never a wildcard.

```bash
python3 skillops.py approve --project <project-id> --skill-key <skill-key> \
  --candidate-version sha256:<full-bundle-hash> --cycle <cycle-id> \
  --evidence-sha256 <cycle-file-sha256> --expected-active-version none \
  --expected-active-execution-sha256 none --results <results-directory>
```

Review the displayed binding and type `approve <full-candidate-version>` separately.
Approval calls no model and does not change Active. The original Skill and evidence
stay immutable. Canonical private WorkItems are retained before replay execution;
no private requests/operator identities are copied to public results.
The current development-only/offline outputs are **not approvable**. Runtime
isolation probes are implemented, but confirmation provider, next-use and adoption
publication integration remain separate checkpoints; no operational success is
implied by the synthetic authorization-contract tests.

### Offline presentation backup

For a **no-paid-calls terminal demo/backup**, use a clean committed integration
checkout and a new output directory:

```bash
SKILLOPS_LIVE_EVALUATION_ENABLED=false python3 tests/export_iteration_evidence.py \
  --output /tmp/skillops-iterate-demo
python3 -m json.tool /tmp/skillops-iterate-demo/manifest.json
python3 project_results.py validate --results /tmp/skillops-iterate-demo/n2-feedback
```

The exporter runs production provider/loop/storage modules with synthetic inputs
and simulated model/container transport. It does not copy prebuilt result
fixtures. The manifest contains the integration SHA, scenario directories,
cycle/run IDs and hashes for N=2 feedback, early stop, maximum rounds, budget
exhaustion, unsaved attempts and persistence failure. All results are explicitly
`offline_test`; confirmation stays unverified/not run. Existing outputs are never
overwritten. The official builder includes `trace.js` and hash-verified replay/cycle
links. Public deployment still requires separate authorization; design PR #32 is
not part of this demo.

To prepare the read-only local screen, merge reviewed historical reports and the
generated `n2-feedback` directory into a new results directory, then use the
official build (never copy private runtime directories):

```bash
python3 project_results.py merge --incoming <reviewed-live-results> --results <demo-results>
python3 project_results.py merge --incoming /tmp/skillops-iterate-demo/n2-feedback --results <demo-results>
python3 project_results.py build --results <demo-results> --output <new-site>
python3 -m http.server 8765 --bind 127.0.0.1 --directory <new-site>
```

Use `/?project=<project>&run=<exact-cycle-id>&skill=<skill-key>` on the loopback
server; cycle IDs and run references are in the exporter manifest. The screen
labels `offline_test` as non-measured and keeps final confirmation/approval
incomplete. Historical reports retain their original bytes and outcomes.
Retain screenshots and a file-openable gallery as a no-server backup.

### Prepared project samples

- `projects/sample_repo`: original issue-management seeds plus an explicitly added development skill.
- `projects/project-a`: pinned `dbader/schedule` 1.2.2 source with a scheduling-specific, unvalidated skill draft.
- `projects/project-b`: pinned `obra/superpowers` v6.3.0 source with its existing skills preserved.

```bash
python3 project_samples.py verify --project project-a
python3 project_samples.py verify --project project-b
python3 project_samples.py add-skill --project sample_repo --skill skills/develop/SKILL.md
```

The portable helper imports public commit-pinned archives into unused project
directories, preserves licenses/hashes, and requires a selected draft if skills
are missing. See [preparation commands and boundaries](docs/PROJECT-EVALUATION.md#reproducible-sample-preparation)
before importing another repository. It never executes upstream code or replaces
existing skills. Draft installation is not successful behavioral evaluation.

The project workflow's `sample-onboarding-results` artifact verifies actual
non-model reports using the existing evaluator and schema. Project guide assessment
remains blocked in this deliberately non-model job, and execution is `live_disabled` for all
prepared projects. Green contract tests do not change these
blocked/unconfigured states or authorize Azure deployment.
Existing published reports and optional validated skill-history sidecars are retained
byte-for-byte when adding new sample runs.
See the [owner's dashboard direction](docs/PROJECT-EVALUATION.md#owner-dashboard-direction)
for baseline/project separation, target skill/version selection, guide/APO evidence,
execution comparisons, skill diffs, history and supported execution boundaries.

## Repository validation

The **SkillOps validation** GitHub Actions workflow runs offline runner tests,
real Docker positive/negative controls and checks of the historical evidence
snapshot. Each run records the checked-out commit, Python/Docker/image versions
and test log in its summary and `baseline-ci-<commit>` artifact.
No Copilot credentials or live model calls are used by this workflow.

See `evidence/README.md` and `evidence/baseline-v2.json` for the sanitized actual
local baseline. Historical model results and the current CI execution are
different evidence sources; CI success is not a fresh model-evaluation result.
Source changes are proposed through a feature branch/PR; this milestone does
not deploy, promote or roll back skills.

## Current implementation

The discovery-based project path is described above. The legacy benchmark evaluator supports
three independent Python issue-management
families, an unchanged development skill v1, five coding tasks, protected
checks, a calibrated independent judge, and JSON/Markdown reports.

The LLM can now author a candidate development skill directly from historical
development feedback, and the runner can compare it with the unchanged base
on fresh paired tasks. Generated instructions and their rationale are hypotheses,
not proven improvements. Canary deployment, promotion and rollback are not implemented.

Legacy local project registration binds a project, the `develop` skill and the
`issue-management-v2` evaluation set. Bound evaluation uses the registered source
files and an immutable initial skill pin. A read-only eligibility command verifies
recorded evidence; it does not install or deploy a skill.

Requirements: Linux, Python 3.12+, Git, an installed and authenticated GitHub
Copilot CLI with access to the chosen model, and a running Docker daemon.
All Python dependencies are standard-library modules.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 skillops.py doctor
```

`doctor` makes no model calls and does not claim authentication is working.
It reports the CLI controls, enabled judge skills and Docker/image availability.
It does not pull an image automatically. If the official image is missing:

```bash
docker pull python:3.12-slim
```

Each evaluation records and executes the resolved immutable local image ID,
not a mutable tag. Image, CLI or evaluation-input changes invalidate calibration.

## Dedicated authentication

Authenticate from your terminal:

```bash
python3 skillops.py login
```

This uses a project-owned private Copilot profile, not your personal profile.
Complete the CLI's browser/device login yourself. Do not paste tokens into
source files, reports or chat. Profile credentials and CLI state stay under
`.skillops-private/`, which is private and ignored by Git.

An explicitly exported standard Copilot token environment variable is also
supported, but never copied into a report or printed.

## Live conformance probe

```bash
python3 skillops.py probe --model gpt-6-astra
```

This invokes the model and can consume Copilot usage. It requests no tools,
disables discovered skills, custom instructions, hooks and memory, and rejects
unexpected plugins, custom MCP servers and alternate-provider registries.
Authentication failure is a blocker, not a successful evaluation.

The probe asks for dummy-file access, verifies an actual zero-tool manifest
and no tool calls, and requires the `NO_TOOLS` response. This is a narrow
conformance check, not proof of general isolation. Every measured call also
validates its observed model, tool inventory and terminal completion.

The installed CLI was verified with `--available-tools=skill` for development
and `--available-tools=skill --excluded-tools=skill` for judge calls.
An empty `--available-tools=` does **not** disable tools in this tested version.
The runtime does not use `--no-auto-login`, which prevented use of the saved
dedicated login during verification.

## Run the baseline

These commands consume actual Copilot usage. There are nine judge calls
for calibration (three per family), then one developer and one judge call for
each of five tasks: 19 coding/calibration calls for a new sequence, plus the
size-dependent skill-guide judge calls described below.
There are no automatic model retries or silent output repairs.

```bash
python3 skillops.py calibrate --model gpt-6-astra
python3 skillops.py baseline --model gpt-6-astra
```

Calibration checks authored correct, defective and instruction-in-data controls
for every family. Within each family, the correct control must outrank the
defective control overall and on correctness; the injected defective review
must not receive full marks. Missing, duplicate or failed family groups block
the baseline.
Baseline requires a successful matching calibration and reuses it without
additional calibration calls. The fingerprint covers rubric, fixtures, tasks,
protected checks, all family seeds, runner/evaluator/runtime code, model, CLI version,
controlled judge settings/inventory and image ID.

Each task starts from its family's seed in a fresh disposable Git repository.
An evaluator-owned registry fixes seed paths and callable names; the catalog
cannot select arbitrary paths or imports. The seed is copied to `issues.py`,
so proposal paths and the isolated test module stay the same for every family.
The developer actually invokes the staged `develop` skill, then returns only
`issues.py`, `test_generated.py` and a review as JSON. It has no shell or
file-writing tools. The trusted runner applies the files, captures `git diff`
and executes the generated Python inside Docker.

The judge runs in a new session and workspace without development skills or
tools. It receives only the current task, rubric and code/execution evidence.
Neither other family contracts/task requests nor protected expected values
reach the developer.

## Catalog v2

| Family | Callable | Tasks / split | Protected checks per task |
|---|---|---|---|
| Listing | `list_issues` | 3 development | 26 |
| Labels | `normalize_labels` | 1 development | 17 |
| Updates | `update_issue` | 1 held-out | 22 |

Labels exercise stable whitespace/Unicode casefold normalization and validation.
Updates exercise validated, nonmutating atomic patches. Their seeds are in
`projects/sample_repo/labels.py` and `projects/sample_repo/updates.py`; full contracts are in
`eval/tasks.json`.

A family cannot appear in both development and held-out splits. The previously
evaluated listing boundary request is now a development regression; the new
updates family is held out from development-task inputs. Historical v1 reports
keep their original split labels. Held-out separation applies to generator inputs;
it does not claim that benchmark authors have never seen the held-out data.

## Anthropic skill guide evaluation

Baseline also discovers skills under the evaluated project's `.github/skills`,
`.claude/skills` and `skills` roots. Each `SKILL.md` defines an independently
evaluated bundle. Nested skills are evaluated separately, and their files are
excluded from the parent bundle.

- **Calls and usage:** a small single-file skill needs one guide-judge call.
  Large text bundles are split into bounded batches with additional calls;
  model call count and usage increase with skill size. Binary assets contribute
  only metadata (path, size and hash); their contents are not sent to the model
  as text.
- **Static checks:** required YAML frontmatter with nonempty `name` and
  `description`, a nonempty body, and local references that exist within the
  bundle. Warnings cover `SKILL.md` exceeding the 500-line recommendation and
  Markdown references over 300 lines without a table of contents.
- **Zero-tool judge dimensions:** trigger description, workflow clarity,
  generalization, instruction quality, progressive disclosure, resource
  organization and principle of lack of surprise.
- **Small-skill applicability:** for single-file skills without supporting
  resources, `progressive_disclosure` and `resource_organization` are
  `not_applicable` (N/A) and excluded from the score denominator, not penalized.

Bounded skill summaries appear under the `anthropic_skill_guide` key in
`report.json` and the `Anthropic skill guide` table in `report.md`. Each summary's
project-relative `artifact` points to a per-skill `result.json` containing full
static findings, applicability and judge dimensions/scores/rationales; these are
not duplicated in the baseline. Large metadata fields are identified by SHA-256
and UTF-8 byte length instead of copying source text. Repeated reference findings
carry explicit counts and target hashes. Source evidence is redacted before
batching and result storage: GitHub/runtime tokens and absolute local paths are
removed; stored judge rationales are also redacted. Relative file identities and
original snapshot hashes are retained. Artifact IDs use the full path SHA-256;
collisions and existing artifact directories/results fail without overwriting.
Guide scores and findings are
**report-only**: they do not change coding-task outcomes or canary/promotion
decisions. This is automated guidance, not Anthropic certification or a
replacement for human calibration.

Each result artifact is limited to 2 MiB of serialized UTF-8 JSON. Overflow
records only that skill as `blocked` with `skill_result_limit`, without blocking
the baseline. Judge rationales, including merged rationales, are limited to 4,096
UTF-8 bytes; overflow is an explicit validation failure, never silent truncation.

## Read the evidence

Commands print their run-specific artifact paths. Under `runs/<unique-id>/`:

- `calibration.json`: observed control scores, pass/fail gate and fingerprint.
- `report.json` and `report.md`: baseline results and readable summary.
- Per-task folders: normalized developer/judge calls, proposal and actual diff.
- `anthropic-skill-guide/<skill-id>/`: full `result.json`, `manifest.json` and
  individual `judge-<batch>.json` receipts.

Baseline JSON is the report source of truth. It includes per-check outcomes,
generated-test counts, rubric scores/rationales, source/input hashes, elapsed
time and per-call usage. All requested tasks retain a row, including failures.
Correctness means passing **the entire family-specific protected suite**, divided
by all requested tasks. Mean judge score uses only completed evaluations and
shows its denominator. Reports also include split and family summaries.
The aggregate is task-weighted: five tasks across three families are not five
independent samples, and the three listing tasks still share a seed/contract.
A completed evaluation can still have incorrect code; judge marks cannot
override protected failures.

Usage retains CLI units (`nano_aiu`, premium request units, tokens and
milliseconds). Missing values are `null` with `not_reported_by_cli`, never zero
or an estimated dollar amount. Calibration/probes are separate from baseline
usage; include them when accounting for the complete workflow.

For baseline/calibration, exit code 0 means the requested workflow completed (or calibration passed).
Exit code 2 means a blocker, calibration failure or incomplete task evaluation.
It does not mean the measured code is correct merely because baseline exited 0.

The initial actual Astra baseline on September 14, 2026 completed all three
tasks, passed 20/20 protected checks per task and received a mean judge score
of 100/100. This tiny synthetic result is not evidence of statistical
generalization or skill improvement.

The strengthened listing suite retains those original 20 cases and adds six checks
for valid page sizes 1 and 3, including later and filtered pages. The initial
report remains historical evidence; changed checks require fresh calibration
and a new baseline rather than overwriting earlier scores.
The subsequent actual run on September 14, 2026 passed 26/26 checks for all
three tasks and again received a mean judge score of 100/100. This establishes
the strengthened baseline, not a measured skill improvement.
Those historical three-task results are not directly comparable to catalog v2's
expanded population. New reports use schema version 2 and record catalog version
and per-task family/seed metadata.

The actual catalog-v2 run on September 14, 2026 completed all five tasks:
listing passed 26/26 checks per task, labels 17/17 and updates 22/22.
All family calibration gates passed, and the task-weighted mean judge score
was 100/100. The expansion adds distinct targets, but its scores still show a
ceiling; it is not a claim that the development skill improved.

## Generate and compare a skill candidate

```bash
python3 skillops.py propose --baseline BASELINE_RUN_ID --model gpt-6-astra
python3 skillops.py calibrate --model gpt-6-astra
python3 skillops.py compare --candidate CANDIDATE_RUN_ID --model gpt-6-astra
```

Use the generated run IDs, not filesystem paths. The baseline must exist locally;
the public historical projection alone is not an input report for `propose`.
When a matching calibration already exists, comparison can reuse it. Code,
benchmark, runtime or judge-context changes require fresh calibration.

`propose` makes one actual zero-tool Copilot generator call. Only current
development-task requests and numeric feedback are selected from the historical
report, along with the existing skill. Held-out rows, full-report aggregates,
model response text and source-code answers are not sent to the generator.
Static benchmark/rubric/control/seed hashes and the original skill must match.
Historical runner differences are recorded, not passed off as fresh comparison.
If development tasks have no observed failures, the prompt says so and asks for
an efficiency hypothesis rather than inventing failure-driven learning.

The model returns a replacement instruction body and a rationale. The runner
preserves the `develop` frontmatter and saves `base-SKILL.md`, `SKILL.md`,
`generator.json` and `candidate.json` in a new run directory. The existing v1
file is not changed. Bounded schemas and rejection of obvious benchmark names/code
are narrow safeguards, not proof that generated instructions are universally safe.
Hashes identify snapshots; they are not signatures against malicious local edits.

`compare` executes five fresh base/candidate pairs (20 developer/judge calls),
alternating which arm runs first. Both arms use the same task executor,
benchmark, current calibration, model and explicit skill snapshots; every role
gets a fresh session/workspace. It never reuses historical baseline executions as
the comparison's base arm. Incomplete pairs remain in the requested denominator.
Including a fresh nine-call calibration and one generator call takes 30 model
calls; failed prerequisites stop dependent calls, with no automatic retries.

`comparison.json` and `comparison.md` record order, hashes, task evidence,
per-arm/split summaries and the decision. Demo policy v1 requires:

- Complete pairs and passing fixed/generated tests for every candidate task.
- No decrease in any paired judge dimension.
- Both total task cost and time at most 5% worse than the fresh base.
- At least one corrected fixed/generated failure or increased judge dimension,
  **or** at least 10% lower total task cost or time.

Cost is actual developer-plus-judge NanoAIU; time sums complete task elapsed
seconds. Generator/calibration usage is separate and must be included when
accounting for the full optimization workflow. Missing metrics are not zero.
One pair per task, shared-model judging and variable cache/network effects do
not establish statistical superiority or family-independent generalization.

Comparison exits **0** for provisional `eligible_for_canary`, **1** for
`rejected`, and **2** for `blocked`. Eligibility does not deploy anything.
Rejection or no measured improvement is a valid outcome; the workflow does not
keep generating candidates until it can advertise a favorable score.

### First actual candidate outcome

On September 15, 2026, the generator authored a candidate proposing reuse of a
requirement-to-assertion checklist. The base was 1,028 bytes and the candidate
2,410 bytes; greater length was not treated as evidence of improvement.
The actual generator, nine calibration controls and twenty paired calls used
30 distinct CLI sessions. Both arms completed five tasks and passed every
protected/generated suite, with mean judge score 100/100 and denominator five.

| Metric | Fresh base | Candidate |
|---|---:|---:|
| Developer + judge cost (NanoAIU) | 125,712,450,000 | 127,772,200,000 |
| Total task elapsed seconds | 296.064 | 302.923 |

The observed candidate cost was 1.64% higher and elapsed time 2.32% higher.
Policy returned **rejected** because neither quality nor the required efficiency
improvement was observed. This is one small comparison, not proof of statistical
degradation. The base skill stayed unchanged; no candidate was deployed.
These figures exclude generation and calibration costs.

Local receipt IDs: candidate `20260915T005404Z-611d681b8bf7`,
calibration `20260915T010210Z-45057be9ce19`,
comparison `20260915T010916Z-74058d9807da`. Full receipts remain in ignored
`runs/`; these identifiers are not links to committed public artifacts.

This historical run predates the oversized-metric input guard. Its original
receipts and fingerprint are preserved; the guard changes the source fingerprint
and requires matching fresh calibration before a new live comparison.
The guard was checked offline against the recorded decision, not by rerunning
the models. It adds no spending cap and does not change the selection policy.

## Register a project and check eligibility

These commands are offline: they require neither Copilot authentication nor Docker.

```bash
python3 skillops.py register --repository sample --path projects/sample_repo \
  --skill develop --evaluation-set issue-management-v2
python3 skillops.py repositories
```

Registration stores an immutable snapshot of the current engine
`skills/develop/SKILL.md`, identified by its SHA-256. Changing that source file later
does not change an existing pin. Duplicate IDs or paths are rejected rather than
overwriting a registration. IDs are lowercase slugs, not GitHub URLs.
Relative paths are relative to the SkillOps installation; absolute local paths
can identify another project checkout.

The first adapter requires `issues.py`, `labels.py` and `updates.py` directly in
the target directory, with the existing issue-management contracts in
`eval/tasks.json`. Each family is evaluated as a standalone Python source file.
Registration checks input shape and availability, not correctness. It does not
clone a remote repository, install an application's dependencies, execute its
arbitrary test command, or support other languages automatically.

For an **explicitly approved live evaluation**, use the registered ID:

```bash
python3 skillops.py calibrate --repository sample --model gpt-6-astra
python3 skillops.py baseline --repository sample --model gpt-6-astra
python3 skillops.py propose --baseline BOUND_BASELINE_RUN_ID --model gpt-6-astra
python3 skillops.py compare --repository sample --candidate CANDIDATE_RUN_ID --model gpt-6-astra
python3 skillops.py eligibility --repository sample --comparison COMPARISON_RUN_ID
```

The first four commands call the model; `eligibility` does not. Generation inherits
the baseline's binding and still receives development feedback only. Calibration
uses evaluator-owned controls, not target code relabeled as a known-bad control.
The registered source bytes and pinned skill actually enter baseline/comparison
execution. Other project files are not mounted into the execution container.
Existing commands without `--repository` retain the unregistered workflow.

Bound reports include repository/skill/evaluation identities and source/pin hashes
in their fingerprint. A changed target, pin or evaluator requires current evidence,
including matching calibration. Bound comparison records commit both candidate
metadata and the exact consumed calibration bytes; successful finalization records
the comparison digest in the local registry.

Eligibility requires that recorded digest, unchanged inputs and snapshots, valid
calibration, matching task/activation evidence and an identical recomputed decision.
It returns **0: eligible_for_canary**, **1: rejected**, or **2: blocked**.
Every outcome leaves the pin and target files unchanged. Old unbound reports,
unrecorded imports and modified evidence cannot grant deployment eligibility.
Eligibility is a current observation, not a durable deployment authorization.

State is bounded, owner-only and ignored by Git under `.skillops/`. Updates use a
nonblocking lock and atomic replacement. Local paths and skill contents remain
there; do not publish that directory. The registry-owning operator is trusted:
these checks are not remote attestation or protection against a hostile local owner.
There is no pin-update command yet; canary, promotion and rollback own that later
transition. PR-triggered evaluation and a live dashboard feed are also not implemented.

The bound integration is covered by synthetic transport regressions and actual
offline registration/denial checks. No new live bound-model comparison was run for
this implementation. Historical model results remain unchanged; the changed engine
fingerprint requires fresh calibration before future live execution.

## Execution boundary and validation

Generated Python never runs on the host. Containers use no network, a read-only
root/source mount, an unprivileged user, dropped capabilities and no-new-privileges.
They have no credentials or Docker socket, and are bounded to 15 seconds,
256 MiB memory, one CPU, 64 PIDs and 1 MiB combined output.
CLI calls have 180-second and 4 MiB limits.

Expected outputs stay on the host. An evaluator-owned runner collects actual
values and runs generated unittest tests; printed success claims and ordinary
early exits are not accepted as completion.
However, imported candidate Python shares its container interpreter with the
harness. Observations are **not tamper-proof against hostile Python**.
This is a non-adversarial synthetic demo, not a security certification.
Judge process separation also does not eliminate shared-model bias.

```bash
# Offline runner tests; no installed Copilot, model calls or Docker needed.
python3 -m unittest discover -s tests -p 'test_*.py' -v

# Also execute container positive/negative controls; requires the image.
SKILLOPS_CONTAINER_TESTS=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The dedicated profile is serialized by a nonblocking lock. Temporary developer
and judge workspaces are removed after their calls; private CLI receipts and
run evidence remain. `.skillops-private/` and `runs/` are ignored by Git.
Only synthetic run artifacts should be shared, never the private profile.
Offline tests explicitly mock CLI executable discovery and CLI responses;
these mocks are not evidence of real CLI conformance.
