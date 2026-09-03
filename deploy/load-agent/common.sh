#!/usr/bin/env bash

agent_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
agent_env_file="${agent_script_dir}/.env"
agent_compose_file="${agent_script_dir}/docker-compose.yml"

fail() {
  echo "错误：$*" >&2
  exit 1
}

require_docker_compose() {
  command -v docker >/dev/null 2>&1 || fail "未安装 Docker，请先安装 Docker Engine。"
  docker compose version >/dev/null 2>&1 || fail "未安装 Docker Compose v2 插件。"
}

saved_value() {
  key="$1"
  [ -f "${agent_env_file}" ] || return 0
  awk -v wanted="${key}" 'index($0, wanted "=") == 1 { print substr($0, length(wanted) + 2); exit }' "${agent_env_file}"
}

validate_plain_value() {
  name="$1"
  value="$2"
  case "${value}" in
    *$'\n'*|*$'\r'*) fail "${name} 不能包含换行" ;;
  esac
}

compose() {
  docker compose --project-directory "${agent_script_dir}" --env-file "${agent_env_file}" -f "${agent_compose_file}" "$@"
}

scrub_enrollment_token() {
  [ -f "${agent_env_file}" ] || return 0
  temporary="${agent_env_file}.tmp.$$"
  awk '/^ENROLL_TOKEN=/{print "ENROLL_TOKEN="; next} {print}' "${agent_env_file}" > "${temporary}"
  chmod 0600 "${temporary}"
  mv "${temporary}" "${agent_env_file}"
}

build_with_candidates() {
  raw_k6="${K6_IMAGE_CANDIDATES:-$(saved_value K6_IMAGE_CANDIDATES)}"
  raw_python="${PYTHON_IMAGE_CANDIDATES:-$(saved_value PYTHON_IMAGE_CANDIDATES)}"
  raw_k6="${raw_k6:-grafana/k6:0.52.0}"
  raw_python="${raw_python:-python:3.12.5-slim-bookworm}"
  old_ifs="${IFS}"
  IFS=','
  for k6_candidate in ${raw_k6}; do
    for python_candidate in ${raw_python}; do
      IFS="${old_ifs}"
      echo "正在尝试镜像：k6=${k6_candidate}，Python=${python_candidate}"
      if K6_IMAGE="${k6_candidate}" PYTHON_IMAGE="${python_candidate}" compose build --pull; then
        IFS="${old_ifs}"
        return 0
      fi
      IFS=','
    done
  done
  IFS="${old_ifs}"
  fail "所有镜像候选均构建失败，请检查 Docker 镜像源或在 .env 中补充候选。"
}

