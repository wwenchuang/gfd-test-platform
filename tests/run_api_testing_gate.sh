#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${API_TESTING_PYTHON:-${ROOT_DIR}/.venv/bin/python}"
POSTGRES_PASSWORD="${API_TESTING_POSTGRES_PASSWORD:-task5-test-postgres-only}"
DATABASE_URL="${TEST_DATABASE_URL:-postgresql+psycopg://midscene:${POSTGRES_PASSWORD}@127.0.0.1:5432/midscene_api_testing}"
PYTEST_REDIS_URL="${API_TESTING_PYTEST_REDIS_URL:-redis://127.0.0.1:6379/14}"
E2E_REDIS_URL="${API_TESTING_E2E_REDIS_URL:-redis://127.0.0.1:6379/15}"

cd "${ROOT_DIR}"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "API testing gate failed: missing ${PYTHON_BIN}" >&2
  exit 1
fi

# Service tests must never open the host's production identity database. HTTP
# identity/E2E tests bootstrap their own accounts and access profiles separately.
GATE_AUTH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/midscene-api-gate-auth.XXXXXX")"
trap 'rm -rf -- "${GATE_AUTH_DIR}"' EXIT

API_TESTING_POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  docker compose -f deploy/api-testing-compose.yml up -d --wait

API_TESTING_DATABASE_URL="${DATABASE_URL}" \
  "${PYTHON_BIN}" -m alembic \
    -c task_server/api_testing/migrations/alembic.ini upgrade head

TEST_DATABASE_URL="${DATABASE_URL}" \
TEST_REDIS_URL="${PYTEST_REDIS_URL}" \
API_TESTING_REQUIRE_POSTGRES_TESTS=1 \
TASK_AUTH_DB="${GATE_AUTH_DIR}/identity.sqlite3" \
  "${PYTHON_BIN}" -m pytest tests/api_testing -q

npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js

TEST_DATABASE_URL="${DATABASE_URL}" \
TEST_REDIS_URL="${E2E_REDIS_URL}" \
  npx playwright test tests/api_testing_e2e.spec.mjs --project=chromium
