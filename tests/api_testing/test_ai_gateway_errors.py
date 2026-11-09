import io
import json
import socket
import urllib.error
from types import SimpleNamespace

import pytest

from task_server.api_testing.services.ai_service import AiCaseService, AiGatewayClient, AiGatewayError


def call():
    return AiGatewayClient('http://127.0.0.1:8090', receipt_grace_seconds=5).chat(messages=[{'role': 'user', 'content': 'test'}], provider_id='', model='', timeout_seconds=120)


def http_error(payload, status=500):
    return urllib.error.HTTPError('http://localhost', status, 'error', {}, io.BytesIO(payload))


def test_provider_timeout_is_classified_and_only_codes_are_trusted(monkeypatch):
    observed = {}
    def fail(request, timeout):
        observed['payload'] = json.loads(request.data)
        observed['timeout'] = timeout
        raise http_error(b'{"error_code":"provider_timeout", "error":"secret-must-not-escape"}')
    monkeypatch.setattr('urllib.request.urlopen', fail)
    with pytest.raises(AiGatewayError) as caught:
        call()
    assert caught.value.code == 'gateway_timeout'
    assert 'secret' not in str(caught.value)
    assert observed['payload']['timeoutMs'] == 120000
    assert observed['timeout'] > 120


@pytest.mark.parametrize('payload', [b'{"error":"timeout and secret"}', b'not json', b'{"error_code":"provider_timeout_secret"}'])
def test_ordinary_or_untrusted_500_is_not_misclassified(monkeypatch, payload):
    def fail(*_args, **_kwargs):
        raise http_error(payload)
    monkeypatch.setattr('urllib.request.urlopen', fail)
    with pytest.raises(AiGatewayError) as caught:
        call()
    assert caught.value.code == 'gateway_error'
    assert 'secret' not in str(caught.value)


def test_local_transport_timeout_is_classified_without_leaking_detail(monkeypatch):
    def fail(*_args, **_kwargs):
        raise urllib.error.URLError(socket.timeout('secret'))
    monkeypatch.setattr('urllib.request.urlopen', fail)
    with pytest.raises(AiGatewayError) as caught:
        call()
    assert caught.value.code == 'gateway_timeout'
    assert 'secret' not in str(caught.value)


def test_public_batch_view_only_exposes_allowed_error_code():
    batch = SimpleNamespace(id='b', sequence=1, state='failed_gateway', endpoint_ids=[], requested_model='', actual_model='', result={}, error={'code': 'gateway_timeout', 'message': 'secret'})
    view = AiCaseService._batch_view(batch)
    assert view.error_code == 'gateway_timeout'
    assert 'secret' not in repr(view)
    batch.error = {'code': 'sensitive-untrusted', 'message': 'secret'}
    assert AiCaseService._batch_view(batch).error_code == 'gateway_error'


def test_five_second_receipt_grace_is_limited_to_generation_client(monkeypatch):
    from task_server.api_testing.services.ai_service import AiCaseService, AiFailureAnalyzer

    captured = []
    def fail(_request, timeout):
        captured.append(timeout)
        raise urllib.error.URLError('failure')
    monkeypatch.setattr('urllib.request.urlopen', fail)
    for client in (AiCaseService(None).gateway_client, AiFailureAnalyzer().gateway_client):
        with pytest.raises(AiGatewayError):
            client.chat(messages=[{'role': 'user', 'content': 'test'}], provider_id='', model='', timeout_seconds=20)
    assert captured == [25, 20]


def test_generation_client_sends_scoped_thinking_and_random_correlation(monkeypatch):
    captured = {}
    def respond(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(b'{"success":true,"thinkingModeSent":"off"}')
    monkeypatch.setattr('urllib.request.urlopen', respond)
    client = AiGatewayClient('http://127.0.0.1:8090')
    client.chat(messages=[{'role':'user','content':'sensitive'}], provider_id='', model='',
                timeout_seconds=20, thinking_mode='off', correlation_id='1db47cfa-0d88-48e4-b2cc-35500085dd16')
    assert captured['thinkingMode'] == 'off'
    assert captured['correlationId'] == '1db47cfa-0d88-48e4-b2cc-35500085dd16'


@pytest.mark.parametrize('mode', ['off', 'on', 'default'])
def test_generation_thinking_mode_allowed(mode):
    assert AiCaseService._thinking_mode(mode) == mode


@pytest.mark.parametrize('mode', ['', True, 'false', 'OFF', {}, None])
def test_generation_thinking_mode_invalid_fails_closed(mode):
    from task_server.api_testing.services.ai_service import AiJobInputError
    with pytest.raises(AiJobInputError):
        AiCaseService._thinking_mode(mode)


def test_unsupported_thinking_route_is_not_misreported_as_provider_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise http_error(b'{"error_code":"thinking_route_unsupported", "error":"sensitive provider detail"}', 400)
    monkeypatch.setattr('urllib.request.urlopen', fail)
    with pytest.raises(AiGatewayError) as caught:
        call()
    assert caught.value.code == 'thinking_route_unsupported'
    assert 'sensitive' not in str(caught.value)
    batch = SimpleNamespace(id='b', sequence=1, state='failed_gateway', endpoint_ids=[], requested_model='', actual_model='', result={}, error={'code':'thinking_route_unsupported'})
    assert AiCaseService._batch_view(batch).error_code == 'thinking_route_unsupported'


@pytest.mark.parametrize('receipt', [{}, {'thinkingModeSent':'on'}, {'thinkingModeSent': False}])
def test_explicit_mode_rejects_old_or_inconsistent_gateway_receipt(monkeypatch, receipt):
    def respond(_request, timeout):
        return io.BytesIO(json.dumps({'success':True, **receipt}).encode())
    monkeypatch.setattr('urllib.request.urlopen', respond)
    with pytest.raises(AiGatewayError) as caught:
        AiGatewayClient().chat(messages=[{'role':'user','content':'test'}], provider_id='', model='', timeout_seconds=20, thinking_mode='off')
    assert caught.value.code == 'thinking_mode_unconfirmed'
