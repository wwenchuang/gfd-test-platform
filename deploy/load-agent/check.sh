#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

require_docker_compose
[ -f "${agent_env_file}" ] || fail "未找到 .env，请先执行 install.sh。"
echo "配置文件权限：$(stat -c '%a' "${agent_env_file}" 2>/dev/null || stat -f '%Lp' "${agent_env_file}")（应为 600）"
echo "容器状态："
compose ps
echo "当前运行版本："
if ! compose exec -T load-agent python -c 'import load_agent; print("Agent", load_agent.__version__)'; then
  echo "Agent 容器尚未就绪，无法读取内部版本。"
fi
if ! compose exec -T load-agent k6 version; then
  echo "k6 尚未就绪，无法读取版本。"
fi
echo "最近 80 行历史排障信息（旧错误不代表当前仍失败；请以容器状态、版本和平台心跳为准）："
compose logs --tail 80 load-agent
