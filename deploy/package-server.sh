#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${OUT_DIR:-${SRC_DIR}/dist}"
VERSION="${VERSION:-$(date +%Y%m%d-%H%M%S)}"
KEEP_PACKAGES="${KEEP_PACKAGES:-5}"
PACKAGE_NAME="midscene-task-platform-${VERSION}.tar.gz"

mkdir -p "${OUT_DIR}"
find "${OUT_DIR}" -name "._*" -delete 2>/dev/null || true
find "${OUT_DIR}" -name ".DS_Store" -delete 2>/dev/null || true
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

pkg_dir="${tmp_dir}/midscene-task-platform"
mkdir -p "${pkg_dir}"

cp "${SRC_DIR}/midscene-upload.py" "${pkg_dir}/"
cp "${SRC_DIR}/midscene_upload_compat.py" "${pkg_dir}/"
cp "${SRC_DIR}/task-manager.html" "${pkg_dir}/"
cp "${SRC_DIR}/trace-viewer.html" "${pkg_dir}/"
cp "${SRC_DIR}/sonic-midscene-task-runner.groovy" "${pkg_dir}/"
for runner_file in "windows-midscene-runner.py" "mac-midscene-runner.py" "run-mac-midscene-runner.sh"; do
  if [ -f "${SRC_DIR}/${runner_file}" ]; then
    cp "${SRC_DIR}/${runner_file}" "${pkg_dir}/"
  fi
done
if [ -d "${SRC_DIR}/assets" ]; then
  cp -R "${SRC_DIR}/assets" "${pkg_dir}/"
fi
if [ -d "${SRC_DIR}/ai-gateway" ]; then
  mkdir -p "${pkg_dir}/ai-gateway"
  cp -R "${SRC_DIR}/ai-gateway/config" "${pkg_dir}/ai-gateway/"
  cp -R "${SRC_DIR}/ai-gateway/prompts" "${pkg_dir}/ai-gateway/"
  cp -R "${SRC_DIR}/ai-gateway/validators" "${pkg_dir}/ai-gateway/"
  cp -R "${SRC_DIR}/ai-gateway/agent" "${pkg_dir}/ai-gateway/"
  cp "${SRC_DIR}/ai-gateway/package.json" "${pkg_dir}/ai-gateway/"
  cp "${SRC_DIR}/ai-gateway/package-lock.json" "${pkg_dir}/ai-gateway/" 2>/dev/null || true
  cp "${SRC_DIR}/ai-gateway/.env.example" "${pkg_dir}/ai-gateway/"
  cp "${SRC_DIR}/ai-gateway/server.js" "${pkg_dir}/ai-gateway/"
  cp "${SRC_DIR}/ai-gateway/README.md" "${pkg_dir}/ai-gateway/"
  mkdir -p "${pkg_dir}/ai-gateway/agent-assets"
  touch "${pkg_dir}/ai-gateway/agent-assets/.gitkeep"
fi
if [ -d "${SRC_DIR}/css" ]; then
  cp -R "${SRC_DIR}/css" "${pkg_dir}/"
fi
if [ -d "${SRC_DIR}/js" ]; then
  cp -R "${SRC_DIR}/js" "${pkg_dir}/"
fi
cp -R "${SRC_DIR}/ai_skills" "${pkg_dir}/"
if [ -d "${SRC_DIR}/task_server" ]; then
  cp -R "${SRC_DIR}/task_server" "${pkg_dir}/"
fi
if [ -d "${SRC_DIR}/legacy" ]; then
  mkdir -p "${pkg_dir}/legacy"
  if [ -f "${SRC_DIR}/legacy/midscene-upload.legacy.py" ]; then
    cp "${SRC_DIR}/legacy/midscene-upload.legacy.py" "${pkg_dir}/legacy/"
  fi
fi
cp -R "${SRC_DIR}/deploy" "${pkg_dir}/"
if [ -f "${SRC_DIR}/README.md" ]; then
  cp "${SRC_DIR}/README.md" "${pkg_dir}/"
fi
if [ -f "${SRC_DIR}/package.json" ]; then
  cp "${SRC_DIR}/package.json" "${pkg_dir}/"
fi
if [ -f "${SRC_DIR}/package-lock.json" ]; then
  cp "${SRC_DIR}/package-lock.json" "${pkg_dir}/"
fi
if [ -d "${SRC_DIR}/docs" ]; then
  cp -R "${SRC_DIR}/docs" "${pkg_dir}/"
fi
for dir in "server-tasks" "server-tasks-all"; do
  if [ -d "${SRC_DIR}/${dir}" ]; then
    cp -R "${SRC_DIR}/${dir}" "${pkg_dir}/"
  fi
done
if [ -d "${SRC_DIR}/tests" ]; then
  cp -R "${SRC_DIR}/tests" "${pkg_dir}/"
  rm -rf "${pkg_dir}/tests/artifacts"
fi

find "${pkg_dir}" -name ".DS_Store" -delete
find "${pkg_dir}" -name "._*" -delete
find "${pkg_dir}" -name "__MACOSX" -type d -prune -exec rm -rf {} +
find "${pkg_dir}" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "${pkg_dir}" -name ".pytest_cache" -type d -prune -exec rm -rf {} +
find "${pkg_dir}" -name "node_modules" -type d -prune -exec rm -rf {} +
find "${pkg_dir}" -name "dist" -type d -prune -exec rm -rf {} +
find "${pkg_dir}" -name "logs" -type d -prune -exec rm -rf {} +
find "${pkg_dir}" -name "*.tar.gz" -delete
find "${pkg_dir}" -name "*.zip" -delete
find "${pkg_dir}" -name "*.log" -delete
tar -C "${tmp_dir}" -czf "${OUT_DIR}/${PACKAGE_NAME}" "midscene-task-platform"

if [ "${KEEP_PACKAGES}" -gt 0 ] 2>/dev/null; then
  index=0
  while IFS= read -r pkg; do
    [ -n "${pkg}" ] || continue
    index=$((index + 1))
    if [ "${index}" -gt "${KEEP_PACKAGES}" ]; then
      rm -f "${pkg}"
    fi
  done < <(ls -1t "${OUT_DIR}"/midscene-task-platform-*.tar.gz 2>/dev/null || true)
fi

echo "${OUT_DIR}/${PACKAGE_NAME}"
