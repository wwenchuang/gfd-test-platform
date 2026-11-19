"""Read-only failure evidence, using an isolated SQL store and UI batch fixture."""
import copy
from datetime import datetime, timedelta, timezone
import importlib
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from task_server.services.operation_attribution import actor_context
from tests.test_ui_execution_service import environment, ACTORS, request as ui_request


def adapter():
    return importlib.import_module('task_server.services.failure_analysis_sources').EvidenceSource


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def api(tmp_path, monkeypatch):
    from task_server import identity
    from task_server.api_testing.models.case import ApiCase, ApiCaseVersion
    from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
    from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
    from task_server.api_testing.models.project import ApiProject
    engine = create_engine('sqlite:///' + str(tmp_path / 'evidence.sqlite'))
    # Exercise real ORM predicates without requiring any live PostgreSQL service.
    models = [ApiProject, ApiEnvironment, ApiEnvironmentRevision, ApiCase, ApiCaseVersion, ApiExecution, ApiExecutionCase]
    with engine.begin() as db:
        for model in models:
            columns = ','.join('"' + col.name + '" ' + ('INTEGER' if col.type.__class__.__name__ == 'Integer' else 'TEXT')
                               + (' PRIMARY KEY' if col.primary_key else '') for col in model.__table__.columns)
            db.exec_driver_sql('CREATE TABLE ' + model.__tablename__ + '(' + columns + ')')
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    actor = {'kind': 'user', 'username': 'reader', 'user_id': 'u-reader', 'display_name': 'Reader'}
    project, env, revision, version, case, endpoint, source = [uid() for _ in range(7)]
    profile = dict(actor, status='active', must_change_password=False, is_superuser=False,
                   permissions=['api.view'], scope={'api_projects': [project], 'api_environments': [env]})
    monkeypatch.setattr(identity, 'get_access_profile', lambda name: copy.deepcopy(profile) if name == 'reader' else None)
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    def audit():
        return dict(owner_id='owner', created_by='owner', updated_by='owner', created_at=now, updated_at=now, row_version=1)
    with factory.begin() as db:
        db.add(ApiProject(id=project, name='Project', slug='project', settings={}, **audit()))
        db.add(ApiEnvironment(id=env, project_id=project, name='QA', **audit()))
        db.add(ApiEnvironmentRevision(id=revision, environment_id=env, revision_number=1, name='QA', default_headers={}, **audit()))
        db.add(ApiCase(id=case, project_id=project, endpoint_id=endpoint, name='读取列表', origin='manual', **audit()))
        db.add(ApiCaseVersion(id=version, case_id=case, endpoint_id=endpoint, version_number=1, purpose='query', request_template={}, **audit()))
    def add(*, age=0, status='FAILED', run_changes=None, child_changes=None, payload=None):
        rid, cid = uid(), uid()
        run = dict(id=rid, project_id=project, source_revision_id=source, environment_revision_id=revision,
                   state='DONE', execution_type='debug', idempotency_key=rid, requested_case_ids=[version],
                   request_snapshot={}, summary={}, started_at=now + timedelta(hours=age), finished_at=now + timedelta(hours=age),
                   **{**audit(), 'created_at': now + timedelta(hours=age)})
        run.update(run_changes or {})
        child = dict(id=cid, execution_id=rid, case_version_id=version, endpoint_id=endpoint,
                     environment_revision_id=run['environment_revision_id'], ordinal=1, status=status,
                     failure_category='assertion', duration_ms=12, sanitized_result=payload or {
                         'request': {'method':'GET', 'path':'/items'}, 'response': {'status_code':200, 'body':{'code':0}},
                         'assertions':[{'type':'jsonpath', 'passed':False, 'expected':1, 'actual':0}],
                         'trace':[{'stage':'main','status':'failed'}], 'error_message':'Expected one item'}, **audit())
        child.update(child_changes or {})
        with factory.begin() as db:
            db.add(ApiExecution(**run)); db.add(ApiExecutionCase(**child))
        return rid, cid
    run_id, child_id = add()
    yield SimpleNamespace(factory=factory, actor=actor, profile=profile, add=add, run_id=run_id,
                          child_id=child_id, version=version, revision=revision, project=project)
    engine.dispose()


def test_api_failed_steps_are_actual_bounded_and_run_bound(api):
    other_run, other_child = api.add(age=-1)
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
        result = source.get_failed_steps()
        assert result['cases'][0]['case_id'] == api.child_id
        assert result['cases'][0]['status'] == 'FAILED'
        assert result['evidence'] and all(api.run_id in item['href'] for item in result['evidence'])
        assert all(item['id'] and item['kind'] and item['title'] for item in result['evidence'])
        with pytest.raises(LookupError):
            source.get_request_trace(other_child)
        with pytest.raises(LookupError):
            source.compare_last_pass(other_child)


def test_api_scope_is_rechecked_on_every_tool_and_trusted_actor_required(api):
    with pytest.raises(PermissionError):
        adapter()(api.actor, 'api', api.run_id, session_factory=api.factory).authorize()
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
        source.get_request_trace(api.child_id)
        api.profile['permissions'] = []
        for invoke in (source.get_failed_steps, lambda: source.get_request_trace(api.child_id), lambda: source.compare_last_pass(api.child_id)):
            with pytest.raises(PermissionError):
                invoke()


def test_trusted_current_actor_mapping_is_accepted(api):
    from task_server.services.operation_attribution import current_actor
    with actor_context(api.actor):
        source = adapter()(current_actor(), 'api', api.run_id, session_factory=api.factory)
        assert source.get_failed_steps()['cases'][0]['case_id'] == api.child_id


def test_api_environment_revocation_and_unknown_identity_deny(api):
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
        api.profile['scope']['api_environments'] = []
        with pytest.raises(LookupError):
            source.get_failed_steps()
        api.profile['scope']['api_environments'] = ['*']
        api.profile['user_id'] = 'recreated-user'
        with pytest.raises(PermissionError):
            source.authorize()


def test_api_redaction_covers_repeated_secrets_and_ignores_ai_analysis(api):
    payload = {'request': {'headers': {'Authorization':'Bearer secret-fixture', 'X-API-Key':'key-fixture'},
                           'path':'/items?token=url-fixture'},
               'response': {'body': {'password':'password-fixture'}},
               'trace': [{'message':'key-fixture password-fixture Bearer secret-fixture token=url-fixture'}],
               'failure_analysis': {'reason':'FAKE PREVIOUS AI CONCLUSION'},
               'error_message':'x' * 30000}
    rid, cid = api.add(payload=payload)
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', rid, session_factory=api.factory).get_request_trace(cid)
    encoded = json.dumps(result)
    assert all(value not in encoded for value in ('secret-fixture','key-fixture','password-fixture','url-fixture','FAKE PREVIOUS'))
    assert len(encoded) < 24000


def test_failed_steps_redacts_secret_copied_from_request_into_assertion(api):
    rid, _ = api.add(payload={'request': {'headers': {'X-API-Key':'repeated-private-fixture'}},
                            'assertions': [{'passed':False, 'actual':'repeated-private-fixture'}],
                            'error_message':'Observed repeated-private-fixture'})
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', rid, session_factory=api.factory).get_failed_steps()
    assert 'repeated-private-fixture' not in json.dumps(result)


def test_trace_redacts_basic_auth_and_full_cookie_lines(api):
    rid, cid = api.add(payload={'trace':[{'message':'Authorization: Basic abc-private-fixture\nCookie: sid=cookie-private-fixture; session=second-private-fixture'}]})
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', rid, session_factory=api.factory).get_request_trace(cid)
    assert 'private-fixture' not in json.dumps(result)


def test_run_secret_context_redacts_across_cases(api):
    from sqlalchemy import select
    from task_server.api_testing.models.execution import ApiExecutionCase
    with api.factory.begin() as db:
        first = db.scalar(select(ApiExecutionCase).where(ApiExecutionCase.id == api.child_id))
        first.sanitized_result = {'response': {'body': {'access_token':'cross-case-private-fixture'}}}
        values = {column.name:getattr(first, column.name) for column in ApiExecutionCase.__table__.columns}
        second = uid()
        values.update(id=second, ordinal=2, sanitized_result={'error_message':'observed cross-case-private-fixture'})
        db.add(ApiExecutionCase(**values))
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
        for result in (source.get_failed_steps(), source.get_request_trace(second)):
            assert 'cross-case-private-fixture' not in json.dumps(result)


def test_removed_secret_metadata_still_redacts_trace_and_comparison(api):
    payload = {'extracted_variables': {'access_token':'metadata-private-fixture'},
               'trace':[{'message':'received metadata-private-fixture'}]}
    api.add(age=-1, status='PASSED', payload=payload)
    rid, cid = api.add(payload=payload)
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', rid, session_factory=api.factory)
        for result in (source.get_request_trace(cid), source.compare_last_pass(cid)):
            encoded = json.dumps(result)
            assert 'metadata-private-fixture' not in encoded
            assert 'extracted_variables' not in encoded


def test_incomplete_secret_context_never_emits_unredacted_free_text(api, monkeypatch):
    module = importlib.import_module('task_server.services.failure_analysis_sources')
    monkeypatch.setattr(module, 'SECRET_CONTEXT_BYTES', 10)
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
        for result in (source.get_failed_steps(), source.get_request_trace(api.child_id), source.compare_last_pass(api.child_id)):
            assert 'secret_context_incomplete' in result['missing']
            assert result['evidence'] == []
            assert 'Expected one item' not in json.dumps(result)


def test_failed_cases_and_total_tool_output_are_bounded_and_queries_are_readonly(api):
    from sqlalchemy import event, select
    from task_server.api_testing.models.execution import ApiExecutionCase
    with api.factory.begin() as db:
        seed = db.scalar(select(ApiExecutionCase).where(ApiExecutionCase.id == api.child_id))
        for index in range(1, 31):
            values = {column.name:getattr(seed, column.name) for column in ApiExecutionCase.__table__.columns}
            values.update(id=uid(), ordinal=index + 1, sanitized_result={
                'assertions':[{'passed':False, 'actual':'huge value ' * 3000} for _ in range(5)]})
            db.add(ApiExecutionCase(**values))
    statements = []
    @event.listens_for(api.factory.kw['bind'], 'before_cursor_execute')
    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory).get_failed_steps()
    assert len(result['cases']) == 25
    assert 'failed_cases_truncated_at_25' in result['missing']
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 40000
    assert statements and all(statement.lstrip().upper().startswith('SELECT') for statement in statements)


def test_last_pass_only_matches_prior_same_case_source_project_endpoint_environment(api):
    correct_run, correct_child = api.add(age=-10, status='PASSED')
    for options in (
        {'age':1}, {'age':-1, 'run_changes':{'environment_revision_id':uid()}},
        {'age':-2, 'run_changes':{'source_revision_id':uid()}},
        {'age':-3, 'run_changes':{'project_id':uid()}},
        {'age':-4, 'child_changes':{'case_version_id':uid()}},
        {'age':-5, 'child_changes':{'endpoint_id':uid()}},
        {'age':-6, 'run_changes':{'state':'ARCHIVED'}},
        {'age':-7, 'child_changes':{'environment_revision_id':uid()}},
        {'age':-8, 'run_changes':{'finished_at':datetime(2026,11,19,tzinfo=timezone.utc)}},
    ):
        api.add(status='PASSED', **options)
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory).compare_last_pass(api.child_id)
    assert result['comparable'] is True
    assert result['previous_run_id'] == correct_run
    assert correct_child in json.dumps(result)


def test_permissions_revoked_during_evidence_read_discard_result(api, monkeypatch):
    with actor_context(api.actor):
        source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
        read = source._api_case
        def revoke(*args, **kwargs):
            result = read(*args, **kwargs)
            api.profile['permissions'] = []
            return result
        monkeypatch.setattr(source, '_api_case', revoke)
        with pytest.raises(PermissionError):
            source.get_request_trace(api.child_id)


def test_no_last_pass_does_not_invent_history(api):
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory).compare_last_pass(api.child_id)
    assert result['comparable'] is False
    assert 'prior_comparable_pass_not_found' in result['missing']


def test_last_success_selects_latest_completion_when_runs_overlap(api):
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    latest_finish, _ = api.add(age=-10, status='PASSED', run_changes={'finished_at': now - timedelta(hours=1)})
    api.add(age=-5, status='PASSED', run_changes={'finished_at': now - timedelta(hours=4)})
    with actor_context(api.actor):
        result = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory).compare_last_pass(api.child_id)
    assert result['previous_run_id'] == latest_finish


def test_ui_missing_network_and_environment_are_explicit_and_ownership_enforced(environment):
    from task_server.services.ui_execution_service import create_ui_batch
    with actor_context(ACTORS['b']):
        batch = create_ui_batch(ACTORS['b'], ui_request(), 'analysis-read')
        source = adapter()(ACTORS['b'], 'ui', batch['ui_batch_id'])
        child = batch['case_attempts'][0]['attempt_id']
        result = source.get_request_trace(child)
        assert 'network_trace_not_collected' in result['missing']
        comparison = source.compare_last_pass(child)
        assert comparison['comparable'] is False
        assert 'environment_identity_unavailable' in comparison['missing']
        assert source.get_failed_steps()['cases'] == []
        with pytest.raises(LookupError):
            source.get_request_trace('uia_' + 'f' * 32)
    with actor_context(ACTORS['a']):
        with pytest.raises(LookupError):
            adapter()(ACTORS['a'], 'ui', batch['ui_batch_id']).authorize()


def test_ui_unknown_step_and_input_value_in_log_are_not_exposed(environment):
    from task_server.services.ui_execution_service import create_ui_batch
    from task_server.services.ui_execution_store import UiExecutionStore
    with actor_context(ACTORS['b']):
        batch = create_ui_batch(ACTORS['b'], ui_request(), 'unknown-step')
        store = UiExecutionStore()
        db = store._connect()
        try:
            db.execute("UPDATE snapshots SET source_yaml=? WHERE ui_batch_id=?", (
                'tasks:\n- flow:\n  - aiInput:\n      value: typed-private-fixture\n      locate: username\n', batch['ui_batch_id']))
            db.execute("UPDATE case_attempts SET status='failed',result_json=? WHERE ui_batch_id=?", (
                json.dumps({'stderr':'typed-private-fixture token=log-private-fixture',
                            'local_report_path':'/private/credential-path',
                            'report_url':'https://untrusted.invalid/proof'}), batch['ui_batch_id']))
            db.commit()
        finally:
            db.close()
        source = adapter()(ACTORS['b'], 'ui', batch['ui_batch_id'])
        result = source.get_failed_steps()
        assert any(reason.startswith('step_progress_not_recorded:') for reason in result['missing'])
        assert result['evidence'][0]['data']['failed_steps'] == []
        trace = source.get_request_trace(batch['case_attempts'][0]['attempt_id'])
    encoded = json.dumps(trace)
    assert all(value not in encoded for value in ('typed-private-fixture','log-private-fixture','credential-path','untrusted.invalid'))
    assert trace['evidence'][0]['href'] == '/task-manager.html?ui_batch=' + batch['ui_batch_id']


def test_ui_incomplete_input_secret_context_does_not_emit_logs(environment, monkeypatch):
    from task_server.services.ui_execution_service import create_ui_batch
    from task_server.services.ui_execution_store import UiExecutionStore
    module = importlib.import_module('task_server.services.failure_analysis_sources')
    with actor_context(ACTORS['b']):
        batch = create_ui_batch(ACTORS['b'], ui_request(), 'input-budget')
        db = UiExecutionStore()._connect()
        try:
            db.execute("UPDATE case_attempts SET status='failed',result_json=? WHERE ui_batch_id=?", (
                json.dumps({'stderr':'private text without field label'}), batch['ui_batch_id']))
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(module, 'SECRET_CONTEXT_BYTES', 10)
        source = adapter()(ACTORS['b'], 'ui', batch['ui_batch_id'])
        for result in (source.get_failed_steps(), source.get_request_trace(batch['case_attempts'][0]['attempt_id'])):
            assert 'secret_context_incomplete' in result['missing']
            assert result['evidence'] == []


@pytest.mark.parametrize('source,run_id', [('oops',uid()),('api','../report'),('ui','uib_not-valid')])
def test_invalid_source_and_identifiers_rejected(source, run_id):
    with pytest.raises(ValueError):
        adapter()(ACTORS['b'], source, run_id)


def test_device_logs_match_only_authorized_execution(api, monkeypatch):
    from sqlalchemy import text
    source = adapter()(api.actor, 'api', api.run_id, session_factory=api.factory)
    with actor_context(api.actor):
        monkeypatch.delenv('MIDSCENE_DEVICE_LOG_VIEW_ENABLED', raising=False)
        assert source.get_device_logs(api.child_id)['missing']
        monkeypatch.setenv('MIDSCENE_DEVICE_LOG_VIEW_ENABLED', '1')
        assert source.get_device_logs(api.child_id)['evidence'] == []
        with api.factory.begin() as db:
            db.execute(text('CREATE TABLE agent_device_log_events (project_id, environment_revision_id, execution_id, execution_case_id, event_id, event_time, stage, error_code)'))
            for project in (api.project, 'other-project'):
                db.execute(text("INSERT INTO agent_device_log_events VALUES (:p,:e,:r,:c,:p,'12:01','received',null)"),
                           dict(p=project, e=api.revision, r=api.run_id, c=api.child_id))
        records = source.get_device_logs(api.child_id)['evidence'][0]['data']
        assert [row['event_id'] for row in records] == [api.project]
        with pytest.raises(LookupError):
            source.get_device_logs(uid())
