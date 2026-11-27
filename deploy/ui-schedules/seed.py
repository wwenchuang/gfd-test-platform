"""One-time adoption of the two deployed manifests. Never overwrite saved edits."""
import json
from pathlib import Path

from task_server.services.ui_schedule_service import seed_schedule

JOBS = (
    ('android', '05:00', 'mac-android-adouceshi', 'ecbfd645', '/opt/midscene-3d-daily/current'),
    ('ios', '22:00', 'mac-ios-adouceshi', '00008110-000150CC21C2801E', '/opt/midscene-3d-ios-nightly/current'),
)


def main():
    for platform, time_string, runner, device, directory in JOBS:
        rows = json.loads((Path(directory) / 'baselines.json').read_text(encoding='utf-8'))
        target = dict(platform=platform, runner_id=runner, device_id=device)
        seed_schedule(platform, time_string, target, rows)


if __name__ == '__main__':
    main()
