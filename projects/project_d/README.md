# Project D: queue failure lifecycle

This is a synthetic, standard-library-only benchmark inspired by public bug
reports, not a reproduction of RQ or urllib3. The implementations and tests
were written independently; no upstream implementation or patch is bundled.
The installed Skill is an unvalidated neutral draft, not a deliberately
weakened baseline.

## Public provenance

| Source | Reported failure pattern | Local adaptation |
| --- | --- | --- |
| [RQ #2516](https://github.com/rq/rq/pull/2516) | A failure in dependent enqueueing prevents slot release; follow-up actions require committed state | `failure.py`: atomic persistence and independently failing side effects |
| [RQ #2515](https://github.com/rq/rq/pull/2515) | Repeated iteration loses dependent jobs from a generator | `enqueue.py`: stable partition of one-shot inputs |
| [urllib3 #3743](https://github.com/urllib3/urllib3/pull/3743) | An excessive Retry-After delay causes time conversion failure | `retry.py`: pure bounded delay parsing without sleeping |

The PRs were confirmed merged during source research. Their fixes were not
reproduced against upstream revisions here. The local operation names, return
values, retry rules, and delay cap are this benchmark's contracts, not claims
about upstream APIs. Public fixes may already be known to a model.

## Contracts and checks

`eval/datasets/project_d.json` is the authoritative task-request recipe.

| Role | Task | Editable source | Checks |
| --- | --- | --- | --- |
| Development | `failure-lifecycle` | `failure.py` | 18 |
| Validation | `batch-iterable` | `enqueue.py` | 5 |
| Confirmation | `bounded-retry-after` | `retry.py` | 7 |

The development task covers committed versus uncommitted failures, notification,
dependent enqueueing, slot release, stopped jobs, scheduled retries, exhausted
retries, ordered error records, input preservation, and unexpected exceptions.
A protected memory backend records each operation and injects deterministic
exceptions. The fault matrix checks all 16 fault combinations against four
flag combinations; these are subtests, not 64 independent benchmark tasks.
The other tasks have separate sources and checks.

Run the intentionally failing baseline source checks:

```bash
cd projects/project_d
python3 -B -m unittest discover -s tests
```

An exit code of 1 is expected: the starter implementations contain defects.
This is not evidence that an agent using the original Skill fails.
The surrounding `skillops/tests/test_project_d_sample.py` suite verifies the
recipe, the assertion-only starter failures, and that reference repairs can
satisfy the checks without changing them. Reference repairs are not part of the
project or model task inputs.

## Prepare a pinned experiment

From the repository root, after committing all project files:

```bash
python3 -B skillops/evaluation_datasets.py prepare \
  --spec eval/datasets/project_d.json \
  --output .skillops-private/datasets/project_d.json
python3 -B skillops/evaluation_datasets.py export-development \
  --dataset .skillops-private/datasets/project_d.json \
  --task failure-lifecycle \
  --output .skillops-private/work-items/project_d-development.json
```

These commands make no model calls. Outputs must be new private paths.
Source changes require a fresh committed snapshot and new output paths.

Screen the original Skill using the existing `skill_pipeline.execute_work`
provider with `BudgetRuntime`, a live-authorized policy, pinned check images,
and the exported development WorkItem. Preserve the application receipt,
check observation, generated source, usage, and source identity privately.
Do not count runtime or authentication errors as task failures.
Only a verified failure after the agent's repair qualifies this task for an
optional local GEPA N=2 run. A passing original stops this experiment; do not
weaken it or keep increasing N to manufacture improvement.

The current runner executes full-project regression checks, including checks
assigned other roles. This recipe therefore provides structural separation,
not secret held-out execution. Only required development checks determine
screening success. Neither the other-role checks nor `development_reuse`
establish independent validation, confirmation, approval, or adoption.
Do not publish private artifacts or apply generated repairs to this starter.
