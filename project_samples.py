"""Prepare pinned, inert project snapshots and explicitly selected skill drafts."""

import argparse
from functools import wraps
import gzip
from hashlib import sha256
import http.client
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import urllib.error
import urllib.request
import zlib


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_FILES = 10000
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_PROJECTS = 50
MAX_PAX_BYTES = 4096
MAX_MEMBERS = 20000
MAX_TAR_BYTES = MAX_TOTAL_BYTES + MAX_MEMBERS * 1024
MAX_PATH_BYTES = 1024
MAX_DEPTH = 64
SOURCE = ".skillops-source.json"
BOOTSTRAP = ".skillops-bootstrap.json"
METADATA = frozenset((SOURCE, BOOTSTRAP))
SKILL_ROOTS = (".github/skills", ".claude/skills", "skills")
EXPORT_POLICY = "ordinary_files_only"
NORMALIZATION = "crlf_to_lf"
PAX_KEYS = frozenset((b"path", b"linkpath", b"comment", b"mtime", b"atime", b"ctime", b"uid", b"gid",
                      b"uname", b"gname", b"size", b"charset", b"hdrcharset"))
IDENTIFIER = r"[a-z0-9][a-z0-9_-]{0,63}"
SKILL_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
HASH = r"[0-9a-f]{64}"
DEVICES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$", "clock$"}
                    | {prefix + suffix for prefix in ("com", "lpt") for suffix in "123456789¹²³"})
ERROR_CODES = frozenset((
    "sample_error", "invalid_arguments", "invalid_project", "invalid_repository", "invalid_commit",
    "unsafe_path", "path_collision", "reserved_path", "missing_input", "invalid_archive", "archive_limit",
    "file_limit", "project_limit", "unsupported_member", "download_failed", "destination_exists",
    "missing_skill", "invalid_template", "missing_manifest", "invalid_manifest", "source_mismatch",
    "bootstrap_mismatch", "io_error",
))


class SampleError(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in ERROR_CODES else "sample_error"
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise SampleError(code)


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _io_boundary(function):
    @wraps(function)
    def guarded(*arguments, **options):
        try:
            return function(*arguments, **options)
        except OSError:
            raise SampleError("io_error") from None
    return guarded


def _identifier(value, code):
    _require(_matches(IDENTIFIER, value) and value not in DEVICES, code)
    return value


def _identity(repository, commit):
    _require(isinstance(repository, str) and len(repository) <= 129, "invalid_repository")
    parts = repository.split("/")
    _require(len(parts) == 2, "invalid_repository")
    for component in parts:
        _identifier(component, "invalid_repository")
    _require(_matches(r"[0-9a-fA-F]{40}", commit), "invalid_commit")
    return repository, commit.lower()


def _component(value):
    _require(value not in ("", ".", "..") and not value.endswith((".", " ")), "unsafe_path")
    _require(not any(character in '<>:"\\|?*' or ord(character) < 32 or ord(character) == 127
                     for character in value), "unsafe_path")
    try:
        _require(len(value.encode("utf-8")) <= 255, "unsafe_path")
    except UnicodeError:
        raise SampleError("unsafe_path") from None
    _require(value.split(".")[0].rstrip(" .").casefold() not in DEVICES, "unsafe_path")


def _relative(value, metadata=False):
    _require(isinstance(value, str), "unsafe_path")
    try:
        _require(0 < len(value.encode("utf-8")) <= MAX_PATH_BYTES, "unsafe_path")
    except UnicodeError:
        raise SampleError("unsafe_path") from None
    parts = value.split("/")
    _require(len(parts) <= MAX_DEPTH, "unsafe_path")
    for component in parts:
        _component(component)
        _require(component.casefold() != ".git", "unsafe_path")
    if parts[0].casefold() in METADATA:
        _require(metadata and len(parts) == 1 and value in METADATA, "reserved_path")
    return parts


class _Paths:
    def __init__(self):
        self.nodes = {}
        self.explicit = set()

    def add(self, value, kind, metadata=False):
        parts = _relative(value, metadata)
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            folded = path.casefold()
            expected = kind if depth == len(parts) else "directory"
            previous = self.nodes.get(folded)
            _require(previous is None or previous == (path, expected), "path_collision")
            if depth == len(parts):
                _require(folded not in self.explicit, "path_collision")
                self.explicit.add(folded)
            self.nodes[folded] = (path, expected)
            _require(len(self.nodes) <= MAX_MEMBERS, "archive_limit")


def _ordinary(info):
    return not (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _same_file(first, second):
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _checked_path(value):
    try:
        path = Path(value)
    except (TypeError, ValueError):
        raise SampleError("unsafe_path") from None
    _require(".." not in path.parts and not (path.drive and not path.root), "unsafe_path")
    path = Path(os.path.abspath(path))
    for component in path.parts[1:]:
        _component(component)
    for ancestor in reversed((path, *path.parents)):
        try:
            info = os.lstat(ancestor)
        except FileNotFoundError:
            continue
        _require(_ordinary(info), "unsafe_path")
        if ancestor != path:
            _require(stat.S_ISDIR(info.st_mode), "unsafe_path")
    return path


def _directory(value):
    path = _checked_path(value)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise SampleError("missing_input") from None
    _require(stat.S_ISDIR(info.st_mode), "unsafe_path")
    return path


def _read_file(value):
    path = _checked_path(value)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise SampleError("missing_input") from None
    _require(stat.S_ISREG(before.st_mode), "unsafe_path")
    _require(0 <= before.st_size <= MAX_FILE_BYTES, "file_limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        current = os.fstat(stream.fileno())
        _require(_ordinary(current) and stat.S_ISREG(current.st_mode) and _same_file(before, current), "unsafe_path")
        _checked_path(path)
        content = stream.read(MAX_FILE_BYTES + 1)
    _require(len(content) <= MAX_FILE_BYTES, "file_limit")
    _checked_path(path)
    _require(_same_file(before, os.lstat(path)) and len(content) == before.st_size, "io_error")
    return content


def _budget(sizes):
    _require(len(sizes) <= MAX_FILES, "project_limit")
    _require(all(0 <= size <= MAX_FILE_BYTES for size in sizes), "file_limit")
    _require(sum(sizes) <= MAX_TOTAL_BYTES, "project_limit")


def _catalog_capacity(projects):
    projects = _checked_path(projects)
    if not projects.exists():
        return
    count = 0
    with os.scandir(_directory(projects)) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if entry.name == "README.md" and _ordinary(info) and stat.S_ISREG(info.st_mode):
                continue
            count += 1
            _require(count < MAX_PROJECTS, "project_limit")


def _scan(project):
    project = _directory(project)
    files = {}
    sizes = []
    directories = set()
    paths = _Paths()
    pending = [project]
    while pending:
        directory = _directory(pending.pop())
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = Path(entry.path).relative_to(project).as_posix()
                info = entry.stat(follow_symlinks=False)
                _require(_ordinary(info), "unsafe_path")
                if stat.S_ISDIR(info.st_mode):
                    _require(relative not in METADATA, "invalid_manifest")
                    paths.add(relative, "directory", metadata=True)
                    directories.add(relative)
                    pending.append(Path(entry.path))
                else:
                    _require(stat.S_ISREG(info.st_mode), "unsafe_path")
                    paths.add(relative, "file", metadata=True)
                    sizes.append(info.st_size)
                    _budget(sizes)
                    content = _read_file(entry.path)
                    files[relative] = sha256(content).hexdigest()
    return files, sizes, directories


def _existing_skill(files):
    for root in SKILL_ROOTS:
        matches = sorted(path for path in files if path.startswith(root + "/") and path.endswith("/SKILL.md"))
        if matches:
            return matches[0]
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise SampleError("download_failed")


def fetch_archive(repository, commit):
    repository, commit = _identity(repository, commit)
    address = f"https://codeload.github.com/{repository}/tar.gz/{commit}"
    request = urllib.request.Request(address, headers={"Accept": "application/gzip", "User-Agent": "skillops-samples/1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            _require(response.status == 200 and response.geturl() == address, "download_failed")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                _require(_matches(r"[0-9]{1,20}", declared), "download_failed")
                _require(int(declared) <= MAX_ARCHIVE_BYTES, "archive_limit")
            content = response.read(MAX_ARCHIVE_BYTES + 1)
            _require(len(content) <= MAX_ARCHIVE_BYTES, "archive_limit")
            return content
    except (OSError, ValueError, http.client.HTTPException):
        raise SampleError("download_failed") from None


def _preflight_pax(content, global_header):
    allowed = frozenset((b"comment",)) if global_header else PAX_KEYS
    seen = set()
    offset = 0
    while offset < len(content):
        separator = content.find(b" ", offset)
        _require(separator > offset, "invalid_archive")
        length = content[offset:separator]
        _require(length.isdigit() and len(length) <= len(str(MAX_PAX_BYTES)), "invalid_archive")
        end = offset + int(length)
        _require(separator + 1 < end <= len(content) and content[end - 1:end] == b"\n", "invalid_archive")
        key, delimiter, value = content[separator + 1:end - 1].partition(b"=")
        _require(delimiter and key in allowed and key not in seen, "invalid_archive")
        seen.add(key)
        offset = end


def _preflight_tar(content):
    offset = 0
    count = 0
    extensions = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
    ordinary = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE)
    while offset < len(content):
        header = content[offset:offset + tarfile.BLOCKSIZE]
        _require(len(header) == tarfile.BLOCKSIZE, "invalid_archive")
        if not any(header):
            _require(len(content) - offset >= 2 * tarfile.BLOCKSIZE
                     and not any(memoryview(content)[offset:]), "invalid_archive")
            return
        member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        count += 1
        _require(count <= MAX_MEMBERS, "archive_limit")
        _require(member.type in ordinary + extensions, "unsupported_member")
        _require(member.size >= 0, "invalid_archive")
        _require(member.size <= MAX_FILE_BYTES, "file_limit")
        if member.type in (tarfile.XHDTYPE, tarfile.XGLTYPE):
            _require(member.size <= MAX_PAX_BYTES, "archive_limit")
            payload = offset + tarfile.BLOCKSIZE
            _preflight_pax(content[payload:payload + member.size], member.type == tarfile.XGLTYPE)
        offset += tarfile.BLOCKSIZE + ((member.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
        _require(offset <= len(content), "invalid_archive")
    raise SampleError("invalid_archive")


def _unpack(content, repository, commit):
    _require(isinstance(content, bytes), "invalid_archive")
    _require(len(content) <= MAX_ARCHIVE_BYTES, "archive_limit")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
            expanded = compressed.read(MAX_TAR_BYTES + 1)
        _require(len(expanded) <= MAX_TAR_BYTES, "archive_limit")
        _preflight_tar(expanded)
        prefix = repository.split("/")[1] + "-" + commit
        paths = _Paths()
        omissions = []
        members = []
        root_seen = False
        sizes = []
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for count, member in enumerate(archive, start=1):
                _require(count <= MAX_MEMBERS, "archive_limit")
                _require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE)
                         and member.sparse is None, "unsupported_member")
                _require(member.size >= 0, "invalid_archive")
                _require(member.size <= MAX_FILE_BYTES, "file_limit")
                name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
                _relative(name)
                if name == prefix:
                    _require(member.isdir() and member.size == 0 and not root_seen, "invalid_archive")
                    root_seen = True
                    continue
                _require(name.startswith(prefix + "/"), "invalid_archive")
                relative = name[len(prefix) + 1:]
                kind = "directory" if member.isdir() else "symlink" if member.issym() else "file"
                paths.add(relative, kind)
                if kind == "file":
                    sizes.append(member.size)
                    _budget(sizes)
                    members.append((relative, member))
                else:
                    _require(member.size == 0, "invalid_archive")
                    if kind == "symlink":
                        omissions.append({"path": relative, "reason": "symlink"})
            _require(not any(expanded[archive.offset:]), "invalid_archive")
            files = {}
            for relative, member in members:
                with archive.extractfile(member) as stream:
                    payload = stream.read(MAX_FILE_BYTES + 1)
                _require(len(payload) == member.size, "invalid_archive")
                files[relative] = payload
        return files, sorted(omissions, key=lambda omission: omission["path"]), paths
    except (tarfile.TarError, OSError, EOFError, ValueError, zlib.error, RecursionError):
        raise SampleError("invalid_archive") from None


def _scalar(value):
    value = value.strip()
    _require(bool(value), "invalid_template")
    if value.startswith('"'):
        try:
            parsed, end = json.JSONDecoder().raw_decode(value)
        except ValueError:
            raise SampleError("invalid_template") from None
        _require(not value[end:].strip() or value[end:].lstrip().startswith("#"), "invalid_template")
        return parsed
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
        _require(match is not None, "invalid_template")
        return match.group(1).replace("''", "'")
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    _require(bool(value) and value[0] not in "[]{}&*!|>@`%#", "invalid_template")
    _require(not re.search(r":(?:\s|$)|^[-?](?:\s|$)", value), "invalid_template")
    _require(value.casefold() not in ("null", "true", "false", "~", ".nan", ".inf", "-.inf", "+.inf"),
             "invalid_template")
    _require(not _matches(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", value),
             "invalid_template")
    return value


def _template_name(content):
    """Validate required scalar metadata, including quoted scalars and description blocks."""
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise SampleError("invalid_template") from None
    lines = text.split("\n")
    _require(lines[0] == "---", "invalid_template")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise SampleError("invalid_template") from None
    _require(len("\n".join(lines[:end]).encode("utf-8")) <= 16384, "invalid_template")
    fields = {}
    position = 1
    while position < end:
        line = lines[position]
        position += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([a-z][a-z0-9_-]*):(?:[ \t]+(.*))?", line)
        _require(match is not None, "invalid_template")
        key, value = match.group(1), (match.group(2) or "").strip()
        _require(key not in fields, "invalid_template")
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            _require(key == "description", "invalid_template")
            block = []
            indentation = None
            while position < end and (lines[position].startswith(" ") or not lines[position].strip()):
                continuation = lines[position]
                position += 1
                if continuation.strip():
                    current = len(continuation) - len(continuation.lstrip(" "))
                    if indentation is None:
                        indentation = current
                    _require(current >= indentation, "invalid_template")
                    block.append(continuation[indentation:])
                else:
                    block.append("")
            fields[key] = ("\n" if value.startswith("|") else " ").join(block).strip()
        else:
            fields[key] = _scalar(value)
    name = fields.get("name")
    description = fields.get("description")
    _require(_matches(SKILL_NAME, name) and len(name) <= 64 and name not in DEVICES, "invalid_template")
    _require(isinstance(description, str) and 0 < len(description.strip()) <= 1024, "invalid_template")
    _require(not any(ord(character) < 32 and character not in "\n\t" for character in description),
             "invalid_template")
    return name


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "invalid_manifest")
        result[key] = value
    return result


def _invalid_constant(value):
    raise SampleError("invalid_manifest")


def _json_object(content):
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise SampleError("invalid_manifest") from None
    _require(isinstance(value, dict), "invalid_manifest")
    return value


def _license_files(files):
    return sorted(path for path in files
                  if _matches(r"(?:licen[cs]e|copying|unlicense)(?:[._-].+)?", path.rsplit("/", 1)[-1].casefold()))


def _bootstrap_files(skill, source_content=None):
    _require(skill is not None, "missing_skill")
    content = _read_file(skill).replace(b"\r\n", b"\n")
    name = _template_name(content)
    relative = f".github/skills/{name}/SKILL.md"
    metadata = {
        "schema_version": 2, "path": relative, "sha256": sha256(content).hexdigest(),
        "state": "unvalidated_draft", "normalization": NORMALIZATION,
        "source_manifest_sha256": sha256(source_content).hexdigest() if source_content is not None else None,
    }
    return {relative: content, BOOTSTRAP: _json_bytes(metadata)}, relative


def _shape(value, fields, version=1):
    _require(isinstance(value, dict) and set(value) == set(fields)
             and type(value.get("schema_version")) is int and value["schema_version"] == version, "invalid_manifest")


def _verify_data(source, bootstrap, hashes, directories, read_content):
    _require(source is not None or bootstrap is not None, "missing_manifest")
    installed = None
    if bootstrap is not None:
        _shape(bootstrap, ("schema_version", "path", "sha256", "state", "normalization", "source_manifest_sha256"),
               version=2)
        _require(bootstrap["state"] == "unvalidated_draft" and bootstrap["normalization"] == NORMALIZATION
                 and _matches(HASH, bootstrap["sha256"]), "invalid_manifest")
        source_binding = bootstrap["source_manifest_sha256"]
        _require(source_binding is None or _matches(HASH, source_binding), "invalid_manifest")
        if source_binding is None:
            _require(source is None, "invalid_manifest")
        else:
            _require(source is not None, "missing_manifest")
            _require(hashes.get(SOURCE) == source_binding, "source_mismatch")
        installed = bootstrap["path"]
        _relative(installed)
        _require(_matches(r"\.github/skills/[a-z0-9]+(?:-[a-z0-9]+)*/SKILL\.md", installed), "invalid_manifest")
        _require(hashes.get(installed) == bootstrap["sha256"], "bootstrap_mismatch")
        content = read_content(installed)
        _require(sha256(content).hexdigest() == bootstrap["sha256"], "bootstrap_mismatch")
        _require(b"\r\n" not in content, "invalid_manifest")
        try:
            name = _template_name(content)
        except SampleError:
            raise SampleError("invalid_manifest") from None
        _require(installed == f".github/skills/{name}/SKILL.md", "invalid_manifest")
    files = {}
    licenses = []
    omissions = []
    if source is not None:
        _shape(source, ("schema_version", "repository", "commit", "files", "license_files", "omissions", "export_policy"))
        try:
            repository, commit = _identity(source["repository"], source["commit"])
        except SampleError:
            raise SampleError("invalid_manifest") from None
        _require(commit == source["commit"] and repository == source["repository"]
                 and source["export_policy"] == EXPORT_POLICY, "invalid_manifest")
        files, licenses, omissions = source["files"], source["license_files"], source["omissions"]
        _require(isinstance(files, dict) and len(files) <= MAX_FILES, "invalid_manifest")
        paths = _Paths()
        for relative, checksum in files.items():
            paths.add(relative, "file")
            _require(_matches(HASH, checksum), "invalid_manifest")
        _require(isinstance(licenses, list) and licenses == _license_files(files), "invalid_manifest")
        _require(isinstance(omissions, list) and len(omissions) <= MAX_MEMBERS, "invalid_manifest")
        actual_paths = {path.casefold() for path in (*hashes, *directories)}
        for omission in omissions:
            _require(isinstance(omission, dict) and set(omission) == {"path", "reason"}
                     and omission["reason"] == "symlink", "invalid_manifest")
            paths.add(omission["path"], "symlink")
            _require(omission["path"].casefold() not in actual_paths, "source_mismatch")
        if installed is not None:
            _require(installed not in files, "invalid_manifest")
            paths.add(installed, "file")
        actual = {path: checksum for path, checksum in hashes.items() if path not in METADATA and path != installed}
        _require(actual == files, "source_mismatch")
    return {"source_files": len(files), "license_files": len(licenses), "omissions": len(omissions),
            "bootstrap": "unvalidated_draft" if bootstrap is not None else None}


def _verify_inventory(project, hashes, directories):
    source = _json_object(_read_file(project / SOURCE)) if SOURCE in hashes else None
    bootstrap = _json_object(_read_file(project / BOOTSTRAP)) if BOOTSTRAP in hashes else None
    return _verify_data(source, bootstrap, hashes, directories, lambda relative: _read_file(project / relative))


def _mkdirs(value, created):
    path = _checked_path(value)
    pending = []
    while not path.exists():
        pending.append(path)
        path = path.parent
    _directory(path)
    for directory in reversed(pending):
        _checked_path(directory)
        try:
            directory.mkdir()
        except FileExistsError:
            _directory(directory)
        else:
            created.append((directory, os.lstat(directory)))
            _directory(directory)


def _write_file(path, content, created):
    path = _checked_path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError:
        raise SampleError("destination_exists") from None
    with os.fdopen(descriptor, "wb") as stream:
        info = os.fstat(stream.fileno())
        created.append((path, info))
        _checked_path(path)
        _require(_ordinary(info) and stat.S_ISREG(info.st_mode) and _same_file(info, os.lstat(path)), "unsafe_path")
        stream.write(content)


def _rollback(files, directories):
    for path, identity in reversed(files):
        try:
            _checked_path(path)
            if _same_file(identity, os.lstat(path)):
                path.unlink()
        except (OSError, SampleError):
            pass
    for path, identity in reversed(directories):
        try:
            _checked_path(path)
            if _same_file(identity, os.lstat(path)):
                path.rmdir()
        except (OSError, SampleError):
            pass


def _install(project, files, new_project=False, directories=()):
    created_files = []
    created_directories = []
    try:
        if new_project:
            _mkdirs(project.parent, created_directories)
            _checked_path(project)
            _catalog_capacity(project.parent)
            try:
                project.mkdir()
            except FileExistsError:
                raise SampleError("destination_exists") from None
            created_directories.append((project, os.lstat(project)))
        _directory(project)
        for relative in sorted(directories):
            _mkdirs(project.joinpath(*_relative(relative)), created_directories)
        for relative in sorted(files, key=lambda value: (value in METADATA, value)):
            target = project.joinpath(*_relative(relative, metadata=True))
            _mkdirs(target.parent, created_directories)
            _write_file(target, files[relative], created_files)
        hashes, sizes, actual_directories = _scan(project)
        _require(all(hashes.get(relative) == sha256(content).hexdigest() for relative, content in files.items()),
                 "io_error")
        _verify_inventory(project, hashes, actual_directories)
    except BaseException:
        _rollback(created_files, created_directories)
        raise


@_io_boundary
def import_project(root, project_id, repository, commit, *, skill=None):
    """Import under root/projects without replacing an existing destination."""
    _identifier(project_id, "invalid_project")
    repository, commit = _identity(repository, commit)
    root = _directory(root)
    project = _checked_path(root / "projects" / project_id)
    _require(not os.path.lexists(project), "destination_exists")
    _catalog_capacity(project.parent)
    files, omissions, paths = _unpack(fetch_archive(repository, commit), repository, commit)
    provenance = {
        "schema_version": 1, "repository": repository, "commit": commit, "export_policy": EXPORT_POLICY,
        "files": {relative: sha256(content).hexdigest() for relative, content in sorted(files.items())},
        "license_files": _license_files(files), "omissions": omissions,
    }
    files[SOURCE] = _json_bytes(provenance)
    paths.add(SOURCE, "file", metadata=True)
    directories = {relative for relative, kind in paths.nodes.values() if kind == "directory"}
    if _existing_skill((*files, *directories)) is None:
        if skill is not None:
            try:
                selected = Path(skill)
            except (TypeError, ValueError):
                raise SampleError("unsafe_path") from None
            skill = selected if selected.is_absolute() else root / selected
        additions, installed = _bootstrap_files(skill, files[SOURCE])
        for relative, content in additions.items():
            paths.add(relative, "file", metadata=True)
            files[relative] = content
    _budget([len(content) for content in files.values()])
    hashes = {relative: sha256(content).hexdigest() for relative, content in files.items()}
    bootstrap = _json_object(files[BOOTSTRAP]) if BOOTSTRAP in files else None
    directories = {relative for relative, kind in paths.nodes.values() if kind == "directory"}
    _verify_data(provenance, bootstrap, hashes, directories, files.__getitem__)
    _install(project, files, new_project=True, directories=directories)
    return provenance


@_io_boundary
def ensure_skill(project, skill):
    """Preserve existing skills, or add an explicitly selected unvalidated draft."""
    project = _directory(project)
    hashes, sizes, directories = _scan(project)
    if METADATA.intersection(hashes):
        _verify_inventory(project, hashes, directories)
    existing = _existing_skill((*hashes, *directories))
    if existing is not None:
        return {"action": "preserved", "path": existing}
    source_content = _read_file(project / SOURCE) if SOURCE in hashes else None
    additions, installed = _bootstrap_files(skill, source_content)
    paths = _Paths()
    for relative in sorted(directories):
        paths.add(relative, "directory", metadata=True)
    for relative in hashes:
        paths.add(relative, "file", metadata=True)
    for relative in additions:
        paths.add(relative, "file", metadata=True)
    _budget(sizes + [len(content) for content in additions.values()])
    prospective = {**hashes, **{relative: sha256(content).hexdigest() for relative, content in additions.items()}}
    source = _json_object(source_content) if source_content is not None else None
    bootstrap = _json_object(additions[BOOTSTRAP])
    prospective_directories = {relative for relative, kind in paths.nodes.values() if kind == "directory"}
    _verify_data(source, bootstrap, prospective, prospective_directories, additions.__getitem__)
    _install(project, additions)
    return {"action": "added", "path": installed}


@_io_boundary
def verify_project(project):
    """Check bounded local manifests and return counts, never source or skill bodies."""
    project = _directory(project)
    hashes, sizes, directories = _scan(project)
    return _verify_inventory(project, hashes, directories)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise SampleError("invalid_arguments")


def main(argv=None):
    try:
        parser = _ArgumentParser(description=__doc__)
        parser.add_argument("--root", default=str(Path(__file__).absolute().parent))
        commands = parser.add_subparsers(dest="command", required=True)
        for name in ("import", "add-skill", "verify"):
            command = commands.add_parser(name)
            command.add_argument("--project", required=True)
            if name == "import":
                command.add_argument("--repository", required=True)
                command.add_argument("--commit", required=True)
            if name != "verify":
                command.add_argument("--skill", required=name == "add-skill")
        arguments = parser.parse_args(argv)
        _identifier(arguments.project, "invalid_project")
        root = _directory(arguments.root)
        project = _checked_path(root / "projects" / arguments.project)
        skill = getattr(arguments, "skill", None)
        if skill is not None:
            skill = root.joinpath(*_relative(skill))
        if arguments.command == "import":
            provenance = import_project(root, arguments.project, arguments.repository, arguments.commit, skill=skill)
            result = {"action": "imported", "project": arguments.project, "source_files": len(provenance["files"])}
        elif arguments.command == "add-skill":
            result = ensure_skill(project, skill)
        else:
            result = verify_project(project)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (SampleError, OSError) as error:
        print(error.code if isinstance(error, SampleError) else "io_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
