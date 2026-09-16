"""Project skill bundle discovery."""

from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from copilot_runtime import RuntimeFailure


SKILL_ROOTS = (".github/skills", ".claude/skills", "skills")
FILE_LIMIT = 2 * 1024 * 1024
BUNDLE_LIMIT = 8 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".sh"}


def fail(code, message):
    raise RuntimeFailure(code, message)


def _read_relative(root_fd, relative):
    """Read bounded bytes through a no-follow chain rooted at a pinned directory."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        fail("unsafe_skill_path", "Skill file paths must stay relative to their root.")
    descriptors = []
    parent_fd = root_fd
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        for component in relative.parts[:-1]:
            descriptor = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent_fd)
            descriptors.append(descriptor)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                fail("unsafe_skill_path", "Skill path components must be directories.")
            parent_fd = descriptor
        descriptor = os.open(relative.name, flags, dir_fd=parent_fd)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            fail("unsafe_skill_path", "Skill bundles may contain only ordinary files.")
        if info.st_size > FILE_LIMIT:
            fail("skill_file_limit", "A skill file exceeds the size limit.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(FILE_LIMIT + 1)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def regular_files(folder, skill_folders, *, project=None):
    """Enumerate candidates, then validate their descriptor chains from one root."""
    files = []
    total = 0
    nested = {path for path in skill_folders if path != folder and folder in path.parents}
    root = folder if project is None else project
    root_fd = None
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            fail("unsafe_skill_path", "Skill roots must be directories.")
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                fail("unsafe_skill_path", "Skill bundles cannot contain symbolic links.")
            if any(root == path or root in path.parents for root in nested):
                continue
            if stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode):
                continue
            raw = _read_relative(root_fd, path.relative_to(root))
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
    return files, total


def discover(project):
    project = Path(project).resolve(strict=True)
    skill_files = []
    for name in SKILL_ROOTS:
        root = project / name
        relative = Path(name)
        if any((project / Path(*relative.parts[:index])).is_symlink()
               for index in range(1, len(relative.parts) + 1)):
            fail("unsafe_skill_path", "Skill roots cannot be symbolic links.")
        if not root.is_dir():
            continue
        for path in root.rglob("SKILL.md"):
            if path.is_symlink():
                fail("unsafe_skill_path", "SKILL.md cannot be a symbolic link.")
            if not path.is_file():
                fail("unsafe_skill_path", "SKILL.md must be an ordinary file.")
            skill_files.append(path)

    folders = sorted({path.parent for path in skill_files})
    bundles = []
    for folder in folders:
        files, total = regular_files(folder, folders, project=project)
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
