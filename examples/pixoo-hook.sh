#!/bin/bash

set -euo pipefail

payload="$(cat)"

curl -fsS \
  -X POST \
  -H 'content-type: application/json' \
  --data-binary "$payload" \
  http://127.0.0.1:8765/hooks >/dev/null 2>&1 || true
