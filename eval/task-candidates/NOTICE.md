# Source and license notices

This directory contains local research artifacts, not an upstream release.
No upstream source, patch, or test is represented as original SkillOps code.

## RQ

- Project: <https://github.com/rq/rq>
- License: BSD-2-Clause, verified from the literal `LICENSE` at all four
  before/after revisions, not inferred from GitHub's license classifier.
- Full copyright notice, conditions, disclaimer, and concluding statement:
  [licenses/rq-LICENSE](licenses/rq-LICENSE).
- `evaluator/rq-2515/check.py` reproduces the regression method from
  `tests/test_queue.py` at `f114a649c57555bed3100b7b1e00fb8996d5d1d5`.
  Imports/class wrapper are adapted; the method's AST is unchanged.
  Two control methods are reused from the corresponding checkout; their ASTs
  are identical before and after.
- `evaluator/rq-2516/check.py` is a newly written evaluator integration check,
  not an upstream regression test. RQ API setup references upstream
  `tests/test_retry.py` and `tests/test_rate_limit.py`; the retained RQ notice
  also covers these references/adaptations.
- Both RQ `solution.patch` files and code snippets in GitHub metadata are
  upstream-derived and retain the above license via this notice.

## urllib3

- Project: <https://github.com/urllib3/urllib3>
- License: MIT, verified from literal `LICENSE.txt` in both revisions.
- Full copyright and permission notice:
  [licenses/urllib3-LICENSE.txt](licenses/urllib3-LICENSE.txt).
- `evaluator/urllib3-3743/solution.patch` and metadata snippets derive from
  `13f0bfd55e4468fe1ea9c6f809d3a87b0f93ebab`.
- `evaluator/urllib3-3743/check.py` is newly written for this study.
  It is **not** the upstream added test. The latter uses a new class constant
  absent before the fix; simply copying it would fail before a behavioral
  assertion. The authored checks instead fail on the unbounded values.

## Dependencies and redistribution

Images, installed dependencies, and complete source trees are preparation
artifacts, not committed or redistributed here. Preparation retains upstream
license files in `.cache/`; pip wheel URLs, versions, SHA-256 hashes, and
package license metadata are recorded in
`evaluator/dependency-install-report.json`. Images are digest-pinned.
If exporting source, copied tests, patches, or binaries elsewhere, retain
the applicable full notices; distributing runtime images requires separately
preserving their dependency notices. `NOASSERTION` is not used as permission.
