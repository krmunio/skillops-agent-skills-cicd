# HTTP Retry Policy Skill - Baseline

HTTP 클라이언트 변경 작업에서 기존 동작을 유지한다.

- 요청은 한 번만 수행한다.
- 실패 응답을 자동으로 재시도하지 않는다.
- POST 같은 비멱등 요청의 중복 부작용을 만들지 않는다.
