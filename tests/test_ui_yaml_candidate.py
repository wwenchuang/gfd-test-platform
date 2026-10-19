import copy
import pytest
import yaml
from tests.test_ui_case_draft_service import env, create, A, B, APP, TARGET
from task_server.services.operation_attribution import actor_context
from task_server.services import ui_case_draft_service as service
from task_server.services.ui_case_draft_contract import build_draft_yaml
from task_server.services.ui_case_draft_store import DraftConflict


def apply(draft, raw):
    return service.apply_yaml_candidate(A, draft['draft_id'], {'revision': draft['revision'], 'yaml_content': raw})


def test_roundtrip_resets_reviews_and_preserves_flow(env):
    draft = create(env)
    with actor_context(A):
        result = apply(draft, draft['yaml_content'])
    assert result['revision'] == draft['revision'] + 1
    assert result['yaml_candidate']['status'] == 'converted'
    assert yaml.safe_load(result['yaml_content']) == yaml.safe_load(draft['yaml_content'])
    assert all(s['review_state'] == 'needs_review' and s['source_ref'] == 'manual_yaml' for s in result['steps'])
    assert len(env[2]) == 1


@pytest.mark.parametrize('raw', ['tasks: [', 'android: {}\nandroid: {}\ntasks: []', 'android: &a {}\ntasks: [*a]', 'x' * 100001,
    'android: {}\ntasks: [{name: test, flow: [{runAdbShell: ls}]}]',
    'android: {}\ntasks: [{name: test, flow: [{launch: com.other.app}]}]',
    'android: {deviceId: other}\ntasks: []',
    'android: {}\ntasks: [{name: test, flow: [{aiWaitFor: 页面显示首页, timeout: 999}]}]'])
def test_invalid_persisted_blocks_every_gate_and_recovers(env, raw):
    draft = create(env)
    from task_server.services.ui_case_draft_execution import _source
    with actor_context(A):
        result = apply(draft, raw)
        assert result['yaml_candidate']['raw'] == raw
        assert result['yaml_candidate']['error']
        assert result['steps'] == draft['steps']
        loaded = service.get_draft(A, result['draft_id'])
        assert loaded == result
        assert not service.validate_draft(A, result['draft_id'], {'revision':result['revision'], 'target':TARGET})['ready']
        with pytest.raises(ValueError):
            _source(result)
        with pytest.raises(ValueError, match='draft_yaml_candidate_unresolved'):
            service.update_draft(A, result['draft_id'], {'revision':result['revision'], **{k:result[k] for k in service._EDIT_KEYS}})
        restored = service.restore_draft(A, result['draft_id'], {'revision':result['revision']})
        assert restored['yaml_candidate'] == {}
        assert restored['steps'] == draft['steps']
        assert restored['revision'] == result['revision'] + 1


def test_candidate_owner_app_cas_and_changed_assertion(env):
    draft = create(env)
    with actor_context(B), pytest.raises(KeyError):
        service.apply_yaml_candidate(B, draft['draft_id'], {'revision':draft['revision'], 'yaml_content':draft['yaml_content']})
    with actor_context(A):
        changed = apply(draft, draft['yaml_content'].replace('我的页面显示个人中心', '我的页面显示我的设备入口'))
        assert changed['steps'][-1]['target'] == '我的页面显示我的设备入口'
        with pytest.raises(DraftConflict):
            apply(draft, draft['yaml_content'])
        env[0]['a']['scope']['ui_apps'] = ['com.other.app']
        with pytest.raises(PermissionError):
            apply(changed, changed['yaml_content'])

def test_maximum_generated_roundtrip_preserves_all_actions(env):
    from task_server.services.ui_case_draft_contract import normalize_steps
    draft = create(env)
    steps = [{'action':'aiTap','target':f'第{i}个入口','expected':f'第{i}个页面显示标题'} for i in range(49)]
    steps.append({'action':'aiAssert','target':'最终页面显示完成标题'})
    original = normalize_steps({'title':'最长用例','steps':steps}, APP)
    raw = build_draft_yaml(original, APP)['yaml_content']
    with actor_context(A):
        result = apply(draft, raw)
    assert result['yaml_candidate']['status'] == 'converted'
    assert len(result['steps']) <= 50
    assert yaml.safe_load(result['yaml_content']) == yaml.safe_load(raw)


def test_supported_input_scroll_and_wait_values_are_preserved(env):
    draft = create(env)
    raw = yaml.safe_dump({'android':{},'tasks':[{'name':'输入检查','flow':[
        {'launch':APP}, {'aiInput':'搜索框','value':'你好 123'}, {'aiAssert':'搜索框显示你好 123'},
        {'aiScroll':'结果列表','direction':'down','distance':240,'scrollType':'singleAction'}, {'aiAssert':'列表显示第二页'},
        {'aiWaitFor':'加载标识消失','timeout':12000}, {'aiAssert':'结果显示三条记录'}]}]}, allow_unicode=True)
    with actor_context(A):
        result = apply(draft,raw)
    assert result['yaml_candidate']['status'] == 'converted'
    assert yaml.safe_load(result['yaml_content']) == yaml.safe_load(raw)


def test_repeated_invalid_then_restore_skips_invalid_revisions(env):
    draft = create(env)
    with actor_context(A):
        invalid = apply(draft,'tasks: [')
        invalid = apply(invalid,'android: {}')
        restored = service.restore_draft(A, invalid['draft_id'], {'revision': invalid['revision']})
    assert restored['steps'] == draft['steps']
    assert restored['revision'] == invalid['revision'] + 1

def test_manual_yaml_provenance_survives_save_and_old_raw_is_cleared(env):
    draft = create(env)
    with actor_context(A):
        converted = apply(draft, draft['yaml_content'])
        body = {k:copy.deepcopy(converted[k]) for k in service._EDIT_KEYS}
        body['steps'][-1]['target'] = body['steps'][-1]['expected'] = '我的页面显示新的目标'
        saved = service.update_draft(A, draft['draft_id'], {**body,'revision':converted['revision']})
    assert saved['yaml_candidate'] == {}
    assert '我的页面显示新的目标' in saved['yaml_content']
    assert all(step['source_ref'] == 'manual_yaml' for step in saved['steps'])


@pytest.mark.parametrize('raw', [
    'android: {}\ntasks: [{name: a, flow: [{aiAssert: 结果显示正常, x: true}]}]',
    'android: {}\ntasks: [{name: a, flow: [{aiAssert: {x: 1}}]}]',
    'android: {}\ntasks: [{name: a, flow: [{aiAssert: 结果显示正常}], config: {}}]',
    'android: {}\ntasks: []\n---\nandroid: {}\ntasks: []',
    '[' * 20 + '0' + ']' * 20,
    'android: {}\ntasks: [{name: a, flow: [{aiWaitFor: 结果显示正常}]}]',
])
def test_unsupported_structures_preserved(env, raw):
    draft = create(env)
    with actor_context(A):
        result = apply(draft, raw)
    assert result['yaml_candidate']['status'] == 'invalid'
    assert result['yaml_candidate']['raw'] == raw

def test_yaml_import_keeps_recording_snapshot_only_as_audit(env):
    from task_server.services.ui_case_draft_contract import normalize_steps
    snapshot = {'snapshot_id':'a'*64,'actions':[{'id':'b'*32}],'evidence_note':'原始录制证据'}
    normalized = normalize_steps({'title':'原录制','steps':[{'action':'aiAssert','target':'我的页面显示我的设备入口'}]}, APP)
    source = {**normalized,'yaml_content':build_draft_yaml(normalized,APP)['yaml_content'],'ready':False}
    draft = service._store().create(A,APP,'录制来源',recording_snapshot=snapshot,recording_content=source)
    with actor_context(A):
        result = apply(draft, draft['yaml_content'])
    assert result['source_snapshot'] == snapshot
    assert all(s['source_ref'] == 'manual_yaml' for s in result['steps'])
    assert set(s['step_id'] for s in result['steps']).isdisjoint(s['step_id'] for s in draft['steps'])

def test_repeated_explicit_waits_are_not_dropped(env):
    draft = create(env)
    raw = yaml.safe_dump({'android':{},'tasks':[{'name':'重复等待','flow':[
        {'launch':APP}, {'aiWaitFor':'我的入口','timeout':12000}, {'aiWaitFor':'我的入口','timeout':12000},
        {'aiTap':'我的入口'}, {'aiAssert':'我的页面显示设备入口'}]}]}, allow_unicode=True)
    with actor_context(A):
        result = apply(draft,raw)
    assert result['yaml_candidate']['status'] == 'converted'
    assert yaml.safe_load(result['yaml_content']) == yaml.safe_load(raw)

def test_repeated_assertions_are_never_silently_dropped(env):
    draft = create(env)
    raw = yaml.safe_dump({'android':{},'tasks':[{'name':'重复检查','flow':[
        {'launch':APP}, {'aiWaitFor':'我的入口','timeout':12000}, {'aiTap':'我的入口'},
        {'aiAssert':'我的页面显示设备入口'}, {'aiAssert':'我的页面显示设备入口'}, {'aiAssert':'列表显示三台设备'}]}]}, allow_unicode=True)
    with actor_context(A):
        result = apply(draft,raw)
    assert result['yaml_candidate']['status'] == 'invalid' or yaml.safe_load(result['yaml_content']) == yaml.safe_load(raw)
