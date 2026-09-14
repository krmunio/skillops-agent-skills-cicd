from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EVALUATOR_VERSION = "skillops-http-retry-evaluator-v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_PREFIXES = ("tests/", "skillops/", ".github/workflows/")
PROTECTED_FILES = {"examples/manifests/approval_policy.json"}
IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
TRANSIENT_STATUSES = {502, 503, 504}
TRUSTED_FIXTURE_ROOT = REPO_ROOT / "examples" / "fixtures"
DEMO_TARGET_REPO = "krmunio/example-http-client"
DEMO_SNAPSHOT = "1111111111111111111111111111111111111111"
DEMO_LIVE_SNAPSHOT = "0000000000000000000000000000000000000000"
DEMO_REQUEST_PATH = "/charge"


@dataclass
class RunResult:
    subject: str
    skill_content_hash: str
    workspace: Path
    artifact_path: Path | None
    changed_files: list[str]
    error: str | None = None


class FakeHTTPService:
    def __init__(self, statuses: list[int], side_effect_methods: set[str] | None = None):
        self.statuses = list(statuses)
        self.side_effect_methods = side_effect_methods or set()
        self.calls = 0
        self.side_effects = 0

    def request(self, method: str, path: str) -> dict[str, Any]:
        self.calls += 1
        if method.upper() in self.side_effect_methods:
            self.side_effects += 1
        status = self.statuses.pop(0) if self.statuses else 200
        return {"status": status, "path": path, "call": self.calls}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def skill_hash(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    if not skill_dir.is_dir():
        raise ValueError(f"skill directory not found: {skill_dir}")
    files = sorted(path for path in skill_dir.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"skill directory has no files: {skill_dir}")
    for path in files:
        rel = path.relative_to(skill_dir).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON object expected in {path}")
    return data


def run_fixture(subject: str, skill_dir: Path, artifact_file: Path, workspace_root: Path) -> RunResult:
    """Collect a trusted fixture artifact into a clean subject workspace."""
    workspace = workspace_root / subject
    workspace.mkdir(parents=True, exist_ok=True)
    if not artifact_file.is_file():
        result = RunResult(subject, skill_hash(skill_dir), workspace, None, [], f"artifact not found: {artifact_file}")
    elif not _is_relative_to(artifact_file, TRUSTED_FIXTURE_ROOT):
        result = RunResult(subject, skill_hash(skill_dir), workspace, None, [], f"untrusted fixture artifact: {artifact_file}")
    else:
        dest = workspace / artifact_file.name
        shutil.copy2(artifact_file, dest)
        (workspace / ".skillops_trusted_fixture").write_text(str(artifact_file.resolve()), encoding="utf-8")
        result = RunResult(subject, skill_hash(skill_dir), workspace, dest, [artifact_file.name])
    (workspace / "run_result.json").write_text(json.dumps({
        "subject": result.subject,
        "skill_hash": result.skill_content_hash,
        "artifact_path": str(result.artifact_path) if result.artifact_path else None,
        "changed_files": result.changed_files,
        "error": result.error,
    }, indent=2, sort_keys=True), encoding="utf-8")
    return result


def load_run_result(path: Path) -> RunResult:
    data = _load_json(path)
    required = {"subject", "skill_hash", "artifact_path", "changed_files"}
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"run result missing fields: {', '.join(missing)}")
    changed = data["changed_files"]
    if not isinstance(changed, list) or not all(isinstance(item, str) for item in changed):
        raise ValueError("run result changed_files must be a string list")
    artifact = Path(data["artifact_path"]) if data.get("artifact_path") else None
    return RunResult(
        subject=str(data["subject"]),
        skill_content_hash=str(data["skill_hash"]),
        workspace=path.parent,
        artifact_path=artifact,
        changed_files=changed,
        error=data.get("error"),
    )


def _load_retry_module(path: Path):
    module_id = hashlib.sha1(str(path.resolve()).encode()).hexdigest()
    spec = importlib.util.spec_from_file_location(f"skillops_artifact_{module_id}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import artifact: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "request"):
        raise ValueError("artifact must define request(method, service, path, max_retries=...)")
    return module


def _is_trusted_artifact(path: Path, workspace: Path) -> bool:
    if _is_relative_to(path, TRUSTED_FIXTURE_ROOT):
        return True
    marker = workspace / ".skillops_trusted_fixture"
    if not marker.is_file():
        return False
    return _is_relative_to(Path(marker.read_text(encoding="utf-8").strip()), TRUSTED_FIXTURE_ROOT)


def _call_request(module: Any, method: str, service: FakeHTTPService, max_retries: int = 2) -> dict[str, Any]:
    response = module.request(method, service, DEMO_REQUEST_PATH, max_retries=max_retries)
    if not isinstance(response, dict) or "status" not in response:
        raise AssertionError("request() must return a response dict with status")
    return response


def evaluate_artifact(result: RunResult) -> dict[str, Any]:
    """Run the independent HTTP retry evaluator against a collected artifact."""
    protected_changes = sorted(
        path for path in result.changed_files
        if path in PROTECTED_FILES or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)
    )
    cases: list[dict[str, Any]] = []
    if result.error:
        return {"valid_result_data": False, "protected_files_changed": protected_changes,
                "cases": [{"name": "runner produced artifact", "passed": False, "reason": result.error}]}
    if result.artifact_path is None or not result.artifact_path.is_file():
        return {"valid_result_data": False, "protected_files_changed": protected_changes,
                "cases": [{"name": "runner produced artifact", "passed": False, "reason": "missing artifact_path"}]}
    if not _is_trusted_artifact(result.artifact_path, result.workspace):
        return {"valid_result_data": False, "protected_files_changed": protected_changes,
                "cases": [{"name": "trusted fixture artifact", "passed": False,
                           "reason": "only pre-vetted repository fixtures may be imported"}]}
    try:
        module = _load_retry_module(result.artifact_path)
    except Exception as exc:  # pragma: no cover - exact import failures vary
        return {"valid_result_data": False, "protected_files_changed": protected_changes,
                "cases": [{"name": "artifact import", "passed": False, "reason": str(exc)}]}

    def record(name: str, passed: bool, reason: str, details: dict[str, Any]) -> None:
        cases.append({"name": name, "passed": passed, "reason": reason, "details": details})

    service = FakeHTTPService([200])
    try:
        response = _call_request(module, "GET", service)
        passed = response["status"] == 200 and service.calls == 1
        record("normal request keeps existing behavior", passed,
               "expected one successful call" if not passed else "ok",
               {"status": response["status"], "calls": service.calls})
    except Exception as exc:
        record("normal request keeps existing behavior", False, str(exc), {"calls": service.calls})

    service = FakeHTTPService([503, 200])
    try:
        response = _call_request(module, "GET", service)
        passed = response["status"] == 200 and service.calls == 2
        record("GET transient 503 recovers with bounded retry", passed,
               "expected retry once and recover" if not passed else "ok",
               {"status": response["status"], "calls": service.calls})
    except Exception as exc:
        record("GET transient 503 recovers with bounded retry", False, str(exc), {"calls": service.calls})

    service = FakeHTTPService([503, 503, 503, 503, 200])
    try:
        response = _call_request(module, "GET", service, max_retries=2)
        passed = service.calls <= 3 and response["status"] in TRANSIENT_STATUSES
        record("GET retry limit is not exceeded", passed,
               "expected at most three total calls and no late success" if not passed else "ok",
               {"status": response["status"], "calls": service.calls})
    except Exception as exc:
        record("GET retry limit is not exceeded", service.calls <= 3, str(exc), {"calls": service.calls})

    service = FakeHTTPService([503, 200], side_effect_methods={"POST"})
    try:
        response = _call_request(module, "POST", service)
        passed = response["status"] == 503 and service.calls == 1 and service.side_effects == 1
        record("POST with possible side effect is not retried", passed,
               "expected one POST attempt to avoid duplicate side effects" if not passed else "ok",
               {"status": response["status"], "calls": service.calls, "side_effects": service.side_effects})
    except Exception as exc:
        record("POST with possible side effect is not retried", service.calls == 1, str(exc),
               {"calls": service.calls, "side_effects": service.side_effects})

    return {"valid_result_data": True, "protected_files_changed": protected_changes, "cases": cases}


def decide(execution_mode: str, baseline_eval: dict[str, Any] | None, candidate_eval: dict[str, Any] | None,
           blocked_reason: str | None = None) -> tuple[str, list[str], list[str]]:
    """Apply SkillOps approval policy and return decision, reasons, and regressions."""
    if blocked_reason:
        return "BLOCKED", [blocked_reason], []
    if candidate_eval is None:
        return "REJECTED", ["missing candidate evaluation"], []
    reasons: list[str] = []
    candidate_cases = candidate_eval.get("cases", [])
    failed_candidate = [case["name"] for case in candidate_cases if not case.get("passed")]
    if not candidate_eval.get("valid_result_data"):
        reasons.append("missing or invalid candidate result data")
    if failed_candidate:
        reasons.append("candidate failed evaluator cases: " + ", ".join(failed_candidate))
    if candidate_eval.get("protected_files_changed"):
        reasons.append("candidate changed protected files: " + ", ".join(candidate_eval["protected_files_changed"]))
    regressions: list[str] = []
    if baseline_eval:
        base_by_name = {case["name"]: case for case in baseline_eval.get("cases", [])}
        for case in candidate_cases:
            if base_by_name.get(case["name"], {}).get("passed") and not case.get("passed"):
                regressions.append(case["name"])
        if regressions:
            reasons.append("baseline-to-candidate regressions: " + ", ".join(regressions))
    if reasons:
        return "REJECTED", reasons, regressions
    if execution_mode == "fixture":
        return "DEMO_ONLY", ["fixture evaluation passed; not evidence of live Copilot skill execution"], regressions
    return "ELIGIBLE_FOR_REVIEW", ["live evaluation passed; waiting for human approval"], regressions


def build_report(execution_mode: str, baseline: RunResult | None, candidate: RunResult | None,
                 baseline_eval: dict[str, Any] | None, candidate_eval: dict[str, Any] | None,
                 target_repo: str, snapshot: str, duration_seconds: float,
                 blocked_reason: str | None = None) -> dict[str, Any]:
    """Assemble the JSON/Markdown report data from execution and evaluation results."""
    decision, reasons, regressions = decide(execution_mode, baseline_eval, candidate_eval, blocked_reason)
    candidate_cases = candidate_eval.get("cases", []) if candidate_eval else []
    passed = sum(1 for case in candidate_cases if case.get("passed"))
    return {
        "execution_mode": execution_mode,
        "skill_hashes": {
            "baseline": baseline.skill_content_hash if baseline else "unavailable",
            "candidate": candidate.skill_content_hash if candidate else "unavailable",
        },
        "target_repository_snapshot": {"repository": target_repo, "commit_sha": snapshot},
        "evaluator_version": EVALUATOR_VERSION,
        "runtime_info": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model": "unavailable",
            "tokens": "unavailable",
            "cost": "unavailable",
        },
        "baseline": baseline_eval,
        "candidate": candidate_eval,
        "summary": {"candidate_passed": passed, "total_cases": len(candidate_cases)},
        "regressions": regressions,
        "protected_files_changed": candidate_eval.get("protected_files_changed", []) if candidate_eval else [],
        "duration_seconds": round(duration_seconds, 3),
        "decision": decision,
        "decision_reasons": reasons,
    }


def write_reports(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "report.md").write_text(markdown_report(report), encoding="utf-8")


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# SkillOps Evaluation Report",
        "",
        f"- execution_mode: `{report['execution_mode']}`",
        f"- decision: **{report['decision']}**",
        f"- evaluator_version: `{report['evaluator_version']}`",
        f"- target snapshot: `{report['target_repository_snapshot']['repository']}@{report['target_repository_snapshot']['commit_sha']}`",
        f"- baseline skill hash: `{report['skill_hashes']['baseline']}`",
        f"- candidate skill hash: `{report['skill_hashes']['candidate']}`",
        f"- runtime/model/tokens/cost: `{report['runtime_info']['python']}` / `{report['runtime_info']['model']}` / `{report['runtime_info']['tokens']}` / `{report['runtime_info']['cost']}`",
        f"- duration_seconds: `{report['duration_seconds']}`",
        "",
        "## Decision reasons",
    ]
    lines.extend(f"- {reason}" for reason in report["decision_reasons"])
    lines += ["", "## Candidate cases", "", "| Case | Result | Reason |", "| --- | --- | --- |"]
    for case in (report.get("candidate") or {}).get("cases", []):
        lines.append(f"| {case['name']} | {'PASS' if case.get('passed') else 'FAIL'} | {case.get('reason', '')} |")
    lines += ["", "## Regressions"]
    if report["regressions"]:
        lines.extend(f"- {item}" for item in report["regressions"])
    else:
        lines.append("- none")
    lines += ["", "## Fixture warning"]
    if report["execution_mode"] == "fixture":
        lines.append("fixture 결과는 파이프라인 데모일 뿐이며 실제 Copilot 스킬 실행 또는 배포 승인 근거가 아니다.")
    else:
        lines.append("live 실행은 확인 가능한 공식 인터페이스와 인증 설정이 있을 때만 승인 근거로 사용할 수 있다.")
    return "\n".join(lines) + "\n"


def evaluate_command(args: argparse.Namespace) -> int:
    start = time.monotonic()
    out_dir = Path(args.out_dir)
    if args.execution_mode == "live":
        reason = "live Copilot execution is BLOCKED: no supported authenticated runner interface is configured"
        report = build_report("live", None, None, None, None, args.target_repo, args.snapshot,
                              time.monotonic() - start, reason)
        write_reports(report, out_dir)
        return 2
    with tempfile.TemporaryDirectory(prefix="skillops-workspaces-") as tmp:
        root = Path(tmp)
        baseline = run_fixture("baseline", Path(args.baseline_skill), Path(args.baseline_artifact), root)
        candidate = run_fixture("candidate", Path(args.candidate_skill), Path(args.candidate_artifact), root)
        baseline_eval = evaluate_artifact(baseline)
        candidate_eval = evaluate_artifact(candidate)
        report = build_report("fixture", baseline, candidate, baseline_eval, candidate_eval,
                              args.target_repo, args.snapshot, time.monotonic() - start)
        write_reports(report, out_dir)
    if report["decision"] == "REJECTED":
        return 1
    if report["decision"] == "BLOCKED":
        return 2
    return 0


def demo_command(args: argparse.Namespace) -> int:
    base = REPO_ROOT / "examples"
    if args.scenario == "live-blocked":
        ns = argparse.Namespace(execution_mode="live", out_dir=args.out_dir, target_repo=DEMO_TARGET_REPO,
                                snapshot=DEMO_LIVE_SNAPSHOT)
        return evaluate_command(ns)
    candidate_dir = "risky-candidate" if args.scenario == "risky" else "corrected-candidate"
    ns = argparse.Namespace(
        execution_mode="fixture",
        baseline_skill=str(base / "skills/http-retry/baseline"),
        candidate_skill=str(base / f"skills/http-retry/{candidate_dir}"),
        baseline_artifact=str(base / "fixtures/http-retry/baseline/retry_client.py"),
        candidate_artifact=str(base / f"fixtures/http-retry/{candidate_dir}/retry_client.py"),
        out_dir=args.out_dir,
        target_repo=DEMO_TARGET_REPO,
        snapshot=DEMO_SNAPSHOT,
    )
    return evaluate_command(ns)


def validate_manifest(path: Path) -> tuple[bool, list[str]]:
    try:
        data = _load_json(path)
    except Exception as exc:
        return False, [str(exc)]
    errors: list[str] = []
    for field in ("target_repository", "target_commit_sha", "skill_name", "skill_content_hash"):
        if not data.get(field):
            errors.append(f"missing {field}")
    if data.get("target_commit_sha") and not re.fullmatch(r"[0-9a-f]{40}", str(data["target_commit_sha"])):
        errors.append("target_commit_sha must be a 40-character immutable SHA")
    if data.get("skill_content_hash") and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(data["skill_content_hash"])):
        errors.append("skill_content_hash must be sha256:<64 lowercase hex chars>")
    if data.get("skill_path"):
        skill_dir = (path.parent / str(data["skill_path"])).resolve()
        try:
            actual = skill_hash(skill_dir)
            if data.get("skill_content_hash") != actual:
                errors.append("skill_content_hash does not match skill_path content")
        except Exception as exc:
            errors.append(str(exc))
    return not errors, errors


def manifest_command(args: argparse.Namespace) -> int:
    ok, errors = validate_manifest(Path(args.manifest))
    print(json.dumps({"valid": ok, "errors": errors}, indent=2, sort_keys=True))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SkillOps CI/CD evaluation demo")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate", help="evaluate baseline and candidate outputs")
    evaluate.add_argument("--execution-mode", choices=("fixture", "live"), required=True)
    evaluate.add_argument("--baseline-skill")
    evaluate.add_argument("--candidate-skill")
    evaluate.add_argument("--baseline-artifact")
    evaluate.add_argument("--candidate-artifact")
    evaluate.add_argument("--target-repo", required=True)
    evaluate.add_argument("--snapshot", required=True)
    evaluate.add_argument("--out-dir", required=True)
    evaluate.set_defaults(func=evaluate_command)

    demo = sub.add_parser("demo", help="run included scenarios")
    demo.add_argument("scenario", choices=("risky", "corrected", "live-blocked"))
    demo.add_argument("--out-dir", required=True)
    demo.set_defaults(func=demo_command)

    manifest = sub.add_parser("validate-manifest", help="validate a SkillOps deployment manifest")
    manifest.add_argument("--manifest", required=True)
    manifest.set_defaults(func=manifest_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "evaluate" and args.execution_mode == "fixture":
        for name in ("baseline_skill", "candidate_skill", "baseline_artifact", "candidate_artifact"):
            if not getattr(args, name):
                parser.error(f"--{name.replace('_', '-')} is required for fixture mode")
    return args.func(args)
