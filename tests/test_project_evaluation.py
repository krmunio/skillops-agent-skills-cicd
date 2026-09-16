import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


class ProjectEvaluationTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("project_evaluation"), "orchestrator is missing")
        return importlib.import_module("project_evaluation")

    def test_disabled_evaluation_records_blocked_without_initializing_runtime(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "projects/sample_repo").mkdir(parents=True)
            (root / "projects/sample_repo/a.py").write_text("a=1")
            runtime = Mock()
            row = {"id": "sample_repo", "adapter": "issue-management-v2", "tree_sha256": "b" * 64, "error": None}
            result = m.assess(root, row, "123-1", "a" * 40, {}, runtime_factory=runtime)
            self.assertEqual(result["guide"]["status"], "not_assessed")
            self.assertEqual(result["execution"]["reason_code"], "live_disabled")
            runtime.assert_not_called()

    def test_skills_and_unsupported_adapter_have_explicit_nonpassing_states(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / "projects/other/.github/skills/test/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: test\ndescription: test\n---\nInstruction")
            row = {"id": "other", "adapter": "arbitrary-shell", "tree_sha256": "b" * 64, "error": None}
            result = m.assess(root, row, "123-1", "a" * 40, {})
            self.assertEqual(result["guide"]["reason_code"], "guide_integration_pending")
            self.assertEqual(result["execution"]["reason_code"], "unsupported_adapter")
            self.assertNotIn("Instruction", json.dumps(result))

    def test_budget_stops_before_extra_invocation(self):
        m = self.module()
        raw = Mock()
        budget = m.BudgetRuntime(raw, {"calls": 0, "max_calls": 1, "deadline": m.time.monotonic() + 60})
        budget.invoke("first")
        self.assertGreater(raw.invoke.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(raw.invoke.call_args.kwargs["timeout"], 60)
        with self.assertRaises(m.RuntimeFailure):
            budget.invoke("second")
        self.assertEqual(raw.invoke.call_count, 1)

    def test_missing_auth_or_limits_never_starts_model_runtime(self):
        m = self.module()
        for policy, reason in (
            ({"enabled": True, "authenticated": False}, "missing_auth"),
            ({"enabled": True, "authenticated": True}, "missing_limits"),
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "projects/sample_repo").mkdir(parents=True)
                runtime = Mock()
                row = {"id": "sample_repo", "adapter": "issue-management-v2", "tree_sha256": "b" * 64, "error": None}
                result = m.assess(root, row, "123-1", "a" * 40, policy, runtime_factory=runtime)
                self.assertEqual(result["execution"]["reason_code"], reason)
                runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
