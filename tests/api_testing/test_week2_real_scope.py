"""Authorization integration against the isolated API-testing PostgreSQL fixture."""
import pytest
import json

from task_server.services import week2_real
from task_server.api_testing.http import ApiHttpError
from tests.api_testing.test_access import api_context, owned_records, identities, _passing_evidence
from tests.test_identity import identity_db, create_member, activate


def test_existing_platform_scope_is_enforced_for_week2(api_context, owned_records, identities):
    evidence_id = _passing_evidence(api_context['factory'], owned_records)
    execution_id = owned_records['execution'].id
    week2_real._scope_authorized('reader', execution_id, evidence_id)
    identities['reader']['scope']['api_environments'] = []
    with pytest.raises(ApiHttpError) as denied:
        week2_real._scope_authorized('reader', execution_id, evidence_id)
    assert denied.value.status == 404
    identities['reader']['scope']['api_environments'] = [owned_records['environment'].id]
    identities['reader']['scope']['api_projects'] = [owned_records['other_project'].id]
    with pytest.raises(ApiHttpError) as denied:
        week2_real._scope_authorized('reader', execution_id, evidence_id)
    assert denied.value.status == 404
    identities['reader']['scope']['api_projects'] = [owned_records['project'].id]
    with pytest.raises(ValueError):
        week2_real._scope_authorized('reader', owned_records['second_execution'].id, evidence_id)


def test_contract_scope_checks_real_project_environment_and_source(api_context, owned_records, identities):
    from task_server.api_testing.models.source import ApiSourceEndpoint
    evidence_id = _passing_evidence(api_context['factory'], owned_records)
    execution_id = owned_records['execution'].id
    ref = '#/components/schemas/Result'
    operation = {'responses': {'200': {'content': {'application/json': {'schema': {'$ref': ref}}}}},
                 'resolved_dependencies': {ref: {'type':'object', 'required':['code','msg','data']}}}
    with api_context['factory'].begin() as session:
        endpoint = session.get(ApiSourceEndpoint, owned_records['endpoint'].id)
        endpoint.method = 'GET'
        endpoint.path = '/print3d/api/v1/feedback/category/first'
        endpoint.operation = operation
    assert week2_real._contract_operation_authorized('reader', execution_id, evidence_id) == operation
    identities['reader']['scope']['api_environments'] = []
    with pytest.raises(ApiHttpError):
        week2_real._contract_operation_authorized('reader', execution_id, evidence_id)


def test_real_subprocess_rechecks_identity_and_project_environment_scope(api_context, owned_records, identity_db, monkeypatch):
    from task_server.api_testing.models.execution import ApiExecutionCase
    identity, store = identity_db
    store.create_role('admin', {'id':'week2-reader', 'name':'week2 reader', 'permissions':['api.view','platform.configure']})
    scope = {'ui_apps': '*', 'api_projects': [owned_records['project'].id],
             'api_environments': [owned_records['environment'].id]}
    create_member(store, username='w2reader', role_ids=['week2-reader'], scope=scope)
    activate(store, 'w2reader')
    owner = identity.get_access_profile('w2reader')['user_id']
    case_id = _passing_evidence(api_context['factory'], owned_records)
    execution_id = owned_records['execution'].id
    with api_context['factory'].begin() as session:
        child = session.get(ApiExecutionCase, case_id)
        child.sanitized_result = {'status':'PASSED',
            'request': {'method':'GET', 'url':'https://print.wisebeginner3d.com/app/print3d/api/v1/feedback/category/first'},
            'response': {'status_code':200, 'body':json.dumps({'code':0,'data':[
                {'sn':'sample','name':'sample','children':[],'order_related':False}]})},
            'assertions':[{'type':'status_code','passed':True},{'type':'json_path','passed':True},{'type':'schema','passed':True}]}
    db_url = api_context['factory'].kw['bind'].url.render_as_string(hide_password=False)
    monkeypatch.setenv('API_TESTING_DATABASE_URL', db_url)
    monkeypatch.setenv('API_TESTING_ENABLED', '1')
    monkeypatch.setenv('API_TESTING_SECRET_KEY', 'isolated-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz')
    sources = [(execution_id, case_id)]
    assert week2_real._invoke_authorization(owner, 'w2reader', sources, 10) == [True]
    assert week2_real._invoke_authorization('different-owner', 'w2reader', sources, 10) == [False]
    result = week2_real.run(owner, 'w2reader', execution_id=execution_id, case_id=case_id)
    assert result['status'] == 'passed' and result['total'] == 17
    scope['api_environments'] = []
    store.update_user('admin', 'w2reader', {'scope':scope})
    assert week2_real._invoke_authorization(owner, 'w2reader', sources, 10) == [False]
    history = week2_real.list_results(owner, username='w2reader')
    assert len(history) == 1 and history[0]['status'] == 'blocked' and 'checks' not in history[0]
    scope['api_environments'] = [owned_records['environment'].id]
    scope['ui_apps'] = ['one.app']
    store.update_user('admin', 'w2reader', {'scope':scope})
    assert week2_real._invoke_authorization(owner, 'w2reader', sources, 10) == [False]
    scope['ui_apps'] = '*'
    store.create_role('admin', {'id':'week2-no-config', 'name':'no config', 'permissions':['api.view']})
    store.update_user('admin', 'w2reader', {'scope':scope, 'role_ids':['week2-no-config']})
    assert week2_real._invoke_authorization(owner, 'w2reader', sources, 10) == [False]
    store.create_role('admin', {'id':'week2-no-api', 'name':'no api', 'permissions':['platform.configure']})
    store.update_user('admin', 'w2reader', {'scope':scope, 'role_ids':['week2-no-api']})
    assert week2_real._invoke_authorization(owner, 'w2reader', sources, 10) == [False]

from task_server import router
from task_server.api_testing import http
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecutionCase
from tests.api_testing.test_http_contract import _audit, http_client


@pytest.mark.parametrize('other_environment_authorized', [False, True])
def test_parent_child_environment_mismatch_is_hidden_even_if_both_are_authorized(
        api_context, owned_records, identities, http_client, tmp_path, monkeypatch, other_environment_authorized):
    records = owned_records
    with api_context['factory'].begin() as session:
        other = ApiEnvironment(project_id=records['project'].id, source_id=records['source'].id,
                               name='second', **_audit('owner-a'))
        session.add(other); session.flush()
        revision = ApiEnvironmentRevision(environment_id=other.id, source_revision_id=records['revision'].id,
                                          revision_number=1, name='second', **_audit('owner-a'))
        session.add(revision); session.flush()
        valid = ApiExecutionCase(execution_id=records['execution'].id, case_version_id=records['version'].id,
                                 endpoint_id=records['endpoint'].id,
                                 environment_revision_id=records['environment_revision'].id,
                                 ordinal=0, status='PASSED', sanitized_result={}, **_audit('owner-a'))
        mismatched = ApiExecutionCase(execution_id=records['execution'].id, case_version_id=records['version'].id,
                                      endpoint_id=records['endpoint'].id, environment_revision_id=revision.id,
                                      ordinal=1, status='PASSED', sanitized_result={}, **_audit('owner-a'))
        session.add_all((valid, mismatched)); session.flush()
        valid_id, mismatch_id, other_id = valid.id, mismatched.id, other.id
    execution_id = records['execution'].id
    profile = identities['reader']
    profile.update(user_id='reader-id', scope={'ui_apps':'*', 'api_projects':[records['project'].id],
                  'api_environments':[records['environment'].id] + ([other_id] if other_environment_authorized else [])})
    profile['permissions'].append('platform.configure')
    monkeypatch.setenv('TASK_AUTH_DB', str(tmp_path / 'identity.sqlite3'))
    monkeypatch.setattr(week2_real.identity, 'get_access_profile', lambda username: profile if username == 'reader' else None)
    monkeypatch.setattr(week2_real, '_analyze', lambda payload, execution, case:
                        {'status':'passed', 'execution_case_id':case, 'checks':[{'id':'private'}]})
    actual_get = http._get
    reads = []
    def record_read(segments, *args, **kwargs):
        reads.append(segments)
        return actual_get(segments, *args, **kwargs)
    monkeypatch.setattr(http, '_get', record_read)
    assert week2_real._verify_sources('reader-id', 'reader', [(execution_id, valid_id)]) == [True]
    assert week2_real._verify_sources('reader-id', 'reader', [(execution_id, mismatch_id)]) == [False]
    assert week2_real._derive('reader-id', 'reader', execution_id, valid_id)[0] is True
    reads.clear()
    assert week2_real._derive('reader-id', 'reader', execution_id, mismatch_id) == (False, None)
    assert ('executions', execution_id, 'cases', mismatch_id) not in reads

    # Exercise the real HTTP route and its final/history authorization while using
    # the test process in place of subprocesses so it sees the isolated schema.
    monkeypatch.setattr(router, '_week2_real_actor', lambda handler: ('reader-id', 'reader'))
    monkeypatch.setattr(week2_real, '_invoke_worker', lambda *args, **kwargs: week2_real._derive(*args[:4]))
    monkeypatch.setattr(week2_real, '_invoke_authorization', lambda owner, username, sources, timeout, **kwargs:
                        week2_real._verify_sources(owner, username, sources))
    response = http_client.post('/api/platform/week2-real', {'execution_id':execution_id, 'case_id':mismatch_id})
    assert response.status == 200 and response.body['result']['status'] == 'blocked'
    assert 'execution_id' not in response.body['result'] and 'execution_case_id' not in response.body['result']
    assert 'private' not in str(response.body)
    history = http_client.get('/api/platform/week2-real')
    assert history.status == 200 and history.body['results'][0]['status'] == 'blocked'
    assert 'execution_id' not in history.body['results'][0] and 'private' not in str(history.body)
