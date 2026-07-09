#!/usr/bin/env bash
# Aliyun cloud LLM env
#
# Key type -> Base URL (must match):
#   sk-ws-*     Token Plan team  -> token-plan.cn-beijing.maas.aliyuncs.com
#   sk-sp-*     Coding Plan      -> coding.dashscope.aliyuncs.com/v1
#   sk-*        Pay-as-you-go    -> dashscope.aliyuncs.com/compatible-mode/v1
#
# Usage:
#   source scripts/voice_nav_cloud.sh
#   python3 scripts/test_cloud_llm.py

export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-sk-PASTE-YOUR-KEY-HERE}"

# Token Plan (sk-ws-*): use token-plan URL, NOT workspace MaaS URL.
export DASHSCOPE_BASE_URL="${DASHSCOPE_BASE_URL:-https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1}"
export DASHSCOPE_MODEL="${DASHSCOPE_MODEL:-qwen3.6-35b-a3b}"

export VOICE_NAV_USE_LLM=1
export VOICE_NAV_BACKEND="${VOICE_NAV_BACKEND:-cloud}"
export VOICE_NAV_CLOUD_TIMEOUT="${VOICE_NAV_CLOUD_TIMEOUT:-15}"
export VOICE_NAV_CLOUD_MAX_TOKENS="${VOICE_NAV_CLOUD_MAX_TOKENS:-256}"

export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export VOICE_NAV_LLM_TIMEOUT="${VOICE_NAV_LLM_TIMEOUT:-20}"

voice_nav_cloud_info() {
  echo "  backend: ${VOICE_NAV_BACKEND}  model: ${DASHSCOPE_MODEL}"
  echo "  url:     ${DASHSCOPE_BASE_URL}"
  if [[ -z "${DASHSCOPE_API_KEY:-}" || "${DASHSCOPE_API_KEY}" == sk-PASTE-YOUR-KEY-HERE ]]; then
    echo "  [warn] DASHSCOPE_API_KEY not set"
  fi
}
