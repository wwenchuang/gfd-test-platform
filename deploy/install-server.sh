#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/midscene-task-platform}"
WEB_DIR="${WEB_DIR:-/www/html}"
ENV_FILE="${ENV_FILE:-/opt/midscene.env}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/midscene-task.service}"
USER_NAME="${USER_NAME:-midscene}"
GROUP_NAME="${GROUP_NAME:-midscene}"
PORT="${PORT:-8091}"
WEB_CONTAINER="${WEB_CONTAINER:-sonic-server-272-midscene-reports-1}"
NGINX_CLIENT_MAX_BODY_SIZE="${NGINX_CLIENT_MAX_BODY_SIZE:-300m}"
NGINX_UPLOAD_LIMIT_CONF="${NGINX_UPLOAD_LIMIT_CONF:-/etc/nginx/conf.d/midscene-upload-size.conf}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 或 sudo 执行：sudo bash deploy/install-server.sh"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少 python3，请先安装 Python 3"
  exit 1
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import yaml
PY
then
  echo "未检测到 PyYAML，正在安装 python3-yaml（用于 YAML 结构解析和可执行性检查）..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3-yaml
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3-pyyaml || yum install -y PyYAML
  elif python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install PyYAML
  else
    echo "警告：无法自动安装 PyYAML；服务仍可启动，但会使用文本兜底校验。建议手动安装 python3-yaml 或 PyYAML。"
  fi
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import pypdf
PY
then
  echo "未检测到 pypdf，正在安装（用于 Agent 解析 PDF 需求文档）..."
  pypdf_installed=0
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3-pypdf && pypdf_installed=1
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3-pypdf && pypdf_installed=1
  fi
  if [ "${pypdf_installed}" -eq 0 ] && python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install pypdf && pypdf_installed=1
  fi
  if [ "${pypdf_installed}" -eq 0 ]; then
    echo "警告：无法自动安装 pypdf；Agent 仍可运行，但 PDF 需求文档只能使用文件名/备注。建议手动安装 pypdf。"
  fi
fi

if ! getent group "${GROUP_NAME}" >/dev/null 2>&1; then
  groupadd --system "${GROUP_NAME}"
fi

if ! id "${USER_NAME}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin -g "${GROUP_NAME}" "${USER_NAME}"
fi

install -d -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${APP_DIR}"
install -d -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${APP_DIR}/deploy"
install -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/midscene-upload.py" "${APP_DIR}/midscene-upload.py"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/midscene_upload_compat.py" "${APP_DIR}/midscene_upload_compat.py"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/task-manager.html" "${APP_DIR}/task-manager.html"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/trace-viewer.html" "${APP_DIR}/trace-viewer.html"
if [ -d "${SRC_DIR}/assets" ]; then
  rm -rf "${APP_DIR}/assets"
  cp -R "${SRC_DIR}/assets" "${APP_DIR}/assets"
  find "${APP_DIR}/assets" -name "._*" -delete
  find "${APP_DIR}/assets" -name ".DS_Store" -delete
  chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/assets"
  find "${APP_DIR}/assets" -type d -exec chmod 0755 {} \;
  find "${APP_DIR}/assets" -type f -exec chmod 0644 {} \;
fi
install -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/install-server.sh" "${APP_DIR}/deploy/install-server.sh"
install -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/package-server.sh" "${APP_DIR}/deploy/package-server.sh"
install -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/sync-docker-web.sh" "${APP_DIR}/deploy/sync-docker-web.sh"
install -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/cleanup-server-packages.sh" "${APP_DIR}/deploy/cleanup-server-packages.sh"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/README.md" "${APP_DIR}/deploy/README.md"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/midscene.env.example" "${APP_DIR}/deploy/midscene.env.example"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/midscene-task.service" "${APP_DIR}/deploy/midscene-task.service"
install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SCRIPT_DIR}/nginx-midscene-task.conf" "${APP_DIR}/deploy/nginx-midscene-task.conf"
if [ -f "${SRC_DIR}/sonic-midscene-task-runner.groovy" ]; then
  install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/sonic-midscene-task-runner.groovy" "${APP_DIR}/sonic-midscene-task-runner.groovy"
  install -m 0644 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/sonic-midscene-task-runner.groovy" "/opt/sonic-midscene-task-runner.groovy"
fi
for runner_file in "windows-midscene-runner.py" "mac-midscene-runner.py" "run-mac-midscene-runner.sh"; do
  if [ -f "${SRC_DIR}/${runner_file}" ]; then
    install -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${SRC_DIR}/${runner_file}" "${APP_DIR}/${runner_file}"
  fi
done
if [ -d "${WEB_DIR}" ]; then
  install -m 0644 "${SRC_DIR}/task-manager.html" "${WEB_DIR}/task-manager.html"
  install -m 0644 "${SRC_DIR}/trace-viewer.html" "${WEB_DIR}/trace-viewer.html"
  if [ -d "${SRC_DIR}/assets" ]; then
    rm -rf "${WEB_DIR}/assets"
    cp -R "${SRC_DIR}/assets" "${WEB_DIR}/assets"
    find "${WEB_DIR}/assets" -name "._*" -delete
    find "${WEB_DIR}/assets" -name ".DS_Store" -delete
    find "${WEB_DIR}/assets" -type d -exec chmod 0755 {} \;
    find "${WEB_DIR}/assets" -type f -exec chmod 0644 {} \;
  fi
  if [ -d "${SRC_DIR}/css" ]; then
    rm -rf "${WEB_DIR}/css"
    cp -R "${SRC_DIR}/css" "${WEB_DIR}/css"
    find "${WEB_DIR}/css" -name "._*" -delete
    find "${WEB_DIR}/css" -type d -exec chmod 0755 {} \;
    find "${WEB_DIR}/css" -type f -exec chmod 0644 {} \;
  fi
  if [ -d "${SRC_DIR}/js" ]; then
    rm -rf "${WEB_DIR}/js"
    cp -R "${SRC_DIR}/js" "${WEB_DIR}/js"
    find "${WEB_DIR}/js" -name "._*" -delete
    find "${WEB_DIR}/js" -type d -exec chmod 0755 {} \;
    find "${WEB_DIR}/js" -type f -exec chmod 0644 {} \;
  fi
fi

if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "${WEB_CONTAINER}"; then
  mapfile -t existing_container_pages < <(docker exec "${WEB_CONTAINER}" sh -lc "find / -name task-manager.html 2>/dev/null" | tr -d '\r')
  container_pages=(
    "/usr/share/nginx/html/task-manager.html"
    "/usr/share/nginx/html/reports/task-manager.html"
    "/var/www/html/task-manager.html"
    "/www/html/task-manager.html"
    "${existing_container_pages[@]}"
  )
  deduped_container_pages=()
  seen_container_pages="|"
  for target_html in "${container_pages[@]}"; do
    target_html="$(printf '%s' "${target_html}" | tr -d '\r')"
    [ -n "${target_html}" ] || continue
    case "${seen_container_pages}" in
      *"|${target_html}|"*) continue ;;
    esac
    seen_container_pages="${seen_container_pages}${target_html}|"
    deduped_container_pages+=("${target_html}")
  done
  container_pages=("${deduped_container_pages[@]}")
  for target_html in "${container_pages[@]}"; do
    if [ -n "${target_html}" ]; then
      target_dir="$(dirname "${target_html}")"
      docker exec "${WEB_CONTAINER}" sh -lc "mkdir -p '${target_dir}'"
      docker exec "${WEB_CONTAINER}" sh -lc "if [ -f '${target_html}' ]; then cp '${target_html}' '${target_html}.bak.$(date +%Y%m%d-%H%M%S)'; fi"
      docker cp "${SRC_DIR}/task-manager.html" "${WEB_CONTAINER}:${target_html}"
      docker exec "${WEB_CONTAINER}" sh -lc "chmod 644 '${target_html}'"
      docker cp "${SRC_DIR}/trace-viewer.html" "${WEB_CONTAINER}:${target_dir}/trace-viewer.html"
      docker exec "${WEB_CONTAINER}" sh -lc "chmod 644 '${target_dir}/trace-viewer.html'"
      if [ -d "${SRC_DIR}/assets" ]; then
        docker exec "${WEB_CONTAINER}" sh -lc "rm -rf '${target_dir}/assets' && mkdir -p '${target_dir}'"
        docker cp "${SRC_DIR}/assets" "${WEB_CONTAINER}:${target_dir}/assets"
        docker exec "${WEB_CONTAINER}" sh -lc "find '${target_dir}/assets' -type d -exec chmod 755 {} \\; && find '${target_dir}/assets' -type f -exec chmod 644 {} \\;"
      fi
      if [ -d "${SRC_DIR}/css" ]; then
        docker exec "${WEB_CONTAINER}" sh -lc "rm -rf '${target_dir}/css' && mkdir -p '${target_dir}'"
        docker cp "${SRC_DIR}/css" "${WEB_CONTAINER}:${target_dir}/css"
        docker exec "${WEB_CONTAINER}" sh -lc "find '${target_dir}/css' -type d -exec chmod 755 {} \\; && find '${target_dir}/css' -type f -exec chmod 644 {} \\;"
      fi
      if [ -d "${SRC_DIR}/js" ]; then
        docker exec "${WEB_CONTAINER}" sh -lc "rm -rf '${target_dir}/js' && mkdir -p '${target_dir}'"
        docker cp "${SRC_DIR}/js" "${WEB_CONTAINER}:${target_dir}/js"
        docker exec "${WEB_CONTAINER}" sh -lc "find '${target_dir}/js' -type d -exec chmod 755 {} \\; && find '${target_dir}/js' -type f -exec chmod 644 {} \\;"
      fi
      echo "已同步页面到 Docker 容器：${WEB_CONTAINER}:${target_html}"
    fi
  done
  docker exec "${WEB_CONTAINER}" sh -lc "if [ -d /etc/nginx ]; then find /etc/nginx -type f \( -name '*.conf' -o -name 'nginx.conf' \) -print | while IFS= read -r f; do tmp=\"/tmp/nginx-conf.\$\$.tmp\"; sed 's/client_max_body_size[[:space:]][^;]*;/client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};/g' \"\$f\" > \"\$tmp\" && cat \"\$tmp\" > \"\$f\"; rm -f \"\$tmp\"; done; fi; mkdir -p /etc/nginx/conf.d; printf 'client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};\n' > /etc/nginx/conf.d/midscene-upload-size.conf && nginx -t" \
    && docker exec "${WEB_CONTAINER}" sh -lc "nginx -s reload 2>/dev/null || true" \
    && echo "已更新 Docker Nginx 上传上限：${NGINX_CLIENT_MAX_BODY_SIZE}" \
    || docker exec "${WEB_CONTAINER}" sh -lc "rm -f /etc/nginx/conf.d/midscene-upload-size.conf; nginx -t >/dev/null 2>&1 || true"
  docker exec "${WEB_CONTAINER}" sh -lc "nginx -s reload 2>/dev/null || true"
fi

if command -v nginx >/dev/null 2>&1 && [ -d "$(dirname "${NGINX_UPLOAD_LIMIT_CONF}")" ]; then
  if [ -d /etc/nginx ]; then
    find /etc/nginx -type f \( -name '*.conf' -o -name 'nginx.conf' \) -print | while IFS= read -r nginx_conf; do
      tmp_conf="$(mktemp)"
      sed "s/client_max_body_size[[:space:]][^;]*;/client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};/g" "${nginx_conf}" > "${tmp_conf}"
      cat "${tmp_conf}" > "${nginx_conf}"
      rm -f "${tmp_conf}"
    done
  fi
  printf 'client_max_body_size %s;\n' "${NGINX_CLIENT_MAX_BODY_SIZE}" > "${NGINX_UPLOAD_LIMIT_CONF}"
  if nginx -t; then
    systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || true
    echo "已更新宿主机 Nginx 上传上限：${NGINX_CLIENT_MAX_BODY_SIZE}"
  else
    rm -f "${NGINX_UPLOAD_LIMIT_CONF}"
    nginx -t >/dev/null 2>&1 || true
    echo "警告：宿主机 Nginx 上传上限配置校验失败，已回滚 ${NGINX_UPLOAD_LIMIT_CONF}"
  fi
fi

rm -rf "${APP_DIR}/ai_skills"
cp -R "${SRC_DIR}/ai_skills" "${APP_DIR}/ai_skills"
find "${APP_DIR}/ai_skills" -name "._*" -delete
chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/ai_skills"
find "${APP_DIR}/ai_skills" -type d -exec chmod 0755 {} \;
find "${APP_DIR}/ai_skills" -type f -exec chmod 0644 {} \;

# Deploy task_server Python package
if [ -d "${SRC_DIR}/task_server" ]; then
  rm -rf "${APP_DIR}/task_server"
  cp -R "${SRC_DIR}/task_server" "${APP_DIR}/task_server"
  find "${APP_DIR}/task_server" -name "._*" -delete
  find "${APP_DIR}/task_server" -name ".DS_Store" -delete
  find "${APP_DIR}/task_server" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/task_server"
  find "${APP_DIR}/task_server" -type d -exec chmod 0755 {} \;
  find "${APP_DIR}/task_server" -type f -exec chmod 0644 {} \;
fi

# Deploy css/ and js/ frontend assets
if [ -d "${SRC_DIR}/css" ]; then
  rm -rf "${APP_DIR}/css"
  cp -R "${SRC_DIR}/css" "${APP_DIR}/css"
  find "${APP_DIR}/css" -name "._*" -delete
  find "${APP_DIR}/css" -name ".DS_Store" -delete
  chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/css"
  find "${APP_DIR}/css" -type d -exec chmod 0755 {} \;
  find "${APP_DIR}/css" -type f -exec chmod 0644 {} \;
fi
if [ -d "${SRC_DIR}/js" ]; then
  rm -rf "${APP_DIR}/js"
  cp -R "${SRC_DIR}/js" "${APP_DIR}/js"
  find "${APP_DIR}/js" -name "._*" -delete
  find "${APP_DIR}/js" -name ".DS_Store" -delete
  chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/js"
  find "${APP_DIR}/js" -type d -exec chmod 0755 {} \;
  find "${APP_DIR}/js" -type f -exec chmod 0644 {} \;
fi

# Deploy docs/
if [ -d "${SRC_DIR}/docs" ]; then
  rm -rf "${APP_DIR}/docs"
  cp -R "${SRC_DIR}/docs" "${APP_DIR}/docs"
  find "${APP_DIR}/docs" -name "._*" -delete
  find "${APP_DIR}/docs" -name ".DS_Store" -delete
  chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/docs"
  find "${APP_DIR}/docs" -type d -exec chmod 0755 {} \;
  find "${APP_DIR}/docs" -type f -exec chmod 0644 {} \;
fi

# Deploy task baseline cases
for dir in "server-tasks" "server-tasks-all"; do
  if [ -d "${SRC_DIR}/${dir}" ]; then
    rm -rf "${APP_DIR}/${dir}"
    cp -R "${SRC_DIR}/${dir}" "${APP_DIR}/${dir}"
    find "${APP_DIR}/${dir}" -name "._*" -delete
    find "${APP_DIR}/${dir}" -name ".DS_Store" -delete
    chown -R "${USER_NAME}:${GROUP_NAME}" "${APP_DIR}/${dir}" 2>/dev/null || true
    find "${APP_DIR}/${dir}" -type d -exec chmod 0755 {} \;
    find "${APP_DIR}/${dir}" -type f -exec chmod 0644 {} \;
    echo "已部署任务用例到 ${APP_DIR}/${dir}"
  fi
done

for dir in \
  /opt/midscene-tasks \
  /opt/midscene-reports \
  /opt/midscene-learning \
  /opt/midscene-assets \
  /opt/midscene-cases \
  /opt/midscene-generate-jobs \
  /opt/midscene-knowledge
do
  install -d -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "$dir"
done


# Deploy AI Gateway used by /ai-gateway/ reverse proxy. Keep .env and existing router configs.
AI_GATEWAY_DIR="${AI_GATEWAY_DIR:-/opt/ai-gateway}"
if [ -d "${SRC_DIR}/ai-gateway" ]; then
  echo "同步 AI Gateway 到 ${AI_GATEWAY_DIR} ..."
  install -d -m 0755 "${AI_GATEWAY_DIR}"
  for item in server.js package.json package-lock.json README.md prompts validators agent agent-assets; do
    if [ -e "${SRC_DIR}/ai-gateway/${item}" ]; then
      rm -rf "${AI_GATEWAY_DIR:?}/${item}"
      cp -R "${SRC_DIR}/ai-gateway/${item}" "${AI_GATEWAY_DIR}/${item}"
    fi
  done
  install -d -m 0755 "${AI_GATEWAY_DIR}/config"
  for cfg in providers.json model-router.json agent-whitelist.json; do
    if [ -f "${SRC_DIR}/ai-gateway/config/${cfg}" ] && [ ! -f "${AI_GATEWAY_DIR}/config/${cfg}" ]; then
      install -m 0644 "${SRC_DIR}/ai-gateway/config/${cfg}" "${AI_GATEWAY_DIR}/config/${cfg}"
    fi
  done
  find "${AI_GATEWAY_DIR}" -name "._*" -delete
  find "${AI_GATEWAY_DIR}" -name ".DS_Store" -delete
  if command -v npm >/dev/null 2>&1 && [ -f "${AI_GATEWAY_DIR}/package.json" ]; then
    (cd "${AI_GATEWAY_DIR}" && npm install --omit=dev)
  else
    echo "警告：未检测到 npm，跳过 AI Gateway 依赖安装"
  fi
  if command -v pm2 >/dev/null 2>&1; then
    if pm2 describe ai-gateway >/dev/null 2>&1; then
      pm2 restart ai-gateway --update-env || true
    else
      (cd "${AI_GATEWAY_DIR}" && pm2 start server.js --name ai-gateway --update-env) || true
    fi
    pm2 save || true
  else
    echo "提示：未检测到 pm2，请手动启动 AI Gateway：cd ${AI_GATEWAY_DIR} && node server.js"
  fi
fi

if [ ! -f "${ENV_FILE}" ]; then
  install -m 0600 -o root -g root "${SCRIPT_DIR}/midscene.env.example" "${ENV_FILE}"
  sed -i.bak "s/^export PORT=.*/export PORT='${PORT}'/" "${ENV_FILE}" || true
  rm -f "${ENV_FILE}.bak"
  echo "已创建 ${ENV_FILE}，请先填写 DASHSCOPE_API_KEY、Sonic 等配置后再启动服务"
else
  chmod 0600 "${ENV_FILE}"
fi

ensure_env_default() {
  local key="$1"
  local value="$2"
  if ! grep -q "^export ${key}=" "${ENV_FILE}"; then
    printf "export %s='%s'\n" "${key}" "${value}" >> "${ENV_FILE}"
  fi
}

upgrade_env_default_if_old() {
  local key="$1"
  local value="$2"
  local old_values_regex="$3"
  if grep -Eq "^export ${key}='?(${old_values_regex})'?$" "${ENV_FILE}"; then
    sed -i.bak "s|^export ${key}=.*|export ${key}='${value}'|" "${ENV_FILE}" || true
    rm -f "${ENV_FILE}.bak"
  fi
}

ensure_env_default "AI_SKILLS_DIR" "${APP_DIR}/ai_skills"
upgrade_env_default_if_old "PORT" "8091" "8088"
ensure_env_default "TASK_MAX_BODY_SIZE" "314572800"
ensure_env_default "TASK_MAX_UPLOAD_BODY_SIZE" "314572800"
ensure_env_default "FIGMA_PARSE_LIMIT" "80"
ensure_env_default "FIGMA_REFERENCE_LIMIT" "36"
ensure_env_default "FIGMA_MAX_REFERENCE_LIMIT" "72"
ensure_env_default "FIGMA_VISUAL_IMAGE_LIMIT" "40"
ensure_env_default "FIGMA_IMAGE_WORKERS" "4"
ensure_env_default "FIGMA_MIN_RELEVANCE_SCORE" "5"
ensure_env_default "FIGMA_AUTO_SAVE_MIN_RELEVANCE" "5"
ensure_env_default "MIDSCENE_AI_VISION_IMAGE_LIMIT" "40"
ensure_env_default "MIDSCENE_MINDMAP_VISUAL_BATCH_SIZE" "1"
ensure_env_default "MIDSCENE_MINDMAP_VISUAL_TIMEOUT_SECONDS" "90"
ensure_env_default "MIDSCENE_MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS" "360"
ensure_env_default "MIDSCENE_AI_CHAT_TIMEOUT_SECONDS" "480"
ensure_env_default "MIDSCENE_AI_CHAT_RETRY_COUNT" "1"
ensure_env_default "MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS" "150"
ensure_env_default "MIDSCENE_COVERAGE_MODEL_WHEN_LOCAL_OK" "0"
ensure_env_default "MIDSCENE_YAML_BASELINE_CACHE_TTL_SECONDS" "600"
ensure_env_default "MIDSCENE_YAML_BASELINE_CACHE_MAX_FILES" "1200"
ensure_env_default "MIDSCENE_YAML_BASELINE_CACHE_PATH" "/opt/midscene-tasks/cache/yaml-baseline-cache.json"
upgrade_env_default_if_old "MIDSCENE_YAML_BASELINE_CACHE_PATH" "/opt/midscene-tasks/cache/yaml-baseline-cache.json" "/opt/midscene-learning/cache/yaml-baseline-cache.json"
ensure_env_default "MIDSCENE_REPORT_RETENTION_DAYS" "14"
ensure_env_default "MIDSCENE_REPORT_RETENTION_MIN_KEEP" "200"
ensure_env_default "MIDSCENE_REPORT_CLEANUP_INTERVAL_SECONDS" "86400"
ensure_env_default "MIDSCENE_REPORT_CLEANUP_ON_STARTUP" "1"
ensure_env_default "MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_SECONDS" "1800"
ensure_env_default "MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_PER_JOB_SECONDS" "900"
ensure_env_default "MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_MAX_SECONDS" "7200"
ensure_env_default "MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS" "900"
ensure_env_default "MIDSCENE_AGENT_GENERATED_RUNNER_SMOKE_LIMIT" "3"
ensure_env_default "MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT" "3"
ensure_env_default "MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_LIMIT" "5"
ensure_env_default "MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT" "5"
upgrade_env_default_if_old "MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS" "900" "7200|3600|1800"
upgrade_env_default_if_old "MIDSCENE_AGENT_GENERATED_RUNNER_SMOKE_LIMIT" "3" "8"
upgrade_env_default_if_old "MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_LIMIT" "5" "30"
upgrade_env_default_if_old "MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT" "5" "16"
upgrade_env_default_if_old "MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS" "150" "90"
ensure_env_default "SONIC_TASK_CALLBACK_GRACE_SECONDS" "180"

upgrade_env_default_if_old "FIGMA_PARSE_LIMIT" "80" "20|40|60"
upgrade_env_default_if_old "FIGMA_REFERENCE_LIMIT" "36" "12|24"
upgrade_env_default_if_old "FIGMA_MAX_REFERENCE_LIMIT" "72" "24|32|48"
upgrade_env_default_if_old "FIGMA_VISUAL_IMAGE_LIMIT" "40" "16|24"
upgrade_env_default_if_old "MIDSCENE_AI_VISION_IMAGE_LIMIT" "40" "16|24"
upgrade_env_default_if_old "MIDSCENE_MINDMAP_VISUAL_BATCH_SIZE" "1" "2|4|8"
upgrade_env_default_if_old "MIDSCENE_MINDMAP_VISUAL_TIMEOUT_SECONDS" "90" "120"
upgrade_env_default_if_old "MIDSCENE_MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS" "360" "300"
upgrade_env_default_if_old "TASK_MAX_BODY_SIZE" "314572800" "20971520|52428800|83886080|125829120"
upgrade_env_default_if_old "TASK_MAX_UPLOAD_BODY_SIZE" "314572800" "83886080|125829120"
ensure_env_default "MIDSCENE_YAML_VISUAL_BATCH_SIZE" "4"
ensure_env_default "MIDSCENE_YAML_VISUAL_TIMEOUT_SECONDS" "900"
ensure_env_default "MIDSCENE_YAML_VISUAL_TOTAL_BUDGET_SECONDS" "3600"
upgrade_env_default_if_old "MIDSCENE_YAML_VISUAL_BATCH_SIZE" "4" "8"
upgrade_env_default_if_old "MIDSCENE_YAML_VISUAL_TIMEOUT_SECONDS" "900" "600"
upgrade_env_default_if_old "MIDSCENE_YAML_VISUAL_TOTAL_BUDGET_SECONDS" "3600" "5400"

install -m 0644 "${SCRIPT_DIR}/midscene-task.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable midscene-task.service

echo "部署文件已安装到 ${APP_DIR}"
echo "配置文件：${ENV_FILE}"
echo "启动：sudo systemctl restart midscene-task"
echo "检查：curl http://127.0.0.1:${PORT}/api/health"
