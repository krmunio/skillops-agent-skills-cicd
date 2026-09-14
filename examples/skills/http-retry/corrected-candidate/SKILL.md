# HTTP Retry Policy Skill - Corrected Candidate

HTTP 요청 재시도 정책을 안전하게 적용한다.

- GET 같은 멱등 요청만 일시적인 502, 503, 504 응답에서 제한적으로 재시도한다.
- `max_retries`를 초과하지 않는다.
- POST 같은 비멱등 요청은 이미 처리되었을 수 있으므로 자동 재시도하지 않는다.
