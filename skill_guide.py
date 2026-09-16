"""Project skill bundle discovery and report-only static assessment."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote

from copilot_runtime import RuntimeFailure


SKILL_ROOTS = (".github/skills", ".claude/skills", "skills")
FILE_LIMIT = 2 * 1024 * 1024
BUNDLE_LIMIT = 8 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".sh"}
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def fail(code, message):
    raise RuntimeFailure(code, message)


def _directory_identity(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        fail("unsafe_skill_path", "Skill path components must be directories.")
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _read_relative(root_fd, relative, *, identities=None):
    """Read bounded bytes through a no-follow chain rooted at a pinned directory."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        fail("unsafe_skill_path", "Skill file paths must stay relative to their root.")
    descriptors = []
    parent_fd = root_fd
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        for index, component in enumerate(relative.parts[:-1], 1):
            descriptor = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent_fd)
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                fail("unsafe_skill_path", "Skill path components must be directories.")
            if identities is not None and identities[Path(*relative.parts[:index])] != (
                info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)
            ):
                fail("unsafe_skill_path", "Skill directories changed after discovery.")
            parent_fd = descriptor
        descriptor = os.open(relative.name, flags, dir_fd=parent_fd)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            fail("unsafe_skill_path", "Skill bundles may contain only ordinary files.")
        if identities is not None and identities[relative] != (
            info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)
        ):
            fail("unsafe_skill_path", "Skill files changed after discovery.")
        if info.st_size > FILE_LIMIT:
            fail("skill_file_limit", "A skill file exceeds the size limit.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(FILE_LIMIT + 1)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def regular_files(folder, candidates, *, project=None, identities=None):
    """Read the entrypoint once, then retained candidates relative to the pinned root."""
    files = []
    total = 0
    root = folder if project is None else project
    entrypoint = (folder / "SKILL.md").relative_to(root)
    root_fd = None
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(root_fd)
        if not stat.S_ISDIR(info.st_mode):
            fail("unsafe_skill_path", "Skill roots must be directories.")
        if identities is not None and identities[Path(".")] != (
            info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)
        ):
            fail("unsafe_skill_path", "Skill roots changed after discovery.")
        for relative in [entrypoint, *sorted(path for path in candidates if path != entrypoint)]:
            path = root / relative
            raw = _read_relative(root_fd, relative, identities=identities)
            size = len(raw)
            if size > FILE_LIMIT:
                fail("skill_file_limit", "A skill file exceeds the size limit.")
            total += size
            if total > BUNDLE_LIMIT:
                fail("skill_bundle_limit", "A skill bundle exceeds the size limit.")
            text = None
            if path.name == "SKILL.md" or path.suffix.lower() in TEXT_SUFFIXES:
                try:
                    text = raw.decode("utf-8")
                except UnicodeError as error:
                    raise RuntimeFailure("invalid_skill_encoding", "Skill text files must be UTF-8.") from error
            files.append({
                "path": path.relative_to(folder).as_posix(),
                "bytes": size,
                "sha256": sha256(raw).hexdigest(),
                "text": text,
            })
    except OSError as error:
        raise RuntimeFailure("unsafe_skill_path", "Cannot safely read a skill file.") from error
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return sorted(files, key=lambda row: Path(row["path"])), total


def discover(project):
    skill_files = []
    file_candidates = {}
    try:
        project = Path(os.path.abspath(project))
        directories = {project: _directory_identity(project)}
        for name in SKILL_ROOTS:
            root = project / name
            relative = Path(name)
            components = [
                project / Path(*relative.parts[:index])
                for index in range(1, len(relative.parts) + 1)
            ]
            for component in components:
                try:
                    directories[component] = _directory_identity(component)
                except FileNotFoundError:
                    continue
            if root not in directories:
                continue
            if any(component not in directories for component in components):
                fail("unsafe_skill_path", "Skill path components changed during discovery.")
            pending = [root]
            while pending:
                directory = pending.pop()
                if _directory_identity(directory) != directories[directory]:
                    fail("unsafe_skill_path", "Skill directories changed during discovery.")
                with os.scandir(directory) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            fail("unsafe_skill_path", "Skill paths cannot be symbolic links.")
                        if entry.name == "SKILL.md":
                            if not stat.S_ISREG(info.st_mode):
                                fail("unsafe_skill_path", "SKILL.md must be an ordinary file.")
                            skill_files.append(path)
                        if stat.S_ISDIR(info.st_mode):
                            directories[path] = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
                            pending.append(path)
                        else:
                            file_candidates[path] = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
        for directory, identity in directories.items():
            if _directory_identity(directory) != identity:
                fail("unsafe_skill_path", "Skill directories changed during discovery.")
    except OSError as error:
        raise RuntimeFailure("unsafe_skill_path", "Cannot safely discover skill files.") from error

    folders = sorted({path.parent for path in skill_files})
    identities = {
        path.relative_to(project): identity
        for path, identity in (directories | file_candidates).items()
    }
    bundles = []
    for folder in folders:
        nested = {path for path in folders if path != folder and folder in path.parents}
        candidates = [
            path.relative_to(project) for path in file_candidates
            if folder in path.parents and not any(root in path.parents for root in nested)
        ]
        files, total = regular_files(folder, candidates, project=project, identities=identities)
        bundles.append({
            "path": folder.relative_to(project).as_posix(),
            "root": str(folder),
            "files": files,
            "file_count": len(files),
            "text_files": sum(row["text"] is not None for row in files),
            "binary_files": sum(row["text"] is None for row in files),
            "bytes": total,
            "sha256": sha256(json.dumps(
                [(row["path"], row["sha256"]) for row in files],
                separators=(",", ":"),
            ).encode()).hexdigest(),
        })
    return sorted(bundles, key=lambda row: row["path"])


def skill_text(bundle):
    return next(row["text"] for row in bundle["files"] if row["path"] == "SKILL.md")


def frontmatter(text):
    """Read only scalar name/description fields and indented continuations, not full YAML."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return {}, text, False
    end = lines.index("---", 1)
    values = {}
    current = None
    for line in lines[1:end]:
        match = re.fullmatch(r"(name|description):\s*(.*)", line)
        if match:
            current = match.group(1)
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
            values[current] = value
        elif current and line.startswith((" ", "\t")):
            values[current] = (values[current] + " " + line.strip()).strip()
        else:
            current = None
    return values, "\n".join(lines[end + 1:]), True


def finding(check, severity, message, path="SKILL.md"):
    return {"check": check, "severity": severity, "path": path, "message": message}


def static_assessment(bundle):
    """Report structure and inline local-link findings using only the discovered bundle."""
    text = skill_text(bundle)
    metadata, body, delimited = frontmatter(text)
    findings = []
    if not delimited:
        findings.append(finding("frontmatter", "error", "YAML frontmatter delimiters are required."))
    for key in ("name", "description"):
        if not metadata.get(key):
            findings.append(finding(f"frontmatter_{key}", "error", f"{key} must be nonempty."))
    if not body.strip():
        findings.append(finding("body", "error", "Skill instructions must be nonempty."))
    if len(text.splitlines()) > 500:
        findings.append(finding(
            "skill_line_count", "warning", "SKILL.md exceeds the 500-line guide recommendation.",
        ))

    paths = {row["path"] for row in bundle["files"]}
    for target in LINK.findall(body):
        target = target.strip()
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        target = unquote(target.split("#", 1)[0])
        if not target:
            continue
        normalized = Path(target)
        if normalized.is_absolute() or ".." in normalized.parts:
            findings.append(finding(
                "unsafe_reference", "error", "Local references must stay inside the skill bundle.",
            ))
        elif normalized.as_posix() not in paths:
            findings.append(finding("missing_reference", "error", f"Referenced file is missing: {target}"))

    resource_files = [row for row in bundle["files"] if row["path"] != "SKILL.md"]
    for row in resource_files:
        if Path(row["path"]).suffix.lower() != ".md" or row["text"] is None:
            continue
        lines = row["text"].splitlines()
        if len(lines) > 300 and not any(
            re.fullmatch(r"## (?:Contents|Table of Contents)\s*", line, re.IGNORECASE)
            for line in lines[:80]
        ):
            findings.append(finding(
                "reference_toc", "warning",
                "Markdown references over 300 lines should include a table of contents.",
                row["path"],
            ))
    applicable = "applicable" if resource_files else "not_applicable"
    return {
        "metadata": {"name": metadata.get("name"), "description": metadata.get("description")},
        "applicability": {
            "progressive_disclosure": applicable,
            "resource_organization": applicable,
        },
        "findings": findings,
    }
