#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${CONTAINER:-sonic-server-272-midscene-reports-1}"
SOURCE_HTML="${SOURCE_HTML:-/opt/midscene-task-platform/task-manager.html}"
SOURCE_TRACE_VIEWER="${SOURCE_TRACE_VIEWER:-/opt/midscene-task-platform/trace-viewer.html}"
SOURCE_ASSETS="${SOURCE_ASSETS:-/opt/midscene-task-platform/assets}"
SOURCE_CSS="${SOURCE_CSS:-/opt/midscene-task-platform/css}"
SOURCE_JS="${SOURCE_JS:-/opt/midscene-task-platform/js}"
TARGET_HTML="${TARGET_HTML:-}"

if ! command -v docker >/dev/null 2>&1; then
  echo "缺少 docker 命令"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo "容器未运行：${CONTAINER}"
  exit 1
fi

if [ ! -f "${SOURCE_HTML}" ]; then
  echo "页面不存在：${SOURCE_HTML}"
  exit 1
fi

if [ -z "${TARGET_HTML}" ]; then
  TARGET_HTML="$(docker exec "${CONTAINER}" sh -lc "find / -name task-manager.html 2>/dev/null | head -n 1" | tr -d '\r')"
fi

if [ -z "${TARGET_HTML}" ]; then
  TARGET_HTML="/usr/share/nginx/html/task-manager.html"
fi

backup_path="${TARGET_HTML}.bak.$(date +%Y%m%d-%H%M%S)"
docker exec "${CONTAINER}" sh -lc "if [ -f '${TARGET_HTML}' ]; then cp '${TARGET_HTML}' '${backup_path}'; fi"
docker cp "${SOURCE_HTML}" "${CONTAINER}:${TARGET_HTML}"
docker exec "${CONTAINER}" sh -lc "chmod 644 '${TARGET_HTML}'"
target_dir="$(dirname "${TARGET_HTML}")"
if [ -f "${SOURCE_TRACE_VIEWER}" ]; then
  docker cp "${SOURCE_TRACE_VIEWER}" "${CONTAINER}:${target_dir}/trace-viewer.html"
  docker exec "${CONTAINER}" sh -lc "chmod 644 '${target_dir}/trace-viewer.html'"
fi

if [ -d "${SOURCE_ASSETS}" ]; then
  docker exec "${CONTAINER}" sh -lc "rm -rf '${target_dir}/assets' && mkdir -p '${target_dir}'"
  docker cp "${SOURCE_ASSETS}" "${CONTAINER}:${target_dir}/assets"
  docker exec "${CONTAINER}" sh -lc "find '${target_dir}/assets' -type d -exec chmod 755 {} \\; && find '${target_dir}/assets' -type f -exec chmod 644 {} \\;"
  echo "已同步静态资源到 ${CONTAINER}:${target_dir}/assets"
fi

if [ -d "${SOURCE_CSS}" ]; then
  docker exec "${CONTAINER}" sh -lc "rm -rf '${target_dir}/css' && mkdir -p '${target_dir}'"
  docker cp "${SOURCE_CSS}" "${CONTAINER}:${target_dir}/css"
  docker exec "${CONTAINER}" sh -lc "find '${target_dir}/css' -type d -exec chmod 755 {} \\; && find '${target_dir}/css' -type f -exec chmod 644 {} \\;"
  echo "已同步样式资源到 ${CONTAINER}:${target_dir}/css"
fi

if [ -d "${SOURCE_JS}" ]; then
  docker exec "${CONTAINER}" sh -lc "rm -rf '${target_dir}/js' && mkdir -p '${target_dir}'"
  docker cp "${SOURCE_JS}" "${CONTAINER}:${target_dir}/js"
  docker exec "${CONTAINER}" sh -lc "find '${target_dir}/js' -type d -exec chmod 755 {} \\; && find '${target_dir}/js' -type f -exec chmod 644 {} \\;"
  echo "已同步脚本资源到 ${CONTAINER}:${target_dir}/js"
fi

missing_refs="$(docker exec "${CONTAINER}" sh -lc "
  set -eu
  html='${TARGET_HTML}'
  root='${target_dir}'
  refs=\$(sed -n 's/.*\\(href\\|src\\)=\"\\([^\"]*\\)\".*/\\2/p' \"\$html\" | grep -E '^(assets|css|js)/' || true)
  missing=''
  for ref in \$refs; do
    if [ ! -f \"\$root/\$ref\" ]; then
      missing=\"\$missing \$ref\"
    fi
  done
  printf '%s' \"\$missing\"
" | tr -d '\r')"

if [ -n "${missing_refs}" ]; then
  echo "同步后校验失败，容器内缺少引用文件：${missing_refs}"
  exit 1
fi

echo "已同步页面到 ${CONTAINER}:${TARGET_HTML}"
echo "备份路径：${backup_path}"
