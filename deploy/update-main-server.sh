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

check_url() {
  local url="$1"
  echo "检查：${url}"
  curl -fsS "${url}" >/dev/null
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

if [ "${BUILD_API_TEST}" = "1" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "BUILD_API_TEST=1 但服务器缺少 npm" >&2
    exit 2
  fi
  npm --prefix api-testing-ui install
  npm --prefix api-testing-ui run build
fi

APP_DIR="${APP_DIR}" WEB_DIR="${WEB_DIR}" PORT="${PORT}" run_as_root bash deploy/install-server.sh

restart_service_if_present midscene-task
restart_service_if_present midscene-api-worker
restart_service_if_present midscene-api-scheduler

for url in ${HEALTH_URLS}; do
  check_url "${url}"
done
verify_api_test_frontend

echo "部署完成：$(git rev-parse --short HEAD)"
