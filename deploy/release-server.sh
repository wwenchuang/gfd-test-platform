#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bash deploy/release-server.sh <ssh-target>

Examples:
  bash deploy/release-server.sh root@101.34.197.12
  SSH_PORT=2222 bash deploy/release-server.sh root@101.34.197.12
  RUN_TESTS=0 bash deploy/release-server.sh root@101.34.197.12

Environment:
  SSH_PORT             SSH port. Default: 22
  REMOTE_TMP_DIR       Remote upload directory. Default: /tmp
  RUN_TESTS            Run npm static/visual checks before packaging. Default: 1
  KEEP_PACKAGES        Local package retention count. Default: package-server.sh default
  REMOTE_KEEP_PACKAGES Remote package retention count. Default: 3
  APP_DIR              Remote app directory passed to install-server.sh. Default: /opt/midscene-task-platform
  WEB_DIR              Remote web directory passed to install-server.sh. Default: /www/html
  PORT                 Remote Task service port passed to install-server.sh. Default: 8091
  HEALTH_URLS          Space-separated remote health URLs. Default: http://127.0.0.1:${PORT}/api/health
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

TARGET="${1:-${DEPLOY_TARGET:-}}"
if [ -z "${TARGET}" ]; then
  usage >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SSH_PORT="${SSH_PORT:-22}"
REMOTE_TMP_DIR="${REMOTE_TMP_DIR:-/tmp}"
RUN_TESTS="${RUN_TESTS:-1}"
REMOTE_KEEP_PACKAGES="${REMOTE_KEEP_PACKAGES:-3}"
PORT="${PORT:-8091}"
HEALTH_URLS="${HEALTH_URLS:-http://127.0.0.1:${PORT}/api/health}"

ssh_cmd=(ssh -p "${SSH_PORT}" -o ServerAliveInterval=20 -o ServerAliveCountMax=3 "${TARGET}")
scp_cmd=(scp -P "${SSH_PORT}")

cd "${SRC_DIR}"

if [ "${RUN_TESTS}" != "0" ]; then
  npm run test:static
  npm run test:visual
fi

package_path="$(bash deploy/package-server.sh)"
package_name="$(basename "${package_path}")"
remote_package="${REMOTE_TMP_DIR%/}/${package_name}"
remote_release_dir="${REMOTE_TMP_DIR%/}/midscene-release-${package_name%.tar.gz}"

echo "Uploading ${package_path} to ${TARGET}:${remote_package}"
"${scp_cmd[@]}" "${package_path}" "${TARGET}:${remote_package}"

remote_script="$(cat <<REMOTE
set -euo pipefail
REMOTE_PACKAGE='${remote_package}'
REMOTE_RELEASE_DIR='${remote_release_dir}'
REMOTE_KEEP_PACKAGES='${REMOTE_KEEP_PACKAGES}'
APP_DIR='${APP_DIR:-/opt/midscene-task-platform}'
WEB_DIR='${WEB_DIR:-/www/html}'
PORT='${PORT}'
HEALTH_URLS='${HEALTH_URLS}'

rm -rf "\${REMOTE_RELEASE_DIR}"
mkdir -p "\${REMOTE_RELEASE_DIR}"
tar -xzf "\${REMOTE_PACKAGE}" -C "\${REMOTE_RELEASE_DIR}"
cd "\${REMOTE_RELEASE_DIR}/midscene-task-platform"
find . -name '._*' -delete
find . -name '.DS_Store' -delete

APP_DIR="\${APP_DIR}" WEB_DIR="\${WEB_DIR}" PORT="\${PORT}" bash deploy/install-server.sh
chmod 600 /opt/midscene.env 2>/dev/null || true
systemctl restart midscene-task
systemctl status midscene-task --no-pager -l

for url in \${HEALTH_URLS}; do
  echo "Checking \${url}"
  curl -fsS "\${url}" >/dev/null
done

if command -v bash >/dev/null 2>&1 && [ -x /opt/midscene-task-platform/deploy/cleanup-server-packages.sh ]; then
  KEEP="\${REMOTE_KEEP_PACKAGES}" bash /opt/midscene-task-platform/deploy/cleanup-server-packages.sh "\$(dirname "\${REMOTE_PACKAGE}")" || true
fi

echo "Release complete: \${REMOTE_PACKAGE}"
REMOTE
)"

echo "Installing on ${TARGET}"
"${ssh_cmd[@]}" "${remote_script}"

echo "Deployed ${package_name} to ${TARGET}"
