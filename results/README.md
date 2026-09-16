# Public evaluation results

`projects/` contains evaluated source snapshots. `results/` contains public-safe,
versioned evaluation metadata. The `main` branch carries this skeleton; automated
histories are appended to **evaluation-results** without bypassing main protection.

```text
results/index.json
results/<project>/index.json
results/<project>/<run-id>/report.json
```

Reports are immutable through the supplied writer. Identical retries are
idempotent; different content with the same identity is rejected. This is not
tamper-proof storage: repository administrators can still change Git history.

Only approved fields are exported. Never add raw prompts, source code, CLI logs,
credentials, private paths, runtime settings or unreviewed model explanations.
Historical imports retain their original timestamp/schema/report hash. Unknown
commit and project-tree identities remain null; imports are not current evaluations.

See [project evaluation](../docs/PROJECT-EVALUATION.md) for commands and activation.
