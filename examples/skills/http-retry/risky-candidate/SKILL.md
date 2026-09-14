# HTTP Retry Policy Skill - Risky Candidate

HTTP 5xx 응답이 보이면 성공할 때까지 요청을 반복한다.

이 후보는 재시도 횟수 제한과 비멱등 요청 보호가 없어서 위험한 예시이다.
