"""Collect closed hourly model_usage buckets; timestamp is the window end."""
import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
FIELDS = ('input_tokens', 'output_tokens', 'total_tokens')


def snapshot(workspace, now):
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end.replace(hour=0) - timedelta(days=6)
    args = ['bl', 'monitor', 'metrics', '--metric', 'model_usage', '--agg', 'sum',
            '--step', '3600', '--start-time', start.isoformat(), '--end-time', end.isoformat(),
            '--workspace-id', workspace, '--console-region', 'cn-beijing', '--output', 'json']
    reply = json.loads(subprocess.check_output(args, timeout=60))
    days = {(start + timedelta(days=n)).date().isoformat(): {} for n in range(7)}
    for series in reply['series']:
        labels = series['labels']
        key = labels.get('usage_type')
        if key not in FIELDS or labels.get('unit') != 'tokens':
            continue
        for point in series['points']:
            stop = datetime.fromtimestamp(point['timestamp'] / 1000, ZONE)
            if start < stop <= end:
                value = point['value']
                if type(value) not in (int, float) or value < 0 or int(value) != value:
                    raise ValueError('invalid token count')
                row = days[(stop - timedelta(seconds=1)).date().isoformat()]
                row[key] = row.get(key, 0) + int(value)
    return {'updated': now.isoformat(timespec='minutes'), 'through': end.isoformat(timespec='minutes'),
            'days': [dict(date=date, **counts) for date, counts in reversed(days.items())]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    data = snapshot(options.workspace, datetime.now(ZONE))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=options.output.parent, delete=False) as handle:
        json.dump(data, handle)
    os.chmod(handle.name, 0o644)
    os.replace(handle.name, options.output)
