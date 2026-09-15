# Historical baseline evidence

`baseline-v2.json` is a sanitized projection of actual **local** Copilot CLI
runs on September 14, 2026, using `gpt-6-astra`. It is not a model run performed
by GitHub Actions, a signed attestation, or evidence of skill improvement.

| Task | Family / split | Protected checks | Judge / 100 |
|---|---|---:|---:|
| pagination-first-page | listing / development | 26/26 | 100 |
| filter-before-pagination | listing / development | 26/26 | 100 |
| pagination-boundaries | listing / development | 26/26 | 100 |
| labels-normalization | labels / development | 17/17 | 100 |
| issue-update-validation | updates / heldout | 22/22 | 100 |

Nine calibration controls cover three families, each with known-good,
known-bad and instruction-in-data observations. All family gates passed.
The task-weighted mean judge score is 100 with denominator five, not evidence
of generalization from five independent samples. Three tasks share a family.

The JSON retains original report SHA-256 digests, benchmark/input/skill hashes,
model/CLI/image identifiers, fixed/generated counts, judge dimension scores,
per-role usage units and null reasons, elapsed times and aggregate denominators.
Values were compared against both original reports before publication.
No currency conversion or replacement of missing values with zero is performed.

Original private profiles, raw CLI logs, session IDs, local artifact links,
discovered personal skill names and free-text model responses are not included.
The original report digests identify the unpublished originals; they do not
let a reader independently authenticate those originals or reconstruct omitted
proposals. A new live run requires an authorized CLI login/model entitlement.

CI validates this snapshot's structure and internal consistency, and runs
the evaluator's offline and actual Docker controls. It does **not** replay
these historical model sessions. The snapshot is immutable history: later
evaluator or benchmark changes require fresh calibration and new live records,
not rewriting these results to match new source hashes.
