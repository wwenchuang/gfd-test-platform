#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bash deploy/update-main-server.sh

Recommended server layout:
  /opt/midscene-task-platform-src  # git checkout of main
  /opt/midscene-task-platform      # installed runtime directory

Environment:
  SOURCE_DIR          Git checkout to update. Default: current repo, then /opt/midscene-task-platform-src
  REMOTE              Git remote. Default: origin
  BRANCH              Git branch. Default: main
  APP_DIR             Runtime install directory. Default: /opt/midscene-task-platform
  WEB_DIR             Static web root passed to install-server.sh. Default: /www/html
  PORT                Task service port. Default: 8091
  HEALTH_URLS         Space-separated health URLs. Default: http://127.0.0.1:8091/api/health http://127.0.0.1:8088/api/health
  AUTH_LOGIN_URL      Login contract URL. Default: http://127.0.0.1:8091/api/auth/login
  API_TEST_URL        API testing frontend URL to verify. Default: http://127.0.0.1:8088/api-test/
  REQUIRE_API_TEST_TEXT  Text expected in an API testing JS bundle. Default: 用例管理
  ALLOW_DIRTY         Set to 1 to allow deploying from a dirty git checkout. Default: 0
  BUILD_API_TEST      Set to 1 to run npm install/build before install. Default: 0
  SONIC_CONTAINER_PREFIX  Sonic Docker container name prefix observed before/after deploy. Default: sonic-server-272-
  CONFIGURE_SONIC_RESTART Set to 1 to configure existing Sonic containers with restart=unless-stopped. Default: 1
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_SOURCE_DIR="/opt/midscene-task-platform-src"

if [ -z "${SOURCE_DIR:-}" ]; then
  if [ -d "${SCRIPT_SOURCE_DIR}/.git" ]; then
    SOURCE_DIR="${SCRIPT_SOURCE_DIR}"
  else
    SOURCE_DIR="${DEFAULT_SOURCE_DIR}"
  fi
fi

REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/midscene-task-platform}"
WEB_DIR="${WEB_DIR:-/www/html}"
PORT="${PORT:-8091}"
HEALTH_URLS="${HEALTH_URLS:-http://127.0.0.1:${PORT}/api/health http://127.0.0.1:8088/api/health}"
API_TEST_URL="${API_TEST_URL:-http://127.0.0.1:8088/api-test/}"
AUTH_LOGIN_URL="${AUTH_LOGIN_URL:-http://127.0.0.1:${PORT}/api/auth/login}"
REQUIRE_API_TEST_TEXT="${REQUIRE_API_TEST_TEXT:-用例管理}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
BUILD_API_TEST="${BUILD_API_TEST:-0}"
SONIC_CONTAINER_PREFIX="${SONIC_CONTAINER_PREFIX:-sonic-server-272-}"
CONFIGURE_SONIC_RESTART="${CONFIGURE_SONIC_RESTART:-1}"

if [ ! -d "${SOURCE_DIR}/.git" ]; then
  cat >&2 <<EOF
源码目录不是 git 仓库：${SOURCE_DIR}

首次准备可在服务器执行：
  git clone <repo-url> ${DEFAULT_SOURCE_DIR}
  cd ${DEFAULT_SOURCE_DIR}
  bash deploy/update-main-server.sh
EOF
  exit 2
fi

if ! command -v git >/dev/null 2>&1; then
  echo "缺少 git 命令" >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "缺少 curl 命令" >&2
  exit 2
fi

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -E "$@"
  else
    echo "当前用户不是 root，且缺少 sudo：$*" >&2
    exit 2
  fi
}

TASK_SERVICE_NEEDS_RESTORE=0

restore_task_service_on_exit() {
  local status=$?
  trap - EXIT
  if [ "${TASK_SERVICE_NEEDS_RESTORE}" = "1" ]; then
    echo "部署异常退出，自动恢复 midscene-task.service" >&2
    run_as_root systemctl start midscene-task.service >/dev/null 2>&1 || true
  fi
  exit "${status}"
}

trap restore_task_service_on_exit EXIT

SONIC_STATE_CAPTURED=0
SONIC_RUNNING_BEFORE=""

sonic_container_names() {
  local include_stopped="${1:-0}"
  local docker_args=(ps --format '{{.Names}}')
  if [ "${include_stopped}" = "1" ]; then
    docker_args=(ps -a --format '{{.Names}}')
  fi
  run_as_root docker "${docker_args[@]}" \
    | awk -v prefix="${SONIC_CONTAINER_PREFIX}" 'index($0, prefix) == 1'
}

capture_sonic_container_state() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "未检测到 Docker，跳过 Sonic 容器状态保护"
    return 0
  fi

  local all_containers
  if ! all_containers="$(sonic_container_names 1)"; then
    echo "无法读取 Docker 容器状态，跳过 Sonic 容器状态保护" >&2
    return 0
  fi
  if ! SONIC_RUNNING_BEFORE="$(sonic_container_names 0)"; then
    echo "无法读取运行中的 Docker 容器，跳过 Sonic 容器状态保护" >&2
    SONIC_RUNNING_BEFORE=""
    return 0
  fi
  SONIC_STATE_CAPTURED=1

  if [ -z "${all_containers}" ]; then
    echo "部署前未发现 ${SONIC_CONTAINER_PREFIX}* 容器"
  elif [ -z "${SONIC_RUNNING_BEFORE}" ]; then
    echo "部署前 Sonic 容器已全部停止；本次部署不会自动启动，但会更新异常恢复策略"
  else
    echo "部署前运行中的 Sonic 容器："
    printf '%s\n' "${SONIC_RUNNING_BEFORE}"
  fi
}

configure_sonic_restart_policy() {
  if [ "${CONFIGURE_SONIC_RESTART}" != "1" ]; then
    echo "已按配置跳过 Sonic 自动恢复策略设置"
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1; then
    echo "未检测到 Docker，跳过 Sonic 自动恢复策略设置"
    return 0
  fi

  local containers
  containers="$(sonic_container_names 1 2>/dev/null || true)"
  if [ -z "${containers}" ]; then
    echo "未发现 ${SONIC_CONTAINER_PREFIX}* 容器，跳过 Sonic 自动恢复策略设置"
    return 0
  fi

  echo "配置 Sonic 容器异常退出后自动恢复（不会启动、停止或重启容器）"
  run_as_root env \
    SONIC_CONTAINER_PREFIX="${SONIC_CONTAINER_PREFIX}" \
    bash deploy/configure-sonic-restart.sh
}

verify_sonic_container_state() {
  [ "${SONIC_STATE_CAPTURED}" = "1" ] || return 0
  [ -n "${SONIC_RUNNING_BEFORE}" ] || return 0

  local running_after
  if ! running_after="$(sonic_container_names 0)"; then
    echo "部署后无法读取 Sonic 容器状态" >&2
    return 1
  fi

  local stopped=""
  local container
  while IFS= read -r container; do
    [ -n "${container}" ] || continue
    if ! printf '%s\n' "${running_after}" | grep -Fxq "${container}"; then
      stopped="${stopped}${container}"$'\n'
    fi
  done <<< "${SONIC_RUNNING_BEFORE}"

  if [ -n "${stopped}" ]; then
    echo "Sonic 容器在部署期间停止，拒绝把本次部署标记为完成：" >&2
    printf '%s' "${stopped}" >&2
    run_as_root docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' \
      | awk -v prefix="${SONIC_CONTAINER_PREFIX}" 'NR == 1 || index($1, prefix) == 1' >&2 || true
    echo "恢复现有容器：docker ps -a --format '{{.Names}}' | grep '^${SONIC_CONTAINER_PREFIX}' | xargs -r docker start" >&2
    return 1
  fi
  echo "Sonic 容器状态未被本次部署改变"
}

warn_sonic_restart_policies() {
  command -v docker >/dev/null 2>&1 || return 0
  local containers container policy unsafe=""
  containers="$(sonic_container_names 1 2>/dev/null || true)"
  [ -n "${containers}" ] || return 0
  while IFS= read -r container; do
    [ -n "${container}" ] || continue
    policy="$(run_as_root docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "${container}" 2>/dev/null || true)"
    if [ -z "${policy}" ] || [ "${policy}" = "no" ]; then
      unsafe="${unsafe}${container} restart=${policy:-未知}"$'\n'
    fi
  done <<< "${containers}"
  if [ -n "${unsafe}" ]; then
    echo "提示：以下 Sonic 容器退出后不会自动恢复（restart=no）：" >&2
    printf '%s' "${unsafe}" >&2
    echo "一次性修正：bash deploy/configure-sonic-restart.sh" >&2
  else
    echo "Sonic 容器均已配置退出后自动恢复"
  fi
}

disable_legacy_systemd_units() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  local unit state definition
  while read -r unit state; do
    [ -n "${unit}" ] || continue
    [ "${unit}" != "midscene-task.service" ] || continue
    definition="$(run_as_root systemctl cat "${unit}" 2>/dev/null || true)"
    printf '%s\n' "${definition}" | grep -Eq '^[[:space:]]*ExecStart=.*midscene-upload\.py([[:space:]]|$)' || continue
    echo "停用旧后端 systemd 服务：${unit}（${state:-状态未知}）"
    run_as_root systemctl disable --now "${unit}"
  done < <(run_as_root systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null || true)
  run_as_root systemctl daemon-reload
}

remove_legacy_pm2_processes() {
  if ! command -v pm2 >/dev/null 2>&1; then
    return 0
  fi

  local legacy_ids
  if ! legacy_ids="$({ run_as_root pm2 jlist 2>/dev/null || true; } | python3 -c '
import json
import sys

processes = json.load(sys.stdin)
for process in processes:
    env = process.get("pm2_env") or {}
    values = [
        env.get("pm_exec_path"),
        env.get("args"),
    ]
    command = " ".join(
        " ".join(map(str, value)) if isinstance(value, list) else str(value or "")
        for value in values
    )
    if "midscene-upload.py" in command:
        print(process.get("pm_id"))
')"; then
    echo "无法读取 PM2 进程清单，拒绝在未确认旧后端状态时继续部署" >&2
    return 1
  fi
  [ -n "${legacy_ids}" ] || return 0

  local process_id
  for process_id in ${legacy_ids}; do
    [ "${process_id}" != "None" ] || continue
    echo "移除 PM2 旧后端进程：${process_id}"
    run_as_root pm2 delete "${process_id}"
  done
  run_as_root pm2 save
}

backup_legacy_entrypoint() {
  local legacy_entrypoint="/opt/midscene-upload.py"
  [ -e "${legacy_entrypoint}" ] || return 0

  local backup_path="/opt/midscene-upload.py.disabled.$(date +%Y%m%d-%H%M%S)"
  echo "备份并停用旧后端入口：${backup_path}"
  run_as_root mv "${legacy_entrypoint}" "${backup_path}"
}

migrate_legacy_task_launchers() {
  echo "检查旧后端启动器"
  disable_legacy_systemd_units
  remove_legacy_pm2_processes
  backup_legacy_entrypoint
}

restart_service_if_present() {
  local service="$1"
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  if systemctl list-unit-files --type=service --no-legend "${service}.service" 2>/dev/null | grep -q "^${service}.service" \
    || systemctl status "${service}.service" >/dev/null 2>&1; then
    run_as_root systemctl restart "${service}.service"
  fi
}

listener_pids() {
  if command -v fuser >/dev/null 2>&1; then
    run_as_root fuser -n tcp "${PORT}" 2>/dev/null || true
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    run_as_root ss -H -ltnp "sport = :${PORT}" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | sort -u
    return 0
  fi
  echo "无法检查 ${PORT} 端口监听进程：服务器缺少 fuser 和 ss" >&2
  return 2
}

stop_stale_task_listeners() {
  local pids
  pids="$(listener_pids)" || return $?
  [ -n "${pids}" ] || return 0

  local pid
  for pid in ${pids}; do
    local command_line
    command_line="$(run_as_root ps -p "${pid}" -o args= 2>/dev/null || true)"
    case "${command_line}" in
      *task_server*|*midscene-upload.py*|*midscene-task-platform*)
        echo "停止遗留后端进程：PID ${pid} ${command_line}"
        run_as_root kill -TERM "${pid}" 2>/dev/null || true
        ;;
      *)
        echo "${PORT} 端口被未知进程占用，拒绝自动终止：PID ${pid} ${command_line:-命令未知}" >&2
        return 1
        ;;
    esac
  done

  for _ in $(seq 1 20); do
    [ -z "$(listener_pids)" ] && return 0
    sleep 0.25
  done
  pids="$(listener_pids)"
  for pid in ${pids}; do
    echo "遗留后端进程未在 5 秒内退出，强制停止：PID ${pid}" >&2
    run_as_root kill -KILL "${pid}" 2>/dev/null || true
  done
}

restart_task_service_cleanly() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "缺少 systemctl，无法确保后端进程已替换" >&2
    return 2
  fi
  TASK_SERVICE_NEEDS_RESTORE=1
  run_as_root systemctl stop midscene-task.service >/dev/null 2>&1 || true
  stop_stale_task_listeners
  run_as_root systemctl start midscene-task.service
  for _ in $(seq 1 40); do
    if run_as_root systemctl is-active --quiet midscene-task.service \
      && curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
      TASK_SERVICE_NEEDS_RESTORE=0
      return 0
    fi
    sleep 0.5
  done
  run_as_root systemctl status midscene-task.service --no-pager >&2 || true
  run_as_root journalctl -u midscene-task.service -n 80 --no-pager >&2 || true
  echo "后端服务启动后 20 秒内未就绪" >&2
  return 1
}

verify_health_release() {
  local url="$1"
  local expected_revision="$2"
  echo "检查：${url}"
  local body
  body="$(curl -fsS "${url}")"
  local actual_revision
  actual_revision="$(printf '%s' "${body}" | python3 -c 'import json, sys; print(str(json.load(sys.stdin).get("release_revision") or ""))')"
  if ! printf '%s' "${body}" | python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("dependencies", {}).get("pyyaml") is True else 1)'; then
    echo "YAML 强校验未就绪：请在 Task 服务虚拟环境安装 PyYAML 并重启，再检查 /api/health。" >&2
    return 1
  fi
  if [ "${actual_revision}" != "${expected_revision}" ]; then
    echo "后端运行版本不一致：${url}" >&2
    echo "期望：${expected_revision}" >&2
    echo "实际：${actual_revision:-未上报（可能仍是旧进程）}" >&2
    echo "请检查 midscene-task.service 状态以及 ${PORT} 端口是否被旧进程占用。" >&2
    return 1
  fi
}

verify_login_contract() {
  local url="$1"
  echo "检查：${url}"
  local body_file
  body_file="$(mktemp)"
  local status
  status="$(curl -sS -o "${body_file}" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' \
    --data '{"username":"__deployment_contract_probe__","password":"__deployment_contract_probe__"}' \
    "${url}")"
  if [ "${status}" != "401" ] || ! python3 - "${body_file}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if payload.get("ok") is not False or not isinstance(payload.get("error"), str):
    raise SystemExit(1)
PY
  then
    echo "登录路由合同检查失败：${url} 返回 HTTP ${status}，期望 HTTP 401 JSON。" >&2
    rm -f "${body_file}"
    return 1
  fi
  rm -f "${body_file}"
}

verify_api_test_frontend() {
  [ -n "${API_TEST_URL}" ] || return 0
  echo "检查：${API_TEST_URL}"
  local index
  index="$(curl -fsS "${API_TEST_URL}")"
  local origin="${API_TEST_URL%%/api-test*}"
  local refs
  refs="$(printf '%s\n' "${index}" | sed -n 's/.*\(href\|src\)="\([^"]*\)".*/\2/p' | grep -E '(^/api-test/|^assets/)' || true)"
  if [ -z "${refs}" ]; then
    echo "API testing 页面没有找到静态资源引用：${API_TEST_URL}" >&2
    return 1
  fi

  local required_text_found=0
  local ref
  while IFS= read -r ref; do
    [ -n "${ref}" ] || continue
    local asset_url
    case "${ref}" in
      http://*|https://*) asset_url="${ref}" ;;
      /api-test/*) asset_url="${origin}${ref}" ;;
      assets/*) asset_url="${API_TEST_URL%/}/${ref}" ;;
      *) continue ;;
    esac
    echo "检查：${asset_url}"
    local asset_body
    asset_body="$(curl -fsS "${asset_url}")"
    case "${asset_url}" in
      *.js*)
        if [ -z "${REQUIRE_API_TEST_TEXT}" ] || [[ "${asset_body}" == *"${REQUIRE_API_TEST_TEXT}"* ]]; then
          required_text_found=1
        fi
        ;;
    esac
  done <<< "${refs}"

  if [ -n "${REQUIRE_API_TEST_TEXT}" ] && [ "${required_text_found}" != "1" ]; then
    echo "API testing JS 未包含预期入口文案：${REQUIRE_API_TEST_TEXT}" >&2
    return 1
  fi
}

cd "${SOURCE_DIR}"
echo "源码目录：${SOURCE_DIR}"
echo "目标分支：${REMOTE}/${BRANCH}"

if [ "${ALLOW_DIRTY}" != "1" ] && [ -n "$(git status --porcelain)" ]; then
  echo "源码目录存在未提交改动，拒绝部署。确认要覆盖时设置 ALLOW_DIRTY=1。" >&2
  git status --short >&2
  exit 2
fi

git fetch "${REMOTE}" "${BRANCH}"
if git rev-parse --verify "${BRANCH}" >/dev/null 2>&1; then
  git checkout "${BRANCH}"
else
  git checkout -B "${BRANCH}" "${REMOTE}/${BRANCH}"
fi
git pull --ff-only "${REMOTE}" "${BRANCH}"
DEPLOY_REVISION="$(git rev-parse HEAD)"

if [ "${BUILD_API_TEST}" = "1" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "BUILD_API_TEST=1 但服务器缺少 npm" >&2
    exit 2
  fi
  npm --prefix api-testing-ui install
  npm --prefix api-testing-ui run build
fi

capture_sonic_container_state
configure_sonic_restart_policy
migrate_legacy_task_launchers

TASK_SERVICE_NEEDS_RESTORE=1
APP_DIR="${APP_DIR}" WEB_DIR="${WEB_DIR}" PORT="${PORT}" RELEASE_REVISION="${DEPLOY_REVISION}" run_as_root bash deploy/install-server.sh
TASK_SERVICE_NEEDS_RESTORE=0

restart_task_service_cleanly
restart_service_if_present midscene-api-worker
restart_service_if_present midscene-api-scheduler
verify_sonic_container_state
warn_sonic_restart_policies

for url in ${HEALTH_URLS}; do
  verify_health_release "${url}" "${DEPLOY_REVISION}"
done
verify_login_contract "${AUTH_LOGIN_URL}"
verify_api_test_frontend

echo "部署完成：$(git rev-parse --short HEAD)"
