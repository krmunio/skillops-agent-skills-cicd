import json
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_rejects_files_replaced_before_open(self):
        outside = self.root / "outside.md"
        outside.write_text("Outside content must not enter a bundle.")
        open_file = os.open
        for replacement in ("symlink", "missing", "directory"):
            with self.subTest(replacement=replacement):
                folder = self.write_skill(f"skills/{replacement}", "Original content.")
                source = folder / "SKILL.md"

                def replace_before_open(path, flags):
                    source.unlink()
                    if replacement == "symlink":
                        source.symlink_to(outside)
                    elif replacement == "directory":
                        source.mkdir()
                    return open_file(path, flags)

                with patch("os.open", side_effect=replace_before_open):
                    with self.assertRaises(RuntimeFailure) as caught:
                        skill_guide.regular_files(folder, [folder])
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
            files, total = skill_guide.regular_files(folder, [folder])

        opened.assert_called_once_with(source, os.O_RDONLY | os.O_NOFOLLOW)
        checked.assert_called_once()
        self.assertTrue(source.is_symlink())
        self.assertEqual(files, [{
            "path": "SKILL.md", "bytes": len(b"Original content."),
            "sha256": sha256(b"Original content.").hexdigest(), "text": "Original content.",
        }])
        self.assertEqual(total, len(b"Original content."))
        streams[0].read.assert_called_once_with(skill_guide.FILE_LIMIT + 1)
        self.assertTrue(streams[0].closed)
        with self.assertRaises(OSError):
            fstat(checked.call_args.args[0])

    def test_rejects_file_growing_after_descriptor_check(self):
        folder = self.write_skill("skills/example", "Original content.")
        source = folder / "SKILL.md"
        fstat = os.fstat

        def grow_after_fstat(descriptor):
            info = fstat(descriptor)
            source.write_bytes(b"x" * (skill_guide.FILE_LIMIT + 1))
            return info

        with patch("os.fstat", side_effect=grow_after_fstat):
            with self.assertRaises(RuntimeFailure) as caught:
                skill_guide.regular_files(folder, [folder])
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
