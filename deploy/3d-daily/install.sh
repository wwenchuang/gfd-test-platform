#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" != 0 || $# != 1 || ! "$1" =~ ^[A-Za-z0-9_.@-]{1,64}$ ]]; then
  echo "Usage: sudo bash install.sh existing-workbench-username" >&2
  exit 2
fi
owner="$1"
source_dir="$(cd "$(dirname "$0")" && pwd)"
app=/opt/midscene-task-platform
python="$app/.venv/bin/python"
base=/opt/midscene-3d-daily
systemctl is-active --quiet midscene-task.service
test "$(systemctl show -p User --value midscene-task.service)" = midscene
test "$(systemctl show -p WorkingDirectory --value midscene-task.service)" = "$app"
test -x "$python"
install -d -o root -g root -m 0755 "$base"
release="$(mktemp -d "$base/release.XXXXXXXX")"
chmod 0755 "$release"
install -m 0644 "$source_dir/run.py" "$source_dir/baselines.json" "$release/"
as_service=(runuser -u midscene -- env "PYTHONPATH=$app" MIDSCENE_ENV_FILE=/opt/midscene.env)
cd "$app"
owner_id="$("${as_service[@]}" "$python" -c 'import sys; from task_server.services.operation_attribution import actor_from_trusted_username; a=actor_from_trusted_username(sys.argv[1]); assert a["kind"]=="user"; print(a["user_id"])' "$owner")"
[[ "$owner_id" =~ ^[A-Za-z0-9_.:-]{1,128}$ ]]
"${as_service[@]}" "MIDSCENE_3D_DAILY_USERNAME=$owner" "MIDSCENE_3D_DAILY_USER_ID=$owner_id" "$python" "$release/run.py" --check
printf 'MIDSCENE_3D_DAILY_USERNAME=%s\nMIDSCENE_3D_DAILY_USER_ID=%s\n' "$owner" "$owner_id" > "$release/owner.env"
chmod 0644 "$release/owner.env"
ln -s "$(basename "$release")" "$release/current"
mv -Tf "$release/current" "$base/current"
install -m 0644 "$source_dir/midscene-3d-daily.service" "$source_dir/midscene-3d-daily.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now midscene-3d-daily.timer
systemctl is-enabled midscene-3d-daily.timer
systemctl is-active midscene-3d-daily.timer
systemctl list-timers --all midscene-3d-daily.timer
