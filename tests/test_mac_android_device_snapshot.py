import base64
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


spec = importlib.util.spec_from_file_location('android_snapshot_runner', Path('mac-midscene-runner.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + (1080).to_bytes(4, 'big') + (2400).to_bytes(4, 'big')


@pytest.fixture
def android(monkeypatch):
    monkeypatch.setattr(runner, 'RUNNER_PLATFORM', 'android')
    resolve = Mock(return_value=('/test/adb', ['selected-phone']))
    monkeypatch.setattr(runner, 'resolve_adb_with_devices', resolve)
    sent = []
    monkeypatch.setattr(runner, 'http_json', lambda method, path, payload, **kw: sent.append((method, path, payload, kw)))
    return resolve, sent


def test_requested_android_preview_uses_exact_device_and_existing_upload_contract(android, monkeypatch):
    calls = []
    def execute(args, **kwargs):
        calls.append((args, kwargs))
        assert args[:3] == ['/test/adb', '-s', 'selected-phone']
        if args[3:] == ['exec-out', 'screencap', '-p']:
            assert kwargs == {'capture_output': True, 'timeout': 15}
            return SimpleNamespace(stdout=PNG, returncode=0)
        assert args[3] == 'shell'
        values = {'battery': 'level: 83\ntemperature: 312', 'display': 'Display Power: state=ON',
                  'window': 'mCurrentFocus=Window{123 u0 com.kfb.model/.MainActivity}'}
        return SimpleNamespace(stdout=values[args[5]], returncode=0)
    monkeypatch.setattr(runner.subprocess, 'run', execute)
    runner.upload_snapshot_requests({'snapshot_requests': [{'device_id': 'selected-phone', 'request_id': 'request-1'}]},
                                    [{'device_id': 'selected-phone', 'status': 'online'}])
    resolve, sent = android
    assert len(sent) == 1
    method, path, payload, kwargs = sent[0]
    assert (method, path) == ('POST', '/api/runner/device-snapshot')
    assert (payload['runner_id'], payload['device_id'], payload['request_id']) == (runner.RUNNER_ID, 'selected-phone', 'request-1')
    assert base64.b64decode(payload['content_base64']) == PNG
    assert payload['battery_level'] == 83 and payload['battery_temperature_c'] == 31.2
    assert payload['screen_on'] is True and payload['foreground_package'] == 'com.kfb.model'
    assert kwargs['timeout'] == 15
    assert len([args for args, _ in calls if args[3] == 'exec-out']) == 1


@pytest.mark.parametrize('response', [{}, {'snapshot_requests': []}, {'snapshot_requests': None}, {'snapshot_requests': [None, {}, {'device_id': 'selected-phone'}]}])
def test_no_valid_request_does_not_resolve_adb_or_capture(android, monkeypatch, response):
    command = Mock(side_effect=AssertionError('unexpected device command'))
    monkeypatch.setattr(runner.subprocess, 'run', command)
    runner.upload_snapshot_requests(response, [{'device_id': 'selected-phone'}])
    resolve, sent = android
    resolve.assert_not_called()
    command.assert_not_called()
    assert sent == []


@pytest.mark.parametrize('devices', [[], [{'device_id': 'another-phone'}], [{'device_id': 'selected-phone', 'status': 'offline'}]])
def test_unavailable_phone_reports_failure_without_falling_back(android, monkeypatch, devices):
    command = Mock(side_effect=AssertionError('unexpected device command'))
    monkeypatch.setattr(runner.subprocess, 'run', command)
    runner.upload_snapshot_requests({'snapshot_requests': [{'device_id': 'selected-phone', 'request_id': 'q'}]}, devices)
    resolve, sent = android
    resolve.assert_not_called()
    command.assert_not_called()
    assert len(sent) == 1 and sent[0][2]['error']
    assert sent[0][2]['device_id'] == 'selected-phone'
    assert 'content_base64' not in sent[0][2]


def test_duplicate_refresh_captures_once_per_device(android, monkeypatch):
    capture = Mock(return_value={'content_base64': base64.b64encode(PNG).decode()})
    monkeypatch.setattr(runner, 'capture_device_snapshot', capture, raising=False)
    request = {'device_id': 'selected-phone', 'request_id': 'q'}
    runner.upload_snapshot_requests({'snapshot_requests': [request, request]}, [{'device_id': 'selected-phone'}])
    capture.assert_called_once_with('/test/adb', 'selected-phone')
    assert len(android[1]) == 1


@pytest.mark.parametrize('failure', [RuntimeError('invalid PNG'), subprocess.TimeoutExpired(['adb'], 15)])
def test_failed_capture_uploads_error_without_fake_image(android, monkeypatch, failure):
    monkeypatch.setattr(runner, 'capture_device_snapshot', Mock(side_effect=failure), raising=False)
    runner.upload_snapshot_requests({'snapshot_requests': [{'device_id': 'selected-phone', 'request_id': 'q'}]},
                                    [{'device_id': 'selected-phone'}])
    assert len(android[1]) == 1 and android[1][0][2]['error']
    assert 'content_base64' not in android[1][0][2]


@pytest.mark.parametrize('content,returncode,max_bytes', [(b'not PNG', 0, 1024), (PNG, 1, 1024), (PNG, 0, 8)])
def test_android_capture_rejects_invalid_failed_or_oversized_images(content, returncode, max_bytes):
    execute = Mock(return_value=SimpleNamespace(stdout=content, returncode=returncode))
    with pytest.raises(RuntimeError):
        runner.capture_screen_png('/test/adb', 'selected-phone', run=execute, max_bytes=max_bytes)
    execute.assert_called_once_with(['/test/adb', '-s', 'selected-phone', 'exec-out', 'screencap', '-p'],
                                    capture_output=True, timeout=15)


def test_ios_preview_still_uses_wda_without_resolving_adb(android, monkeypatch):
    monkeypatch.setattr(runner, 'RUNNER_PLATFORM', 'ios')
    capture = Mock(return_value={'content_base64': base64.b64encode(PNG).decode()})
    monkeypatch.setattr(runner, 'capture_ios_device_snapshot', capture)
    runner.upload_snapshot_requests({'snapshot_requests': [{'device_id': 'iphone', 'request_id': 'q'}]}, [{'device_id': 'iphone'}])
    capture.assert_called_once_with('iphone')
    android[0].assert_not_called()
    assert len(android[1]) == 1 and android[1][0][3]['timeout'] == 15


def test_detected_android_metadata_satisfies_catalog_and_execution_platform_contract(android, monkeypatch):
    from task_server import identity
    from task_server.services import business_line_service, runner_service, ui_execution_catalog
    from task_server.services.operation_attribution import actor_context
    from task_server.services.ui_execution_contract import resolve_ui_target

    monkeypatch.setattr(runner, 'detect_device_ids', lambda: ['selected-phone'])
    monkeypatch.setattr(runner, 'adb_shell_text', lambda *args, **kw: '14' if args[3:] == ('ro.build.version.release',) else '')
    monkeypatch.setattr(runner, 'device_market_name', lambda *args: 'OPPO phone')
    monkeypatch.setattr(runner, 'runner_app_packages', lambda: ['com.kfb.model', 'com.xbxxhz.box'])
    monkeypatch.setattr(runner, 'detect_package_info', lambda adb, device, package: {
        'package': package, 'installed': True, 'version_name': '2.0.6', 'version_code': '76'})
    devices = runner_service.normalize_device_list(runner.detect_devices())
    registration = {'mac-android': {'online': True, 'capabilities': runner.RUNNER_CAPABILITIES, 'devices': devices}}
    actor = {'kind': 'user', 'username': 'tester', 'user_id': 'u1', 'display_name': '测试员'}
    profile = {**actor, 'status': 'active', 'permissions': ['ui.view', 'ui.execute'],
               'scope': {'ui_apps': ['com.kfb.model']}, 'must_change_password': False}
    app = {'package': 'com.kfb.model', 'name': '智小白3D', 'enabled': True}
    monkeypatch.setattr(identity, 'get_access_profile', lambda username: profile)
    monkeypatch.setattr(business_line_service, 'configured_test_applications', lambda **kw: [app])
    monkeypatch.setattr(business_line_service, 'configured_test_application', lambda package: app)
    monkeypatch.setattr(runner_service, 'list_runners', lambda: registration)
    target = {'runner_id': 'mac-android', 'device_id': 'selected-phone', 'platform': 'android'}
    with actor_context(actor):
        row = ui_execution_catalog.get_ui_devices(None, 'com.kfb.model')['devices'][0]
        assert row['connection_status'] == 'ready'
        assert row['target'] == target
        resolved = resolve_ui_target('com.kfb.model', target)
    assert resolved['ok'] and resolved['device_snapshot']['platform'] == 'android'
    assert {app['package'] for app in devices[0]['installed_apps']} == {'com.kfb.model', 'com.xbxxhz.box'}
    assert resolved['device_snapshot']['app_versions'][0]['version_name'] == '2.0.6'
