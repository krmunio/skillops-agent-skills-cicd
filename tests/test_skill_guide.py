import json
from hashlib import sha256
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from copilot_runtime import RuntimeFailure
import skill_guide


class SkillGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)

    def write_skill(self, relative, body, extras=None):
        folder = self.root / relative
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(body)
        for name, content in (extras or {}).items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)
        return folder

    def test_discovers_each_skill_and_excludes_nested_skill_files(self):
        parent = self.write_skill(
            ".github/skills/parent",
            "---\nname: parent\ndescription: Parent work.\n---\nRead notes.\n",
            {"references/notes.md": "# Notes\n", "assets/icon.bin": b"\x00\x01"},
        )
        child = self.write_skill(
            ".github/skills/parent/children/child",
            "---\nname: child\ndescription: Child work.\n---\nDo child work.\n",
            {"references/child.md": "# Child\n"},
        )
        self.write_skill(
            ".claude/skills/second",
            "---\nname: second\ndescription: Second work.\n---\nDo second work.\n",
        )

        bundles = skill_guide.discover(self.root)

        self.assertEqual([row["path"] for row in bundles], [
            ".claude/skills/second",
            ".github/skills/parent",
            ".github/skills/parent/children/child",
        ])
        by_path = {row["path"]: row for row in bundles}
        parent_bundle = by_path[".github/skills/parent"]
        self.assertEqual([file["path"] for file in parent_bundle["files"]], [
            "SKILL.md", "assets/icon.bin", "references/notes.md",
        ])
        self.assertEqual(parent_bundle["file_count"], 3)
        self.assertEqual(parent_bundle["text_files"], 2)
        self.assertEqual(parent_bundle["binary_files"], 1)
        self.assertEqual(parent_bundle["bytes"], sum(file["bytes"] for file in parent_bundle["files"]))
        self.assertEqual(parent_bundle["files"][1], {
            "path": "assets/icon.bin",
            "bytes": 2,
            "sha256": sha256(b"\x00\x01").hexdigest(),
            "text": None,
        })
        self.assertNotIn("references/child.md", json.dumps(parent_bundle))
        self.assertEqual(parent_bundle["root"], str(parent))
        self.assertEqual(by_path[".github/skills/parent/children/child"]["root"], str(child))

    def test_rejects_symlinks(self):
        folder = self.write_skill(
            "skills/example",
            "---\nname: example\ndescription: Example.\n---\nDo work.\n",
        )
        (folder / "linked.md").symlink_to(self.root / "outside.md")

        with self.assertRaises(RuntimeFailure) as caught:
            skill_guide.discover(self.root)

        self.assertEqual(caught.exception.code, "unsafe_skill_path")

    def test_rejects_symlinked_skill_roots(self):
        outside = self.write_skill("outside", "Linked skill.")
        for name in skill_guide.SKILL_ROOTS:
            with self.subTest(root=name):
                project = self.root / name.replace("/", "-")
                root = project / name
                root.parent.mkdir(parents=True)
                root.symlink_to(outside, target_is_directory=True)

                with self.assertRaises(RuntimeFailure) as caught:
                    skill_guide.discover(project)

                self.assertEqual(caught.exception.code, "unsafe_skill_path")

    def test_rejects_project_replaced_by_external_symlink_before_discovery(self):
        self.write_skill("project/skills/example", "Original skill.")
        sentinel = "OUTSIDE SENTINEL: never include this content."
        self.write_skill("outside/skills/example", sentinel)
        project = self.root / "project"
        project.rename(self.root / "original-project")
        project.symlink_to(self.root / "outside", target_is_directory=True)

        result, failure = [], None
        with patch("skill_guide._read_relative", wraps=skill_guide._read_relative) as read:
            try:
                result = skill_guide.discover(project)
            except RuntimeFailure as error:
                failure = error

        self.assertNotIn(sentinel, json.dumps(result))
        self.assertIsNotNone(failure, "The project symlink must be rejected before reading.")
        self.assertEqual(failure.code, "unsafe_skill_path")
        read.assert_not_called()

    def test_skips_initially_missing_skill_roots_after_one_no_follow_stat(self):
        with patch("os.stat", wraps=os.stat) as checked:
            self.assertEqual(skill_guide.discover(self.root), [])

        for name in skill_guide.SKILL_ROOTS:
            with self.subTest(root=name):
                root = self.root / name
                self.assertEqual(
                    [entry for entry in checked.call_args_list if entry.args[0] == root],
                    [call(root, follow_symlinks=False)],
                )

    def test_rejects_initially_non_directory_skill_roots(self):
        for name in skill_guide.SKILL_ROOTS:
            for kind in ("file", "dangling_symlink"):
                with self.subTest(root=name, kind=kind):
                    project = self.root / name.replace("/", "-") / kind
                    root = project / name
                    root.parent.mkdir(parents=True)
                    if kind == "file":
                        root.write_text("Not a directory.")
                    else:
                        root.symlink_to(project / "missing", target_is_directory=True)

                    with self.assertRaises(RuntimeFailure) as caught:
                        skill_guide.discover(project)

                    self.assertEqual(caught.exception.code, "unsafe_skill_path")

    def test_rejects_skill_roots_replaced_after_initial_stat_before_traversal(self):
        stat_path = os.stat
        for name in skill_guide.SKILL_ROOTS:
            for replacement in ("missing", "file", "symlink", "dangling_symlink", "directory"):
                with self.subTest(root=name, replacement=replacement):
                    case = Path(name.replace("/", "-")) / replacement
                    project = self.root / case / "project"
                    self.write_skill(case / "project" / name / "example", "Original skill.")
                    root = project / name
                    outside = self.root / case / "outside"
                    outside.mkdir()
                    replaced = False

                    def replace_after_stat(path, *args, **kwargs):
                        nonlocal replaced
                        info = stat_path(path, *args, **kwargs)
                        if path == root and not kwargs.get("follow_symlinks", True) and not replaced:
                            self.assertTrue(stat.S_ISDIR(info.st_mode))
                            root.rename(outside / "original")
                            if replacement == "file":
                                root.write_text("Not a directory.")
                            elif replacement in ("symlink", "dangling_symlink"):
                                target = outside if replacement == "symlink" else outside / "missing"
                                root.symlink_to(target, target_is_directory=True)
                            elif replacement == "directory":
                                root.mkdir()
                            replaced = True
                        return info

                    with patch("os.stat", side_effect=replace_after_stat), patch(
                        "os.scandir", wraps=os.scandir
                    ) as scanned:
                        with self.assertRaises(RuntimeFailure) as caught:
                            skill_guide.discover(project)

                    self.assertTrue(replaced)
                    self.assertEqual(caught.exception.code, "unsafe_skill_path")
                    scanned.assert_not_called()

    def test_rejects_root_ancestor_symlink_replacement_before_initial_root_stat(self):
        self.write_skill("project/.github/skills/example", "Original skill.")
        project = self.root / "project"
        ancestor = project / ".github"
        root = ancestor / "skills"
        outside = self.root / "outside"
        outside.mkdir()
        stat_path = os.stat
        replaced = False

        def replace_before_stat(path, *args, **kwargs):
            nonlocal replaced
            if path == root and not kwargs.get("follow_symlinks", True) and not replaced:
                ancestor.rename(self.root / "original-github")
                ancestor.symlink_to(outside, target_is_directory=True)
                replaced = True
            return stat_path(path, *args, **kwargs)

        with patch("os.stat", side_effect=replace_before_stat), patch(
            "skill_guide.regular_files", wraps=skill_guide.regular_files
        ) as read:
            with self.assertRaises(RuntimeFailure) as caught:
                skill_guide.discover(project)

        self.assertTrue(replaced)
        self.assertEqual(caught.exception.code, "unsafe_skill_path")
        read.assert_not_called()

    def test_rejects_previous_skill_root_replacement_while_discovering_later_root(self):
        self.write_skill(".github/skills/example", "Original skill.")
        self.write_skill(".claude/skills/second", "Second skill.")
        sentinel = "REPLACEMENT SENTINEL: never include this content."
        self.write_skill("replacement/example", sentinel)
        root = self.root / ".github/skills"
        later_root = self.root / ".claude/skills"
        scandir = os.scandir
        replaced = False

        def replace_before_scan(path):
            nonlocal replaced
            if Path(path) == later_root and not replaced:
                root.rename(self.root / "original-github-skills")
                (self.root / "replacement").rename(root)
                replaced = True
            return scandir(path)

        result, failure = [], None
        with patch("os.scandir", side_effect=replace_before_scan), patch(
            "skill_guide.regular_files", wraps=skill_guide.regular_files
        ) as read:
            try:
                result = skill_guide.discover(self.root)
            except RuntimeFailure as error:
                failure = error

        self.assertTrue(replaced)
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertIsNotNone(failure, "Previously discovered roots must be revalidated before reading.")
        self.assertEqual(failure.code, "unsafe_skill_path")
        read.assert_not_called()

    def test_rejects_directory_symlinks_before_discovery(self):
        outside = self.write_skill("outside", "Linked skill.")
        for name in skill_guide.SKILL_ROOTS:
            for relative in ("example", "group/example", "parent/references"):
                with self.subTest(root=name, directory=relative):
                    project = self.root / name.replace("/", "-") / relative.replace("/", "-")
                    root = project / name
                    directory = root / relative
                    directory.parent.mkdir(parents=True)
                    if relative == "parent/references":
                        (directory.parent / "SKILL.md").write_text("Parent skill.")
                    directory.symlink_to(outside, target_is_directory=True)

                    with patch("skill_guide._read_relative", wraps=skill_guide._read_relative) as read:
                        with self.assertRaises(RuntimeFailure) as caught:
                            skill_guide.discover(project)

                    self.assertEqual(caught.exception.code, "unsafe_skill_path")
                    read.assert_not_called()

    def test_rejects_directories_replaced_during_discovery(self):
        scandir = os.scandir
        for relative in ("skills", "skills/example", "skills/group"):
            for replacement in ("symlink", "missing", "file", "directory"):
                with self.subTest(directory=relative, replacement=replacement):
                    case = Path(relative.replace("/", "-")) / replacement
                    project = self.root / case / "project"
                    self.write_skill(case / "project" / relative / "child", "Original skill.")
                    directory = project / relative
                    outside = self.root / case / "outside"
                    outside.mkdir()
                    replaced = False

                    def replace_before_scan(path):
                        nonlocal replaced
                        if Path(path) == directory and not replaced:
                            directory.rename(outside / "original")
                            if replacement == "symlink":
                                directory.symlink_to(outside / "empty", target_is_directory=True)
                                (outside / "empty").mkdir()
                            elif replacement == "file":
                                directory.write_text("Not a directory.")
                            elif replacement == "directory":
                                directory.mkdir()
                            replaced = True
                        return scandir(path)

                    with patch("os.scandir", side_effect=replace_before_scan):
                        with self.assertRaises(RuntimeFailure) as caught:
                            skill_guide.discover(project)

                    self.assertTrue(replaced)
                    self.assertEqual(caught.exception.code, "unsafe_skill_path")

    def test_rejects_skill_replaced_by_directory_after_discovery(self):
        folder = self.write_skill("skills/example", "Original content.")
        source = folder / "SKILL.md"
        regular_files = skill_guide.regular_files

        def replace_before_enumeration(*args, **kwargs):
            source.unlink()
            source.mkdir()
            return regular_files(*args, **kwargs)

        with patch("skill_guide.regular_files", side_effect=replace_before_enumeration), patch(
            "skill_guide._read_relative", wraps=skill_guide._read_relative
        ) as read:
            with self.assertRaises(RuntimeFailure) as caught:
                skill_guide.discover(self.root)

        self.assertEqual(caught.exception.code, "unsafe_skill_path")
        read.assert_called_once()
        self.assertEqual(read.call_args.args[1], source.relative_to(self.root))

    def test_rejects_discovered_resources_changed_before_read(self):
        sentinel = "REPLACEMENT SENTINEL: never include this content."
        read_relative = skill_guide._read_relative
        for relative in ("notes.md", "references/notes.md"):
            for replacement in ("missing", "renamed", "directory", "file", "symlink"):
                with self.subTest(resource=relative, replacement=replacement):
                    case = Path(relative.replace("/", "-")) / replacement
                    folder = self.write_skill(
                        case / "project/skills/example", "Original skill.",
                        {relative: "Original notes."},
                    )
                    project = folder.parent.parent
                    source = folder / relative
                    replaced = False

                    def replace_after_skill_read(*args, **kwargs):
                        nonlocal replaced
                        raw = read_relative(*args, **kwargs)
                        if Path(args[1]).name == "SKILL.md" and not replaced:
                            if replacement == "missing":
                                source.unlink()
                            elif replacement == "renamed":
                                source.rename(source.with_name("renamed.md"))
                            else:
                                source.rename(self.root / case / "original-notes.md")
                                if replacement == "directory":
                                    source.mkdir()
                                elif replacement == "file":
                                    source.write_text(sentinel)
                                else:
                                    outside = self.root / case / "outside.md"
                                    outside.write_text(sentinel)
                                    source.symlink_to(outside)
                            replaced = True
                        return raw

                    result, failure = [], None
                    with patch("skill_guide._read_relative", side_effect=replace_after_skill_read):
                        try:
                            result = skill_guide.discover(project)
                        except RuntimeFailure as error:
                            failure = error

                    self.assertTrue(replaced)
                    self.assertNotIn(sentinel, json.dumps(result))
                    self.assertIsNotNone(failure, "A discovered resource must not be silently omitted.")
                    self.assertEqual(failure.code, "unsafe_skill_path")

    def test_rejects_discovered_directories_changed_before_resource_read(self):
        read_relative = skill_guide._read_relative
        open_file, fstat = os.open, os.fstat
        for relative in (".github", ".github/skills", ".github/skills/example",
                         ".github/skills/example/references"):
            for replacement in ("renamed", "file", "directory"):
                with self.subTest(directory=relative, replacement=replacement):
                    case = Path(relative.replace("/", "-")) / replacement
                    self.write_skill(
                        case / "project/.github/skills/example", "Original skill.",
                        {"references/notes.md": "Original notes."},
                    )
                    project = self.root / case / "project"
                    directory = project / relative
                    replaced = False
                    descriptors = []

                    def replace_after_skill_read(*args, **kwargs):
                        nonlocal replaced
                        raw = read_relative(*args, **kwargs)
                        if Path(args[1]).name == "SKILL.md" and not replaced:
                            moved = self.root / case / "original-directory"
                            directory.rename(moved)
                            if replacement == "file":
                                directory.write_text("Not a directory.")
                            elif replacement == "directory":
                                directory.mkdir()
                                for child in moved.iterdir():
                                    child.rename(directory / child.name)
                            replaced = True
                        return raw

                    def track_open(*args, **kwargs):
                        descriptor = open_file(*args, **kwargs)
                        descriptors.append(descriptor)
                        return descriptor

                    with patch("skill_guide._read_relative", side_effect=replace_after_skill_read), patch(
                        "os.open", side_effect=track_open
                    ):
                        with self.assertRaises(RuntimeFailure) as caught:
                            skill_guide.discover(project)

                    self.assertTrue(replaced)
                    self.assertEqual(caught.exception.code, "unsafe_skill_path")
                    for descriptor in descriptors:
                        with self.assertRaises(OSError):
                            fstat(descriptor)

    def test_rejects_project_replaced_before_root_open(self):
        open_file = os.open
        for replacement in ("symlink", "directory"):
            with self.subTest(replacement=replacement):
                project = self.root / replacement / "project"
                self.write_skill(Path(replacement) / "project/skills/example", "Original skill.")
                replaced = False

                def replace_before_open(path, flags, **kwargs):
                    nonlocal replaced
                    if Path(path) == project and not replaced:
                        moved = project.with_name("original-project")
                        project.rename(moved)
                        if replacement == "symlink":
                            project.symlink_to(moved, target_is_directory=True)
                        else:
                            project.mkdir()
                            (moved / "skills").rename(project / "skills")
                        replaced = True
                    return open_file(path, flags, **kwargs)

                with patch("os.open", side_effect=replace_before_open):
                    with self.assertRaises(RuntimeFailure) as caught:
                        skill_guide.discover(project)

                self.assertTrue(replaced)
                self.assertEqual(caught.exception.code, "unsafe_skill_path")

    def test_defers_new_files_added_after_skill_read_until_next_discovery(self):
        folder = self.write_skill("skills/example", "Original skill.")
        read_relative = skill_guide._read_relative

        def add_after_skill_read(*args, **kwargs):
            raw = read_relative(*args, **kwargs)
            if Path(args[1]).name == "SKILL.md":
                (folder / "late.md").write_text("Added after traversal.")
            return raw

        with patch("skill_guide._read_relative", side_effect=add_after_skill_read):
            first = skill_guide.discover(self.root)
        second = skill_guide.discover(self.root)

        self.assertEqual([file["path"] for file in first[0]["files"]], ["SKILL.md"])
        self.assertEqual([file["path"] for file in second[0]["files"]], ["SKILL.md", "late.md"])

    def test_reads_skill_once_without_reenumerating_bundle(self):
        body = "Original skill: 원본."
        folder = self.write_skill("skills/example", body, {"README.md": "Notes."})
        source = folder / "SKILL.md"

        with patch("skill_guide._read_relative", wraps=skill_guide._read_relative) as read, patch(
            "pathlib.Path.rglob", side_effect=AssertionError("Discovery candidates must not be reenumerated.")
        ):
            bundles = skill_guide.discover(self.root)

        self.assertEqual(len(bundles), 1)
        self.assertEqual([file["path"] for file in bundles[0]["files"]], ["README.md", "SKILL.md"])
        self.assertEqual(bundles[0]["files"][1]["text"], body)
        self.assertEqual([entry.args[1] for entry in read.call_args_list], [
            source.relative_to(self.root),
            (folder / "README.md").relative_to(self.root),
        ])

    def test_rejects_invalid_skill_encoding_before_resource_read(self):
        folder = self.write_skill("skills/example", "Original content.", {"README.md": "Notes."})
        (folder / "SKILL.md").write_bytes(b"\xff")

        with patch("skill_guide._read_relative", wraps=skill_guide._read_relative) as read:
            with self.assertRaises(RuntimeFailure) as caught:
                skill_guide.discover(self.root)

        self.assertEqual(caught.exception.code, "invalid_skill_encoding")
        read.assert_called_once()
        self.assertEqual(read.call_args.args[1], (folder / "SKILL.md").relative_to(self.root))

    def test_rejects_directories_replaced_by_external_symlinks_before_open(self):
        sentinel = "OUTSIDE SENTINEL: never include this content."
        open_file, fstat = os.open, os.fstat
        for component in ("bundle", "subdirectory"):
            with self.subTest(component=component):
                folder = self.write_skill(
                    f"{component}/project/skills/example", "Original skill.",
                    {"references/notes.md": "Original notes."},
                )
                project = folder.parent.parent
                outside = self.write_skill(
                    f"{component}/outside", sentinel,
                    {"references/notes.md": sentinel, "notes.md": sentinel},
                )
                directory = folder if component == "bundle" else folder / "references"
                source = folder / ("SKILL.md" if component == "bundle" else "references/notes.md")
                replaced = False
                descriptors = []

                def replace_before_open(path, flags, **kwargs):
                    nonlocal replaced
                    if not replaced and (Path(path) == source or Path(path).name == directory.name):
                        directory.rename(directory.with_name(f"moved-{directory.name}"))
                        directory.symlink_to(outside, target_is_directory=True)
                        replaced = True
                    descriptor = open_file(path, flags, **kwargs)
                    descriptors.append(descriptor)
                    return descriptor

                result, failure = [], None
                with patch("os.open", side_effect=replace_before_open):
                    try:
                        result = skill_guide.discover(project)
                    except RuntimeFailure as error:
                        failure = error

                self.assertTrue(replaced)
                self.assertNotIn(sentinel, json.dumps(result))
                self.assertIsNotNone(failure, "The replaced directory must be rejected.")
                self.assertEqual(failure.code, "unsafe_skill_path")
                for descriptor in descriptors:
                    with self.assertRaises(OSError):
                        fstat(descriptor)

    def test_rejects_file_replaced_by_fifo_without_waiting_for_writer(self):
        folder = self.write_skill("skills/example", "Original content.")
        source = folder / "SKILL.md"
        open_file, fstat = os.open, os.fstat
        descriptors = []

        def replace_before_open(path, flags, **kwargs):
            if Path(path).name == source.name:
                source.unlink()
                os.mkfifo(source)
                self.assertTrue(flags & os.O_NONBLOCK, "Opening a FIFO must not wait for a writer.")
            descriptor = open_file(path, flags, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with patch("os.open", side_effect=replace_before_open), patch("os.fdopen") as stream:
            with self.assertRaises(RuntimeFailure) as caught:
                skill_guide.regular_files(folder, [Path("SKILL.md")])

        self.assertEqual(caught.exception.code, "unsafe_skill_path")
        stream.assert_not_called()
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                fstat(descriptor)

    def test_rejects_files_replaced_before_open(self):
        outside = self.root / "outside.md"
        outside.write_text("Outside content must not enter a bundle.")
        open_file = os.open
        for replacement in ("symlink", "missing", "directory"):
            with self.subTest(replacement=replacement):
                folder = self.write_skill(f"skills/{replacement}", "Original content.")
                source = folder / "SKILL.md"

                def replace_before_open(path, flags, **kwargs):
                    if Path(path).name == source.name:
                        source.unlink()
                        if replacement == "symlink":
                            source.symlink_to(outside)
                        elif replacement == "directory":
                            source.mkdir()
                    return open_file(path, flags, **kwargs)

                with patch("os.open", side_effect=replace_before_open):
                    with self.assertRaises(RuntimeFailure) as caught:
                        skill_guide.regular_files(folder, [Path("SKILL.md")])
                self.assertEqual(caught.exception.code, "unsafe_skill_path")

    def test_reads_checked_descriptor_when_path_is_replaced(self):
        folder = self.write_skill("skills/example", "Original content.")
        source = folder / "SKILL.md"
        outside = self.root / "outside.md"
        outside.write_text("Outside content must not enter a bundle.")
        fstat, fdopen = os.fstat, os.fdopen
        streams = []

        def replace_after_fstat(descriptor):
            info = fstat(descriptor)
            if stat.S_ISREG(info.st_mode):
                source.unlink()
                source.symlink_to(outside)
            return info

        def track_stream(*args, **kwargs):
            stream = fdopen(*args, **kwargs)
            stream.read = Mock(wraps=stream.read)
            streams.append(stream)
            return stream

        with patch("os.open", wraps=os.open) as opened, patch(
            "os.fstat", side_effect=replace_after_fstat
        ) as checked, patch("os.fdopen", side_effect=track_stream):
            files, total = skill_guide.regular_files(folder, [Path("SKILL.md")])

        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        root_fd, file_fd = (entry.args[0] for entry in checked.call_args_list)
        self.assertEqual(opened.call_args_list, [
            call(folder, flags | os.O_DIRECTORY),
            call("SKILL.md", flags, dir_fd=root_fd),
        ])
        self.assertTrue(source.is_symlink())
        self.assertEqual(files, [{
            "path": "SKILL.md", "bytes": len(b"Original content."),
            "sha256": sha256(b"Original content.").hexdigest(), "text": "Original content.",
        }])
        self.assertEqual(total, len(b"Original content."))
        streams[0].read.assert_called_once_with(skill_guide.FILE_LIMIT + 1)
        self.assertTrue(streams[0].closed)
        for descriptor in (root_fd, file_fd):
            with self.assertRaises(OSError):
                fstat(descriptor)

    def test_rejects_file_growing_after_descriptor_check(self):
        folder = self.write_skill("skills/example", "Original content.")
        source = folder / "SKILL.md"
        fstat = os.fstat

        def grow_after_fstat(descriptor):
            info = fstat(descriptor)
            if stat.S_ISREG(info.st_mode):
                source.write_bytes(b"x" * (skill_guide.FILE_LIMIT + 1))
            return info

        with patch("os.fstat", side_effect=grow_after_fstat):
            with self.assertRaises(RuntimeFailure) as caught:
                skill_guide.regular_files(folder, [Path("SKILL.md")])
        self.assertEqual(caught.exception.code, "skill_file_limit")

    def test_rejects_invalid_text_encoding_and_size_limits(self):
        folder = self.write_skill(
            "skills/example",
            "---\nname: example\ndescription: Example.\n---\nDo work.\n",
        )
        (folder / "bad.txt").write_bytes(b"\xff")
        with self.assertRaises(RuntimeFailure) as caught:
            skill_guide.discover(self.root)
        self.assertEqual(caught.exception.code, "invalid_skill_encoding")

        (folder / "bad.txt").unlink()
        (folder / "large.bin").write_bytes(b"x" * (skill_guide.FILE_LIMIT + 1))
        with self.assertRaises(RuntimeFailure) as caught:
            skill_guide.discover(self.root)
        self.assertEqual(caught.exception.code, "skill_file_limit")

    def test_enforces_aggregate_bundle_size_boundary(self):
        file_limit = 2 * 1024 * 1024
        bundle_limit = 8 * 1024 * 1024
        for difference in (-1, 0, 1):
            with self.subTest(bytes=bundle_limit + difference):
                folder = self.write_skill(f"case-{difference}/skills/example", "x")
                sizes = [file_limit] * 3 + [file_limit - 1 + difference]
                for index, size in enumerate(sizes):
                    with (folder / f"asset-{index}.bin").open("wb") as stream:
                        stream.truncate(size)
                actual_sizes = [path.stat().st_size for path in folder.iterdir()]
                self.assertTrue(all(size <= file_limit for size in actual_sizes))
                self.assertEqual(sum(actual_sizes), bundle_limit + difference)

                if difference > 0:
                    with self.assertRaises(RuntimeFailure) as caught:
                        skill_guide.discover(folder.parent.parent)
                    self.assertEqual(caught.exception.code, "skill_bundle_limit")
                else:
                    bundles = skill_guide.discover(folder.parent.parent)
                    self.assertEqual(len(bundles), 1)
                    self.assertEqual(bundles[0]["bytes"], bundle_limit + difference)
                    self.assertEqual(bundles[0]["file_count"], 5)

    def test_bundle_hash_is_stable_for_unchanged_content(self):
        self.write_skill("skills/example", "Skill.", {"references/notes.md": "Notes."})

        first = skill_guide.discover(self.root)[0]
        second = skill_guide.discover(self.root)[0]

        self.assertRegex(first["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["sha256"], second["sha256"])

    def test_bundle_hash_changes_when_one_file_content_changes(self):
        folder = self.write_skill("skills/example", "Skill.", {"references/notes.md": "Before."})
        first = skill_guide.discover(self.root)[0]

        (folder / "references/notes.md").write_text("After..")
        second = skill_guide.discover(self.root)[0]

        self.assertEqual(first["bytes"], second["bytes"])
        self.assertEqual(first["file_count"], second["file_count"])
        self.assertNotEqual(first["sha256"], second["sha256"])
