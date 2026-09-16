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
