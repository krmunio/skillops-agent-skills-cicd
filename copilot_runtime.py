"""Restricted native Copilot CLI access for SkillOps."""

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from uuid import uuid4


TOKEN_KEYS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
PROMPT_LIMIT = 100000
MIN_AI_CREDITS = 30


class RuntimeFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def redact(text, env=None):
    for key in TOKEN_KEYS:
        value = (env or {}).get(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[REDACTED]", text)
    return re.sub(r"(https?://)[^/\s@]+:[^/\s@]+@", r"\1[REDACTED]@", text)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RuntimeFailure("invalid_json", "Duplicate JSON key.")
            result[key] = value
        return result

    def constant(value):
        raise RuntimeFailure("invalid_json", "Nonfinite JSON number.")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        def finite(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise RuntimeFailure("invalid_json", "Nonfinite JSON number.")
            if isinstance(item, dict):
                for child in item.values():
                    finite(child)
            elif isinstance(item, list):
                for child in item:
                    finite(child)
        finite(value)
        return value
    except (ValueError, RecursionError) as error:
        raise RuntimeFailure("invalid_json", "Expected one valid JSON value.") from error


def capture(args, *, cwd=None, env=None, timeout=180, limit=4 * 1024 * 1024):
    if not math.isfinite(timeout) or timeout <= 0 or limit <= 0:
        raise RuntimeFailure("invalid_limit", "Timeout and output limit must be positive.")
    try:
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except (OSError, ValueError) as error:
        raise RuntimeFailure("launch_error", "Could not start the requested executable.") from error
    output = {1: bytearray(), 2: bytearray()}
    deadline = time.monotonic() + timeout
    total = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream, number in ((process.stdout, 1), (process.stderr, 2)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, number)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeFailure("timeout", "Subprocess exceeded its time limit.")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), min(65536, limit - total + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise RuntimeFailure("output_limit_exceeded", "Subprocess output exceeded its byte limit.")
                    output[key.data].extend(chunk)
        return subprocess.CompletedProcess(
            args, process.wait(),
            output[1].decode("utf-8", errors="replace"),
            redact(output[2].decode("utf-8", errors="replace"), env),
        )
    finally:
        # Descendants can outlive the leader or keep its output pipes open.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def disabled_skills(rows, expected_path, *, skill_name="develop"):
    if not isinstance(skill_name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name):
        raise RuntimeFailure("invalid_skill_name", "Use an explicit safe Skill name.")
    if not isinstance(rows, list):
        raise RuntimeFailure("invalid_inventory", "Skill inventory must be a list.")
    names = []
    matches = []
    expected = Path(expected_path).resolve() if expected_path is not None else None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise RuntimeFailure("invalid_inventory", "Invalid skill inventory entry.")
        if not isinstance(row.get("enabled"), bool) or not isinstance(row.get("path"), str):
            raise RuntimeFailure("invalid_inventory", "Skill inventory lacks path or enabled status.")
        name = row["name"]
        names.append(name)
        if expected is not None and name == skill_name:
            path = Path(row["path"])
            path = path if path.name == "SKILL.md" else path / "SKILL.md"
            matches.append(path.resolve())
    if expected is not None and matches != [expected]:
        raise RuntimeFailure("skill_mismatch", "Expected exactly one selected Skill from the staged source.")
    return sorted(set(names) - ({skill_name} if expected is not None else set()))


def verify_staged_version(entrypoint, version):
    from evolution_records import capture_version, validate_version

    validate_version(version)
    entrypoint = Path(entrypoint).absolute()
    if entrypoint.name != "SKILL.md" or any(path.is_symlink() for path in (entrypoint, *entrypoint.parents)):
        raise RuntimeFailure("unsafe_skill_path", "Staged Skill must use an ordinary SKILL.md.")
    files = {}
    total = 0
    for path in entrypoint.parent.rglob("*"):
        if path.is_symlink():
            raise RuntimeFailure("unsafe_skill_path", "Staged Skill resources must not be symbolic links.")
        if path.is_dir():
            continue
        if not path.is_file() or len(files) >= 256:
            raise RuntimeFailure("skill_capture_limit", "Staged Skill inventory is invalid or oversized.")
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        total += len(raw)
        if len(raw) > 2 * 1024 * 1024 or total > 8 * 1024 * 1024:
            raise RuntimeFailure("skill_capture_limit", "Staged Skill content exceeds the capture limits.")
        files[path.relative_to(entrypoint.parent).as_posix()] = raw
    captured, _ = capture_version(files, capture_scope=version["capture_scope"], complete_inventory=list(files))
    if captured != version:
        raise RuntimeFailure("skill_version_mismatch", "Staged Skill bytes differ from the selected version.")
    return captured["version_id"]


def parse_events(text, model, role, *, skill_name="develop"):
    if role not in ("developer", "judge", "generator"):
        raise RuntimeFailure("invalid_role", "Unknown CLI role.")
    events = [strict_json(line) for line in text.splitlines() if line.strip()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise RuntimeFailure("invalid_events", "CLI output must contain JSONL event objects.")
    results = [e for e in events if e.get("type") == "result"]
    if len(results) != 1 or events[-1] != results[0] or results[0].get("exitCode") != 0:
        raise RuntimeFailure("incomplete_run", "CLI terminal result is missing or unsuccessful.")
    session_id = results[0].get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeFailure("invalid_events", "CLI terminal result lacks a session identity.")
    final = []
    manifests = []
    calls = []
    tool_completions = []
    for event in events:
        kind, data = event.get("type"), event.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeFailure("invalid_events", "CLI event data must be an object.")
        if kind == "model.call_start" and data.get("model") != model:
            raise RuntimeFailure("model_mismatch", "CLI used an unexpected model.")
        if kind in ("session.error", "error"):
            raise RuntimeFailure("cli_error", "CLI emitted an error event.")
        if kind == "assistant.message" and data.get("phase") == "final_answer" and not data.get("toolRequests"):
            if data.get("model") != model or not isinstance(data.get("content"), str):
                raise RuntimeFailure("model_mismatch", "Final response has an unexpected model or shape.")
            final.append(data["content"])
        if kind == "tool.execution_start":
            if not isinstance(data.get("toolCallId"), str) or not data["toolCallId"]:
                raise RuntimeFailure("invalid_events", "Tool call identity must be nonempty text.")
            calls.append(data)
        if kind == "tool.execution_complete":
            if not isinstance(data.get("toolCallId"), str) or not data["toolCallId"]:
                raise RuntimeFailure("invalid_events", "Tool completion identity must be nonempty text.")
            tool_completions.append(data)
        if kind == "session.usage_checkpoint":
            conversations = data.get("promptCacheBreakState", [])
            if not isinstance(conversations, list):
                raise RuntimeFailure("invalid_events", "Tool manifest conversations must be a list.")
            for conversation in conversations:
                if not isinstance(conversation, dict) or not isinstance(conversation.get("models"), dict):
                    raise RuntimeFailure("invalid_events", "Tool manifest model collection must be an object.")
                for value in conversation["models"].values():
                    if not isinstance(value, dict):
                        raise RuntimeFailure("invalid_events", "Tool manifest must be an object.")
                    if value.get("model") != model:
                        raise RuntimeFailure("model_mismatch", "Tool manifest references an unexpected model.")
                    tools = value.get("tools")
                    if not isinstance(tools, list) or any(
                        not isinstance(tool, dict) or not isinstance(tool.get("name"), str) for tool in tools
                    ):
                        raise RuntimeFailure("invalid_events", "Tool manifest requires a list of named tool objects.")
                    if type(value.get("tool_count")) is not int or type(value.get("tools_truncated")) is not int:
                        raise RuntimeFailure("invalid_events", "Tool manifest counts must be integers.")
                    manifests.append({
                        "model": model, "tool_count": value.get("tool_count"),
                        "tools": [tool["name"] for tool in tools],
                        "truncated": value.get("tools_truncated"),
                    })
    expected = ["skill"] if role == "developer" else []
    if not manifests or any(
        row["tools"] != expected or row["tool_count"] != len(expected) or row["truncated"] != 0
        for row in manifests
    ):
        raise RuntimeFailure("tool_exposure", "Observed CLI tools do not match the role allowlist.")
    if role != "developer" and calls:
        raise RuntimeFailure("tool_execution", "A zero-tool role attempted to execute a tool.")
    activated = False
    if role == "developer":
        completed = {row.get("toolCallId"): row.get("success") for row in tool_completions}
        if not calls or any(
            call.get("toolName") != "skill" or call.get("arguments") != {"skill": skill_name}
            or completed.get(call.get("toolCallId")) is not True
            for call in calls
        ):
            raise RuntimeFailure("skill_not_activated", "The selected Skill was not successfully invoked.")
        activated = True
    if len(final) != 1:
        raise RuntimeFailure("invalid_final", "Expected exactly one final assistant response.")
    return {
        "content": final[0], "observed_model": model, "session_id": session_id,
        "tool_manifests": manifests, "skill_activated": activated,
        "tool_calls": [{key: call.get(key) for key in ("toolCallId", "toolName", "arguments")} for call in calls],
    }


def usage_metrics(value, model):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise RuntimeFailure("invalid_usage", "CLI usage must be an object.")
    if value.get("currentModel", model) != model:
        raise RuntimeFailure("model_mismatch", "CLI usage reports an unexpected model.")
    model_usage = value
    for key in ("modelMetrics", model, "usage"):
        model_usage = model_usage.get(key, {})
        if not isinstance(model_usage, dict):
            raise RuntimeFailure("invalid_usage", "Nested CLI usage must contain objects.")
    fields = {
        "nano_aiu": (value.get("totalNanoAiu"), "nano_aiu"),
        "premium_request_cost": (value.get("totalPremiumRequestCost"), "premium_request_units"),
        "api_duration_ms": (value.get("totalApiDurationMs"), "milliseconds"),
        "input_tokens": (model_usage.get("inputTokens"), "tokens"),
        "output_tokens": (model_usage.get("outputTokens"), "tokens"),
        "cache_read_tokens": (model_usage.get("cacheReadTokens"), "tokens"),
        "cache_write_tokens": (model_usage.get("cacheWriteTokens"), "tokens"),
    }
    result = {}
    for name, (number, unit) in fields.items():
        if number is not None and (type(number) not in (int, float) or not math.isfinite(number) or number < 0):
            raise RuntimeFailure("invalid_usage", "CLI usage contains an invalid numeric value.")
        result[name] = {"value": number, "unit": unit, "reason": "not_reported_by_cli" if number is None else None}
    return result


class CopilotRuntime:
    def __init__(self, project, inherited=None):
        source = os.environ if inherited is None else inherited
        self.project = Path(project).resolve(strict=True)
        self.cli = shutil.which("copilot", path=source.get("PATH"))
        if self.cli is None:
            raise RuntimeFailure("missing_cli", "Copilot CLI is not installed.")
        self.cli = str(Path(self.cli).absolute())
        self.private = self.project / ".skillops-private"
        marker = self.private / ".owner"
        if self.private.is_symlink():
            raise RuntimeFailure("unsafe_profile", "Private profile must not be a symlink.")
        if self.private.exists():
            if not self.private.is_dir() or self.private.stat().st_uid != os.getuid():
                raise RuntimeFailure("unsafe_profile", "Private directory has an unexpected owner.")
            if self.private.stat().st_mode & 0o077:
                raise RuntimeFailure("unsafe_profile", "Private directory must have mode 0700.")
            if marker.is_symlink() or not marker.is_file() or marker.read_text() != "skillops-profile-v1\n":
                raise RuntimeFailure("unsafe_profile", "Refusing an unowned private directory.")
        else:
            self.private.mkdir(mode=0o700)
            marker.write_text("skillops-profile-v1\n")
        self.home = self.private / "home"
        self.config = self.home / ".copilot"
        for directory in (self.home, self.config, self.home / ".cache", self.home / ".config"):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise RuntimeFailure("unsafe_profile", "Private profile contains an unsafe directory.")
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
                raise RuntimeFailure("unsafe_profile", "Private profile directories must be owned and mode 0700.")
        allowed = (
            "PATH", "LANG", "LC_ALL", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
        ) + TOKEN_KEYS
        self.env = {key: source[key] for key in allowed if source.get(key)}
        self.env.update({
            "HOME": str(self.home),
            "COPILOT_HOME": str(self.config),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "COPILOT_CACHE_HOME": str(self.home / ".cache" / "copilot"),
        })
        self.check_profile()

    def check_profile(self):
        registry = self.config / "providers.json"
        if registry.exists() or registry.is_symlink():
            raise RuntimeFailure("alternate_provider", "Remove alternate-provider configuration from the dedicated profile before running.")
        for name in ("settings.json", "config.json", ".runtime.lock"):
            path = self.config / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise RuntimeFailure("unsafe_profile", "Profile configuration must be an ordinary file.")

    @contextmanager
    def locked(self):
        self.check_profile()
        descriptor = os.open(self.config / ".runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeFailure("profile_busy", "Another run owns the dedicated profile.") from error
            yield
        finally:
            os.close(descriptor)

    def login_command(self):
        return shlex.join([
            "env", f"HOME={self.home}", f"COPILOT_HOME={self.config}",
            self.cli, "--no-auto-update", "login",
        ])

    def command(self, *args):
        return [self.cli, "--no-auto-update", *args]

    def model_command(self, prompt, model, role, *, max_ai_credits=None):
        if role not in ("developer", "judge", "generator") or not isinstance(model, str) or not model:
            raise RuntimeFailure("invalid_role", "An explicit model and supported role are required.")
        if max_ai_credits is not None and (
                type(max_ai_credits) not in (int, float) or not math.isfinite(max_ai_credits)
                or max_ai_credits < MIN_AI_CREDITS):
            raise RuntimeFailure("invalid_limit", f"AI credit limit must be finite and at least {MIN_AI_CREDITS}.")
        return self.command(
            "--no-custom-instructions", "--disable-builtin-mcps", "--no-ask-user", "--no-color",
            "--available-tools=skill", *(["--excluded-tools=skill"] if role != "developer" else []),
            "--allow-all-tools",
            *(["--max-ai-credits", str(max_ai_credits)] if max_ai_credits is not None else []),
            "--model", model, "--output-format", "json", "-p", prompt,
        )

    def inventory(self, workdir, kind):
        result = capture(self.command("-C", str(workdir), kind, "list", "--json"), env=self.env, timeout=30)
        if result.returncode:
            raise RuntimeFailure("inventory_error", f"Could not inspect {kind} inventory: {result.stderr[:1024]}")
        return strict_json(result.stdout)

    def write_settings(self, disabled):
        self.check_profile()
        settings = {
            "disableAllHooks": True, "memory": False, "ide": {"autoConnect": False},
            "customAgents": {"defaultLocalOnly": True}, "disabledSkills": disabled,
        }
        descriptor, path = tempfile.mkstemp(prefix=".settings-", dir=self.config)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(settings, output, indent=2)
            os.replace(path, self.config / "settings.json")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def configure(self, workdir, expected_skill=None, *, skill_name="develop"):
        self.check_profile()
        self.write_settings([])
        discovered = self.inventory(workdir, "skill")
        self.write_settings(disabled_skills(discovered, expected_skill, skill_name=skill_name))
        checked = self.inventory(workdir, "skill")
        disabled_skills(checked, expected_skill, skill_name=skill_name)
        enabled = sorted(row["name"] for row in checked if row["enabled"])
        if enabled != ([skill_name] if expected_skill is not None else []):
            raise RuntimeFailure("skill_mismatch", "Enabled skills do not match the role allowlist.")
        if self.inventory(workdir, "instruction"):
            raise RuntimeFailure("unexpected_instructions", "Unexpected instructions discovered in the role workspace.")
        if self.inventory(workdir, "plugin") != []:
            raise RuntimeFailure("unexpected_plugins", "The dedicated profile must not contain plugins.")
        if self.inventory(workdir, "mcp") != {"mcpServers": {}}:
            raise RuntimeFailure("unexpected_mcp", "The dedicated profile must not contain custom MCP servers.")
        return {
            "enabled_skills": enabled, "discovered_count": len(checked),
            "instructions": [], "plugins": [], "custom_mcp_servers": [],
        }

    def invoke(self, prompt, model, role, workdir, artifact, expected_skill=None, *, timeout=180,
               skill_name="develop", expected_version=None, max_ai_credits=None):
        if len(prompt.encode("utf-8")) > PROMPT_LIMIT:
            raise RuntimeFailure("prompt_limit", "Prompt exceeds the bounded CLI input size.")
        artifact = Path(artifact)
        try:
            with artifact.open("x") as output:
                output.write('{"status":"starting"}\n')
        except FileExistsError as error:
            raise RuntimeFailure("artifact_exists", "Refusing to overwrite a prior invocation artifact.") from error
        identifier = uuid4().hex
        private = self.private / "probes"
        private.mkdir(mode=0o700, exist_ok=True)
        usage_path = private / f"{identifier}-usage.json"
        started = time.monotonic()
        record = {
            "role": role, "requested_model": model, "prompt": prompt,
            "usage": usage_metrics(None, model),
        }
        try:
            if expected_version is not None:
                if role != "developer" or expected_skill is None:
                    raise RuntimeFailure("invalid_role", "Version binding requires a staged developer Skill.")
                record["staged_version_id"] = verify_staged_version(expected_skill, expected_version)
            record["inventory"] = self.configure(workdir, expected_skill, skill_name=skill_name)
            command = self.model_command(prompt, model, role, max_ai_credits=max_ai_credits)
            command += ["--usage-output-file", str(usage_path)]
            result = capture(command, cwd=workdir, env=self.env, timeout=timeout)
            stdout, stderr = redact(result.stdout, self.env), redact(result.stderr, self.env)
            (private / f"{identifier}.jsonl").write_text(stdout)
            record.update(returncode=result.returncode, stderr=stderr[:16384])
            value = strict_json(usage_path.read_text()) if usage_path.is_file() else None
            record["usage"] = usage_metrics(value, model)
            if result.returncode:
                raise RuntimeFailure("cli_error", f"CLI failed: {stderr[:1024]}")
            record.update(parse_events(stdout, model, role, skill_name=skill_name))
            if expected_version is not None:
                verify_staged_version(expected_skill, expected_version)
                record["skill_version_verified"] = True
            record["status"] = "completed"
        except RuntimeFailure as error:
            record.update(status="contract_error", error={"code": error.code, "message": str(error)})
            record["elapsed_seconds"] = time.monotonic() - started
            artifact.write_text(json.dumps(record, indent=2) + "\n")
            raise
        record["elapsed_seconds"] = time.monotonic() - started
        artifact.write_text(json.dumps(record, indent=2) + "\n")
        return record
