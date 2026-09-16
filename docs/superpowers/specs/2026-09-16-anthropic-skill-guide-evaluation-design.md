# Anthropic Skill 가이드 평가 설계

## 목표

등록된 프로젝트의 skill을 `SKILL.md` 단위로 자동 발견하고, 각 skill의 실제 크기와 번들 구성에 맞춰 Anthropic `skill-creator` 작성 가이드 준수 여부를 평가한다. 기존 coding-task 품질 점수나 후보 승격 판정과 합산하지 않고 별도 보고 항목으로 제공한다.

## 평가 단위 발견

프로젝트 루트의 `.github/skills/`, `.claude/skills/`, `skills/` 아래에서 `SKILL.md`를 찾는다. 각 `SKILL.md`의 부모 디렉터리가 하나의 skill 번들이다. 중첩된 다른 `SKILL.md`의 디렉터리는 별도 skill이므로 상위 번들에서 제외한다.

등록 프로젝트가 없을 때는 SkillOps 자체의 `skills/`를 같은 규칙으로 평가한다. manifest와 개별 override는 실제 자동 발견 오분류가 확인되기 전까지 추가하지 않는다.

## 크기에 따른 평가

모든 skill에 동일한 고정 비용을 부과하지 않는다.

- 리소스가 없는 작은 skill은 `SKILL.md`만 한 번 평가한다.
- `references/`, `scripts/`, `assets/` 등 번들 파일이 있으면 파일 목록과 텍스트 내용을 추가 평가한다.
- 큰 텍스트 번들은 제한된 크기의 파일 배치로 나누어 여러 judge 호출로 평가한다.
- 바이너리 asset은 내용을 모델에 보내지 않고 경로, 크기, 형식만 기록한다.

따라서 작은 skill은 작은 문맥과 호출 수를 사용하고, 큰 skill은 전체 번들에 비례해 더 많은 근거와 호출을 사용한다. 개별 파일과 전체 번들에는 명시적인 크기 제한을 적용한다.

## 정적 검사

표준 라이브러리만 사용해 다음을 검사한다.

- YAML frontmatter 구분자와 비어 있지 않은 `name`, `description`
- 비어 있지 않은 본문
- Anthropic 가이드의 `SKILL.md` 500줄 권고
- 본문에서 참조한 로컬 파일의 존재와 번들 밖 경로 거부
- 300줄을 넘는 Markdown reference의 목차 존재 여부
- symlink, 비정상 파일, UTF-8 오류와 크기 제한

번들별 읽기는 프로젝트 또는 bundle root descriptor를 한 번 고정한다.
`rglob`/`stat`은 후보 열거에만 사용하고, 상대 경로의 각 디렉터리와 마지막 파일은
`dir_fd`, `O_NOFOLLOW`, `O_NONBLOCK`으로 열어 `fstat`으로 검사한다.
디렉터리에는 `O_DIRECTORY`도 적용하며, 절대 경로와 `..` 요소는 거부한다.
실제로 연 일반 파일의 크기·내용·hash만 사용하고 모든 descriptor는 `finally`에서 닫는다.
symlink 및 FIFO/device/socket/directory 교체는 내용을 읽지 않고 `unsafe_skill_path`로 거부한다.

500줄과 목차 항목은 권고 finding이며, 누락 파일·안전하지 않은 경로·필수 frontmatter 오류는 명백한 구조 오류로 기록한다.

## LLM rubric

도구 없는 독립 judge가 Anthropic 가이드에 근거해 적용 가능한 항목만 평가한다.

- description이 skill의 기능과 trigger 상황을 구체적으로 설명하는가
- 목적, 입력, 출력과 작업 흐름이 명확한가
- 지침이 일반화되어 있고 특정 예제에 과적합하지 않는가
- 불필요한 반복과 강압적 규칙 대신 간결한 명령형과 이유 설명을 사용하는가
- 큰 skill이 progressive disclosure를 사용하고 관련 reference만 읽도록 안내하는가
- 반복적·결정적 작업을 scripts로, 지식 자료를 references로 적절히 분리하는가
- 설명된 의도와 다른 놀라운 동작이나 위험한 지침이 없는가

리소스가 없는 작은 skill에는 progressive disclosure와 리소스 구성 항목을 `not_applicable`로 기록하고 분모에서 제외한다. judge 결과는 정적 검사를 덮어쓰지 않으며, 사람 검토로 교정되지 않은 정성 평가라는 제한을 보고서에 명시한다.

## 보고서

baseline JSON과 Markdown에 `anthropic_skill_guide` 섹션을 추가한다.

- 프로젝트 요약: 발견한 skill 수와 `pass`, `review`, `blocked` 개수
- skill별 결과: 경로, bundle hash, 파일 수·바이트, 적용 항목, 정적 findings, rubric 점수·근거, judge 호출 수
- 파일/배치별 artifact: judge 입력에 사용한 manifest와 정규화된 호출 기록

프로젝트 전체 평균 점수는 만들지 않는다. 각 skill은 독립적으로 표시해 큰 skill이나 다수의 작은 skill이 다른 결과를 가리지 않게 한다. 이 섹션은 보고 전용이며 기존 `eligible_for_canary`, `rejected`, `blocked` 판정에 영향을 주지 않는다.

## 오류 처리

한 skill의 잘못된 frontmatter나 파일은 다른 skill 평가를 중단하지 않는다. 해당 skill만 `blocked` 또는 `review`로 기록하고 다음 skill을 계속 평가한다. 프로젝트 경로 자체가 안전하지 않거나 평가 중 입력이 변경되면 기존 원칙대로 전체 실행을 차단한다.

## 검증

작은 단일 파일 skill, reference가 있는 중간 skill, 500줄·300줄 경계를 넘는 큰 skill, 중첩 skill, 깨진 참조, symlink, binary asset fixture를 사용한다. 테스트는 자동 발견, 번들 경계, 조건부 항목, 배치 수 증가, 부분 실패 지속, 보고 전용 판정 불변을 확인한다. 마지막에 전체 오프라인 unittest를 실행한다.

## 문서

영문·한글 README에 발견 경로, skill별 평가 항목, 크기에 따른 호출 증가, 보고 전용이라는 점과 Anthropic 가이드 기반이지만 공식 인증이나 사람 교정을 대체하지 않는다는 제한을 설명한다.
