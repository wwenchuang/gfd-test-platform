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

verify_health_release() {
  local url="$1"
  local expected_revision="$2"
  echo "检查：${url}"
  local body
  body="$(curl -fsS "${url}")"
  local actual_revision
  actual_revision="$(printf '%s' "${body}" | python3 -c 'import json, sys; print(str(json.load(sys.stdin).get("release_revision") or ""))')"
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

APP_DIR="${APP_DIR}" WEB_DIR="${WEB_DIR}" PORT="${PORT}" RELEASE_REVISION="${DEPLOY_REVISION}" run_as_root bash deploy/install-server.sh

restart_service_if_present midscene-task
restart_service_if_present midscene-api-worker
restart_service_if_present midscene-api-scheduler

for url in ${HEALTH_URLS}; do
  verify_health_release "${url}" "${DEPLOY_REVISION}"
done
verify_login_contract "${AUTH_LOGIN_URL}"
verify_api_test_frontend

echo "部署完成：$(git rev-parse --short HEAD)"
