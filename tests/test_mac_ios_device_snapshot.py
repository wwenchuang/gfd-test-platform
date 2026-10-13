import base64
import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest
from task_server.services import runner_service

spec = importlib.util.spec_from_file_location('ios_snapshot_runner', Path('mac-midscene-runner.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
PNG = b'\x89PNG\r\n\x1a\n' + b'\0'*8 + (1170).to_bytes(4,'big') + (2532).to_bytes(4,'big')


@pytest.fixture(autouse=True)
def connected(monkeypatch):
    monkeypatch.setattr(runner, 'ios_connected_device_ids', lambda: ['iphone'])
    monkeypatch.setattr(runner, 'ios_marketing_name', lambda _: 'iPhone 13')


def test_metadata_survives_server_normalization():
    row = runner_service.normalize_device_list([{'device_id':'iphone','platform':'ios','ios_version':'26.6'}])[0]
    assert row['platform'] == 'ios'
    assert row['ios_version'] == '26.6'


def test_snapshot_uses_exact_device_and_reports_png(monkeypatch):
    monkeypatch.setattr(runner, 'IOS_BUNDLE_ID', 'com.kfb.3d')
    calls=[]
    def fetch(device,path):
        calls.append((device,path))
        return base64.b64encode(PNG).decode() if path == '/screenshot' else {'bundleId':'com.kfb.3d'}
    monkeypatch.setattr(runner, 'ios_wda_read', fetch, raising=False)
    monkeypatch.setattr(runner, 'ios_battery_level', lambda device: 36, raising=False)
    result = runner.capture_ios_device_snapshot('iphone')
    assert result['content_base64'] == base64.b64encode(PNG).decode()
    assert result['foreground_package'] == 'com.kfb.3d'
    assert result['resolution'] == '1170x2532'
    assert result['battery_level'] == 36
    assert all(device == 'iphone' for device,path in calls)
    with pytest.raises(ValueError): runner.capture_ios_device_snapshot('other')


def test_failed_capture_is_reported_and_does_not_upload_fake_image(monkeypatch):
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'IOS_BUNDLE_ID','com.kfb.3d')
    def fail(_): raise RuntimeError('WDA not ready')
    monkeypatch.setattr(runner,'capture_ios_device_snapshot',fail,raising=False)
    sent=[]
    monkeypatch.setattr(runner,'http_json',lambda method,path,payload,**kw: sent.append(payload))
    runner.upload_snapshot_requests({'snapshot_requests':[{'device_id':'iphone','request_id':'q'}]},[{'device_id':'iphone'}])
    assert sent[0]['error']
    assert 'content_base64' not in sent[0]


def test_offline_and_other_device_never_captured(monkeypatch):
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'IOS_BUNDLE_ID','com.kfb.3d')
    capture=Mock()
    monkeypatch.setattr(runner,'capture_ios_device_snapshot',capture,raising=False)
    sent=[]
    monkeypatch.setattr(runner,'http_json',lambda method,path,payload,**kw: sent.append(payload))
    runner.upload_snapshot_requests({'snapshot_requests':[{'device_id':'other','request_id':'q'}]},[])
    capture.assert_not_called()
    assert sent[0]['error']


def test_metadata_is_cached_and_only_selected_app_is_reported(monkeypatch):
    import plistlib
    monkeypatch.setattr(runner,'IOS_BUNDLE_ID','com.kfb.3d')
    monkeypatch.setattr(runner,'IOS_SOURCE_APP','com.kfb.model')
    runner._IOS_METADATA_CACHE.clear()
    calls=[]
    def output(args,**kw):
        calls.append(args)
        assert '-u' in args and args[args.index('-u')+1] == 'iphone'
        if args[0] == 'ideviceinfo':
            return plistlib.dumps({'DeviceName':'iPhone 13','ProductType':'iPhone14,5','ProductVersion':'26.6','PrivateField':'not-uploaded'})
        assert args[args.index('-b')+1]=='com.kfb.3d'
        return plistlib.dumps([{'CFBundleIdentifier':'com.kfb.3d','CFBundleShortVersionString':'1.19.7','CFBundleVersion':'70'}])
    monkeypatch.setattr(runner.subprocess,'check_output',output)
    monkeypatch.setattr(runner,'ios_battery_level',lambda _:37)
    row=runner.ios_device_metadata('iphone')
    assert runner.ios_device_metadata('iphone')==row
    assert len(calls)==2
    assert row['ios_version']=='26.6'
    assert row['installed_apps'][0]['source_package']=='com.kfb.model'
    assert 'PrivateField' not in row


def test_invalid_wda_image_rejected(monkeypatch):
    monkeypatch.setattr(runner,'IOS_BUNDLE_ID','com.kfb.3d')
    monkeypatch.setattr(runner,'ios_wda_read',lambda *a: base64.b64encode(b'not an image').decode())
    with pytest.raises(RuntimeError): runner.capture_ios_device_snapshot('iphone')


def test_upload_preserves_request_identity(monkeypatch):
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'IOS_BUNDLE_ID','com.kfb.3d')
    monkeypatch.setattr(runner,'capture_ios_device_snapshot',lambda d: {'content_base64':base64.b64encode(PNG).decode()})
    sent=[]
    monkeypatch.setattr(runner,'http_json',lambda method,path,payload,**kw:sent.append((path,payload)))
    runner.upload_snapshot_requests({'snapshot_requests':[{'device_id':'iphone','request_id':'q'},{'device_id':'iphone','request_id':'q'}]},[{'device_id':'iphone'}])
    assert len(sent)==1
    assert sent[0][0]=='/api/runner/device-snapshot'
    assert sent[0][1]['request_id']=='q'


def test_two_connected_iphones_are_reported_without_changing_execution_target(monkeypatch):
    monkeypatch.setattr(runner, 'RUNNER_PLATFORM', 'ios')
    monkeypatch.setattr(runner, 'IOS_BUNDLE_ID', 'com.kfb.3d')
    monkeypatch.setattr(runner, 'ios_connected_device_ids', lambda: ['iphone', 'iphone2'])
    monkeypatch.setattr(runner, 'ios_device_metadata', lambda device: {'device_id': device})
    assert [d['device_id'] for d in runner.detect_devices()] == ['iphone','iphone2']
    assert 'jobs/next' not in __import__('inspect').getsource(runner.run_ios_device_monitor)
