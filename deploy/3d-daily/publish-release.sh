#!/usr/bin/env bash
# Run from an extracted daily release after deploying the matching platform code.
set -euo pipefail
if [[ $EUID != 0 || $# -lt 1 || $# -gt 2 || ( $1 != --check && $1 != --apply ) ]]; then
  echo "Usage: sudo bash publish-release.sh --check|--apply [existing-publisher]" >&2
  exit 2
fi
mode="$1"
dir="$(cd "$(dirname "$0")" && pwd)"
app=/opt/midscene-task-platform
current=/opt/midscene-3d-daily/current
python="$app/.venv/bin/python"
live="$current/baselines.json"
cd "$dir"
sha256sum --quiet --check SHA256SUMS
cmp baselines.json "$app/deploy/3d-daily/baselines.json"
exec 9>>/var/lib/midscene-3d-daily/schedule.lock
flock -n 9 || { echo "3D daily schedule in use" >&2; exit 1; }
previous=''
for known in prior/*.json; do
  if cmp -s "$live" "$known"; then previous="$dir/$known"; break; fi
done
if [[ -z "$previous" ]]; then
  cmp "$live" baselines.json || { echo "Current manifest differs from this release" >&2; exit 1; }
  previous="$(find "$dir/prior" -type f -name '*.json' | sort | head -n1)"
fi
set -a
. "$current/owner.env"
set +a
as=(runuser -u midscene -- env "PYTHONPATH=$app" MIDSCENE_ENV_FILE=/opt/midscene.env)
args=(--previous "$previous" --source-root "$dir/files" --live "$live")
if [[ $# = 2 ]]; then args+=(--publisher "$2"); fi
if [[ "$mode" = --apply ]]; then args+=(--apply); fi
cd "$app"
"${as[@]}" "$python" "$dir/publish.py" "${args[@]}"
if [[ "$mode" = --apply ]]; then
  bash "$dir/install.sh" "$MIDSCENE_3D_DAILY_USERNAME"
  # Apply OnCalendar to an already running timer without starting its oneshot service.
  systemctl restart midscene-3d-daily.timer
  cmp "$dir/baselines.json" "$live"
  "${as[@]}" "$python" "$current/run.py" --check
  systemctl is-active midscene-3d-daily.timer
  TZ=Asia/Shanghai systemctl list-timers --all midscene-3d-daily.timer
fi
