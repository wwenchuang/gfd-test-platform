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
echo "最近 80 行日志（不会打印 .env 或注册令牌）："
compose logs --tail 80 load-agent

