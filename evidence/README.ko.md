# 과거 baseline 평가 기록

[English](README.md) | [한국어](README.ko.md)

`baseline-v2.json`은 2026년 9월 14일 `gpt-6-astra`로 수행한 실제 **로컬** Copilot CLI 실행 기록에서
공개할 필드만 추린 정제본입니다. GitHub Actions에서 수행한 모델 실행도, 서명된 증명서도,
스킬 개선의 근거도 아닙니다.

| 작업 | 작업군 / 분할 | 고정 검사 | Judge / 100 |
|---|---|---:|---:|
| pagination-first-page | listing / development | 26/26 | 100 |
| filter-before-pagination | listing / development | 26/26 | 100 |
| pagination-boundaries | listing / development | 26/26 | 100 |
| labels-normalization | labels / development | 17/17 | 100 |
| issue-update-validation | updates / heldout | 22/22 | 100 |

교정 대조 사례는 작업군 3개마다 정상·결함·데이터 내 지시문 삽입 사례를 하나씩, 총 9개입니다.
모든 작업군 gate를 통과했습니다.
작업 수 기준 judge 평균은 분모 5의 100점입니다.
세 작업이 같은 작업군을 공유하므로, 독립 표본 5개로부터 일반화한 결과는 아닙니다.

JSON은 원본 보고서의 SHA-256, 벤치마크·입력·스킬 해시, 모델·CLI·이미지 식별자,
고정·생성 검사 수, judge 항목별 점수, 역할별 사용량의 값·단위·누락 이유,
경과 시간과 집계 분모를 보존합니다. 게시 전에 선택한 값을 원본 보고서 두 개와 대조했습니다.
통화로 환산하거나 누락된 값을 0으로 대체하지 않았습니다.

비공개 프로필, 원본 CLI 로그, 세션 ID, 로컬 artifact 링크, 발견된 개인 스킬 이름,
모델의 자유 형식 응답은 포함하지 않았습니다.
원본 해시는 비공개 원본을 식별할 뿐, 독자가 원본의 진위를 독립적으로 인증하거나
생략된 코드 제안을 복원할 수 있게 해 주지는 않습니다.
새 실제 실행에는 승인된 CLI 로그인과 해당 모델 사용 권한이 필요합니다.

CI는 이 snapshot의 구조와 내부 일관성, 평가기의 오프라인 검사 및 실제 Docker 대조 검사를 실행합니다.
과거 모델 세션을 **다시 실행하지는 않습니다.**
이 파일은 과거 기록이므로 평가기나 벤치마크가 바뀌면 새로운 교정·실행 기록을 만들어야 합니다.
새 소스 해시에 맞추려고 과거 결과를 고쳐 쓰면 안 됩니다.
