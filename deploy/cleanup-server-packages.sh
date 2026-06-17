#!/usr/bin/env bash
set -euo pipefail

KEEP="${KEEP:-3}"
DRY_RUN="${DRY_RUN:-0}"

ROOTS=()
if [ "$#" -gt 0 ]; then
  ROOTS=("$@")
else
  ROOTS=("/tmp" "$(pwd)")
fi

run_cmd() {
  if [ "${DRY_RUN}" = "1" ]; then
    printf '[dry-run] %q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

cleanup_root() {
  local root="$1"
  if [ ! -d "${root}" ]; then
    return
  fi
  echo "清理目录：${root}（保留最近 ${KEEP} 个部署包）"

  find "${root}" -maxdepth 3 \( -name '._*' -o -name '.DS_Store' \) -print -exec rm -f {} \; 2>/dev/null || true

  local index=0
  while IFS= read -r pkg; do
    [ -n "${pkg}" ] || continue
    index=$((index + 1))
    if [ "${index}" -le "${KEEP}" ]; then
      echo "保留部署包：${pkg}"
    else
      echo "删除旧部署包：${pkg}"
      run_cmd rm -f "${pkg}"
    fi
  done < <(
    find "${root}" -maxdepth 2 -type f -name 'midscene-task-platform-*.tar.gz' -print0 2>/dev/null \
      | xargs -0 ls -1t 2>/dev/null || true
  )

  while IFS= read -r dir; do
    [ -n "${dir}" ] || continue
    if [ "${dir}" = "/opt/midscene-task-platform" ]; then
      echo "跳过运行目录：${dir}"
      continue
    fi
    echo "删除临时解压目录：${dir}"
    run_cmd rm -rf "${dir}"
  done < <(find "${root}" -maxdepth 1 -type d -name 'midscene-task-platform' -print 2>/dev/null || true)
}

for root in "${ROOTS[@]}"; do
  cleanup_root "${root}"
done

echo "清理完成。"
