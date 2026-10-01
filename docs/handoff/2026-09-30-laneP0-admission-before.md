# BEFORE Test: chat_admission_busy (default config)

Date: 2026-09-30 15:16:50 (local)
Gateway: http://127.0.0.1:20128
Process: PID 134804 (server-ws.mjs, default admission config)
Concurrent requests: 4 (stream=true, body>=283630 bytes)
Model: auto/cheap

## Results

| Request | Status | Elapsed (ms) | Preview |
|---------|--------|--------------|---------|
| 1 | 503 | 2131 | {"error":{"message":"Chat admission capacity is temporarily unavailable. Retry shortly.","type":"server_error","code":"chat_admission_busy"}} |
| 2 | 503 | 2126 | {"error":{"message":"Chat admission capacity is temporarily unavailable. Retry shortly.","type":"server_error","code":"chat_admission_busy"}} |
| 3 | 503 | 2106 | {"error":{"message":"Chat admission capacity is temporarily unavailable. Retry shortly.","type":"server_error","code":"chat_admission_busy"}} |
| 4 | 503 | 2111 | {"error":{"message":"Chat admission capacity is temporarily unavailable. Retry shortly.","type":"server_error","code":"chat_admission_busy"}} |

## Summary
- Successes (200): 0
- Rejections (503): 4
- Other: 0
- chat_admission_busy detected: True

## Interpretation
CHAT_MAX_HEAVY_IN_FLIGHT defaults to 1, healthy headroom defaults to 1 = 2 max concurrent heavy.
With 4 concurrent heavy streaming requests, expect >=2 rejections (503 chat_admission_busy).

