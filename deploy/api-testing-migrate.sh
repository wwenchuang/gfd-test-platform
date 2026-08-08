#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/midscene-task-platform}"
VENV_DIR="${VENV_DIR:-${APP_DIR}/.venv}"
ALEMBIC_CONFIG="${API_TESTING_ALEMBIC_CONFIG:-${APP_DIR}/task_server/api_testing/migrations/alembic.ini}"
API_TESTING_ENABLED="${API_TESTING_ENABLED:-0}"

API_TESTING_ENABLED_NORMALIZED="$(printf '%s' "${API_TESTING_ENABLED}" | tr '[:upper:]' '[:lower:]')"
case "${API_TESTING_ENABLED_NORMALIZED}" in
  1|true|yes|on) ;;
  *)
    echo "API testing is disabled; skipping database migration."
    exit 0
    ;;
esac

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  echo "API testing migration failed: missing ${VENV_DIR}/bin/python" >&2
  exit 1
fi

if [ ! -f "${ALEMBIC_CONFIG}" ]; then
  echo "API testing migration failed: missing ${ALEMBIC_CONFIG}" >&2
  exit 1
fi

cd "${APP_DIR}"
"${VENV_DIR}/bin/python" -m alembic -c "${ALEMBIC_CONFIG}" upgrade head
