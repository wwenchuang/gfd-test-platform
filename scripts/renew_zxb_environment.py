"""Keep the two workbench environment profiles usable with their own authenticated refresh endpoint."""
import argparse
import base64
import json
import sys
import time

from task_server.api_testing.db import _session_factory
from task_server.api_testing.executor import ExecutorLimits, HttpExecutor
from task_server.api_testing.services.environment_service import EnvironmentService
from task_server.services.puzzle_preset_api import _authorized_runtime, _ENVIRONMENTS


def times(token):
    # Claims only schedule the next refresh. The business endpoint validates the credential.
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('当前凭证没有可识别的有效期')
    data = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
    expiry = int(data['exp'])
    issued = int(data.get('iat', 0))
    return expiry, min(86400, max(1800, (expiry-issued)//3))


def checked_body(client, base, suffix, headers):
    result = client._request('GET', base + suffix, headers, None, authorized_base=base, follow_redirects=False)
    body = result.json_body if isinstance(result.json_body, dict) else {}
    code = body.get('code')
    if result.status_code != 200 or str(code) != '0':
        safe_code = str(code) if str(code).lstrip('-').isdigit() else '未知'
        raise ValueError(f'鉴权验证失败，HTTP {result.status_code}，业务码 {safe_code}')
    return body.get('data')


def renew(stage, actor, force):
    rev, runtime = _authorized_runtime(actor, stage)
    base = runtime.base_url_for('default').rstrip('/')
    if base != 'https://' + _ENVIRONMENTS[stage][1] + '/app':
        raise ValueError('环境和服务地址不匹配')
    token = runtime.secrets.get('ZXBToken')
    headers = dict(runtime.headers)
    auth_key = next((key for key in headers if key.lower() == 'authorization'), None)
    if not token or not auth_key or headers[auth_key] != token:
        raise ValueError('当前环境缺少绑定 Authorization 的 ZXBToken')
    expiry, window = times(token)
    if expiry <= time.time():
        raise ValueError('凭证已过期，请在该环境重新授权')
    if not force and expiry-time.time() > window:
        return '凭证在有效期内'
    client = HttpExecutor(None, None, limits=ExecutorLimits(timeout_seconds=12, max_redirects=0))
    user = checked_body(client, base, '/print3d/api/v1/users/getNewToken', headers)
    candidate = user.get('token') if isinstance(user, dict) else None
    if not isinstance(candidate, str) or times(candidate)[0] <= time.time():
        raise ValueError('续期接口未返回有效凭证')
    headers[auth_key] = candidate
    rows = checked_body(client, base, '/print3d/api/v1/puzzle/presets', headers)
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) and row.get('name') and row.get('imageUrl') for row in rows):
        raise ValueError('新凭证未通过拼图预设业务校验')
    if candidate != token:
        EnvironmentService(_session_factory()).create_revision(runtime.environment_id, {}, {'ZXBToken': candidate},
            actor, expected_revision_id=rev)
    elif expiry-time.time() <= window:
        raise ValueError('服务返回原凭证，未延长有效期')
    return f'凭证已验证，拼图预设 {len(rows)} 条'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('environment', nargs='+', choices=['测试', '正式'])
    parser.add_argument('--actor', required=True)
    parser.add_argument('--now', action='store_true', help='立即调用续期和业务接口')
    args = parser.parse_args()
    failed = False
    for stage in args.environment:
        try:
            print(stage, renew(stage, args.actor, args.now))
        except Exception as exc:
            # Never expose infrastructure exception messages (headers/URLs may be embedded).
            reason = str(exc) if type(exc) is ValueError and str(exc).startswith(('当前','鉴权','新凭证','续期','凭证','环境','服务返回')) else type(exc).__name__
            print(stage, '未完成', reason, file=sys.stderr)
            failed = True
    sys.exit(int(failed))
