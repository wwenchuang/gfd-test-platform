"""Read-only execution provenance for one existing baseline."""

from uuid import uuid4

import pytest
from sqlalchemy import event

from task_server.api_testing import http
from task_server.api_testing.models.case import ApiBaseline
from task_server.api_testing.models.execution import ApiExecutionCase
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecution
from task_server.api_testing.models.source import ApiSourceEndpoint, ApiSourceRevision
from tests.api_testing.test_access import identities
from tests.api_testing.test_http_contract import api_context, owned_records, http_client, _audit, _auth


def _baseline(factory, records, *, result=None):
    with factory.begin() as session:
        child = ApiExecutionCase(
            execution_id=records['execution'].id,
            case_version_id=records['version'].id,
            endpoint_id=records['endpoint'].id,
            environment_revision_id=records['environment_revision'].id,
            ordinal=0, status='PASSED', sanitized_result=result or {}, **_audit('owner-a'),
        )
        session.add(child)
        session.flush()
        baseline = ApiBaseline(
            project_id=records['project'].id, case_id=records['case'].id,
            case_version_id=records['version'].id,
            environment_revision_id=records['environment_revision'].id,
            debug_execution_case_id=child.id, status='active', **_audit('owner-a'),
        )
        session.add(baseline)
        session.flush()
        return baseline.id, child.id


def test_selected_baseline_returns_only_safe_evidence_without_replaying(
    api_context, owned_records, identities, http_client,
):
    result = {
        'request': {'headers': {'Authorization': 'secret-token'}},
        'response': {'status_code': 400, 'body': 'private-body'},
        'assertions': [{'type': 'status_code', 'operator': 'equals', 'passed': True,
                        'actual': 400, 'expected': 400},
                       {'type': 'json_path', 'operator': 'equals', 'passed': True,
                        'actual': 'private-value', 'expected': 'private-value'}],
    }
    baseline_id, case_id = _baseline(api_context['factory'], owned_records, result=result)
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200, response.body
    data = response.body['data']
    assert data['baseline_id'] == baseline_id
    assert data['history_complete'] is False
    assert data['scan_limit'] == 100
    assert len(data['items']) == 1
    item = data['items'][0]
    assert item['execution_id'] == owned_records['execution'].id
    assert item['execution_case_id'] == case_id
    assert item['response_recorded'] is True
    assert item['assertions_recorded'] == 2
    assert item['assertions_passed'] == 2
    assert item['status'] == 'PASSED'
    assert 'secret-token' not in str(data)
    assert 'private-body' not in str(data)
    assert 'private-value' not in str(data)


def test_scope_revocation_and_unknown_baseline_fail_closed(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records)
    path = http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence'
    assert http_client.get(path, _auth('reader')).status == 200
    identities['reader']['scope']['api_environments'] = []
    assert http_client.get(path, _auth('reader')).status == 404
    identities['reader']['scope']['api_environments'] = [owned_records['environment'].id]
    identities['reader']['scope']['api_projects'] = [owned_records['other_project'].id]
    assert http_client.get(path, _auth('reader')).status == 404
    assert http_client.get(http.API_PREFIX + f'/baselines/{uuid4()}/execution-evidence', _auth('reader')).status == 404


def test_absent_response_is_not_claimed_as_executed_or_passed(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records,
        result={'request': {'method': 'GET'}, 'assertions': []})
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    item = response.body['data']['items'][0]
    assert item['response_recorded'] is False
    assert item['assertions_recorded'] == 0
    assert item['evidence_state'] == 'insufficient'


def test_mismatched_child_environment_is_excluded_even_when_both_are_authorized(
    api_context, owned_records, identities, http_client,
):
    baseline_id, original_id = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        env = ApiEnvironment(project_id=owned_records['project'].id,
                             source_id=owned_records['source'].id, name='other-env', **_audit('owner-a'))
        session.add(env); session.flush()
        revision = ApiEnvironmentRevision(environment_id=env.id,
            source_revision_id=owned_records['revision'].id, revision_number=1, name='other', **_audit('owner-a'))
        session.add(revision); session.flush()
        child = ApiExecutionCase(execution_id=owned_records['second_execution'].id,
            case_version_id=owned_records['version'].id, endpoint_id=owned_records['endpoint'].id,
            environment_revision_id=revision.id, ordinal=0, status='PASSED',
            sanitized_result={'response': {'status_code': 200}}, **_audit('owner-a'))
        session.add(child); session.flush()
        other_environment_id, mismatched_id = env.id, child.id
    identities['reader']['scope']['api_environments'].append(other_environment_id)
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert [item['execution_case_id'] for item in response.body['data']['items']] == [original_id]
    assert mismatched_id not in str(response.body)


def test_mismatched_source_and_endpoint_are_excluded(
    api_context, owned_records, identities, http_client,
):
    baseline_id, original_id = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        source_revision = ApiSourceRevision(source_id=owned_records['source'].id,
            revision_number=2, status='active', document_hash='f'*64,
            normalized_document={}, **_audit('owner-a'))
        session.add(source_revision); session.flush()
        endpoint = ApiSourceEndpoint(revision_id=source_revision.id, stable_key='d'*64,
            operation_id='other', method='GET', path='/other', normalized_path='/other',
            operation={}, **_audit('owner-a'))
        session.add(endpoint); session.flush()
        other = ApiExecution(project_id=owned_records['project'].id,
            source_revision_id=source_revision.id,
            environment_revision_id=owned_records['environment_revision'].id,
            execution_type='debug', state='DONE', idempotency_key='different-source',
            requested_case_ids=[owned_records['version'].id], request_snapshot={}, **_audit('owner-a'))
        session.add(other); session.flush()
        mismatch_source = ApiExecutionCase(execution_id=other.id, case_version_id=owned_records['version'].id,
            endpoint_id=owned_records['endpoint'].id, environment_revision_id=owned_records['environment_revision'].id,
            ordinal=0, status='PASSED', sanitized_result={}, **_audit('owner-a'))
        mismatch_endpoint = ApiExecutionCase(execution_id=owned_records['second_execution'].id,
            case_version_id=owned_records['version'].id, endpoint_id=endpoint.id,
            environment_revision_id=owned_records['environment_revision'].id, ordinal=0,
            status='PASSED', sanitized_result={}, **_audit('owner-a'))
        session.add_all((mismatch_source, mismatch_endpoint)); session.flush()
        invalid_ids = (mismatch_source.id, mismatch_endpoint.id)
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert [item['execution_case_id'] for item in response.body['data']['items']] == [original_id]
    assert all(identifier not in str(response.body) for identifier in invalid_ids)


def test_duplicate_children_do_not_count_as_separate_runs(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        session.add(ApiExecutionCase(execution_id=owned_records['execution'].id,
            case_version_id=owned_records['version'].id, endpoint_id=owned_records['endpoint'].id,
            environment_revision_id=owned_records['environment_revision'].id,
            ordinal=1, status='PASSED', sanitized_result={}, **_audit('owner-a')))
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert len(response.body['data']['items']) == 1


def test_failed_and_cancelled_records_remain_distinct_from_passing(
    api_context, owned_records, identities, http_client,
):
    baseline_id, original_id = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        child = session.get(ApiExecutionCase, original_id)
        child.status = 'FAILED'
        child.sanitized_result = {'response': {'status_code': 500},
                                  'assertions': [{'type': 'status_code', 'passed': False}]}
        session.get(ApiExecution, owned_records['execution'].id).state = 'DONE'
        other = session.get(ApiExecution, owned_records['second_execution'].id)
        other.state = 'CANCELLED'
        session.add(ApiExecutionCase(execution_id=other.id, case_version_id=owned_records['version'].id,
            endpoint_id=owned_records['endpoint'].id, environment_revision_id=owned_records['environment_revision'].id,
            ordinal=0, status='CANCELLED', sanitized_result={'request': {'method': 'GET'}}, **_audit('owner-a')))
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    items = {item['status']: item for item in response.body['data']['items']}
    assert items['FAILED']['assertions_passed'] == 0
    assert items['FAILED']['evidence_state'] == 'recorded'
    assert items['CANCELLED']['response_recorded'] is False
    assert items['CANCELLED']['evidence_state'] == 'insufficient'


def test_passed_case_with_failing_assertion_is_marked_inconsistent(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records,
        result={'response': {'status_code': 200},
                'assertions': [{'type': 'json_path', 'operator': 'equals', 'passed': False}]})
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    item = response.body['data']['items'][0]
    assert item['status'] == 'PASSED'
    assert item['assertions_recorded'] == 1
    assert item['assertions_passed'] == 0
    assert item['inconsistent'] is True
    assert item['evidence_state'] == 'insufficient'


def test_cleanup_failure_can_follow_successful_main_assertions_without_inconsistency(
    api_context, owned_records, identities, http_client,
):
    baseline_id, case_id = _baseline(api_context['factory'], owned_records,
        result={'response': {'status_code': 200},
                'assertions': [{'type': 'json_path', 'operator': 'equals', 'passed': True}]})
    with api_context['factory'].begin() as session:
        child = session.get(ApiExecutionCase, case_id)
        child.status = 'FAILED'
        child.failure_category = 'cleanup'
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    item = response.body['data']['items'][0]
    assert item['status'] == 'FAILED'
    assert item['assertions_passed'] == 1
    assert item['inconsistent'] is False
    assert item['evidence_state'] == 'recorded'


def test_dependency_or_archived_execution_is_not_reported_as_a_requested_run(
    api_context, owned_records, identities, http_client,
):
    baseline_id, original_id = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        dependency = session.get(ApiExecution, owned_records['second_execution'].id)
        dependency.requested_case_ids = []
        dependency_child = ApiExecutionCase(execution_id=dependency.id,
            case_version_id=owned_records['version'].id, endpoint_id=owned_records['endpoint'].id,
            environment_revision_id=owned_records['environment_revision'].id,
            ordinal=0, status='PASSED', sanitized_result={}, **_audit('owner-a'))
        session.add(dependency_child); session.flush(); dependency_id = dependency_child.id
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert [item['execution_case_id'] for item in response.body['data']['items']] == [original_id]
    assert dependency_id not in str(response.body)
    with api_context['factory'].begin() as session:
        session.get(ApiExecution, owned_records['execution'].id).state = 'ARCHIVED'
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert response.body['data']['items'] == []
    assert response.body['data']['history_complete'] is False


def test_bounded_recent_history_never_claims_completeness(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        executions = [ApiExecution(project_id=owned_records['project'].id,
            source_revision_id=owned_records['revision'].id,
            environment_revision_id=owned_records['environment_revision'].id,
            execution_type='debug', state='DONE', idempotency_key=f'bounded-{index}',
            requested_case_ids=[owned_records['version'].id], request_snapshot={}, **_audit('owner-a'))
            for index in range(101)]
        session.add_all(executions); session.flush()
        session.add_all([ApiExecutionCase(execution_id=execution.id,
            case_version_id=owned_records['version'].id, endpoint_id=owned_records['endpoint'].id,
            environment_revision_id=owned_records['environment_revision'].id,
            ordinal=0, status='PASSED', sanitized_result={}, **_audit('owner-a')) for execution in executions])
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert response.body['data']['scan_limit'] == 100
    assert response.body['data']['history_complete'] is False
    assert len(response.body['data']['items']) == 3
    assert len({item['execution_id'] for item in response.body['data']['items']}) == 3


def test_malformed_or_unbounded_assertions_are_not_presented_as_complete(
    api_context, owned_records, identities, http_client,
):
    baseline_id, case_id = _baseline(api_context['factory'], owned_records)
    for assertions in ([{'type': 'status_code', 'passed': 'true'}],
                       [{'type': 'status_code', 'passed': True}] * 101):
        with api_context['factory'].begin() as session:
            session.get(ApiExecutionCase, case_id).sanitized_result = {
                'response': {'status_code': 200}, 'assertions': assertions,
            }
        response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
        assert response.status == 200
        item = response.body['data']['items'][0]
        assert item['evidence_state'] == 'insufficient'
        assert item['assertions_recorded'] == 0


@pytest.mark.parametrize('status,category', [('FAILED', 'setup'), ('CANCELLED', 'cancelled')])
def test_failed_setup_response_is_not_mistaken_for_main_endpoint_evidence(
    api_context, owned_records, identities, http_client, status, category,
):
    baseline_id, case_id = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        child = session.get(ApiExecutionCase, case_id)
        child.status = status
        child.failure_category = category
        child.sanitized_result = {'response': {'status_code': 200},
                                  'assertions': [{'type': 'json_path', 'passed': True}]}
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    item = response.body['data']['items'][0]
    assert item['response_recorded'] is False
    assert item['assertions_recorded'] == 0
    assert item['evidence_state'] == 'insufficient'


def test_reused_environment_revision_does_not_override_execution_source_revision(
    api_context, owned_records, identities, http_client,
):
    baseline_id, original_id = _baseline(api_context['factory'], owned_records)
    with api_context['factory'].begin() as session:
        earlier = ApiSourceRevision(source_id=owned_records['source'].id,
            revision_number=2, status='superseded', document_hash='e'*64,
            normalized_document={}, **_audit('owner-a'))
        session.add(earlier); session.flush()
        session.get(ApiEnvironmentRevision, owned_records['environment_revision'].id).source_revision_id = earlier.id
    response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    assert response.status == 200
    assert [item['execution_case_id'] for item in response.body['data']['items']] == [original_id]


def test_revoked_scope_during_evidence_read_does_not_return_stale_evidence(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records,
        result={'response': {'status_code': 200}, 'assertions': [{'type': 'status_code', 'passed': True}]})
    engine = api_context['factory'].kw['bind']
    revoked = []
    def on_statement(_conn, _cursor, statement, _parameters, _context, _many):
        if 'api_execution_cases.sanitized_result' in statement and not revoked:
            identities['reader']['scope']['api_environments'] = []
            revoked.append(True)
    event.listen(engine, 'after_cursor_execute', on_statement)
    try:
        response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    finally:
        event.remove(engine, 'after_cursor_execute', on_statement)
    assert revoked
    assert response.status == 404
    assert 'execution_case_id' not in str(response.body)


def test_baseline_retired_during_evidence_read_is_not_returned(
    api_context, owned_records, identities, http_client,
):
    baseline_id, _ = _baseline(api_context['factory'], owned_records,
        result={'response': {'status_code': 200}, 'assertions': [{'type': 'status_code', 'passed': True}]})
    engine = api_context['factory'].kw['bind']
    changed = []
    def on_statement(_conn, _cursor, statement, _parameters, _context, _many):
        if 'api_execution_cases.sanitized_result' in statement and not changed:
            changed.append(True)
            with api_context['factory'].begin() as other:
                other.get(ApiBaseline, baseline_id).status = 'superseded'
    event.listen(engine, 'after_cursor_execute', on_statement)
    try:
        response = http_client.get(http.API_PREFIX + f'/baselines/{baseline_id}/execution-evidence', _auth('reader'))
    finally:
        event.remove(engine, 'after_cursor_execute', on_statement)
    assert changed
    assert response.status == 404
    assert 'execution_case_id' not in str(response.body)
