# Reviewed-result publication without evaluation

This path reads existing public evidence. It never generates candidates, evaluates
tasks, approves a Skill, changes Active, or invokes a model. It is independent of
the evaluation workflow. Publishing files to a feature PR is itself public
disclosure: inspect their actual contents, including decoded Skill captures,
before committing or pushing them.

## Separate identities and authority

- **Code SHA:** the exact trusted, clean publisher checkout. The CLI verifies
  its executing root, HEAD, tracked changes, and all root Python source bytes
  (including validators) against the commit. Actions uses
  the main-only dispatch's `github.sha`, never a data-supplied code revision.
- **Data SHA:** a full commit in this same repository. Only bounded regular Git
  blobs under `publication-candidates/<packet-id>` are read; its code, scripts,
  Git attributes, hooks, and configuration are never checked out or executed.
- **Manifest SHA-256:** pins canonical `manifest.json` bytes and every payload's
  path, size and hash. `producer_commit` preserves the original producer version.
  None of these hashes proves public suitability, human review or Skill approval.
- **Content review:** inspect the exact bytes independently. Actions requires a
  separate `public_content_reviewed=true` acknowledgement for storage/deployment.
  Default `operation=validate` does neither, even if review is acknowledged.

The allowlist is the existing eight report/sidecar filenames, at exact
`results/<project>/<run>/<filename>` paths. Packets exclude indexes, private
directories, requests, source context, operator information, credentials and logs.
Each packet includes its complete transitive graph. Symlinks, executable files,
submodules, unlisted files, noncanonical JSON and hash mismatches are rejected.
Limits are 2,048 payloads, 32 MiB total, 1 MiB per ordinary JSON and 2 MiB per
evolution JSON; merged public results are bounded too. Manifest size is at most
1 MiB. Generated indexes are mutable; historical payload bytes are immutable.
The legacy data branch's root `results/README.md` is retained as repository
metadata, never executed or exported to the public build. It is not allowed
inside an incoming publication packet.

Existing schema/graph validation and `merge_results`, `reindex` and `build` are
reused in disposable staging before local export or a prepared data commit.
The official builder also rejects static local example links unless their exact
project/run and selected Skill exist in the validated graph, before creating
the site. This applies to the legacy deployment workflow too: new example UI
cannot silently build against missing example data.
Validation/immutable conflicts do not change existing results. An IO failure
after validation is reported separately and may leave incomplete local staging;
do not treat that directory as a successful output or retry over it.

## Local commands: no remote writes

Run from a clean checkout containing the publisher, using an existing data SHA.
The bundled candidate data commit is
`0712005982eeeb4f3837c526831222d65b564c62`.

```bash
CODE_SHA="$(git rev-parse HEAD)"
python3 skillops/reviewed_publication.py \
  --code-sha "$CODE_SHA" \
  --data-sha 0712005982eeeb4f3837c526831222d65b564c62 \
  --bundle publication-candidates/hackathon-offline-v1 \
  --manifest-sha 5438e9eb7258feeadc651f1481b5adef0b4d1c8213a15467e17de2ee64a637ec \
  --existing results
```

Default mode validates only and creates no local output. Add
`--mode stage --output /tmp/reviewed-publication` for a new local `results/`,
official static `site/` and `receipt.json`. Output must not overlap existing
results. `--existing-sha <full-sha>` reads the data branch's committed results
without checkout instead of `--existing <directory>`.

Internal `--mode commit` additionally prepares a **local Git object**, requiring
`--existing-sha`. It uses a temporary index and raw blob hashes without filters,
hooks, data checkout or branch updates. It does not push or claim result-branch
storage. There is no remote-write CLI mode.

The five status fields are `local_storage`, `input_validation`,
`result_branch_storage`, `deployment`, and `public_url`. The CLI reports completed,
failed and unrequested stages separately, retaining prior completed stages on
failure. Workflow summaries report each responsible job/step's observed outcome.
`approve --publish-reviewed` and `run-approved --publish-reviewed` only save
local public projections; **neither flag deploys or verifies a public URL**.

## Separately authorized Actions

`publish-reviewed-results.yml` runs only by explicit manual dispatch on main.
Inputs are `data_sha`, `bundle`, `manifest_sha`, `operation` and the content-review
acknowledgement. There is no input URL, external repository or model credential.

| Operation | Authority and ordered effects |
|---|---|
| `validate` (default) | Read-only Git access; validate packet and current data-branch merge |
| `store` | Revalidate current branch, prepare exact bytes, then fast-forward push to `evaluation-results` |
| `deploy` | Store first; after acquiring the existing production deployment lock, re-read the latest durable results, require all packet bytes to be present, build and deploy static assets |

Only the persist job has `contents: write`; only the deployment step receives
`SKILLOPS_SWA_DEPLOYMENT_TOKEN`. No model permissions or evaluation/approval
commands are present. Concurrent non-fast-forward writes fail explicitly; no
force push or retry hides the conflict. A later separately approved dispatch
can revalidate against the newer branch.

Deployment requires the repository variable `SKILLOPS_PUBLIC_URL`, an HTTPS
`*.azurestaticapps.net` root origin. No settings are provisioned by this feature.
After static deployment, GET verification compares the actual HTML, hashed
modules, CSS, sample data, indexes and full public payload bytes with that exact
build. Redirects and oversized/stale content fail. Deployment may succeed while
URL verification fails; those states remain distinct. Custom domains require a
separately reviewed origin policy, not an arbitrary URL override.

## Session 5 candidate handoff

All rows use project `sample_repo`, Skill `path:90ae807bd3d394fc140a6df8`,
mode `offline_test`. They are synthetic integration evidence, not paid-model
measurements; passed confirmation is still ineligible for operating approval.

| Scenario | Cycle run |
|---|---|
| N=2 feedback; final confirmation unverified | `local-20260918T094535Z-b9a681f6cfb2` |
| N=2; confirmation passed | `local-20260918T094533Z-f6297a587afc` |
| N=2; confirmation failed | `local-20260918T094534Z-bcba9c0a589e` |

The packet contains **47 payloads / 206,684 bytes**: the 33 files for the three
primary examples plus all fourteen historical files retained in the original b8
public packet. Its seventeen reports include two prior measured assessments and
an earlier offline cycle; their original provenance and decisions are unchanged.
All files are copied byte-for-byte from the public packet assembled at
`b8dade4c832d1353d67f2a49de7b9202e2bd9e4b`. No approval/use record is included.
All scalar content and all eight unique decoded Skill captures were inspected
before inclusion; no private requests, source context, operator information,
absolute paths, logs or credentials are included. This inspection is not
operational approval.

**Do not confuse the two manifests.** The original
`completion-offline-b8dade4/manifest.json` hash is
`78040e9e5f48d69c9f2026f533724e62c0be47cd7a7f7d9904f35ab93db0ea2f`.
It describes the producer's eleven scenarios and is the session 5 browser-test
provenance input. The publication manifest hash
`5438e9eb7258feeadc651f1481b5adef0b4d1c8213a15467e17de2ee64a637ec`
binds the 47 public payload files only. Neither manifest is modified
to mimic the other. The older efdd68a demo packet is not an input.

Session 5 UI HEAD is `9dd81096a11912c1ff466375179a5ddf02390531` (PR #41).
Its `skillops/tests/dashboard-workflow-production.spec.js` also checks fourteen retained
historical payloads, for 47 bytes-preserved files in total. Six were already in
results commit `5d812c69d5cb4300e9ee2576add629c98a0e2e5d`; eight historical
offline files were absent there and are explicitly included, not regenerated.
Validate the full packet plus the existing data branch when testing deployment.

Use local stage mode above, then
`python3 skillops/project_results.py validate --results /tmp/reviewed-publication/results`.
The `site/` directory comes from the existing official hashed-asset builder.
Serve that directory locally for browser checks and compare each manifest file
with both `results/` and `site/results/`; no payload hash may change.
Do not deploy public example links before the result branch, deployment and
public-byte verification stages have actually succeeded. Until then these are
candidate IDs and local preview routes only.

Review the publisher PR and PR #41 together, but keep their file ownership and
commits separate. Before any authorized UI merge, store the reviewed data first
and verify its complete graph in the durable results branch. Then use the exact
combined main code SHA and reviewed data SHA for the authorized deployment and
public-origin browser verification. The older evaluation workflow still has
main-push triggers and deployment capability: do not assume merging UI code is
publication-free. No merge, workflow dispatch or production configuration change
is authorized by this implementation/verification handoff.
