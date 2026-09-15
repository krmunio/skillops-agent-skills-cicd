---
name: develop
description: Fix a small Python application, add regression tests, and review the resulting change against the supplied requirements.
---

Read the request, public API contract and supplied source before changing code.
Identify the root cause and make the smallest complete correction.
Validate input at the documented boundary without changing unrelated behavior.
Add executable standard-library unittest tests for the regression and relevant
boundary conditions. Keep input data unchanged when the contract requires it.
Review the proposed diff for correctness, missing cases and unintended changes.
Distinguish observed behavior from assumptions; never claim tests ran when they did not.

When the caller supplies source in the prompt and requests structured output,
use that source directly and return exactly the requested JSON containing the
complete source, complete tests and a concise review. Use only available tools.
The caller applies the files and executes tests; do not invent execution results.
