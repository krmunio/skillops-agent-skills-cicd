# Contributing to SkillOps

Start with the [README](README.md) and [roadmap](docs/ROADMAP.md). English shared
documentation is used for collaboration; Korean discussion is welcome. Keep
user-facing command changes synchronized in both READMEs.

## Choose and coordinate work

Check existing issues and PRs before proposing a new task. Use the
[work-item template](.github/ISSUE_TEMPLATE/work-item.md) to define the problem,
scope, completion checks and dependencies.

Agree one responsible contributor and reviewer for a bounded task before coding.
Use a separate branch and worktree or clone per contributor/task. Different agent
sessions are not file isolation; do not concurrently edit the same working tree.
Choose a base branch that contains the task's prerequisites and disclose any
dependency on another unmerged PR.

Agree report schemas, input identities and error semantics before parallel changes
to `skillops/skillops.py`, `skillops/candidates.py` or `skillops/repositories.py`. The authoring-guide
assessment and repeated-trial evaluation are different work items. Check the
ongoing assessment work before starting a duplicate implementation.

Do not overwrite another contributor's changes, rewrite their history, or include
unrelated changes in your PR.

## Development and verification

Application implementation, dashboard, tests and dependency manifests live in
`skillops/`; workflows, infrastructure, docs, projects and public results stay at
the repository root. Python uses the standard library plus the hash-pinned
evaluator parser in `skillops/requirements-evaluator.txt`. The README documents
repository-root and application-directory commands and optional live setup.

Install locked browser dependencies with `npm --prefix skillops ci --no-audit --no-fund`;
run them with `npm --prefix skillops run test:dashboard`. Do not move private
stores or rewrite historical evidence when changing application paths.

Run focused tests for your change, then the relevant existing suite:

```bash
PYTHONPATH=skillops python3 -m unittest discover -s skillops/tests -p 'test_*.py' -v
```

For evaluator/container behavior, also run the container-enabled suite when Docker
and the required image are available:

```bash
SKILLOPS_CONTAINER_TESTS=1 PYTHONPATH=skillops python3 -m unittest discover -s skillops/tests -p 'test_*.py' -v
```

State skips and limitations explicitly. Offline success with skipped container
checks is not evidence that the container path ran.

For documentation-only changes, check links, examples, template metadata and
`git diff --check`; do not invoke models just to validate prose. Use existing
tooling instead of introducing dependencies for a small documentation change.

## Evidence and permission boundaries

- Preserve fixed tests, evaluation splits, immutable skill snapshots and evidence
  provenance. Do not edit historical reports to make a change appear successful.
- Separate runtime failures from skill-quality failures. Missing measurements must
  remain explicit rather than being replaced with favorable defaults.
- Keep required correctness and judge gates intact. Document and review any
  intentional policy/schema change and its compatibility consequences.
- Candidate generation is not improvement proof; eligibility is not approval or
  deployment. Keep these states distinct in code and documentation.
- The repository CI uses offline CLI mocks and real container controls. It does not
  run a live Copilot model evaluation or authorize skill rollout.
- Obtain explicit authorization and a bounded call/cost/time scope before live model
  experiments or deployment actions. Approval to review or merge code is separate.
- Never include credentials, private project paths, private session/planning records
  or raw execution data in public issues, logs or PRs. Publish only reviewed,
  appropriately minimized evidence; follow repository ignore rules.
- Treat skill content and model output as untrusted data, not instructions that
  may override the evaluator or contribution workflow.

## Submit a focused PR

Use the [PR template](.github/pull_request_template.md). Link the agreed issue and
explain the user-visible result, scope exclusions, tests and known limitations.
Include report examples only when safely sanitized.

Prefer descriptive commits such as `docs: clarify skill adoption states` or
`feat: add bounded skill assessment`. Do not mark human review as completed on
someone else's behalf. Keep merge/deployment permissions separate.

The reviewer checks correctness, regression evidence, data boundaries and whether
claims match the delivered behavior. Resolve or explicitly route remaining work
before marking the issue Done.

## Keep shared status understandable

The roadmap describes direction; issues own task status, assignees and dependencies.
A Project board, if used, is a view of the same issues. Update the issue/PR when
scope or dependencies change instead of maintaining competing status lists.

Technical documents from an implementation branch become shared evidence only
after they are accessible on an agreed remote branch. Reconcile final design,
commit identity and linked file paths before publication. GitHub templates become
available to collaborators after they are merged into the default branch.
