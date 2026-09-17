"""Minimized execution measurements, independent of quality and adoption decisions."""

from hashlib import sha256
import json
import sys
import time

from copilot_runtime import RuntimeFailure, usage_metrics
import evolution_records as evolution
from evolution_records import exact, matches, require


STAGES = {
    "discovery": "Project / check discovery",
    "original_checks": "Project / untouched source checks",
    "base_quality": "Baseline / original / Anthropic",
    "work": "Improvement / existing failure selection",
    "generation": "Improvement / APO-inspired candidate generation",
    "candidate_quality": "Baseline / candidate / Anthropic",
    "base_application": "Project / original Skill application and checks",
    "candidate_application": "Project / candidate Skill application and checks",
    "qualification": "Baseline / APO-inspired evidence and final qualification",
}
USAGE = tuple(usage_metrics(None, "unreported"))


def number(value):
    require(type(value) in (int, float) and 0 <= value <= sys.float_info.max, "invalid_telemetry")


def error_code(value):
    return value if matches(r"[a-z0-9_]{1,128}", value) else "invalid_error_code"


def usage_values(receipt):
    usage = receipt.get("usage", {})
    require(isinstance(usage, dict), "invalid_telemetry")
    values = {}
    for name in USAGE:
        metric = usage.get(name, {})
        require(isinstance(metric, dict), "invalid_telemetry")
        value = metric.get("value")
        if value is not None:
            number(value)
        values[name] = value
    return values


class Recorder:
    def __init__(self, observer=None):
        self.observer = observer
        self.active = None
        self.started = None
        self.stages = {
            stage: {"status": "not_started", "code": None, "elapsed_seconds": None, "invocations": []}
            for stage in STAGES
        }

    def __call__(self, stage, status, code=None):
        require(stage in self.stages, "invalid_telemetry")
        row = self.stages[stage]
        if status == "started":
            require(self.active is None and row["status"] == "not_started", "invalid_telemetry")
            self.active, self.started = stage, time.monotonic()
            row["status"] = "running"
        else:
            require(self.active == stage and status in ("completed", "blocked", "failed"), "invalid_telemetry")
            row.update(status=status, code=error_code(code) if code is not None else None,
                       elapsed_seconds=time.monotonic() - self.started)
            self.active = None
        if self.observer is not None:
            self.observer(stage, status, code)

    def invoke(self, runtime, *args, **kwargs):
        require(self.active is not None, "invalid_telemetry")
        started = time.monotonic()
        call = {"status": "failed", "code": None, "elapsed_seconds": None,
                "usage": dict.fromkeys(USAGE)}
        self.stages[self.active]["invocations"].append(call)
        try:
            receipt = runtime.invoke(*args, **kwargs)
            call["usage"] = usage_values(receipt)
            call["status"] = "completed"
            return receipt
        except (RuntimeFailure, OSError) as error:
            call["code"] = error_code(error.code) if isinstance(error, RuntimeFailure) else "io_error"
            if isinstance(error, RuntimeFailure) and hasattr(error, "usage"):
                call["usage"] = usage_values({"usage": error.usage})
            raise
        finally:
            call["elapsed_seconds"] = time.monotonic() - started


def digest(value):
    return sha256((json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()).hexdigest()


def bind(report, assessment, skills):
    return validate({
        "schema_version": 1, "project_id": report["project_id"], "run_id": report["run_id"],
        "report_sha256": digest(report), "assessment_sha256": digest(assessment) if assessment else None,
        "skills": skills,
    }, report, assessment)


def validate(data, report, assessment):
    exact(data, "schema_version project_id run_id report_sha256 assessment_sha256 skills")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_telemetry")
    require(data["project_id"] == report["project_id"] and data["run_id"] == report["run_id"],
            "telemetry_identity_mismatch")
    require(data["report_sha256"] == digest(report)
            and data["assessment_sha256"] == (digest(assessment) if assessment else None),
            "telemetry_evidence_mismatch")
    require(isinstance(data["skills"], list) and 0 < len(data["skills"]) <= 256, "invalid_telemetry")
    expected = {row["skill_key"]: row["source_path"] for row in assessment["skills"]} if assessment else {}
    seen, paths, calls = set(), set(), 0
    for skill in data["skills"]:
        exact(skill, "skill_key source_path stages")
        key, path = skill["skill_key"], skill["source_path"]
        require(matches(evolution.SKILL_KEY, key) and key not in seen, "invalid_telemetry")
        evolution.relative_path(path)
        require(path not in paths and (key not in expected or expected[key] == path), "telemetry_identity_mismatch")
        seen.add(key)
        paths.add(path)
        exact(skill["stages"], " ".join(STAGES))
        for stage in skill["stages"].values():
            exact(stage, "status code elapsed_seconds invocations")
            require(isinstance(stage["status"], str)
                    and stage["status"] in ("not_started", "running", "completed", "blocked", "failed"),
                    "invalid_telemetry")
            require(stage["code"] is None or matches(r"[a-z0-9_]{1,128}", stage["code"]), "invalid_telemetry")
            require(isinstance(stage["invocations"], list), "invalid_telemetry")
            calls += len(stage["invocations"])
            require(calls <= 1000, "telemetry_limit")
            if stage["status"] in ("not_started", "running"):
                require(stage["elapsed_seconds"] is None and stage["code"] is None, "invalid_telemetry")
                if stage["status"] == "not_started":
                    require(not stage["invocations"], "invalid_telemetry")
            else:
                number(stage["elapsed_seconds"])
                if stage["status"] == "completed":
                    require(stage["code"] is None, "invalid_telemetry")
            for call in stage["invocations"]:
                exact(call, "status code elapsed_seconds usage")
                require(isinstance(call["status"], str) and call["status"] in ("completed", "failed"),
                        "invalid_telemetry")
                require(call["code"] is None or matches(r"[a-z0-9_]{1,128}", call["code"]), "invalid_telemetry")
                if call["status"] == "completed":
                    require(call["code"] is None, "invalid_telemetry")
                number(call["elapsed_seconds"])
                exact(call["usage"], " ".join(USAGE))
                for value in call["usage"].values():
                    if value is not None:
                        number(value)
    require(set(expected) <= seen, "telemetry_identity_mismatch")
    invocation_count = (report["execution"]["metrics"] or {}).get("cli_invocations")
    require(invocation_count is None or invocation_count == calls, "telemetry_call_mismatch")
    return data
