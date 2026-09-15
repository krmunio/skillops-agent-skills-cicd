# skillops-agent-skills-cicd

Agent-skill CI/CD, starting with a reproducible coding-task baseline.

## Repository validation

The **Baseline validation** GitHub Actions workflow runs offline runner tests,
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

The baseline evaluator now supports three independent Python issue-management
families, an unchanged development skill v1, five coding tasks, protected
checks, a calibrated independent judge, and JSON/Markdown reports.

Candidate generation, version comparison, canary, promotion and rollback are
not implemented. This milestone establishes a measured starting point, not
an improvement claim.

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
each of five tasks: 19 calls for a new complete calibration/baseline sequence.
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
`sample_repo/labels.py` and `sample_repo/updates.py`; full contracts are in
`eval/tasks.json`.

A family cannot appear in both development and held-out splits. The previously
evaluated listing boundary request is now a development regression; the new
updates family is held out from development-task inputs. Historical v1 reports
keep their original split labels. This does not claim that benchmark authors
have never seen the held-out data, or that a future optimizer is already implemented.

## Read the evidence

Commands print their run-specific artifact paths. Under `runs/<unique-id>/`:

- `calibration.json`: observed control scores, pass/fail gate and fingerprint.
- `report.json` and `report.md`: baseline results and readable summary.
- Per-task folders: normalized developer/judge calls, proposal and actual diff.

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

Exit code 0 means the requested workflow completed (or calibration passed).
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
