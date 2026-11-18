"""Bounded, native tool-calling transport to the configured Qwen endpoint."""
import json
import math
import re
import socket
import urllib.error
import urllib.request

from task_server.config import (
    dashscope_api_key as get_dashscope_api_key,
    dashscope_base_url as get_dashscope_base_url,
    dashscope_text_model as get_text_model,
)
from task_server.services.week1_probe import NoRedirect, validate_base_url

MAX_RESPONSE_BYTES = 131072
MAX_REQUEST_BYTES = 65536


class ModelError(Exception):
    """Only fixed public error codes; never provider response bodies or credentials."""


def model_name():
    name = get_text_model()
    return name if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,100}', name) else 'invalid'


def call_model(*, messages, tools, timeout_seconds, max_tokens):
    """One request, no redirect, provider fallback, SDK retry or background task."""
    try:
        key = get_dashscope_api_key()
    except ValueError:
        raise ModelError('missing_key') from None
    try:
        endpoint = validate_base_url(get_dashscope_base_url()) + '/chat/completions'
    except (ValueError, TypeError):
        raise ModelError('unsupported_endpoint') from None
    name = model_name()
    if name == 'invalid':
        raise ModelError('invalid_model')
    if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ModelError('time_budget')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 2000:
        raise ModelError('invalid_token_budget')
    payload = {'model': name, 'messages': messages, 'tools': tools, 'tool_choice': 'auto',
               'enable_thinking': False, 'stream': False, 'max_tokens': max_tokens}
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_REQUEST_BYTES:
        raise ModelError('message_budget')
    request = urllib.request.Request(endpoint, data=raw, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=min(45, timeout_seconds)) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ModelError('model_response_too_large')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ModelError('invalid_model_response')
            return result
    except ModelError:
        raise
    except urllib.error.HTTPError as exc:
        code = 'model_http_' + str(exc.code) if 100 <= exc.code <= 599 else 'model_http_error'
        raise ModelError(code) from None
    except (TimeoutError, socket.timeout):
        raise ModelError('model_timeout') from None
    except (ValueError, UnicodeError):
        raise ModelError('invalid_model_response') from None
    except Exception:
        raise ModelError('model_request_failed') from None
