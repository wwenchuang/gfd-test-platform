"""Exercise practice evidence against real PostgreSQL API scope predicates."""
import json
from contextlib import contextmanager

from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
from task_server.services import practice_cleanup_evidence as service, week2_real
from tests.api_testing.test_access import identities
from tests.api_testing.test_http_contract import _audit, api_context, owned_records


def test_evidence_uses_project_environment_and_execution_case_scope(api_context, owned_records, identities, tmp_path, monkeypatch):
    @contextmanager
    def slot(_):
        yield 1
    monkeypatch.setattr(week2_real, '_history_slot', slot)
    monkeypatch.setattr(service, '_invoke_authorization',
                        lambda owner, username, sources, *_args, **_kwargs: service._verify_sources(owner, username, sources))
    records = owned_records
    with api_context['factory'].begin() as session:
        other_env = ApiEnvironment(project_id=records['project'].id, source_id=records['source'].id,
                                   name='second env', **_audit('owner-a'))
        session.add(other_env); session.flush()
        revision = ApiEnvironmentRevision(environment_id=other_env.id, source_revision_id=records['revision'].id,
                                         revision_number=1, name='second env', **_audit('owner-a'))
        session.add(revision); session.flush()
        second = ApiExecution(project_id=records['project'].id, source_revision_id=records['revision'].id,
                              environment_revision_id=revision.id, execution_type='debug', state='DONE',
                              idempotency_key='scope-evidence', requested_case_ids=[records['version'].id],
                              request_snapshot={}, **_audit('owner-a'))
        session.add(second); session.flush()
        cases = []
        for execution, environment in [(records['execution'], records['environment_revision']), (second, revision)]:
            case = ApiExecutionCase(execution_id=execution.id, case_version_id=records['version'].id,
                                    endpoint_id=records['endpoint'].id, environment_revision_id=environment.id,
                                    ordinal=0, status='PASSED', sanitized_result={}, **_audit('owner-a'))
            session.add(case); session.flush(); cases.append(case.id)
        mismatched = ApiExecutionCase(execution_id=records['execution'].id, case_version_id=records['version'].id,
                                      endpoint_id=records['endpoint'].id, environment_revision_id=revision.id,
                                      ordinal=1, status='PASSED', sanitized_result={}, **_audit('owner-a'))
        session.add(mismatched); session.flush(); mismatch_id = mismatched.id
        second_id, other_environment_id = second.id, other_env.id

    profile = identities['reader']
    profile.update(user_id='reader-id', scope={'ui_apps':'*', 'api_projects':[records['project'].id],
                                               'api_environments':[records['environment'].id, other_environment_id]})
    profile['permissions'].append('platform.configure')
    monkeypatch.setattr(service.identity, 'get_access_profile', lambda username: profile if username == 'reader' else None)
    def diagnostics(execution_id, case_ids):
        return {'scope':'snapshot_hits_only', 'membership_revalidated':False,
                'history_source':'same_schedule_and_exact_version', 'count':len(case_ids),
                'items':[{'cleanup_structure':{'steps':[{'index':0,'enabled':True,'extraction_count':1}]},
                          'latest_scheduled_history':{'execution_id':execution_id, 'execution_case_id':case_id,
                            'created_at':'2026-11-09T00:00:00+00:00','case_status':'PASSED',
                            'cleanup_trace':[{'index':0,'attempts':[{'status':'PASSED','output_state':'nonempty_recorded'}]}]}}
                         for case_id in case_ids]}
    path = tmp_path / 'diagnostic.json'
    path.write_text(json.dumps(diagnostics(records['execution'].id, [cases[0]]))); path.chmod(0o600)
    monkeypatch.setenv('TASK_PRACTICE_CLEANUP_EVIDENCE', str(path))
    # If this were to perform HTTP business execution, this test would fail.
    monkeypatch.setattr('task_server.api_testing.http._get', lambda *_: (_ for _ in ()).throw(AssertionError('business call')))
    assert service.get_evidence('reader-id', 'reader')['execution_id'] == records['execution'].id
    profile['scope']['api_projects'] = [records['other_project'].id]
    assert service.get_evidence('reader-id', 'reader') is None
    profile['scope']['api_projects'] = [records['project'].id]
    profile['scope']['api_environments'] = [other_environment_id]
    assert service.get_evidence('reader-id', 'reader') is None
    path.write_text(json.dumps(diagnostics(second_id, [cases[1]])))
    assert service.get_evidence('reader-id', 'reader')['execution_id'] == second_id
    profile['scope']['api_environments'] = [records['environment'].id]
    assert service.get_evidence('reader-id', 'reader') is None
    profile['scope']['api_environments'].append(other_environment_id)
    # Both environments are authorized, but this case's environment differs from its parent.
    path.write_text(json.dumps(diagnostics(records['execution'].id, [mismatch_id])))
    assert service.get_evidence('reader-id', 'reader') is None
    # Both cases exist and are authorized, but the second belongs to another run.
    path.write_text(json.dumps(diagnostics(records['execution'].id, cases)))
    assert service.get_evidence('reader-id', 'reader') is None
