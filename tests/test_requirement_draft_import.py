"""Requirement source imports are private immutable, bounded normalization inputs."""
import copy
import pytest
from test_ui_case_draft_service import env, A, B, APP
from task_server.services.operation_attribution import actor_context
from task_server.services import ui_case_draft_service as service, agent_service

@pytest.fixture
def source(env, monkeypatch):
    run = {'runId': 'agent-123-abcdef12', 'ownerUserId': A['user_id'], 'sourceType': 'requirement', 'appPackage': APP,
           'artifacts': {'generatedCases': {'cases': [{'id': 'C001', 'title': '我的入口', 'preconditions': ['已登录'], 'steps': ['点击我的'], 'expected': ['个人中心显示']}]}}}
    monkeypatch.setattr(agent_service, 'load_agent_runs', lambda: [copy.deepcopy(run)])
    return run

def imported():
    with actor_context(A):
        return service.import_requirement_draft(A, {'app_id': APP, 'run_id': 'agent-123-abcdef12', 'case_id': 'C001'})

def test_import_and_retry_keep_edits(source, env):
    draft = imported()
    assert draft['generation_status'] == 'queued'
    assert imported()['draft_id'] == draft['draft_id']
    assert len(env[1]) == 1
    env[1].pop()()
    loaded = imported()
    assert loaded['source_snapshot']['kind'] == 'requirement'
    assert all(s['review_state'] == 'needs_review' and s['source_ref'] == 'text' for s in loaded['steps'])
    body = {k: loaded[k] for k in ('title', 'preconditions', 'steps', 'questions')}
    body.update(title='人工修改', revision=loaded['revision'])
    with actor_context(A): service.update_draft(A, loaded['draft_id'], body)
    assert imported()['title'] == '人工修改'
    assert len(env[2]) == 1
    source['artifacts']['generatedCases']['cases'][0]['title'] = '新版'
    changed = imported()
    assert changed['draft_id'] != draft['draft_id']
    env[1].pop()()

@pytest.mark.parametrize('change', [{'ownerUserId': ''}, {'ownerUserId': B['user_id']}, {'appPackage': 'com.other'}, {'sourceType': 'manual'}])
def test_scope(source, change):
    source.update(change)
    with pytest.raises((ValueError, PermissionError)): imported()

@pytest.mark.parametrize('field', ['steps', 'yaml_content', 'path', 'owner'])
def test_forged_body(source, field):
    with actor_context(A), pytest.raises(ValueError):
        service.import_requirement_draft(A, {'app_id': APP, 'run_id': source['runId'], 'case_id': 'C001', field: 'bad'})

@pytest.mark.parametrize('steps', [[], ['a']*51, ['x'*20001], [{'action': 'runAdbShell', 'value': 'bad'}]])
def test_bad_steps(source, steps):
    source['artifacts']['generatedCases']['cases'][0]['steps'] = steps
    with pytest.raises(ValueError): imported()

def test_missing_expectation_stays_question(source, env):
    source['artifacts']['generatedCases']['cases'][0].pop('expected')
    draft = imported(); env[1].pop()()
    assert '原始用例未提供明确预期，请补充并核对' in imported()['questions']

def test_permissions(source, env):
    env[0]['a']['permissions'] = ['ui.view']
    with pytest.raises(PermissionError): imported()

def test_index_hash_supports_no_id_and_rejects_changed_selection(source, env):
    from task_server.services.requirement_draft_import import import_refs
    source['artifacts']['generatedCases']['cases'][0].pop('id')
    ref = import_refs(source)[0]
    body = {'app_id': APP, 'run_id': source['runId'], **ref}
    with actor_context(A):
        draft = service.import_requirement_draft(A, body)
    env[1].pop()()
    source['artifacts']['generatedCases']['cases'][0]['steps'] = ['新的操作']
    with actor_context(A), pytest.raises(service.DraftConflict): service.import_requirement_draft(A, body)
    assert draft['source_snapshot']['steps'] == ['点击我的']

def test_concurrent_retry_queues_once(source, env):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        drafts = list(pool.map(lambda _: imported(), range(12)))
    assert len({d['draft_id'] for d in drafts}) == 1
    assert len(env[1]) == 1
    env[1].pop()()

def test_actor_owner_is_stamped_not_request(monkeypatch):
    monkeypatch.setattr(agent_service, 'load_agent_runs', lambda: [])
    monkeypatch.setattr(agent_service, 'save_agent_runs', lambda rows: None)
    with actor_context(A):
        run = agent_service.create_agent_run({'target': '检查我的', 'sourceType': 'requirement', 'appPackage': APP, 'ownerUserId': B['user_id']})
    assert run['ownerUserId'] == A['user_id']

def test_app_disabled_and_scope_revoked(source, env, monkeypatch):
    env[0]['a']['scope']['ui_apps'] = []
    with pytest.raises(PermissionError): imported()
    env[0]['a']['scope']['ui_apps'] = [APP]
    monkeypatch.setattr(service.business_line_service, 'configured_test_application', lambda _: {'enabled':False})
    with pytest.raises(ValueError): imported()

def test_manual_case_and_duplicate_id_rejected(source):
    case = source['artifacts']['generatedCases']['cases'][0]
    case['level'] = 'manual'
    with pytest.raises(ValueError): imported()
    case.pop('level'); source['artifacts']['generatedCases']['cases'].append(copy.deepcopy(case))
    with pytest.raises(ValueError): imported()

def test_import_route_permission_and_feature_disabled(monkeypatch):
    from task_server.access_control import MainAccess, AccessDenied
    from task_server import ui_case_draft_http
    path = '/api/ui-case-drafts/import-requirement'
    with pytest.raises(AccessDenied): MainAccess({'permissions':['ui.view'],'scope':{'ui_apps':'*'}}, []).check('POST', path)
    MainAccess({'permissions':['ui.edit'],'scope':{'ui_apps':'*'}}, []).check('POST', path)
    routes = {}
    def register(key):
        def deco(fn): routes[key] = fn; return fn
        return deco
    ui_case_draft_http.register_routes(register,register,register,register)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','0')
    class Handler:
        def _body(self): raise AssertionError('disabled feature must not read body')
        def _json(self, body, status=200): self.body, self.status = body, status
    handler = Handler(); routes[path](handler,{})
    assert handler.status == 503

def test_worker_start_failure_is_terminal_not_forever_queued(source, env, monkeypatch):
    def fail(_): raise RuntimeError('worker unavailable')
    monkeypatch.setattr(service, '_start_worker', fail)
    with pytest.raises(RuntimeError): imported()
    assert imported()['generation_status'] == 'failed'

def test_manual_group_cannot_be_imported_as_automatic(source):
    source['artifacts']['generatedCaseGroups'] = {'manual_cases': [{'id': 'C001'}]}
    with pytest.raises(ValueError): imported()

@pytest.mark.parametrize('manual', [None, 3, 'manual', {}])
def test_malformed_manual_group_fails_closed(source, manual):
    source['artifacts']['generatedCaseGroups'] = {'manual_cases': manual}
    with pytest.raises(ValueError): imported()

def test_missing_expected_with_full_question_list_fails_without_losing_source(source, env, monkeypatch):
    from test_ui_case_draft_service import proposal
    source['artifacts']['generatedCases']['cases'][0].pop('expected')
    result = proposal(); result['questions'] = [f'待确认问题{i}' for i in range(100)]
    monkeypatch.setattr(service.ai_skill_service, 'run_ai_skill', lambda *a, **kw: result)
    draft = imported(); env[1].pop()()
    loaded = imported()
    assert loaded['generation_status'] == 'failed'
    assert loaded['source_snapshot'] == draft['source_snapshot']
    assert len(loaded['questions']) <= 100

@pytest.mark.parametrize('manual_source', ['generated', 'validation'])
def test_partial_upper_groups_cannot_hide_manual_case(source, manual_source):
    from task_server.services.requirement_draft_import import import_refs
    source['artifacts']['generatedCaseGroups'] = {'executable_cases': [{'id': 'other'}]}
    if manual_source == 'validation':
        source['artifacts']['yamlValidation'] = {'executionGroups': {'manual_cases': [{'id':'C001'}]}}
    else:
        source['artifacts']['yamlValidation'] = {'executionGroups': {'draft_cases': []}}
        source['artifacts']['generatedCases']['manual_cases'] = [{'id':'C001'}]
    with pytest.raises(ValueError, match='requirement_import_manual'): imported()
    assert import_refs(source) == []

def test_explicit_empty_manual_group_matches_ui_precedence(source, env):
    source['artifacts']['generatedCaseGroups'] = {'manual_cases': []}
    source['artifacts']['generatedCases']['manual_cases'] = [{'id':'C001'}]
    imported(); env[1].pop()()

@pytest.mark.parametrize('action', [[], {}, ['aiTap'], {'name':'aiTap'}, None, 3])
def test_malformed_structured_action_has_controlled_failure(source, action):
    from task_server.services.requirement_draft_import import import_refs
    source['artifacts']['generatedCases']['cases'][0]['steps'] = [{'action': action, 'target': '我的'}]
    with pytest.raises(ValueError, match='requirement_import_unsupported'): imported()
    assert import_refs(source) == []
