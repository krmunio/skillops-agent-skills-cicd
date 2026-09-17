---
name: develop
description: Repair the issue-management sample when asked to fix Python behavior, add regression coverage, or review a proposed change.
---

# Develop the issue-management sample

Read the requested behavior and existing tests before changing code. Identify the affected
function and preserve unrelated behavior and public interfaces.

Make a focused implementation change. Cover ordinary inputs, boundary cases, invalid inputs,
ordering and input mutation where relevant. Preserve existing assertions and fixtures.

Review the resulting diff against the request. Report any unverified behavior or remaining
failures. When tools cannot execute tests, state that limitation rather than claiming a pass.

This project is a controlled demonstration with intentional defects, not a production benchmark.
