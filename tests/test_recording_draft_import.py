import copy
import json
import pytest
from test_ui_case_draft_service import env, A, B, APP
from task_server.services.operation_attribution import actor_context
from task_server.services import ui_case_draft_service as service, device_recording_service as recording

SID = 'a' * 32
STEP = 'b' * 32

@pytest.fixture
def source(env, monkeypatch):
    row = {'id': SID, 'created_by': 'a', 'app_package': APP, 'status': 'finished', 'steps': [
        {'id': STEP, 'type': 'tap', 'semantic_description': '我的', 'semantic_source': 'manual',
         'evidence_status': 'captured', 'screenshot_sha256': 'c' * 64, 'screenshot_path': '/secret/image.png',
         'point': {'x': 100, 'y': 200}}]}
    monkeypatch.setattr(recording, 'get_recording_session', lambda sid: copy.deepcopy(row) if sid == SID else (_ for _ in ()).throw(KeyError(sid)))
    return row

def imported():
    with actor_context(A):
        return service.import_recording_draft(A, {'app_id': APP, 'session_id': SID})

def test_import_is_private_reviewed_idempotent_snapshot(source, env):
    draft = imported()
    assert draft['generation_status'] == 'ready' and not draft['ready']
    assert draft['steps'][0]['action'] == 'aiTap'
    assert draft['steps'][0]['review_state'] == 'needs_review'
    assert draft['steps'][0]['expected'] == ''
    assert draft['steps'][0]['source_ref'].startswith('recording:')
    assert any('结果证据' in q for q in draft['questions'])
    assert '/secret' not in json.dumps(draft)
    assert 'screenshot_sha256' in json.dumps(draft['source_snapshot'])
    assert imported()['draft_id'] == draft['draft_id']
    source['steps'][0]['semantic_description'] = '首页'
    changed = imported()
    assert changed['draft_id'] != draft['draft_id']
    with actor_context(A):
        assert service.get_draft(A, draft['draft_id'])['steps'][0]['target'] == '我的'
        assert len(service.list_drafts(A, APP)['items']) == 2
    with actor_context(B):
        with pytest.raises(PermissionError): service.import_recording_draft(B, {'app_id': APP, 'session_id': SID})
    assert not env[1] and not env[2]

@pytest.mark.parametrize('status', ['recording', 'paused', 'cancelled', 'generating'])
def test_non_finished_rejected(source, status):
    source['status'] = status
    with pytest.raises(ValueError): imported()

@pytest.mark.parametrize('change', [{'app_package': 'com.other'}, {'created_by': 'b'}])
def test_source_scope_rejected(source, change):
    source.update(change)
    with pytest.raises(PermissionError): imported()

def test_missing_and_untrusted_body_rejected(source):
    with actor_context(A):
        with pytest.raises(KeyError): service.import_recording_draft(A, {'app_id': APP, 'session_id': 'd'*32})
        for key in ('steps', 'owner', 'yaml_content', 'path'):
            with pytest.raises(ValueError): service.import_recording_draft(A, {'app_id': APP, 'session_id': SID, key: 'bad'})

def test_stale_and_unsupported_remain_blocking(source):
    source['steps'][0].update(evidence_status='failed', evidence_warning='stale')
    source['steps'].append({'id': 'e'*32, 'type': 'key', 'key': 'HOME'})
    draft = imported()
    assert draft['steps'][0]['target'] == ''
    assert any('不支持' in q for q in draft['questions'])
    assert 'runAdbShell' not in draft['yaml_content']
    assert len(draft['source_snapshot']['actions']) == 2

def test_source_provenance_survives_edit_and_cannot_be_forged(source):
    draft = imported()
    body = {key: copy.deepcopy(draft[key]) for key in ('title', 'preconditions', 'steps', 'questions')}
    body['revision'] = draft['revision']
    with actor_context(A):
        edited = service.update_draft(A, draft['draft_id'], body)
        assert edited['source_snapshot'] == draft['source_snapshot']
        body['revision'] = edited['revision']
        body['steps'][0]['source_ref'] = 'recording:' + 'f'*64 + ':' + STEP
        with pytest.raises(ValueError): service.update_draft(A, draft['draft_id'], body)

def test_supported_actions_and_cross_app_launch(source):
    source['steps'] = [dict(id=str(i)*32, type=t, **v) for i,t,v in [
        (1,'launch',{'package': APP}), (2,'text',{'text':'查询', 'semantic_description':'搜索框', 'semantic_source':'manual', 'evidence_status':'captured'}),
        (3,'swipe',{'start':{'x':1,'y':100},'end':{'x':1,'y':10}}),
        (4,'checkpoint',{'description':'搜索结果显示模型', 'checkpoint_kind':'assert'})]]
    draft = imported()
    assert [s['action'] for s in draft['steps']] == ['launch','aiInput','aiScroll','aiAssert']
    assert all(s['review_state'] == 'needs_review' for s in draft['steps'])
    source['steps'][0]['package'] = 'com.other'
    with pytest.raises(ValueError): imported()

@pytest.mark.parametrize('steps', [[], [{}]*51])
def test_import_is_bounded(source, steps):
    source['steps'] = steps
    with pytest.raises(ValueError): imported()

def test_revoked_app_scope_and_edit_permission(source, env):
    env[0]['a']['scope']['ui_apps'] = []
    with pytest.raises(PermissionError): imported()
    env[0]['a']['scope']['ui_apps'] = [APP]
    env[0]['a']['permissions'].remove('ui.edit')
    with pytest.raises(PermissionError): imported()

def test_concurrent_retry_creates_one_draft(source):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        drafts = list(pool.map(lambda _: imported(), range(8)))
    assert len({d['draft_id'] for d in drafts}) == 1
    with actor_context(A): assert len(service.list_drafts(A, APP)['items']) == 1

@pytest.mark.parametrize('target', ['待识别', '未知', '按钮', '坐标(100,200)', '点击(100,200)'])
def test_ambiguous_or_coordinate_target_is_not_executable(source, target):
    source['steps'][0]['semantic_description'] = target
    draft = imported()
    assert draft['steps'][0]['target'] == ''
    assert any('目标' in q for q in draft['questions'])

def test_fifty_uncertain_steps_remain_editable(source):
    source['steps'] = [{'id':f'{i:032x}', 'type':'tap'} for i in range(50)]
    draft = imported()
    body={key:copy.deepcopy(draft[key]) for key in ('title','preconditions','steps','questions')}
    with actor_context(A):
        edited=service.update_draft(A,draft['draft_id'],{**body,'revision':draft['revision']})
    assert len(edited['steps'])==50
