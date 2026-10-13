"""Trusted UI operation attribution through the real registered HTTP handlers."""
import json
import pytest
from test_operation_http import operation_server, request, events
from test_identity import identity_db
from task_server import auth, operation_http

BATCH = 'persisted-batch'
DIGEST = 'a' * 64
SECRET = 'PRIVATE_CONTENT_TOKEN_SENTINEL'


@pytest.mark.parametrize('method,path,module,function,result,body,action,resource,expected', [
    ('POST', '/api/ui-executions', 'ui_execution_service', 'create_ui_batch', {'ui_batch_id': BATCH, 'status': 'queued'}, {'created_by': SECRET}, 'create', BATCH, 'accepted'),
    ('POST', '/api/ui-executions/preflight', 'ui_execution_service', 'preflight_ui_batch', {'ready': True}, {}, 'preflight', '', 'success'),
    ('GET', '/api/ui-executions', 'ui_execution_service', 'list_ui_batches', {'items': []}, None, 'list', '', 'success'),
    ('GET', '/api/ui-executions/' + BATCH, 'ui_execution_service', 'get_ui_batch', {'ui_batch_id': BATCH, 'status': 'running'}, None, 'view', BATCH, 'accepted'),
    ('POST', '/api/ui-executions/' + BATCH + '/cancel', 'ui_batch_dispatch', 'cancel_ui_batch', {'ui_batch_id': BATCH, 'status': 'cancelling'}, {}, 'cancel', BATCH, 'accepted'),
    ('POST', '/api/ui-executions/' + BATCH + '/rerun', 'ui_batch_dispatch', 'rerun_ui_batch', {'ui_batch_id': 'actual-new-batch', 'status': 'queued'}, {'scope': 'failed'}, 'rerun', 'actual-new-batch', 'accepted'),
    ('POST', '/api/ui-executions/' + BATCH + '/confirm-stop', 'ui_batch_dispatch', 'confirm_ui_attempt_stopped', {'ui_batch_id': BATCH, 'status': 'interrupted'}, {}, 'confirm_stop', BATCH, 'success'),
    ('GET', '/api/ui-executions/' + BATCH + '/report', 'ui_batch_report_service', 'get_ui_batch_report', {'ui_batch_id': BATCH, 'status': 'pending'}, None, 'report_view', BATCH, 'accepted'),
    ('POST', '/api/ui-executions/' + BATCH + '/report', 'ui_batch_report_service', 'request_ui_batch_report', {'ui_batch_id': BATCH, 'status': 'pending'}, {}, 'report_generate', BATCH, 'accepted'),
    ('GET', '/api/ui-executions/' + BATCH + '/report/download', 'ui_batch_report_service', 'download_ui_batch_report', (SECRET.encode(), 'text/html', 'private.html'), None, 'report_download', BATCH, 'success'),
    ('GET', '/api/ui-executions/' + BATCH + '/summary', 'ui_batch_report_service', 'get_ui_batch_summary', {'status': 'ready', 'summary': SECRET}, None, 'summary', BATCH, 'success'),
    ('GET', '/api/ui-executions/' + BATCH + '/artifacts/' + DIGEST, 'ui_batch_report_service', 'read_ui_artifact', (SECRET.encode(), 'image/png', 'private.png'), None, 'artifact_download', BATCH, 'success'),
    ('GET', '/api/ui-executions/' + BATCH + '/deliveries', 'ui_batch_notification_service', 'get_batch_deliveries', {'items': [], 'delivery_status': 'pending'}, None, 'notification_status', BATCH, 'success'),
])
def test_successful_registered_ui_handlers_bind_only_validated_resource(operation_server, monkeypatch, method, path, module, function, result, body, action, resource, expected):
    from importlib import import_module
    from task_server.services.operation_attribution import current_actor
    server, store = operation_server
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    def operation(*args, **kwargs):
        assert current_actor()['username'] == 'admin'
        return result
    monkeypatch.setattr(import_module('task_server.services.' + module), function, operation)
    status, raw = request(server, method, path + '?id=' + SECRET, auth.create_session_token(), body)
    assert status < 300, raw
    event = events(store)[0]
    assert event['resource_type'] == 'ui_batch'
    assert event['resource_id'] == resource
    assert event['action'] == 'ui_batch.' + action
    assert event['result'] == expected
    assert event['actor']['username'] == 'admin'
    assert SECRET not in json.dumps(event)


@pytest.mark.parametrize('module,function,path,method,body', [
    ('ui_execution_service','create_ui_batch','/api/ui-executions','POST', {'ui_batch_id':SECRET}),
    ('ui_execution_service','get_ui_batch','/api/ui-executions/'+SECRET,'GET', None),
    ('ui_batch_dispatch','rerun_ui_batch','/api/ui-executions/'+SECRET+'/rerun','POST', {'scope':'failed'}),
    ('ui_batch_dispatch','confirm_ui_attempt_stopped','/api/ui-executions/'+SECRET+'/confirm-stop','POST', {}),
    ('ui_batch_report_service','download_ui_batch_report','/api/ui-executions/'+SECRET+'/report/download','GET', None),
    ('ui_batch_report_service','read_ui_artifact','/api/ui-executions/'+SECRET+'/artifacts/'+DIGEST,'GET', None),
    ('ui_batch_notification_service','get_batch_deliveries','/api/ui-executions/'+SECRET+'/deliveries','GET', None),
])
@pytest.mark.parametrize('failure,status', [('denied',403), ('missing',404)])
def test_rejected_ui_service_outcomes_never_promote_forged_identity(operation_server, monkeypatch, module, function, path, method, body, failure, status):
    from importlib import import_module
    from task_server.services.ui_execution_contract import AccessDenied
    server, store = operation_server
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    def operation(*args, **kwargs):
        if failure == 'denied': raise AccessDenied('app_forbidden')
        if module == 'ui_batch_report_service' or module == 'ui_batch_notification_service': raise FileNotFoundError()
        if function == 'create_ui_batch':
            from task_server.services.ui_execution_service import BatchValidationError
            raise BatchValidationError('request_invalid')
        return None
    monkeypatch.setattr(import_module('task_server.services.' + module), function, operation)
    expected_status = 400 if failure == 'missing' and function == 'create_ui_batch' else status
    assert request(server, method, path+'?id='+SECRET, auth.create_session_token(), body)[0] == expected_status
    event = events(store)[0]
    assert event['resource_id'] == ''
    assert event['result'] == ('denied' if failure == 'denied' else 'failed')
    assert SECRET not in json.dumps(event)


def test_ui_route_keys_are_stable_distinct_and_unknown_actions_stay_unknown():
    cancel = operation_http.route_key('POST', '/api/ui-executions/batch/cancel')
    rerun = operation_http.route_key('POST', '/api/ui-executions/batch/rerun')
    assert cancel != rerun
    assert cancel == operation_http.route_key('POST', '/api/ui-executions/other/cancel')
    assert rerun == operation_http.route_key('POST', '/api/ui-executions/other/rerun')
    assert operation_http.route_key('POST', '/api/ui-executions/batch/'+SECRET) == 'unknown'
    assert operation_http.route_key('GET', '/api/ui-executions/batch/artifacts/invalid') == 'unknown'
    assert operation_http._resource('/api/ui-executions/batch/'+SECRET, 'POST', {}, {}, 404) == ('unknown', '', 'unknown.request')


@pytest.mark.parametrize('delivery_batch,fail_retry,expected_status', [(BATCH,False,202), ('another-batch',False,404), (BATCH,True,403)])
def test_notification_retry_binds_actual_delivery_batch_after_success(operation_server, tmp_path, monkeypatch, delivery_batch, fail_retry, expected_status):
    import sqlite3
    from task_server.services import ui_batch_notification_service as notifications, ui_execution_service as service
    from task_server.services.ui_execution_store import UiExecutionStore
    from task_server.services.ui_execution_contract import AccessDenied
    server, store = operation_server
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    db_path = tmp_path / 'deliveries.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE delivery_outbox (delivery_key TEXT, ui_batch_id TEXT)')
        db.execute('INSERT INTO delivery_outbox VALUES (?,?)', (DIGEST,delivery_batch))
    monkeypatch.setattr(UiExecutionStore, '_connect', lambda _: sqlite3.connect(db_path))
    monkeypatch.setattr(service, 'get_ui_batch', lambda *args: {'ui_batch_id':BATCH})
    calls=[]
    def retry(*args):
        calls.append(args)
        if fail_retry: raise AccessDenied('app_forbidden')
        return {'delivery_key':DIGEST,'ui_batch_id':delivery_batch,'status':'pending'}
    monkeypatch.setattr(notifications, 'retry_batch_delivery', retry)
    status, raw = request(server,'POST','/api/ui-executions/'+BATCH+'/deliveries/'+DIGEST+'/retry',auth.create_session_token(),{'confirmed_not_received':True})
    assert status == expected_status, raw
    event=events(store)[0]
    assert event['action'] == 'ui_batch.notification_retry'
    assert event['resource_id'] == (BATCH if status == 202 else '')
    assert event['result'] == ('accepted' if status == 202 else 'denied' if status == 403 else 'failed')
    if delivery_batch != BATCH: assert calls == []


from test_ui_execution_service import environment


def test_real_batch_service_authorization_precedes_http_resource_binding(operation_server, environment, identity_db, monkeypatch):
    from task_server import identity
    from test_ui_execution_service import request as batch_request, APP
    from test_identity import create_member, activate
    server, store = operation_server
    # Use real persisted accounts/sessions with the isolated baseline/runner fixtures.
    monkeypatch.setattr(identity, 'get_access_profile', identity_db[1].get_access_profile)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    create_member(identity_db[1], scope={'ui_apps':[APP], 'api_projects':[], 'api_environments':[]})
    activate(identity_db[1])
    admin=auth.create_session_token()
    status, raw=request(server,'POST','/api/ui-executions',admin,batch_request(),{'Idempotency-Key':'real-click'})
    assert status == 201, raw
    batch=json.loads(raw)['batch']
    batch_id=batch['ui_batch_id']
    assert request(server,'GET','/api/ui-executions/'+batch_id,admin)[0] == 200
    assert request(server,'GET','/api/ui-executions/'+batch_id,auth.create_session_token('member'))[0] == 404
    assert request(server,'POST','/api/ui-executions',admin,{**batch_request(),'created_by':SECRET},{'Idempotency-Key':'forged-click'})[0] == 400
    rows=events(store,4)
    assert [row['resource_id'] for row in rows] == ['', '', batch_id, batch_id]
    assert [row['result'] for row in rows] == ['failed','failed','accepted','accepted']
    assert rows[-1]['actor']['user_id'] == batch['owner_user_id']
    assert SECRET not in json.dumps(rows)
    # Follow the actual persisted service path through cancellation and a new rerun.
    assert request(server,'POST','/api/ui-executions/'+batch_id+'/cancel',admin,{})[0] == 200
    status,raw=request(server,'POST','/api/ui-executions/'+batch_id+'/rerun',admin,{'scope':'all'},{'Idempotency-Key':'real-rerun'})
    assert status == 201, raw
    rerun=json.loads(raw)['batch']
    assert rerun['ui_batch_id'] != batch_id
    rows=events(store,6)
    assert rows[0]['action'] == 'ui_batch.rerun'
    assert rows[0]['resource_id'] == rerun['ui_batch_id']
    assert rows[0]['result'] == 'accepted'
    assert rows[1]['action'] == 'ui_batch.cancel'
    assert rows[1]['resource_id'] == batch_id


@pytest.mark.parametrize('status,settled,expected', [('blocked',False,'accepted'), ('interrupted',False,'accepted'), ('interrupted',True,'success'), ('passed',True,'success'), ('failed',True,'success'), ('cancelled',True,'success')])
def test_ui_http_result_reflects_pending_execution_without_rewriting_terminal_result(operation_server, monkeypatch, status, settled, expected):
    from task_server.services import ui_batch_dispatch
    server,store=operation_server
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1')
    monkeypatch.setattr(ui_batch_dispatch,'confirm_ui_attempt_stopped',lambda *args: {'ui_batch_id':BATCH,'status':status,'execution_settled':settled})
    assert request(server,'POST','/api/ui-executions/'+BATCH+'/confirm-stop',auth.create_session_token(),{})[0] == 200
    assert events(store)[0]['result'] == expected


def test_new_ui_nested_state_does_not_change_legacy_request_semantics(operation_server, monkeypatch):
    from task_server import app
    server,store=operation_server
    monkeypatch.setattr(app,'dispatch_post',lambda handler: handler._json({'ok':True,'batch':{'status':'running'}}))
    assert request(server,'POST','/api/files/op',auth.create_session_token(),{})[0] == 200
    assert events(store)[0]['result'] == 'success'
