"""Deterministic display-only examples; never run models or modify project Skills."""

from difflib import unified_diff


PROJECTS = ("project-a", "project-b")
DIMENSIONS = (
    ("trigger_description", "사용 조건의 명확성"),
    ("workflow_clarity", "작업 흐름의 명확성"),
    ("generalization", "일반화"),
    ("instruction_quality", "지침 품질"),
    ("principle_of_lack_of_surprise", "의도 밖 동작 방지"),
    ("progressive_disclosure", "단계적 정보 제공"),
    ("resource_organization", "리소스 구성"),
)
SCENARIOS = (
    ("improved", "개선", 4, 5, 90, 80, 40,
     "Record the expected behavior before editing. Verify the change and unchanged paths before reporting."),
    ("unchanged", "유지", 3, 4, 80, 100, 50,
     "Summarize the same workflow using a short checklist without changing the verification steps."),
    ("regressed", "악화", 2, 3, 60, 120, 65,
     "Repeat the same inspection twice and omit the final unchanged-path check."),
)


def project_sample(identifier, inventory):
    skills = []
    for detected in inventory:
        name = detected["display_name"]
        original = (
            f"---\nname: {name}\ndescription: Synthetic workflow example for {name}.\n---\n\n"
            f"# {name} - synthetic original\n\n"
            "This is display-only sample text, not the repository's actual SKILL.md.\n"
            "Inspect the requested change. Preserve unrelated behavior. Verify the result.\n"
        )
        versions = {"base": {"version": "합성 원본 · 실제 SKILL.md 아님", "content": original}}
        reports = []
        for ordinal, (scenario, label, score, passed, judge, cost, seconds, change) in enumerate(SCENARIOS, 1):
            candidate_id = f"candidate-{ordinal}"
            candidate = original + f"\n## Synthetic candidate {ordinal} - {scenario}\n\n{change}\n"
            versions[candidate_id] = {"version": f"합성 후보 {ordinal} · 미채택", "version_id": candidate_id, "content": candidate}
            quality = []
            for index, (dimension, title) in enumerate(DIMENSIONS):
                applicable = index < 5
                candidate_score = score if index < 2 else 3
                quality.append({
                    "id": dimension, "name": title, "method": "합성 rubric 예시",
                    "base": "pass" if applicable else "not_applicable",
                    "candidate": ("review" if candidate_score < 3 else "pass") if applicable else "not_applicable",
                    "base_score": 3 if applicable else None,
                    "candidate_score": candidate_score if applicable else None,
                    "finding": f"합성 예시: {label} 시나리오의 기준·후보 점수이며 실제 LLM 채점이 아닙니다."
                    if applicable else "합성 단일 문서 예시에는 적용하지 않습니다. 실제 프로젝트 번들의 판단이 아닙니다.",
                })
            reports.append({
                "run_id": f"sample-{identifier}-{ordinal}",
                "purpose": "comparison", "scenario": scenario, "round_label": f"{ordinal}회차 · {label}",
                "created_at": f"2026-09-17T0{4 - ordinal}:00:00Z", "origin": "synthetic",
                "guide": {"status": "completed", "reason_code": "evaluation_completed", "metrics": None, "decision": None},
                "execution": {
                    "status": "completed", "reason_code": "evaluation_completed", "decision": None,
                    "metrics": {
                        "base_requested": 5, "candidate_requested": 5,
                        "base_correctness_successes": 4, "candidate_correctness_successes": passed,
                        "base_judge_score": 80, "candidate_judge_score": judge,
                        "base_cost_nano_aiu": 100000000000, "candidate_cost_nano_aiu": cost * 1000000000,
                        "base_elapsed_seconds": 50, "candidate_elapsed_seconds": seconds,
                    },
                },
                "details": {
                    "base": "base", "candidate": candidate_id,
                    "scope": f"{identifier} / {name} · 합성 작업 5개. 이름·경로만 실제 탐지 정보이며 "
                    "지침 본문·시각·점수·검사·비용·시간은 예시입니다. 실제 프로젝트 검사 범위를 나타내지 않습니다.",
                    "quality": quality,
                    "generation": {
                        "synthetic": True, "status": "completed", "candidate_count": 1,
                        "candidate_version": candidate_id, "model_invocations": 0,
                        "adoption": "not_adopted", "requires_explicit_approval": True,
                        "diff": "".join(unified_diff(original.splitlines(True), candidate.splitlines(True),
                                                    fromfile="synthetic-base/SKILL.md",
                                                    tofile=f"{candidate_id}/SKILL.md")),
                    },
                    "improvement": {
                        "observation": f"합성 관측: {name}의 작업 순서와 확인 기준을 개선할 여지가 있다고 가정했습니다.",
                        "hypothesis": "검증 순서를 명시하면 같은 작업에서 누락과 재작업을 줄일 수 있다는 합성 가설입니다.",
                        "change": change,
                        "verification": f"합성 재평가: 기본 품질 3 → {score}, 정답 작업 4/5 → {passed}/5. "
                        f"프로젝트 실행 비용·시간은 {label} 예시입니다.",
                        "conclusion": f"{label} 시나리오 · 후보 미채택. 합성 결과는 채택 근거가 아니며 실제 적용에는 명시적 승인이 필요합니다.",
                    },
                    "policy": {
                        "version": "synthetic-manual-approval-v1",
                        "summary": "미채택 · 명시적 승인 필요. 이 샘플의 실행 완료나 점수 상승은 채택을 뜻하지 않습니다.",
                        "rows": [
                            {"name": "기본 Skill 품질", "threshold": "기준·후보를 같은 rubric으로 비교하는 예시",
                             "observed": f"3 → {score} / 4"},
                            {"name": "프로젝트 평가", "threshold": "동일한 합성 작업 5개 비교",
                             "observed": f"4/5 → {passed}/5"},
                            {"name": "채택·원본 변경", "threshold": "명시적 승인 전 적용 금지",
                             "observed": "미채택 · 원본 변경 없음"},
                        ],
                    },
                    "tasks": [{"name": f"합성 작업 {task}", "base": int(task <= 4),
                               "candidate": int(task <= passed), "total": 1} for task in range(1, 6)],
                },
            })
        skills.append({"id": detected["skill_key"], "name": name, "source_path": detected["source_path"],
                       "versions": versions, "reports": reports})
    return {"schema_version": 1, "synthetic": True, "project_id": f"sample-{identifier}",
            "source_project_id": identifier, "name": f"{identifier} · 합성 평가 샘플",
            "round_count": 3, "skills": skills}
