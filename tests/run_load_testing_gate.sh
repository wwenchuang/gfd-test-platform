#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${API_TESTING_PYTHON:-${ROOT_DIR}/.venv/bin/python}"
POSTGRES_PASSWORD="${API_TESTING_POSTGRES_PASSWORD:-task5-test-postgres-only}"
DATABASE_URL="${TEST_DATABASE_URL:-postgresql+psycopg://midscene:${POSTGRES_PASSWORD}@127.0.0.1:5432/midscene_api_testing}"

cd "${ROOT_DIR}"
[ -x "${PYTHON_BIN}" ] || { echo "性能测试门禁失败：缺少 ${PYTHON_BIN}" >&2; exit 1; }

GATE_AUTH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/midscene-load-gate-auth.XXXXXX")"
trap 'rm -rf -- "${GATE_AUTH_DIR}"' EXIT

API_TESTING_POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" docker compose -f deploy/api-testing-compose.yml up -d --wait
API_TESTING_DATABASE_URL="${DATABASE_URL}" "${PYTHON_BIN}" -m alembic -c task_server/api_testing/migrations/alembic.ini upgrade head

TEST_DATABASE_URL="${DATABASE_URL}" \
API_TESTING_REQUIRE_POSTGRES_TESTS=1 \
TASK_AUTH_DB="${GATE_AUTH_DIR}/identity.sqlite3" \
  "${PYTHON_BIN}" -m pytest \
    tests/api_testing/test_load_testing_e2e.py \
    tests/api_testing/test_load_testing_repository.py \
    tests/api_testing/test_load_agent_service.py \
    tests/api_testing/test_load_agent_http.py \
    tests/api_testing/test_load_run_service.py \
    tests/api_testing/test_load_metric_service.py \
    tests/api_testing/test_load_report_service.py \
    tests/api_testing/test_load_ai_analysis_service.py \
    tests/load_agent -q

bash -n deploy/load-agent/*.sh deploy/install-server.sh deploy/package-server.sh
python3 tests/frontend_static_checks.py
git diff --check
