# Hackathon contracts: replay, bounded improvement, explicit adoption

Contract revision: **1.4** (reviewed confirmation admission and runtime boundary;
public schema version 1 and revision 1.3 persistence semantics retained).
Original code baseline: `3a6a2a3`.
The operator confirmed **explicit local CLI approval** on September 17, 2026.
Interface declarations do not authorize model calls; delivered APIs are identified
in the implementation handoffs below.
Revision 1 was accepted in PR #24. This follow-up fixes the provider boundaries
requested by sessions 3 and 4; API declarations below are not implementation evidence.
Revision 1.2 supersedes revision 1.1's `development_scope` and context-only
feedback signature. It accepts session 2's issue #25 proposal, with the explicit
binding checks below. Commit `9a2ce57` provides replay source/outcome helpers only;
it is not completion of `prepare_replay`, `generate_candidate` or `evaluate_candidate`.
It also accepts session 4's ABA/concurrent-Active correction: private approval
and execution records bind both the previous version and previous execution
receipt hash. Public adoption projections remain unchanged.
Revision 1.3 retains those interfaces and distinguishes an admitted attempt
ending before durable evaluation storage from a persistence callback failure.
Only the former can become a terminal round with a null `run_id`. This is a
validation clarification within schema version 1, not a rewrite of prior evidence.
Revision 1.4 accepts the preregistration/disclosure/frozen-selection mechanism
reviewed in issue #36, with the runtime owner explicitly assigned by the operator
to session 1 on September 18, 2026. Section 10 defines the callable changes and
fail-closed implementation gate. This declaration does not establish that runtime
isolation, confirmation, local approval or next-use integration is implemented.
Dependent PRs must name the accepted contract commit; changes go through the
integration owner, not independent reinterpretations by each session.
No presentation file was available in this checkout or conversation attachments;
the three goals supplied in the task are the presentation requirements used here.

## 1. Scope and current implementation

Keep instruction quality, project execution, candidate qualification, operator
approval, and observed use as separate facts. A score improvement is not approval;
approval is not proof that an execution loaded the approved Skill.

### Post-presentation implementation audit: September 18, 2026

Audit baseline: main `fcf2cad6e61908be7461526365cf2a608220dbdf`, the merge
of PR #35. This is a code audit, not a new schema revision or authorization to
execute models. Earlier implementation handoffs below describe their named
commits, not completion of every target API in this contract.

| Contract / acceptance target | State at this baseline | Code evidence and remaining work |
| --- | --- | --- |
| Sections 1-2: legacy behavior, identities, strict encoding | Implemented for legacy/replay/cycle | Existing assessment path is separate; `project_results`, `evolution_records`, and `skill_assessments` validate exact fields, complete captures and canonical immutable sidecars. Adoption still needs the same treatment. |
| Section 3: recorded development input and real request replay | Partially implemented | `validate_work_item`, `prepare_replay` and `_replay_application` bind source/check/input identities and use the request in the developer prompt. The adapter does not yet persist WorkItems at the canonical private `work-items/<task>/<hash>.json` path required by approval. |
| Section 4: fixed-reference one-candidate evaluation | Implemented for development | `evaluate_candidate` reuses the original Capture and quality baseline, runs fresh paired applications and returns actual retained rows/captures. Confirmation is explicitly rejected by the provider. |
| Section 5: N=2 feedback lineage and bounded termination | Implemented for development | `run_iterations` delegates to the real `run_cycle`; preparation and rounds share one budget. Nullable admitted attempts and persistence-callback failures have different behavior. No duplicate loop is needed. |
| Section 5: precommitment and confirmation isolation | Partially implemented; enabling blocked | `_replay_session` freezes distinct WorkItems before model invocation; `run_cycle` selects once and has a lazy confirmation callback. Both `prepare_replay` and `_check_replay` reject confirmation. No provider-owned registration/one-use selection binding or enforced model-filesystem boundary exists. |
| Sections 3-5: protected evaluation | Implemented boundaries; confirmation claim incomplete | Source replacement allowlists, fixed plans, protected file hashes, Skill companion preservation, tool manifests and provider-issued feedback are checked. These do not establish filesystem isolation of the host model process. |
| Section 6: explicit approval separation | Not integrated | `skillops.main` has no `approve` command. PR #29's module is not on main; reuse its exact owner commits, not replacement state logic. Add CI/noninteractive rejection and separate human confirmation at the CLI. |
| Section 6: exact next use | Not integrated | No `run-approved` command or execution adapter exists. Fresh work/run/budget, staging/activation/post-invocation inventory and host-derived receipts must be connected to the session 4 API. |
| Section 6: durable local state / ABA protection | Owner module only, not on main | PR #29 `00babd180f5bd8b03373d5f16dfd8f0eda2a328a` contains locked version/receipt-pair comparison, durable receipt-before-pointer ordering and private environment binding. Integration and real adapter tests remain required. |
| Section 7: immutable replay/cycle publication | Implemented | `store_replay`, `store_cycle`, loaders, `merge_results`, `reindex` and `build` retain the validated transitive graph and optional history links. Non-live traces are excluded from `current_run`; this index is not Active. |
| Section 7: adoption validation/projection/publication | Not implemented in Python | `store_adoption` and `load_adoptions` are declarations only. `_require_supported_publication` rejects adoption with `adoption_publication_pending`; `validate` does not yet validate it. Implement validation and safe projection before removing this guard. |
| Section 8: CLI confirmation eligibility | Partially implemented | `iterate` accepts a confirmation path, but the real provider blocks it and `run_iterations` always returns `approval_eligible: false`. An improved development result alone must never change this. |
| Section 8: Actions work/Skill/N selection | Not implemented | The workflow exposes legacy project/live/budget selection, not recorded work, exact Skill or N. Add a private input channel and bounded dispatch adapter; no `approve` invocation or local approval credential in Actions. |
| Sections 7-9: public trace | Replay/cycle integrated; adoption consumer only | `dashboard/trace.js` has read-only approval/use validation and labels, but no official Python-produced adoption graph is published. PR #31 follow-up `1433c554a5ca7aabeda29200ed8be528a9e0d47f` adds browser tests only and is not in this main. |
| Section 9: no fake production path / integrated acceptance | Development acceptance only | Common integration tests call actual provider/loop/validators/storage with external boundaries simulated. Synthetic confirmation fixtures and owner module tests are not a real provider confirmation or operational approval. The complete CLI chain is not integrated. |

Scoped baseline command:
`PYTHONPATH=tests:. python3 -m unittest test_hackathon_contracts test_hackathon_integration -q`
ran 57 tests successfully with no skips. This is not a new full-suite, container,
live-model, approval, deployment or exact-final-HEAD CI result.

Implementation order is reviewed confirmation admission/isolation contract;
owner provider and loop changes; approval/next-use CLI and private WorkItem
storage; adoption public graph; private bounded Actions input; then one exact
integrated codebase and official build. Independent approval-store, public
consumer and fail-closed loop work may proceed against unchanged accepted APIs.
Do not block all sessions on the model-runtime implementation.

Session 2's issue #36 identifies an ownership decision needed before the
confirmation guard can be lifted: `copilot_runtime.py` launches a host process
with tool/profile restrictions, not enforced filesystem confinement. It is not
in sessions 1-5's assigned modification lists. Select and authorize its owner
and a verifiable process boundary separately; a new `isolated=True` flag, empty
test-context array, role cwd, mode-0700 directory or passing prompt-sentinel test
does not resolve this gap. The proposed registration/disclosure/selection APIs
in that issue are not accepted interfaces yet. Existing provider and loop
signatures remain authoritative until the reviewed follow-up is committed.

No old stacked PR is implicitly reimported. PR #29's owned-file changes may be
reused after exact-delta inspection. PR #31's test-only follow-up is a separate
input; PR #32 design and moving source files into a `skillops/` package are
excluded. Paid evaluation, real `approve`/`run-approved`, main merge, public
deployment and protection-rule changes require separate authorization.

### Historical initial baseline

The initially inspected implementation provided:

| Surface | Existing behavior to preserve |
| --- | --- |
| `skill_pipeline.evaluate_skill(...)` | One generated candidate, guide quality, an automatically derived failed-check repair, isolated original/base/candidate comparison; returns `(assessment, captures)` |
| `project_checks.discover/execute/compare` | Frozen check plans, container execution, protected-file checks and explicit regression/unverified results |
| `evolution_records.capture_version(...)` | Content-addressed entrypoint or complete-bundle capture; complete versions include references, scripts and assets |
| `CopilotRuntime.invoke(..., expected_version=...)` | Staged-version and Skill-activation verification |
| `project_evaluation.BudgetRuntime` | Shared call count and monotonic deadline; optional per-session Credit soft cap |
| `project_results` | Validated, immutable reports and optional attachments; allowlisted static publication |
| `repositories` | Initial legacy entrypoint pins and read-only comparison eligibility, not full-bundle adoption |

The legacy assessment `work` object has only `sha256`, `provenance: generated`, and
`check_id`. It is not a recorded user development task. The existing assessment
decision specifically requires repairing an originally failing check. Neither
that provenance nor historical decisions may be relabeled for the new replay path.

Phase 1 adds a separately selected replay path, a bounded loop and local approval.
It does not add a queue, service, database, generic approval framework, automatic
source rewrite, canary rollout, rollback, or GitHub-based operational approval.
No changes to `candidates.py`, evaluation rubrics or protected project tests are
delegated. Session 1 additionally owns `copilot_runtime.py` and its runtime tests
for the explicitly authorized section 10 isolation implementation; other sessions
request changes to that boundary rather than introducing another runtime.

## 2. Shared identities, encoding and errors

All new persisted objects use `schema_version: 1`; this does not change the
versions of existing formats. Field lists below are exact unless explicitly
marked as a public projection. Required nullable fields must be present.

| Type | Contract |
| --- | --- |
| Project | Existing `project_results.ID`; never a filesystem path |
| Skill | Existing `evolution_records.SKILL_KEY` plus project-relative bundle `source_path` |
| Source commit | Full lowercase 40-character Git commit; verify the checkout and project tree |
| Version | Existing `sha256:<64 lowercase hex>` complete-bundle ID, never the entrypoint hash alone |
| Run | Existing `project_results.RUN`; generate unique local IDs with its `local-` form |
| Cycle | A run ID, identifying one project and one Skill optimization attempt |
| Round | Positive integer `round_number`; `round_id = cycle_id + "-r" + str(round_number)` |
| Event/task/environment ID | Existing `project_results.ID`; immutable meaning within its local environment |
| Timestamp | Non-null UTC ISO 8601, generated by the host, not model output |
| Digest | Lowercase SHA-256 hex without the `sha256:` prefix |

`H(object)` means SHA-256 of `project_results.encoded(object)`, including its
trailing newline. File evidence uses the hash of actual stored bytes; new JSON
files must use that encoding. Do not use `H` to replace existing bundle, check-plan,
protected-file, project-tree or historical hash algorithms.

An artifact reference is exactly `{project_id, run_id, path, sha256}`. `path` is
relative to `results/<project_id>/<run_id>/` and must be an allowlisted filename
from section 7. Validators check identity, bytes and semantic cross-references,
not merely digest syntax. No arbitrary URL or path traversal is accepted.

Reject unknown fields, bool-as-integer limits, non-finite numbers, duplicate IDs,
unsafe paths, symlinks, hash mismatches and orphan references. Reuse strict JSON,
path guards, size bounds and `RuntimeFailure(code, message)`. Input/authorization
failures exit CLI status 2 without an approval or success-shaped result.
Recoverable stage failures retain completed evidence and an explicit failure;
missing observations stay null/unverified. No unrestricted exception swallowing.

## 3. Recorded development work

`WorkItem` is an operator-supplied private JSON object with these exact fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `task_id`, `project_id` | Version 1 and stable identities |
| `source_commit`, `project_tree_sha256` | Exact source snapshot, including protected inputs |
| `request` | Nonempty UTF-8 development request, at most 16 KiB |
| `split` | `development` or `confirmation`, selected before generation |
| `sources` | Nonempty map of project-relative editable existing source paths to original byte SHA-256 |
| `checks` | Object described below, fixed before candidate generation |
| `input_sha256` | `H(WorkItem without input_sha256)` |

`checks` has exactly `plan_sha256`, `protected_sha256`, `required_case_ids`,
`required_gate_ids`. ID arrays are sorted, unique, and bind existing discovered
checks; at least one required case is mandatory. Required gates include all
discovered gates. All required outcomes must actually be observed as `passed`;
missing, skipped, expected-failure or errored cases are not task completion.
The complete observed population still goes through `project_checks.compare`.
An arbitrary command in the request is never executable check configuration.

Source contents are read from the pinned project, not trusted from model output.
Use existing replay limits: at most 16 editable source files, at most 64 KiB of
UTF-8 source total, and at most 32 KiB of exposed protected test context. Enforce
the existing file/project limits too. Editable paths cannot intersect tests,
fixtures, Skill bundles, evaluator code, configuration or dependency manifests.
Phase 1 supports replacement of listed existing source files, not arbitrary file
creation/deletion. Unsupported tasks fail explicitly instead of being silently
converted into the legacy generated repair task.

Every original/base/candidate application starts from the same pristine project
snapshot and `input_sha256`. Outputs from earlier applications or rounds never
become the next project source. The actual `request` must reach the developer
prompt as data, alongside the permitted sources and appropriate test context.
Hashing the request without using it is not replay.

The public `work` projection is exactly
`{task_id, input_sha256, split, provenance, checks}`, with `provenance: recorded`
and the same pinned check hashes/required IDs as the private WorkItem.
Raw requests, source contents, test contents and private paths are not published.
The full private WorkItem must remain available for local approval validation.

## 4. One candidate evaluation and fixed reference

Use ordinary dictionaries and existing captured-version tuples, not a new class
hierarchy. `Capture` means `(validated_version_manifest, {relative_path: bytes})`.
Only `capture_scope: complete_bundle` is accepted on the replay/adoption path.

`prepare_replay` produces a private context containing the validated WorkItem,
source snapshot, original Capture, discovered plan, prepared image IDs and the
following serializable `reference`:

```text
schema_version project_id skill_key source_path source_commit
project_tree_sha256 input_sha256 original_version_id rubric_sha256
quality_context_sha256 evaluator_sha256 policy_sha256 plan_sha256
environment_sha256 protected_sha256 original_checks base_quality
reference_sha256
```

`reference_sha256 = H(reference without reference_sha256)`.
`original_checks` and `base_quality` reuse current observation/quality shapes.
`policy_sha256` binds the unchanged `candidates.POLICY` plus the replay decision
rule identifier `replay-v1`, specifically `H({"policy": POLICY, "rule": "replay-v1"})`.
Rubric, model/CLI quality context, evaluator, plan,
prepared image identities, protected files and original version remain fixed.
Recheck them before and after every application. A changed input ends the cycle.

`evaluate_candidate` evaluates a supplied Capture without generating another
candidate. It runs fresh base and candidate applications on the same WorkItem,
assesses candidate quality using the fixed quality context, and returns an
evaluation object plus captures. It may reuse the frozen original checks and
base guide assessment, but not an old base application/cost measurement. The base
is always `reference.original_version_id`, never the previous round's candidate.

The new evaluation row has exactly:

```text
skill_key source_path base_version_id candidate_version_id work
reference_sha256 quality applications checks decision errors
```

`quality`, `applications` and `checks` reuse current nested shapes:
`quality: {base, candidate}`, `applications: {base, candidate}`,
`checks: {original, base, candidate}`. Application `work_sha256` equals the
WorkItem input hash. Its existing `version_id`, `staged_version_id`, `activated`,
output hash and measurement fields retain their meanings.
`errors` uses the current bounded `{stage, code}` entries.

`decision` is exactly `{policy_id, status, reasons, regression}`, where
`policy_id: replay-v1` and status is `improved`, `not_improved`, `rejected` or
`unverified`. Reuse current guide-quality non-regression, coverage, activation,
actual execution-effect and efficiency gates, including the current 5% maximum
paired cost/time regression. Missing cost/time cannot become zero.
The deliberate replay difference is task satisfaction: all pinned required cases
and gates must pass; a case need not have failed before the development request.
Do not weaken the legacy failed-check rule or change its policy version.

Observed guide regression/project regression or an observed failed required task
criterion rejects. Incomplete evidence or missing criteria is unverified.
With all gates satisfied, a guide
dimension/finding improvement is `improved`, otherwise `not_improved`. Check
completion alone does not establish instruction-quality improvement.

An immutable `replay-evaluation.json` wrapper is exactly:

```text
schema_version project_id run_id report_sha256 execution_mode reference
generation evaluation
```

`execution_mode` is `live`, `offline_test` or `sample`.
For a generated candidate, `generation` has exactly
`{parent_version_id, feedback_sha256, addressed_findings, hypothesis}`.
The IDs addressed must exist in the validated generation feedback packet; a
hypothesis is an unverified explanation. Confirmation uses `generation: null`
and evaluates the selected bytes without invoking the generator.

The wrapper binds that run's ordinary `report.json` and `skill-evolution.json`;
the latter supplies validated base/candidate captures through existing bindings.
New replay runs use this attachment rather than misrepresenting their task in
`skill-assessments.json`. Legacy runs keep their current attachments and decisions.

## 5. Bounded feedback cycles and final confirmation

One cycle covers exactly one project, one Skill and one development WorkItem.
`N` counts candidate generation attempts, including a no-change or failed attempt;
base preparation and final confirmation are not extra rounds. Default `N=1`;
explicit `N=2` enables two attempts, not two candidates per round.
Phase 1 accepts integer `1 <= N <= 10`, further limited by the approved budget.

Round 1 uses the original Capture as parent and its measured guide/check findings.
Later rounds use the preceding generated Capture as parent, and only the preceding
development round's measured feedback. Comparisons still use the fixed original.
Rejected or not-improved candidates may supply development feedback; they are
not Active and cannot be approved by the loop.

The private feedback packet has exactly
`{schema_version, input_sha256, source_round_id, quality, checks, application, decision}`.
For initial feedback, `source_round_id` and `decision` are null,
`quality` is base quality, `checks` is filtered original checks and `application` is null.
Later packets contain the previous candidate quality/checks/application and
decision, with `source_round_id` equal to that previous round ID. `application`
is the candidate receipt, not the base/candidate map. Filter check cases to the
development-visible scope. The source decision is included unchanged only when
it is reproducible from that scope alone; otherwise block rather than remove
reasons/regressions or expose excluded diagnostics. Hash the packet with `H`;
validate it against that deterministic projection of retained evaluation bytes.
Expose only bounded,
redacted development evidence to the generator.

Each round record has exactly:

```text
round_id round_number run_id parent_version_id candidate_version_id
input_sha256 reference_sha256 feedback_source_round_id feedback_sha256
evaluation_ref decision stop_reason
```

An attempt is added only when candidate generation is actually admitted. Do not
invent a placeholder round for a budget/input/feedback failure before admission.
If an admitted attempt terminates before evaluation persistence, its `run_id`,
`evaluation_ref` and `decision` are all null. Its `candidate_version_id` may be
present only if candidate generation completed. Preserve the actual stop reason;
an unsaved attempt is necessarily the last round and cannot mean `improved` or
`max_rounds`. A saved round has a valid run ID equal to its evaluation reference
and a validated evaluation decision; a null run ID cannot reference stored evidence.
`round_id` remains `<cycle_id>-r<number>` even when `run_id` is null.
The public validator checks this shape, not private proof of attempt admission;
the loop must retain the actual attempt and must not synthesize missing rounds.
`stop_reason` is null while continuing, otherwise one of:
`improved`, `max_rounds`, `no_change`, `call_limit`, `time_limit`,
`credit_limit`, `input_changed`, `evaluation_unverified`, `runtime_error`,
`cancelled`. `credit_limit` requires an actual runtime limit outcome, not a
conversion from nano-AIU. A completed `improved` round stops immediately;
otherwise `no_change`, invalid inputs, missing evidence, runtime/budget errors or
cancellation stop. A completed `rejected`/`not_improved` round can continue until N.
At a boundary, an actual failure takes priority over `max_rounds`; an improvement
on round N has stop reason `improved`, not `max_rounds`.

`cycle.json` has exactly:

```text
schema_version project_id run_id report_sha256 execution_mode cycle_id
skill_key source_path input_sha256 reference_sha256 original_version_id
max_rounds budget rounds stop_reason selected_candidate_version_id
confirmation_ref confirmation_status
```

`run_id == cycle_id`. `budget` is exactly
`{max_invocations, max_seconds, max_ai_credits_per_session}`.
Invocation/time caps obey the existing 1-1000 / 1-7200-second bounds. The Credit
field is a valid per-session soft cap or null when none was authorized; null is
not an unlimited-spend authorization.
It records authorized caps, not a fresh allowance per round. All preparation,
generation, quality judgments, base/candidate applications and confirmation share
one `BudgetRuntime` call counter/deadline. Reuse telemetry; never sum per-session
Credit soft caps and claim an enforced aggregate monetary ceiling.

`selected_candidate_version_id` is the first fully verified improved candidate,
or null. `confirmation_status` is `not_run`, `passed`, `failed` or `unverified`.
With no confirmation artifact, `confirmation_ref` is null: `not_run` means no
attempt, while `unverified` means preparation was attempted but blocked before
an evaluation could be emitted (including the phase-1 isolation guard).
Neither state permits approval; passed/failed require a validated artifact.
The selected bytes are frozen before confirmation. Confirmation evaluates them
once against a different, precommitted `split: confirmation` WorkItem and the
same original Skill. Its reference has that task's input hash and original check
observations, while keeping source, evaluator, rubric, policy and runtime fixed.
A complete `improved` or `not_improved` confirmation with all replay gates
satisfied is `passed`; rejection is `failed`; incomplete evidence is `unverified`.

Confirmation tasks must have different task/input identities and disjoint required
case IDs from development tasks, registered before generation. Identity difference
alone is insufficient: the operator must review that these are distinct tasks.
Confirmation requests, test context, scores and failure details are withheld from
the generator and development feedback. If the current context builder cannot
exclude confirmation-only tests/fixtures or their contents, fail with
`confirmation_isolation_unverified` rather than claim a held-out check. Such files
remain protected and may run in the isolated checker without entering prompts.

No further candidate may be generated from this confirmation result. A failed
confirmation is not sent back as round N+1; another experiment needs new,
unexposed confirmation work. Missing confirmation permits a development-only
demonstration but blocks approval. Do not borrow legacy `heldout` benchmark data
or historical dashboard outcomes as new confirmation evidence.

Persist each completed round under its own ordinary run ID. A cycle report is
written once after its terminal state and references those runs. Do not rewrite
completed rounds to append feedback. Interrupted processes may leave valid round
records without a completed cycle; they are visibly incomplete and not approvable.
If `persist_round` itself fails (including IO, validation, immutable conflict or
an invalid returned reference), propagate the exception to fail the CLI. Do not
convert that failure into a null-run terminal round, return a terminal cycle, or
write a success receipt. Preserve already stored rounds. Individual writes may
leave partial artifacts, but there is no fabricated completed cycle or retry,
automatic repair, overwrite or rollback of earlier evidence. The same fail-closed
rule applies if binding, writing or reloading the final cycle fails.
Automatic resume is out of scope.

## 6. Human approval and proof of next use

**Decision confirmed by the operator:** use only explicit local CLI approval.
The human executes the approval command separately. Candidate generators,
evaluation agents and automatic pipelines must never execute it. An agent may
display the exact command for review, but user authorization to implement this
feature is not authorization to approve a candidate.

The proposed command surface below is not implemented by this document:

```text
python3 skillops.py approve --project <project> --skill-key <key> \
  --candidate-version sha256:<complete-bundle-hash> \
  --evidence-sha256 <cycle-file-hash> --cycle <cycle-id> \
  --expected-active-version <version-or-none> \
  --expected-active-execution-sha256 <receipt-hash-or-none> \
  --results <results-directory>
```

No `--latest`, auto-approve flag, approval based only on a score, or browser write
endpoint. Reject CI/Actions invocation and require an interactive confirmation
showing project, Skill, full candidate hash, evidence hash and both previous
Active version and execution receipt hash.
This is a guardrail within a trusted local operator environment, not proof against
malicious software with the same OS account or proof of a GitHub identity.

Before accepting approval, revalidate the cycle, every referenced artifact, source
and full candidate bytes, successful confirmation, `live` evidence, and the exact
local target. Sample/mock/legacy missing-evidence records are not approvable.
`evidence_sha256` is the hash of the immutable `cycle.json` bytes; its transitive
artifact references bind the development and final confirmation evidence.

Private approval record fields are exactly:

```text
schema_version approval_id project_id skill_key source_path source_commit
project_tree_sha256 candidate_version_id cycle_id evidence_sha256
approved_by approved_at environment_id previous_active_version_id
previous_active_execution_sha256
```

`approved_by` is host-derived local operator identity, never a model claim or
caller-supplied GitHub login. `environment_id` identifies the owner-only local
approval store, not a portable authorization token.
`previous_active_version_id` and `previous_active_execution_sha256` are both
null only when no Active pointer exists. Otherwise both are required: a complete
version ID and the lowercase SHA-256 of the actual canonical private execution
receipt bytes referenced by Active. A half-null pair, missing/invalid receipt,
unverified receipt or mismatched target/environment is invalid state, not absence.
Never infer an Active pair from an entrypoint-only legacy pin.
Serialize approval writes using the existing local registry lock pattern and
compare the recorded Active pair with both `--expected-active-*` values under
that same lock. CLI literal `none` maps to Python `None`; never accept a missing
hash as a wildcard. Concurrent or stale approval attempts fail. The accepted pair
is stored in the approval, not reconstructed later from the version alone.
A repeat with identical binding returns the existing
receipt; changed evidence requires a new explicit approval.

Approval does **not** overwrite original Skill files, change the recorded Active,
or prove application. It makes those exact bytes selectable for a subsequent
explicit local run:

```text
python3 skillops.py run-approved --project <project> --skill-key <key> \
  --approval <approval-id> --candidate-version sha256:<complete-bundle-hash> \
  --evidence-sha256 <cycle-file-hash> --work-item <next-work-json> \
  --results <results-directory>
```

`run-approved` does not generate a candidate or re-approve one. It is a model
execution and separately requires approved call/time/Credit conditions.
The next work item may describe a new request against the same approved source
snapshot; it is execution, not reused evaluation evidence. Changed project,
Skill path/key, source snapshot, candidate bytes, evaluation bytes or environment
invalidates the selection. Require new evaluation/approval as appropriate.

Resolve only the explicitly named approval, recapture the complete bundle before
staging, and use existing runtime `expected_version` and activation checks.
Verify the staged inventory again after invocation. No fallback to workspace
Skill, old Active, newest candidate, or another approval on mismatch.

The private next-use record has exactly:

```text
schema_version execution_id run_id project_id skill_key approval_id
approval_sha256 evidence_sha256 environment_id work_input_sha256
approved_version_id loaded_version_id skill_version_verified observed_at
status reason_code previous_active_version_id
previous_active_execution_sha256
```

`status` is `verified`, `failed` or `blocked`. `loaded_version_id` is null when
not observed. `verified` requires matching full IDs, successful runtime activation
verification and the same approval/evidence/environment binding. It proves
observed Skill use, not task success; the execution report carries task outcomes.
Both previous-Active fields must equal the approval's captured pair. Revalidate
the pair when resolving approval, then compare again under the Active update lock
immediately before persisting a successful transition. Hold that lock across
comparison, receipt persistence and pointer replacement. Reject a changed version
**or changed execution receipt hash**, including same-version re-execution and
A -> B -> A, with `active_conflict`; leave the existing Active untouched.
Persist the receipt before updating the private Active pointer to the new version
and the hash of that exact receipt. The receipt's `execution_id` distinguishes
separate executions even if they use identical Skill bytes.
Any failure before a durable verified receipt leaves Active unchanged. If pointer
persistence fails, report an error; do not claim an Active transition. Active reads
validate their referenced receipt. A verified receipt alone is not a recovery rule.
Retrying the exact same already-recorded execution is idempotent only if Active
still points to that same version/receipt pair; never restore an earlier pointer
after an intervening transition. A receipt orphaned by failed pointer persistence
does not authorize automatic recovery.

Active is keyed by local environment, project and Skill, and means the last
successfully recorded explicit version selection. Approval pending first use,
observed use, task outcome and Active-pointer state are separate UI labels.
The local record confers **no GitHub Actions operational-use authority**.
Actions may publish reviewed evidence but must not import a local approval as
permission to execute or apply a Skill. Central GitHub approval is future scope.

## 7. Storage, public projection and compatibility

Use existing private roots and append-only result storage:

| Location | Contents / owner |
| --- | --- |
| `.skillops-private/work-items/<task_id>/<input_sha256>.json` | Immutable WorkItems; integration input adapter |
| `.skillops-private/assessments/<run_id>/...` | Existing isolated artifacts, feedback packets, requests and model receipts; execution owners |
| `.skillops/approvals/<approval_id>.json` | Owner-only immutable approval; session 4 |
| `.skillops/executions/<execution_id>.json` | Owner-only next-use receipt; session 4 |
| `.skillops/active.json` | Validated pointer map keyed by environment/project/Skill; session 4 |
| `.skillops/environment.json` | Owner-only stable environment ID; session 4 |
| `results/<project>/<run>/report.json` | Existing schema 1, unchanged exact field set; integration |
| `results/<project>/<run>/skill-evolution.json` | Existing capture/binding schema; integration |
| `results/<project>/<run>/replay-evaluation.json` | Optional new single evaluation attachment; integration |
| `results/<project>/<cycle>/cycle.json` | Optional bounded-cycle attachment; integration |
| `results/<project>/<run>/adoption.json` | Optional reviewed public approval/use projections; integration |
| `results/<project>/<run>/stage-metrics.json` | Existing optional telemetry where supported; no fabricated measurements |

Do not modify `registry.json` schema 1, immutable initial pins, old reports,
`skill-assessments.json`, legacy adoption observations or their bytes. New optional
attachments have independent validators. An absent attachment means unrecorded,
not no approvals, failure, zero cost, successful adoption or an N=1 replay.
Legacy one-candidate calls and CLI defaults remain on their existing path.

New sidecars are bounded to 1 MiB each; existing evolution bounds remain unchanged.
Store reports/captures/evaluation before their cycle references. Merge validates
the union of existing and incoming evidence and rejects conflicting bytes before
writing. Cycles cannot refer to missing rounds. Reindexing can rebuild indexes,
never edit immutable evidence. No in-place enrichment of historical report files.

The public `adoption.json` wrapper is exactly
`{schema_version, project_id, run_id, report_sha256, execution_mode, approvals, executions}`.
Use a new report/run to publish later observations; do not append to an old file.
Approval projections contain exactly:

```text
approval_id project_id skill_key candidate_version_id cycle_id evidence_sha256
approved_at approved_by trust_scope previous_active_version_id
```

Public `approved_by` is the literal `local_operator`, not OS username;
`trust_scope` is the literal `local_environment`. Execution projections contain:

```text
execution_id run_id project_id skill_key approval_id evidence_sha256
work_input_sha256 approved_version_id loaded_version_id
skill_version_verified observed_at status reason_code
```

Do not publish raw operator identity, environment ID, absolute paths, credentials,
requests, source/test context, feedback text, prompts, responses or CLI diagnostics.
Existing public Skill captures remain subject to explicit review/redaction before
publication; hashing private content alone does not make that content publishable.
New generation hypotheses/findings must pass the existing sensitive-text controls.
Public approval/use projections are reviewed observations, not executable authority
or cryptographically authenticated human identity.

`execution_mode` is mandatory on replay/cycle/adoption attachments. `offline_test` and
`sample` must be visibly labeled and excluded from approval and measured-quality
claims. Keep existing report `origin` unchanged in meaning: samples still use
`origin: sample`; a test-local report is not therefore a live model observation.
Sample replay references retain null `source_commit`, `project_tree_sha256` and
`evaluator_sha256`, matching the sample report; never invent measured identities.
Publish only explicitly reviewed projections; never copy private roots wholesale.

The existing project history index gains optional keys
`replay_evaluation`, `cycle`, `adoption`, each equal to
`<run_id>/<matching-allowlisted-filename>`. Reports remain reachable by their
existing run links. Session 5 adds validated deep-link selection with query keys
`project`, `run`, `skill`; unknown/missing evidence shows an error or unrecorded
state, never silently selects another successful run. Browser reads only static
results, verifies hashes, renders untrusted text safely, and carries no write
credential or approval action.

## 8. Ownership and callable handoffs

Names below are the agreed target surface, **not claims that functions exist**.
Keyword-only arguments are intentional. Use Python standard-library data and
reuse existing helpers. No fallback test implementation may be installed on a
production import path while a provider is missing.

| Session | Owned files | Provides / consumes |
| --- | --- | --- |
| 1: contract/integration | This document, `project_evaluation.py`, `project_results.py`, `skill_assessments.py`, `evolution_records.py`, `skillops.py`, `.github/workflows/project-evaluation.yml`; common tests/docs | Work/evidence validation, storage/index/build, CLI and Actions adapters |
| 2: work replay | `skill_pipeline.py`, `project_checks.py`, `tests/test_skill_pipeline.py`, `tests/test_project_checks.py` | Frozen work execution, one-candidate generation/evaluation helpers, protected context isolation |
| 3: iteration | New `skill_iterations.py`, `tests/test_skill_iterations.py` | Bounded loop and feedback lineage; calls session 2, never approval |
| 4: approval | New `skill_approvals.py`, `repositories.py`, `tests/test_skill_approvals.py`, `tests/test_repositories.py` | Local approval store, exact selection, next-use receipts and Active state |
| 5: public UI | `dashboard/`, `tests/dashboard.spec.js` and new browser tests under `tests/` | Read-only contracts, explicit sample/measurement and approval/use distinctions, exact run links |

Existing common tests are owned by session 1: `tests/test_project_evaluation.py`,
`tests/test_project_results.py`, `tests/test_skill_assessments.py`,
`tests/test_evolution_records.py`, `tests/test_skillops.py`,
`tests/test_project_workflow.py`, and new `tests/test_hackathon_contracts.py` /
`tests/test_hackathon_integration.py`. Session 5 requests publisher changes from
session 1; session 3 requests replay helper changes from session 2. File ownership
does not authorize touching someone else's worktree or staging their changes.

### Session 1: shared validation and storage, delivered first after acceptance

```python
skill_assessments.validate_work_item(data, *, project, source_commit)  # -> WorkItem
skill_assessments.validate_replay(data, *, report, lifecycle)         # -> wrapper
skill_assessments.decide_replay(evaluation, *, work_item)             # -> decision
skill_assessments.validate_development_feedback(
    packet, *, context, evaluation, parent, source_round_id,
)  # -> packet; pure semantic validation, not issuance or isolation certification
project_results.validate_cycle(data, *, report, evaluations)         # -> cycle
project_results.store_replay(results, data)                          # -> Path
project_results.load_replays(results, rows=None)                     # -> {(project, run): wrapper}
project_results.store_cycle(results, data)                           # -> Path
project_results.load_cycles(results, rows=None)                      # -> {(project, cycle): cycle}
project_results.load_replay_evidence(results, rows=None)             # -> {(project, run): {report, lifecycle, replay}}
project_results.store_adoption(results, data)                        # -> Path
project_results.load_adoptions(results, rows=None)                    # -> {(project, run): wrapper}
```

`project` in WorkItem validation is the pinned project directory. `evaluations`
is a mapping keyed by `(project_id, run_id)` of already loaded report, lifecycle
and replay wrapper objects; each value is exactly
`{report, lifecycle, replay}`. Validation checks reference hashes too. Private
WorkItems and feedback packets must be validated before projection. Public
validation recomputes what the public evidence establishes; it cannot establish
the truth of a hidden human request. Local approval checks the private inputs.

Session 1 wires existing `merge`, `validate`, `index`, `build`, evaluator
fingerprinting, publication allowlists and optional history links for all three
attachments before consumers rely on them. Missing providers remain an explicit
blocked integration point, not a placeholder returning success.

#### Common foundation implementation handoff (PR #26)

The foundation implements `validate_work_item`, `validate_replay`,
`decide_replay`, `validate_development_feedback`, `load_replays`, `load_cycles`,
`validate_cycle` and `budget_limits`. These are validators/readers/cap snapshots,
not the replay provider, local approval implementation or operational adapters.
`project_results.py validate` also checks replay/cycle sidecars.
At the foundation stage, publishers rejected new sidecars until lossless
publication was connected. The final demo integration supports validated replay
and cycle graphs; unsupported adoption evidence still fails closed with
`adoption_publication_pending`. Legacy report bytes remain unchanged.

The reusable fixture is `tests/hackathon_fixtures.py`; invariant tests are
`tests/test_hackathon_contracts.py`. Every generated fixture is `offline_test`,
including its synthetic confirmation result. No provider issuance, model
execution or semantic confirmation isolation is established by these fixtures.
`validate_work_item` checks the supplied full commit against the WorkItem and
verifies the actual project bytes/plan; the caller must independently verify
that the supplied commit identifies its checkout. It does not execute Git or tests.
`validate_development_feedback` is pure validation, never the private issuance
registry described below.

Minimal offline calls from the repository root with `PYTHONPATH=tests:.`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import project_results as results
import skill_assessments as assessments
from hackathon_fixtures import fixture, write_results

with TemporaryDirectory() as folder:
    data = fixture(Path(folder))
    assessments.validate_work_item(data["work_item"], project=data["project"], source_commit="a" * 40)
    for item in data["evaluations"].values():
        assessments.validate_replay(item["replay"], report=item["report"], lifecycle=item["lifecycle"])
    results.validate_cycle(data["cycle"], report=data["cycle_report"], evaluations=data["evaluations"])
    directory = write_results(Path(folder) / "results", data)
    assert len(results.load_replays(directory)) == 3
    assert len(results.load_cycles(directory)) == 1
```

#### Single-replay adapter implementation handoff

The opt-in `skillops.py replay` adapter uses the actual session 2 provider, not
the legacy generated-work assessment path. Its entry point is:

```python
project_evaluation.run_replay(
    root, *, project_id, skill_key, work_item, output, model, execution_mode,
    policy, runtime_factory=None,
)
project_evaluation.persist_replay(
    output, evaluation, captures, generation, reference,
    *, execution_mode, run_id=None,
)
```

`work_item` is a private WorkItem JSON path; the project is
`root/projects/<project_id>`. Select the exact discovered Skill key, reusing
validated identity history from the output directory. The command requires
both `--live` and `SKILLOPS_LIVE_EVALUATION_ENABLED=true`, authentication and
explicit invocation/time/per-session Credit limits. Gates run before runtime
construction or model/image preparation. The CLI always sets `execution_mode`
to `live`; there is no production mock/offline CLI option. Offline tests inject
only the transport and container boundaries and explicitly set `offline_test`.
The supplied budget object is reused without resetting calls, deadline or the
original authorized `max_seconds`.

The reusable session 3 callback is exactly:

```python
from functools import partial
from project_evaluation import persist_replay

persist_round = partial(persist_replay, output, execution_mode=runtime.execution_mode)
ref = persist_round(evaluation, captures, generation, reference)
# ref: {project_id, run_id, path: "replay-evaluation.json", sha256}
```

Every callback creates a fresh run unless an explicit valid `run_id` is supplied.
It validates the complete report/capture/replay package before writing, uses
the existing immutable writers, and returns only after the sidecar is written.
`project_results.store_replay(results, data)` is implemented and validates its
stored report and complete lifecycle before append-only sidecar storage.
IO/validation/conflict errors propagate, never a success-shaped reference.
Writes are individually atomic, not a multi-file transaction: an IO interruption
may leave report/capture files without a replay sidecar. The aggregate report
remains `execution: blocked / assessment_unverified`; do not treat partial
storage as a completed replay or publish it as one.

The callback does not mutate or replace `evaluation`. Pass the provider's
original row object to `development_feedback` after persistence; neither a
deserialized row nor a deep copy is valid private feedback evidence.
No legacy `skill-assessments.json`, cycle, approval, Active or next-use state
is fabricated. The CLI reports `confirmation_status: not_run`,
`confirmation_reason: confirmation_isolation_unverified` and
`approval_eligible: false`, even when the development decision is `improved`.
At the PR #33 stage, iteration and cycle storage were separate unfinished
integrations. The following handoff adds those two pieces only; adoption writers,
human-approval/next-use CLI and live iteration Actions remain unfinished.
The final demo publisher connects replay/cycle evidence, not adoption.

`tests/test_hackathon_integration.py` exercises real provider/guide evaluator,
common validation, staged-bundle verification and storage with explicitly
simulated model/container boundaries. Its two development evaluations prove
feedback lineage and shared budget wiring, not completion of session 3's loop,
semantic confirmation isolation, model improvement or actual Skill activation.

#### Development iteration implementation handoff

The integration branch retains session 3's `run_cycle` from `9f100bb` and the
session 2 provider follow-up `4676c6c`, on PR #33's `b93249e` base. It introduces
no replacement loop or evaluation/persistence callback.

```python
project_evaluation.run_iterations(
    root, *, project_id, skill_key, work_item, output, model, execution_mode,
    policy, max_rounds=1, confirmation_work_item=None, runtime_factory=None,
    cycle_id=None,
)
project_evaluation.persist_cycle(output, cycle, reference)  # -> cycle artifact reference
```

`run_iterations` and `run_replay` share only the preparation context: target and
WorkItem validation, live gates, one runtime/budget, private artifact allocation,
the lock and prepared-image lifetime. The former delegates attempts to the
delivered `run_cycle` with `partial(persist_replay, output,
execution_mode=execution_mode)`; the latter retains its single replay behavior.
The iteration module is included in the evaluator fingerprint for new evidence;
stored historical hashes and decisions are not rewritten.

Optional confirmation input is read and validated before any model invocation:
same project/source commitment, a distinct task/input, disjoint required cases
and `split: confirmation`. Its retained value is passed only to a lazy preparation
callback after selection, with the exact same runtime/images/deadline. It never
enters development context or prompts. This does not implement semantic isolation:
the actual provider still raises `confirmation_isolation_unverified`.

`persist_cycle` accepts only the terminal payload returned by the loop, binds an
aggregate report, validates all stored report/capture/replay references, then
uses `store_cycle`. It rereads the result through `load_cycles` and compares
canonical bytes before returning `{project_id, run_id, path: "cycle.json", sha256}`.
`load_replay_evidence` supplies the transitive mapping consumed by this same
validator and writer. The aggregate report remains blocked/unverified, never
an approval or a passed final confirmation. Storage errors are not caught as
normal cycle termination. No resume, repair, overwrite or additional cycle is
attempted on failure.

The opt-in CLI is:

```text
python3 skillops.py iterate --project <project> --skill-key <key> \
  --work-item <development-json> --confirmation-work-item <confirmation-json> \
  --max-rounds 2 --results <results-directory>
```

Confirmation input is optional for a development-only run. `--max-rounds` is
1-10, default 1. As with `replay`, execution requires both `--live` and explicit
enabled/authenticated/bounded environment settings. There is no offline/mock
production CLI flag. Exit 0 means a recorded normal development stop
(`improved`, `max_rounds`, `no_change`), not improvement proof or approval.
Budget/runtime/unverified termination returns 2 but may include a valid terminal
cycle reference; callback/storage failure returns 2 without a success reference.
The CLI always reports `approval_eligible: false`.

`tests/test_hackathon_integration.py` exercises the actual loop, provider,
validators, writer, loader and CLI dispatch. Only external transport/IO boundaries
are simulated; the CLI test boundary explicitly labels simulated execution
`offline_test`. Both pre-storage termination and callback failure are checked
through the same implementation. `tests/export_iteration_evidence.py` generates
fresh records directly into a new output directory through those production
modules, not by copying a saved result fixture. Its manifest records the clean
integration SHA, scenario paths, call counts, cycle/run IDs and canonical hashes.
Input project and model/container observations are synthetic; these results are
offline wiring evidence, never paid model-improvement or real activation evidence.

### Session 2: single-candidate primitives

```python
skill_pipeline.prepare_replay(
    runtime, model, project, bundle, skill_key, rubric, images, artifact,
    *, work_item, deadline,
)  # -> private context containing reference and original Capture
skill_pipeline.generate_candidate(
    runtime, model, parent, feedback, artifact, *, deadline,
)  # -> (generation, candidate Capture); no evaluation or approval
skill_pipeline.evaluate_candidate(
    runtime, model, context, candidate, artifact,
    *, deadline, progress=None,
)  # -> (evaluation row, captures); no generation or persistence to public results
```

The private context has exactly these keys (no closures or unspecified extras):

| Key | Python type / invariant |
| --- | --- |
| `reference` | `dict`, exact reference in section 4 |
| `work_item` | `dict`, validated full private WorkItem |
| `original` | `tuple[dict, dict[str, bytes]]`, complete-bundle Capture |
| `execution_mode` | `str`: `live`, `offline_test`, `sample`; never inferred from a passing result |
| `source_project` | Absolute `pathlib.Path` to the original pinned project; recheck for drift |
| `project` | Absolute `pathlib.Path` to a private pristine copy; no generated output is copied back |
| `plan` | `dict`, the existing `project_checks.discover` result |
| `images` | `dict[str, str]`, prepared language -> immutable image ID |
| `rubric` | `dict`, independent copy of the pinned guide rubric |
| `sources` | `dict[str, str]`, original UTF-8 editable contents, matching WorkItem byte hashes |
| `feedback_scope` | `dict`, exactly `{input_sha256, case_ids, gate_ids, test_context_paths}` |
| `feedback` | `dict`, issued initial development packet; `None` for future supported confirmation contexts |

`reference` supplies project/Skill/source-path and all comparison hashes; do not
duplicate these as alternative top-level identities. Artifact directories,
runtime, model and shared deadline are explicit function arguments, not context
state. Session 1 explicitly sets `runtime.execution_mode` before preparation;
session 2 requires a literal string from the three allowed values and copies it.
There is no default, credentials-based inference or truthy Mock acceptance.
Every subsequent primitive checks it still matches the prepared/retained mode.
Production CLI sets `live` only after the live gate; tests set `offline_test`.
The three existing primitive signatures remain unchanged.

Session 2 also owns the **only** development-feedback projection API:

```python
skill_pipeline.development_feedback(
    runtime, context, evaluation, *, source_round_id,
)  # -> issued section 5 packet; caller hashes with H(packet)
```

`prepare_replay` internally calls this boundary with `evaluation=None` and
`source_round_id=None`, stores the returned packet in `context["feedback"]`,
and privately retains its binding. Session 3 uses that packet for round 1.
For later rounds, session 3 passes the actual retained development evaluation
and the ID of the round that produced it. It must not hand-build the packet.
Session 2 owns projection, private issuance and generation-time provenance checks;
session 1 owns the pure validator above. Missing validation blocks, never falls
back to a local always-valid substitute.

Phase 1 accepts the WorkItem's `required_case_ids` and `required_gate_ids` as
the **only** operator-declared development-visible IDs. `feedback_scope` is
derived from those exact lists and the WorkItem's input hash; lists are sorted,
unique and must occur in the retained original checker observations. An extra
ID, changed input hash or scope widened to all discovered checks is invalid.
`test_context_paths` is always `[]`: no protected test/fixture body, including
mixed modules and imported fixtures, enters either developer or generator prompts.
There is no `runtime.development_scope` attribute or implicit test-path discovery.
An empty test-context list is not proof that the task is semantically held out.

The packet still has the exact section 5 fields; no public split/parent fields
are added. Packet `checks` is exactly `{cases, gates}` with only allowlisted
`{id, status}` observations. Full-suite status, timings and diagnostic text are
excluded. The application receipt is the actual development task receipt, not
an aggregate checker receipt. Quality contains guide-only evidence.
For later feedback, the decision must be the unchanged validated source decision
and must also be reproducible from development-visible observations alone.
If excluded cases/diagnostics affect it, block with
`confirmation_isolation_unverified`; do not remove a rejection/unverified reason
to manufacture passing feedback. Full observations remain in evaluation/reference
and continue to govern regression/approval.

Required validation and issuance sequence:

1. Validate private WorkItem identity/source/check hashes, both pristine project
   snapshots, reference digest, original complete Capture, runtime mode and exact
   feedback scope. Reject any confirmation context before model invocation.
2. For initial feedback, bind to the original Capture and reference's actually
   retained preparation observations. For later feedback, require byte-identical
   provider-retained `evaluate_candidate` output for the same input/reference,
   project/Skill/source path and mode. Arbitrary self-hashed evaluation dictionaries
   are not issuable evidence. Recompute the source decision from full observations.
3. Bind later `parent` to that evaluation's exact candidate complete Capture,
   not its base version or an entrypoint hash. Verify retained capture bytes,
   inventory and version digest. Bind `source_round_id` to the existing cycle/
   round naming rule; a retained evaluation cannot be reissued under a different
   source-round ID. Session 3 supplies that ID immediately after its evaluation.
4. Session 2 constructs the permitted projection; the session 1 validator checks
   exact fields, projection content, source row decision, full WorkItem/reference,
   initial/later null rules and parent matching. Success returns the packet only:
   it is not `confirmation_isolated: true`, approval, or evidence of a live run.
5. Session 2 retains an immutable private issuance binding: packet bytes/hash,
   input hash, reference hash, full parent version, source-round ID, source
   evaluation hash (null initially), and execution mode. Reuse the current
   runtime-private artifacts; no registry service or generic token framework.
6. Before invocation, `generate_candidate` requires the supplied packet **and**
   parent Capture to match that issued binding and its retained source evidence
   in the same runtime environment. A valid packet schema/hash alone, a retained
   packet paired with another parent, or an unretained forged packet fails.
   Confirmation evidence is never registered for generation.

These checks establish evidence provenance and an explicit disclosure boundary,
not a proof that no semantic information about confirmation was inferable from
ordinary project sources. Session 2 therefore keeps `split=confirmation`
preparation blocked with `confirmation_isolation_unverified` in phase 1.
The caller records confirmation unverified, never passed; approval remains
blocked. Lifting this guard needs a separately reviewed isolation mechanism,
not merely tests that hash the same packet. The final confirmation contract in
section 5 remains the integration acceptance target, not implemented capability.

Provider tests must reject forged/unretained packets and evaluations, wrong parent
bundles, changed mode/reference/input, conflicting source-round issuance, extra
visible IDs and nonempty test-context paths before `invoke`. Use a distinctive
hidden-case/fixture sentinel and assert absence in **both** prompt paths.
Changing excluded outcomes/diagnostics/timings must either leave permitted packet
bytes identical or explicitly block projection, never leak or erase a failure.
Provider-retained full observations must remain unchanged. Fixture-only success
does not establish confirmation isolation or live evaluation.

`parent` is a Capture. `feedback` is the packet in section 5. `generation` is the
section 4 object. `context` includes the frozen reference and validated WorkItem.
Session 1 assembles existing lifecycle capture/binding shapes independently of
`attachments()`: that helper validates legacy generated-work rows and must not
be called with replay rows. Session 2 preserves the existing
`evaluate_skill` signature/return/default behavior and may extract its current
generation/application logic rather than duplicate it. Generation consumes only
development feedback; checks continue using the existing isolated runner.

### Session 3: loop, with one narrow persistence callback

```python
skill_iterations.run_cycle(
    runtime, model, context, artifact,
    *, cycle_id, max_rounds, budget, confirmation_context, persist_round,
)  # -> terminal cycle payload, before report/report_sha256 binding
```

`runtime` is the caller's shared `BudgetRuntime`; `budget` is the same underlying
live budget, not a copy. The live dictionary contains `calls: int`,
`max_calls: int`, `max_seconds: int`, `deadline: float`, and optional
`max_ai_credits: float`. `policy_from_environment` stores `max_seconds` at
authorization time, alongside `deadline = monotonic() + max_seconds`.
Only `calls` changes; never reconstruct authorized duration from remaining time.
`project_evaluation.budget_limits(budget)` returns exactly the public budget
object from section 5 using the stored caps and rejects missing `max_seconds`
with `missing_limits`. Legacy direct `BudgetRuntime` use remains compatible;
only the new cycle/persistence path requires the added cap.

`confirmation_context` is a callable prepared by session
1/2, invoked once only after candidate selection; it returns a replay context for
the precommitted confirmation task without exposing it to generation.
Revision 1.4 changes this private callback from `confirmation_context()` to
`confirmation_context(selected)`, with an independent copy of the frozen selected
Capture. Session 3 changes the sole loop caller; session 1 changes the adapter
closure; session 2 supplies `prepare_confirmation` as specified in section 10.
The enclosing `run_cycle` signature and persisted cycle format do not change.
`persist_round(evaluation, captures, generation, reference)` is supplied by the
integration adapter; it writes one run's report/captures/replay evidence and
returns its artifact reference. Confirmation calls it with `generation=None`.
The module is the sole loop owner and must not call approval or Active APIs.

### Session 4: local approval and selection

```python
skill_approvals.approve(
    root, *, project_id, skill_key, candidate_version_id,
    cycle_id, evidence_sha256, expected_active_version_id,
    expected_active_execution_sha256, results,
)  # -> private approval record; human CLI boundary only
skill_approvals.resolve_approved(
    root, *, project_id, skill_key, approval_id,
    candidate_version_id, evidence_sha256, results,
)  # -> (approval record, verified Capture); never marks use
skill_approvals.record_execution(root, *, approval, receipt)  # -> private use record
repositories.active_version(root, *, project_id, skill_key)  # -> version ID or None
repositories.active_snapshot(root, *, project_id, skill_key)
# -> {"version_id": str | None, "execution_sha256": str | None}
```

Session 1 owns CLI parsing/interactive confirmation and the execution adapter;
session 4 validates authorization bindings and owns all local state writes.
`receipt` is the next-use record in section 6, derived from actual runtime results;
the module verifies the immutable approval and current local environment before
persisting it. Public records alone cannot be passed off as local approvals.
The adapter requests and records a fresh `run_id`, never an old evaluation run.
`active_snapshot` validates and reads one atomic pair in the local environment
under the existing lock. Its empty result has both values null. `active_version`
remains a read-only convenience, not a concurrency token: approval/update callers
must use a single snapshot, never two separately timed reads. Session 4 owns the
snapshot API, pair validation and locked compare-and-swap; session 1 owns CLI
flags and display/confirmation of both values. `record_execution` keeps its
signature and receives the extra field in `receipt`; `resolve_approved` keeps
its signature and checks the approval's pair before returning a Capture.
This adds no fields to public `adoption.json` projections. The private approval
hash naturally changes when its previous execution binding changes; do not
silently fill missing fields in older private records. Reject incomplete records
and request a fresh explicit approval.
CI/noninteractive rejection and human confirmation are **session 1 CLI** duties;
complete-bundle selection, activation and post-invocation inventory checks are
**session 1 execution-adapter** duties, using session 4's binding validation.
Neither a declared interface nor a passing provider unit test proves these
adapters are wired. Missing adapters must remain explicit integration blockers.

### Session 5: read-only consumer

Use optional index keys and the exact public formats above. Keep legacy views
working when keys are absent. New JavaScript assets must be coordinated with
session 1's existing explicit build list. No server/API dependency is introduced.
Browser tests must distinguish approval pending use, matching verified use,
mismatch/failure, missing attachments, sample data and unchanged legacy records.

The development iteration CLI is opt-in (see the implemented handoff above):

```text
python3 skillops.py iterate --project <project> --skill-key <key> \
  --work-item <development-work-json> --confirmation-work-item <confirmation-json> \
  --max-rounds 2 --results <results-directory>
```

Without the replay command/options, existing single-cycle CLI and project
evaluation behavior stays unchanged. Actions integration adds only explicit
work/Skill/round selection and append-only publication; default model access
remains disabled, and local approval is never an Actions execution credential.
The local command and read-only replay/cycle publication are implemented.
Live iteration Actions and operational approval remain separate. Shared CLI
changes require synchronized English/Korean README updates.

### Final demo publication boundary

The official builder registers modules in dependency order:
`views.js`, `evolution.js`, `assessments.js`, `trace.js`, `app.js`. All five
filenames hash their final rewritten bytes; importers point to those exact
hashed modules. Sources are not rewritten and there is no alternate demo builder.

`merge`, `index` and `build` validate complete replay/cycle graphs with the same
loaders used by CLI storage. Incoming result packets must contain their own
transitive reports, captures and replay references. Merge preflights immutable
conflicts before writing; build validates before creating its output directory.
Only allowlisted public files are copied. Optional history keys are precisely
`replay_evaluation: <run>/replay-evaluation.json` and
`cycle: <run>/cycle.json`; referenced report and capture files are retained.
Non-live replay/cycle runs are never selected as `current_run`, even when their
input/evaluator hashes happen to match. This pointer is not an Active claim.

Adoption publication remains unsupported and explicitly blocked; no operational
approval or next-use API is enabled by the UI integration. The browser can show
historical absence and unverified state but cannot approve or deploy.

For the presentation, combine the reviewed N=2 `offline_test` package produced
by the integration exporter with unchanged pre-run public reports, using the
official commands:

```text
python3 project_results.py merge --incoming <reviewed-live-results> --results <demo-results>
python3 project_results.py merge --incoming <offline-package>/n2-feedback --results <demo-results>
python3 project_results.py build --results <demo-results> --output <new-site>
python3 -m http.server <port> --bind 127.0.0.1 --directory <new-site>
```

Start links use `/?project=<project>&run=<exact-cycle-or-report-id>&skill=<key>`.
Check the official HTTP-served build, refresh/deep links, round navigation,
read-only requests, offline labels and unchanged historical outcomes. Retain
screenshots plus a file-openable gallery as backup; none is evidence of new live
evaluation. Opening a final integration PR targeting main registers the existing
CI jobs; both validation workflows check the exact PR head. No CI bypass,
main merge or public deployment is part of this preparation.

## 9. Delivery order and acceptance evidence

1. Review and accept this contract PR. No runtime code is included in this step.
2. Session 1 supplies shared validators/storage with failing-first contract tests.
   Sessions 2-5 implement only their files against that accepted commit.
3. Session 1 integrates delivered modules, not parallel replacement versions.
   Each provider supplies its PR, commit, signatures and actual offline evidence.
4. Run cross-module offline tests, then authorized container controls; publish
   only reviewed evidence. Unit/provider tests alone do not complete the product.
5. Request separate authorization before any live evaluation or operational run.

Minimum acceptance checks, implemented as failing tests before code changes:

| Check | Required evidence |
| --- | --- |
| Legacy preservation | Existing single-candidate tests pass; stored historical report/attachment bytes and decisions are unchanged |
| Real request replay | Distinct requests produce distinct input hashes and developer prompt inputs; exact source/check identities are enforced |
| N=2 linkage | First non-improved completed round supplies actual feedback to round 2; same WorkItem and original comparison, changed parent lineage, shared counters |
| Budget/termination | No third attempt; call/time caps cannot reset between stages/rounds; partial failure does not fabricate completion |
| Protected evaluation | Candidate/source outputs cannot alter tests, check plan, rubric, policy, Skill companions or approval state |
| Confirmation isolation | Hidden final-work material never enters generator feedback; final failure blocks approval and cannot initiate another round |
| Approval separation | Iteration never calls approval; noninteractive/CI approval rejected; changed candidate/evidence/target/stale Active rejected |
| Next use | Approved whole bundle is explicitly selected; mismatch, missing activation or different local environment blocks use; original Skill files remain byte-identical |
| Durable local state | Approval alone preserves Active; receipt/pointer write failures and concurrent changes cannot claim completed adoption |
| ABA/concurrent Active | Reject same-version different-receipt and A -> B -> A changes, half-null pairs and stale approval; compare both fields under one lock, preserve existing Active and keep the hash private |
| Public trace | Report -> round/cycle -> approval/use evidence resolves to the selected dashboard run; browser is read-only and mock/sample states are visible |
| No fake production path | Test doubles exist only in tests; integrated commands import real provider modules and do not substitute canned success |

Offline tests may use clearly labeled doubles and prove wiring/invariants, not
model improvement or observed live Skill use. Container controls prove actual
checker execution only. Initial live scope must be separately approved for
**one project and one Skill**, with exact task/split, model, N, invocation cap,
wall-time cap, per-session Credit conditions and acknowledgement of soft-cap
limitations. Authorization for evaluation does not authorize `approve`.
Improvement is not guaranteed; rejection, no improvement and incomplete evidence
are legitimate results.

Each completion report must name PR, commit, changed files, provided interfaces,
commands actually executed with outcomes/skips, and unresolved integration links.
Do not merge main, force-push, modify another session's worktree or clean unknown
files as part of this handoff.

## 10. Reviewed confirmation admission and isolation (revision 1.4)

This section supersedes the phase-1 blanket prohibition only when all its
production gates are implemented and verified together. Until then
`confirmation_isolation_unverified` remains mandatory. Schema/fixture success
or an adapter flag is not permission to remove either provider guard.
Development-only calls without registration retain their existing behavior.

### 10.1 Private reviewed disclosure and registration

Session 1 provides the pure common API:

```python
skill_assessments.validate_confirmation_disclosure(
    data, *, project, development_work_item, confirmation_work_item,
    original, source_path,
)  # -> validated data; not a capability or isolation certificate
```

The private disclosure object has exactly:

```text
schema_version development_input_sha256 confirmation_input_sha256
model_visible_files checker_only_files disclosure_sha256
```

Schema version is 1. Both file maps contain project-relative paths and actual
original byte SHA-256 values. Their disjoint union must equal the complete pinned
project inventory under the existing `tree_hash` safety/size bounds.
`model_visible_files` is exactly the union of the two WorkItems' permitted source
paths and all files in the original complete Skill bundle. All other files are
checker-only, including complete mixed test modules and imported fixtures.
The disclosure digest is `H(data without disclosure_sha256)`.
Unknown fields, wrong hashes, incomplete inventories, widened visible scopes,
symlinks, changed bundles and invalid WorkItems fail before model invocation.

The operator supplies the reviewed disclosure as a separate private input.
The CLI adapter adds `--confirmation-disclosure <private-json>` and requires it
together with `--confirmation-work-item`; neither is inferred from public reports.
The operator must review that the requests are distinct and the visible sources
and complete Skill companions do not contain held-out task/test answers.
Identical requests or literal confirmation request text in development-visible
input are rejected. This negative check does not detect all paraphrases or prove
semantic independence. Unsupported overlapping disclosure remains blocked, never
"fixed" by redacting bytes and claiming the same captured version.

Session 1 persists validated private WorkItems at the section 7 canonical paths.
It retains disclosure and invocation artifacts only below the runtime-private
root, never in result packets, public inputs, summaries or uploaded diagnostics.

Session 2 owns:

```python
skill_pipeline.register_confirmation(
    runtime, model, project, bundle, skill_key, rubric, images, artifact,
    *, development_work_item, confirmation_work_item, disclosure, deadline,
)  # -> opaque runtime-local registration ID
skill_pipeline.prepare_confirmation(
    runtime, model, context, selected, artifact,
    *, registration_id, deadline,
)  # -> unchanged exact replay context; feedback is None
```

The session 1 `_replay_session` caller enters the verified runtime boundary,
then registers the pair before development preparation or any model exposure.
Registration validates disclosure and binds the actual same BudgetRuntime,
original full Capture, project/source tree, Skill identity/path, model/CLI,
rubric/policy/evaluator, prepared images, mode and original authorized budget.
It retains immutable private bytes and original observations in the existing
provider-owned runtime-local state. A new runtime, late registration, changed
input or registration ID reconstructed from public data is not accepted.
Registration performs no generation or confirmation application.

Session 1 supplies a closure accepting the selected Capture and calling
`prepare_confirmation` with the registration ID and a fresh private artifact
path. Session 3 passes a copy only after verified development selection.
Existing `prepare_replay`, `evaluate_candidate`, `generate_candidate` and
`development_feedback` signatures remain unchanged. No registration fields are
added to WorkItem, the exact replay context, replay, cycle or adoption formats.

### 10.2 Enforced model-process boundary, owned by session 1

The runtime exposes:

```python
with runtime.confirmation_isolation(deadline=deadline):
    # Registration, development, final preparation/evaluation share this scope.
    ...
runtime.require_confirmation_isolation()  # checks live internal capability; no flag argument
```

Use the existing Docker prerequisite rather than a second model provider.
Run the pinned native Copilot executable read-only on the pinned Node runtime
image. Validate the installed native package against the repository lock,
retain its exact byte digest and image ID, and fail on drift. No automatic image
pull, package installation, daemon-policy modification or privileged fallback is
part of this API. Missing prerequisites fail before paid invocation.

Each invocation uses a fresh role workspace, HOME/config and bounded output
location. Generator/judge workspaces are empty: even a guide artifact directory
passed as `workdir` must not be mounted. The developer receives only the exact
validated complete Skill bundle. Source contents and the correct task request
reach it through the existing explicitly bounded prompt, not a project mount.
The host writes private model/checker artifacts after collecting the process
result; prior artifacts and usage history are never input mounts.

The process runs non-root, with read-only root, dropped capabilities,
no-new-privileges and bounded temporary storage/process/memory. Do not mount
the source project, frozen checker copy, confirmation/private store, whole host
HOME, ancestor/sibling workspace, host socket/device or Docker socket; do not use
host PID or host networking. Credentials are passed through the existing minimal
token environment only, never command text or public records. Existing disabled
hooks/memory/custom instructions/plugins/MCP and observed tool/activation checks
still apply inside the isolated filesystem, not merely in a host-side inventory.

Before issuing its private capability, the actual runtime must run non-model
subprocess probes with network disabled. Probes verify the allowed workspace
is readable, the denied host/project/private/history/sibling sentinels are not,
the root is read-only, capabilities are dropped and process isolation is active.
Provider-supplied booleans or successful test doubles cannot issue the real
runtime capability. Check the live capability and pinned inputs at registration,
confirmation admission, and before/after model applications.

Model execution can use normal container networking only behind the existing
separately authorized live/authentication/budget gates; offline access probes do
not invoke the model or require its token. This boundary promises local file and
process isolation, not a generic network-egress policy or defense against a
malicious host administrator, compromised kernel/Docker daemon or trusted CLI
distribution. A new egress policy/service is not introduced here.

Timeout/cancellation must clean up only the exact owned container, retaining an
explicit failure. A missing/failed sandbox never retries on the host. Exiting the
scope invalidates its capability. Unsupported runtime/platform configurations
keep confirmation blocked. No paid execution is authorized by these declarations.

### 10.3 Frozen selection, one-use exposure and failures

`prepare_confirmation` accepts only the exact retained improved development
candidate and unchanged registration/reference. It closes generation and
development-feedback issuance for that experiment before any final request is
exposed, including reuse of previously issued packets. Confirmation rows are
never registered as generation evidence.

Under the existing runtime lock, admission exclusively creates an immutable
private marker at
`.skillops-private/confirmation-exposures/<confirmation_input_sha256>.json`.
It records the registered input/disclosure/selected-version bindings and mode.
The provider owns this marker; it is never public approval or evidence of task
success. Exclusive creation, rather than identical-value idempotence, enforces
one use. A prior marker blocks a new runtime/process too. Consume before model
exposure and keep it on failure; no automatic recovery, retry or marker removal.
An unused registration does not consume the task. Renaming a task to reuse
exposed final material violates the operator-reviewed new-task requirement.

Confirmation retains original Capture and base guide quality without rejudging
the baseline. Only its distinct WorkItem input/reference hashes and fresh
original-check observations differ; all existing loop/reference bindings remain.
Evaluate the frozen selected bytes once with fresh base/candidate applications,
the same check policy and the same shared invocation/time/Credit budget.
No confirmation failure can trigger another generation or silently become a
development-only approval. Persist through the existing callback with
`generation=None`; callback failure still propagates under revision 1.3.

| Condition | Required failure |
| --- | --- |
| Missing/late registration | `confirmation_not_registered` |
| Registered inputs/runtime/image/CLI changed | `confirmation_inputs_changed` |
| Wrong, unretained or non-improved selected Capture | `confirmation_candidate_mismatch` |
| Previously consumed confirmation input/admission | `confirmation_already_used` |
| Generation or development feedback after admission | `development_closed` |
| Unsupported disclosure or unavailable/unverified runtime boundary | `confirmation_isolation_unverified` |

Preserve more specific existing validation/budget/IO errors where applicable.
Operational failures are unverified, observed task/regression failures rejected;
neither becomes success through fallback. Status and approval eligibility still
require the actual persisted evidence, not a returned registration ID.

### 10.4 Next-use execution handoff and acceptance

Session 2 also provides the narrow application primitive for session 1:

```python
skill_pipeline.execute_work(
    runtime, model, project, captured, images, artifact, *, work_item, deadline,
)  # -> (application receipt, checker observation)
```

Reuse the application implementation, not a second paired evaluator.
Validate the fresh private development WorkItem and project/check commitments,
stage the exact complete captured bundle, enforce runtime activation and inventory
before/after invocation, apply only permitted replacements to a pristine copy,
then run the protected checker. No candidate generation, guide baseline,
approval/receipt-store or Active-pointer write occurs in this primitive.
Session 1 calls it only after `resolve_approved`, fresh-run/work checks and a
separately authorized budget. Its observed application/check output feeds the
host-derived section 6 receipt and task report; model text is never use evidence.

Before integrating confirmation, tests must cover registration/drift/wrong
runtime, mixed hidden fixtures and Skill companions, actual denied filesystem
reads, unexpected tools/instructions, retained-candidate mismatch, consumed
admission, old development-packet reuse, shared limits and no post-final round.
Use actual provider/loop/validators/persistence in integration tests; only external
model/container transport may be doubled with explicit offline labels.
Real no-model container access probes are a separate required check. Neither
those probes nor synthetic confirmation/adoption data prove paid model results,
human approval, verified operational use or public deployment.
