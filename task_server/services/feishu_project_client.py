"""Feishu Project OpenAPI. Group bot webhooks use different credentials and receipts."""
import http.client
import json
import os
import re

from task_server.core.http_client import read_response_bytes, ResponseTooLargeError


class ProjectUnavailable(RuntimeError):
    pass


class ProjectRejected(RuntimeError):
    pass


CREDENTIALS = ('FEISHU_PROJECT_PLUGIN_ID', 'FEISHU_PROJECT_PLUGIN_SECRET', 'FEISHU_PROJECT_USER_KEY')


def missing_credentials():
    return [key for key in CREDENTIALS if not os.getenv(key, '').strip()]


def _post(path, payload, headers=None):
    if not re.fullmatch(r'/open_api/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+', path):
        raise ValueError('feishu_project_path_invalid')
    connection = http.client.HTTPSConnection('project.feishu.cn', timeout=12)
    try:
        connection.request('POST', path, json.dumps(payload, ensure_ascii=False).encode(),
                           {'Content-Type': 'application/json', **(headers or {})})
        reply = connection.getresponse()
        if not 200 <= reply.status < 300:
            raise ProjectUnavailable('feishu_http_' + str(reply.status))
        value = json.loads(read_response_bytes(reply, 4 * 1024 * 1024, '飞书项目'))
        if not isinstance(value, dict):
            raise ProjectUnavailable('feishu_invalid_receipt')
        return value
    except (OSError, http.client.HTTPException, ValueError, ResponseTooLargeError) as exc:
        raise ProjectUnavailable('feishu_response_unavailable') from None
    finally:
        connection.close()


def project_headers():
    if missing_credentials():
        raise ProjectUnavailable('feishu_credentials_missing')
    reply = _post('/open_api/authen/plugin_token', {'plugin_id':os.environ[CREDENTIALS[0]],
                  'plugin_secret':os.environ[CREDENTIALS[1]], 'type':0})
    error, data = reply.get('error'), reply.get('data')
    if not isinstance(error, dict) or type(error.get('code')) is not int or error['code'] != 0:
        raise ProjectUnavailable('feishu_plugin_auth_failed')
    token = data.get('token') if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise ProjectUnavailable('feishu_plugin_auth_failed')
    return {'X-PLUGIN-TOKEN':token, 'X-USER-KEY':os.environ[CREDENTIALS[2]]}


def project_post(path, payload, *, headers=None):
    result = _post(path, payload, headers or project_headers())
    code = result.get('err_code')
    if type(code) is not int:
        raise ProjectUnavailable('feishu_invalid_receipt')
    if code != 0:
        raise ProjectRejected('feishu_business_error_' + str(code))
    return result
