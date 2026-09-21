import contextlib
import gzip
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


REPOSITORY = "example/repo"
COMMIT = "a" * 40
PREFIX = f"repo-{COMMIT}"
SOURCE = ".skillops-source.json"
BOOTSTRAP = ".skillops-bootstrap.json"
SKILL_PATH = ".github/skills/local-draft/SKILL.md"
TEMPLATE = b"---\r\nname: local-draft\r\ndescription: Work on this project.\r\n---\r\nPrivate draft body.\r\n"
UPSTREAM_SKILL = b"---\r\nname: original\r\ndescription: Existing skill.\r\n---\r\nKeep these bytes.\r\n"
FILES = {
    "LICENSE": b"Upstream license\r\n",
    "module.py": b"raise RuntimeError('never execute imported code')\r\n",
    "skills/original/SKILL.md": UPSTREAM_SKILL,
}


def archive_entry(name, content=b"", entry_type=None, target=""):
    import tarfile

    member = tarfile.TarInfo(name)
    member.type = tarfile.REGTYPE if entry_type is None else entry_type
    member.size = len(content)
    member.linkname = target
    return member, content


def archive_bytes(files=None, extra=(), prefix=PREFIX, include_root=True, archive_format=None):
    import tarfile

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=archive_format) as archive:
        entries = []
        if include_root:
            entries.append(archive_entry(prefix, entry_type=tarfile.DIRTYPE))
        entries.extend(archive_entry(f"{prefix}/{name}", content)
                       for name, content in (FILES if files is None else files).items())
        entries.extend(extra)
        for member, content in entries:
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def digest(content):
    return hashlib.sha256(content).hexdigest()


class SamplesTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("project_samples"), "Preparation helper is missing")
        self.samples = importlib.import_module("project_samples")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "projects/fixture"
        self.template = self.root / "templates/SKILL.md"
        self.template.parent.mkdir()
        self.template.write_bytes(TEMPLATE)

    def import_files(self, files=None, extra=(), skill=None):
        with patch.object(self.samples, "fetch_archive", return_value=archive_bytes(files, extra)) as fetch:
            result = self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT, skill=skill)
        fetch.assert_called_once_with(REPOSITORY, COMMIT)
        return result

    def reject_archive(self, data, code=None, skill=None):
        with patch.object(self.samples, "fetch_archive", return_value=data), self.assertRaises(
            self.samples.SampleError
        ) as caught:
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT, skill=skill)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), caught.exception.code)
        self.assertFalse(os.path.lexists(self.project))

    def snapshot(self):
        return {path.relative_to(self.project).as_posix(): path.read_bytes()
                for path in self.project.rglob("*") if path.is_file()}

    def manifest(self, name=SOURCE):
        return json.loads((self.project / name).read_bytes())

    def write_manifest(self, value, name=SOURCE):
        (self.project / name).write_text(json.dumps(value), encoding="utf-8")

    def make_symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        self.addCleanup(lambda: link.unlink(missing_ok=True))


class SourceImportTests(SamplesTestCase):
    def test_import_preserves_original_bytes_licenses_and_provenance(self):
        files = {**FILES, "vendor/LICENSE.txt": b"Another license\n", "data.bin": b"\x00\xff\r\n"}
        provenance = self.import_files(files)
        self.assertEqual(provenance, self.manifest())
        self.assertEqual(provenance["schema_version"], 1)
        self.assertEqual(provenance["repository"], REPOSITORY)
        self.assertEqual(provenance["commit"], COMMIT)
        self.assertEqual(provenance["export_policy"], "ordinary_files_only")
        self.assertEqual(provenance["files"], {name: digest(content) for name, content in files.items()})
        self.assertEqual(provenance["license_files"], ["LICENSE", "vendor/LICENSE.txt"])
        self.assertEqual(provenance["omissions"], [])
        for name, content in files.items():
            self.assertEqual((self.project / name).read_bytes(), content)
        self.assertFalse((self.project / BOOTSTRAP).exists())
        self.assertEqual(self.samples.verify_project(self.project), {
            "source_files": 5, "license_files": 2, "omissions": 0, "bootstrap": None,
        })

    def test_missing_skill_uses_selected_draft_and_normalizes_only_template(self):
        files = {"LICENSE": b"Original license\r\n", "module.py": b"original\r\n"}
        provenance = self.import_files(files, skill="templates/SKILL.md")
        normalized = TEMPLATE.replace(b"\r\n", b"\n")
        self.assertEqual((self.project / SKILL_PATH).read_bytes(), normalized)
        self.assertEqual(self.template.read_bytes(), TEMPLATE)
        self.assertEqual((self.project / "module.py").read_bytes(), files["module.py"])
        self.assertEqual(set(provenance["files"]), set(files))
        self.assertEqual(self.manifest(BOOTSTRAP), {
            "schema_version": 2, "path": SKILL_PATH, "sha256": digest(normalized),
            "state": "unvalidated_draft", "normalization": "crlf_to_lf",
            "source_manifest_sha256": digest((self.project / SOURCE).read_bytes()),
        })
        self.assertEqual(self.samples.verify_project(self.project)["bootstrap"], "unvalidated_draft")

    def test_no_skill_and_no_template_fails_without_publication(self):
        self.reject_archive(archive_bytes({"LICENSE": b"license"}), "missing_skill")

    def test_invalid_template_fails_without_publication(self):
        self.template.write_bytes(b"not frontmatter")
        self.reject_archive(archive_bytes({"module.py": b"original"}), "invalid_template", self.template)

    def test_existing_skill_ignores_missing_selected_template(self):
        self.import_files(skill=self.root / "missing/SKILL.md")
        self.assertEqual((self.project / "skills/original/SKILL.md").read_bytes(), UPSTREAM_SKILL)
        self.assertFalse((self.project / BOOTSTRAP).exists())

    def test_destination_is_never_replaced_even_when_empty(self):
        self.project.mkdir(parents=True)
        for nonempty in (False, True):
            with self.subTest(nonempty=nonempty):
                if nonempty:
                    (self.project / "keep.txt").write_bytes(b"untouched")
                before = self.snapshot()
                with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(
                    self.samples.SampleError
                ) as caught:
                    self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
                self.assertEqual(caught.exception.code, "destination_exists")
                self.assertEqual(self.snapshot(), before)
                fetch.assert_not_called()

    def test_file_destination_is_not_replaced(self):
        self.project.parent.mkdir()
        self.project.write_bytes(b"keep")
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError):
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        fetch.assert_not_called()
        self.assertEqual(self.project.read_bytes(), b"keep")

    def test_destination_created_during_download_is_not_replaced(self):
        def concurrent_destination(repository, commit):
            self.project.mkdir(parents=True)
            return archive_bytes()

        with patch.object(self.samples, "fetch_archive", side_effect=concurrent_destination), self.assertRaises(
            self.samples.SampleError
        ) as caught:
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        self.assertEqual(caught.exception.code, "destination_exists")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_invalid_ids_repositories_and_revisions_are_rejected_before_download(self):
        invalid_ids = ("", "../escape", "Upper", "a.b", "with space", "a" * 65, "nul", "con", "com1", "lpt9",
                       "-project", "_project")
        invalid_repositories = ("repo", "owner/repo/more", "https://github.com/owner/repo", "Owner/repo",
                                "../repo", "owner/repo.git", "nul/repo", "owner/com1", "a" * 65 + "/repo")
        invalid_commits = ("main", "v1.0", "a" * 39, "a" * 41, "g" * 40, "../" + "a" * 40, None)
        arguments = [(identifier, REPOSITORY, COMMIT, "invalid_project") for identifier in invalid_ids]
        arguments += [("fixture", repository, COMMIT, "invalid_repository") for repository in invalid_repositories]
        arguments += [("fixture", REPOSITORY, commit, "invalid_commit") for commit in invalid_commits]
        for identifier, repository, commit, code in arguments:
            with self.subTest(arguments=(identifier, repository, commit)), patch.object(
                self.samples, "fetch_archive"
            ) as fetch, self.assertRaises(self.samples.SampleError) as caught:
                self.samples.import_project(self.root, identifier, repository, commit)
            self.assertEqual(caught.exception.code, code)
            fetch.assert_not_called()

    def test_symlinks_are_omitted_without_recording_or_following_targets(self):
        import tarfile

        links = [archive_entry(f"{PREFIX}/AGENTS.md", entry_type=tarfile.SYMTYPE, target="CLAUDE.md"),
                 archive_entry(f"{PREFIX}/outside", entry_type=tarfile.SYMTYPE, target="../../private/token")]
        provenance = self.import_files(extra=links)
        self.assertEqual(provenance["omissions"], [
            {"path": "AGENTS.md", "reason": "symlink"}, {"path": "outside", "reason": "symlink"},
        ])
        self.assertFalse(os.path.lexists(self.project / "AGENTS.md"))
        self.assertNotIn("private", json.dumps(provenance))
        self.assertEqual(self.samples.verify_project(self.project)["omissions"], 2)

    def test_hardlinks_and_special_members_are_rejected(self):
        import tarfile

        for entry_type in (tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE,
                           tarfile.GNUTYPE_SPARSE, tarfile.CONTTYPE, b"V"):
            with self.subTest(entry_type=entry_type):
                member = archive_entry(f"{PREFIX}/special", entry_type=entry_type, target=f"{PREFIX}/LICENSE")
                self.reject_archive(archive_bytes(extra=[member]))

    def test_unsafe_archive_paths_are_rejected(self):
        paths = ("../escape", "/absolute", "C:/drive", "C:relative", "\\\\server\\share", "back\\slash",
                 "a/../escape", "a/./file", "a//file", "file.", "file ", "a:b", "a?b", "a*b", 'a"b',
                 "a<b", "a>b", "a|b", "bad\nname", "bad\x7fname", "con", "NUL.txt", "aux.data",
                 "dir/COM1.py", "dir/lpt9", "COM¹.txt", "LPT²", "conin$", "a" * 256)
        for name in paths:
            with self.subTest(name=name):
                member = archive_entry(f"{PREFIX}/{name}")
                self.reject_archive(archive_bytes(extra=[member]))
        for name in ("/outside", "C:/outside", "../outside"):
            with self.subTest(absolute_member=name):
                self.reject_archive(archive_bytes(extra=[archive_entry(name)]))

    def test_git_and_reserved_metadata_paths_are_rejected(self):
        for name in (".git", ".GIT/config", "nested/.git/config", SOURCE, SOURCE.upper(), BOOTSTRAP,
                     BOOTSTRAP.upper(), SOURCE + "/child"):
            with self.subTest(name=name):
                self.reject_archive(archive_bytes(extra=[archive_entry(f"{PREFIX}/{name}")]))

    def test_duplicate_case_colliding_and_file_ancestor_paths_are_rejected(self):
        import tarfile

        groups = [
            [("same", b"first"), ("same", b"second")],
            [("Readme", b"first"), ("README", b"second")],
            [("Folder/first", b"first"), ("folder/second", b"second")],
            [("parent", b"file"), ("parent/child", b"child")],
            [("parent/child", b"child"), ("parent", b"file")],
        ]
        for group in groups:
            with self.subTest(group=group):
                entries = [archive_entry(f"{PREFIX}/{name}", content) for name, content in group]
                self.reject_archive(archive_bytes(extra=entries), "path_collision")
        directory = archive_entry(f"{PREFIX}/duplicate", entry_type=tarfile.DIRTYPE)
        self.reject_archive(archive_bytes(extra=[directory, directory]), "path_collision")
        symlink = archive_entry(f"{PREFIX}/parent", entry_type=tarfile.SYMTYPE, target="somewhere")
        child = archive_entry(f"{PREFIX}/parent/child")
        self.reject_archive(archive_bytes(extra=[symlink, child]), "path_collision")

    def test_explicit_directory_after_implicit_parent_is_accepted(self):
        import tarfile

        directory = archive_entry(f"{PREFIX}/skills", entry_type=tarfile.DIRTYPE)
        self.import_files(extra=[directory])
        self.assertEqual(self.samples.verify_project(self.project)["source_files"], len(FILES))

    def test_bootstrap_does_not_overwrite_a_source_ancestor(self):
        self.reject_archive(archive_bytes({".github/skills/local-draft": b"ordinary file"}),
                            "path_collision", self.template)

    def test_archive_must_be_complete_gzip_tar_with_the_pinned_root(self):
        truncated_tar = gzip.decompress(archive_bytes({"module.py": b"a" * 100}))[:1050]
        invalid = (b"", b"not an archive", gzip.compress(b"not tar"), archive_bytes()[:-20],
                   gzip.decompress(archive_bytes()), gzip.compress(truncated_tar),
                   archive_bytes(prefix="another-root"),
                   archive_bytes(extra=[archive_entry("another-root/file")]),
                   archive_bytes({}, [archive_entry(PREFIX)], include_root=False))
        for data in invalid:
            with self.subTest(length=len(data)):
                self.reject_archive(data, skill=self.template)

    def test_import_never_calls_tar_extraction_methods(self):
        with patch("tarfile.TarFile.extract", side_effect=AssertionError("Unsafe extraction")), patch(
            "tarfile.TarFile.extractall", side_effect=AssertionError("Unsafe extraction")
        ):
            self.import_files()

    def test_archive_truncated_at_a_file_boundary_is_not_complete(self):
        raw = gzip.decompress(archive_bytes({"skills/SKILL.md": b"existing"}))
        self.reject_archive(gzip.compress(raw[:1536]), "invalid_archive")

    def test_existing_skill_directory_matches_evaluator_discovery(self):
        import tarfile

        relative = ".claude/skills/malformed/SKILL.md"
        directory = archive_entry(f"{PREFIX}/{relative}", entry_type=tarfile.DIRTYPE)
        self.import_files({"README.md": b"source"}, extra=[directory])
        self.assertTrue((self.project / relative).is_dir())
        self.assertFalse((self.project / BOOTSTRAP).exists())
        self.assertEqual(self.samples.ensure_skill(self.project, self.template), {
            "action": "preserved", "path": relative,
        })


class SkillPreparationTests(SamplesTestCase):
    def test_add_skill_to_untracked_builtin_project(self):
        self.project.mkdir(parents=True)
        source = self.project / "module.py"
        source.write_bytes(b"original\r\n")
        result = self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(result, {"action": "added", "path": SKILL_PATH})
        self.assertEqual(source.read_bytes(), b"original\r\n")
        self.assertFalse((self.project / SOURCE).exists())
        metadata = self.manifest(BOOTSTRAP)
        self.assertEqual(metadata["schema_version"], 2)
        self.assertIn("source_manifest_sha256", metadata)
        self.assertIsNone(metadata["source_manifest_sha256"])
        self.assertEqual(self.samples.verify_project(self.project), {
            "source_files": 0, "license_files": 0, "omissions": 0, "bootstrap": "unvalidated_draft",
        })

    def test_repeated_preparation_preserves_all_bytes_and_provenance(self):
        self.import_files({"LICENSE": b"license"}, skill=self.template)
        before = self.snapshot()
        self.template.write_bytes(b"invalid replacement")
        for selected in (self.template, None, self.root / "missing"):
            with self.subTest(selected=selected):
                self.assertEqual(self.samples.ensure_skill(self.project, selected), {
                    "action": "preserved", "path": SKILL_PATH,
                })
                self.assertEqual(self.snapshot(), before)

    def test_existing_malformed_skills_in_each_recursive_root_are_preserved(self):
        self.project.mkdir(parents=True)
        for directory in (".github/skills", ".claude/skills", "skills"):
            with self.subTest(directory=directory):
                relative = directory + "/nested/bundle/SKILL.md"
                existing = self.project / relative
                existing.parent.mkdir(parents=True)
                existing.write_bytes(b"\xffmalformed, not UTF-8 or frontmatter\r\n")
                before = self.snapshot()
                result = self.samples.ensure_skill(self.project, self.root / "missing")
                self.assertEqual(result, {"action": "preserved", "path": relative})
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.project / BOOTSTRAP).exists())
                existing.unlink()

    def test_skill_outside_discovery_roots_does_not_satisfy_preparation(self):
        self.project.mkdir(parents=True)
        ignored = self.project / "docs/SKILL.md"
        ignored.parent.mkdir()
        ignored.write_bytes(UPSTREAM_SKILL)
        before = self.snapshot()
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.ensure_skill(self.project, None)
        self.assertEqual(caught.exception.code, "missing_skill")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.samples.ensure_skill(self.project, self.template)["action"], "added")
        self.assertEqual(ignored.read_bytes(), UPSTREAM_SKILL)

    def test_missing_or_directory_template_leaves_project_unchanged(self):
        self.project.mkdir(parents=True)
        for selected in (None, self.root / "missing", self.template.parent):
            with self.subTest(selected=selected), self.assertRaises(self.samples.SampleError):
                self.samples.ensure_skill(self.project, selected)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_invalid_template_metadata_or_encoding_leaves_no_files(self):
        self.project.mkdir(parents=True)
        templates = [
            b"\xff", b"No frontmatter", b"---\nname: draft\n---\nbody", b"---\ndescription: test\n---\nbody",
            b"---\nname: draft\ndescription: test\nbody", b"---\nname: ../escape\ndescription: test\n---\n",
            b"---\nname: Upper\ndescription: test\n---\n", b"---\nname: nul\ndescription: test\n---\n",
            b"---\nname: draft\nname: changed\ndescription: test\n---\n",
            b"---\nname: draft\ndescription: test\ndescription: changed\n---\n",
            b"---\nname: draft\ndescription: \n---\n", b"---\nname: draft\ndescription: [not, a, string]\n---\n",
            b"---\nname: draft\ndescription: null\n---\n", b"---\nname: draft\ndescription: true\n---\n",
            b"---\nname: draft\ndescription: &anchor value\n---\n",
            b"---\nname: draft\ndescription: invalid: mapping\n---\n",
            b"---\nname: " + b"a" * 65 + b"\ndescription: test\n---\n",
            b"---\nname: draft\ndescription: " + b"a" * 1025 + b"\n---\n",
        ]
        for content in templates:
            with self.subTest(content=content[:60]):
                self.template.write_bytes(content)
                with self.assertRaises(self.samples.SampleError) as caught:
                    self.samples.ensure_skill(self.project, self.template)
                self.assertEqual(caught.exception.code, "invalid_template")
                self.assertEqual(list(self.project.iterdir()), [])

    def test_simple_quoted_and_block_description_frontmatter_is_supported(self):
        templates = (
            b'---\nname: "local-draft"\ndescription: "Use when: editing code."\n---\nBody\n',
            b"---\nname: 'local-draft'\ndescription: 'Review the project''s code.'\n---\nBody\n",
            b"---\nname: local-draft\ndescription: >-\n  Work on the project\n  without making claims.\n---\nBody\n",
            b"---\nname: local-draft\ndescription: |\n  Work on this project.\n  Keep source intact.\n---\nBody\n",
        )
        for index, content in enumerate(templates):
            with self.subTest(index=index):
                project = self.root / f"project-{index}"
                project.mkdir()
                self.template.write_bytes(content)
                self.assertEqual(self.samples.ensure_skill(project, self.template)["action"], "added")
                self.assertEqual((project / SKILL_PATH).read_bytes(), content)

    def test_source_manifest_corruption_is_not_silently_preserved(self):
        self.import_files()
        (self.project / "module.py").write_bytes(b"modified")
        before = self.snapshot()
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(caught.exception.code, "source_mismatch")
        self.assertEqual(self.snapshot(), before)

    def test_failed_bootstrap_write_rolls_back_only_new_files(self):
        self.project.mkdir(parents=True)
        (self.project / "keep").write_bytes(b"preserve")
        before = self.snapshot()
        original = self.samples._write_file

        def fail_metadata(path, content, created):
            if path.name == BOOTSTRAP:
                raise OSError("private/path/token")
            return original(path, content, created)

        with patch.object(self.samples, "_write_file", side_effect=fail_metadata), self.assertRaises(
            self.samples.SampleError
        ) as caught:
            self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(caught.exception.code, "io_error")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(path.name for path in self.project.iterdir()), ["keep"])

    def test_preexisting_skill_directory_is_preserved_without_an_assessment(self):
        existing = self.project / "skills/malformed/SKILL.md"
        existing.mkdir(parents=True)
        self.assertEqual(self.samples.ensure_skill(self.project, self.template), {
            "action": "preserved", "path": "skills/malformed/SKILL.md",
        })
        self.assertTrue(existing.is_dir())
        self.assertFalse((self.project / BOOTSTRAP).exists())

    def test_omission_conflict_is_rejected_before_any_skill_write(self):
        self.project.mkdir(parents=True)
        self.write_manifest({
            "schema_version": 1, "repository": REPOSITORY, "commit": COMMIT, "export_policy": "ordinary_files_only",
            "files": {}, "license_files": [], "omissions": [{"path": SKILL_PATH, "reason": "symlink"}],
        })
        before = self.snapshot()
        with patch.object(self.samples, "_write_file") as write, self.assertRaises(self.samples.SampleError):
            self.samples.ensure_skill(self.project, self.template)
        write.assert_not_called()
        self.assertEqual(self.snapshot(), before)


class ResourceLimitTests(SamplesTestCase):
    def test_global_pax_dictionary_amplification_is_rejected_before_tarfile_parsing(self):
        import tarfile

        files = {"skills/SKILL.md": b"existing", **{f"source-{index}": b"a" for index in range(300)}}
        header = tarfile.TarInfo.create_pax_global_header({f"opaque_{index}": "x" for index in range(8000)})
        raw = header + gzip.decompress(archive_bytes(files))
        data = gzip.compress(raw)
        self.assertLess(len(data), 65536)
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples._preflight_tar(raw)
        self.assertEqual(caught.exception.code, "archive_limit")
        with patch("tarfile.open", side_effect=AssertionError("PAX parser must not run")) as open_archive:
            self.reject_archive(data, "archive_limit")
        open_archive.assert_not_called()

    def test_unrecognized_global_and_local_pax_keys_are_rejected_before_parsing(self):
        import tarfile

        source = archive_bytes({"skills/SKILL.md": b"existing"})
        global_header = tarfile.TarInfo.create_pax_global_header({"private.opaque": "ignored"})
        member, content = archive_entry(f"{PREFIX}/skills/SKILL.md", b"existing")
        member.pax_headers = {"private.opaque": "ignored"}
        local_source = archive_bytes({}, extra=[(member, content)])
        for data in (gzip.compress(global_header + gzip.decompress(source)), local_source):
            with self.subTest(size=len(data)), patch(
                "tarfile.open", side_effect=AssertionError("PAX parser must not run")
            ) as open_archive:
                self.reject_archive(data, "invalid_archive")
            open_archive.assert_not_called()

    def test_codeload_global_comment_and_pax_long_paths_still_import(self):
        import tarfile

        relative = "docs/" + "nested-segment/" * 8 + "ordinary.txt"
        files = {**FILES, relative: b"ordinary long-path source\r\n"}
        header = tarfile.TarInfo.create_pax_global_header({"comment": COMMIT})
        data = gzip.compress(header + gzip.decompress(archive_bytes(files)))
        with patch.object(self.samples, "fetch_archive", return_value=data):
            provenance = self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        self.assertEqual(provenance["files"][relative], digest(files[relative]))
        self.assertEqual((self.project / relative).read_bytes(), files[relative])
        self.assertEqual(self.samples.verify_project(self.project)["source_files"], len(files))

    def test_gnu_long_paths_still_import(self):
        import tarfile

        relative = "docs/" + "nested-segment/" * 8 + "ordinary.txt"
        files = {**FILES, relative: b"ordinary long-path source\r\n"}
        data = archive_bytes(files, archive_format=tarfile.GNU_FORMAT)
        with patch.object(self.samples, "fetch_archive", return_value=data):
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        self.assertEqual((self.project / relative).read_bytes(), files[relative])

    def test_pax_metadata_has_a_small_independent_byte_budget(self):
        member, content = archive_entry(f"{PREFIX}/skills/SKILL.md", b"existing")
        member.pax_headers = {"comment": "a" * 5000}
        data = archive_bytes({}, extra=[(member, content)])
        with patch("tarfile.open", side_effect=AssertionError("PAX parser must not run")) as open_archive:
            self.reject_archive(data, "archive_limit")
        open_archive.assert_not_called()

    def test_compressed_download_limit_is_checked_even_for_mocked_downloads(self):
        with patch.object(self.samples, "MAX_ARCHIVE_BYTES", 32):
            self.reject_archive(b"a" * 33, "archive_limit")

    def test_tar_expansion_is_bounded_before_member_parsing(self):
        with patch.object(self.samples, "MAX_TAR_BYTES", 1024):
            self.reject_archive(gzip.compress(b"\x00" * 2048), "archive_limit")

    def test_each_upstream_file_is_bounded(self):
        with patch.object(self.samples, "MAX_FILE_BYTES", 100):
            self.reject_archive(archive_bytes({**FILES, "large": b"a" * 101}), "file_limit")

    def test_generated_source_metadata_counts_toward_per_file_limit(self):
        with patch.object(self.samples, "MAX_FILE_BYTES", 200):
            self.reject_archive(archive_bytes({"skills/example/SKILL.md": b"malformed"}), "file_limit")

    def test_final_file_count_includes_source_and_bootstrap_metadata(self):
        with patch.object(self.samples, "MAX_FILES", 2):
            self.reject_archive(archive_bytes({"module.py": b"source"}), "project_limit", self.template)
        with patch.object(self.samples, "MAX_FILES", 2):
            self.import_files({"skills/example/SKILL.md": b"malformed"})
        self.assertEqual(len(self.snapshot()), 2)

    def test_final_total_size_includes_generated_metadata(self):
        with patch.object(self.samples, "MAX_TOTAL_BYTES", 100):
            self.reject_archive(archive_bytes({"skills/example/SKILL.md": b"malformed"}), "project_limit")

    def test_directory_and_omission_member_counts_are_bounded(self):
        import tarfile

        directories = [archive_entry(f"{PREFIX}/empty-{index}", entry_type=tarfile.DIRTYPE) for index in range(4)]
        with patch.object(self.samples, "MAX_MEMBERS", 3):
            self.reject_archive(archive_bytes({}, directories), "archive_limit", self.template)

    def test_hidden_pax_metadata_is_bounded_before_tarfile_processes_it(self):
        member, content = archive_entry(f"{PREFIX}/skills/SKILL.md", b"existing")
        member.pax_headers = {"comment": "a" * 513}
        data = archive_bytes({}, extra=[(member, content)])
        with patch.object(self.samples, "MAX_FILE_BYTES", 512):
            self.reject_archive(data, "file_limit")

    def test_hidden_pax_headers_count_toward_archive_member_limit(self):
        import tarfile

        raw = gzip.decompress(archive_bytes({"skills/SKILL.md": b"existing"}))
        header = tarfile.TarInfo.create_pax_global_header({"comment": "bounded"})
        data = gzip.compress(raw[:512] + header * 4 + raw[512:])
        with patch.object(self.samples, "MAX_MEMBERS", 5):
            self.reject_archive(data, "archive_limit")

    def test_paths_are_bounded_by_length_and_depth(self):
        for name in ("nested/" * 65 + "file", "long" * 64 + "/file"):
            with self.subTest(name=name):
                self.reject_archive(archive_bytes(extra=[archive_entry(f"{PREFIX}/{name}")]))

    def test_ensure_skill_enforces_budget_before_writing(self):
        self.project.mkdir(parents=True)
        (self.project / "existing").write_bytes(b"source")
        before = self.snapshot()
        with patch.object(self.samples, "MAX_FILES", 2), self.assertRaises(self.samples.SampleError) as caught:
            self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(caught.exception.code, "project_limit")
        self.assertEqual(self.snapshot(), before)
        with patch.object(self.samples, "MAX_FILE_BYTES", len(TEMPLATE) - 1), self.assertRaises(
            self.samples.SampleError
        ) as caught:
            self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(caught.exception.code, "file_limit")
        self.assertEqual(self.snapshot(), before)


class ManifestVerificationTests(SamplesTestCase):
    def test_import_cannot_downgrade_to_bootstrap_only_after_source_manifest_deletion(self):
        self.import_files({"module.py": b"original source"}, skill=self.template)
        (self.project / SOURCE).unlink()
        (self.project / "module.py").write_bytes(b"private modified source")
        before = self.snapshot()
        for operation in (lambda: self.samples.verify_project(self.project),
                          lambda: self.samples.ensure_skill(self.project, self.template)):
            with self.subTest(operation=operation), self.assertRaises(self.samples.SampleError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "missing_manifest")
            self.assertEqual(self.snapshot(), before)

    def test_import_bootstrap_binds_exact_source_manifest_bytes(self):
        self.import_files({"module.py": b"original source"}, skill=self.template)
        provenance = self.manifest()
        (self.project / "module.py").write_bytes(b"private modified source")
        provenance["files"]["module.py"] = digest(b"private modified source")
        self.write_manifest(provenance)
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.verify_project(self.project)
        self.assertEqual(caught.exception.code, "source_mismatch")

    def test_legacy_bootstrap_requires_explicit_metadata_migration(self):
        self.import_files({"module.py": b"original source"}, skill=self.template)
        legacy = self.manifest(BOOTSTRAP)
        legacy["schema_version"] = 1
        legacy.pop("source_manifest_sha256", None)
        self.write_manifest(legacy, BOOTSTRAP)
        before = self.snapshot()
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(caught.exception.code, "invalid_manifest")
        self.assertEqual(self.snapshot(), before)

    def test_bootstrap_source_binding_is_required_and_cannot_claim_builtin_with_source(self):
        self.import_files({"module.py": b"original source"}, skill=self.template)
        original = self.manifest(BOOTSTRAP)
        for value in (None, True, {}, "not a hash"):
            with self.subTest(value=value):
                self.write_manifest({**original, "source_manifest_sha256": value}, BOOTSTRAP)
                with self.assertRaises(self.samples.SampleError) as caught:
                    self.samples.verify_project(self.project)
                self.assertEqual(caught.exception.code, "invalid_manifest")
        missing = dict(original)
        missing.pop("source_manifest_sha256", None)
        self.write_manifest(missing, BOOTSTRAP)
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.verify_project(self.project)
        self.assertEqual(caught.exception.code, "invalid_manifest")

    def test_builtin_bootstrap_migration_does_not_invent_source_provenance(self):
        self.project.mkdir(parents=True)
        self.samples.ensure_skill(self.project, self.template)
        metadata = self.manifest(BOOTSTRAP)
        metadata["schema_version"] = 1
        metadata.pop("source_manifest_sha256", None)
        self.write_manifest(metadata, BOOTSTRAP)
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.verify_project(self.project)
        self.assertEqual(caught.exception.code, "invalid_manifest")
        metadata["schema_version"] = 2
        metadata["source_manifest_sha256"] = None
        self.write_manifest(metadata, BOOTSTRAP)
        self.assertEqual(self.samples.verify_project(self.project)["source_files"], 0)
        self.assertFalse((self.project / SOURCE).exists())

    def test_source_modified_missing_or_unrecorded_files_are_rejected(self):
        self.import_files()
        original = (self.project / "module.py").read_bytes()
        for modification in ("changed", "missing", "extra", "unrecorded"):
            with self.subTest(modification=modification):
                provenance = self.manifest()
                if modification == "changed":
                    (self.project / "module.py").write_bytes(b"private modified contents")
                elif modification == "missing":
                    (self.project / "module.py").unlink()
                elif modification == "extra":
                    (self.project / "extra.py").write_bytes(b"extra")
                else:
                    del provenance["files"]["module.py"]
                    self.write_manifest(provenance)
                with self.assertRaises(self.samples.SampleError) as caught:
                    self.samples.verify_project(self.project)
                self.assertEqual(caught.exception.code, "source_mismatch")
                self.assertNotIn("private", str(caught.exception))
                (self.project / "module.py").write_bytes(original)
                (self.project / "extra.py").unlink(missing_ok=True)
                provenance["files"]["module.py"] = digest(original)
                self.write_manifest(provenance)

    def test_corrupt_json_and_duplicate_metadata_keys_are_rejected(self):
        self.import_files()
        before = (self.project / SOURCE).read_bytes()
        invalid = (b"not JSON", b"[]", b"{}", b'{"schema_version":1,"schema_version":1}',
                   b"[" * 1500 + b"]" * 1500, before.replace(b'"schema_version": 1', b'"schema_version": NaN'))
        for content in invalid:
            with self.subTest(content=content[:60]):
                (self.project / SOURCE).write_bytes(content)
                with self.assertRaises(self.samples.SampleError) as caught:
                    self.samples.verify_project(self.project)
                self.assertEqual(caught.exception.code, "invalid_manifest")
        (self.project / SOURCE).write_bytes(before)

    def test_source_metadata_lies_and_unsafe_paths_are_rejected(self):
        self.import_files()
        original = self.manifest()
        mutations = (
            ("schema_version", True), ("schema_version", 2), ("repository", "https://private.invalid/token"),
            ("commit", "main"), ("export_policy", "executed_hooks"), ("license_files", []),
            ("license_files", ["LICENSE", "LICENSE"]), ("extra", "private field"),
            ("omissions", [{"path": "LICENSE", "reason": "symlink"}]),
            ("omissions", [{"path": "missing", "reason": "executed"}]),
            ("omissions", [{"path": "../private", "reason": "symlink"}]),
            ("files", {"../private": "a" * 64}), ("files", {"C:/private": "a" * 64}),
            ("files", {"NUL.txt": "a" * 64}), ("files", {SOURCE: "a" * 64}),
            ("files", {"same": "a" * 64, "SAME": "b" * 64}),
            ("files", {"parent": "a" * 64, "parent/child": "b" * 64}),
            ("files", {"module.py": "not a digest"}),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.write_manifest({**original, key: value})
                with self.assertRaises(self.samples.SampleError) as caught:
                    self.samples.verify_project(self.project)
                self.assertNotIn("private", str(caught.exception))
                self.assertLess(len(str(caught.exception)), 64)
        self.write_manifest(original)

    def test_omitted_path_cannot_reappear_as_a_file_or_directory(self):
        import tarfile

        omission = archive_entry(f"{PREFIX}/omitted", entry_type=tarfile.SYMTYPE, target="private")
        self.import_files(extra=[omission])
        (self.project / "omitted").mkdir()
        with self.assertRaises(self.samples.SampleError):
            self.samples.verify_project(self.project)

    def test_bootstrap_content_and_metadata_are_verified(self):
        self.import_files({"module.py": b"source"}, skill=self.template)
        original = self.manifest(BOOTSTRAP)
        installed = self.project / SKILL_PATH
        content = installed.read_bytes()
        installed.write_bytes(b"private corruption")
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.verify_project(self.project)
        self.assertEqual(caught.exception.code, "bootstrap_mismatch")
        installed.write_bytes(content)
        for key, value in (("schema_version", True), ("state", "validated"), ("normalization", "none"),
                           ("path", "../private"), ("path", "module.py"), ("sha256", "bad"),
                           ("extra", "private content")):
            with self.subTest(key=key):
                self.write_manifest({**original, key: value}, BOOTSTRAP)
                with self.assertRaises(self.samples.SampleError):
                    self.samples.verify_project(self.project)
        self.write_manifest(original, BOOTSTRAP)
        installed.unlink()
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.verify_project(self.project)
        self.assertEqual(caught.exception.code, "bootstrap_mismatch")

    def test_bootstrap_cannot_lie_about_normalization_name_or_original_source(self):
        self.import_files({"module.py": b"source"}, skill=self.template)
        original = self.manifest(BOOTSTRAP)
        installed = self.project / SKILL_PATH
        normalized = installed.read_bytes()
        for content in (TEMPLATE, normalized.replace(b"local-draft", b"another-name"), b"malformed"):
            with self.subTest(content=content[:40]):
                installed.write_bytes(content)
                self.write_manifest({**original, "sha256": digest(content)}, BOOTSTRAP)
                with self.assertRaises(self.samples.SampleError):
                    self.samples.verify_project(self.project)
        installed.write_bytes(normalized)
        self.write_manifest(original, BOOTSTRAP)
        provenance = self.manifest()
        provenance["files"][SKILL_PATH] = digest(normalized)
        self.write_manifest(provenance)
        with self.assertRaises(self.samples.SampleError):
            self.samples.verify_project(self.project)

    def test_verify_requires_at_least_one_manifest(self):
        self.project.mkdir(parents=True)
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.verify_project(self.project)
        self.assertEqual(caught.exception.code, "missing_manifest")

    def test_source_manifest_directory_cannot_masquerade_as_absent_provenance(self):
        self.import_files({"module.py": b"source"}, skill=self.template)
        (self.project / SOURCE).unlink()
        (self.project / SOURCE).mkdir()
        with self.assertRaises(self.samples.SampleError):
            self.samples.verify_project(self.project)

    def test_verify_returns_only_bounded_counts_and_state(self):
        self.import_files({"module.py": b"private source body"}, skill=self.template)
        summary = json.dumps(self.samples.verify_project(self.project))
        self.assertLess(len(summary), 160)
        self.assertNotIn("Private draft", summary)
        self.assertNotIn("private source", summary)
        self.assertNotIn(str(self.root), summary)


class CatalogCapacityTests(SamplesTestCase):
    def populate(self, count):
        self.project.parent.mkdir()
        for index in range(count):
            (self.project.parent / f"existing-{index:02d}").mkdir()

    def test_fifty_existing_projects_reject_import_before_download(self):
        self.populate(50)
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError) as caught:
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        self.assertEqual(caught.exception.code, "project_limit")
        fetch.assert_not_called()
        self.assertFalse(self.project.exists())
        self.assertEqual(len(list(self.project.parent.iterdir())), 50)

    def test_forty_nine_projects_allow_exactly_the_fiftieth(self):
        self.populate(49)
        self.import_files()
        self.assertEqual(len(list(self.project.parent.iterdir())), 50)

    def test_ordinary_readme_is_exempt_from_the_catalog_project_limit(self):
        self.populate(49)
        readme = self.project.parent / "README.md"
        readme.write_bytes(b"project catalog documentation\r\n")
        self.import_files()
        self.assertEqual(readme.read_bytes(), b"project catalog documentation\r\n")
        self.assertEqual(sum(path.is_dir() for path in self.project.parent.iterdir()), 50)
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError) as caught:
            self.samples.import_project(self.root, "another", REPOSITORY, COMMIT)
        self.assertEqual(caught.exception.code, "project_limit")
        fetch.assert_not_called()

    def test_readme_directory_is_not_exempt(self):
        self.populate(49)
        (self.project.parent / "README.md").mkdir()
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError) as caught:
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        self.assertEqual(caught.exception.code, "project_limit")
        fetch.assert_not_called()
        self.assertFalse(self.project.exists())

    def test_capacity_is_rechecked_before_publication_after_download(self):
        self.populate(49)

        def fill_last_slot(repository, commit):
            (self.project.parent / "concurrent-project").mkdir()
            return archive_bytes()

        with patch.object(self.samples, "fetch_archive", side_effect=fill_last_slot) as fetch, self.assertRaises(
            self.samples.SampleError
        ) as caught:
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        self.assertEqual(caught.exception.code, "project_limit")
        fetch.assert_called_once_with(REPOSITORY, COMMIT)
        self.assertFalse(self.project.exists())
        self.assertTrue((self.project.parent / "concurrent-project").is_dir())
        self.assertEqual(len(list(self.project.parent.iterdir())), 50)


class FilesystemSafetyTests(SamplesTestCase):
    def test_import_rejects_a_symlinked_projects_directory(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.make_symlink(self.root / "projects", outside, directory=True)
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError):
            self.samples.import_project(self.root, "fixture", REPOSITORY, COMMIT)
        fetch.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_import_rejects_a_symlinked_root_ancestor(self):
        linked = self.root / "linked"
        self.make_symlink(linked, self.root, directory=True)
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError):
            self.samples.import_project(linked, "fixture", REPOSITORY, COMMIT)
        fetch.assert_not_called()

    def test_selected_template_cannot_traverse_symlinks(self):
        self.project.mkdir(parents=True)
        linked = self.root / "linked-template"
        self.make_symlink(linked, self.template.parent, directory=True)
        with self.assertRaises(self.samples.SampleError) as caught:
            self.samples.ensure_skill(self.project, linked / "SKILL.md")
        self.assertEqual(caught.exception.code, "unsafe_path")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_symlinked_existing_skill_is_not_followed_or_overwritten(self):
        existing = self.project / "skills/nested/SKILL.md"
        existing.parent.mkdir(parents=True)
        self.make_symlink(existing, self.template)
        with self.assertRaises(self.samples.SampleError):
            self.samples.ensure_skill(self.project, self.template)
        self.assertTrue(existing.is_symlink())
        self.assertEqual(self.template.read_bytes(), TEMPLATE)

    def test_verify_rejects_symlinked_source_and_metadata(self):
        self.import_files()
        for name in ("module.py", SOURCE):
            with self.subTest(name=name):
                path = self.project / name
                content = path.read_bytes()
                outside = self.root / "outside-file"
                outside.write_bytes(content)
                path.unlink()
                self.make_symlink(path, outside)
                with self.assertRaises(self.samples.SampleError):
                    self.samples.verify_project(self.project)
                path.unlink()
                path.write_bytes(content)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junctions_are_rejected_for_root_template_and_project_tree(self):
        import _winapi

        outside = self.root / "outside"
        outside.mkdir()
        junction = self.root / "junction"
        _winapi.CreateJunction(str(outside), str(junction))
        self.addCleanup(junction.rmdir)
        with patch.object(self.samples, "fetch_archive") as fetch, self.assertRaises(self.samples.SampleError):
            self.samples.import_project(junction, "fixture", REPOSITORY, COMMIT)
        fetch.assert_not_called()
        self.project.mkdir(parents=True)
        (outside / "SKILL.md").write_bytes(TEMPLATE)
        with self.assertRaises(self.samples.SampleError):
            self.samples.ensure_skill(self.project, junction / "SKILL.md")
        nested = self.project / "skills"
        _winapi.CreateJunction(str(outside), str(nested))
        self.addCleanup(nested.rmdir)
        with self.assertRaises(self.samples.SampleError):
            self.samples.ensure_skill(self.project, self.template)
        self.assertEqual(list(outside.iterdir()), [outside / "SKILL.md"])

    def test_parent_traversal_in_local_inputs_is_rejected(self):
        self.project.mkdir(parents=True)
        with self.assertRaises(self.samples.SampleError):
            self.samples.ensure_skill(self.project, self.template.parent / ".." / "templates/SKILL.md")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_failed_import_write_rolls_back_its_new_destination(self):
        original = self.samples._write_file

        def fail_source_metadata(path, content, created):
            if path.name == SOURCE:
                raise OSError("private/path/token")
            return original(path, content, created)

        with patch.object(self.samples, "_write_file", side_effect=fail_source_metadata):
            self.reject_archive(archive_bytes(), "io_error")
        self.assertEqual(self.template.read_bytes(), TEMPLATE)


class DownloadAndCliTests(SamplesTestCase):
    def response(self, content=b"archive"):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = f"https://codeload.github.com/{REPOSITORY}/tar.gz/{COMMIT}"
        response.headers = {"Content-Length": str(len(content))}
        response.read.return_value = content
        return response

    def test_fetch_uses_only_the_pinned_public_https_endpoint_and_bounded_reads(self):
        response = self.response()
        with patch.object(self.samples.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            self.assertEqual(self.samples.fetch_archive(REPOSITORY, COMMIT), b"archive")
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, response.geturl.return_value)
        self.assertEqual(request.get_method(), "GET")
        self.assertFalse(request.has_header("Authorization"))
        self.assertGreater(build.return_value.open.call_args.kwargs["timeout"], 0)
        response.read.assert_called_once_with(self.samples.MAX_ARCHIVE_BYTES + 1)

    def test_fetch_rejects_large_declared_and_actual_downloads(self):
        for declared in (True, False):
            with self.subTest(declared=declared):
                response = self.response(b"a" * 33)
                if not declared:
                    response.headers = {}
                with patch.object(self.samples, "MAX_ARCHIVE_BYTES", 32), patch.object(
                    self.samples.urllib.request, "build_opener"
                ) as build:
                    build.return_value.open.return_value = response
                    with self.assertRaises(self.samples.SampleError) as caught:
                        self.samples.fetch_archive(REPOSITORY, COMMIT)
                self.assertEqual(caught.exception.code, "archive_limit")
                if declared:
                    response.read.assert_not_called()

    def test_fetch_fails_closed_on_redirect_status_or_network_errors(self):
        for failure in ("redirect", "status", "network"):
            with self.subTest(failure=failure), patch.object(self.samples.urllib.request, "build_opener") as build:
                response = self.response()
                if failure == "redirect":
                    response.geturl.return_value = "https://private.invalid/token"
                elif failure == "status":
                    response.status = 404
                else:
                    build.return_value.open.side_effect = urllib.error.URLError("private/path/token")
                build.return_value.open.return_value = response
                with self.assertRaises(self.samples.SampleError) as caught:
                    self.samples.fetch_archive(REPOSITORY, COMMIT)
                self.assertEqual(caught.exception.code, "download_failed")
                self.assertNotIn("private", str(caught.exception))

    def test_redirect_handler_never_follows_another_url(self):
        with self.assertRaises(self.samples.SampleError):
            self.samples._NoRedirect().redirect_request(None, None, 302, "private", {}, "https://private.invalid")

    def test_fetch_validates_identity_before_constructing_a_network_client(self):
        with patch.object(self.samples.urllib.request, "build_opener") as build:
            for repository, commit in (("https://private.invalid/token", COMMIT), (REPOSITORY, "main")):
                with self.subTest(repository=repository), self.assertRaises(self.samples.SampleError):
                    self.samples.fetch_archive(repository, commit)
        build.assert_not_called()

    def run_cli(self, arguments):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = self.samples.main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def test_cli_import_verify_and_preserve_return_success(self):
        prefix = ["--root", str(self.root)]
        with patch.object(self.samples, "fetch_archive", return_value=archive_bytes()):
            status, output, errors = self.run_cli(prefix + [
                "import", "--project", "fixture", "--repository", REPOSITORY, "--commit", COMMIT,
            ])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(json.loads(output)["action"], "imported")
        status, output, errors = self.run_cli(prefix + ["verify", "--project", "fixture"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["source_files"], len(FILES))
        self.assertEqual(errors, "")
        status, output, errors = self.run_cli(prefix + [
            "add-skill", "--project", "fixture", "--skill", "templates/SKILL.md",
        ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["action"], "preserved")
        self.assertEqual(errors, "")

    def test_cli_add_skill_resolves_template_relative_to_root(self):
        self.project.mkdir(parents=True)
        status, output, errors = self.run_cli([
            "--root", str(self.root), "add-skill", "--project", "fixture", "--skill", "templates/SKILL.md",
        ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), {"action": "added", "path": SKILL_PATH})
        self.assertEqual(errors, "")

    def test_cli_default_root_is_the_repository_above_the_app(self):
        self.import_files()
        with patch.object(self.samples, "__file__", str(self.root / "skillops/project_samples.py")):
            status, output, errors = self.run_cli(["verify", "--project", "fixture"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["source_files"], len(FILES))
        self.assertEqual(errors, "")

    def test_cli_errors_have_nonzero_status_without_private_diagnostics(self):
        arguments = (
            ["verify", "--project", "../private"], ["verify", "--project", "missing"],
            ["add-skill", "--project", "fixture", "--skill", "../private/SKILL.md"],
            ["add-skill", "--project", "fixture", "--skill", str(self.template)],
            ["add-skill", "--project", "fixture", "--skill", "templates\\SKILL.md"],
            ["import", "--project", "fixture", "--repository", "private.invalid/token", "--commit", "main"],
            ["verify"], ["private-command"],
        )
        for command in arguments:
            with self.subTest(command=command), patch.object(self.samples, "fetch_archive") as fetch:
                status, output, errors = self.run_cli(["--root", str(self.root), *command])
                self.assertEqual(status, 2)
                self.assertEqual(output, "")
                self.assertLess(len(errors), 64)
                self.assertNotIn("private", errors)
                self.assertNotIn(str(self.root), errors)
                fetch.assert_not_called()

    def test_cli_verification_failure_does_not_print_source_or_manifest_contents(self):
        self.import_files()
        (self.project / SOURCE).write_bytes(b"private token and invalid JSON")
        status, output, errors = self.run_cli(["--root", str(self.root), "verify", "--project", "fixture"])
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        self.assertEqual(errors, "invalid_manifest\n")

    def test_unknown_error_codes_are_sanitized(self):
        error = self.samples.SampleError("private/path/token")
        self.assertEqual(error.code, "sample_error")
        self.assertEqual(str(error), "sample_error")

    def test_module_is_standalone_without_linux_or_evaluator_imports(self):
        original = __import__
        forbidden = {"fcntl", "evaluation", "repositories", "project_evaluation", "project_results", "copilot_runtime"}

        def portable_import(name, *arguments, **options):
            if name.split(".")[0] in forbidden:
                raise AssertionError("Nonportable or evaluator dependency")
            return original(name, *arguments, **options)

        with patch("builtins.__import__", side_effect=portable_import):
            runpy.run_path(self.samples.__file__, run_name="samples_portability_test")


if __name__ == "__main__":
    unittest.main()
