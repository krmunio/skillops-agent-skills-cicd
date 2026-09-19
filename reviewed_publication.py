"""Publishable-data preparation only: no evaluation, approval, or remote writes."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import project_results as results
from copilot_runtime import RuntimeFailure, strict_json


MAX_FILES = 2048
MAX_TOTAL_BYTES = 32 * results.LIMIT
PAYLOADS = (
    "report.json", "skill-snapshots.json", "skill-evolution.json", "skill-assessments.json",
    "stage-metrics.json", "replay-evaluation.json", "cycle.json", "adoption.json",
)
STAGES = ("local_storage", "input_validation", "result_branch_storage", "deployment", "public_url")


def git(root, *args, data=None, env=None, limit=4 * results.LIMIT):
    with tempfile.TemporaryFile() as out:
        process = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
             "-c", "core.fsmonitor=false", "-C", str(root), *args],
            input=data, stdout=out, stderr=subprocess.PIPE, timeout=30,
            env={**os.environ, **(env or {})})
        results.require(process.returncode == 0, "publication_git_failed")
        results.require(out.tell() <= limit, "publication_size_limit")
        out.seek(0)
        return out.read()


def commit_id(value):
    results.require(results.matches(r"[a-f0-9]{40}", value), "invalid_commit")
    return value


def payload_path(path, *, indexes=False):
    parts = path.split("/")
    if indexes and (parts == ["index.json"] or (
            len(parts) == 2 and results.matches(results.ID, parts[0]) and parts[1] == "index.json")):
        return True
    return (len(parts) == 3 and results.matches(results.ID, parts[0])
            and results.matches(results.RUN, parts[1]) and parts[2] in PAYLOADS)


def file_limit(path):
    return results.EVOLUTION_LIMIT if path.endswith("/skill-evolution.json") else results.LIMIT


def tree(root, revision, prefix):
    commit_id(revision)
    results.require(git(root, "cat-file", "-t", revision).strip() == b"commit", "invalid_commit")
    raw = git(root, "ls-tree", "-rlz", revision, "--", prefix + "/")
    entries, total = {}, 0
    for item in raw.split(b"\0"):
        if not item:
            continue
        metadata, name = item.split(b"\t", 1)
        mode, kind, oid, size = metadata.decode("ascii").split()
        path = name.decode("utf-8")
        results.require(mode == "100644" and kind == "blob" and path.startswith(prefix + "/"),
                        "non_public_entry")
        path = path[len(prefix) + 1:]
        size = int(size)
        total += size
        results.require(size <= file_limit(path) and total <= MAX_TOTAL_BYTES
                        and len(entries) < MAX_FILES + 1, "publication_size_limit")
        entries[path] = (oid, size)
    return entries


def canonical(raw):
    value = strict_json(raw.decode("utf-8"))
    results.require(raw == results.encoded(value), "noncanonical_publication")
    return value


def packet(root, data_sha, bundle, manifest_sha, destination):
    results.require(results.matches(r"publication-candidates/[a-z0-9][a-z0-9_-]{0,63}", bundle),
                    "invalid_bundle")
    results.require(results.matches(r"[a-f0-9]{64}", manifest_sha), "invalid_manifest_hash")
    entries = tree(root, data_sha, bundle)
    results.require("manifest.json" in entries, "missing_manifest")
    manifest_raw = git(root, "cat-file", "blob", entries["manifest.json"][0], limit=results.LIMIT)
    results.require(sha256(manifest_raw).hexdigest() == manifest_sha, "manifest_hash_mismatch")
    manifest = canonical(manifest_raw)
    results.require(isinstance(manifest, dict)
                    and set(manifest) == {"schema_version", "producer_commit", "files"}
                    and type(manifest["schema_version"]) is int and manifest["schema_version"] == 1,
                    "invalid_manifest")
    commit_id(manifest["producer_commit"])
    files = manifest["files"]
    results.require(isinstance(files, list) and 0 < len(files) <= MAX_FILES, "invalid_manifest")
    paths = []
    for record in files:
        results.require(isinstance(record, dict) and set(record) == {"path", "size", "sha256"},
                        "invalid_manifest")
        path = record["path"]
        results.require(isinstance(path, str) and path.startswith("results/")
                        and payload_path(path[8:]), "non_public_entry")
        results.require(type(record["size"]) is int and 0 < record["size"] <= file_limit(path)
                        and results.matches(r"[a-f0-9]{64}", record["sha256"]), "invalid_manifest")
        paths.append(path)
    results.require(paths == sorted(set(paths)) and set(entries) == {"manifest.json", *paths},
                    "manifest_inventory_mismatch")
    for record in files:
        oid, size = entries[record["path"]]
        results.require(size == record["size"], "payload_size_mismatch")
        raw = git(root, "cat-file", "blob", oid, limit=file_limit(record["path"]))
        results.require(sha256(raw).hexdigest() == record["sha256"], "payload_hash_mismatch")
        canonical(raw)
        path = destination / record["path"][8:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return manifest


def inventory(directory, *, indexes=False):
    directory = results.safe_path(directory)
    results.require(not directory.exists() or directory.is_dir(), "invalid_existing_results")
    files, total = {}, 0
    for path in sorted(directory.rglob("*")):
        results.safe_path(path)
        if path.is_dir():
            continue
        name = path.relative_to(directory).as_posix()
        if indexes and name == "README.md":
            continue
        results.require(path.is_file() and payload_path(name, indexes=indexes), "non_public_entry")
        raw = results.read_bytes(path, file_limit(name))
        total += len(raw)
        results.require(len(files) < MAX_FILES and total <= MAX_TOTAL_BYTES, "publication_size_limit")
        canonical(raw)
        files[name] = raw
    return files


def existing_tree(root, revision, destination):
    for path, (oid, _) in tree(root, revision, "results").items():
        if path == "README.md":
            continue
        results.require(payload_path(path, indexes=True), "non_public_entry")
        raw = git(root, "cat-file", "blob", oid, limit=file_limit(path))
        canonical(raw)
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def prepare(root, *, data_sha, code_sha, bundle, manifest_sha, existing=None,
            existing_sha=None, output=None, stage=False, require_stored=False, progress=None):
    progress = dict.fromkeys(STAGES, "not_requested") if progress is None else progress
    progress["input_validation"] = "in_progress"
    root = results.safe_path(root)
    commit_id(code_sha)
    results.require(git(root, "rev-parse", "HEAD").decode().strip() == code_sha, "publisher_commit_mismatch")
    for source in sorted(Path(__file__).resolve().parent.glob("*.py")):
        results.require(git(root, "cat-file", "blob", code_sha + ":" + source.name)
                        == results.read_bytes(source), "publisher_commit_mismatch")
    git(root, "diff", "--quiet", "--no-ext-diff", "--no-textconv", code_sha, "--")
    results.require(not (existing is not None and existing_sha is not None), "ambiguous_existing_results")
    results.require(not stage or output is not None, "output_required")
    if output is not None:
        output = results.safe_path(output)
        results.require(not output.exists(), "output_exists")
        if existing is not None:
            previous = results.safe_path(existing)
            results.require(not output.is_relative_to(previous) and not previous.is_relative_to(output),
                            "overlapping_results")
    value = {"schema_version": 1, "code_sha": code_sha, "data_sha": data_sha,
             "bundle": bundle, "manifest_sha256": manifest_sha, "model_calls": 0,
             "content_review_proven_by_manifest": False,
             "stages": progress}
    with tempfile.TemporaryDirectory(prefix="skillops-publication-") as temporary:
        work = Path(temporary)
        incoming, prior, merged = work / "incoming", work / "prior", work / "prepared/results"
        manifest = packet(root, data_sha, bundle, manifest_sha, incoming)
        if existing_sha is not None:
            existing_tree(root, existing_sha, prior)
            existing = prior
        existing_files = inventory(existing, indexes=True) if existing is not None else {}
        if require_stored:
            for item in manifest["files"]:
                raw = existing_files.get(item["path"][8:])
                results.require(raw is not None and sha256(raw).hexdigest() == item["sha256"],
                                "result_not_stored")
        # Both graphs and all immutable collisions are checked before publishing
        # anything outside this disposable workspace.
        results.merge_results(root, existing or prior, merged)
        results.merge_results(root, incoming, merged)
        inventory(merged, indexes=True)
        for name, raw in existing_files.items():
            if name.endswith("/index.json") or name == "index.json":
                continue
            results.require(results.read_bytes(merged / name, file_limit(name)) == raw, "historical_bytes_changed")
        for item in manifest["files"]:
            results.require(sha256(results.read_bytes(
                work / "prepared" / item["path"], file_limit(item["path"]))).hexdigest() == item["sha256"],
                "payload_bytes_changed")
        value["stages"]["input_validation"] = "complete"
        value["files"] = len(manifest["files"])
        value["producer_commit"] = manifest["producer_commit"]
        if stage:
            progress["local_storage"] = "in_progress"
            results.build(root, merged, work / "prepared/site")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(work / "prepared", output)
            results.atomic_json(output / "receipt.json",
                                {**value, "stages": {**progress, "local_storage": "complete"}})
            progress["local_storage"] = "complete"
    return value


def commit_results(root, directory, parent_sha):
    """Create a local data commit without checkout, filters, hooks or remote writes."""
    commit_id(parent_sha)
    files = inventory(directory, indexes=True)
    with tempfile.TemporaryDirectory(prefix="skillops-publication-index-") as folder:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        git(root, "read-tree", parent_sha, env=env)
        updates = []
        for name, raw in files.items():
            oid = git(root, "hash-object", "-w", "--stdin", "--no-filters", data=raw).strip()
            updates.append(b"100644 " + oid + b"\tresults/" + name.encode("utf-8") + b"\0")
        git(root, "update-index", "-z", "--index-info", data=b"".join(updates), env=env)
        tree_id = git(root, "write-tree", env=env).decode().strip()
        if tree_id == git(root, "rev-parse", parent_sha + "^{tree}").decode().strip():
            return parent_sha
        return git(root, "-c", "user.name=github-actions[bot]", "-c",
                   "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                   "commit-tree", tree_id, "-p", parent_sha,
                   "-m", "data: append reviewed public results").decode().strip()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeFailure("public_url_redirect", "Public verification must stay on the configured origin.")


def fetch_public(url, limit):
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        results.require(response.status == 200, "public_url_unavailable")
        raw = response.read(limit + 1)
        results.require(len(raw) <= limit, "public_url_size_limit")
        return raw


def public_origin(url):
    parsed = urlsplit(url)
    results.require(parsed.scheme == "https" and parsed.username is None and parsed.password is None
                    and parsed.port is None and parsed.path in ("", "/")
                    and not parsed.query and not parsed.fragment
                    and results.matches(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.azurestaticapps\.net", parsed.hostname),
                    "invalid_public_url")
    return url.rstrip("/")


def verify_url(site, url):
    url = public_origin(url)
    site = results.safe_path(site)
    files = {f"results/{name}": raw for name, raw in inventory(site / "results", indexes=True).items()}
    for path in sorted(site.iterdir()):
        results.safe_path(path)
        if path.name == "staticwebapp.config.json" or path.name == "results":
            continue
        results.require(path.name in ("index.html", "styles.css", "sample-data.json") or results.matches(
            r"(?:views|evolution|assessments|trace|app)\.[a-f0-9]{12}\.js", path.name), "non_public_entry")
        files[path.name] = results.read_bytes(path)
    results.require("index.html" in files and "results/index.json" in files, "incomplete_public_site")
    for name, raw in files.items():
        digest = sha256(raw).hexdigest()
        observed = fetch_public(url.rstrip("/") + "/" + name + "?publication=" + digest, file_limit(name))
        results.require(sha256(observed).hexdigest() == digest, "public_url_hash_mismatch")
    return {"stages": {**dict.fromkeys(STAGES, "not_requested"), "public_url": "complete"},
            "verified_files": len(files), "public_url": url.rstrip("/"), "model_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--mode", choices=("validate", "stage", "commit", "verify-url"), default="validate")
    for name in ("data-sha", "code-sha", "bundle", "manifest-sha", "existing-sha", "public-url"):
        parser.add_argument("--" + name)
    for name in ("existing", "output", "site"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--require-stored", action="store_true")
    args = parser.parse_args(argv)
    progress = dict.fromkeys(STAGES, "not_requested")
    try:
        if args.mode == "verify-url":
            progress["public_url"] = "in_progress"
            results.require(args.site is not None and args.public_url is not None, "public_url_required")
            value = verify_url(args.site, args.public_url)
        else:
            progress["input_validation"] = "in_progress"
            results.require(args.root.resolve() == Path(__file__).resolve().parent, "publisher_root_mismatch")
            if args.public_url is not None:
                public_origin(args.public_url)
            results.require(args.mode != "commit" or args.existing_sha is not None, "existing_commit_required")
            value = prepare(args.root, data_sha=args.data_sha, code_sha=args.code_sha, bundle=args.bundle,
                            manifest_sha=args.manifest_sha, existing=args.existing, existing_sha=args.existing_sha,
                            output=args.output, stage=args.mode != "validate", require_stored=args.require_stored,
                            progress=progress)
            if args.mode == "commit":
                value["prepared_commit"] = commit_results(args.root, args.output / "results", args.existing_sha)
                value["parent_commit"] = args.existing_sha
        print(json.dumps(value, indent=2))
        return 0
    except (RuntimeFailure, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "publication_io_or_contract"
        print(json.dumps({"status": "blocked", "code": code,
                          "stages": {name: "failed" if state == "in_progress" else state
                                     for name, state in progress.items()},
                          "model_calls": 0}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
