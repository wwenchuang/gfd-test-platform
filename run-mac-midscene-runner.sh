#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export TASK_SERVER="${TASK_SERVER:-http://101.34.197.12:8088}"
export RUNNER_ID="${RUNNER_ID:-mac-runner-01}"
export MIDSCENE_RUNNER_TOKEN="${MIDSCENE_RUNNER_TOKEN:-midscene2026}"
export MIDSCENE_RUNNER_WORKSPACE="${MIDSCENE_RUNNER_WORKSPACE:-$HOME/midscene-runner}"
export POLL_INTERVAL="${POLL_INTERVAL:-3}"
export MIDSCENE_TIMEOUT="${MIDSCENE_TIMEOUT:-900}"

if [[ -n "${DASHSCOPE_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="$DASHSCOPE_API_KEY"
fi

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
export MIDSCENE_MODEL_NAME="${MIDSCENE_MODEL_NAME:-${DASHSCOPE_VL_MODEL:-qwen3.6-plus}}"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "缺少模型 Key：请先执行 export DASHSCOPE_API_KEY=\"你的百炼key\"，或 export OPENAI_API_KEY=\"你的百炼key\""
  exit 1
fi

echo "Midscene model: $MIDSCENE_MODEL_NAME"
echo "Model base URL: $OPENAI_BASE_URL"

python3 ./mac-midscene-runner.py
