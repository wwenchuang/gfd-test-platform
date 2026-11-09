"""Project and environment scope for legacy historical evidence."""
from contextlib import contextmanager

from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecutionCase
from task_server.services import practice_legacy_evidence as service, week2_real
from tests.api_testing.test_access import identities
from tests.api_testing.test_http_contract import _audit, api_context, owned_records
from tests.test_identity import identity_db, create_member, activate


def test_legacy_records_require_project_and_environment_and_all_children_match(api_context, owned_records, identities, monkeypatch):
    records = owned_records
    with api_context['factory'].begin() as session:
        other = ApiEnvironment(project_id=records['project'].id, source_id=records['source'].id,
                               name='other', **_audit('owner-a'))
        session.add(other); session.flush()
        rev = ApiEnvironmentRevision(environment_id=other.id, source_revision_id=records['revision'].id,
                                     revision_number=1, name='other', **_audit('owner-a'))
        session.add(rev); session.flush()
        child = ApiExecutionCase(execution_id=records['execution'].id, case_version_id=records['version'].id,
                                 endpoint_id=records['endpoint'].id, environment_revision_id=records['environment_revision'].id,
                                 ordinal=0, status='PASSED', sanitized_result={}, **_audit('owner-a'))
        session.add(child); session.flush()
        child_id, other_id, other_rev = child.id, other.id, rev.id
    profile = identities['reader']
    profile.update(user_id='reader-id', scope={'ui_apps':'*','api_projects':[records['project'].id],
                                               'api_environments':[records['environment'].id,other_id]})
    profile['permissions'].append('platform.configure')
    monkeypatch.setattr(service.identity,'get_access_profile',lambda username:profile if username=='reader' else None)
    monkeypatch.setattr(service,'_RECORDS',({'key':'feedback','execution_id':records['execution'].id,'cases':1,'summary':'secret'},))
    @contextmanager
    def slot(_): yield 1
    monkeypatch.setattr(week2_real,'_history_slot',slot)
    monkeypatch.setattr(service,'_invoke_authorization',lambda owner,username,sources,*args,**kwargs:service._verify_sources(owner,username,sources))
    assert len(service.get_records('reader-id','reader')) == 1
    profile['scope']['api_projects']=[records['other_project'].id]
    assert service.get_records('reader-id','reader') == []
    profile['scope']['api_projects']=[records['project'].id]
    profile['scope']['api_environments']=[other_id]
    assert service.get_records('reader-id','reader') == []
    profile['scope']['api_environments'].append(records['environment'].id)
    with api_context['factory'].begin() as session:
        session.get(ApiExecutionCase,child_id).environment_revision_id=other_rev
    assert service.get_records('reader-id','reader') == []


def test_actual_authorization_subprocess_respects_current_scope(api_context, owned_records, identity_db, monkeypatch):
    records = owned_records
    identity, store = identity_db
    store.create_role('admin', {'id':'legacy-reader', 'name':'reader', 'permissions':['platform.configure','api.view']})
    scope={'ui_apps':'*', 'api_projects':[records['project'].id], 'api_environments':[records['environment'].id]}
    create_member(store, username='reader', role_ids=['legacy-reader'], scope=scope); activate(store,'reader')
    owner=identity.get_access_profile('reader')['user_id']
    with api_context['factory'].begin() as session:
        child=ApiExecutionCase(execution_id=records['execution'].id,case_version_id=records['version'].id,
                               endpoint_id=records['endpoint'].id,environment_revision_id=records['environment_revision'].id,
                               ordinal=0,status='PASSED',sanitized_result={},**_audit('owner-a'))
        session.add(child)
    monkeypatch.setenv('API_TESTING_ENABLED','1')
    monkeypatch.setenv('API_TESTING_DATABASE_URL',api_context['factory'].kw['bind'].url.render_as_string(hide_password=False))
    monkeypatch.setenv('API_TESTING_SECRET_KEY','test-local-secret-abcdefghijklmnopqrstuvwxyz-2026')
    source=[{'execution_id':records['execution'].id,'cases':1}]
    with week2_real._history_slot(None) as slot:
        assert service._invoke_authorization(owner,'reader',source,15,slot)==[True]
    store.update_user('admin','reader',{'scope':{'ui_apps':'*','api_projects':[records['other_project'].id],
                                                 'api_environments':[records['environment'].id]}})
    with week2_real._history_slot(None) as slot:
        assert service._invoke_authorization(owner,'reader',source,15,slot)==[False]
