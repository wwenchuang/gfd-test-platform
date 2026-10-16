"""Private draft boundaries, fresh identity and fail-closed generation."""
import copy
import threading

import pytest

from task_server.services.operation_attribution import actor_context
from task_server.services.ui_case_draft_store import DraftConflict
from task_server.services import ui_case_draft_service as service

APP = 'com.kfb.model'
A = {'kind': 'user', 'username': 'a', 'user_id': 'u-a', 'display_name': '作者A'}
B = {'kind': 'user', 'username': 'b', 'user_id': 'u-b', 'display_name': '作者B'}
TARGET = {'runner_id': 'mac', 'device_id': 'iphone', 'platform': 'ios'}


def proposal():
    return {'title': '导航', 'preconditions': [], 'questions': [], 'steps': [
        {'action': 'aiTap', 'target': '首页我的按钮', 'value': '', 'expected': '我的页面显示个人中心', 'review_state': 'needs_review', 'source_ref': 'text'},
        {'action': 'aiAssert', 'target': '我的页面显示个人中心', 'value': '', 'expected': '我的页面显示个人中心', 'review_state': 'needs_review', 'source_ref': 'text'}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from task_server import identity
    from task_server.services import business_line_service, runner_service, ai_skill_service, yaml_baseline_cache, sonic_service
    monkeypatch.setenv('TASK_UI_DRAFT_DB', str(tmp_path / 'private' / 'drafts.sqlite3'))
    profiles = {actor['username']: {**actor, 'status': 'active', 'permissions': ['ui.edit', 'ui.view', 'ui.execute'], 'scope': {'ui_apps': [APP]}} for actor in (A, B)}
    monkeypatch.setattr(identity, 'get_access_profile', lambda name: copy.deepcopy(profiles.get(name)))
    monkeypatch.setattr(business_line_service, 'configured_test_application', lambda app: {'enabled': True, 'package': APP, 'platform_mappings': {'android': APP, 'ios': 'com.kfb.3d'}} if app == APP else None)
    monkeypatch.setattr(runner_service, 'list_runners', lambda: {'mac': {'online': True, 'capabilities': {'ui_execution_contract': 'ui-v1', 'ui_platforms': ['ios'], 'ui_actions': ['launch', 'aiTap', 'aiWaitFor', 'aiAssert']}, 'devices': [{'device_id': 'iphone', 'platform': 'ios', 'status': 'online', 'preflight_status': 'ready'}]}})
    calls, pending = [], []
    monkeypatch.setattr(ai_skill_service, 'run_ai_skill', lambda name, payload, **kwargs: calls.append((name, payload)) or proposal())
    monkeypatch.setattr(yaml_baseline_cache, 'get_yaml_baseline_cache', lambda **kwargs: {'items': []})
    monkeypatch.setattr(sonic_service, 'sonic_notify_known_apps', lambda: [{'package': APP, 'modules': ['shared']}])
    monkeypatch.setattr(service, '_generation_lock', threading.Lock())
    monkeypatch.setattr(service, '_start_worker', lambda work: pending.append(work))
    return profiles, pending, calls


def create(env):
    with actor_context(A):
        draft = service.create_draft(A, {'app_id': APP, 'text': '点击首页我的，检查个人中心'})
    env[1].pop(0)()
    with actor_context(A):
        return service.get_draft(A, draft['draft_id'])


def test_async_create_default_review_and_private_reads(env):
    with actor_context(A):
        draft = service.create_draft(A, {'app_id': APP, 'text': '点击我的'})
        assert draft['generation_status'] == 'queued'
        assert not env[2]
    env[1].pop(0)()
    with actor_context(A):
        loaded = service.get_draft(A, draft['draft_id'])
        assert loaded['revision'] == 2 and loaded['steps'][0]['review_state'] == 'needs_review'
        assert service.list_drafts(A, APP)['items'][0]['draft_id'] == draft['draft_id']
    with actor_context(B):
        with pytest.raises(KeyError):
            service.get_draft(B, draft['draft_id'])
        assert service.list_drafts(B, APP)['items'] == []


def test_busy_rejects_without_creating_second_draft(env):
    with actor_context(A):
        service.create_draft(A, {'app_id': APP, 'text': '点击我的'})
        with pytest.raises(ValueError, match='draft_generation_busy'):
            service.create_draft(A, {'app_id': APP, 'text': '点击首页'})
        assert len(service.list_drafts(A, APP)['items']) == 1
    env[1].pop(0)()


def test_untrusted_forged_and_revoked_actors_are_denied(env):
    with pytest.raises(PermissionError):
        service.list_drafts(A, APP)
    with actor_context(A):
        with pytest.raises(PermissionError):
            service.create_draft(B, {'app_id': APP, 'text': '点击我的'})
        with pytest.raises(PermissionError):
            service.create_draft(A, {'app_id': 'com.other', 'text': '点击我的'})
        draft = service.create_draft(A, {'app_id': APP, 'text': '点击我的'})
    env[0]['a']['permissions'].remove('ui.edit')
    env[1].pop(0)()
    assert not env[2]
    with actor_context(A):
        assert service.get_draft(A, draft['draft_id'])['generation_status'] == 'failed'


def test_worker_rechecks_auth_after_model_return(env, monkeypatch):
    from task_server.services import ai_skill_service
    def model(*args, **kwargs):
        env[0]['a']['scope'] = {'ui_apps': []}
        return proposal()
    monkeypatch.setattr(ai_skill_service, 'run_ai_skill', model)
    with actor_context(A):
        draft = service.create_draft(A, {'app_id': APP, 'text': '点击我的'})
    env[1].pop(0)()
    assert service._store().get(draft['draft_id'], A['user_id'])['generation_status'] == 'failed'


def test_edit_cas_restore_and_resolve_questions(env):
    draft = create(env)
    body = {key: draft[key] for key in ('title', 'preconditions', 'steps', 'questions')}
    body.update(revision=2, title='新标题', questions=[])
    with actor_context(A):
        edited = service.update_draft(A, draft['draft_id'], body)
        assert edited['revision'] == 3 and edited['title'] == '新标题'
        with pytest.raises(DraftConflict):
            service.update_draft(A, draft['draft_id'], body)
        restored = service.restore_draft(A, draft['draft_id'], {'revision': 3})
        assert restored['revision'] == 4 and restored['title'] == draft['title']
        with pytest.raises(ValueError):
            service.restore_draft(A, draft['draft_id'], {'revision': 4, 'target_revision': 1})


def test_late_worker_never_overwrites_manual_edit(env):
    with actor_context(A):
        draft = service.create_draft(A, {'app_id': APP, 'text': '点击我的'})
        body = proposal()
        body.update(revision=1, title='人工编辑')
        service.update_draft(A, draft['draft_id'], body)
    env[1].pop(0)()
    with actor_context(A):
        current = service.get_draft(A, draft['draft_id'])
        assert current['title'] == '人工编辑' and current['revision'] == 2
    assert not env[2]


@pytest.mark.parametrize('bad', [{'step_id': 'bad'}, {'review_state': 'confirmed'}, {'source_ref': 'recording:fake'}, {'action': 'runAdbShell'}])
def test_model_cannot_forge_verified_provenance_or_actions(env, monkeypatch, bad):
    from task_server.services import ai_skill_service
    result = proposal()
    result['steps'][0].update(bad)
    monkeypatch.setattr(ai_skill_service, 'run_ai_skill', lambda *args, **kwargs: result)
    draft = create(env)
    assert draft['generation_status'] == 'failed' and not draft['steps']
    assert 'bad' not in draft['error']


def test_ai_error_is_safe_chinese_and_releases_capacity(env, monkeypatch):
    from task_server.services import ai_skill_service
    def fail(*args, **kwargs):
        raise TimeoutError('token=secret https://private/config')
    monkeypatch.setattr(ai_skill_service, 'run_ai_skill', fail)
    draft = create(env)
    assert draft['generation_status'] == 'failed'
    assert '超时' in draft['error'] and 'secret' not in draft['error']
    with actor_context(A):
        service.create_draft(A, {'app_id': APP, 'text': '重试'})
    env[1].pop(0)()


def test_top3_filters_app_and_verified_success_before_limiting(env, monkeypatch):
    from task_server.services import yaml_baseline_cache
    def baseline(app, index):
        return {'module': 'shared', 'title': '我的'+str(index), 'snippet': 'android: {}\ntasks:\n- name: 我的\n  flow:\n  - launch: '+app+'\n  - aiAssert: 我的页面', 'trusted': True, 'baselineUsable': True, 'lastRunStatus': 'success', 'keywords': ['我的']}
    rows = [baseline('com.other', i) for i in range(5)] + [baseline(APP, i) for i in range(4)]
    monkeypatch.setattr(yaml_baseline_cache, 'get_yaml_baseline_cache', lambda **kwargs: {'items': rows})
    create(env)
    refs = env[2][0][1]['baseline_examples']
    assert len(refs) == 3 and all('com.other' not in str(ref) for ref in refs)


def test_validate_requires_review_static_score_and_exact_target(env, monkeypatch):
    draft = create(env)
    with actor_context(A):
        gate = service.validate_draft(A, draft['draft_id'], {'revision': 2, 'target': TARGET})
        assert not gate['ready'] and gate['blockers']
        body = {key: draft[key] for key in ('title', 'preconditions', 'steps', 'questions')}
        for step in body['steps']:
            step['review_state'] = 'confirmed'
        body['revision'] = 2
        edited = service.update_draft(A, draft['draft_id'], body)
        gate = service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': TARGET})
        assert gate['compiled_case']['ok']
        assert 'com.kfb.3d' in gate['compiled_case']['yaml_content']
        assert gate['target_resolution']['device_id'] == 'iphone'
        with pytest.raises(DraftConflict):
            service.validate_draft(A, draft['draft_id'], {'revision': 2, 'target': TARGET})
        env[0]['a']['permissions'].remove('ui.execute')
        with pytest.raises(PermissionError):
            service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': TARGET})


def confirm(draft):
    body = {key: copy.deepcopy(draft[key]) for key in ('title', 'preconditions', 'steps', 'questions')}
    body['revision'] = draft['revision']
    for step in body['steps']:
        step['review_state'] = 'confirmed'
    return body


def test_worker_runs_in_real_thread_with_trusted_context(env, monkeypatch):
    threads = []
    def spawn(work):
        thread = threading.Thread(target=work)
        threads.append(thread)
        thread.start()
    monkeypatch.setattr(service, '_start_worker', spawn)
    with actor_context(A):
        draft = service.create_draft(A, {'app_id': APP, 'text': '点击我的'})
    threads[0].join(timeout=3)
    assert not threads[0].is_alive()
    with actor_context(A):
        assert service.get_draft(A, draft['draft_id'])['generation_status'] == 'ready'
    assert not service._generation_lock.locked()


def test_model_questions_require_explicit_human_resolution(env, monkeypatch):
    from task_server.services import ai_skill_service
    result = proposal()
    result['questions'] = ['请选择同名按钮所在区域']
    monkeypatch.setattr(ai_skill_service, 'run_ai_skill', lambda *args, **kwargs: result)
    draft = create(env)
    with actor_context(A):
        edited = service.update_draft(A, draft['draft_id'], confirm(draft))
        gate = service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': TARGET})
        assert not gate['ready']
        body = confirm(edited)
        body['questions'] = []
        edited = service.update_draft(A, draft['draft_id'], body)
        assert service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': TARGET})['ready']


def test_ios_input_is_saved_but_blocked_by_real_compiler(env):
    draft = create(env)
    body = confirm(draft)
    body['steps'][0].update(action='aiInput', target='搜索输入框', value='机器人', expected='输入框显示机器人')
    with actor_context(A):
        edited = service.update_draft(A, draft['draft_id'], body)
        gate = service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': TARGET})
    assert not gate['ready']
    assert gate['compiled_case']['block_code'] == 'unsupported_steps'


def test_generated_score_blocks_even_after_human_confirmation(env, monkeypatch):
    draft = create(env)
    with actor_context(A):
        edited = service.update_draft(A, draft['draft_id'], confirm(draft))
        monkeypatch.setattr(service, 'score_midscene_yaml_executable', lambda *args, **kwargs: {'ok': False, 'errors': [], 'reasons': ['缺少可执行证据']})
        gate = service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': TARGET})
    assert not gate['ready'] and any(item['message'] == '缺少可执行证据' for item in gate['blockers'])


def test_restore_initial_empty_revision_is_explicitly_blocked(env):
    draft = create(env)
    with actor_context(A), pytest.raises(ValueError, match='draft_previous_revision_empty'):
        service.restore_draft(A, draft['draft_id'], {'revision': 2})


@pytest.mark.parametrize('body', [
    {'app_id': APP, 'text': ''}, {'app_id': APP, 'text': 'x' * 20001},
    {'app_id': APP, 'text': '点击我的', 'owner_user_id': 'u-b'},
])
def test_creation_is_bounded_and_cannot_forge_owner(env, body):
    with actor_context(A), pytest.raises(ValueError):
        service.create_draft(A, body)
    assert not env[1] and not env[2]


def test_normalizer_prompt_includes_actual_description_and_scoped_baselines():
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    rendered = render_ai_skill_prompt('ui_step_normalizer', {'app_id': APP, 'source_text': '描述唯一标记123', 'baseline_examples': [{'title': '基线唯一标记456'}]})
    assert '描述唯一标记123' in rendered and '基线唯一标记456' in rendered


def test_top3_accepts_existing_cache_task_block_format(env, monkeypatch):
    from task_server.services import yaml_baseline_cache
    snippet = '- name: 我的导航\n  flow:\n  - launch: com.kfb.model\n  - aiTap: 首页我的按钮\n  - aiAssert: 我的页面显示个人中心'
    monkeypatch.setattr(yaml_baseline_cache, 'get_yaml_baseline_cache', lambda **kwargs: {'items': [{'module': 'shared', 'title': '我的导航', 'snippet': snippet, 'trusted': True, 'baselineUsable': True, 'lastRunStatus': 'success', 'keywords': ['我的']}]})
    create(env)
    assert len(env[2][0][1]['baseline_examples']) == 1


def test_blockers_have_stable_code_and_chinese_message(env):
    draft = create(env)
    with actor_context(A):
        edited = service.update_draft(A, draft['draft_id'], confirm(draft))
        gate = service.validate_draft(A, draft['draft_id'], {'revision': edited['revision'], 'target': {**TARGET, 'runner_id': 'missing'}})
    assert any(item['code'] == 'runner_not_found' and '执行器' in item['message'] for item in gate['blockers'])


def test_startup_recovery_runs_once_without_touching_live_worker(env):
    stale = service.DraftStore().create({key: A[key] for key in ('user_id', 'username', 'display_name')}, APP, '重启前描述')
    with actor_context(A):
        assert service.get_draft(A, stale['draft_id'])['generation_status'] == 'failed'
        current = service.create_draft(A, {'app_id': APP, 'text': '当前工作'})
        assert service.get_draft(A, current['draft_id'])['generation_status'] == 'queued'
    env[1].pop(0)()
    with actor_context(A):
        assert service.get_draft(A, current['draft_id'])['generation_status'] == 'ready'


def test_top3_same_launch_cannot_leak_other_app_module(env, monkeypatch):
    from task_server.services import yaml_baseline_cache, sonic_service
    monkeypatch.setattr(sonic_service, 'sonic_notify_known_apps', lambda: [{'package': APP, 'modules': ['allowed']}, {'package': 'com.other', 'modules': ['private']}])
    snippet = '- name: 私有逻辑\n  flow:\n  - launch: com.kfb.model\n  - aiAssert: 他人私有业务'
    monkeypatch.setattr(yaml_baseline_cache, 'get_yaml_baseline_cache', lambda **kwargs: {'items': [{'module': 'private', 'title': '泄露', 'snippet': snippet, 'trusted': True, 'baselineUsable': True, 'lastRunStatus': 'success', 'keywords': []}]})
    create(env)
    assert env[2][0][1]['baseline_examples'] == []


def test_top3_rejects_ambiguous_module_and_metadata_conflict(env, monkeypatch):
    from task_server.services import yaml_baseline_cache, sonic_service
    monkeypatch.setattr(sonic_service, 'sonic_notify_known_apps', lambda: [{'package': APP, 'modules': ['shared']}, {'package': 'com.other', 'modules': ['shared']}])
    row = {'module': 'shared', 'app_package': APP, 'title': '同名模块', 'snippet': '- name: 导航\n  flow:\n  - launch: com.kfb.model\n  - aiAssert: 我的页面', 'trusted': True, 'baselineUsable': True, 'lastRunStatus': 'success', 'keywords': []}
    monkeypatch.setattr(yaml_baseline_cache, 'get_yaml_baseline_cache', lambda **kwargs: {'items': [row]})
    create(env)
    assert env[2][0][1]['baseline_examples'] == []


def test_permissions_are_specific_for_edit_view_and_execute(env):
    draft = create(env)
    env[0]['a']['permissions'] = ['ui.view']
    with actor_context(A):
        assert service.get_draft(A, draft['draft_id'])['draft_id'] == draft['draft_id']
        with pytest.raises(PermissionError):
            service.update_draft(A, draft['draft_id'], confirm(draft))
        with pytest.raises(PermissionError):
            service.validate_draft(A, draft['draft_id'], {'revision': draft['revision'], 'target': TARGET})
    env[0]['a']['permissions'] = ['ui.execute']
    with actor_context(A):
        with pytest.raises(PermissionError):
            service.get_draft(A, draft['draft_id'])
        assert not service.validate_draft(A, draft['draft_id'], {'revision': draft['revision'], 'target': TARGET})['ready']


def test_video_selection_step_can_be_confirmed_and_saved(env):
    from task_server.services.ui_video_analysis import draft_proposal
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    event={'action':'aiTap','target':'{{本次对象}}的删除按钮','value':'','start_ms':0,'end_ms':100,
      'frame_refs':['f1'],'evidence_kind':'inferred','uncertainties':[],
      'selection':'当前列表中第一条可删除测试记录','observation':'目标记录消失，其他记录保留',
      'reusable_expected':'{{本次对象}}消失，{{保留对象}}仍显示'}
    source={'source_id':'uvs_'+'c'*32,'analysis_version':1,'app_id':APP,'goal':'',
            'media':{'duration_ms':1000,'frames':[]},'uncertainties':[],'events':[event]}
    proposal,snapshot=draft_proposal(source)
    built=build_draft_yaml(proposal,APP)
    with actor_context(A):
        record=service._store().create(A,APP,'录屏来源',recording_snapshot=snapshot,
                         recording_content={**proposal,'yaml_content':built['yaml_content'],'ready':False})
        fetched=service.get_draft(A,record['draft_id'])
        body={key:copy.deepcopy(fetched[key]) for key in ('revision','title','preconditions','steps','questions')}
        body['questions']=[]
        for step in body['steps']:step['review_state']='confirmed'
        saved=service.update_draft(A,record['draft_id'],body)
    assert saved['revision']==record['revision']+1
    assert saved['steps'][0]['action']=='selectRecord'
