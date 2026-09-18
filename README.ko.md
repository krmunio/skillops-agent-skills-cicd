# Self-Evolving Agent SkillOps

[English](README.md) | [한국어](README.ko.md)

**자가 진화 에이전트 스킬옵스 — 에이전트 행동 규칙을 위한 CI/CD**

코드에는 CI/CD가 있는데, 에이전트의 행동 규칙에는 왜 없을까요?
프로젝트의 Skill 묶음을 탐지하고, 관측된 문제를 바탕으로 개선 후보를 생성한 뒤,
기존 버전과 비교해 품질과 프로젝트 회귀를 검증합니다.
여기서 자가 진화는 **후보 생성과 평가의 자동화**이며, 원본 Skill을 몰래 덮어쓰거나
검증하지 않은 후보를 곧바로 채택·배포한다는 뜻이 아닙니다.

## 개선 흐름

```text
프로젝트 등록
  → Skill 묶음 탐지
  → 지침 품질 평가·개선 근거 수집
  → 평가 가능한 Skill별 후보 생성
  → 기존·후보 재평가
  → 지원되는 프로젝트 동작의 회귀 검증
  → 버전·결과·판정 보존
```

Skill은 파일 하나나 특정 워크플로 하나로 한정하지 않습니다.
`.github/skills`, `.claude/skills`, `skills`와 그 하위 경로의 `SKILL.md`를 탐지하며,
해당 묶음에는 참조 문서·스크립트·자산이 포함될 수 있습니다.
이름이 같아도 경로가 다르면 구분하고, 버전은 진입점뿐 아니라 묶음 전체의 내용을 기록합니다.
현재 후보 생성은 frontmatter와 부속 파일을 보존하면서 `SKILL.md` 본문을 수정합니다.

## 평가 구분

| 영역 | 확인할 내용 | 근거 |
| --- | --- | --- |
| 베이스라인 평가 | 지침이 명확하고 개선에 타당한 근거가 있는가? | Anthropic 가이드를 참고한 지침 품질, APO 접근을 참고한 문제·가설·변경·재평가의 연결 |
| 프로젝트 평가 | 변경된 Skill이 작업을 수행하면서 회귀를 만들지 않았는가? | 공통 과제 비교와 실제 프로젝트 검사, 범위를 구분한 품질·실패·비용·시간 |
| 채택 상태 | 후보가 실제로 승인·적용되었는가? | 명시적인 채택 기록. 점수 상승이나 검사 통과만으로 채택을 추정하지 않음 |

현재 공통 과제 벤치마크와 탐지 기반 프로젝트 검사는 별도 실행 경로입니다.
CLI의 `baseline` 명령은 기존 Skill의 공통 과제 벤치마크를 뜻하며,
대시보드 설계에서 말하는 지침 품질 중심의 베이스라인 평가와 구분합니다.
검사 부재·미지원 실행·실패 단계·미기록 측정값은 그대로 표시합니다.
검증할 작업을 도출하지 못하면 실행 효과는 `unverified`이며, 성공으로 꾸미지 않습니다.

### 설계 중인 기능

대시보드는 프로젝트 → Skill 묶음 → 버전·후보 순서로 선택하고,
상단에서 채택 상태와 간단한 요약을 확인한 뒤 베이스라인 평가와 프로젝트 평가를
위아래로 살펴보는 구조로 개편 중입니다. 해시와 상세 근거는 펼쳐보도록 설계합니다.

평가셋은 add-on 방식으로 추가·비교·복수 선택할 수 있도록 설계 중이며,
Anthropic 기반 지침 품질과 APO 기반 개선 근거를 기본 선택 항목으로 둡니다.
**평가셋 카탈로그·설정 기능은 아직 구현되지 않았습니다.**
과거 결과는 당시 사용한 평가셋·버전을 유지하고, 서로 다른 점수를 임의로 합산하지 않습니다.

## 협업

[로드맵](docs/ROADMAP.md)에서 향후 기능과 의존성을,
[기여 가이드](CONTRIBUTING.md)에서 작업 선택·변경 조율·검증 근거 공유 방법을 확인할 수 있습니다.
공유 문서는 영어로 작성하며, 계획된 기능을 구현 완료로 해석하지 않습니다.

## 다중 프로젝트 기반 구조

샘플은 `projects/sample_repo/`로 이동했습니다.
Owner가 운영하는 [공개 대시보드](https://agreeable-pebble-0ea54a800.6.azurestaticapps.net/)에
접속할 수 있으며, 화면 구성은 개편 중입니다.
[프로젝트 평가 안내](docs/PROJECT-EVALUATION.md)에 `results/` 형식, Actions,
결과 전용 브랜치와 정적 대시보드 사용법이 있습니다.
`skill_guide.py`의 Anthropic 가이드 기반 평가기는 구현되어 있으며 baseline 실행에 연동되어 있습니다.
프로젝트별 Skill 탐지 → 품질 평가 → 후보 생성·재평가 → 원본/기존/후보 적용 결과의 회귀 비교가
연결되어 있습니다. 별도 루트 어댑터 등록은 필요하지 않습니다. 지원되는 검사나 검증 가능한
작업이 없으면 실행 효과는 `unverified`로 남깁니다. 실제 모델 실행에는 명시적 설정과 인증이 필요합니다.
Actions의 단계별 로그와 실행 요약은 Anthropic 베이스라인 품질, APO-inspired 개선 근거 연결,
프로젝트 회귀 검사를 구분합니다. `python3 evaluation_reporting.py --results results --run-id <saved-run-id>`로
모델 호출 없이 저장된 근거의 요약을 확인할 수 있습니다. 별도 평가 job이나 APO 점수를 추가하는 기능은 아닙니다.
main push에서는 추가·변경된 프로젝트만 선택하며, 공통 평가기 변경은 전체 목록을 대상으로 합니다.
선택된 Skill마다 후보는 최대 1개이고 전체 실행 한도를 공유합니다. 기존 `--project <id>` 수동 선택은 유지하며,
`--changed-since <full-before-sha>`는 체크아웃된 `--source-commit`을 기준으로 영향받은 프로젝트를 선택합니다.
두 선택 옵션은 함께 사용할 수 없습니다. 대상이 없으면 모델 평가를 생략하며 통과 결과를 만들지 않습니다.
새 자동 Skill 실행은 보고서에 연결된 `stage-metrics.json`에 단계별 완료 여부, 한도 내에서 시도된 CLI 호출,
소요 시간과 확인 가능한 사용량을 보존합니다. 알 수 없는 사용량은 null로 남기며, Actions 요약은 부분 측정값과
전체 비용을 구분하고 기록된 판정 정책을 표시합니다. 기존 결과 형식과 대시보드 동작은 유지합니다.
Actions 수동 실행에서는 명시한 `project`에 `max_invocations`, `max_seconds`(최대 7200),
`max_ai_credits`를 지정할 수 있습니다. 호출·시간 예산은 해당 프로젝트의 모든 Skill이 공유하고,
Credit 한도는 세션별입니다. 재정의하지 않으면 저장소의 기본 설정을 유지합니다.

대상 Skill 드롭다운은 실제 탐지 목록과 보관된 이력을 함께 사용하며, 미평가 Skill도 선택할 수 있습니다.
project-a·b의 일반 평가 이력에 화면 구성용 개선·유지·악화 3회분을 저장했습니다.
`results/<project>/sample-<run>/`에 원본·후보, 생성 근거·diff, 기본 품질·프로젝트 평가를
실제 평가와 동일한 보고서·assessment·evolution 형식으로 보관합니다. 별도 화면이나 배지는 없으며,
출처는 `origin: sample` 메타데이터에서 확인합니다. 예시 본문·수치는 실측값이 아니므로
실평가의 과거 근거 및 현재 버전 검증에서는 제외합니다. 모델 호출·원본 변경·채택은 하지 않습니다.
배포 시 publisher가 `merge-samples`로 결과 브랜치에 추가하고, 정적 빌드는 저장된 결과만 읽습니다.

### 기록된 개발 작업의 단일 재평가

Opt-in 어댑터는 기록된 development WorkItem과 고정된 원본 Skill을 기준으로
후보 1개를 평가합니다. 기존 자동 단일 후보 경로는 변경하지 않습니다.

```bash
python3 skillops.py replay --project <project-id> --skill-key <discovered-skill-key> \
  --work-item <private-development-json> --results <replay-results-directory>
```

`--live`를 명시하고 환경에 `SKILLOPS_LIVE_EVALUATION_ENABLED=true`,
인증(`COPILOT_GITHUB_TOKEN` 또는 `GITHUB_TOKEN`), 승인된
`SKILLOPS_MAX_INVOCATIONS`, `SKILLOPS_MAX_SECONDS`,
`SKILLOPS_MAX_AI_CREDITS_PER_SESSION`을 제공하지 않으면 **모델 호출 전에 차단**합니다.
활성화 전 프로젝트·Skill·한도에 대한 별도 승인을 받아야 합니다.
Credit은 세션별 soft cap이며 전체 금액의 보장된 상한이 아닙니다.
모든 단계는 하나의 호출·시간 예산을 공유합니다. 프로젝트는 `projects/` 아래에 있어야 하며,
비공개 WorkItem은 실제 Git 커밋·요청·소스 해시·보호된 검사를 고정합니다.
스키마와 재사용 콜백은 [replay 계약](docs/HACKATHON-CONTRACTS.md)을 참고하세요.

실행마다 불변 `report.json`, 전체 번들의 `skill-evolution.json`,
`replay-evaluation.json`을 저장하고 CLI가 정확한 근거 참조를 반환합니다.
Confirmation은 `not_run` 및 `confirmation_isolation_unverified`로 남고 승인 가능 여부는 항상 false입니다.
개발 평가의 개선은 최종 확인·승인·다음 실행의 검증된 사용을 뜻하지 않으며 Active는 변경하지 않습니다.
개발 반복은 아래 `iterate` 명령으로 연결했습니다.
승인/다음 실행 CLI와 실제 반복 평가 Actions는 별도 연결 범위입니다.
공식 게시 경로는 이제 검증된 replay/cycle 근거를 보존하지만 adoption 게시는 계속 차단합니다.
오프라인 통합 테스트는 모델·컨테이너 경계만 대체하고
결과를 `offline_test`로 표시하며 실측 모델 개선으로 표시하지 않습니다.

### 제한된 개발 반복과 오프라인 데모

```bash
python3 skillops.py iterate --project <project-id> --skill-key <discovered-skill-key> \
  --work-item <private-development-json> --confirmation-work-item <private-confirmation-json> \
  --max-rounds 2 --results <iteration-results-directory>
```

실제 `run_cycle`과 기존 replay 저장 콜백을 사용합니다. 비교 기준은 처음 원본으로 고정하고,
부모 후보와 development 피드백만 다음 라운드로 연결합니다.
준비·모든 라운드·선택적 최종 확인은 같은 runtime과 승인된 예산을 공유합니다.
단일 replay와 같은 `--live`·인증·한도 조건이 필요하므로 위 예제만으로 모델을 호출하지 않습니다.
Confirmation 입력은 선택 사항이며 생성 전에 고정하지만 격리 검증 차단은 유지합니다.
후보를 승인하거나 Active로 바꾸지 않습니다.

저장된 평가마다 별도 run을 만들고 종료 `cycle.json`과 집계 `report.json`으로 연결합니다.
실제 loader가 참조 해시와 전체 캡처를 다시 검증합니다. 시작한 시도가 저장 전에 끝나면
`run_id`·`evaluation_ref`·`decision`은 null이며, 시작하지 않은 라운드를 만들지 않습니다.
저장 콜백 자체의 실패는 예외로 전파하고 종료 cycle·성공 영수증을 만들지 않습니다.
이미 저장된 라운드는 보존하며 자동 재시도·복구는 하지 않습니다.
종료 코드 0은 정상적인 개발 반복 종료일 뿐 최종 확인·승인이 아닙니다.
예산·runtime·미검증 종료는 코드 2를 반환합니다.

### 별도의 로컬 승인

구현된 `approve` 명령은 사람이 별도로 조작하는 대화형 터미널과 최종 확인을 통과한 live cycle,
정확한 전체 번들·평가 근거 해시를 요구합니다. CI/Actions·파이프 입력·이전 Active 필드 누락은
거부합니다. 두 `none`은 이전 Active가 없다는 명시적 null이며 wildcard가 아닙니다.

```bash
python3 skillops.py approve --project <project-id> --skill-key <skill-key> \
  --candidate-version sha256:<full-bundle-hash> --cycle <cycle-id> \
  --evidence-sha256 <cycle-file-sha256> --expected-active-version none \
  --expected-active-execution-sha256 none --results <results-directory>
```

표시된 전체 바인딩을 확인하고 `approve <전체 후보 버전>`을 별도로 입력합니다.
승인은 모델을 호출하거나 Active를 바꾸지 않습니다. 원본 Skill·기존 평가 근거는 유지하며,
replay 실행 전에 정규 private WorkItem을 보존합니다. 비공개 요청·운영자 식별자는 공개하지 않습니다.
현재 development-only·offline 결과는 **승인할 수 없습니다**. 런타임 격리 검사와 검토된 adoption
게시 경로는 구현됐지만 confirmation provider·다음 실행 연결은 별도 단계입니다.
합성 승인 계약 테스트를 실제 운영 성공으로 해석하면 안 됩니다.

공개 projection과 참조된 Skill 캡처를 검토한 경우에만 `--publish-reviewed`를 추가합니다.
새 로컬 `adoption.json` 관측을 저장할 뿐 배포하지 않습니다. 공식 `validate`·`merge`·`index`·`build`가
최종 확인 cycle·승인·실제 작업 보고서의 참조 전체를 검증하고 private/추가 필드·상충 관측은 거부합니다.
기존 근거는 수정하지 않고 승인만 기록한 보고서에는 작업 수치를 만들지 않습니다.
Projection 실패 시 private 승인은 남아 있으나 명시적인 게시 오류를 반환하며 Active는 바꾸지 않습니다.

### 오프라인 발표 백업

별도로 승인된 개발 작업 Actions dispatch에서는 **project·work_id·skill_key·max_rounds(1~10)**를
명시합니다. `live=true`·인증·승인된 호출/시간/Credit 한도가 모두 필요하며 기본은 비활성입니다.
별도 설정하는 `SKILLOPS_RECORDED_WORK_ITEMS` secret은 작업 ID를
`{"work_item": <private development WorkItem>}`에 매핑합니다. 요청 본문은 공개 dispatch 입력,
명령행 인자·로그·공개 아티팩트로 전달하지 않으며 정확한 dispatch 소스 커밋과 일치해야 합니다.
기존 반복·provider·저장을 재사용하고 workflow run/attempt를 cycle ID로 씁니다.
이 개발 전용 dispatch는 후보를 승인하거나 로컬 승인을 Actions 실행 권한으로 사용하지 않습니다.
이번 구현 작업에서 secret 설정이나 live dispatch를 실행하지 않습니다.

**유료 호출 없는 터미널 데모·백업**은 커밋된 변경 없는 통합 checkout에서 새 경로로 생성합니다.

```bash
SKILLOPS_LIVE_EVALUATION_ENABLED=false python3 tests/export_iteration_evidence.py \
  --output /tmp/skillops-iterate-demo
python3 -m json.tool /tmp/skillops-iterate-demo/manifest.json
python3 project_results.py validate --results /tmp/skillops-iterate-demo/n2-feedback
```

생성기는 실제 provider·반복·저장 모듈을 사용하며 입력 프로젝트와 모델·컨테이너 응답만 합성합니다.
미리 작성한 결과 fixture를 복사하지 않습니다. Manifest에는 통합 SHA, 시나리오 경로,
cycle/run ID와 해시가 남습니다. N=2 피드백, 조기 종료, 최대 라운드, 예산 소진,
미저장 시도와 저장 실패를 확인할 수 있습니다.
모든 결과는 `offline_test`이며 confirmation은 미검증·미실행 상태입니다.
기존 경로는 덮어쓰지 않습니다. 공식 빌드에는 `trace.js`와 해시로 검증한 replay/cycle 연결이 포함됩니다.
공개 배포는 여전히 별도 승인이 필요하며 디자인 PR #32는 이번 데모에 포함하지 않습니다.

읽기 전용 로컬 화면은 검토한 과거 실제 보고서와 생성된 `n2-feedback`을 새 결과 경로에 합친 뒤
공식 빌드로 준비합니다. 비공개 runtime 디렉터리를 통째로 복사하지 마세요.

```bash
python3 project_results.py merge --incoming <reviewed-live-results> --results <demo-results>
python3 project_results.py merge --incoming /tmp/skillops-iterate-demo/n2-feedback --results <demo-results>
python3 project_results.py build --results <demo-results> --output <new-site>
python3 -m http.server 8765 --bind 127.0.0.1 --directory <new-site>
```

로컬 서버의 `/?project=<project>&run=<exact-cycle-id>&skill=<skill-key>`로 시작합니다.
정확한 cycle/run ID는 생성기 manifest에 있습니다. 화면은 `offline_test`를 비실측으로 구분하고
최종 확인·승인 미완료를 유지합니다. 과거 실제 보고서의 본문·판정·수치는 변경하지 않습니다.
서버 없이 열 수 있는 스크린샷 갤러리를 백업으로 보관하세요.

### 준비된 프로젝트 샘플

- `projects/sample_repo`: 원래 이슈 관리 소스를 보존하고 개발 스킬을 명시적으로 추가했습니다.
- `projects/project-a`: `dbader/schedule` 1.2.2의 고정 커밋과 스케줄링 전용 미검증 스킬 초안입니다.
- `projects/project-b`: `obra/superpowers` v6.3.0의 고정 커밋이며 기존 스킬을 그대로 보존합니다.

```bash
python3 project_samples.py verify --project project-a
python3 project_samples.py verify --project project-b
python3 project_samples.py add-skill --project sample_repo --skill skills/develop/SKILL.md
```

준비 도구는 Windows/Linux의 Python 3.12 이상에서 동작합니다.
공개 GitHub 저장소의 커밋을 고정해 비어 있는 이름의 새 프로젝트 디렉터리로 가져오고,
라이선스와 원본 해시를 보존합니다. 스킬이 없으면 프로젝트에 맞는 초안을 명시적으로 선택해야 합니다.
다른 저장소를 추가하기 전에 [준비 명령과 경계](docs/PROJECT-EVALUATION.md#reproducible-sample-preparation)를 확인하세요.
가져온 코드는 실행하지 않고 기존 디렉터리나 스킬을 덮어쓰지 않습니다.
스킬 초안을 넣었다는 사실과 행동 품질이 검증됐다는 판단은 다릅니다.

프로젝트 워크플로의 `sample-onboarding-results` artifact는 기존 평가기로 생성한
비모델 보고서와 index의 실제 계약 검증 기록입니다. 이 작업에서는 의도적으로 모델을 호출하지
않으므로 가이드 평가는 차단 상태이고, 모든 준비된 프로젝트의 실행은 `live_disabled`입니다.
계약 테스트가 성공해도 이 차단·설정 필요 상태가 통과로 바뀌거나 Azure 배포가 승인되지 않습니다.
새 샘플 기록을 추가할 때 기존 공개 보고서와 검증된 선택적 스킬 이력 sidecar는
바이트 단위로 보존합니다.
[Owner 대시보드 방향](docs/PROJECT-EVALUATION.md#owner-dashboard-direction)에 자동 CI와
프로젝트 평가의 구분, 대상 스킬·버전 선택, 가이드/APO 근거, 실행 비교, 변경 내역·이력 및
지원 범위를 정리했습니다. 화면의 과거 비교 수치를 새 샘플의 평가 결과로 사용하지 않습니다.

## 현재 구현한 기능

프로젝트 탐지 기반 경로는 위에서 설명합니다. 아래는 기존 공통 과제 벤치마크와 CLI 기능입니다.

- Python 이슈 관리 작업 3개 작업군, 5개 작업과 기존 개발 스킬 v1
- 보호된 고정 검사와 모델이 생성한 테스트의 Docker 실행
- 개발 스킬·도구 없이 별도 세션에서 실행되는 독립 judge와 교정 평가
- development 결과만 입력받는 실제 Copilot CLI 기반 후보 스킬 생성
- 기존 스킬과 후보를 새 세션에서 실행하는 동일 작업별 비교
- 품질·비용·시간에 따른 판정과 JSON/Markdown 보고서
- 모델 인증 없이 실행되는 GitHub Actions 검사와 공개용 과거 평가 기록
- 로컬 대상 프로젝트·스킬·평가셋 등록과 변경 불가능한 초기 스킬 pin
- 등록한 소스와 pin을 사용하는 평가 흐름 및 읽기 전용 배포 자격 검사

**Canary 배포, 승격, 롤백은 아직 구현하지 않았습니다.**
후보의 변경 이유는 모델이 제시한 가설이며, 개선이 입증됐다는 뜻이 아닙니다.
스킬 생성·평가 시스템 코드의 병합과 후보 스킬의 운영 승격도 별개입니다.

## 저장소 CI와 실제 모델 평가의 차이

`Baseline validation` 워크플로는 평가기 자체의 오프라인 테스트, 실제 Docker 정상·오류 대조 검사,
과거 공개 기록의 구조·일관성을 검사합니다. 실행한 커밋, Python/Docker/이미지 정보와 테스트 로그를
job summary 및 `baseline-ci-<commit>` artifact에 기록합니다.

이 워크플로는 **Copilot 인증 정보를 사용하지 않으며 실제 모델을 호출하지 않습니다.**
CI 성공을 새로운 모델 평가 결과로 해석하면 안 됩니다.
실제 로컬 baseline의 정제된 기록은 [평가 기록 설명](evidence/README.ko.md)과
`evidence/baseline-v2.json`에서 확인할 수 있습니다.

## 시작하기

실제 평가 요구사항은 Linux, Python 3.12 이상, Git, 선택한 모델에 접근할 수 있는
설치·인증된 GitHub Copilot CLI, 실행 중인 Docker daemon입니다.
Python 코드는 표준 라이브러리만 사용합니다.

```bash
# 오프라인 테스트: Copilot 설치·인증이나 Docker 불필요
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 실제 실행 환경 사전 확인
python3 skillops.py doctor
```

`doctor`는 모델을 호출하지 않습니다. CLI 제어 옵션, 활성 judge 스킬, Docker와 이미지 상태를
확인하지만 로그인이나 실제 모델 호출이 성공했다고 보장하지는 않습니다.
이미지를 자동으로 받지 않으므로 공식 이미지가 없다면 다음을 실행합니다.

```bash
docker pull python:3.12-slim
```

실제 평가에서는 태그가 아니라 로컬에서 확인한 불변 이미지 ID를 기록하고 실행합니다.
이미지, CLI 또는 평가 입력이 바뀌면 기존 교정 결과를 재사용할 수 없습니다.

## 전용 프로필 로그인

```bash
python3 skillops.py login
```

개인 Copilot 프로필 대신 프로젝트 소유의 별도 프로필을 사용합니다.
브라우저·기기 로그인은 사용자 터미널에서 직접 완료합니다.
**토큰을 소스, 보고서 또는 채팅에 붙여 넣지 마세요.**
인증 정보와 CLI 상태는 Git에서 제외된 비공개 `.skillops-private/`에 보관합니다.

명시적으로 내보낸 표준 Copilot 토큰 환경변수도 지원하지만, 보고서에 복사하거나 출력하지 않습니다.

## 실제 실행 계약 확인

```bash
python3 skillops.py probe --model gpt-6-astra
```

이 명령은 실제 모델을 호출하므로 Copilot 사용량이 발생할 수 있습니다.
도구를 제공하지 않고, 발견된 스킬·사용자 지침·hooks·memory를 비활성화합니다.
예상 밖의 플러그인, 사용자 MCP 서버와 대체 provider 설정은 차단합니다.
인증 실패는 평가 성공이 아니라 진행 차단으로 기록합니다.

probe는 가짜 파일에 접근해 보도록 요청한 뒤, 실제 도구 manifest가 비어 있고 도구 호출이 없으며
응답이 `NO_TOOLS`인지 확인합니다. 이는 제한적인 실행 계약 확인이지, 일반적인 보안 격리 증명은 아닙니다.
모든 실제 호출에서도 관측된 모델, 도구 목록과 정상 종료를 확인합니다.

검증한 CLI에서는 개발 역할에 `--available-tools=skill`,
judge와 generator에 `--available-tools=skill --excluded-tools=skill`을 사용합니다.
빈 `--available-tools=`는 이 버전에서 도구를 끄지 않았습니다.
저장된 전용 로그인을 막았던 `--no-auto-login`도 사용하지 않습니다.

## Baseline 실행

```bash
python3 skillops.py calibrate --model gpt-6-astra
python3 skillops.py baseline --model gpt-6-astra
```

처음부터 실행하면 작업군별 3개씩 교정 judge 호출 9회와, 작업 5개에 대한 developer/judge 호출 10회,
교정·코딩 평가에 **19회**의 실제 모델 호출이 필요하며, 아래 설명한 스킬 크기별 guide judge 호출이
추가됩니다. 자동 재시도나 조용한 응답 보정은 하지 않습니다.

교정은 각 작업군에서 정상 코드, 결함 코드, 데이터에 지시문이 섞인 대조 사례를 평가합니다.
정상 사례는 결함 사례보다 전체 점수와 정확성 점수가 높아야 하고,
지시문이 삽입된 결함 사례가 만점을 받아서는 안 됩니다.
그룹 누락·중복·실패는 baseline 실행을 차단합니다.

Baseline은 현재 fingerprint와 일치하는 성공한 교정 결과가 필요합니다.
Fingerprint에는 rubric, 대조 자료, 작업 목록, 고정 검사, 작업군 seed, 실행기·평가기·runtime 코드,
모델, CLI 버전, 제어된 judge 설정·목록과 이미지 ID가 포함됩니다.

각 작업은 해당 작업군의 seed를 복사한 새 임시 Git 저장소에서 시작합니다.
평가기의 고정 registry가 seed 경로와 호출할 함수를 정하며, 작업 목록이 임의 경로나 import를 고를 수 없습니다.
Seed는 공통 `issues.py`로 복사됩니다.

개발 역할은 준비된 `develop` 스킬을 실제 호출한 뒤 `issues.py`, `test_generated.py`, 리뷰를 JSON으로 반환합니다.
Shell이나 파일 쓰기 도구는 없습니다. 신뢰된 실행기가 제안을 적용하고 실제 `git diff`를 기록한 뒤
생성된 Python을 Docker 안에서만 실행합니다.
스킬의 [한국어 참고 번역](skills/develop/SKILL.ko.md)도 제공하지만, 실행에는 원본 `SKILL.md`를 사용합니다.

Judge는 개발 스킬과 도구가 없는 새 세션·작업공간에서 실행됩니다.
현재 작업, rubric, 코드·실행 근거만 전달받습니다.
다른 작업군의 계약·요청이나 보호된 기대값은 개발 역할에 전달하지 않습니다.

## 작업 목록 v2

| 작업군 | 함수 | 작업 수 / 분할 | 작업당 고정 검사 |
|---|---|---|---:|
| Listing | `list_issues` | development 3개 | 26 |
| Labels | `normalize_labels` | development 1개 | 17 |
| Updates | `update_issue` | heldout 1개 | 22 |

Labels는 공백·Unicode casefold 정규화, 순서 보존과 검증을 다룹니다.
Updates는 입력을 변경하지 않는 원자적 업데이트와 검증을 다룹니다.
Seed는 `projects/sample_repo/`, 전체 공개 계약은 `eval/tasks.json`에 있습니다.

한 작업군이 development와 heldout에 동시에 속할 수는 없습니다.
이미 반복 평가한 listing 경계 작업은 development 회귀 작업이며, updates는 generator 입력에서 제외합니다.
과거 보고서의 원래 분할 표시는 보존합니다.
이 분리는 generator 입력에 관한 것이지, 벤치마크 작성자도 heldout 내용을 모른다는 뜻은 아닙니다.

## Anthropic 스킬 가이드 평가

Baseline은 평가 대상 프로젝트의 `.github/skills`, `.claude/skills`, `skills` 루트 아래에서
스킬도 발견합니다. 각 `SKILL.md`는 독립적으로 평가하는 번들을 정의합니다.
중첩된 스킬은 별도로 평가하며, 해당 파일들은 상위 스킬 번들에서 제외합니다.

- **호출과 사용량:** 작은 단일 파일 스킬에는 guide judge 호출 1회가 필요합니다.
  큰 텍스트 번들은 크기가 제한된 배치로 나누어 추가 호출하므로,
  스킬 크기에 따라 모델 호출 수와 사용량이 증가합니다. 바이너리 자산은
  메타데이터(경로, 크기, 해시)만 제공하며, 내용을 텍스트로 모델에 전송하지 않습니다.
- **정적 검사:** 비어 있지 않은 `name`과 `description`을 포함한 필수 YAML frontmatter,
  비어 있지 않은 본문, 번들 내부에 존재하는 로컬 참조를 검사합니다.
  `SKILL.md`의 500줄 권장 기준 초과와, 300줄을 넘는 Markdown 참조 문서의
  목차 누락은 경고로 기록합니다.
- **도구 없는 judge 평가 차원:** 트리거 설명(trigger description), 작업 흐름 명확성(workflow clarity),
  일반화(generalization), 지시문 품질(instruction quality), 점진적 공개(progressive disclosure),
  리소스 구성(resource organization), 예상 밖 동작을 피하는 원칙(principle of lack of surprise).
- **작은 스킬의 적용 여부:** 지원 리소스가 없는 단일 파일 스킬에서는
  `progressive_disclosure`와 `resource_organization`을 `not_applicable`(N/A)로 표시하고
  점수 분모에서 제외하며, 감점하지 않습니다.

크기가 제한된 스킬 요약은 `report.json`의 `anthropic_skill_guide` 키와 `report.md`의
`Anthropic skill guide` 표에 기록합니다. 각 요약의 프로젝트 상대 경로 `artifact`가 가리키는
스킬별 `result.json`에는 전체 정적 findings, 적용 여부, judge 차원·점수·근거를 저장하며,
baseline에는 복제하지 않습니다. 큰 metadata는 원문 대신 SHA-256과 UTF-8 바이트 수로
식별하고, 반복 참조 finding에는 횟수와 target hash를 명시합니다.
Source evidence는 배치 분할과 결과 저장 전에 GitHub/runtime token 및 절대 로컬 경로를
정제하며, 저장하는 judge rationale도 정제합니다. 상대 파일 identity와 원본 snapshot hash는
유지합니다. Artifact ID에는 전체 경로 SHA-256을 사용하며, 충돌이나 기존 artifact
디렉터리·결과가 있으면 덮어쓰지 않고 명시적으로 차단합니다.
가이드 점수와 지적 사항은 **보고 전용(report-only)**이며,
코딩 작업 결과나 canary·승격 결정을 바꾸지 않습니다.
이는 자동화된 가이드 평가이지 Anthropic 인증이 아니며, 사람의 교정을 대체하지 않습니다.

각 result artifact는 직렬화한 UTF-8 JSON 기준 2 MiB 이하입니다. 초과하면 해당 스킬만
`blocked` / `skill_result_limit`으로 기록하며 baseline은 차단하지 않습니다.
병합 결과를 포함한 judge rationale은 각각 4,096 UTF-8 bytes 이하로 검증하고,
초과 시 조용히 자르지 않고 명시적인 검증 오류를 기록합니다.

## 평가 기록 읽기

명령은 실행별 파일 경로를 출력합니다. `runs/<unique-id>/`의 주요 파일은 다음과 같습니다.

- `calibration.json`: 대조 사례 점수, 통과 여부와 fingerprint
- `report.json`, `report.md`: baseline 결과와 사람이 읽는 요약
- 작업별 폴더: 정규화된 developer/judge 호출 기록, 코드 제안과 실제 diff
- `anthropic-skill-guide/<skill-id>/`: 전체 `result.json`, `manifest.json`,
  개별 `judge-<batch>.json` 호출 기록

Baseline의 기준 기록은 JSON입니다. 검사별 결과, 생성된 테스트 수, rubric 점수·근거,
소스·입력 해시, 경과 시간과 실제 사용량을 포함합니다.
실패한 작업도 요청 분모에서 제외하지 않습니다.
정확성 성공은 **해당 작업군의 고정 검사 전체를 통과한 작업 수 / 요청한 전체 작업 수**입니다.
Judge 평균은 평가가 완료된 작업만 사용하되 분모를 표시합니다.

집계는 작업 수 기준입니다. 3개 listing 작업이 seed·계약을 공유하므로 5개 작업을 독립 표본 5개라고 볼 수 없습니다.
평가 완료와 코드 정답은 다르며, 높은 judge 점수가 고정 검사 실패를 덮어쓰지 않습니다.

사용량은 CLI가 보고한 `nano_aiu`, premium request units, 토큰과 milliseconds 단위를 유지합니다.
누락된 값은 `null`과 `not_reported_by_cli`로 표시하며 0이나 추정 달러 비용으로 대체하지 않습니다.
전체 비용을 계산할 때는 baseline과 별도로 기록된 교정·probe 사용량도 포함해야 합니다.

Baseline/calibration의 종료 코드 0은 절차 완료 또는 교정 통과,
2는 진행 차단·교정 실패·불완전한 평가를 뜻합니다.
Baseline이 0으로 끝났다는 이유만으로 코드가 정확하다고 판단하면 안 됩니다.

### 과거 실제 baseline

2026년 9월 14일 첫 Astra baseline은 작업 3개에서 각각 고정 검사 20/20, judge 평균 100/100을 기록했습니다.
이후 유효한 page size 1·3에 대한 검사 6개를 추가한 새 실행은 각각 26/26과 같은 judge 평균을 기록했습니다.
원래 기록은 덮어쓰지 않았으며, 검사 변경 뒤 새 교정·평가를 수행했습니다.

같은 날 catalog v2 실행은 작업 5개를 완료했습니다.
Listing은 작업당 26/26, labels는 17/17, updates는 22/22를 통과했고
모든 작업군 교정 gate와 작업 수 기준 judge 평균 100/100을 기록했습니다.
과거 3개 작업과 현재 5개 작업의 모집단은 다르므로 그대로 개선율을 계산할 수 없습니다.
이 작은 합성 평가 결과는 스킬 개선이나 일반적인 안전성을 입증하지 않습니다.

## LLM 후보 스킬 생성과 비교

```bash
python3 skillops.py propose --baseline BASELINE_RUN_ID --model gpt-6-astra
python3 skillops.py calibrate --model gpt-6-astra
python3 skillops.py compare --candidate CANDIDATE_RUN_ID --model gpt-6-astra
```

파일 경로가 아니라 실제 생성된 run ID를 넣습니다.
Baseline 원본 보고서가 로컬에 있어야 하며, 공개용 정제본만으로 `propose`를 실행할 수는 없습니다.
현재 조건에 맞는 교정이 이미 있다면 비교에서 재사용할 수 있습니다.
코드·벤치마크·runtime·judge 문맥이 바뀌면 새 교정이 필요합니다.

`propose`는 도구 없는 generator로 실제 Copilot 호출 1회를 수행합니다.
기존 스킬과 현재 development 작업의 요청·수치 피드백만 선택합니다.
Heldout 결과, 전체 집계, 모델의 자유 형식 응답, 정답 소스 코드는 전달하지 않습니다.
과거 자료의 벤치마크·rubric·대조 자료·seed 해시와 원본 스킬이 현재와 같아야 합니다.
실행기 코드 차이는 이력으로 기록하며 과거 실행을 새로운 비교 결과로 취급하지 않습니다.
관측된 실패가 없다면 이를 그대로 알리고 **효율 개선 가설**을 요청합니다.

모델은 새 지침 본문과 변경 이유를 반환합니다.
실행기는 `develop` frontmatter를 보존하고 새 실행 폴더에
`base-SKILL.md`, `SKILL.md`, `generator.json`, `candidate.json`을 저장합니다.
기존 v1은 변경하지 않습니다.
크기·형식 제한 및 명백한 벤치마크 이름·코드 거절은 제한적인 방어이며,
지침의 일반적인 안전성을 증명하지 않습니다. 해시도 악의적인 로컬 편집에 대한 서명은 아닙니다.

`compare`는 5개 작업의 기존/후보를 각각 새로 실행하며 먼저 실행하는 쪽을 번갈아 바꿉니다.
Developer/judge 호출은 총 20회입니다.
양쪽 모두 같은 실행기·벤치마크·현재 교정·모델을 사용하고 스킬 snapshot을 명시적으로 전달합니다.
역할마다 세션과 작업공간은 새로 만들며, 과거 baseline 실행을 비교의 기존 스킬 결과로 재사용하지 않습니다.
불완전한 작업도 분모에 남깁니다.
새 교정 9회와 후보 생성 1회를 포함하면 총 30회이며, 선행 조건이 막히면 종속 호출을 실행하지 않습니다.

`comparison.json`과 `comparison.md`에는 순서, 해시, 작업 근거,
기존/후보 및 분할별 요약, 판정이 기록됩니다. 데모 정책 v1은 다음을 요구합니다.

- 모든 비교가 완료되고 모든 후보 작업이 고정·생성 테스트를 통과할 것
- 동일 작업의 judge 세부 점수가 어느 항목에서도 떨어지지 않을 것
- 전체 작업 비용과 시간이 각각 기존보다 5% 넘게 악화되지 않을 것
- 고정·생성 테스트 실패를 하나 이상 해결하거나 judge 세부 점수가 하나 이상 개선될 것,
  **또는** 전체 작업 비용이나 시간이 10% 이상 줄어들 것

비용은 developer와 judge의 실제 NanoAIU 합이며, 시간은 작업 전체 경과 시간의 합입니다.
Generator·교정 비용은 별도이므로 전체 최적화 비용을 따질 때 더해야 합니다.
작업당 한 쌍, 동일 모델 계열의 판단 편향, 캐시·네트워크 변동 때문에 통계적 우월성을 주장할 수 없습니다.

비교 종료 코드는 **0: `eligible_for_canary`**, **1: `rejected`**, **2: `blocked`**입니다.
통과는 잠정 자격일 뿐 배포를 실행하지 않습니다.
개선 없음이나 거절도 유효한 결과이며, 좋은 점수가 나올 때까지 자동으로 다시 생성하지 않습니다.

### 첫 실제 후보 결과

2026년 9월 15일 모델은 요구사항과 테스트 assertion을 연결한 체크리스트를 재사용하는 후보를 작성했습니다.
원본은 1,028바이트, 후보는 2,410바이트였습니다. 길어졌다는 이유로 더 좋다고 판단하지 않았습니다.
Generator 1회, 교정 9회, 비교 20회는 서로 다른 실제 CLI 세션 30개에서 실행했습니다.
양쪽 모두 작업 5개와 모든 고정·생성 테스트를 통과했고, judge 평균은 분모 5의 100/100이었습니다.

| 지표 | 새로 실행한 기존 스킬 | 후보 |
|---|---:|---:|
| Developer + judge 비용, NanoAIU | 125,712,450,000 | 127,772,200,000 |
| 전체 작업 경과 시간, 초 | 296.064 | 302.923 |

이번 관측에서 후보 비용은 1.64%, 시간은 2.32% 증가했습니다.
품질 향상이나 요구한 효율 개선이 없어 **`rejected`**로 판정했습니다.
이는 작은 단일 비교 결과이지 통계적인 성능 저하 증명은 아닙니다.
원본 스킬은 그대로 유지했으며 후보를 배포하지 않았습니다. 표에는 생성·교정 비용이 포함되지 않습니다.

로컬 실행 ID는 후보 `20260915T005404Z-611d681b8bf7`,
교정 `20260915T010210Z-45057be9ce19`, 비교 `20260915T010916Z-74058d9807da`입니다.
전체 기록은 Git에서 제외된 `runs/`에 있으며, 이 ID는 공개 커밋 파일 링크가 아닙니다.

이 실행은 표현 범위를 넘는 수치 입력을 처리하는 가드 수정 전의 역사적 기록입니다.
원래 실행 기록과 fingerprint는 보존하며, 가드 수정으로 소스 fingerprint가 달라지므로
새 실제 비교 전에는 변경된 코드에 맞는 새 교정이 필요합니다.
가드는 모델 재실행 없이 기존 판정과의 일치 여부를 오프라인으로 확인했습니다.
지출 상한을 추가하거나 후보 선택 정책을 변경한 것은 아닙니다.

## 프로젝트 등록과 배포 자격 검사

다음 명령은 모델을 호출하지 않으며 Copilot 인증이나 Docker가 필요하지 않습니다.

```bash
python3 skillops.py register --repository sample --path projects/sample_repo \
  --skill develop --evaluation-set issue-management-v2
python3 skillops.py repositories
```

등록 시 엔진의 `skills/develop/SKILL.md` 내용을 SHA-256으로 식별하는 변경 불가능한
snapshot으로 저장합니다. 이후 원본 파일을 수정해도 기존 pin은 바뀌지 않습니다.
중복 ID·경로 등록은 덮어쓰지 않고 거부합니다. ID는 GitHub URL이 아닌 소문자 slug입니다.
상대 경로는 SkillOps 설치 폴더 기준이며, 절대 경로로 다른 로컬 프로젝트를 지정할 수 있습니다.

첫 어댑터는 대상 폴더 바로 아래의 `issues.py`, `labels.py`, `updates.py`와
`eval/tasks.json`의 기존 이슈 관리 계약만 지원합니다. 작업군별 소스 파일을 독립적인
Python 파일로 평가합니다. 등록은 입력 형태와 파일 존재를 확인하며 정확성을 보증하지 않습니다.
원격 저장소 clone, 애플리케이션 의존성 설치, 임의 테스트 명령 실행 또는 다른 언어의
자동 지원 기능은 아닙니다.

**별도로 승인한 실제 모델 평가**에서는 등록 ID를 지정합니다.

```bash
python3 skillops.py calibrate --repository sample --model gpt-6-astra
python3 skillops.py baseline --repository sample --model gpt-6-astra
python3 skillops.py propose --baseline BOUND_BASELINE_RUN_ID --model gpt-6-astra
python3 skillops.py compare --repository sample --candidate CANDIDATE_RUN_ID --model gpt-6-astra
python3 skillops.py eligibility --repository sample --comparison COMPARISON_RUN_ID
```

앞의 네 명령은 모델을 호출하고 `eligibility`는 호출하지 않습니다.
후보 생성은 baseline의 연결 정보를 이어받되 development 결과만 모델에 전달합니다.
교정은 평가기가 소유한 대조 사례를 사용하며 대상 소스를 임의로 알려진 오답으로 간주하지 않습니다.
등록한 실제 소스와 고정된 스킬이 baseline·비교 실행에 전달됩니다.
대상 프로젝트의 다른 파일은 실행 컨테이너에 함께 제공하지 않습니다.
`--repository`를 생략한 기존 명령은 이전의 미등록 평가 방식으로 동작합니다.

등록된 평가의 fingerprint에는 저장소·스킬·평가셋 식별자와 소스·pin 해시가 포함됩니다.
대상·pin·평가기 변경 후에는 해당 조건에 맞는 새 교정과 평가 근거가 필요합니다.
비교 기록은 후보 메타데이터와 실제 사용한 교정 파일의 정확한 해시를 포함하고,
완료 시 비교 파일의 해시를 로컬 레지스트리에 기록합니다.

배포 자격 검사는 등록된 비교 해시, 입력·snapshot 일치, 유효한 교정,
작업별 소스·스킬 활성화 근거 및 다시 계산한 판정을 확인합니다.
종료 코드는 **0: eligible_for_canary**, **1: rejected**, **2: blocked**입니다.
어떤 결과에서도 pin이나 대상 파일을 변경하지 않습니다.
과거 미등록 실행, 외부에서 복사한 미등록 보고서, 변조된 근거로는 자격을 얻을 수 없습니다.
이 결과는 현재 시점의 검사이며, 이후에도 유효한 배포 승인 토큰이 아닙니다.

상태는 크기를 제한하고 소유자만 접근하는 `.skillops/`에 저장하며 Git에서 제외합니다.
비차단 잠금과 원자적 파일 교체를 사용합니다. 로컬 경로·스킬 내용이 포함되므로 공개하지 마세요.
레지스트리 소유자는 신뢰한다는 전제이며 원격 서명 증명이나 악의적인 로컬 소유자 방어가 아닙니다.
등록은 대상에 스킬을 설치하지 않으며, pin 변경 명령도 아직 없습니다.
실제 pin 전환은 후속 canary·승격·롤백 구현 범위입니다.
PR 자동 평가 트리거와 제품 대시보드 자동 반영도 아직 구현하지 않았습니다.

이번 연결은 합성 모델 응답을 사용하는 회귀 검사와 실제 오프라인 등록·거부 동작으로 확인했습니다.
새로운 저장소 연결을 사용한 실제 모델 비교를 실행한 것은 아닙니다.
기존 실제 실행 기록은 보존하며, 변경된 엔진으로 모델을 다시 실행하려면 새 교정이 필요합니다.

## 실행 경계와 검증

모델이 생성한 Python은 호스트에서 실행하지 않습니다.
컨테이너는 네트워크 없이, 읽기 전용 root·소스, 비특권 사용자,
capability 제거와 no-new-privileges 설정으로 실행합니다.
인증 정보나 Docker socket을 제공하지 않으며 실행은 15초, 메모리 256MiB,
CPU 1개, PID 64개, 통합 출력 1MiB로 제한합니다.
CLI 호출은 180초와 통합 출력 4MiB로 제한합니다.

기대값은 호스트에 남기고 평가기 소유 runner가 실제 값과 unittest 결과를 수집합니다.
출력에 성공이라고 적거나 조기 종료한 것만으로 완료를 인정하지 않습니다.
하지만 후보 Python과 harness는 컨테이너 안에서 같은 interpreter를 공유하므로
**악의적인 Python에 대한 변조 방지 시스템은 아닙니다.**
비적대적 합성 데모이지 보안 인증이 아니며, judge 프로세스 분리도 동일 모델 편향을 없애지는 않습니다.

```bash
# 오프라인 검사: Copilot 설치·모델 호출·Docker 불필요
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 실제 컨테이너 정상·오류 대조 검사 포함: 이미지 필요
SKILLOPS_CONTAINER_TESTS=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

전용 프로필은 비차단 lock으로 동시 사용을 막습니다.
임시 developer/judge 작업공간은 호출 뒤 제거하고 비공개 CLI 기록과 실행 결과는 보존합니다.
`.skillops-private/`와 `runs/`는 Git에서 제외합니다. 합성 실행 결과만 검토 후 공유하고 프로필은 공유하지 마세요.
오프라인 테스트는 CLI 탐색과 응답을 명시적으로 mock 처리하므로 실제 CLI 동작의 증거가 아닙니다.
