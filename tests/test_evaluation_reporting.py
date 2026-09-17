import importlib
import importlib.util
from contextlib import redirect_stdout
from functools import partial
import io
from pathlib import Path
import tempfile
import unittest

from copilot_runtime import RuntimeFailure
import project_results
from test_skill_assessments import fixture


class EvaluationReportingTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("evaluation_reporting"))
        return importlib.import_module("evaluation_reporting")

    def store_fixture(self, root):
        report, lifecycle, assessment = fixture()
        project_results.store(root, report)
        project_results.store_evolution(root, lifecycle)
        project_results.store_assessments(root, assessment)
        return report, assessment

    def test_summary_separates_baseline_evidence_and_project_results(self):
        reporting = self.module()
        with tempfile.TemporaryDirectory() as folder:
            _, assessment = self.store_fixture(folder)
            summary = reporting.summary(Path(folder), "123-1")
        self.assertIn("Baseline / Anthropic", summary)
        self.assertIn("Baseline / APO-inspired evidence", summary)
        self.assertIn("Project / untouched source", summary)
        self.assertIn("Project / Skill application", summary)
        self.assertIn("1 passed, 1 failed", summary)
        self.assertIn("2 passed, 0 failed", summary)
        self.assertIn("improved", summary)
        self.assertIn("No independent APO score", summary)
        self.assertNotIn(assessment["skills"][0]["generation"]["hypothesis"], summary)
        self.assertNotIn("Boundary guidance is missing.", summary)

    def test_blocked_summary_does_not_invent_skill_assessments(self):
        reporting = self.module()
        with tempfile.TemporaryDirectory() as folder:
            report = fixture()[0]
            report["guide"] = project_results.axis("blocked", "guide_integration_pending")
            report["execution"] = project_results.axis("blocked", "live_disabled")
            project_results.store(folder, report)
            summary = reporting.summary(Path(folder), "123-1")
        self.assertIn("live_disabled", summary)
        self.assertIn("No per-Skill assessment recorded", summary)
        self.assertNotIn("evidence linked", summary)

    def test_summary_rejects_unbound_evidence_and_unknown_run(self):
        reporting = self.module()
        with tempfile.TemporaryDirectory() as folder:
            self.store_fixture(folder)
            with self.assertRaises(RuntimeFailure):
                reporting.summary(Path(folder), "999-1")
            artifact = Path(folder) / "sample_repo/123-1/skill-assessments.json"
            artifact.write_text(artifact.read_text().replace('"report_sha256": "', '"report_sha256": "0'))
            with self.assertRaises(RuntimeFailure):
                reporting.summary(Path(folder), "123-1")

    def test_phase_logs_are_balanced_and_do_not_expose_failure_messages(self):
        reporting = self.module()
        for error in (None, RuntimeFailure("cli_error", "private prompt"), ValueError("private data")):
            with self.subTest(error=error), redirect_stdout(io.StringIO()) as output:
                progress = partial(reporting.progress, "sample_repo", "skills/test\n::warning::injected")
                def run():
                    if error is not None:
                        raise error
                    return 42
                if error is None:
                    self.assertEqual(reporting.run_stage(progress, "base_quality", run), 42)
                else:
                    with self.assertRaises(type(error)):
                        reporting.run_stage(progress, "base_quality", run)
                text = output.getvalue()
                self.assertEqual(text.count("::group::"), 1)
                self.assertEqual(text.count("::endgroup::"), 1)
                self.assertNotIn("\n::warning::", text)
                self.assertNotIn("private", text)
                self.assertIn("Baseline / original / Anthropic", text)

    def test_untrusted_labels_cannot_create_markdown_or_log_commands(self):
        reporting = self.module()
        text = reporting.label("a|[x](url)<script>`\r\n::warning::fake")
        for value in ("|", "[", "<script>", "`", "\r", "\n"):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
