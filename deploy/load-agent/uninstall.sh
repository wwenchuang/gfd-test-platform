#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

require_docker_compose
[ -f "${agent_env_file}" ] || fail "未找到 .env，当前目录没有已安装的压测 Agent。"

if [ "${1:-}" = "--purge" ]; then
  compose down --volumes --remove-orphans
  rm -f "${agent_env_file}"
  echo "压测 Agent 容器、凭据数据卷和本机配置已永久删除。平台中的节点记录仍需由管理员停用。"
  exit 0
fi
if [ "$#" -gt 0 ]; then
  fail "未知参数。普通卸载不删凭据；永久删除请明确使用 --purge。"
fi
compose down --remove-orphans
echo "压测 Agent 已停止，凭据数据卷和 .env 已保留；重新执行 install.sh 可恢复。"

