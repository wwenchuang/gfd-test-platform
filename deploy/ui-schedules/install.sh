#!/usr/bin/env bash
# Adopt both existing jobs in place. This installer does not run a mobile test.
set -euo pipefail
app=/opt/midscene-task-platform
python="$app/.venv/bin/python"
state=/var/lib/midscene-ui-schedules
units=(midscene-3d-daily midscene-3d-ios-nightly)
platforms=(android ios)
[[ "$(id -u)" == 0 ]]
for unit in "${units[@]}"; do
  systemctl is-active --quiet "$unit.timer"
  case "$(systemctl show "$unit.service" -p ActiveState --value)" in
    inactive|failed) ;;
    *) echo "回归正在执行，保留当前调度：$unit" >&2; exit 1 ;;
  esac
done
for f in "$app/deploy/3d-daily/run.py" "$app/deploy/3d-ios-nightly/nightly.py"; do
  grep -q schedule_snapshot "$f"
done
install -d -m 0700 -o midscene -g midscene "$state" "$state/claims"
as_runner=(runuser -u midscene -- env "PYTHONPATH=$app" MIDSCENE_ENV_FILE=/opt/midscene.env)
cd "$app"
"${as_runner[@]}" "$python" "$app/deploy/ui-schedules/seed.py"
for platform in "${platforms[@]}"; do
  "${as_runner[@]}" "$python" "$app/deploy/ui-schedules/dispatch.py" "$platform" --inspect >/dev/null
done
# Preserve earlier overrides if verification fails after the switch.
backup=$(mktemp -d)
files=()
restore() {
  for i in "${!files[@]}"; do
    if [[ -f "$backup/$i" ]]; then cp "$backup/$i" "${files[i]}"; else rm -f "${files[i]}"; fi
  done
  systemctl daemon-reload
  systemctl restart "${units[0]}.timer" "${units[1]}.timer"
}
trap 'restore' ERR
trap 'rm -rf "$backup"' EXIT
for i in "${!units[@]}"; do
  unit="${units[i]}"
  for kind in service timer; do
    dir="/etc/systemd/system/$unit.$kind.d"
    install -d "$dir"
    f="$dir/90-ui-workbench.conf"
    [[ ! -f "$f" ]] || cp "$f" "$backup/${#files[@]}"
    files+=("$f")
    if [[ "$kind" == service ]]; then
      printf '[Service]\nExecStart=\nExecStart=%s %s/deploy/ui-schedules/dispatch.py %s\n' "$python" "$app" "${platforms[i]}" > "$f"
    else
      printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* *:*:00 Asia/Shanghai\nAccuracySec=1s\n' > "$f"
    fi
  done
done
systemctl daemon-reload
systemctl restart "${units[0]}.timer" "${units[1]}.timer"
for i in "${!units[@]}"; do
  unit="${units[i]}"
  systemctl is-active --quiet "$unit.timer"
  systemctl show "$unit.service" -p ExecStart --value | grep -Fq "dispatch.py ${platforms[i]}"
  systemctl show "$unit.timer" -p TimersCalendar --value | grep -Fq '*:*:00'
done
install -m 0444 -o root -g root /dev/null "$state/enabled"
trap - ERR
echo '两条每日 UI 任务已接入工作台'
