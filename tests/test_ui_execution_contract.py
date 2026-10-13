import copy

import pytest

from task_server import identity
from task_server.services import business_line_service as catalog, job_service, runner_service
from task_server.services.operation_attribution import actor_context


ACTOR = {'kind': 'user', 'username': 'tester', 'user_id': 'u1', 'display_name': '测试员'}
APP = {'package': 'com.kfb.model', 'name': '智小白3D', 'enabled': True,
       'platform_mappings': {'android': 'com.kfb.model', 'ios': 'com.kfb.3d'}}
CAPABILITIES = {'ui_execution_contract': 'ui-v1', 'ui_platforms': ['ios'],
                'ui_actions': ['launch', 'aiTap', 'aiWaitFor', 'aiAssert'], 'job_execution': True}
TARGET = {'runner_id': 'mac', 'device_id': 'iphone', 'platform': 'ios'}


@pytest.fixture
def registry(monkeypatch):
    app = copy.deepcopy(APP)
    profile = {'username': 'tester', 'user_id': 'u1', 'status': 'active', 'permissions': ['ui.execute'],
               'scope': {'ui_apps': ['com.kfb.model']}, 'must_change_password': False}
    runners = {'mac': {'online': True, 'capabilities': copy.deepcopy(CAPABILITIES), 'devices': [
        {'device_id': 'iphone', 'platform': 'ios', 'status': 'online', 'preflight_status': 'ready'}]}}
    monkeypatch.setattr(catalog, 'configured_test_application', lambda *args, **kwargs: app)
    monkeypatch.setattr(identity, 'get_access_profile', lambda username: profile if username == 'tester' else None)
    monkeypatch.setattr(runner_service, 'list_runners', lambda **kwargs: runners)
    return app, profile, runners


def resolve(target=None, actor=None):
    from task_server.services.ui_execution_contract import resolve_ui_target
    with actor_context(ACTOR):
        return resolve_ui_target('com.kfb.model', target or TARGET, actor)


def test_exact_authorized_target_uses_platform_mapping(registry):
    registry[2]['mac']['devices'][0].update(model='iPhone14,5', ios_version='18.0', adb_path='/secret/tool',
                                            installed_apps=[{'package': 'com.kfb.3d', 'version_name': '1.0',
                                                             'version_code': '7', 'error': 'private details'}])
    result = resolve()
    assert result['ok'] and result['target_package'] == 'com.kfb.3d'
    assert result['app_id'] == 'com.kfb.model'
    assert result['device_id'] == 'iphone' and result['runner_id'] == 'mac'
    assert 'workspace' not in result and 'wdaPort' not in str(result)
    assert result['device_snapshot']['model'] == 'iPhone14,5'
    assert result['device_snapshot']['ios_version'] == '18.0'
    assert result['device_snapshot']['app_versions'] == [{'package': 'com.kfb.3d', 'version_name': '1.0',
                                                           'version_code': '7'}]
    assert 'adb_path' not in str(result) and 'private details' not in str(result)


@pytest.mark.parametrize('mutation,code', [
    ('mapping', 'app_mapping_missing'), ('old', 'runner_upgrade_required'),
    ('actions', 'runner_capability_missing'), ('device', 'device_not_found'),
    ('platform', 'platform_mismatch'), ('offline', 'runner_offline'),
    ('blocked', 'device_not_ready'), ('scope', 'app_forbidden'),
    ('permission', 'execution_forbidden'), ('disabled', 'account_unavailable'),
    ('password', 'account_unavailable'), ('preview', 'runner_execution_disabled'),
])
def test_blockers_never_choose_another_phone(registry, mutation, code):
    app, profile, runners = registry
    if mutation == 'mapping': app['platform_mappings'].pop('ios')
    if mutation == 'old': runners['mac']['capabilities'].pop('ui_execution_contract')
    if mutation == 'actions': runners['mac']['capabilities']['ui_actions'] = ['launch']
    if mutation == 'device': runners['mac']['devices'][0]['device_id'] = 'other'
    if mutation == 'platform': runners['mac']['devices'][0]['platform'] = 'android'
    if mutation == 'offline': runners['mac']['online'] = False
    if mutation == 'blocked': runners['mac']['devices'][0]['preflight_status'] = 'blocked'
    if mutation == 'scope': profile['scope']['ui_apps'] = []
    if mutation == 'permission': profile['permissions'] = ['ui.read']
    if mutation == 'disabled': profile['status'] = 'disabled'
    if mutation == 'password': profile['must_change_password'] = True
    if mutation == 'preview': runners['mac']['capabilities']['job_execution'] = False
    result = resolve()
    assert result['ok'] is False and result['block_code'] == code
    assert not result.get('runner_id') and not result.get('device_id')


def test_forged_actor_cannot_replace_trusted_context(registry):
    assert resolve(actor={**ACTOR, 'username': 'admin'})['block_code'] == 'actor_untrusted'
    from task_server.services.ui_execution_contract import resolve_ui_target
    assert resolve_ui_target('com.kfb.model', TARGET, ACTOR)['block_code'] == 'actor_untrusted'


def test_public_actor_guard_uses_fresh_profile_and_app_scope(registry):
    from task_server.services.ui_execution_contract import AccessDenied, require_ui_actor
    from task_server.services.operation_attribution import current_actor
    with actor_context(ACTOR):
        assert require_ui_actor(app_id='com.kfb.model')['user_id'] == 'u1'
        assert require_ui_actor(current_actor(), app_id='com.kfb.model')['user_id'] == 'u1'
        with pytest.raises(AccessDenied) as denied:
            require_ui_actor(app_id='com.other.app')
        assert denied.value.code == 'app_forbidden'
        with pytest.raises(AccessDenied) as denied:
            require_ui_actor({**ACTOR, 'username': 'admin'})
        assert denied.value.code == 'actor_untrusted'
        with pytest.raises(AccessDenied) as denied:
            require_ui_actor({**ACTOR, 'display_name': '伪造姓名'})
        assert denied.value.code == 'actor_untrusted'


def test_target_requires_all_explicit_fields(registry):
    for key in TARGET:
        target = {k: v for k, v in TARGET.items() if k != key}
        assert resolve(target)['block_code'] == 'target_required'
    assert resolve({**TARGET, 'platform': 'android'})['block_code'] == 'platform_mismatch'


def test_legacy_android_catalog_keeps_package_and_permission_key():
    result = catalog.normalize_test_application({'package': 'com.kfb.model', 'name': '智小白3D'})
    assert result['package'] == 'com.kfb.model'
    assert 'platform_mappings' not in result


def test_mapping_is_persisted_and_preserved_on_rename():
    original = job_service.normalize_task_app(APP)
    assert original['platform_mappings'] == APP['platform_mappings']
    renamed = job_service.normalize_task_app({'package': 'com.kfb.model', 'name': '新中文名称'}, existing_app=original)
    assert renamed['platform_mappings'] == APP['platform_mappings']


@pytest.mark.parametrize('mapping', [{'ios': ''}, {'web': 'x'}, {'android': 'different'}, {'ios': 'bad bundle'}, ['ios']])
def test_invalid_mapping_is_not_saved(mapping):
    with pytest.raises(ValueError):
        catalog.normalize_test_application({**APP, 'platform_mappings': mapping})


def test_new_contract_dispatch_cannot_use_query_only_phone():
    info = {'capabilities': CAPABILITIES, 'devices': []}
    job = {'ui_execution_contract': 'ui-v1', 'target_runner_id': 'mac', 'device_id': 'iphone', 'platform': 'ios'}
    assert not runner_service.runner_accepts_explicit_job(info, job, 'mac')


def test_contract_dispatch_checks_registered_platform_and_capability():
    info = {'capabilities': copy.deepcopy(CAPABILITIES), 'devices': [
        {'device_id': 'iphone', 'platform': 'ios', 'status': 'online', 'preflight_status': 'ready'}]}
    job = {'ui_execution_contract': 'ui-v1', 'target_runner_id': 'mac', 'device_id': 'iphone', 'platform': 'ios',
           'ui_required_actions': ['aiTap']}
    assert runner_service.runner_accepts_explicit_job(info, job, 'mac')
    assert not runner_service.runner_accepts_explicit_job(info, {**job, 'platform': 'android'}, 'mac')
    assert not runner_service.runner_accepts_explicit_job(info, {**job, 'target_runner_id': 'other'}, 'mac')
    info['capabilities']['ui_actions'] = ['launch']
    assert not runner_service.runner_accepts_explicit_job(info, job, 'mac')


def test_android_baseline_dispatch_keeps_existing_actions():
    info = {'capabilities': {**CAPABILITIES, 'ui_platforms': ['android'], 'android_baseline': True,
                             'ui_actions': ['launch', 'aiTap', 'aiWaitFor', 'aiAssert', 'runAdbShell']},
            'devices': [{'device_id': 'android-phone', 'platform': 'android', 'status': 'online', 'preflight_status': 'ready'}]}
    job = {'ui_execution_contract': 'ui-v1', 'target_runner_id': 'win', 'device_id': 'android-phone',
           'platform': 'android', 'ui_required_actions': ['launch', 'runAdbShell']}
    assert runner_service.runner_accepts_explicit_job(info, job, 'win')
    assert not runner_service.runner_accepts_explicit_job(info, {**job, 'ui_required_actions': ['launch', 'adbShell']}, 'win')
    assert not runner_service.runner_accepts_explicit_job(info, {**job, 'ui_required_actions': ['launch', 'aiScroll']}, 'win')
    assert not runner_service.runner_accepts_explicit_job(info, {k: v for k, v in job.items() if k != 'ui_required_actions'}, 'win')
    assert not runner_service.runner_accepts_explicit_job(info, {**job, 'ui_required_actions': []}, 'win')
    info['capabilities']['ui_actions'].append('adbShell')
    assert not runner_service.runner_accepts_explicit_job(info, {**job, 'ui_required_actions': ['adbShell']}, 'win')


def test_windows_actual_device_heartbeat_resolves_and_dispatches(monkeypatch, registry):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('windows_ui_runner', Path(__file__).resolve().parents[1] / 'windows-midscene-runner.py')
    windows = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(windows)
    monkeypatch.setattr(windows, 'detect_device_ids', lambda: ['android-phone'])
    monkeypatch.setattr(windows, 'resolve_adb_with_devices', lambda **kwargs: ('adb', ['android-phone']))
    monkeypatch.setattr(windows, 'adb_shell_text', lambda *args, **kwargs: '')
    monkeypatch.setattr(windows, 'detect_package_info', lambda *args: {'package': args[-1], 'installed': True})
    monkeypatch.setattr(windows, 'device_market_name', lambda *args: 'Android Phone')
    monkeypatch.setattr(windows, '_DEVICE_DETAILS_CACHE', {})
    devices = windows.detect_devices()
    captured = {}
    monkeypatch.setattr(windows, 'midscene_env', lambda: {})
    monkeypatch.setattr(windows, 'http_json', lambda method, url, payload: captured.update(payload))
    windows.heartbeat(devices)
    assert captured['devices'][0]['platform'] == 'android'
    assert {'runAdbShell', 'aiScroll'} <= set(captured['capabilities']['ui_actions'])
    normalized = runner_service.normalize_device_list(captured['devices'])
    registry[2]['win'] = {'online': True, 'capabilities': captured['capabilities'], 'devices': normalized}
    target = {'runner_id': 'win', 'device_id': 'android-phone', 'platform': 'android'}
    assert resolve(target)['ok']
    job = {'ui_execution_contract': 'ui-v1', 'target_runner_id': 'win', 'device_id': 'android-phone',
           'platform': 'android', 'ui_required_actions': ['launch', 'aiTap']}
    assert runner_service.runner_accepts_explicit_job(registry[2]['win'], job, 'win')
