#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

require_docker_compose
first_install=0
[ -f "${agent_env_file}" ] || first_install=1

platform_url="${PLATFORM_URL:-$(saved_value PLATFORM_URL)}"
enroll_token="${ENROLL_TOKEN:-$(saved_value ENROLL_TOKEN)}"
allow_insecure="${ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT:-$(saved_value ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT)}"
allow_insecure="${allow_insecure:-0}"

[ -n "${platform_url}" ] || fail "PLATFORM_URL 不能为空。请复制平台节点页生成的安装命令。"
if [ "${first_install}" -eq 1 ] && [ -z "${enroll_token}" ]; then
  fail "首次安装必须提供 ENROLL_TOKEN。请在平台“压测节点”页重新生成一次性令牌。"
fi
case "${platform_url}" in
  https://*) ;;
  http://*) [ "${allow_insecure}" = "1" ] || fail "HTTP 仅可用于受控私网/VPN；公网请配置 HTTPS。确认私网后设置 ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT=1。" ;;
  *) fail "PLATFORM_URL 必须是完整的 HTTP(S) 地址。" ;;
esac

for pair in \
  "PLATFORM_URL:${platform_url}" \
  "ENROLL_TOKEN:${enroll_token}" \
  "ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT:${allow_insecure}"; do
  validate_plain_value "${pair%%:*}" "${pair#*:}"
done

value_or_saved() {
  variable="$1"
  fallback="$2"
  eval "current=\${${variable}:-}"
  [ -n "${current}" ] || current="$(saved_value "${variable}")"
  printf '%s' "${current:-${fallback}}"
}

umask 077
temporary="${agent_env_file}.tmp.$$"
trap 'rm -f "${temporary:-}"; scrub_enrollment_token' EXIT
cat > "${temporary}" <<EOF
PLATFORM_URL=${platform_url}
ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT=${allow_insecure}
ENROLL_TOKEN=${enroll_token}
AGENT_MAX_PROCESSES=$(value_or_saved AGENT_MAX_PROCESSES 1)
AGENT_MAX_VUS=$(value_or_saved AGENT_MAX_VUS 500)
AGENT_MAX_ITERATIONS_PER_SECOND=$(value_or_saved AGENT_MAX_ITERATIONS_PER_SECOND 2000)
AGENT_MAX_DURATION_SECONDS=$(value_or_saved AGENT_MAX_DURATION_SECONDS 1800)
LOAD_AGENT_CPU_LIMIT=$(value_or_saved LOAD_AGENT_CPU_LIMIT 2.0)
LOAD_AGENT_MEMORY_LIMIT=$(value_or_saved LOAD_AGENT_MEMORY_LIMIT 2g)
LOAD_AGENT_PIDS_LIMIT=$(value_or_saved LOAD_AGENT_PIDS_LIMIT 256)
LOAD_AGENT_IMAGE=$(value_or_saved LOAD_AGENT_IMAGE midscene-load-agent:0.1.2)
K6_IMAGE=$(value_or_saved K6_IMAGE grafana/k6:0.52.0)
PYTHON_IMAGE=$(value_or_saved PYTHON_IMAGE python:3.12.5-slim-bookworm)
K6_IMAGE_CANDIDATES=$(value_or_saved K6_IMAGE_CANDIDATES grafana/k6:0.52.0)
PYTHON_IMAGE_CANDIDATES=$(value_or_saved PYTHON_IMAGE_CANDIDATES python:3.12.5-slim-bookworm)
AGENT_DATA_DIR=/var/lib/midscene-load-agent
AGENT_REQUEST_TIMEOUT_SECONDS=$(value_or_saved AGENT_REQUEST_TIMEOUT_SECONDS 30)
AGENT_POLL_INTERVAL_SECONDS=$(value_or_saved AGENT_POLL_INTERVAL_SECONDS 2)
AGENT_HEARTBEAT_INTERVAL_SECONDS=$(value_or_saved AGENT_HEARTBEAT_INTERVAL_SECONDS 10)
K6_STOP_GRACE_SECONDS=$(value_or_saved K6_STOP_GRACE_SECONDS 10)
EOF
chmod 0600 "${temporary}"
mv "${temporary}" "${agent_env_file}"

build_with_candidates
compose up -d --remove-orphans

registered=0
attempt=0
while [ "${attempt}" -lt 30 ]; do
  if compose exec -T load-agent test -s /var/lib/midscene-load-agent/credential.json >/dev/null 2>&1; then
    registered=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
[ "${registered}" -eq 1 ] || fail "节点未在 60 秒内完成注册。请执行 bash check.sh 查看日志。"

# 注册凭据已保存在命名卷。清空令牌并重建容器，避免一次性令牌继续出现在容器环境中。
scrub_enrollment_token
compose up -d --force-recreate --remove-orphans
trap - EXIT
echo "压测 Agent 已注册并启动；一次性令牌已从长期配置和容器环境中清除。"
echo "下一步：回到平台“压测节点”页刷新，等待节点在线并点击“校准节点”。"
