"""Resolve a device picture against current UI permissions and capture provenance."""

import os
import re

from . import business_line_service, runner_service
from .ui_execution_contract import AccessDenied, require_ui_actor


def resolve_ui_snapshot(runner_id, device_id, app_id=''):
    profile = require_ui_actor(permission='ui.view')
    runner = runner_service.load_runners().get(runner_id) or {}
    device = next((row for row in runner.get('devices') or []
                   if str(row.get('device_id')) == device_id), None)
    if not device:
        raise FileNotFoundError('device_not_found')

    scope = (profile.get('scope') or {}).get('ui_apps')
    if scope != '*':
        if not app_id:
            raise AccessDenied('app_required')
        require_ui_actor(permission='ui.view', app_id=app_id)
        app = business_line_service.configured_test_application(app_id)
        if not app or not app.get('enabled') or app.get('historical_only'):
            raise AccessDenied('app_forbidden')
        platform = device.get('platform')
        mapping = app.get('platform_mappings') or {}
        expected = app_id if platform == 'android' else mapping.get(platform)
        if not expected or device.get('snapshot_app_package') != expected:
            raise AccessDenied('snapshot_app_forbidden')

    filename = device.get('snapshot_file')
    if filename is None:
        # Historical captures have no reliable app provenance. They are only
        # visible to unrestricted users, as enforced above.
        return runner_service.device_snapshot_path(runner_id, device_id)
    if not isinstance(filename, str) or not re.fullmatch(r'[0-9a-f]{32}\.png', filename):
        raise FileNotFoundError('snapshot_invalid')
    return os.path.join(runner_service.DEVICE_SNAPSHOT_DIR, filename)
