# Anthropic Skill 가이드 평가 설계

## 목표

등록된 프로젝트의 skill을 `SKILL.md` 단위로 자동 발견하고, 각 skill의 실제 크기와 번들 구성에 맞춰 Anthropic `skill-creator` 작성 가이드 준수 여부를 평가한다. 기존 coding-task 품질 점수나 후보 승격 판정과 합산하지 않고 별도 보고 항목으로 제공한다.

## 평가 단위 발견

프로젝트 루트의 `.github/skills/`, `.claude/skills/`, `skills/` 아래에서 `SKILL.md`를 찾는다. 각 `SKILL.md`의 부모 디렉터리가 하나의 skill 번들이다. 중첩된 다른 `SKILL.md`의 디렉터리는 별도 skill이므로 상위 번들에서 제외한다.

발견 단계는 `scandir`로 명시적으로 탐색하며 skill root와 그 아래의 symlink를
`unsafe_skill_path`로 거부한다. 최초 발견 시 경로를 no-follow descriptor로 먼저 열고
`fstat`의 device/inode/type을 기록한다. 각 conventional root는 최초 확인에서 부재한
경우에만 건너뛰며, open이 부재를 보고한 component는 no-follow stat으로 다시 확인한다.
존재하는 root는 디렉터리여야 하며,
탐색 직전에도 경로 identity를 비교하여 삭제나 파일·symlink·다른 디렉터리 교체를 거부한다.
프로젝트 경로는 symlink를 따라가지 않는 절대 lexical 정규화를 사용한다.
프로젝트 자체 symlink도 거부하며, 프로젝트 root와 `.github`, `.claude`, `skills` 등
각 존재하는 경로 component, 탐색 디렉터리와 파일 후보의 원본 descriptor를 내부 snapshot에
보존한다. 모든 root 탐색 후와 모든 번들 읽기 후에 기록 경로를 일괄 재검증한다.
snapshot의 descriptor는 최종 검증까지 유지하므로 delete→recreate로 원본 inode를
재사용할 수 없다. 이 보호는 timestamp 비교에 의존하지 않는다.
다른 root 탐색 중 발생한 삭제·교체·symlink·type 변화도 `unsafe_skill_path`로 거부한다.
중첩 child 번들 파일을 제외한 후보 상대 경로를 읽기 단계에 전달하며 재열거하지 않는다.
`SKILL.md`는 먼저 정확히 한 번 읽는다. 발견한 resource나 경로 디렉터리가 읽기 전에
삭제·rename·교체되면 `unsafe_skill_path`로 거부하고 SKILL-only 번들을 반환하지 않는다.
최초 탐색 이후 추가된 파일은 다음 `discover` 호출에서 발견한다.

등록 프로젝트가 없을 때는 SkillOps 자체의 `skills/`를 같은 규칙으로 평가한다. manifest와 개별 override는 실제 자동 발견 오분류가 확인되기 전까지 추가하지 않는다.

## 크기에 따른 평가

모든 skill에 동일한 고정 비용을 부과하지 않는다.

- 리소스가 없는 작은 skill은 `SKILL.md`만 한 번 평가한다.
- `references/`, `scripts/`, `assets/` 등 번들 파일이 있으면 파일 목록과 텍스트 내용을 추가 평가한다.
- 큰 텍스트 번들은 제한된 크기의 파일 배치로 나누어 여러 judge 호출로 평가한다.
- 바이너리 asset은 내용을 모델에 보내지 않고 경로, 크기, 형식만 기록한다.

따라서 작은 skill은 작은 문맥과 호출 수를 사용하고, 큰 skill은 전체 번들에 비례해 더 많은 근거와 호출을 사용한다. 개별 파일과 전체 번들에는 명시적인 크기 제한을 적용한다.

파일은 각각 2 MiB 이하, 번들 합계는 8 MiB 이하까지 허용한다. 합계가 8 MiB를 넘으면
해당 skill을 `skill_bundle_limit`로 차단한다. 같은 경로와 내용의 정상 번들은 반복 발견 시 같은 SHA-256을
가지며, 파일 내용이 바뀌면 번들 hash도 바뀐다.
실제 judge prompt 전체(rubric, static, manifest, batch, 배치 번호)의 UTF-8 크기는
`CopilotRuntime`과 공유하는 100,000-byte 한도 이하다. 48 KiB 파일 배치만으로 판단하지 않고
직렬화된 전체 prompt 크기로 여유를 계산한다. 큰 static metadata/findings는 잘라 버리지 않고
순서 있는 `static.json_fragment` 배치로 분할하며, applicability map만 각 배치에 유지한다.
작은 static은 그대로 전달하고, rubric이 큰 경우 파일 배치 크기도 줄인다.

## 정적 검사

표준 라이브러리만 사용해 다음을 검사한다.

- YAML frontmatter 구분자와 비어 있지 않은 `name`, `description`
- 비어 있지 않은 본문
- Anthropic 가이드의 `SKILL.md` 500줄 권고
- 본문에서 참조한 로컬 파일의 존재와 번들 밖 경로 거부
- 300줄을 넘는 Markdown reference의 목차 존재 여부
- symlink, 비정상 파일, UTF-8 오류와 크기 제한

발견 시 `dir_fd`, `O_NOFOLLOW`, `O_NONBLOCK`으로 각 후보를 열어 `fstat`으로 검사한다.
디렉터리에는 `O_DIRECTORY`도 적용하며, 절대 경로와 `..` 요소는 거부한다.
번들 읽기는 보존한 원본 descriptor만 사용하며 각 상대 경로 component의 identity도 확인한다.
일반 파일의 실제 바이트·텍스트·hash까지 완성한 뒤 최종 경로 검증을 수행한다.
내부 `ExitStack`은 성공 및 모든 오류 경로에서 descriptor를 닫으며,
공개 `discover` 결과에는 live descriptor가 없다.
symlink 및 FIFO/device/socket/directory 교체는 내용을 읽지 않고 `unsafe_skill_path`로 거부한다.

500줄과 목차 항목은 권고 finding이며, 누락 파일·안전하지 않은 경로·필수 frontmatter 오류는 명백한 구조 오류로 기록한다.

`static_assessment(bundle)`은 이미 발견한 번들만 검사하고 `metadata`, `applicability`,
`findings`를 반환한다. 정적 오류는 예외로 실행을 차단하지 않는다. Frontmatter는 단순
scalar `name`/`description`과 들여쓴 continuation만 읽으며 전체 YAML parser는 추가하지
않는다. 본문의 inline Markdown 링크는 destination과 optional single/double/paren title을
구분한다. angle destination, 균형 잡힌 괄호, escaped punctuation/space를 지원하는
최소 scanner이며 전체 Markdown parser는 아니다. 유효 destination을 URL 디코딩한 뒤
번들 파일 목록과 비교하며 외부 URL과 anchor는 무시한다. 잘못 닫힌 destination/title은
링크로 취급하지 않는다. 300줄 초과 Markdown resource의 목차 heading은 첫 80줄
안에 있어야 한다.

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
`invalid_skill_encoding`, `skill_file_limit`, `skill_bundle_limit`은 `discover`가 안전하게
식별한 bundle path와 오류로 반환하며, 해당 skill은 judge 호출 없이 `blocked`가 된다.
내용 평가가 차단된 번들은 정상 bundle SHA-256을 반환하지 않는다(`sha256: null`).
평가 전후 변경 감지를 위해 이 번들도 모든 후보의 원본 descriptor로 raw bytes를 hash한다.
64 KiB씩 최초 파일 길이까지만 읽어 추가 메모리를 제한하고, 길이가 바뀌면 전체 실행을 차단한다.
이 fingerprint는 경로·identity·크기·content hash를 포함하며 timestamp에 의존하지 않는다.
크기 초과 파일도 일관성 검증을 위한 전체 길이만큼의 I/O는 필요하지만 judge에 전달하지 않는다.
root 탐색 등 bundle 경로조차 안전하게 식별할 수 없는 구조적 오류는 계속 전체 `RuntimeFailure`다.

## 검증

작은 단일 파일 skill, reference가 있는 중간 skill, 500줄·300줄 경계를 넘는 큰 skill, 중첩 skill, 깨진 참조, symlink, binary asset fixture를 사용한다. 테스트는 자동 발견, 번들 경계, 조건부 항목, 배치 수 증가, 부분 실패 지속, 보고 전용 판정 불변을 확인한다. 마지막에 전체 오프라인 unittest를 실행한다.

## 문서

영문·한글 README에 발견 경로, skill별 평가 항목, 크기에 따른 호출 증가, 보고 전용이라는 점과 Anthropic 가이드 기반이지만 공식 인증이나 사람 교정을 대체하지 않는다는 제한을 설명한다.
