"""Manual release intent and private evidence through the registered HTTP routes."""
import http.client
import json
import sqlite3

import pytest

from test_identity import activate, create_member, identity_db
from test_operation_http import events, operation_server, request
from test_ui_batch_dispatch import dispatch_env
from test_ui_batch_report_service import completed
from test_ui_execution_service import ACTORS, APP, environment
from task_server import auth, identity, operation_http


SECRET = 'FORGED_PRIVATE_REPORT_SENTINEL'


@pytest.fixture
def manual_http(operation_server, dispatch_env, identity_db, monkeypatch):
    """Keep real sessions and trusted actor IDs aligned with the batch fixture."""
    store = identity_db[1]
    monkeypatch.setattr(identity, 'get_access_profile', store.get_access_profile)
    for username in ('a', 'b'):
        create_member(store, username, display_name=ACTORS[username]['display_name'],
                      scope={'ui_apps': [APP], 'api_projects': [], 'api_environments': []})
        activate(store, username)
    for username in ('a', 'b', 'admin'):
        profile = store.get_access_profile(username)
        monkeypatch.setitem(ACTORS, username, {'kind': 'user', **{
            key: profile[key] for key in ('username', 'user_id', 'display_name')}})
    tokens = {name: auth.create_session_token(name) for name in ('a', 'b', 'admin')}
    return operation_server[0], operation_server[1], store, tokens


def raw_request(server, path, token, raw):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request('POST', path, body=raw, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize('body', [
    [], 'invalid', True, 1,
    {'requested_ns': 1}, {'actor': {'username': SECRET}}, {'owner_user_id': SECRET},
    {'tested_version': SECRET}, {'report_request': {'actor': SECRET}},
    {'notifications': None}, {'notifications': []}, {'notifications': {}},
    {'notifications': {'enabled': 'false'}}, {'notifications': {'enabled': 0}},
    {'notifications': {'enabled': 1}}, {'notifications': {'enabled': None}},
    {'notifications': {'enabled': True, 'channel_id': SECRET}},
])
def test_report_http_rejects_invalid_or_forged_intent_without_requesting(manual_http, body):
    from task_server.services.ui_execution_store import UiExecutionStore
    from task_server.services import job_service
    server, audit, _, tokens = manual_http
    batch, _, _ = completed()
    before = UiExecutionStore().get_batch(batch['ui_batch_id'])
    jobs = job_service.load_jobs(limit=None)
    status, raw = request(server, 'POST', '/api/ui-executions/' + batch['ui_batch_id'] + '/report', tokens['a'], body)
    assert status == (409 if isinstance(body, dict) else 400), raw
    if isinstance(body, dict):
        assert json.loads(raw)['code'] == 'report_request_invalid'
    after = UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert after == before
    assert job_service.load_jobs(limit=None) == jobs
    event = events(audit)[0]
    assert event['action'] == 'ui_batch.report_generate' and event['resource_id'] == ''
    assert SECRET not in json.dumps(event)


def test_report_http_rejects_json_null_without_requesting(manual_http):
    from task_server.services.ui_execution_store import UiExecutionStore
    server, _, _, tokens = manual_http
    batch, _, _ = completed()
    before = UiExecutionStore().get_batch(batch['ui_batch_id'])
    status, raw = raw_request(server, '/api/ui-executions/' + batch['ui_batch_id'] + '/report', tokens['a'], b'null')
    assert status == 400, raw
    assert UiExecutionStore().get_batch(batch['ui_batch_id']) == before


@pytest.mark.parametrize('body,enabled', [({}, False), ({'notifications': {'enabled': False}}, False),
                                       ({'notifications': {'enabled': True}}, True)])
def test_report_http_binds_authenticated_requester_and_explicit_send_flag(manual_http, monkeypatch, body, enabled):
    from task_server.services.ui_execution_store import UiExecutionStore
    from task_server.services import feishu_service, job_service, ui_batch_report_service as reports
    server, audit, _, tokens = manual_http
    batch, _, _ = completed(status='failed')
    before = UiExecutionStore().get_batch(batch['ui_batch_id'])
    jobs = job_service.load_jobs(limit=None)
    channel = {'channel_id': 'configured-channel', 'target_label': '测试群', 'source': 'app'}
    monkeypatch.setattr(feishu_service, 'task_app_feishu_channel', lambda _: channel)
    monkeypatch.setattr(reports, '_render_docx', lambda _: pytest.fail('HTTP request rendered synchronously'))
    status, raw = request(server, 'POST', '/api/ui-executions/' + batch['ui_batch_id'] + '/report', tokens['admin'], body)
    assert status == 202, raw
    assert json.loads(raw)['report']['status'] == 'pending'
    after = UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert after['report_request']['actor'] == {key: ACTORS['admin'][key] for key in ('user_id', 'username', 'display_name')}
    assert after['report_request']['notifications'] == {'enabled': enabled, **channel}
    assert after['report_requested_ns'] == after['report_request']['requested_ns']
    assert after['delivery_status'] == ('pending' if enabled else 'not_requested')
    for field in ('status', 'owner_user_id', 'owner_display_name', 'selection', 'snapshots', 'case_attempts'):
        assert after[field] == before[field]
    assert job_service.load_jobs(limit=None) == jobs
    event = events(audit)[0]
    assert event['resource_type'] == 'ui_batch' and event['resource_id'] == batch['ui_batch_id']
    assert event['action'] == 'ui_batch.report_generate' and event['actor']['username'] == 'admin'


def test_report_http_rechecks_send_permission_before_mutating(manual_http):
    from task_server.services.ui_execution_store import UiExecutionStore
    server, _, _, tokens = manual_http
    batch, _, _ = completed()
    assert 'platform.notify' not in identity.get_access_profile('a')['permissions']
    path = '/api/ui-executions/' + batch['ui_batch_id'] + '/report'
    status, raw = request(server, 'POST', path, tokens['a'], {'notifications': {'enabled': True}})
    assert status == 403, raw
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['report_requested_ns'] is None
    assert request(server, 'POST', path, tokens['a'], {})[0] == 202


def test_evidence_http_reads_only_manifest_without_formal_report_or_blob_reads(manual_http, monkeypatch):
    from task_server.services import ui_batch_report_service as reports
    from task_server.services.ui_execution_store import UiExecutionStore
    server, audit, _, tokens = manual_http
    batch, job, event = completed()
    artifact = reports.save_ui_artifact(job['job_id'], event, SECRET.encode(), 'png')
    connect = reports._connect
    blob_reads = []

    def metadata_connection():
        db = connect()
        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == 'ui_report_artifacts' and column == 'content':
                blob_reads.append((table, column))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db

    monkeypatch.setattr(reports, '_connect', metadata_connection)
    monkeypatch.setattr(reports, '_build', lambda _: pytest.fail('evidence read generated a report'))
    monkeypatch.setattr(reports, '_render_docx', lambda _: pytest.fail('evidence read rendered a report'))
    status, raw = request(server, 'GET', '/api/ui-executions/' + batch['ui_batch_id'] + '/evidence', tokens['a'])
    assert status == 200, raw
    assert json.loads(raw) == {'ok': True, 'ui_batch_id': batch['ui_batch_id'], 'artifacts': [artifact]}
    assert not blob_reads and SECRET.encode() not in raw
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['report_status'] == 'not_requested'
    audit_event = events(audit)[0]
    assert audit_event['action'] == 'ui_batch.evidence_view'
    assert audit_event['resource_type'] == 'ui_batch' and audit_event['resource_id'] == batch['ui_batch_id']
    assert audit_event['result'] == 'success'


@pytest.mark.parametrize('access,status', [('anonymous', 401), ('invalid', 401), ('other_owner', 404),
                                          ('revoked_scope', 403), ('no_ui_view', 403)])
def test_evidence_http_denials_do_not_leak_metadata_or_promote_resource(manual_http, monkeypatch, access, status):
    from task_server.services import ui_batch_report_service as reports
    server, audit, identities, tokens = manual_http
    batch, job, event = completed()
    artifact = reports.save_ui_artifact(job['job_id'], event, SECRET.encode(), 'png')
    token = tokens['a']
    if access == 'anonymous': token = None
    if access == 'invalid': token = 'invalid-token'
    if access == 'other_owner': token = tokens['b']
    if access == 'revoked_scope':
        identities.update_user('admin', 'a', {'scope': {'ui_apps': [], 'api_projects': [], 'api_environments': []}})
    if access == 'no_ui_view':
        current_profile = identity.get_access_profile
        monkeypatch.setattr(identity, 'get_access_profile', lambda name: {
            **current_profile(name), 'permissions': []} if name == 'a' else current_profile(name))
    received, raw = request(server, 'GET', '/api/ui-executions/' + batch['ui_batch_id'] + '/evidence?id=' + SECRET, token)
    assert received == status, raw
    assert artifact['artifact_id'].encode() not in raw and SECRET.encode() not in raw
    audit_event = events(audit)[0]
    assert audit_event['action'] == 'ui_batch.evidence_view' and audit_event['resource_id'] == ''
    assert SECRET not in json.dumps(audit_event)


def test_evidence_operation_route_key_is_stable_and_distinct():
    key = operation_http.route_key('GET', '/api/ui-executions/batch/evidence')
    assert key == operation_http.route_key('GET', '/api/ui-executions/another/evidence')
    assert key != 'unknown'
    assert key != operation_http.route_key('GET', '/api/ui-executions/batch/report')
