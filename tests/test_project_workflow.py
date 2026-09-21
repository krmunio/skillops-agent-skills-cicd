from pathlib import Path
import os
import subprocess
import textwrap
import unittest


class WorkflowContractTests(unittest.TestCase):
    def test_current_quality_input_is_optional_distinct_and_passed_as_one_quoted_argument(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/project-evaluation.yml").read_text()
        self.assertIn("      assessment_skill_key:", text)
        self.assertRegex(text, r"(?s)      assessment_skill_key:\n.*?type: string\n        default: ''")
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        self.assertIn("ASSESSMENT_SKILL_KEY: ${{ inputs.assessment_skill_key }}", evaluate)
        self.assertIn('args+=(--assessment-skill-key "$ASSESSMENT_SKILL_KEY")', evaluate)
        step = evaluate.split("      - name: Assess projects or record explicit blocked prerequisites\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
        self.assertNotIn("${{", script)
        prefix = 'set -e\nfunction git() { printf "%s\\n" "$SOURCE_SHA"; }\nfunction python3() { printf "%s\\0" "$@"; }\n'
        baseline = {"PATH": os.environ["PATH"], "SOURCE_SHA": "a" * 40, "BEFORE_SHA": "b" * 40,
                    "RESULT_RUN": "123-1", "EVENT_NAME": "workflow_dispatch", "PROJECT_ID": "sample_repo",
                    "DISPATCH_LIVE": "false"}
        selector = "auto:current; echo PRIVATE_SENTINEL"
        cases = [
            ({}, 0, False),
            ({"EVENT_NAME": "push", "PROJECT_ID": ""}, 0, False),
            ({"ASSESSMENT_SKILL_KEY": selector}, 0, True),
            ({"ASSESSMENT_SKILL_KEY": "auto:current", "PROJECT_ID": ""}, 2, False),
            ({"ASSESSMENT_SKILL_KEY": "auto:current", "EVENT_NAME": "push"}, 2, False),
            ({"ASSESSMENT_SKILL_KEY": "auto:current", "WORK_ID": "work"}, 2, False),
            ({"ASSESSMENT_SKILL_KEY": "auto:current", "SKILL_KEY": "auto:recorded"}, 2, False),
            ({"ASSESSMENT_SKILL_KEY": "auto:current", "MAX_ROUNDS": "1"}, 2, False),
        ]
        for extra, status, selected in cases:
            with self.subTest(extra=extra):
                result = subprocess.run(["bash", "-c", prefix + script], env={**baseline, **extra},
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, status, result.stderr)
                if status == 0:
                    argv = result.stdout.decode().split("\0")
                    self.assertEqual("--assessment-skill-key" in argv, selected)
                    if selected:
                        self.assertEqual(argv[argv.index("--assessment-skill-key") + 1], selector)

    def test_recorded_dispatch_selects_only_ids_and_explicit_limits_with_no_approval_command(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/project-evaluation.yml").read_text()
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        for name in ("work_id", "skill_key", "max_rounds"):
            self.assertIn(f"      {name}:", text)
        self.assertIn("secrets.SKILLOPS_RECORDED_WORK_ITEMS", evaluate)
        self.assertIn('args+=(--work-id "$WORK_ID" --skill-key "$SKILL_KEY" --max-rounds "$MAX_ROUNDS")', evaluate)
        self.assertIn('if [ "$DISPATCH_LIVE" = "true" ]', evaluate)
        self.assertNotIn("skillops.py approve", text)
        self.assertNotIn("run-approved", text)
        script = evaluate.split("        run: |\n", 2)[-1]
        self.assertNotIn('"$SKILLOPS_RECORDED_WORK_ITEMS"', script)

    def test_public_result_artifact_uses_explicit_filenames_not_private_trees(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/project-evaluation.yml").read_text()
        artifact = text.split("name: public-project-results", 1)[1].split("\n  persist:", 1)[0]
        self.assertNotIn("path: ci-results/", artifact)
        for name in ("report.json", "skill-evolution.json", "replay-evaluation.json", "cycle.json", "adoption.json"):
            self.assertIn("ci-results/*/*/" + name, artifact)
        self.assertNotIn(".skillops", artifact)

    def test_manual_project_limits_are_not_replicated_per_skill(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        for name in ("max_invocations", "max_seconds", "max_ai_credits"):
            self.assertIn(f"      {name}:", text)
            self.assertIn(f"inputs.{name} || vars.", evaluate)
        self.assertIn("(inputs.max_seconds || vars.SKILLOPS_MAX_SECONDS) > 1200 && 130 || 25", evaluate)
        self.assertIn('if [ -n "$LIMIT_OVERRIDE" ] && [ -z "$PROJECT_ID" ]; then', evaluate)
        self.assertIn("Explicit project required for limit overrides", evaluate)
        self.assertEqual(evaluate.count("python3 project_evaluation.py "), 1)
        self.assertNotIn("matrix:", evaluate)

    def test_push_selects_changed_projects_and_preserves_manual_selection(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        self.assertIn("fetch-depth: 0", evaluate)
        self.assertIn("BEFORE_SHA: ${{ github.event.before }}", evaluate)
        self.assertIn('args+=(--changed-since "$BEFORE_SHA")', evaluate)
        self.assertIn('args+=(--project "$PROJECT_ID")', evaluate)
        self.assertIn("SELECTED_PROJECTS: ${{ steps.assess.outputs.selected_projects }}", evaluate)
        self.assertIn('[ "$SELECTED_PROJECTS" = "0" ]', evaluate)
        self.assertIn("No changed projects", evaluate)
        self.assertIn("      - 'package-lock.json'", text)

    def test_evaluation_exposes_stage_logs_and_validated_failure_summary(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        self.assertIn("SKILLOPS_ACTIONS_PROGRESS: 'true'", evaluate)
        self.assertIn("name: Summarize baseline and project evaluation\n        if: always()", evaluate)
        self.assertIn('python3 evaluation_reporting.py --results ci-results --run-id "$RESULT_RUN" >> "$GITHUB_STEP_SUMMARY"', evaluate)
        self.assertLess(evaluate.index("python3 project_evaluation.py "), evaluate.index("python3 evaluation_reporting.py "))
        self.assertLess(evaluate.index("python3 evaluation_reporting.py "), evaluate.index("name: public-project-results"))

    def test_dashboard_ci_runs_locked_browser_checks_with_pinned_actions_without_privileged_secrets(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/ci.yml").read_text()
        dashboard = workflow.split("\n  dashboard:\n", 1)[1].split("\n  offline-and-container:\n", 1)[0]
        self.assertIn("runs-on: ubuntu-24.04", dashboard)
        self.assertIn("node-version: '22'", dashboard)
        self.assertIn("python-version: '3.12'", dashboard)
        for command in ("npm ci --no-audit --no-fund", "npx playwright install --with-deps chromium", "npm run test:dashboard"):
            self.assertIn(command, dashboard)
        for line in dashboard.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"uses: actions/[a-z-]+@[a-f0-9]{40}(?: |$)")
        self.assertIn("persist-credentials: false", dashboard)
        self.assertNotIn("${{ secrets.", dashboard)
        self.assertNotIn("copilot-requests:", dashboard)

    def test_repository_validation_is_not_named_skill_baseline_evaluation(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/ci.yml").read_text()
        self.assertTrue(text.startswith("name: SkillOps validation\n"))
        self.assertIn("## SkillOps validation", text)
        self.assertIn("  offline-and-container:", text)
        self.assertIn("SKILLOPS_CONTAINER_TESTS", text)
        self.assertIn("python -m unittest discover", text)
        self.assertNotIn("copilot-requests:", text)
        self.assertNotIn("${{ secrets.", text)

    def test_project_triggers_and_dispatch_keep_live_execution_explicit(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        triggers = text.split("\npermissions:", 1)[0]
        self.assertIn("  pull_request:\n    branches: [main]", triggers)
        self.assertIn("  push:\n    branches: [main]", triggers)
        self.assertIn("      - 'projects/**'", triggers)
        self.assertIn("  workflow_dispatch:", triggers)
        self.assertRegex(triggers, r"(?s)      live:\n.*?type: boolean\n        default: false")
        self.assertNotIn("evaluation-results", triggers)
        self.assertNotIn("pull_request_target", triggers)
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        self.assertIn("needs: contracts", evaluate)
        self.assertIn("github.ref == 'refs/heads/main'", evaluate)
        self.assertIn("github.event_name == 'push' || github.event_name == 'workflow_dispatch'", evaluate)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.live", evaluate)
        self.assertEqual(evaluate.count("python3 project_evaluation.py "), 1)
        self.assertNotIn("strategy:", evaluate)

    def test_job_permissions_and_failure_evidence_are_separated(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        contracts = text.split("\n  contracts:\n", 1)[1].split("\n  evaluate:\n", 1)[0]
        evaluate = text.split("\n  evaluate:\n", 1)[1].split("\n  persist:\n", 1)[0]
        persist = text.split("\n  persist:\n", 1)[1].split("\n  deploy:\n", 1)[0]
        deploy = text.split("\n  deploy:\n", 1)[1]
        self.assertNotIn("${{ secrets.", contracts)
        self.assertIn("SKILLOPS_LIVE_EVALUATION_ENABLED: 'false'", contracts)
        self.assertIn('test "$assessment_status" -eq 2', contracts)
        self.assertIn("copilot-requests: write", evaluate)
        self.assertNotIn("contents: write", evaluate)
        self.assertNotIn("SKILLOPS_SWA_DEPLOYMENT_TOKEN", evaluate)
        self.assertIn("if: always()", evaluate)
        self.assertIn("name: public-project-results", evaluate)
        self.assertIn("needs: evaluate", persist)
        self.assertIn("needs.evaluate.result == 'failure'", persist)
        self.assertIn("python3 project_results.py validate --results ci-results", persist)
        self.assertIn('python3 project_results.py merge-samples --results "$target/results"', persist)
        self.assertIn("contents: write", persist)
        self.assertNotIn("copilot-requests:", persist)
        self.assertIn("needs: persist", deploy)
        self.assertIn("needs.persist.result == 'success'", deploy)
        self.assertIn("SKILLOPS_SWA_DEPLOYMENT_TOKEN", deploy)

    def test_privileged_workflow_is_main_only_and_publishes_data_branch(self):
        root = Path(__file__).resolve().parents[1]
        path = root / ".github/workflows/project-evaluation.yml"
        self.assertTrue(path.is_file(), "project workflow is missing")
        text = path.read_text()
        self.assertNotIn("pull_request_target", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("evaluation-results", text)
        self.assertNotIn("git add .", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("SKILLOPS_MAX_INVOCATIONS", text)
        self.assertIn("SKILLOPS_LIVE_EVALUATION_ENABLED", text)
        self.assertIn("if: always()", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_infrastructure_is_swa_only_and_not_free(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "infra/public-dashboard.bicep"
        self.assertTrue(path.is_file(), "SWA template is missing")
        text = path.read_text()
        self.assertIn("Microsoft.Web/staticSites@", text)
        self.assertIn("'Standard'", text)
        self.assertNotIn("Microsoft.Storage", text)
        self.assertNotIn("repositoryToken", text)

    def test_automatic_evaluation_has_scoped_auth_history_and_both_language_images(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/project-evaluation.yml").read_text()
        self.assertIn("copilot-requests: write", text)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", text)
        self.assertIn("SKILLOPS_MAX_AI_CREDITS_PER_SESSION", text)
        self.assertIn("--history .prior-results/results", text)
        self.assertIn("group: skillops-project-evaluation-${{ github.ref }}", text)
        for workflow in ("ci.yml", "project-evaluation.yml"):
            self.assertIn("node@sha256:", (root / ".github/workflows" / workflow).read_text())


if __name__ == "__main__":
    unittest.main()
