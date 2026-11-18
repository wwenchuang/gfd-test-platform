"""Transport tests use a fake HTTP opener, never a paid or business endpoint."""
import importlib
import json
import urllib.error
from unittest.mock import patch

import pytest


def test_model_entrypoint_exists():
    assert importlib.util.find_spec('task_server.services.failure_analysis_model') is not None


@pytest.fixture
def model():
    return importlib.import_module('task_server.services.failure_analysis_model')


class Response:
    def __init__(self, raw): self.raw = raw
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.raw[:limit]


class Opener:
    def __init__(self, raw=b'{"choices":[]}'): self.raw = raw
    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return Response(self.raw)


def test_native_tools_qwen_config_no_redirect_and_remaining_timeout(model):
    opener = Opener()
    messages = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_a', 'type': 'function', 'function': {'name': 'get_failed_steps', 'arguments': '{}'}}]}, {'role': 'tool', 'tool_call_id': 'call_a', 'content': '{}'}]
    with patch.object(model, 'get_dashscope_api_key', return_value='sk-test'), patch.object(model, 'get_dashscope_base_url', return_value='https://dashscope.aliyuncs.com/compatible-mode/v1'), patch.object(model, 'get_text_model', return_value='qwen-test'), patch.object(model.urllib.request, 'build_opener', return_value=opener) as build:
        model.call_model(messages=messages, tools=[{'type': 'function'}], timeout_seconds=7, max_tokens=1900)
    payload = json.loads(opener.request.data)
    assert payload['model'] == 'qwen-test'
    assert payload['messages'] == messages
    assert payload['tool_choice'] == 'auto'
    assert payload['enable_thinking'] is False
    assert payload['max_tokens'] == 1900
    assert opener.timeout == 7
    assert isinstance(build.call_args.args[0], model.NoRedirect)
    assert opener.request.full_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'


def test_missing_key_is_safe_and_no_network_call(model):
    with patch.object(model, 'get_dashscope_api_key', side_effect=ValueError('sensitive value')), patch.object(model.urllib.request, 'build_opener') as build:
        with pytest.raises(model.ModelError, match='missing_key'):
            model.call_model(messages=[], tools=[], timeout_seconds=10, max_tokens=2000)
    build.assert_not_called()


def test_unsafe_endpoint_rejected_without_network(model):
    with patch.object(model, 'get_dashscope_api_key', return_value='sk-test'), patch.object(model, 'get_dashscope_base_url', return_value='https://evil.test/compatible-mode/v1'), patch.object(model.urllib.request, 'build_opener') as build:
        with pytest.raises(model.ModelError, match='unsupported_endpoint'):
            model.call_model(messages=[], tools=[], timeout_seconds=10, max_tokens=2000)
    build.assert_not_called()


def test_transport_never_returns_error_body(model):
    class FailedOpener:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError('https://private', 401, 'sk-secret', {}, None)
    with patch.object(model, 'get_dashscope_api_key', return_value='sk-test'), patch.object(model.urllib.request, 'build_opener', return_value=FailedOpener()):
        with pytest.raises(model.ModelError) as error:
            model.call_model(messages=[], tools=[], timeout_seconds=10, max_tokens=2000)
    assert str(error.value) == 'model_http_401'


def test_huge_response_is_rejected_and_timeout_clamped(model):
    opener = Opener(b'x' * 200000)
    with patch.object(model, 'get_dashscope_api_key', return_value='sk-test'), patch.object(model.urllib.request, 'build_opener', return_value=opener):
        with pytest.raises(model.ModelError, match='model_response_too_large'):
            model.call_model(messages=[], tools=[], timeout_seconds=180, max_tokens=2000)
    assert opener.timeout == 45
