#!/usr/bin/env bash
set -euo pipefail

SONIC_CONTAINER_PREFIX="${SONIC_CONTAINER_PREFIX:-sonic-server-272-}"
START_STOPPED="0"

usage() {
  cat <<EOF
用法：bash deploy/configure-sonic-restart.sh [--start-stopped]

为现有 ${SONIC_CONTAINER_PREFIX}* 容器设置 restart=unless-stopped。
默认只修改重启策略，不启动、停止或重启任何容器。
EOF
}

for arg in "$@"; do
  case "${arg}" in
    --start-stopped) START_STOPPED="1" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：${arg}" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "未安装 Docker，无法配置 Sonic 容器" >&2
  exit 1
fi

containers=()
while IFS= read -r container; do
  [ -n "${container}" ] && containers+=("${container}")
done < <(
  docker ps -a --format '{{.Names}}' | awk -v prefix="${SONIC_CONTAINER_PREFIX}" 'index($0, prefix) == 1'
)
if [ "${#containers[@]}" -eq 0 ]; then
  echo "未找到 ${SONIC_CONTAINER_PREFIX}* 容器" >&2
  exit 1
fi

for container in "${containers[@]}"; do
  docker update --restart unless-stopped "${container}" >/dev/null
  if [ "${START_STOPPED}" = "1" ]; then
    running="$(docker inspect -f '{{.State.Running}}' "${container}")"
    if [ "${running}" != "true" ]; then
      docker start "${container}" >/dev/null
    fi
  fi
done

echo "Sonic 容器重启策略："
for container in "${containers[@]}"; do
  docker inspect -f '容器={{.Name}} 状态={{.State.Status}} 重启策略={{.HostConfig.RestartPolicy.Name}}' "${container}"
done
