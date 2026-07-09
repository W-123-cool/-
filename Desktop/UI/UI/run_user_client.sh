#!/usr/bin/env bash
# 取?端快捷启?（?制 python3，避免系? python 指向 2.7）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export DISPLAY="${DISPLAY:-:0}"
export PICKUP_API_BASE="${PICKUP_API_BASE:-http://127.0.0.1:8000}"
cd "$ROOT"
exec python3 -m user_client.main
