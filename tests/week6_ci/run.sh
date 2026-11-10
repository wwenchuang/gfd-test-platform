#!/usr/bin/env bash
# Run from a clean checkout. Only the evidence directory survives the run.
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
out=${1:?Usage: run.sh NEW_EVIDENCE_DIRECTORY [pr|nightly|wrong-title]}
profile=${2:-pr}
case "$profile" in pr|nightly|wrong-title) ;; *) exit 2 ;; esac
mkdir -p "$(dirname "$out")"
mkdir "$out"
out=$(cd "$out" && pwd)
scratch=$(mktemp -d "${TMPDIR:-/tmp}/platform-smoke.XXXXXXXX")
stage=checkout
finish() {
    rc=$?
    rm -rf "$scratch"
    printf 'stage=%s\nexit=%s\n' "$stage" "$rc" > "$out/status.txt"
}
trap finish EXIT
exec > >(tee "$out/pipeline.log") 2>&1
git diff --quiet HEAD --
revision=$(git rev-parse HEAD)
printf 'revision=%s\nprofile=%s\nstarted=%s\n' "$revision" "$profile" "$(date -u +%FT%TZ)" | tee "$out/run.txt"
python_bin=${W06_PYTHON:-python3}
"$python_bin" --version
node --version
npm --version
uname -sm
stage=dependencies
"$python_bin" -m venv "$scratch/venv"
python="$scratch/venv/bin/python"
"$python" -m pip --isolated install --disable-pip-version-check --no-cache-dir --only-binary=:all: --no-deps -r tests/week6_ci/requirements.lock
"$python" -m pip check
cp package.json package-lock.json tests/week1_smoke/ui_smoke.js "$scratch/"
(cd "$scratch" && npm ci --include=dev --ignore-scripts --audit=false --fund=false --cache "$scratch/npm-cache")
stage=browser
export PLAYWRIGHT_BROWSERS_PATH="$scratch/browsers"
pw="$scratch/node_modules/playwright/cli.js"
node "$pw" --version
node "$pw" install --only-shell chromium
mkdir "$scratch/state"
run_clean() {
    env -i PATH="$PATH" HOME="$scratch/state" TMPDIR="$scratch/state" \
      PYTHONPATH="$PWD" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
      PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
      MIDSCENE_ENV_FILE="$scratch/state/absent.env" TASK_RELEASE_REVISION="$revision" \
      TASK_AUTH_DB="$scratch/state/auth.sqlite3" TASK_OPERATION_DB="$scratch/state/operations.sqlite3" \
      TASK_DIR="$scratch/state/tasks" REPORT_DIR="$scratch/state/reports" LEARNING_DIR="$scratch/state/learning" \
      ASSET_DIR="$scratch/state/assets" CASE_DIR="$scratch/state/cases" KNOWLEDGE_DIR="$scratch/state/knowledge" \
      GENERATE_JOB_DIR="$scratch/state/jobs" \
      "$@"
}
if [[ "$profile" == nightly ]]; then
    stage=static-checks
    run_clean "$python" tests/backend_static_checks.py
    run_clean "$python" tests/frontend_static_checks.py
fi
stage=smoke
run_clean "$python" -u tests/week6_ci/smoke.py "$scratch/ui_smoke.js" "$profile" "$out"
stage=complete
