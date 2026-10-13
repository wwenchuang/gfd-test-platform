"""Open the latest full-phone preview for a current full-app-scope viewer."""

import os
import re

from . import runner_service
from .ui_execution_contract import AccessDenied, require_ui_actor


def open_ui_snapshot(runner_id, device_id):
    profile = require_ui_actor(permission='ui.view')
    if (profile.get('scope') or {}).get('ui_apps') != '*':
        raise AccessDenied('full_phone_preview_forbidden')
    with runner_service.RUNNER_LOCK:
        runner = runner_service.load_runners().get(runner_id) or {}
        device = next((row for row in runner.get('devices') or []
                       if str(row.get('device_id')) == device_id), None)
        if not device:
            raise FileNotFoundError('device_not_found')
        filename = device.get('snapshot_file')
        if filename is None:
            path = runner_service.device_snapshot_path(runner_id, device_id)
        elif isinstance(filename, str) and re.fullmatch(r'[0-9a-f]{32}\.png', filename):
            path = os.path.join(runner_service.DEVICE_SNAPSHOT_DIR, filename)
        else:
            raise FileNotFoundError('snapshot_invalid')
        # Pruning uses the same lock. Only the open descriptor escapes it.
        return open(path, 'rb')
