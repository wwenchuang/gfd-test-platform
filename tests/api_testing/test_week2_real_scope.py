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
