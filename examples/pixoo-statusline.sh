#!/bin/bash

set -euo pipefail

payload="$(cat)"

curl -fsS \
  -X POST \
  -H 'content-type: application/json' \
  --data-binary "$payload" \
  http://127.0.0.1:8765/status >/dev/null 2>&1 || true

session_name="$(jq -r '.session_name // "session"' <<<"$payload")"
ctx_pct="$(jq -r '.context_window.used_percentage // 0' <<<"$payload")"
five_hour_pct="$(jq -r '.rate_limits.five_hour.used_percentage // empty' <<<"$payload")"

if [[ -n "${five_hour_pct}" ]]; then
  printf '%s ctx:%s%% 5h:%s%%\n' "$session_name" "$ctx_pct" "$five_hour_pct"
else
  printf '%s ctx:%s%%\n' "$session_name" "$ctx_pct"
fi
