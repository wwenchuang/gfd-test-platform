"""Permission-filtered summaries for the simple UI entry, separate from device polling."""
import base64
import hashlib
import json
import os
import threading
import time
from urllib.parse import urlencode

import yaml

from . import business_line_service, case_service, runner_service
from .ui_execution_contract import CROSS_PLATFORM_ACTIONS, require_ui_actor

_CACHE_SECONDS = 30
_cache_lock = threading.Lock()
_cache = None
_cache_at = 0.0
_EXECUTABLE = {'active', 'baseline'}


def clear_catalog_cache():
    global _cache, _cache_at
    with _cache_lock:
        _cache, _cache_at = None, 0.0


def _applications(profile):
    scope = (profile.get('scope') or {}).get('ui_apps')
    return [app for app in business_line_service.configured_test_applications()
            if app.get('enabled') and not app.get('historical_only')
            and (scope == '*' or app.get('package') in (scope or []))]


def _catalog_rows():
    global _cache, _cache_at
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache_at < _CACHE_SECONDS:
            return _cache
        rows = []
        for asset in case_service.list_task_case_assets():
            if asset.get('error') or not asset.get('case_id') or not asset.get('app_package'):
                continue
            row = {key: str(asset.get(key) or '') for key in
                   ('case_id', 'task_name', 'module', 'status', 'platform', 'business')}
            row['app_id'] = str(asset['app_package'])
            row['execution_eligible'] = row['status'] in _EXECUTABLE
            row['block_code'] = '' if row['execution_eligible'] else 'generated_case_gate_required'
            row['revision'] = ''
            source = ''
            try:
                source = case_service.task_case_yaml(asset)
                row['revision'] = hashlib.sha256(source.encode('utf-8')).hexdigest()
            except (OSError, ValueError, yaml.YAMLError):
                row['execution_eligible'], row['block_code'] = False, 'case_unavailable'
            try:
                path = case_service._safe_join_task_root(asset.get('_root') or case_service.TASK_DIR,
                                                       asset.get('module'), asset.get('file'))
                row['updated_ns'] = os.stat(path).st_mtime_ns
            except (OSError, ValueError):
                row['updated_ns'] = 0
            # Searchable in memory only; never return source, path, configuration or tokens.
            row['_search'] = '\n'.join([row['task_name'], str(asset.get('file') or ''), row['module'], str(asset.get('goal') or ''),
                                        str(asset.get('expected') or ''), source]).casefold()
            rows.append(row)
        rows.sort(key=lambda row: (-row['updated_ns'], row['case_id']))
        _cache, _cache_at = rows, time.monotonic()
        return rows


def _public(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}


def _page(rows, cursor, fingerprint, limit):
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if (not isinstance(payload, list) or len(payload) != 3 or payload[0] != fingerprint
                    or type(payload[1]) is not int or not isinstance(payload[2], str)):
                raise ValueError()
            rows = [r for r in rows if (-r['updated_ns'], r['case_id']) > (payload[1], payload[2])]
        except (ValueError, TypeError, UnicodeError) as error:
            raise ValueError('cursor_invalid') from error
    selected = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = selected[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            [fingerprint, -last['updated_ns'], last['case_id']], separators=(',', ':')).encode()).decode()
    return [_public(row) for row in selected], next_cursor


def get_ui_catalog(actor=None, *, app_id='', module='', query='', cursor='', limit=20):
    profile = require_ui_actor(actor, permission='ui.view', app_id=app_id or None)
    if (type(limit) is not int or not 1 <= limit <= 100
            or not isinstance(query, str) or len(query) > 200
            or not isinstance(module, str) or len(module) > 128
            or not isinstance(app_id, str) or len(app_id) > 128):
        raise ValueError('filters_invalid')
    apps = _applications(profile)
    if app_id and app_id not in {app['package'] for app in apps}:
        raise ValueError('app_unavailable')
    allowed = {app['package'] for app in apps}
    rows = [r for r in _catalog_rows() if r['app_id'] in allowed
            and (not app_id or r['app_id'] == app_id) and (not module or r['module'] == module)
            and (not query.strip() or query.strip().casefold() in r['_search'])]
    from .feishu_service import task_app_feishu_channel
    summaries = [{'app_id':app['package'], 'name':app.get('name') or app['package'],
                  'notification_channel': task_app_feishu_channel(app),
                  'platforms': ['android'] + (['ios'] if (app.get('platform_mappings') or {}).get('ios') else [])}
                 for app in apps]
    modules = sorted({r['module'] for r in _catalog_rows() if r['app_id'] in allowed and (not app_id or r['app_id'] == app_id)})
    if not app_id:
        if cursor:
            raise ValueError('cursor_invalid')
        return {'apps':summaries, 'modules':modules, 'groups':[
            {'app_id':a['app_id'], 'name':a['name'], 'total':len(group), 'has_more':len(group)>3,
             'items':[_public(r) for r in group[:3]]}
            for a in summaries for group in [[r for r in rows if r['app_id']==a['app_id']]] if group],
            'total':len(rows), 'items':[], 'next_cursor':None}
    fingerprint = hashlib.sha256(json.dumps([app_id,module,query],ensure_ascii=False).encode()).hexdigest()[:16]
    items, next_cursor = _page(rows,cursor,fingerprint,limit)
    return {'apps':summaries, 'modules':modules, 'groups':[], 'total':len(rows),
            'items':items, 'next_cursor':next_cursor}


def get_ui_devices(actor, app_id):
    profile = require_ui_actor(actor, permission='ui.view', app_id=app_id)
    app = next((a for a in _applications(profile) if a['package'] == app_id), None)
    if not app:
        raise ValueError('app_unavailable')
    mappings = {'android':app_id, **(app.get('platform_mappings') or {})}
    result = []
    for runner_id, runner in runner_service.list_runners().items():
        capabilities = runner.get('capabilities') or {}
        for device in runner.get('devices') or []:
            platform, device_id = device.get('platform'), device.get('device_id')
            # Older Android heartbeats omitted platform. Keep evidenced devices
            # visible, but only an explicit platform can satisfy the run contract.
            legacy_android = (platform in (None, '') and not device.get('ios_version') and any(
                isinstance(device.get(key), str) and device[key].strip()
                for key in ('android_version','androidVersion','adb_path','adbPath')))
            if legacy_android:
                platform = 'android'
            if platform not in ('android','ios') or not device_id:
                continue
            connected = runner.get('online') is True and device.get('status') in ('online','device')
            if not connected:
                state = 'offline'
            elif (legacy_android or capabilities.get('ui_execution_contract') != 'ui-v1' or capabilities.get('ui_attempt_events') != 1
                  or capabilities.get('ui_private_reports') != 1 or capabilities.get('ui_process_stop_proof') != 1
                  or platform not in (capabilities.get('ui_platforms') or [])
                  or not set(CROSS_PLATFORM_ACTIONS).issubset(set(capabilities.get('ui_actions') or []))):
                state = 'upgrade_required'
            elif not mappings.get(platform):
                state = 'mapping_missing'
            elif capabilities.get('job_execution') is False or device.get('preflight_status') != 'ready':
                state = 'not_ready'
            else:
                state = 'ready'
            installed = next((a for a in device.get('installed_apps') or []
                              if isinstance(a,dict) and a.get('package') == mappings.get(platform)), {})
            version = '未安装' if installed.get('installed') is False else ' '.join(filter(None,[
                str(installed.get('version_name') or ''),
                '('+str(installed['version_code'])+')' if installed.get('version_code') is not None else ''])) or '版本未上报'
            row = {'app_id':app_id,'target':{'runner_id':runner_id,'device_id':device_id,'platform':platform},
                   'name':device.get('label') or device.get('model') or device_id,
                   'os_version':device.get('ios_version' if platform=='ios' else 'android_version') or '',
                   'resolution':device.get('resolution') or '', 'connection_status':state,
                   'app_version':version, 'snapshot_captured_at':device.get('snapshot_captured_at') or '',
                   'snapshot_url':'', 'snapshot_hint':'', 'last_seen_age_seconds':runner.get('last_seen_age_seconds')}
            if (profile.get('scope') or {}).get('ui_apps') != '*':
                row['snapshot_hint']='手机预览包含整机画面，仅全应用权限账号可查看。'
            elif row['snapshot_captured_at']:
                row['snapshot_url']='/api/runner/device-snapshot?'+urlencode({'runner_id':runner_id,'device_id':device_id,'app_id':app_id})
            result.append(row)
    result.sort(key=lambda r:(r['connection_status']!='ready',r['name'],r['target']['device_id']))
    return {'devices':result}
