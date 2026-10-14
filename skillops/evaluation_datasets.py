"""Prepare private, project-pinned datasets without models or project execution."""

import argparse
import ast
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import sys

from copilot_runtime import RuntimeFailure, capture, private_directory, strict_json
import evolution_records as evolution
import project_checks
import project_results as results
import skill_assessments


SPLITS = ("development", "validation", "confirmation")
TASK_FIELDS = "task_id family split request sources checks"


def _hash(value):
    return sha256(results.encoded(value)).hexdigest()


def _git(root, *arguments):
    completed = capture(["git", "-C", str(root), *arguments])
    results.require(completed.returncode == 0, "dataset_git_error")
    return completed.stdout


def _snapshot(root, project_id):
    results.require(results.matches(results.ID, project_id), "invalid_project_id")
    root = results.safe_path(root).resolve()
    results.require(Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve() == root,
                    "dataset_repository_root_required")
    project = results.safe_path(root / "projects" / project_id)
    results.require(project.is_dir(), "missing_project")
    relative = project.relative_to(root).as_posix()
    tracked = set(_git(root, "ls-files", "-z", "--", relative).rstrip("\0").split("\0"))
    actual = {path.relative_to(root).as_posix() for path in project.rglob("*") if path.is_file()}
    results.require(actual and actual == tracked, "dataset_uncommitted_project")
    results.require(not _git(root, "diff", "--name-only", "HEAD", "--", relative).strip(),
                    "dataset_uncommitted_project")
    commit = _git(root, "rev-parse", "HEAD").strip()
    results.require(results.matches(r"[a-f0-9]{40}", commit), "invalid_source_commit")
    return project, commit, results.tree_hash(project)


def _cases(project):
    """Recognize direct unittest cases statically; never import a target's tests."""
    plan = project_checks.discover(project)
    results.require(plan["status"] == "supported" and len(plan["checks"]) == 1
                    and plan["checks"][0]["id"] == "python-tests"
                    and plan["checks"][0]["runner"] == "unittest", "dataset_unsupported_checks")
    start = project / "tests" if (project / "tests").is_dir() else project
    identifiers = set()
    for path in sorted(start.rglob("test*.py")):
        try:
            tree = ast.parse(results.read_bytes(path).decode("utf-8"))
        except (UnicodeError, SyntaxError) as error:
            raise RuntimeFailure("dataset_invalid_tests", "Cannot inspect unittest definitions.") from error
        module = ".".join(path.relative_to(start).with_suffix("").parts)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not any(
                isinstance(base, ast.Name) and base.id == "TestCase"
                or isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                and base.value.id == "unittest" and base.attr == "TestCase"
                for base in node.bases
            ):
                continue
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith("test"):
                    identifier = f"python-tests:{module}.{node.name}.{method.name}"
                    results.require(identifier not in identifiers, "dataset_duplicate_case")
                    identifiers.add(identifier)
    results.require(bool(identifiers), "dataset_missing_cases")
    return plan, identifiers


def _work_item(dataset, task):
    # Validation is a dataset role, not a new replay split or an exported WorkItem.
    work = {"schema_version": 1, **{key: dataset[key] for key in
            ("project_id", "source_commit", "project_tree_sha256")},
            **{key: deepcopy(task[key]) for key in ("task_id", "request", "sources", "checks")},
            "split": "confirmation" if task["split"] == "confirmation" else "development"}
    work["input_sha256"] = _hash(work)
    return work


def validate(root, dataset):
    evolution.exact(dataset, "schema_version dataset_id project_id origin source_commit "
                    "project_tree_sha256 spec_sha256 tasks dataset_sha256")
    results.require(type(dataset["schema_version"]) is int and dataset["schema_version"] == 1,
                    "invalid_dataset")
    results.require(results.matches(results.ID, dataset["dataset_id"])
                    and dataset["origin"] in ("synthetic", "curated"), "invalid_dataset")
    results.require(results.matches(evolution.DIGEST, dataset["spec_sha256"])
                    and dataset["dataset_sha256"] == _hash(
                        {key: value for key, value in dataset.items() if key != "dataset_sha256"}),
                    "dataset_hash_mismatch")
    project, commit, tree = _snapshot(root, dataset["project_id"])
    results.require(dataset["source_commit"] == commit and dataset["project_tree_sha256"] == tree,
                    "dataset_source_mismatch")
    _, available = _cases(project)
    tasks = dataset["tasks"]
    results.require(isinstance(tasks, list) and 3 <= len(tasks) <= 128, "invalid_dataset_tasks")
    seen, requests, splits = set(), set(), set()
    ownership = {"family": {}, "source": {}, "case": {}}
    for task in tasks:
        evolution.exact(task, TASK_FIELDS)
        results.require(isinstance(task["split"], str) and task["split"] in SPLITS
                        and results.matches(results.ID, task["family"]), "invalid_dataset_task")
        split = task["split"]
        work = _work_item(dataset, task)
        skill_assessments.validate_work_item(work, project=project, source_commit=commit)
        results.require(task["task_id"] not in seen, "dataset_duplicate_task")
        seen.add(task["task_id"])
        request = " ".join(task["request"].split()).casefold()
        results.require(request not in requests, "dataset_duplicate_request")
        requests.add(request)
        splits.add(split)
        cases = task["checks"]["required_case_ids"]
        results.require(set(cases) <= available, "dataset_unknown_case")
        for kind, values in (("family", [task["family"]]), ("source", task["sources"]), ("case", cases)):
            for value in values:
                previous = ownership[kind].setdefault(value, split)
                results.require(previous == split, "dataset_split_overlap")
    results.require(splits == set(SPLITS), "dataset_missing_split")
    results.require(len(results.encoded(dataset)) <= results.LIMIT, "dataset_limit")
    return dataset


def _output(root, path):
    private = results.safe_path(Path(root) / ".skillops-private").resolve()
    target = results.safe_path(path).resolve()
    results.require(target.is_relative_to(private) and target != private, "dataset_private_output_required")
    results.require(not target.exists(), "dataset_output_exists")
    return target


def _write(root, path, value):
    target = _output(root, path)
    private = private_directory(root)
    directory = private
    for part in target.parent.relative_to(private).parts:
        directory = results.safe_path(directory / part)
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.stat()
        results.require(directory.is_dir() and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
                        "unsafe_dataset_store")
    results.atomic_json(target, value, immutable=True)


def prepare(root, spec_path, output):
    _output(root, output)
    raw = results.read_bytes(spec_path)
    try:
        spec = strict_json(raw.decode("utf-8"))
    except UnicodeError as error:
        raise RuntimeFailure("invalid_encoding", "Dataset recipe must be UTF-8 JSON.") from error
    evolution.exact(spec, "schema_version dataset_id project_id origin tasks")
    results.require(type(spec["schema_version"]) is int and spec["schema_version"] == 1,
                    "invalid_dataset")
    project, commit, tree = _snapshot(root, spec["project_id"])
    plan, _ = _cases(project)
    results.require(isinstance(spec["tasks"], list) and 3 <= len(spec["tasks"]) <= 128, "invalid_dataset_tasks")
    dataset = {key: deepcopy(spec[key]) for key in ("schema_version", "dataset_id", "project_id", "origin")}
    dataset.update(source_commit=commit, project_tree_sha256=tree, spec_sha256=sha256(raw).hexdigest(), tasks=[])
    protected = project_checks.protected_digest(project, project_checks.protected_files(project))
    for task in spec["tasks"]:
        evolution.exact(task, "task_id family split request sources required_case_ids")
        sources = task["sources"]
        results.require(isinstance(sources, list) and 1 <= len(sources) <= 16, "invalid_dataset_sources")
        files = {}
        for name in sources:
            evolution.relative_path(name)
            results.require(name not in files, "invalid_dataset_sources")
            files[name] = sha256(results.read_bytes(project / name, 65536)).hexdigest()
        row = {key: deepcopy(task[key]) for key in ("task_id", "family", "split", "request")}
        row.update(sources=files, checks={
            "plan_sha256": plan["sha256"], "protected_sha256": protected,
            "required_case_ids": deepcopy(task["required_case_ids"]),
            "required_gate_ids": sorted(gate["id"] for gate in plan["gates"]),
        })
        dataset["tasks"].append(row)
    dataset["dataset_sha256"] = _hash(dataset)
    validate(root, dataset)
    _write(root, output, dataset)
    return dataset


def export_development(root, dataset_path, task_id, output):
    dataset = validate(root, results.read_json(dataset_path))
    selected = [task for task in dataset["tasks"] if task["task_id"] == task_id]
    results.require(len(selected) == 1 and selected[0]["split"] == "development",
                    "dataset_development_only")
    work = _work_item(dataset, selected[0])
    _write(root, output, work)
    return work


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="Pin one project's recipe; no model or test execution.")
    prepare_parser.add_argument("--spec", required=True)
    prepare_parser.add_argument("--output", required=True)
    validate_parser = commands.add_parser("validate", help="Validate the pinned inputs and structural split boundaries.")
    validate_parser.add_argument("--dataset", required=True)
    export_parser = commands.add_parser("export-development", help="Export one development task for replay/iterate.")
    export_parser.add_argument("--dataset", required=True)
    export_parser.add_argument("--task", required=True)
    export_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        root = results.safe_path(args.root)
        if args.command == "prepare":
            dataset = prepare(root, root / args.spec, root / args.output)
        else:
            dataset = validate(root, results.read_json(root / args.dataset))
            if args.command == "export-development":
                export_development(root, root / args.dataset, args.task, root / args.output)
        print(results.encoded({
            "status": "prepared" if args.command == "prepare" else
                      "exported" if args.command == "export-development" else "validated",
            "project_id": dataset["project_id"], "dataset_id": dataset["dataset_id"],
            "dataset_sha256": dataset["dataset_sha256"], "origin": dataset["origin"],
            "splits": {split: sum(task["split"] == split for task in dataset["tasks"]) for split in SPLITS},
            "model_calls": 0, "confirmation_status": "not_run", "approval_eligible": False,
        }).decode(), end="")
        return 0
    except (RuntimeFailure, OSError) as error:
        code = error.code if isinstance(error, RuntimeFailure) else "io_error"
        print(f"Dataset preparation failed: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
