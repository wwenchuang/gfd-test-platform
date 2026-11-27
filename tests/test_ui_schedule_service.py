import hashlib
import json

import pytest

from test_identity import identity_db, create_member, activate
from test_operation_http import operation_server, request as http_request

from task_server.services import ui_schedule_service as schedules
from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_contract import AccessDenied

APP = 'com.kfb.model'
TARGET = {'platform': 'ios', 'runner_id': 'mac', 'device_id': 'phone'}
SOURCE = 'ios: {}\ntasks:\n- name: 目录\n  flow:\n  - aiTap: 目录\n'
REV = hashlib.sha256(SOURCE.encode()).hexdigest()
ACTOR = {'kind': 'user', 'username': 'tester', 'user_id': 'tester', 'display_name': '测试'}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from task_server import identity
    from task_server.services import business_line_service, case_service, runner_service
    monkeypatch.setattr(schedules.clock, 'CONFIG_DIR', tmp_path)
    monkeypatch.setenv('TASK_UI_EXECUTION_DB', str(tmp_path/'ui.sqlite'))
    rows = [dict(case_id=n, filename=n+'.yaml', module='目录', sha256=REV, name=n)
            for n in ('active', 'deferred', 'incompatible')]
    rows[1]['deferred'] = '暂缓微信'
    manifest = tmp_path / 'baselines.json'
    manifest.write_text(json.dumps(rows))
    monkeypatch.setitem(schedules.MANIFESTS, 'ios', manifest)
    app = dict(package=APP, enabled=True, platform_mappings={'ios': 'com.example.ios'})
    monkeypatch.setattr(business_line_service, 'configured_test_applications', lambda: [app])
    monkeypatch.setattr(business_line_service, 'configured_test_application', lambda _: app)
    assets = [dict(case_id=r['case_id'], file=r['filename'], module=r['module'], app_package=APP,
                   task_name=r['name'], platform='ios', status='active') for r in rows]
    assets.append(dict(assets[0], case_id='outside_manifest'))
    monkeypatch.setattr(case_service, 'list_task_case_assets', lambda: assets)
    monkeypatch.setattr(case_service, 'task_case_yaml', lambda a: SOURCE if a['case_id'] != 'incompatible' else 'tasks: []')
    profile = {'user_id': 'tester', 'status': 'active', 'permissions': ['ui.view','ui.edit','ui.execute'],
               'scope': {'ui_apps': [APP]}}
    monkeypatch.setattr(identity, 'get_access_profile', lambda _: profile)
    runner = {'online': True, 'capabilities': dict(ui_execution_contract='ui-v1', ui_attempt_events=1,
              ui_private_reports=1, ui_process_stop_proof=1, ui_platforms=['ios'],
              ui_actions=['launch','aiTap','aiWaitFor','aiAssert']),
              'devices': [dict(TARGET, status='online', preflight_status='ready')]}
    monkeypatch.setattr(runner_service, 'list_runners', lambda: {'mac': runner})
    doc = schedules.seed_schedule('ios', '22:00', TARGET, rows)
    (tmp_path / 'enabled').touch()
    request = dict(time='06:11', target=TARGET, version=doc['version'], cases=[dict(case_id='active', revision=REV)])
    with actor_context(ACTOR):
        yield tmp_path, request, profile, rows


def test_config_roundtrip_and_stale_save(setup):
    path, request, _, rows = setup
    assert [r['case_id'] for r in schedules.schedule_options('ios', TARGET)['cases']] == ['active']
    saved = schedules.save_schedule('ios', request)
    assert saved['next_run_at'].endswith('+08:00')
    assert saved['version'] != request['version']
    assert saved['cases'][1]['deferred'] == '暂缓微信'
    assert [r['case_id'] for r in saved['cases']] == ['active', 'deferred']
    disk = json.loads((path/'ios.json').read_text())
    assert disk['time'] == '06:11' and disk['target'] == TARGET
    assert schedules.seed_schedule('ios', '22:00', TARGET, rows) == disk
    with pytest.raises(schedules.ScheduleError, match='version_conflict'):
        schedules.save_schedule('ios', request)


@pytest.mark.parametrize('changes', [
    {'cases': []}, {'time': '24:00'}, {'target': dict(TARGET, runner_id='other')},
    {'target': dict(TARGET, platform='android')},
    {'cases': [dict(case_id='active', revision='1'*64)]},
    {'cases': [dict(case_id='outside_manifest', revision=REV)]},
    {'cases': [dict(case_id='incompatible', revision=REV)]},
    {'cases': [dict(case_id='deferred', revision=REV)]},
])
def test_invalid_save_is_atomic(setup, changes):
    path, request, *_ = setup
    before = (path/'ios.json').read_bytes()
    with pytest.raises(schedules.ScheduleError):
        schedules.save_schedule('ios', {**request, **changes})
    assert (path/'ios.json').read_bytes() == before


def test_app_and_write_permissions_before_file_access(setup):
    path, request, profile, _ = setup
    (path/'ios.json').unlink()
    profile['scope']['ui_apps'] = []
    with pytest.raises(AccessDenied):
        schedules.list_schedules()
    profile['scope']['ui_apps'] = [APP]
    for permission in ('ui.edit', 'ui.execute'):
        profile['permissions'].remove(permission)
        with pytest.raises(AccessDenied):
            schedules.save_schedule('ios', request)
        profile['permissions'].append(permission)
    with pytest.raises(schedules.ScheduleError, match='schedule_unavailable'):
        schedules.save_schedule('ios', request)
    assert not (path/'ios.json').exists()


def test_installer_marker_required_before_schedules_are_active(setup):
    path, request, *_ = setup
    (path/'enabled').unlink()
    with pytest.raises(schedules.ScheduleError, match='schedule_inactive'):
        schedules.list_schedules()
    with pytest.raises(schedules.ScheduleError, match='schedule_inactive'):
        schedules.schedule_options('ios', TARGET)
    with pytest.raises(schedules.ScheduleError, match='schedule_inactive'):
        schedules.save_schedule('ios', request)


def test_http_read_parallel_save_and_readback(operation_server, identity_db, setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from task_server import auth
    server, _ = operation_server
    identity, store = identity_db
    monkeypatch.setattr(identity, 'get_access_profile', store.get_access_profile)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    _, payload, _, rows = setup
    create_member(store, scope={'ui_apps': [APP]})
    activate(store)
    token = auth.create_session_token('member')
    schedules.seed_schedule('android', '05:00', dict(TARGET, platform='android'), rows[:1])
    url = '/api/ui-schedules'
    status, raw = http_request(server, 'GET', url, token)
    assert status == 200 and len(json.loads(raw)['schedules']) == 2
    status, raw = http_request(server, 'GET', url+'/ios/options?runner_id=mac&device_id=phone', token)
    assert status == 200 and [r['case_id'] for r in json.loads(raw)['cases']] == ['active']
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes = list(pool.map(lambda t: http_request(server, 'POST', url+'/ios', token, dict(payload, time=t)),
                               ('06:11', '07:00')))
    assert sorted(status for status, _ in writes) == [200, 409]
    saved = next(json.loads(raw)['schedule'] for status, raw in writes if status == 200)
    status, raw = http_request(server, 'GET', url, token)
    assert status == 200 and json.loads(raw)['schedules'][1] == saved
    from task_server.services import runner_service, sonic_service
    runner_service.list_runners()['mac']['devices'][0]['installed_apps'] = [
        dict(package='com.example.ios', installed=True, version_name='1.0', version_code=1)]
    monkeypatch.setattr(sonic_service, 'sonic_list_device_statuses', lambda **kwargs: {
        '__source__': {'available': True}, 'phone': {'usage_status': 'idle'}})
    manual = {'version': saved['version'], 'request_id': 'http-manual'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        starts = list(pool.map(lambda _: http_request(server, 'POST', url+'/ios/run', token, manual), range(2)))
    assert [code for code, _ in starts] == [200, 200], starts
    receipts = [json.loads(raw) for _, raw in starts]
    assert receipts[0] == receipts[1] and receipts[0]['ok'] and receipts[0]['status'] == 'queued'
    assert receipts[0]['run_id'].startswith('manual-')


def test_seed_uses_published_task_name_when_manifest_only_has_generated_filename(setup):
    _, _, _, rows = setup
    row = {k: v for k, v in rows[0].items() if k != 'name'}
    from task_server.services import case_service
    assets = case_service.list_task_case_assets()
    assets[0]['task_name'] = '已发布目录验收'
    doc = schedules.seed_schedule('android', '05:00', dict(TARGET, platform='android'), [row])
    assert doc['cases'][0]['task_name'] == '已发布目录验收'


def test_manual_run_pins_saved_config_and_deduplicates(setup, monkeypatch):
    path, request, profile, _ = setup
    saved = schedules.save_schedule('ios', request)
    payload = {'version': saved['version'], 'request_id': 'test-request-1'}
    monkeypatch.setattr(schedules, 'preflight_ui_batch', lambda actor, req: {'ready': True})
    first = schedules.run_schedule('ios', payload)
    assert first['status'] == 'queued'
    snapshot = json.loads((path/'manual'/'ios'/(first['run_id']+'.queued')).read_text())
    assert snapshot['target'] == TARGET and snapshot['cases'] == saved['cases']
    assert schedules.run_schedule('ios', payload) == first
    with pytest.raises(schedules.ScheduleError, match='run_conflict'):
        schedules.run_schedule('ios', dict(payload, request_id='another'))
    with pytest.raises(schedules.ScheduleError, match='version_conflict'):
        schedules.run_schedule('ios', dict(payload, version='different'))
    for permission in ('ui.execute', 'ui.edit'):
        profile['permissions'].remove(permission)
        with pytest.raises(AccessDenied):
            schedules.run_schedule('ios', payload)
        profile['permissions'].append(permission)
    profile['scope']['ui_apps'] = []
    with pytest.raises(AccessDenied):
        schedules.run_schedule('ios', payload)

def test_manual_run_rejects_busy_and_offline_without_queue(setup, monkeypatch):
    from task_server.services import case_service, runner_service, job_service
    path, request, *_ = setup
    doc = schedules.save_schedule('ios', request)
    body = dict(version=doc['version'], request_id='manual-blocked')
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    monkeypatch.setattr(case_service, 'task_case_yaml', lambda asset: SOURCE + '# changed')
    monkeypatch.setattr(job_service, '_read_jobs_raw', lambda **kw: [dict(device_id='phone', status='running')])
    with pytest.raises(schedules.ScheduleError, match='device_busy'):
        schedules.run_schedule('ios', body)
    runner_service.list_runners()['mac']['online'] = False
    with pytest.raises(schedules.ScheduleError, match='device_unavailable'):
        schedules.run_schedule('ios', body)
    assert not (path/'manual').exists()


def test_new_run_uses_new_publication_and_preserves_deferred_rows(setup, monkeypatch):
    path, request, *_ = setup
    saved = schedules.save_schedule('ios', request)
    updated = SOURCE + '# new publication'
    revision = hashlib.sha256(updated.encode()).hexdigest()
    monkeypatch.setattr(schedules.case_service, 'task_case_yaml', lambda asset: updated)
    checks = []
    monkeypatch.setattr(schedules, 'preflight_ui_batch', lambda actor, req: checks.append(req) or {'ready': True})
    result = schedules.run_schedule('ios', dict(version=saved['version'], request_id='new'))
    snapshot = json.loads((path/'manual'/'ios'/(result['run_id']+'.queued')).read_text())
    assert checks[0]['cases'] == [dict(case_id='active', revision=revision)]
    assert snapshot['cases'][0]['revision'] == revision
    assert snapshot['cases'][1] == saved['cases'][1]
    assert json.loads((path/'ios.json').read_text()) == {k:v for k,v in saved.items() if k != 'next_run_at'}
