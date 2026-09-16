# Anthropic Eval Principles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable repeated trials and variance-aware reporting to SkillOps while preserving the current single-trial behavior by default.

**Architecture:** Keep each model invocation as the existing isolated `evaluate_task` unit and repeat that unit in `baseline` and `compare`. Store trial rows as flat report entries with an additive `trial` field, then aggregate them with small standard-library helpers; this avoids a new report abstraction and lets eligibility independently recompute the same decision.

**Tech Stack:** Python 3.12 standard library (`argparse`, `decimal`, `statistics`, `unittest`), existing Copilot CLI runtime, JSON/Markdown reports.

---

## File Structure

- Modify `skillops.py`: validate `--trials`, attach trial identity, repeat baseline runs, and compute trial statistics.
- Modify `candidates.py`: accept repeated baseline evidence, execute repeated paired comparisons, and apply the existing policy to every fresh trial.
- Modify `repositories.py`: validate and recompute recorded multi-trial comparisons before declaring eligibility.
- Modify `tests/test_skillops.py`: cover trial validation, statistics, baseline repetition, and default compatibility.
- Modify `tests/test_candidates.py`: cover repeated development evidence, paired ordering, and incomplete-trial rejection.
- Modify `tests/test_repositories.py`: cover eligibility validation for repeated trial identities.
- Modify `README.md`: document Anthropic principle mapping and repeated-trial commands.
- Modify `README.ko.md`: add the same user guidance in Korean.

### Task 1: Add Trial Validation and Statistics

**Files:**
- Modify: `skillops.py:1-170`
- Test: `tests/test_skillops.py:567-590`

- [ ] **Step 1: Write failing unit tests for trial validation and statistics**

Add imports and tests to `tests/test_skillops.py`:

```python
from argparse import ArgumentTypeError


def test_trial_count_requires_a_positive_integer(self):
    import skillops
    self.assertEqual(skillops.positive_int("3"), 3)
    for value in ("0", "-1", "1.5", "true"):
        with self.subTest(value=value), self.assertRaises(ArgumentTypeError):
            skillops.positive_int(value)


def test_trial_summary_reports_success_rate_and_variance(self):
    import skillops
    rows = [
        {
            "id": "a", "trial": 1, "status": "completed", "elapsed_seconds": 10,
            "execution": {"fixed": {"all_passed": True}, "generated": {"passed": True}},
            "judge": {"score": 100},
            "developer_usage": {"nano_aiu": {"value": 20, "unit": "nano_aiu"}},
            "judge_usage": {"nano_aiu": {"value": 10, "unit": "nano_aiu"}},
        },
        {
            "id": "a", "trial": 2, "status": "completed", "elapsed_seconds": 14,
            "execution": {"fixed": {"all_passed": False}, "generated": {"passed": True}},
            "judge": {"score": 50},
            "developer_usage": {"nano_aiu": {"value": 30, "unit": "nano_aiu"}},
            "judge_usage": {"nano_aiu": {"value": 10, "unit": "nano_aiu"}},
        },
    ]
    result = skillops.summarize(rows, 2)
    self.assertEqual(result["trial_success_rate"], 0.5)
    self.assertFalse(result["all_trials_passed"])
    self.assertEqual(result["judge_score"], {
        "mean": 75.0, "standard_deviation": 25.0, "denominator": 2,
    })
    self.assertEqual(result["elapsed_seconds"], {
        "mean": 12.0, "standard_deviation": 2.0, "denominator": 2,
    })
    self.assertEqual(result["cost_nano_aiu"], {
        "mean": 35.0, "standard_deviation": 5.0, "denominator": 2,
    })
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
python3 -m unittest \
  tests.test_skillops.EvaluationTests.test_trial_count_requires_a_positive_integer \
  tests.test_skillops.EvaluationTests.test_trial_summary_reports_success_rate_and_variance -v
```

Expected: both tests fail because `positive_int` and the new summary fields do not exist.

- [ ] **Step 3: Implement the minimal standard-library helpers**

In `skillops.py`, add:

```python
from argparse import ArgumentTypeError
from decimal import Decimal
from statistics import fmean, pstdev
```

Add these helpers before `summarize`:

```python
def positive_int(value):
    try:
        parsed = int(value)
    except ValueError as error:
        raise ArgumentTypeError("trials must be a positive integer") from error
    if str(parsed) != value or parsed <= 0:
        raise ArgumentTypeError("trials must be a positive integer")
    return parsed


def row_cost(row):
    values = []
    for role in ("developer_usage", "judge_usage"):
        usage = row.get(role)
        metric = usage.get("nano_aiu") if isinstance(usage, dict) else None
        if metric is None or metric.get("value") is None:
            return None
        if metric.get("unit") != "nano_aiu":
            raise RuntimeFailure("invalid_metric", "Cost requires CLI-reported NanoAIU units.")
        value = metric["value"]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise RuntimeFailure("invalid_metric", "Metrics must be finite nonnegative numbers, not booleans.")
        values.append(Decimal(str(value)))
    return sum(values, Decimal(0))


def stats(values):
    values = [float(value) for value in values]
    return {
        "mean": round(fmean(values), 2) if values else None,
        "standard_deviation": round(pstdev(values), 2) if values else None,
        "denominator": len(values),
    }
```

Import `math`, then extend `summarize` without removing its current fields:

```python
def summarize(rows, requested):
    evaluated = [row for row in rows if row.get("status") == "completed" and "judge" in row]
    attempts = [row for row in rows if row.get("attempted", True)]
    correct = [
        row for row in rows
        if row.get("execution", {}).get("fixed", {}).get("all_passed") is True
    ]
    successful = [
        row for row in rows
        if row.get("status") == "completed"
        if row.get("execution", {}).get("fixed", {}).get("all_passed") is True
        and row.get("execution", {}).get("generated", {}).get("passed") is True
    ]
    scores = [row["judge"]["score"] for row in evaluated]
    elapsed = [row["elapsed_seconds"] for row in attempts if type(row.get("elapsed_seconds")) in (int, float)]
    costs = [value for row in evaluated if (value := row_cost(row)) is not None]
    return {
        "requested": requested,
        "attempted": len(attempts),
        "evaluation_completed": len(evaluated),
        "errors": sum(row.get("status") != "completed" for row in attempts),
        "blocked": sum(not row.get("attempted", True) for row in rows),
        "correctness_successes": len(correct),
        "trial_successes": len(successful),
        "trial_success_rate": round(len(successful) / requested, 4) if requested else None,
        "all_trials_passed": len(successful) == requested and requested > 0,
        "mean_judge_score": round(sum(scores) / len(scores), 2) if scores else None,
        "judge_score_denominator": len(scores),
        "judge_score": stats(scores),
        "elapsed_seconds": stats(elapsed),
        "cost_nano_aiu": stats(costs),
    }
```

- [ ] **Step 4: Run the focused tests**

Run the Step 2 command again.

Expected: both tests pass.

- [ ] **Step 5: Run existing aggregation tests**

Run:

```bash
python3 -m unittest \
  tests.test_skillops.EvaluationTests.test_aggregate_keeps_failures_in_requested_denominator \
  tests.test_candidates.CandidateTests.test_gate_blocks_incomplete_duplicate_and_invalid_metrics -v
```

Expected: both tests pass because the shared helper is additive and the candidate module still uses its existing parser.

- [ ] **Step 6: Commit the helpers**

```bash
git add skillops.py tests/test_skillops.py
git commit -m "feat: add trial evaluation statistics" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2: Repeat Baseline Tasks in Isolated Trials

**Files:**
- Modify: `skillops.py:353-525`
- Test: `tests/test_skillops.py:610-700`
- Test: `tests/test_repositories.py:180-195`

- [ ] **Step 1: Write a failing baseline repetition test**

Add to `tests/test_skillops.py`:

```python
def test_baseline_repeats_each_task_and_groups_trial_summaries(self):
    import skillops
    data = json.loads((self.root / "eval/tasks.json").read_text())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in (*self.evaluation.FINGERPRINT_FILES, "skills/develop/SKILL.md"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / name, target)
        runtime = self.runtime.CopilotRuntime(root)
        context = {"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}
        calls = []

        def evaluate(runtime, model, task, contract, seed, skill, rubric, context, identity, directory, trial=1):
            calls.append((task["id"], trial, directory.relative_to(directory.parents[2]).as_posix()))
            return {
                "id": task["id"], "family": task["family"], "split": task["split"],
                "trial": trial, "status": "completed", "attempted": True,
                "execution": {"fixed": {"all_passed": True}, "generated": {"passed": True}},
                "judge": {"score": 100}, "elapsed_seconds": trial,
            }

        with patch.object(skillops, "context_for", return_value=context), patch.object(
            skillops, "find_calibration", return_value={"path": str(root / "runs/control/calibration.json")}
        ), patch.object(skillops, "evaluate_task", side_effect=evaluate):
            summary, code = skillops.baseline(runtime, "gpt-6-astra", root, trials=3)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), len(data["tasks"]) * 3)
        report = json.loads((root / summary["report"]).with_suffix(".json").read_text())
        self.assertEqual(report["trial_count"], 3)
        self.assertEqual(len(report["tasks"]), len(data["tasks"]) * 3)
        self.assertEqual({row["trial"] for row in report["tasks"]}, {1, 2, 3})
        self.assertEqual(report["aggregate"]["requested"], len(data["tasks"]) * 3)
        self.assertEqual(report["task_summaries"][data["tasks"][0]["id"]]["requested"], 3)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
python3 -m unittest \
  tests.test_skillops.EvaluationTests.test_baseline_repeats_each_task_and_groups_trial_summaries -v
```

Expected: FAIL because `baseline` does not accept `trials`.

- [ ] **Step 3: Attach trial identity to each evaluation**

Change the signature and initial row in `skillops.py`:

```python
def evaluate_task(runtime, model, task, contract, seed, skill_bytes, rubric, context, identity, task_dir, trial=1):
    family = task["family"]
    row = {
        "id": task["id"], "family": family, "split": task["split"], "trial": trial,
        "status": "running", "attempted": True,
        "seed_sha256": sha256(seed.encode()).hexdigest(),
        "skill_sha256": sha256(skill_bytes).hexdigest(),
    }
```

- [ ] **Step 4: Repeat baseline tasks and build per-task summaries**

Change the baseline signature:

```python
def baseline(runtime, model, judge_work, repository=None, trials=1):
```

Set the report count before execution:

```python
report["trial_count"] = trials
requested = len(tasks) * trials
```

Replace the current task loop with:

```python
for task in tasks:
    family = task["family"]
    seed, contract = seeds[family], data["families"][family]["contract"]
    for trial in range(1, trials + 1):
        task_dir = directory / task["id"] / f"trial-{trial}"
        task_dir.parent.mkdir(exist_ok=True)
        row = evaluate_task(
            runtime, model, task, contract, seed, skill_bytes, rubric,
            context, identity, task_dir, trial,
        )
        report["tasks"].append(row)
        report["aggregate"] = summarize(report["tasks"], requested)
        render_report(directory, report)
```

When adding blocked rows, create one for every missing `(task ID, trial)` tuple:

```python
completed = {(row["id"], row["trial"]) for row in report["tasks"]}
report["tasks"].extend(
    {
        "id": task["id"], "family": task["family"], "split": task["split"],
        "trial": trial, "status": "blocked", "attempted": False,
    }
    for task in tasks
    for trial in range(1, trials + 1)
    if (task["id"], trial) not in completed
)
```

Build additive task summaries after the try/except:

```python
report["task_summaries"] = {
    task["id"]: summarize([row for row in report["tasks"] if row["id"] == task["id"]], trials)
    for task in tasks
}
```

Pass `task_count * trials` to split and family summaries.

- [ ] **Step 5: Add the CLI flag with default compatibility**

For `baseline` and `compare` parsers:

```python
command.add_argument("--trials", type=positive_int, default=1,
                     help="Fresh trials per task or arm; defaults to 1.")
```

Pass it only to commands that support it:

```python
elif args.command == "baseline":
    report, exit_code = baseline(runtime, args.model, workdir, args.repository, args.trials)
```

- [ ] **Step 6: Update Markdown baseline rendering**

Add these lines near the model/catalog summary:

```python
f"Trials per task: {report.get('trial_count', 1)}.",
f"All requested trials passed: {summary.get('all_trials_passed', False)}.",
f"Judge score mean ± population SD: "
f"{summary.get('judge_score', {}).get('mean')} ± "
f"{summary.get('judge_score', {}).get('standard_deviation')}.",
```

Add a `Trial` column to the task table and render `row.get("trial", 1)`.

- [ ] **Step 7: Run baseline-focused tests**

Run:

```bash
python3 -m unittest \
  tests.test_skillops.EvaluationTests.test_baseline_repeats_each_task_and_groups_trial_summaries \
  tests.test_skillops.EvaluationTests.test_pipeline_blocks_without_calibration_and_preserves_failed_attempt \
  tests.test_repositories.RepositoryTests.test_bound_baseline_uses_target_bytes_and_pinned_skill -v
```

Expected: all pass. Update existing assertions to expect `trial == 1` and unchanged call counts under the default.

- [ ] **Step 8: Commit baseline trials**

```bash
git add skillops.py tests/test_skillops.py tests/test_repositories.py
git commit -m "feat: run isolated baseline trials" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 3: Repeat Candidate Comparisons and Preserve Development Evidence

**Files:**
- Modify: `candidates.py:1-386`
- Modify: `skillops.py:510-530`
- Test: `tests/test_candidates.py:35-220`

- [ ] **Step 1: Write failing tests for repeated evidence and paired ordering**

Add to `tests/test_candidates.py`:

```python
def test_development_packet_accepts_repeated_trials_without_heldout_leakage(self):
    report = self.historical()
    report["trial_count"] = 2
    report["tasks"] = [
        {**deepcopy(row), "trial": trial}
        for row in report["tasks"]
        for trial in (1, 2)
    ]
    packet = self.c.development_packet(self.root, report, "gpt-6-astra")
    self.assertEqual(len(packet["development"]), 8)
    self.assertEqual({row["trial"] for row in packet["development"]}, {1, 2})
    self.assertNotIn(self.catalog["tasks"][-1]["id"], json.dumps(packet))


def test_compare_repeats_pairs_and_alternates_order_per_trial(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        runtime, baseline_id = self.prepare(root)
        value = {
            "instructions": "Read the contract, fix the cause, test boundaries, and make only supported claims.",
            "rationale": "Synthetic candidate.",
        }
        with patch.object(runtime, "invoke", return_value={
            "content": json.dumps(value), "usage": usage_metrics(None, "gpt-6-astra")
        }):
            candidate_id = self.c.propose(runtime, "gpt-6-astra", baseline_id)[0]["run_id"]
        context = {"model": "gpt-6-astra", "image": "sha256:" + "0" * 64}
        calls = []

        def execute(runtime, model, task, contract, seed, skill, rubric, context, identity, directory, trial=1):
            calls.append((task["id"], trial, directory.name))
            return {**observation(), **task, "trial": trial}

        with patch.object(self.c, "context_for", return_value=context), patch.object(
            self.c, "find_calibration", return_value={"path": str(root / "runs/control/calibration.json")}
        ), patch.object(self.c, "evaluate_task", side_effect=execute):
            report, code = self.c.compare(runtime, "gpt-6-astra", root, candidate_id, trials=2)

        self.assertEqual(code, 1)
        stored = json.loads((root / report["artifact"]).read_text())
        self.assertEqual(stored["trial_count"], 2)
        self.assertEqual(len(stored["pairs"]), len(self.catalog["tasks"]) * 2)
        self.assertEqual(stored["pairs"][0]["order"], ["base", "candidate"])
        self.assertEqual(stored["pairs"][1]["order"], ["candidate", "base"])
        self.assertEqual({pair["trial"] for pair in stored["pairs"]}, {1, 2})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
python3 -m unittest \
  tests.test_candidates.CandidateTests.test_development_packet_accepts_repeated_trials_without_heldout_leakage \
  tests.test_candidates.CandidateTests.test_compare_repeats_pairs_and_alternates_order_per_trial -v
```

Expected: FAIL because reports require one row per task and `compare` lacks `trials`.

- [ ] **Step 3: Accept exact repeated trial populations in `development_packet`**

Read the report count with a backward-compatible default:

```python
trials = report.get("trial_count", 1)
if type(trials) is not int or trials <= 0:
    raise RuntimeFailure("invalid_baseline", "Baseline trial count must be a positive integer.")
rows = report.get("tasks")
expected = {(task["id"], trial) for task in catalog["tasks"] for trial in range(1, trials + 1)}
```

Normalize historical single-trial rows with:

```python
for row in rows:
    trial = row.get("trial", 1)
    identity = (row.get("id"), trial)
    if identity not in expected or identity in by_id:
        raise RuntimeFailure("invalid_baseline", "Historical task/trial identities must be exact and unique.")
    by_id[identity] = row
```

Iterate development rows by task and trial, add `trial` to each feedback object, and add a task ID to `failures` only once:

```python
for task in catalog["tasks"]:
    if task["split"] != "development":
        continue
    for trial in range(1, trials + 1):
        row = by_id[(task["id"], trial)]
        if row.get("split") != task["split"] or row.get("family") != task["family"]:
            raise RuntimeFailure("invalid_baseline", "Task identity/family/split must match the current catalog.")
        if not isinstance(row.get("status"), str) or type(row.get("attempted")) is not bool:
            raise RuntimeFailure("invalid_baseline", "Development status and attempted flag are required.")
        feedback = {
            "id": task["id"], "trial": trial,
            "request": task["request"], "status": row["status"],
        }
        successful = False
        if row["status"] == "completed":
            if row.get("skill_activated") is not True or row["attempted"] is not True:
                raise RuntimeFailure("invalid_baseline", "Completed feedback requires actual skill activation.")
            fixed, generated, dimensions = quality(row)
            measured_cost = row_cost(row)
            feedback.update(
                fixed_passed=fixed,
                generated_passed=generated,
                judge_dimensions=dict(zip(DIMENSIONS, dimensions)),
                cost_nano_aiu=float(measured_cost) if measured_cost is not None else None,
                elapsed_seconds=float(number(row.get("elapsed_seconds"))),
            )
            successful = fixed and generated
        else:
            error = row.get("error", {})
            if not isinstance(error, dict) or not isinstance(error.get("code", "not_completed"), str):
                raise RuntimeFailure("invalid_baseline", "Failure code must be text.")
            feedback["error_code"] = error.get("code", "not_completed")
            if not re.fullmatch(r"[a-z0-9_]{1,128}", feedback["error_code"]):
                raise RuntimeFailure("invalid_baseline", "Failure code must be a bounded diagnostic identifier.")
        packet["development"].append(feedback)
        if not successful and task["id"] not in packet["failures"]:
            packet["failures"].append(task["id"])
```

- [ ] **Step 4: Reuse the shared cost parser**

Change the import from `skillops`:

```python
from skillops import (
    context_for, evaluate_task, find_calibration, load_tasks, new_run,
    row_cost, summarize, write_json,
)
```

Delete the local `cost` function and replace all `cost(row)` calls with `row_cost(row)`. Keep `number` for elapsed-time validation.

- [ ] **Step 5: Repeat paired comparisons**

Change:

```python
def compare(runtime, model, judge_work, candidate_id, repository=None, trials=1):
```

Create flat task/trial pairs:

```python
report["trial_count"] = trials
report["pairs"] = []
for task_index, task in enumerate(tasks):
    for trial in range(1, trials + 1):
        sequence = task_index * trials + trial - 1
        report["pairs"].append({
            "id": task["id"], "family": task["family"], "split": task["split"], "trial": trial,
            "order": ["base", "candidate"] if sequence % 2 == 0 else ["candidate", "base"],
            **{
                arm: {**task, "trial": trial, "status": "blocked", "attempted": False}
                for arm in ("base", "candidate")
            },
        })
```

Execute each pair into a trial-specific directory:

```python
for pair in report["pairs"]:
    task = next(task for task in tasks if task["id"] == pair["id"])
    pair_dir = directory / task["id"] / f"trial-{pair['trial']}"
    pair_dir.mkdir(parents=True)
    for arm in pair["order"]:
        pair[arm] = evaluate_task(
            runtime, model, task, catalog["families"][task["family"]]["contract"],
            seeds[task["family"]], skills[arm], rubric, context, identity,
            pair_dir / arm, pair["trial"],
        )
        render_comparison(directory, report)
```

Pass `len(tasks) * trials` to `decide` and all arm/split summaries. Add `task_summaries` grouped by task and arm.

- [ ] **Step 6: Make decision uniqueness trial-aware**

At the top of `decide`:

```python
identities = {(row.get("id"), row.get("trial", 1)) for row in pairs}
if (not pairs or len(pairs) != requested or len(identities) != requested
        or any(row.get(arm, {}).get("status") != "completed"
               for row in pairs for arm in ("base", "candidate"))):
    result["reasons"] = ["Every requested fresh pair must complete."]
    return result
```

The existing loop already enforces candidate test success, paired judge non-regression, and aggregate cost/time gates across every supplied pair. Keep it unchanged except for calling `row_cost`.

- [ ] **Step 7: Pass the CLI trial count to compare**

In `skillops.py`:

```python
report, exit_code = compare(
    runtime, args.model, workdir, args.candidate, args.repository, args.trials
)
```

- [ ] **Step 8: Update comparison Markdown**

Add `trial_count` and a `Trial` column. Render one line per task/trial pair; do not add a new viewer.

- [ ] **Step 9: Run candidate-focused tests**

Run:

```bash
python3 -m unittest tests.test_candidates -v
```

Expected: all candidate tests pass. Existing single-trial tests keep call counts of 10 and default `trial == 1`.

- [ ] **Step 10: Commit candidate trials**

```bash
git add candidates.py skillops.py tests/test_candidates.py
git commit -m "feat: compare repeated skill trials" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 4: Validate Multi-Trial Eligibility and Document Usage

**Files:**
- Modify: `repositories.py:264-330`
- Modify: `tests/test_repositories.py:200-390`
- Modify: `README.md:95-230`
- Modify: `README.ko.md:95-230`

- [ ] **Step 1: Write a failing eligibility tamper test**

Add to `tests/test_repositories.py`:

```python
def test_eligibility_requires_exact_trial_population(self):
    summary, code, _, _ = self.comparison(trials=2)
    self.assertEqual(code, 0)
    path = self.root / summary["artifact"]
    original = path.read_bytes()
    report = json.loads(original)
    self.assertEqual(report["trial_count"], 2)
    report["pairs"].pop()
    skillops.write_json(path, report)
    self.assertEqual(self.r.eligibility(self.root, "a", summary["run_id"])[1], 2)
    path.write_bytes(original)
```

Update the local `comparison` test helper to accept `trials=1` and pass it to `candidates.compare`.

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
python3 -m unittest \
  tests.test_repositories.RepositoryTests.test_eligibility_requires_exact_trial_population -v
```

Expected: FAIL because eligibility still expects exactly one pair per task.

- [ ] **Step 3: Validate the exact task/trial matrix**

In `repositories.eligibility`, replace the one-pair-per-task check with:

```python
trials = report.get("trial_count", 1)
require(type(trials) is int and trials > 0,
        "invalid_comparison", "Comparison trial count must be a positive integer.")
tasks = load_tasks(root)["tasks"]
pairs = report.get("pairs")
expected = [
    (task, trial)
    for task in tasks
    for trial in range(1, trials + 1)
]
require(isinstance(pairs, list) and len(pairs) == len(expected),
        "invalid_comparison", "Every registered task/trial must have one pair.")
for (task, trial), pair in zip(expected, pairs):
    require(
        isinstance(pair, dict)
        and pair.get("trial", 1) == trial
        and all(pair.get(key) == task[key] for key in ("id", "family", "split")),
        "invalid_comparison", "Comparison task/trial identities do not match the evaluation set.",
    )
    for arm in ("base", "candidate"):
        row = pair.get(arm)
        require(
            isinstance(row, dict)
            and row.get("trial", 1) == trial
            and all(row.get(key) == task[key] for key in ("id", "family", "split"))
            and row.get("status") == "completed"
            and row.get("attempted") is True
            and row.get("skill_activated") is True
            and row.get("skill_sha256") == hashes[arm]
            and row.get("seed_sha256") == snapshot["binding"]["source_sha256"][task["family"]],
            "invalid_comparison", "Pair lacks matching trial, source, skill or activation evidence.",
        )
decision = decide(pairs, len(expected))
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
python3 -m unittest tests.test_repositories -v
```

Expected: all repository tests pass, including recomputation and tamper checks.

- [ ] **Step 5: Document repeated trials in English**

In `README.md`, update examples:

```bash
python3 skillops.py baseline --model gpt-6-astra --trials 3
python3 skillops.py compare --candidate CANDIDATE_RUN_ID --model gpt-6-astra --trials 3
```

Add:

```markdown
`--trials` defaults to 1 for compatibility. Use 3 or more when you need a
basic view of model variance; model calls, elapsed time, and usage scale
linearly with the value. Reports retain every isolated trial and add per-task
success rates plus mean and population standard deviation for judge score,
elapsed time, and reported NanoAIU cost.

This follows the core Anthropic evaluation guidance without copying the
`skill-creator` workspace or viewer: repeated trials expose non-determinism,
Docker checks grade outcomes, the calibrated rubric judge covers qualitative
dimensions, every run starts clean, and stored CLI receipts/diffs remain
available for transcript review. These small samples still do not establish
statistical significance.
```

- [ ] **Step 6: Document repeated trials in Korean**

In `README.ko.md`, add the equivalent:

```markdown
`--trials`의 기본값은 호환성을 위해 1입니다. 모델 변동성을 기본적으로
확인하려면 3 이상을 사용합니다. 모델 호출 수, 경과 시간과 사용량은 지정한
횟수에 비례합니다. 보고서는 격리된 모든 trial을 보존하고 작업별 성공률,
judge 점수·경과 시간·NanoAIU 비용의 평균과 모집단 표준편차를 추가합니다.

이는 Anthropic `skill-creator` 작업공간이나 viewer를 복제하지 않고 핵심 평가
원칙만 반영합니다. 반복 trial은 비결정성을 드러내고, Docker 검사는 결과를,
교정된 rubric judge는 정성 항목을 평가합니다. 각 실행은 깨끗한 환경에서
시작하며 CLI 기록과 diff는 transcript 검토 근거로 남습니다. 작은 표본으로
통계적 유의성을 주장하지 않습니다.
```

- [ ] **Step 7: Run the full offline suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 8: Check CLI help**

Run:

```bash
python3 skillops.py baseline --help
python3 skillops.py compare --help
```

Expected: both commands show `--trials TRIALS`; invalid values such as `--trials 0` exit with argparse error code 2 before constructing the runtime.

- [ ] **Step 9: Check the final diff**

Run:

```bash
git --no-pager diff --check
git --no-pager diff --stat
```

Expected: no whitespace errors; only the planned Python tests, implementation files, and bilingual README files are changed.

- [ ] **Step 10: Commit eligibility and documentation**

```bash
git add repositories.py tests/test_repositories.py README.md README.ko.md
git commit -m "docs: explain variance-aware skill evaluation" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```
