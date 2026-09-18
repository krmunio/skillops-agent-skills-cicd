from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import copilot_runtime as runtime_module
import evolution_records as evolution


class ConfirmationRuntimeTests(unittest.TestCase):
    def runtime(self, root):
        with patch.object(runtime_module.shutil, "which", return_value="/test-only/copilot"):
            return runtime_module.CopilotRuntime(root, inherited={"PATH": os.environ["PATH"]})

    def api(self, runtime, name):
        method = getattr(runtime, name, None)
        self.assertTrue(callable(method), f"Missing verified runtime API: {name}")
        return method

    def test_unissued_capability_is_rejected_even_with_a_truthy_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = self.runtime(Path(folder))
            runtime.isolated = True
            method = self.api(runtime, "require_confirmation_isolation")
            with self.assertRaises(runtime_module.RuntimeFailure) as error:
                method()
            self.assertEqual(error.exception.code, "confirmation_isolation_unverified")

    def test_missing_prerequisite_never_starts_host_model(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = self.runtime(Path(folder))
            method = self.api(runtime, "confirmation_isolation")
            with patch.object(runtime_module.shutil, "which", return_value=None), \
                    patch.object(runtime_module, "capture") as transport:
                with self.assertRaises(runtime_module.RuntimeFailure) as error:
                    with method(deadline=time.monotonic() + 30):
                        self.fail("No verified sandbox may be issued")
            self.assertEqual(error.exception.code, "confirmation_isolation_unverified")
            transport.assert_not_called()

    def test_container_command_has_only_explicit_mounts_and_no_secret_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = self.runtime(root)
            method = self.api(runtime, "_container_capture")
            workspace, home, output = [root / name for name in ("work", "home", "output")]
            for path in (workspace, home, output):
                path.mkdir()
            binary = root / "copilot"
            binary.write_bytes(b"test-only native payload")
            state = {"docker": "/usr/bin/docker", "image": "sha256:" + "a" * 64, "binary": binary}
            calls = []

            def capture(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 0, "{}", "")

            with patch.object(runtime_module, "capture", side_effect=capture):
                method(["/opt/copilot", "--version"], state=state, workspace=workspace, home=home,
                       output=output, timeout=10, network="none", tokens={"GITHUB_TOKEN": "private-token-sentinel"})
            command, options = calls[0]
            self.assertIn("--read-only", command)
            self.assertIn("--cap-drop=ALL", command)
            self.assertIn("--security-opt=no-new-privileges", command)
            self.assertIn("--pull=never", command)
            self.assertNotIn("--privileged", command)
            self.assertNotIn("--network=host", command)
            self.assertNotIn("private-token-sentinel", " ".join(command))
            self.assertIn("GITHUB_TOKEN", command)
            mounts = [command[i + 1] for i, arg in enumerate(command) if arg == "--mount"]
            self.assertEqual(len(mounts), 4)
            self.assertFalse(any("docker.sock" in item or f"src={runtime.private}," in item for item in mounts))
            self.assertEqual(options["env"]["GITHUB_TOKEN"], "private-token-sentinel")

    def test_container_timeout_cleans_exact_owned_container_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = self.runtime(root)
            calls = []

            def capture(args, **kwargs):
                calls.append(args)
                if args[1] == "run":
                    raise runtime_module.RuntimeFailure("timeout", "Synthetic external transport timeout.")
                return subprocess.CompletedProcess(args, 0, "", "")

            with patch.object(runtime_module, "capture", side_effect=capture):
                with self.assertRaises(runtime_module.RuntimeFailure) as error:
                    runtime._container_capture(
                        ["/opt/copilot", "--version"],
                        state={"docker": "/usr/bin/docker", "image": "sha256:" + "a" * 64, "binary": root / "binary"},
                        workspace=root / "work", home=root / "home", output=root / "output", timeout=1)
            self.assertEqual(error.exception.code, "timeout")
            self.assertEqual(len(calls), 2)
            name = calls[0][calls[0].index("--name") + 1]
            self.assertEqual(calls[1], ["/usr/bin/docker", "rm", "--force", name])

    def test_isolated_dispatch_cannot_outlive_the_shared_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = self.runtime(root)
            home = root / "home"
            (home / ".copilot").mkdir(parents=True)
            state = {"material": {}, "deadline": time.monotonic() + 2}
            runtime_module._BOUNDARIES[runtime] = state
            runtime_module._SANDBOX_CALLS[runtime] = {
                "state": {}, "work": root, "home": home, "output": root,
                "host_work": root,
            }
            self.addCleanup(runtime_module._BOUNDARIES.pop, runtime, None)
            self.addCleanup(runtime_module._SANDBOX_CALLS.pop, runtime, None)
            with patch.object(runtime, "require_confirmation_isolation"), patch.object(
                    runtime, "_container_capture", return_value=subprocess.CompletedProcess([], 0, "[]", "")) as transport:
                runtime._capture(runtime.command("-C", str(root), "skill", "list", "--json"), timeout=180)
            self.assertGreater(transport.call_args.kwargs["timeout"], 0)
            self.assertLessEqual(transport.call_args.kwargs["timeout"], 2)


@unittest.skipUnless(os.environ.get("SKILLOPS_ISOLATION_TESTS") == "1",
                     "Set SKILLOPS_ISOLATION_TESTS=1 for real non-model Docker isolation probes.")
class ActualConfirmationIsolationTests(unittest.TestCase):
    def test_actual_boundary_blocks_host_files_and_preserves_exact_skill_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = runtime_module.CopilotRuntime(root, inherited={"PATH": os.environ["PATH"]})
            isolation = getattr(runtime, "confirmation_isolation", None)
            self.assertTrue(callable(isolation), "Missing verified model filesystem boundary")
            role = runtime.private / "role"
            staged = role / ".github/skills/develop/SKILL.md"
            staged.parent.mkdir(parents=True)
            raw = b"---\nname: develop\ndescription: Synthetic non-model access control.\n---\nNo model call.\n"
            staged.write_bytes(raw)
            (role / "hidden-confirmation.txt").write_text("MUST_NOT_BE_MOUNTED")
            (root / "private-request.txt").write_text("MUST_NOT_BE_READ")
            version, _ = evolution.capture_version(
                {"SKILL.md": raw}, capture_scope="complete_bundle", complete_inventory=["SKILL.md"])
            with runtime.locked(), isolation(deadline=time.monotonic() + 120):
                runtime.require_confirmation_isolation()
                with runtime._invocation_isolation("developer", role, staged, version):
                    observed = runtime.configure(role, staged, skill_name="develop")
                    self.assertEqual(observed["enabled_skills"], ["develop"])
                    sandbox = runtime_module._SANDBOX_CALLS[runtime]
                    probe = runtime._container_capture(
                        ["/usr/local/bin/node", "-e",
                         "const fs=require('fs');if(fs.existsSync('/work/hidden-confirmation.txt')"
                         f"||fs.existsSync({json.dumps(str(root / 'private-request.txt'))}))process.exit(3);"
                         "if(!fs.existsSync('/work/.github/skills/develop/SKILL.md'))process.exit(4);"],
                        state=sandbox["state"], workspace=sandbox["work"], home=sandbox["home"],
                        output=sandbox["output"], timeout=10)
                    self.assertEqual(probe.returncode, 0, probe.stderr)
                with runtime._invocation_isolation("judge", role, None, None):
                    observed = runtime.configure(role)
                    self.assertEqual(observed["enabled_skills"], [])
                    sandbox = runtime_module._SANDBOX_CALLS[runtime]
                    self.assertEqual(list(sandbox["work"].iterdir()), [])
                self.assertEqual(staged.read_bytes(), raw)
            with self.assertRaises(runtime_module.RuntimeFailure):
                runtime.require_confirmation_isolation()
            self.assertEqual((root / "private-request.txt").read_text(), "MUST_NOT_BE_READ")

    def test_real_boundary_with_only_model_transport_doubled_records_verified_use(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = runtime_module.CopilotRuntime(root, inherited={"PATH": os.environ["PATH"]})
            runtime.execution_mode = "offline_test"
            role = runtime.private / "role"
            staged = role / ".github/skills/develop/SKILL.md"
            staged.parent.mkdir(parents=True)
            raw = b"---\nname: develop\ndescription: Offline activation wiring.\n---\nUse the permitted inputs.\n"
            staged.write_bytes(raw)
            version, _ = evolution.capture_version(
                {"SKILL.md": raw}, capture_scope="complete_bundle", complete_inventory=["SKILL.md"])
            fixture = json.loads((Path(__file__).parent / "fixtures/cli-contract.json").read_text())
            external_capture = runtime_module.capture
            model_commands = []

            def simulated_model(args, **kwargs):
                if len(args) > 1 and args[1] == "run" and "-p" in args:
                    model_commands.append(args)
                    sandbox = runtime_module._SANDBOX_CALLS[runtime]
                    (sandbox["output"] / "usage.json").write_text(json.dumps({
                        "currentModel": "gpt-6-astra", "totalNanoAiu": 10,
                    }))
                    return subprocess.CompletedProcess(args, 0, "\n".join(map(json.dumps, fixture["developer"])), "")
                return external_capture(args, **kwargs)

            with runtime.locked(), runtime.confirmation_isolation(deadline=time.monotonic() + 120), \
                    patch.object(runtime_module, "capture", side_effect=simulated_model):
                result = runtime.invoke(
                    "Synthetic offline request.", "gpt-6-astra", "developer", role,
                    runtime.private / "observed.json", staged, expected_version=version, timeout=60)
            self.assertEqual(len(model_commands), 1)
            self.assertEqual(result["staged_version_id"], version["version_id"])
            self.assertTrue(result["skill_version_verified"])
            self.assertTrue(result["skill_activated"])
            self.assertEqual(result["usage"]["nano_aiu"]["value"], 10)
            self.assertEqual(staged.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
