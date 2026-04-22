#!/bin/bash

set -euo pipefail

if [[ "$#" -eq 0 ]]; then
  echo "usage: pixoo-statusline-chain.sh <statusline-command> [args...]" >&2
  exit 1
fi

payload="$(cat)"

curl -fsS \
  -X POST \
  -H 'content-type: application/json' \
  --data-binary "$payload" \
  http://127.0.0.1:8765/status >/dev/null 2>&1 || true

printf '%s' "$payload" | "$@"
