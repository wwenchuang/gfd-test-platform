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
