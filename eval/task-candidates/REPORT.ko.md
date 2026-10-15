# 공개 버그 과제 재현 조사

**세 후보 모두 수정 전 의도한 assertion 실패 / 수정 후 통과를 확보했다.**
이는 과거 upstream 버그의 재현이며, 원본 Skill을 사용한 에이전트의 실패나
모델 성능 개선을 검증한 결과가 아니다. 모델 평가·유료 모델 실행·GEPA는 수행하지 않았다.

## 결과와 선정

| 후보 | 수정 전 | 수정 후 | 검사 출처 | 권장 방식 |
|---|---:|---:|---|---|
| [RQ #2515](https://github.com/rq/rq/pull/2515) | 1 실패 / 2 통과 | 3 통과 | upstream 회귀 메서드 + 기존 pipeline 검사 2개 | 원본 저장소 직접 평가 |
| [RQ #2516](https://github.com/rq/rq/pull/2516) | 3 실패 / 7 통과 | 10 통과 | 새로 작성한 오류 주입 통합 검사 | project_d 목업의 동작 패턴 참고 |
| [urllib3 #3743](https://github.com/urllib3/urllib3/pull/3743) | 3 실패 / 1 통과 | 4 통과 | 새로 작성한 결정적 경계 검사 | 원본 저장소 직접 평가 |

독립적인 원본 저장소 과제 두 개가 필요하면 **RQ #2515 + urllib3 #3743**를 우선 사용한다.
상태 전이·예외 경계·부작용의 결합을 강조할 때는 **RQ #2516**이 더 풍부한 사례다.
추가 후보로 확장할 필요는 없었다. 전체 upstream 테스트 스위트를 검증했다고 주장하지 않는다.

## 커밋과 라이선스

| 후보 | 실제 수정 전 커밋 | 실제 수정 후 병합 커밋 | 라이선스 |
|---|---|---|---|
| rq-2515 | `a7233f3a25c2848a071f69b6e4f5be3316b7ab82` | `f114a649c57555bed3100b7b1e00fb8996d5d1d5` | BSD-2-Clause |
| rq-2516 | `a41a36b431b67e329d671bc5cf7455df5890ab1e` | `7d7f925c9a4a5bc0ac5d947d0a227e5fe167c44c` | BSD-2-Clause |
| urllib3-3743 | `8c480bf87bcefd321b3a1ae47f04e908b6b2ed7b` | `13f0bfd55e4468fe1ea9c6f809d3a87b0f93ebab` | MIT |

각 병합 커밋의 실제 첫 부모를 Git으로 확인했다. PR 메타데이터의 base SHA는 세 경우
모두 위 부모와 달랐으며 수정 전 증거로 사용하지 않았다.
`evaluator/provenance.json`에 실제 부모·변경 파일·라이선스 SHA-256을 남겼다.
양쪽 소스의 라이선스 본문과 보관한 고지 파일의 바이트 동일성도 검사했다.
조건과 저작권 고지를 `licenses/`에 보존했으며 `NOTICE.md`에 출처/변경 범위를 명시했다.
GitHub API의 라이선스 분류나 `NOASSERTION`으로 사용 허가를 추정하지 않았다.

## 버그별 근거

**RQ #2515 — 일회성 입력의 소실.** 수정 전에는 독립 작업만 반환되어
`['independent'] != ['independent', 'dependent']` assertion이 실패했다.
수정 후에는 반환 순서·실제 큐 순서·의존 작업의 DEFERRED 저장·deferred registry가 모두 맞았다.
추가된 upstream 회귀 메서드는 래퍼/임포트만 조정했으며 본문 AST가 같다.
두 기존 pipeline 검사도 전후 AST 동일성을 확인했다. 소스는 `rq/queue.py`,
upstream 검사는 `tests/test_queue.py`다.

**RQ #2516 — 실패 처리 이후의 독립적인 부작용.** 일반 실패와 재시도 소진에 대해
정상·의존 등록 실패·저장 실패·첫 저장만 실패·슬롯 반환 실패의 2×5 조합을 검사했다.
수정 전에는 의존 등록 오류 뒤 Redis의 실제 슬롯이 두 경로 모두 남았다.
일반 실패의 첫 저장 오류가 무시된 경우, 실패 상태가 저장되지 않았는데도 후속 처리가 시도됐다.
수정 후에는 저장 실패 시 후속 처리가 없고, 저장 성공 후 의존 등록 실패 시에도 슬롯이 반환됐다.
오류가 기록되는지, 실패 상태/registry/result가 남는지도 검사했다.
주입한 ConnectionError는 redis-py `Pipeline.execute()`의 `finally/reset`처럼
전송되지 않은 명령 버퍼를 비운다. 실제 서버 장애나 commit 후 응답 유실을 재현한 것은 아니다.
PR은 `rq/worker/base.py`만 바꿨으며 **회귀 테스트를 추가하지 않았다**.
여기의 검사는 새로 작성한 평가자용 검사다.
PR 제목과 달리 최종 패치는 슬롯 반환을 의존 등록보다 앞으로 옮기지 않는다.
핵심은 저장 성공을 경계로 후속 작업의 예외 처리를 분리하는 것이다.

**urllib3 #3743 — 과도한 대기 입력의 제한.** 큰 숫자는 `9999999999`,
고정 시각 기준 미래 날짜는 `999999`를 그대로 반환하여 기본 상한 `21600`과 달랐다.
sleep mock도 `sleep(21600)` 대신 `sleep(9999999999)` 호출을 관찰했다.
수정 후에는 숫자/날짜 입력의 기본·사용자 상한, `new()`/`increment()`의 설정 유지,
정상·잘못된·없음·0초 헤더 동작이 통과했다.
실제 긴 대기나 native `OverflowError` 호출 대신 **sleep 경계에 전달된 잘못된 값**을 입증했다.
PR 초기 설명의 `2^31-1` 초 초과 거부가 아니라 최종 병합 계약인 기본 6시간 제한을 적용했다.
upstream에는 `test/test_retry.py` 검사가 추가됐지만 새 상수 접근으로 먼저 실패하므로,
의도한 값 assertion부터 실패하는 새 검사를 사용했다. 대상 소스는 `src/urllib3/util/retry.py`다.

## 실행 및 증거 확인

호스트 Python 3.12 이상, Git, Docker CLI/daemon과 준비 단계의 공개 다운로드 접근이 필요하다.
이 worktree에서 실행한다:

```bash
python3 eval/task-candidates/reproduce.py prepare
python3 eval/task-candidates/reproduce.py run
python3 eval/task-candidates/reproduce.py verify
```

개별 실행: `run --candidate rq-2516`; 한쪽만 실행: `--phase before` 또는 `--phase after`.
`run`의 성공 종료는 **before가 예상한 assertion으로 실패하고 after가 통과했다**는 의미다.
의존성/수집 오류·예상과 다른 실패·누락된 결과는 성공으로 취급하지 않는다.
`verify`는 코드를 실행하지 않고 저장된 전후 증거의 체크/러너/요구사항/설치기록 해시,
테스트 ID·실패 종류·실행환경·종료코드를 비교한다.

Linux aarch64, Python 3.12.14, pytest 8.3.5, Redis 7.2.5, redis-py 5.0.8을 사용했다.
나머지 정확한 버전은 `requirements.txt`, 다운로드 wheel 해시는
`evaluator/dependency-install-report.json`, 컨테이너 digest와 실행 인자는 각 `logs/*.json`에 있다.
양쪽은 같은 이미지·설치된 의존성 디렉터리·검사 파일·러너를 사용한다.
최종 검사로 전후 실행을 반복했으며 `repeat-02/`와 최종 로그의 결과가 같다.

의존성 준비만 네트워크를 사용한다. 바이너리 wheel만 설치하며 호스트에서는 upstream 코드를
임포트하거나 빌드하지 않는다. 테스트는 UID 65534, capability 전부 제거,
no-new-privileges, read-only 루트/입력, 1 CPU·512 MiB·64 PID·90초 제한으로 실행한다.
Redis는 일회성 network-none 컨테이너이고 RQ 검사만 그 loopback namespace에 합류한다.
외부 네트워크 인터페이스 없음·비루트·권한 플래그를 러너에서 확인한다.
호스트 자격증명·SSH agent·Docker socket·개인 설정·다른 세션 데이터는 마운트하지 않는다.
컨테이너는 종료 시 삭제하며 포트는 공개하지 않는다. 루트 권한 Docker daemon의 공유 커널
컨테이너이지 VM 수준 격리는 아니라는 한계는 남는다.

## 실패/환경 차단 및 해결 이력

| 시도 | 분류 | 조치/증거 |
|---|---|---|
| bare Git에 `git -C` 접근 | 준비 환경 차단 | `safe.bareRepository=explicit`; `--git-dir`로 해결. 검사 실행 전 오류 |
| RQ #2515 초기 클래스 임포트 | 검사 수집 문제 | before 59 / after 60개로 달라 최종 증거에서 제외. 모듈 임포트로 3개 고정. `logs/attempt-01/` 보존 |
| urllib3 최초 임포트 | 환경 차단, 버그 아님 | `_version.py` 누락. `logs/attempt-01/` 보존 |
| urllib3 동일 offline build | 환경 차단 해결 | 고정 hatchling/hatch-vcs로 각 소스를 임시 컨테이너 경로에서 빌드. 합성 버전 `0.0.0+reproduction`을 양쪽 동일 적용 |
| RQ #2516 최초 검사 | 예비 근거 | 8개 event 중심 검사에서 10개 실제 상태 검사로 확장, Redis reset 동작 반영. 초기 로그를 대체하지 않고 별도 보관 |

urllib3의 버전 대체는 소스 archive에 `.git`이 없어서 필요한 패키징 설정이다.
버그 소스나 검사 기대값을 패치하지 않았다. 각 원본 소스의 upstream 빌드 훅은
네트워크 없는 제한 컨테이너 안에서만 실행하며, PyPI의 수정된 urllib3를 대신 임포트하지 않는다.
해결하지 못한 환경 차단 후보는 없다.

## 모델·평가자 분리 및 project_d 인계

모델에 제공할 설명은 `tasks/*.md`에만 있다.
`manifest.json`, 보고서, `evaluator/`의 PR 정보·정답 패치·검사·로그,
후속 커밋을 포함하는 `.cache/`는 평가자 전용이다.
실제 평가에서는 모델에 **선택한 before 소스의 새 archive와 해당 task 문서만** 제공하고,
Git 이력·수정 후 소스·평가자 경로·네트워크를 차단해야 한다.
이 폴더를 통째로 모델 환경에 마운트하면 분리 요건을 충족하지 못한다.
정답 패치는 `evaluator/<id>/solution.patch`, 원본 API 근거는 같은 폴더의 JSON이다.

project_d 담당자에게 전달할 계약은 다음과 같다. project_d 파일은 생성하거나 수정하지 않았다.

| 패턴 | 최소 검사 아이디어 |
|---|---|
| 한 번만 소비할 수 있는 입력 | generator에 독립/의존 항목 혼합, 유실·중복 없음, 반환/큐 순서, deferred 저장, 호출자 commit 경계 |
| 저장과 후속 부작용의 예외 경계 | 저장 실패 시 알림·등록·반환 없음; 저장 성공 뒤 등록 실패에도 반환 시도; 반환 오류 로그; 일반 실패/재시도 소진 두 경로 |
| 외부 대기시간 상한과 설정 전파 | 숫자·날짜 경계, 0/과거/잘못된 값, 복제 이후 사용자 상한 유지, fake clock 및 sleep 인자 관찰 |

목업에서 같은 패턴을 구현해도 원본 저장소의 검증을 그대로 이전한 것으로 간주해서는 안 된다.
목업에 대해 별도로 실패/통과와 의미 동등성을 확인해야 한다.

**공개 정답으로 인한 학습 데이터 오염·검색을 통한 정답 접근 가능성은 제거되지 않았다.**
알려진 수정의 재현 성공이 새로운 문제 해결 능력이나 Skill 개선 효과를 의미하지 않는다.
다른 Python/Redis/플랫폼, 모든 iterable/의존 그래프, stopped/nonterminal retry,
실제 webhook/의존 작업 실행, commit 후 통신 단절, 대기 작업 승격은 미검증이다.

## 작업 경계와 로컬 인계

기준: `0990e253f57f0454fa084f6bb85b59aea006cc03`.
브랜치: `research/task-candidates-78115b58`.
Worktree: `/home/kymun/krmunio/dev/skillops/.worktrees/task-candidates-78115b58`.
변경은 `eval/task-candidates/`에만 있다. 현재 root main의 변경, 다른 worktree,
`projects/project_c/**`, `projects/project_d/**`, `skillops/**`, 기존 데이터셋과 private 데이터는 건드리지 않았다.
로컬 커밋만 생성하며 push·PR·merge·외부 공개를 하지 않는다.
최종 커밋 SHA는 인계 응답 또는 이 worktree의 `git rev-parse HEAD`로 확인한다.
