# Project onboarding and readiness evidence

Date: September 16, 2026

## Purpose and boundaries

Add reproducible external-project examples to SkillOps, supply a project-appropriate
initial skill when one is missing, and collect honest, project-scoped readiness
reports. This is the input/evidence side of the existing roadmap, not a replacement
for calibrated task evaluation, old/new skill comparison, or adoption approval.

Installing an initial skill in a sample is not evidence of improvement. Bootstrap
skills remain unvalidated drafts. Existing upstream skills are never overwritten.
The authoring-guide assessment under development remains a separate producer;
this change only checks minimum bundle integrity and does not assign rubric scores.
No model calls, production rollout, Azure resources, or dashboard implementation
are authorized by this feature.

## Samples and source identity

Keep the original root `sample_repo/`, evaluator inputs, historical evidence, and
immutable registration behavior unchanged. Add these independently identified
project snapshots:

| Project | Source | Revision | Purpose |
| --- | --- | --- | --- |
| `sample_repo` | The existing SkillOps `sample_repo/` | `5e239a461115309c2145abb6b0e2e597d7c15d6e` | Existing intentionally defective negative control; add the existing `develop` skill without repairing the seeded defects. |
| `project-a` | `dbader/schedule` | `82a43db1b938d8fdf60103bd41f329e06c8d3651` (`1.2.2`) | Real Python project without skills; add a scheduling-development draft and evaluator-owned core API checks. |
| `project-b` | `obra/superpowers` | `b36e0829c6d0140e93cfef2ca599b1b07d4a7797` (`v6.3.0`) | Real skill collection; preserve existing skills and explicitly leave behavioral evaluation unconfigured. |

Store each snapshot under `projects/<project-id>/`. Preserve upstream licenses and
ordinary source files. Download only public GitHub archives at full commit IDs;
never execute repository setup scripts, hooks, or discovered skill instructions
during import. Do not nest Git repositories. Omit symbolic links without following
them and record every omission, including Superpowers' `AGENTS.md` symlink. Reject
path traversal, absolute/Windows-special paths, duplicate or case-colliding paths,
unsupported archive members, and resource limits before publishing the directory.
Imports cannot replace an existing project directory.

Each `.skillops-project.json` manifest records schema version, project ID, adapter,
repository, full source commit, selected source subdirectory, source-tree SHA-256,
preserved license paths, omitted members, and added skill paths/hashes. A current
source hash distinguishes the original snapshot from subsequent local changes.
Reports never identify a modified snapshot as an unchanged upstream checkout.

IDs allow lowercase letters, digits, hyphens, and underscores, including
`sample_repo`; reject traversal and reserved Windows device names. Require ordinary
files and reject symlinks/junctions when reading inputs and writing results.

## Skill onboarding

Discover immediate skill bundles under `skills/`, `.github/skills/`,
`.agents/skills/`, and `.claude/skills/`. Do not treat fixtures anywhere else in the
repository as active skills. Evaluate each discovered `SKILL.md` as untrusted data.

When no skill exists, import requires an explicitly selected local bootstrap skill
and copies it into `.github/skills/<name>/SKILL.md`. The selected sample profiles
provide the matching bootstrap skill automatically. An unknown project with no
skill and no selected draft is blocked instead of receiving a generic fabricated
skill. Existing skills, including malformed ones, are preserved for review rather
than silently replaced. Re-running evaluation never creates or rewrites a skill.

Minimum checks cover UTF-8, bounded size, frontmatter delimiters, unique name and
description fields, a nonempty body, a valid name matching the containing folder,
and bounded nonempty description text. This is an explicitly limited integrity
check, not a complete YAML validator, authoring-guide score, security certification,
or measurement of trigger accuracy. Report bootstrap origin and unvalidated status.

## Evaluation and result contract

Use a separate standard-library project CLI, leaving the current model-evaluation
CLI and private registry untouched. Separate source import, checks/report storage,
and result publication into focused modules. Static inspection and report handling
work on Windows; actual source execution uses the existing Linux Docker boundary.

The `schedule-core-v1` adapter performs a named, frozen set of scheduling API
checks without sleeps, network access, or optional third-party dependencies. It
does not claim to run the complete upstream suite or cover timezone behavior.
The built-in issue-management adapter reuses the existing fixed expectations and
reports the deliberately seeded failures as failures, not as skill improvements.
The skill-collection adapter performs no behavioral or model execution.

Execute source only in unprivileged, network-disabled containers using a resolved
immutable image ID, read-only source/root mounts, no credentials/socket, dropped
capabilities, and the existing time, memory, process, and output limits. Keep
expected outputs on the host. Validate harness completion and exact case identities.
As in the existing evaluator, observations from a shared candidate interpreter are
not tamper-proof against hostile Python. Missing Docker/Linux is `blocked`, not a
fallback to host execution. Do not install a runtime or cloud resources silently.

Each valid evaluation request, including a blocked one, writes a new immutable
`results/<project-id>/<run-id>/report.json`. Run IDs follow the existing
`YYYYMMDDTHHMMSSZ-<12 lowercase hex>` form. Never overwrite a run. Reports include:

- Schema/kind, project/run identities, UTC start/completion times.
- Source repository/commit, original/current hashes, modification flag, licenses.
- Evaluator version, relevant code hashes, adapter, and actual container image ID.
- Discovered skills, relative paths, content hashes, upstream/bootstrap origins,
  minimum-check findings, and explicit unvalidated bootstrap status.
- Named project-check results and totals, or an explicit unconfigured/blocked code.
- Separate `task_evaluation: not_evaluated` and `adoption: not_authorized` states.
- A readiness conclusion: `ready_for_task_evaluation`, `review_required`,
  `changes_needed`, or `blocked`; none authorizes adoption.
- Observed model-call count of zero and unknown/unmeasured monetary cost.

Use fixed, bounded diagnostic codes. Do not publish raw model output, execution
logs, credentials, absolute local paths, or skill bodies in reports. Invalid project
IDs cannot create paths. A valid ID with missing/invalid inputs still gets a blocked
report with unavailable provenance represented explicitly, not invented.

Rebuild `results/index.json` from validated reports. Include each report's relative
path, exact content SHA-256, run identity, completion time, source commit when
available, and conclusion. Sort deterministically and keep all previous runs.
Reject duplicate JSON keys, mismatched path/embedded identities, malformed reports,
and conflicting run IDs. Report publication must not confer eligibility in the
existing registry.

## CI and accumulation

Add a project workflow for relevant push/PR changes and explicit manual runs.
Select projects from the changed paths; evaluator/workflow changes reevaluate all
projects. A results-only change does not trigger evaluation. Deleted projects retain
historical results but are not evaluated as if their source still existed.

The read-only evaluation job runs regression tests and real Docker controls, writes
reports for all selected projects, and uploads only validated public JSON as an
artifact. Report generation is distinct from project quality: the intentionally
defective sample remains `changes_needed`, and the skill-only example remains
`review_required`. Unexpected runner/report failures fail CI; known project findings
are visible in the job summary and reports rather than being hidden or relabeled.

Only a successful default-branch push/manual run can invoke a separate publisher
with `contents: write`. PRs and feature branches never receive publication authority.
The publisher uses the exact artifact from that run, validates it, and merges new
reports into the dedicated `skillops-results` branch in a temporary checkout. It
never writes to `main` or the contributor's checkout. Preserve immutable reports,
rebuild the combined index, use non-force pushes with bounded race retries, and
retain the artifact if publication cannot complete. Authentication is transient;
never put credentials in remote URLs or committed Git configuration. The results
branch contains only public results, so publishing cannot start an evaluation loop.

## Verification and delivery

Use red/green unit tests for safe archive import, missing-skill insertion, existing
skill preservation, malformed inputs, provenance, deterministic index construction,
blocked evaluations, immutable runs, changed-project selection, and publication
merges/conflicts. Use real Docker positive/negative controls in Linux CI, including
the real Schedule snapshot. Preserve and run the original regression suite.

The current workstation has no usable Linux/Docker installation. Run portable new
tests with an available Python interpreter and use the repository's Linux CI for
the original Linux-only suite and actual container evidence. Document what ran and
what did not. Download, inspect, and commit only sanitized actual run reports.

Keep public documentation in English and synchronize user-facing commands in both
READMEs. Private execution plans stay in the ignored `.superpowers/` directory.
Commit and push the implementation and reviewed evidence to the isolated worktree's
existing feature branch. Do not merge, force-push, create cloud resources, or claim
human review/skill adoption on behalf of another contributor.
