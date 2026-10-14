"""Simple entry catalog: current permissions, bounded summaries and cheap refresh."""
import hashlib
import json

import pytest

from task_server.services.operation_attribution import actor_context
from task_server.services import ui_execution_catalog as catalog

ACTOR = {'kind': 'user', 'username': 'reader', 'user_id': 'u-reader', 'display_name': '读者'}
APP = 'com.fixture.app'
SOURCE = 'android: {}\ntasks:\n- name: 导航\n  flow:\n  - launch: com.fixture.app\n  - aiTap: 打印记录\n'

@pytest.fixture
def data(monkeypatch):
    from task_server import identity
    from task_server.services import business_line_service, case_service, runner_service
    catalog.clear_catalog_cache()
    profile = {'username':'reader','user_id':'u-reader','status':'active','permissions':['ui.view','ui.execute'],
               'scope':{'ui_apps':[APP]},'must_change_password':False}
    apps = [{'package':APP,'name':'测试应用','enabled':True,'platform_mappings':{'android':APP,'ios':'com.fixture.ios'}},
            {'package':'com.secret.app','name':'不可见应用','enabled':True}]
    rows = [{'case_id':f'c{i}','app_package':APP,'task_name':f'导航{i}','module':'账户','file':f'{i}.yaml','status':'active','_root':'/private/root'} for i in range(7)]
    rows.append({'case_id':'hidden','app_package':'com.secret.app','task_name':'不许泄露','module':'秘密','file':'s.yaml'})
    calls = {'catalog':0,'source':0,'runner':0}
    def read_catalog(): calls['catalog']+=1;return rows
    def read_source(row): calls['source']+=1;return SOURCE
    runners={'mac':{'online':True,'workspace':'/private/workspace','token':'never-return','capabilities':{'ui_execution_contract':'ui-v1','ui_attempt_events':1,'ui_private_reports':1,'ui_process_stop_proof':1,'job_execution':True,'ui_platforms':['ios'],'ui_actions':['launch','aiTap','aiWaitFor','aiAssert']},
      'devices':[{'device_id':'phone','label':'iPhone 13','platform':'ios','status':'online','preflight_status':'ready','ios_version':'26.6',
                  'installed_apps':[{'package':'com.fixture.ios','version_name':'1.0','version_code':'7','error':'/private/error'}],
                  'snapshot_captured_at':'2026-10-13 17:00:00','snapshot_app_package':'com.fixture.ios','snapshot_url':'https://evil.example/secret','wdaPort':8100}]}}
    def read_runners(): calls['runner']+=1;return runners
    monkeypatch.setattr(identity,'get_access_profile',lambda name:profile)
    monkeypatch.setattr(business_line_service,'configured_test_applications',lambda **kw:apps)
    monkeypatch.setattr(case_service,'list_task_case_assets',read_catalog)
    monkeypatch.setattr(case_service,'task_case_yaml',read_source)
    monkeypatch.setattr(runner_service,'list_runners',read_runners)
    with actor_context(ACTOR):
        yield profile,rows,calls,runners
    catalog.clear_catalog_cache()

def test_all_groups_preview_three_without_private_fields(data):
    result=catalog.get_ui_catalog(None)
    assert [a['app_id'] for a in result['apps']]==[APP]
    assert result['groups'][0]['total']==7
    assert len(result['groups'][0]['items'])==3
    assert result['groups'][0]['has_more'] is True
    encoded=json.dumps(result)
    assert 'private' not in encoded and '不许泄露' not in encoded and 'yaml_content' not in encoded
    assert result['groups'][0]['items'][0]['revision']==hashlib.sha256(SOURCE.encode()).hexdigest()

def test_searches_steps_and_pages_without_rescanning_catalog(data):
    first=catalog.get_ui_catalog(None,app_id=APP,query='打印记录',limit=3)
    second=catalog.get_ui_catalog(None,app_id=APP,query='打印记录',cursor=first['next_cursor'],limit=3)
    assert len(first['items'])==len(second['items'])==3
    assert not set(x['case_id'] for x in first['items']) & set(x['case_id'] for x in second['items'])
    assert data[2]['catalog']==1
    assert data[2]['source']==8

def test_cached_catalog_still_applies_current_app_scope_and_rejects_forgery(data):
    catalog.get_ui_catalog(None)
    data[0]['scope']['ui_apps']=[]
    assert catalog.get_ui_catalog(None)['groups']==[]
    with pytest.raises(PermissionError): catalog.get_ui_catalog(None,app_id=APP)
    with pytest.raises(PermissionError): catalog.get_ui_catalog({**ACTOR,'user_id':'other'})

def test_device_refresh_reads_one_registry_and_no_case_sources(data):
    result=catalog.get_ui_devices(None,APP)
    assert data[2]=={'catalog':0,'source':0,'runner':1}
    d=result['devices'][0]
    assert d['target']=={'runner_id':'mac','device_id':'phone','platform':'ios'}
    assert d['app_version']=='1.0 (7)' and d['connection_status']=='ready'
    encoded=json.dumps(result)
    assert 'private' not in encoded and 'never-return' not in encoded and 'wdaPort' not in encoded and 'evil' not in encoded
    assert d['snapshot_url']==''
    assert '整机画面' in d['snapshot_hint']

@pytest.mark.parametrize('field,value',[
    ('ui_platforms',[]),
    ('ui_actions',['launch','aiTap']),
    ('ui_private_reports','missing'),
    ('ui_private_reports',None),
    ('ui_private_reports',False),
    ('ui_private_reports',2),
    ('ui_private_reports','1'),
    ('ui_process_stop_proof','missing'),
    ('ui_process_stop_proof',None),
    ('ui_process_stop_proof',False),
    ('ui_process_stop_proof',2),
    ('ui_process_stop_proof','1'),
])
def test_incomplete_runner_capabilities_block_readiness(data,field,value):
    if value == 'missing':
        data[3]['mac']['capabilities'].pop(field)
    else:
        data[3]['mac']['capabilities'][field]=value
    assert catalog.get_ui_devices(None,APP)['devices'][0]['connection_status']=='upgrade_required'

def test_full_scope_snapshot_link_does_not_depend_on_foreground_label(data):
    data[0]['scope']['ui_apps']='*'
    data[3]['mac']['devices'][0]['snapshot_app_package']='com.other.app'
    row=catalog.get_ui_devices(None,APP)['devices'][0]
    assert row['snapshot_url'].startswith('/api/runner/device-snapshot?')
    assert 'app_id='+APP in row['snapshot_url']
    assert row['snapshot_hint']==''

@pytest.mark.parametrize('full_scope',[False,True])
def test_legacy_windows_device_remains_visible_with_saved_preview(data,monkeypatch,full_scope):
    from task_server.services import runner_service
    from urllib.parse import parse_qs, urlsplit
    # d31cdf1 Windows detect_devices reports Android metadata without platform.
    device=runner_service.normalize_device_list([{
        'device_id':'legacy-phone','status':'online','brand':'OPPO','model':'PHM110',
        'adb_path':r'C:\platform-tools\adb.exe','android_version':'14','sdk':'34',
        'resolution':'1080x2412','preflight_status':'ready',
        'installed_apps':[{'package':APP,'installed':True,'version_name':'2.0.0','version_code':'51'}],
    }])[0]
    device.update(snapshot_captured_at='2026-10-13 17:00:00',
                  snapshot_url='/api/runner/device-snapshot?runner_id=win&device_id=legacy-phone')
    data[3]['win']={'online':True,'capabilities':{'yaml_dry_run':True,'apk_install':True},'devices':[device]}
    if full_scope:
        data[0]['scope']['ui_apps']='*'
    def unexpected_io(*args,**kwargs):
        pytest.fail('device catalog must only read the existing runner registry')
    monkeypatch.setattr(runner_service,'request_device_snapshots',unexpected_io)
    monkeypatch.setattr(runner_service,'_load_jobs',unexpected_io)
    result=catalog.get_ui_devices(None,APP)
    row=next(d for d in result['devices'] if d['target']['runner_id']=='win')
    assert row['target']=={'runner_id':'win','device_id':'legacy-phone','platform':'android'}
    assert row['connection_status']=='upgrade_required'
    assert row['name']=='OPPO Reno9' and row['app_version']=='2.0.0 (51)'
    assert row['os_version']=='14' and row['resolution']=='1080x2412'
    assert row['snapshot_captured_at']=='2026-10-13 17:00:00'
    if full_scope:
        assert parse_qs(urlsplit(row['snapshot_url']).query)=={
            'runner_id':['win'],'device_id':['legacy-phone'],'app_id':[APP]}
    else:
        assert row['snapshot_url']=='' and '整机画面' in row['snapshot_hint']
    assert data[2]=={'catalog':0,'source':0,'runner':1}
    assert 'adb_path' not in json.dumps(result)
    assert 'platform' not in device

@pytest.mark.parametrize('evidence',[
    {'android_version':'14'}, {'androidVersion':'14'},
    {'adb_path':'/platform-tools/adb'}, {'adbPath':'/platform-tools/adb'},
])
def test_inferred_legacy_android_never_becomes_ready(data,evidence):
    device=data[3]['mac']['devices'][0]
    device.pop('platform')
    device.pop('ios_version')
    device.update(evidence)
    data[3]['mac']['capabilities']['ui_platforms']=['android']
    row=catalog.get_ui_devices(None,APP)['devices'][0]
    assert row['target']['platform']=='android'
    assert row['connection_status']=='upgrade_required'
    assert 'platform' not in device

@pytest.mark.parametrize('metadata',[
    {}, {'sdk':'34'}, {'android_version':'  ','adb_path':''}, {'android_version':False},
    {'ios_version':'26.6','adb_path':'/platform-tools/adb'},
    {'platform':'web','android_version':'14'},
])
def test_unknown_platform_is_not_inferred_from_runner_or_ambiguous_metadata(data,metadata):
    data[3]['mac']['devices']=[{'device_id':'unknown-phone','status':'online',**metadata}]
    data[3]['mac']['capabilities'].update(ui_platforms=['android'],apk_install=True,yaml_dry_run=True)
    data[3]['mac']['hostname']='WINDOWS-HOST'
    assert catalog.get_ui_devices(None,APP)['devices']==[]

def test_missing_saved_snapshot_does_not_invent_preview(data):
    data[0]['scope']['ui_apps']='*'
    data[3]['mac']['devices'][0].pop('snapshot_captured_at')
    row=catalog.get_ui_devices(None,APP)['devices'][0]
    assert row['snapshot_url']=='' and row['snapshot_captured_at']==''

def test_offline_selection_remains_visible_and_no_replacement_selected(data):
    data[3]['mac']['online']=False
    result=catalog.get_ui_devices(None,APP)
    assert len(result['devices'])==1 and result['devices'][0]['connection_status']=='offline'
    assert 'selected' not in json.dumps(result)

@pytest.mark.parametrize('kwargs',[{'limit':101},{'limit':0},{'query':'x'*201},{'cursor':'bogus'},{'module':'x'*129}])
def test_invalid_filters_rejected(data,kwargs):
    with pytest.raises(ValueError):catalog.get_ui_catalog(None,app_id=APP,**kwargs)

def test_drafts_visible_but_not_labelled_runnable(data):
    data[1][0]['status']='draft'
    result=catalog.get_ui_catalog(None,app_id=APP)
    row=next(x for x in result['items'] if x['case_id']=='c0')
    assert row['execution_eligible'] is False and row['block_code']=='generated_case_gate_required'

def test_catalog_routes_default_off_and_availability_is_readable(data,monkeypatch):
    from task_server import router
    class Handler:
        def _json(self,body,status=200): self.body,self.status=body,status
    h=Handler();monkeypatch.delenv('UI_SIMPLE_FLOW_ENABLED',raising=False)
    router._get_ui_execution_availability(h,{})
    assert h.status==200 and h.body=={'ok':True,'enabled':False,
                                    'evidence_available':True,'recovery_available':True}
    router._get_ui_execution_catalog(h,{})
    assert h.status==404
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1')
    router._get_ui_execution_catalog(h,{'app_id':APP})
    assert h.status==200 and len(h.body['items'])==7
    router._get_ui_execution_devices(h,{'app_id':APP})
    assert h.status==200 and len(h.body['devices'])==1
