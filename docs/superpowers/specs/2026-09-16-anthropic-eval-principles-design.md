# Anthropic 평가 원칙 반영 설계

## 목표

기존 SkillOps 평가 흐름을 유지하면서 모델 비결정성을 반복 trial로 측정한다. Anthropic `skill-creator`의 파일 형식이나 viewer를 복제하지 않는다.

## 변경

- `baseline`과 `compare`에 `--trials`를 추가한다. 기본값은 기존 비용과 동작을 보존하도록 1이며, 양의 정수만 허용한다.
- 각 작업/arm을 지정 횟수만큼 격리된 작업공간에서 실행한다.
- 보고서에는 trial별 기존 증거를 보존하고 작업별 성공률, 전 trial 성공 여부, judge 점수·시간·비용의 평균과 표준편차를 기록한다.
- 비교 판정은 후보의 모든 trial이 고정·생성 검사를 통과하고, 어느 trial에서도 대응 baseline보다 judge 항목이 퇴보하지 않을 때만 품질 gate를 통과한다.
- base/candidate 실행 순서는 trial마다 교대해 순서 편향을 줄인다.
- 기존 Docker 고정 검사, LLM judge 교정, 원본 CLI JSON, diff artifact는 각각 outcome grader, rubric grader, transcript 검토 근거로 재사용한다.

## 오류 처리

한 trial의 실패도 숨기지 않고 해당 trial에 기록한다. 요청한 모든 fresh pair가 완료되지 않으면 비교는 계속 `blocked`다. 비용 값이 누락되거나 유효하지 않으면 기존 정책대로 판정을 차단한다.

## 검증

작은 단위 테스트로 trial 수 검증, 반복 실행 수와 순서 교대, 성공률·일관성·평균/표준편차 집계, 불완전 trial의 차단을 확인한다. 기존 전체 unittest도 실행해 단일 trial 호환성을 확인한다.

## 문서

README에 `--trials 3` 사용 예와 호출 비용이 trial 수에 비례한다는 점을 추가한다. Anthropic 가이드와의 대응 관계는 반복 trial, 결과 중심 grader, 격리 실행, transcript 검토로 한정해 설명한다.
