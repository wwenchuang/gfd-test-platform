#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

require_docker_compose
[ -f "${agent_env_file}" ] || fail "未找到 .env，请先执行 install.sh。"

if [ -n "${LOAD_AGENT_IMAGE:-}" ]; then
  validate_plain_value LOAD_AGENT_IMAGE "${LOAD_AGENT_IMAGE}"
  temporary="${agent_env_file}.tmp.$$"
  awk -v image="${LOAD_AGENT_IMAGE}" '/^LOAD_AGENT_IMAGE=/{print "LOAD_AGENT_IMAGE=" image; found=1; next} {print} END{if(!found) print "LOAD_AGENT_IMAGE=" image}' "${agent_env_file}" > "${temporary}"
  chmod 0600 "${temporary}"
  mv "${temporary}" "${agent_env_file}"
fi

scrub_enrollment_token
build_with_candidates
compose up -d --force-recreate --remove-orphans
echo "压测 Agent 已升级；节点凭据、校准记录和运行数据卷均已保留。"

