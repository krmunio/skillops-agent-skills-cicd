# SkillOps — 에이전트 스킬의 CI/CD

[English](README.md) | [한국어](README.ko.md)

에이전트의 작업 결과를 **LLM이 작성한 스킬 변경 후보**로 바꾸고, 기존 스킬과 실제로 비교해 검증하는 프로젝트입니다.
생성된 지침을 곧바로 배포하거나, 측정하지 않은 개선을 주장하지 않습니다.

## 현재 구현한 기능

- Python 이슈 관리 작업 3개 작업군, 5개 작업과 기존 개발 스킬 v1
- 보호된 고정 검사와 모델이 생성한 테스트의 Docker 실행
- 개발 스킬·도구 없이 별도 세션에서 실행되는 독립 judge와 교정 평가
- development 결과만 입력받는 실제 Copilot CLI 기반 후보 스킬 생성
- 기존 스킬과 후보를 새 세션에서 실행하는 동일 작업별 비교
- 품질·비용·시간에 따른 판정과 JSON/Markdown 보고서
- 모델 인증 없이 실행되는 GitHub Actions 검사와 공개용 과거 평가 기록

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
총 **19회**의 실제 모델 호출이 필요합니다. 자동 재시도나 조용한 응답 보정은 하지 않습니다.

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
Seed는 `sample_repo/`, 전체 공개 계약은 `eval/tasks.json`에 있습니다.

한 작업군이 development와 heldout에 동시에 속할 수는 없습니다.
이미 반복 평가한 listing 경계 작업은 development 회귀 작업이며, updates는 generator 입력에서 제외합니다.
과거 보고서의 원래 분할 표시는 보존합니다.
이 분리는 generator 입력에 관한 것이지, 벤치마크 작성자도 heldout 내용을 모른다는 뜻은 아닙니다.

## 평가 기록 읽기

명령은 실행별 파일 경로를 출력합니다. `runs/<unique-id>/`의 주요 파일은 다음과 같습니다.

- `calibration.json`: 대조 사례 점수, 통과 여부와 fingerprint
- `report.json`, `report.md`: baseline 결과와 사람이 읽는 요약
- 작업별 폴더: 정규화된 developer/judge 호출 기록, 코드 제안과 실제 diff

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
