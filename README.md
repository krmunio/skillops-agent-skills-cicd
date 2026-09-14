# SkillOps Agent Skills CI/CD

SkillOps는 코딩 에이전트가 사용하는 스킬 변경 후보를 GitHub 안에서 비교 평가하고, 검증된 버전만 사람이 승인해 배포하도록 돕는 CI/CD 예제입니다. 이 저장소의 첫 버전은 **GitHub Copilot을 첫 적용 대상으로 고려**하지만, Copilot live 실행 API를 추측해서 구현하지 않습니다.

이번 구현은 Python 3.12 표준 라이브러리만 사용합니다. 웹 서버, 데이터베이스, 대시보드, Kubernetes, 멀티에이전트 오케스트레이션, 자동 머지는 포함하지 않습니다.

## 구현한 기능

- `SKILL.md` 기반 예제 스킬 3종
  - `examples/skills/http-retry/baseline`: 기존 동작
  - `examples/skills/http-retry/risky-candidate`: 모든 실패 요청을 무조건 재시도하는 잘못된 후보
  - `examples/skills/http-retry/corrected-candidate`: 제한된 재시도와 비멱등 요청 보호를 적용한 후보
- fixture 실행기와 독립 평가기 분리
  - 실행기는 baseline/candidate 결과물을 깨끗한 임시 작업공간에 수집합니다.
  - 평가기는 결과물 코드를 로컬 가짜 HTTP 서비스로 검사합니다.
- JSON/Markdown 보고서 생성
- 판정 상태 구분
  - `REJECTED`: 실제 평가 실패 또는 정책 위반
  - `BLOCKED`: 인증/실행/증거 부족으로 판단 불가
  - `DEMO_ONLY`: fixture 평가 통과, 실제 배포 근거로 사용 불가
  - `ELIGIBLE_FOR_REVIEW`: live 평가와 필수 조건 통과, 사람 승인 대기
- SkillOps 전용 배포 manifest 검증
  - 대상 저장소
  - immutable commit SHA
  - 스킬 content hash
- GitHub Actions
  - 테스트 실행
  - offline demo 평가 실행
  - Markdown 결과를 job summary에 표시
  - JSON/Markdown 보고서를 artifact로 저장

## 중요한 제한

fixture 모드는 평가 파이프라인이 동작함을 보여주는 offline demo입니다. Copilot이 실제로 스킬을 사용해 문제를 해결했다는 증거가 아니며, fixture 결과만으로 실제 스킬 배포를 승인하면 안 됩니다.

live 모드는 공식 지원 인터페이스와 인증 설정을 확인한 경우에만 구현해야 합니다. 현재 인증 없이 live 실행을 요청하면 fixture로 조용히 대체하지 않고 `BLOCKED` 보고서를 생성합니다.

이 저장소는 신뢰된 fixture만 자동 실행합니다. 임시 폴더는 실행 결과 분리를 위한 장치일 뿐, 신뢰할 수 없는 코드 실행을 위한 보안 격리라고 주장하지 않습니다. 신뢰할 수 없는 후보 실행에는 별도의 적절한 격리가 필요합니다.

## 빠른 시작

요구 사항: Python 3.12

### 1. 테스트 전체 실행

```bash
python -m unittest discover -s tests
```

### 2. risky candidate가 회귀로 거절되는지 확인

이 명령은 `REJECTED`를 표현하기 위해 종료 코드 `1`을 반환합니다.

```bash
set +e
python -m skillops demo risky --out-dir reports/risky
status=$?
set -e
echo "exit=$status"
cat reports/risky/report.md
```

기대 결과:

- `execution_mode=fixture`
- `decision=REJECTED`
- GET 재시도 제한 초과 회귀
- POST 중복 부작용 회귀

### 3. corrected fixture candidate가 DEMO_ONLY로 통과하는지 확인

```bash
python -m skillops demo corrected --out-dir reports/corrected
cat reports/corrected/report.md
```

기대 결과:

- `execution_mode=fixture`
- `decision=DEMO_ONLY`
- 4개 평가 사례 모두 통과
- 실제 배포 승인 근거가 아니라는 경고 포함

### 4. 인증 없는 live 요청이 BLOCKED로 종료되는지 확인

이 명령은 `BLOCKED`를 표현하기 위해 종료 코드 `2`를 반환합니다.

```bash
set +e
python -m skillops demo live-blocked --out-dir reports/live-blocked
status=$?
set -e
echo "exit=$status"
cat reports/live-blocked/report.md
```

기대 결과:

- `execution_mode=live`
- `decision=BLOCKED`
- 지원되는 인증된 runner 인터페이스가 없다는 이유 기록

### 5. manifest 검증

올바른 manifest:

```bash
python -m skillops validate-manifest --manifest examples/manifests/corrected-fixture.json
```

잘못된 manifest는 성공으로 처리되지 않습니다.

```bash
set +e
python -m skillops validate-manifest --manifest examples/manifests/invalid-manifest.json
status=$?
set -e
echo "exit=$status"
```

## 3분 데모 시나리오

1. `python -m unittest discover -s tests`로 구현 자체 테스트를 실행합니다.
2. `python -m skillops demo risky --out-dir reports/risky`를 실행하고 종료 코드 `1`, 보고서의 `REJECTED`와 회귀 목록을 확인합니다.
3. `python -m skillops demo corrected --out-dir reports/corrected`를 실행하고 종료 코드 `0`, 보고서의 `DEMO_ONLY`와 fixture 경고를 확인합니다.
4. `python -m skillops demo live-blocked --out-dir reports/live-blocked`를 실행하고 종료 코드 `2`, 보고서의 `BLOCKED` 이유를 확인합니다.
5. `python -m skillops validate-manifest --manifest examples/manifests/corrected-fixture.json`로 고정 SHA와 스킬 content hash manifest가 유효한지 확인합니다.

## 평가 사례

평가기는 스킬 이름, 후보 버전, 특정 문구 포함 여부를 보지 않습니다. 결과물의 `request(method, service, path, max_retries=...)` 동작을 로컬 가짜 HTTP 서비스로 검사합니다.

| 사례 | 확인 내용 |
| --- | --- |
| 정상 요청 | 기존처럼 한 번 호출하고 성공 응답을 유지 |
| GET 503 복구 | 일시적인 503을 제한된 재시도로 복구 |
| GET 재시도 제한 | `max_retries`를 초과하지 않음 |
| POST 보호 | 이미 처리되었을 수 있는 POST를 자동 재시도하지 않아 부작용 중복 방지 |

## 보고서 필드

`report.json`과 `report.md`에는 다음 정보가 포함됩니다.

- execution mode: `fixture` 또는 `live`
- baseline/candidate 스킬 content hash
- 대상 저장소 snapshot과 평가기 버전
- 확인 가능한 runtime 정보
- model, token, cost 정보가 없으면 `unavailable`
- 사례별 결과와 실패 근거
- 전체 성공 건수/전체 사례 수
- baseline은 통과했지만 candidate가 실패한 회귀 목록
- 보호 대상 파일 변경 여부
- 실행 시간
- 판정 및 판정 이유

## 배포 manifest

`examples/manifests/corrected-fixture.json`은 이 프로젝트의 SkillOps manifest 예시입니다. GitHub가 기본 제공하는 형식이 아닙니다.

첫 버전은 manifest 검증과 예제만 제공합니다. 실제 적용은 사람이 보고서를 검토한 뒤 별도 PR에서 진행하는 구조입니다. 롤백은 이전 버전 pin을 복원해 이후 실행에 적용하는 것이며, 이미 생성하거나 병합한 코드를 자동 복구하지 않습니다.

## GitHub Actions 보안 원칙

워크플로는 기본 읽기 권한만 사용합니다.

- `permissions: contents: read`
- `pull_request_target` 미사용
- fork PR에 secrets 전달 없음
- 자동 댓글 또는 외부 저장소 쓰기 없음

