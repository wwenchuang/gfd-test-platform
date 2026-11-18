"""Actual HTTP sessions, task ownership, parameter rejection and readonly start."""
import threading
import uuid

from task_server import app, auth
from test_identity import identity_db
from test_failure_analysis_tasks import setup
from test_platform_mcp_http import request


def test_report_analysis_http_uses_actual_sessions_and_owner_scope(setup, monkeypatch):
    tasks, actor, _, allowed, dispatcher = setup
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token, other = auth.create_session_token('reader'), auth.create_session_token('other')
    base = '/api/failure-analysis/tasks'
    try:
        assert request(server, 'GET', base + '?source=ui&run_id=run-one')[0] == 401
        status, data, headers = request(server, 'GET', base + '?source=ui&run_id=run-one', token)
        assert status == 200 and data['task'] is None
        assert headers['Cache-Control'] == 'private, no-store'
        body = dict(source='ui', run_id='run-one', request_id=str(uuid.uuid4()))
        status, data, _ = request(server, 'POST', base, token, body)
        assert status == 202
        key = data['task']['id']
        assert request(server, 'POST', base, token, body)[1]['task']['id'] == key
        assert len(dispatcher.ids) == 1
        assert request(server, 'GET', base + '?id=' + key, other)[0] == 404
        assert request(server, 'POST', base, token, {**body, 'actor': 'admin'})[0] == 400
        assert request(server, 'POST', base, token, b'{')[0] == 400
        assert request(server, 'GET', base + '?id=' + key + '&run_id=run-two', token)[0] == 400
        response = request(server, 'POST', '/api/failure-analysis/cancel', token, {'id': key})
        assert response[0] == 200 and response[1]['task']['status'] == 'cancelled'
        with monkeypatch.context() as patch:
            def unavailable(*args):
                raise RuntimeError('private-fixture-secret-in-sql')
            patch.setattr(tasks, 'latest', unavailable)
            failure = request(server, 'GET', base + '?source=ui&run_id=run-one', token)
            assert failure[0] == 503
            assert 'private-fixture' not in str(failure[1])
        allowed.clear()
        assert request(server, 'GET', base + '?id=' + key, token)[0] == 403
        auth.logout(token)
        assert request(server, 'GET', base + '?id=' + key, token)[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
