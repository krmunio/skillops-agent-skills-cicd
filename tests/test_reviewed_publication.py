import importlib
import importlib.util
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import project_results as results
from copilot_runtime import RuntimeFailure
import test_project_results
from publication_fixtures import dashboard_fixture


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "publication-candidates/test-packet"


class ReviewedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "code"
        self.root.mkdir()
        dashboard_fixture(self.root)
        shutil.copytree(ROOT / "eval", self.root / "eval")
        for path in ROOT.glob("*.py"):
            shutil.copyfile(path, self.root / path.name)
        (self.root / "projects/sample_repo").mkdir(parents=True)
        (self.root / "projects/sample_repo/example.py").write_text("value = 1\n")
        (self.root / "project_profiles.json").write_text('{"schema_version":1,"projects":{}}')
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.code_sha = self.commit()
        self.bundle = self.root / BUNDLE
        self.report = test_project_results.ProjectResultsTests().fixture()
        results.store(self.bundle / "results", self.report)
        self.payload_path = "results/sample_repo/123-1/report.json"
        self.manifest = {
            "schema_version": 1, "producer_commit": "b" * 40,
            "files": [{"path": self.payload_path, "size": len(results.encoded(self.report)),
                       "sha256": sha256(results.encoded(self.report)).hexdigest()}],
        }
        (self.root / "UNTRUSTED.py").write_text("raise RuntimeError('Data code must never run')\n")
        self.save_packet()
        self.existing = self.base / "existing"
        self.output = self.base / "output"

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture", "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def save_packet(self):
        raw = results.encoded(self.manifest)
        (self.bundle / "manifest.json").write_bytes(raw)
        self.digest = sha256(raw).hexdigest()
        self.data_sha = self.commit()
        self.git("checkout", "-q", "--detach", self.code_sha)

    def change_packet(self, mutate):
        self.git("checkout", "-q", "--detach", self.data_sha)
        mutate()
        self.save_packet()

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("reviewed_publication"),
                             "Independent reviewed-result publisher is not implemented")
        return importlib.import_module("reviewed_publication")

    def prepare(self, **changes):
        options = dict(data_sha=self.data_sha, code_sha=self.code_sha, bundle=BUNDLE,
                       manifest_sha=self.digest, existing=self.existing, output=self.output, stage=False)
        options.update(changes)
        return self.module().prepare(self.root, **options)

    def test_default_is_read_only_validation_of_pinned_data_not_its_code(self):
        value = self.prepare()
        self.assertEqual(value["stages"]["input_validation"], "complete")
        self.assertEqual(value["stages"]["local_storage"], "not_requested")
        for name in ("result_branch_storage", "deployment", "public_url"):
            self.assertEqual(value["stages"][name], "not_requested")
        self.assertEqual(value["code_sha"], self.code_sha)
        self.assertEqual(value["data_sha"], self.data_sha)
        self.assertEqual(value["model_calls"], 0)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.existing.exists())
        self.assertFalse((self.root / "UNTRUSTED.py").exists())

    def test_staging_reuses_official_build_and_preserves_payload_bytes(self):
        value = self.prepare(stage=True)
        self.assertEqual(value["stages"]["local_storage"], "complete")
        self.assertEqual((self.output / self.payload_path).read_bytes(), results.encoded(self.report))
        self.assertEqual((self.output / "site" / self.payload_path).read_bytes(), results.encoded(self.report))
        self.assertTrue((self.output / "site/index.html").is_file())
        self.assertTrue((self.output / "receipt.json").is_file())
        self.assertEqual(value["stages"]["deployment"], "not_requested")

    def test_wrong_manifest_hash_and_mutable_revision_are_rejected(self):
        for change in ({"manifest_sha": "0" * 64}, {"data_sha": "main"}, {"code_sha": self.data_sha},
                       {"bundle": "../private"}, {"bundle": BUNDLE + "/../private"}):
            with self.subTest(change=change), self.assertRaises(RuntimeFailure):
                self.prepare(**change)
        self.assertFalse(self.output.exists())

    def test_payload_hash_mismatch_is_rejected_before_any_output(self):
        self.change_packet(lambda: (self.bundle / self.payload_path).write_bytes(b"{}\n"))
        with self.assertRaises(RuntimeFailure):
            self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_unlisted_private_file_and_symlink_are_rejected(self):
        for kind in ("private", "symlink"):
            def change():
                path = self.bundle / "private.json"
                path.unlink(missing_ok=True)
                if kind == "private":
                    path.write_text('{"request":"DO_NOT_PUBLISH"}')
                else:
                    path.symlink_to("/etc/passwd")
            self.change_packet(change)
            with self.subTest(kind=kind), self.assertRaises(RuntimeFailure):
                self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_manifest_cannot_authorize_private_fields_or_non_public_files(self):
        def change():
            self.report["raw_request"] = "DO_NOT_PUBLISH"
            raw = results.encoded(self.report)
            (self.bundle / self.payload_path).write_bytes(raw)
            self.manifest["files"][0].update(size=len(raw), sha256=sha256(raw).hexdigest())
        self.change_packet(change)
        with self.assertRaises(RuntimeFailure):
            self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_transitive_assessment_reference_must_be_complete(self):
        from test_skill_assessments import fixture
        report, _, assessment = fixture()
        def change():
            for name, value in (("report.json", report), ("skill-assessments.json", assessment)):
                path = "results/sample_repo/123-1/" + name
                raw = results.encoded(value)
                (self.bundle / path).write_bytes(raw)
                self.manifest["files"] = [x for x in self.manifest["files"] if x["path"] != path]
                self.manifest["files"].append({"path": path, "size": len(raw), "sha256": sha256(raw).hexdigest()})
            self.manifest["files"].sort(key=lambda x: x["path"])
        self.change_packet(change)
        with self.assertRaises(RuntimeFailure):
            self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_size_and_file_count_limits_apply_before_staging(self):
        m = self.module()
        for limit in ("MAX_TOTAL_BYTES", "MAX_FILES"):
            with self.subTest(limit=limit), patch.object(m, limit, 0), self.assertRaises(RuntimeFailure):
                self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_immutable_conflict_does_not_modify_existing_or_create_output(self):
        self.report["execution"]["reason_code"] = "missing_auth"
        path = results.store(self.existing, self.report)
        before = path.read_bytes()
        with self.assertRaises(RuntimeFailure) as raised:
            self.prepare(stage=True)
        self.assertEqual(raised.exception.code, "immutable_conflict")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_output_must_not_be_inside_existing_results(self):
        results.store(self.existing, self.report)
        with self.assertRaises(RuntimeFailure):
            self.prepare(stage=True, output=self.existing / "nested-output")
        self.assertFalse((self.existing / "nested-output").exists())

    def test_existing_input_file_is_not_treated_as_an_empty_history(self):
        self.existing.write_text("{}")
        with self.assertRaises(RuntimeFailure):
            self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_existing_results_readme_is_metadata_not_a_public_payload(self):
        results.store(self.existing, self.report)
        readme = self.existing / "README.md"
        readme.write_text("Existing data branch maintenance instructions; never execute or export.\n")
        before = readme.read_bytes()
        self.prepare(stage=True)
        self.assertEqual(readme.read_bytes(), before)
        self.assertFalse((self.output / "results/README.md").exists())
        self.assertFalse((self.output / "site/results/README.md").exists())

    def test_dirty_publisher_code_is_not_the_pinned_code(self):
        (self.root / "projects/sample_repo/example.py").write_text("changed = True\n")
        with self.assertRaises(RuntimeFailure):
            self.prepare(stage=True)
        self.assertFalse(self.output.exists())

    def test_untracked_publisher_cannot_claim_an_older_code_commit(self):
        self.git("rm", "reviewed_publication.py")
        old_code = self.commit()
        shutil.copyfile(ROOT / "reviewed_publication.py", self.root / "reviewed_publication.py")
        with self.assertRaises(RuntimeFailure):
            self.prepare(code_sha=old_code)

    def test_same_script_with_different_pinned_validator_bytes_is_rejected(self):
        path = self.root / "project_results.py"
        path.write_text(path.read_text() + "\n# Different validator revision.\n")
        other_code = self.commit()
        with self.assertRaises(RuntimeFailure) as raised:
            self.prepare(code_sha=other_code)
        self.assertEqual(raised.exception.code, "publisher_commit_mismatch")

    def test_cli_defaults_to_validation_and_reports_failure_without_success(self):
        m = self.module()
        args = ["--root", str(self.root), "--data-sha", self.data_sha, "--code-sha", self.code_sha,
                "--bundle", BUNDLE, "--manifest-sha", self.digest, "--existing", str(self.existing)]
        with patch.object(m, "__file__", str(self.root / "reviewed_publication.py")), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(args), 0)
        self.assertEqual(json.loads(out.getvalue())["stages"]["input_validation"], "complete")
        with patch.object(m, "__file__", str(self.root / "reviewed_publication.py")), \
                redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([*args, "--manifest-sha", "0" * 64]), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue())["status"], "blocked")
        self.assertEqual(json.loads(err.getvalue())["stages"]["input_validation"], "failed")

    def test_storage_failure_retains_successful_input_validation_status(self):
        m = self.module()
        args = ["--root", str(self.root), "--data-sha", self.data_sha, "--code-sha", self.code_sha,
                "--bundle", BUNDLE, "--manifest-sha", self.digest, "--mode", "stage",
                "--output", str(self.output)]
        with patch.object(m, "__file__", str(self.root / "reviewed_publication.py")), \
                patch.object(m.shutil, "copytree", side_effect=OSError("disk unavailable")), \
                redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(args), 2)
        stages = json.loads(err.getvalue())["stages"]
        self.assertEqual(stages["input_validation"], "complete")
        self.assertEqual(stages["local_storage"], "failed")
        self.assertEqual(stages["result_branch_storage"], "not_requested")

    def test_cli_cannot_claim_a_different_repository_as_its_code(self):
        m = self.module()
        args = ["--root", str(self.root), "--data-sha", self.data_sha, "--code-sha", self.code_sha,
                "--bundle", BUNDLE, "--manifest-sha", self.digest]
        with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(args), 2)
        self.assertEqual(json.loads(err.getvalue())["code"], "publisher_root_mismatch")

    def test_actual_cli_uses_the_pinned_checkout_without_model_credentials(self):
        run = subprocess.run(
            [sys.executable, str(self.root / "reviewed_publication.py"), "--code-sha", self.code_sha,
             "--data-sha", self.data_sha, "--bundle", BUNDLE, "--manifest-sha", self.digest],
            cwd=self.root, env={"PATH": "/usr/bin:/bin", "SKILLOPS_LIVE_EVALUATION_ENABLED": "false"},
            capture_output=True, text=True, timeout=30)
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        value = json.loads(run.stdout)
        self.assertEqual(value["stages"]["input_validation"], "complete")
        self.assertEqual(value["model_calls"], 0)

    def test_commit_preserves_existing_bytes_without_checkout_filters_or_remote_write(self):
        m = self.module()
        self.prepare(stage=True)
        before = self.git("rev-parse", "HEAD")
        parent = self.code_sha
        committed = m.commit_results(self.root, self.output / "results", parent)
        self.assertNotEqual(committed, parent)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertFalse((self.root / "results").exists())
        raw = subprocess.run(["git", "-C", str(self.root), "cat-file", "blob",
                              committed + ":results/sample_repo/123-1/report.json"],
                             check=True, capture_output=True).stdout
        self.assertEqual(raw, results.encoded(self.report))
        self.assertEqual(m.commit_results(self.root, self.output / "results", committed), committed)

    def test_deployment_staging_requires_packet_to_be_durable_already(self):
        with self.assertRaises(RuntimeFailure) as raised:
            self.prepare(stage=True, require_stored=True)
        self.assertEqual(raised.exception.code, "result_not_stored")
        self.assertFalse(self.output.exists())
        results.store(self.existing, self.report)
        self.assertEqual(self.prepare(stage=True, require_stored=True)["stages"]["local_storage"], "complete")

    def test_existing_git_results_are_data_only_and_have_exact_bytes(self):
        m = self.module()
        self.prepare(stage=True)
        durable = m.commit_results(self.root, self.output / "results", self.code_sha)
        value = self.prepare(existing=None, existing_sha=durable, output=None, require_stored=True)
        self.assertEqual(value["stages"]["input_validation"], "complete")
        self.assertFalse((self.root / "results").exists())

    def test_no_runtime_provider_or_approval_function_can_be_called(self):
        from copilot_runtime import CopilotRuntime
        import project_evaluation
        import skill_approvals
        with patch.object(CopilotRuntime, "invoke", side_effect=AssertionError("model call")), \
                patch.object(project_evaluation, "run_iterations", side_effect=AssertionError("evaluate")), \
                patch.object(project_evaluation, "run_approved", side_effect=AssertionError("use")), \
                patch.object(skill_approvals, "approve", side_effect=AssertionError("approve")):
            self.prepare(stage=True)

    def test_public_url_requires_exact_bytes_not_just_http_success(self):
        m = self.module()
        self.prepare(stage=True)
        with patch.object(m, "fetch_public", return_value=b"stale") as fetched, self.assertRaises(RuntimeFailure):
            m.verify_url(self.output / "site", "https://fixture.azurestaticapps.net")
        self.assertTrue(fetched.called)
        for url in ("http://localhost", "https://example.com", "https://a.azurestaticapps.net/?secret=1"):
            with self.subTest(url=url), self.assertRaises(RuntimeFailure):
                m.verify_url(self.output / "site", url)

    def test_public_url_observation_does_not_claim_branch_or_deployment_authority(self):
        from urllib.parse import urlsplit
        m = self.module()
        self.prepare(stage=True)
        site = self.output / "site"
        def fetch(url, limit):
            return (site / urlsplit(url).path.lstrip("/")).read_bytes()
        with patch.object(m, "fetch_public", side_effect=fetch):
            value = m.verify_url(site, "https://fixture.azurestaticapps.net")
        self.assertEqual(value["stages"]["public_url"], "complete")
        self.assertEqual(value["stages"]["deployment"], "not_requested")
        self.assertEqual(value["stages"]["result_branch_storage"], "not_requested")

    def test_public_url_redirect_is_rejected_instead_of_followed(self):
        m = self.module()
        with self.assertRaises(RuntimeFailure):
            m.NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://elsewhere.invalid")

    def test_invalid_deployment_origin_fails_preflight_before_local_storage(self):
        m = self.module()
        args = ["--root", str(self.root), "--data-sha", self.data_sha, "--code-sha", self.code_sha,
                "--bundle", BUNDLE, "--manifest-sha", self.digest, "--mode", "stage",
                "--output", str(self.output), "--public-url", "http://localhost"]
        with patch.object(m, "__file__", str(self.root / "reviewed_publication.py")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(m.main(args), 2)
        self.assertEqual(json.loads(err.getvalue())["code"], "invalid_public_url")
        self.assertFalse(self.output.exists())

    def test_workflow_is_independent_main_only_and_validate_by_default(self):
        path = ROOT / ".github/workflows/publish-reviewed-results.yml"
        self.assertTrue(path.is_file(), "Independent publication workflow is missing")
        text = path.read_text()
        self.assertIn("default: validate", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("CODE_SHA: ${{ github.sha }}", text)
        self.assertIn("public_content_reviewed", text)
        self.assertIn("needs: validate", text)
        self.assertIn("needs: persist", text)
        self.assertIn("skillops-dashboard-production", text)
        for forbidden in ("project_evaluation.py", "skillops.py approve", "run-approved",
                          "copilot-requests:", "COPILOT_GITHUB_TOKEN", "pull_request_target"):
            self.assertNotIn(forbidden, text)
        validate, rest = text.split("\n  validate:\n", 1)[1].split("\n  persist:\n", 1)
        persist, deploy = rest.split("\n  deploy:\n", 1)
        self.assertNotIn("contents: write", validate)
        self.assertNotIn("${{ secrets.", validate)
        self.assertIn("contents: write", persist)
        self.assertNotIn("SWA_CLI_DEPLOYMENT_TOKEN", persist)
        self.assertNotIn("contents: write", deploy)
        self.assertIn("--require-stored", deploy)
        self.assertIn("git merge-base --is-ancestor", deploy)
        self.assertNotIn("git checkout", text)
        self.assertNotIn("git add", text)
        self.assertNotIn("upload-artifact", text)

    def test_reviewed_candidates_preserve_modes_hashes_and_complete_reference_graph(self):
        bundle = ROOT / "publication-candidates/hackathon-offline-v1"
        raw = (bundle / "manifest.json").read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), "5438e9eb7258feeadc651f1481b5adef0b4d1c8213a15467e17de2ee64a637ec")
        manifest = results.read_json(bundle / "manifest.json")
        self.assertEqual(len(manifest["files"]), 47)
        self.assertEqual(sum(row["size"] for row in manifest["files"]), 206684)
        for row in manifest["files"]:
            payload = (bundle / row["path"]).read_bytes()
            self.assertEqual((len(payload), sha256(payload).hexdigest()), (row["size"], row["sha256"]))
        directory = bundle / "results"
        cycles = results.load_cycles(directory)
        self.assertEqual(len(results.load_reports(directory)), 17)
        self.assertEqual({row["confirmation_status"] for row in cycles.values()}, {"passed", "failed", "unverified"})
        for row in cycles.values():
            self.assertEqual(row["execution_mode"], "offline_test")
            self.assertEqual(row["skill_key"], "path:90ae807bd3d394fc140a6df8")
            self.assertEqual(len(row["rounds"]), 2)
        replays = results.load_replays(directory)
        self.assertEqual(len(replays), 11)
        self.assertTrue(all(row["execution_mode"] == "offline_test" for row in replays.values()))
        self.assertFalse(results.load_adoptions(directory))

    def test_official_build_blocks_static_example_with_missing_public_data(self):
        path = self.root / "dashboard/index.html"
        html = path.read_text()
        path.write_text(html.replace("</body>", '<a href="/?project=sample_repo&amp;run=999-1'
                                               '&amp;skill=auto%3Aexample">Example</a></body>'))
        results.store(self.existing, self.report)
        with self.assertRaises(RuntimeFailure) as raised:
            results.build(self.root, self.existing, self.output)
        self.assertEqual(raised.exception.code, "public_example_missing")
        self.assertFalse(self.output.exists())

    def test_official_build_checks_exact_skill_even_when_example_report_exists(self):
        path = self.root / "dashboard/index.html"
        path.write_text(path.read_text().replace(
            "</body>", '<a href="/?project=sample_repo&amp;run=123-1&amp;skill=auto%3Aunknown">Example</a></body>'))
        results.store(self.existing, self.report)
        with self.assertRaises(RuntimeFailure) as raised:
            results.build(self.root, self.existing, self.output)
        self.assertEqual(raised.exception.code, "public_example_skill_missing")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
