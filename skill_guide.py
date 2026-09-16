"""Project skill bundle discovery."""

from hashlib import sha256
import json
from pathlib import Path
import stat

from copilot_runtime import RuntimeFailure


SKILL_ROOTS = (".github/skills", ".claude/skills", "skills")
FILE_LIMIT = 2 * 1024 * 1024
BUNDLE_LIMIT = 8 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".sh"}


def fail(code, message):
    raise RuntimeFailure(code, message)


def regular_files(folder, skill_folders):
    files = []
    total = 0
    nested = {path for path in skill_folders if path != folder and folder in path.parents}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            fail("unsafe_skill_path", "Skill bundles cannot contain symbolic links.")
        if any(root == path or root in path.parents for root in nested):
            continue
        info = path.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            fail("unsafe_skill_path", "Skill bundles may contain only ordinary files.")
        if info.st_size > FILE_LIMIT:
            fail("skill_file_limit", "A skill file exceeds the size limit.")
        raw = path.read_bytes()
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
        files, total = regular_files(folder, folders)
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
