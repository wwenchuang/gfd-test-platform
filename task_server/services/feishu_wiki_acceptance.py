"""Read current PRD text from the configured tenant's Feishu Wiki or Docs link."""
import http.client
import json
import os
import re
import subprocess
from urllib.parse import parse_qsl, urlsplit


def _api(path, *, token=None, body=None):
    conn = http.client.HTTPSConnection('open.feishu.cn', timeout=15)
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        conn.request('POST' if body else 'GET', '/open-apis' + path,
                     json.dumps(body).encode() if body else None, headers)
        response = conn.getresponse()
        if response.status in (401, 403):
            raise ValueError('prd_document_access_denied')
        if response.status != 200:
            raise ValueError('prd_document_api_unavailable')
        data = response.read(512_001)
        if len(data) > 512_000:
            raise ValueError('prd_document_size_limit')
        result = json.loads(data)
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
        raise ValueError('prd_document_api_unavailable') from None
    finally:
        conn.close()
    if not isinstance(result, dict) or result.get('code') != 0:
        raise ValueError('prd_document_request_rejected')
    return result


def _clauses(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError('prd_document_empty')
    chunks = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        for start in range(0, len(line), 900):
            part = line[start:start + 900]
            if chunks and len(chunks[-1]) + len(part) + 1 <= 900:
                chunks[-1] += '\n' + part
            else:
                chunks.append(part)
            if len(chunks) > 20:
                raise ValueError('prd_document_size_limit')
    return chunks


def _personal_get(path):
    """The official CLI owns the user grant, its refresh and encrypted credentials."""
    env = dict(HOME='/var/lib/midscene-feishu-docs', PATH='/usr/bin:/bin',
               LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
    target = urlsplit(path)
    args = ['/opt/midscene-feishu-docs/lark-cli', 'api', 'GET', '/open-apis' + target.path,
            '--as', 'user', '--format', 'json']
    if target.query:
        args.extend(['--params', json.dumps(dict(parse_qsl(target.query, strict_parsing=True)))])
    try:
        call = subprocess.run(args, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=35)
    except FileNotFoundError:
        raise ValueError('prd_document_user_client_missing') from None
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('prd_document_api_unavailable') from None
    if call.returncode:
        error = (call.stderr + call.stdout)[:32768].lower()
        if any(word in error for word in (b'auth login', b'not logged in', b'token_missing', b'token_expired', b'config init')):
            raise ValueError('prd_document_user_authorization_required')
        if any(word in error for word in (b'permission', b'forbidden', b'access_denied', b'missing_scope')):
            raise ValueError('prd_document_access_denied')
        raise ValueError('prd_document_api_unavailable')
    if len(call.stdout) > 512000:
        raise ValueError('prd_document_size_limit')
    try:
        result = json.loads(call.stdout)
    except (ValueError, UnicodeError):
        raise ValueError('prd_document_request_rejected') from None
    if (not isinstance(result, dict) or result.get('ok') is not True
            or result.get('identity') != 'user' or not isinstance(result.get('data'),dict)):
        raise ValueError('prd_document_request_rejected')
    return result


def _app_token():
    app_id = os.environ.get('FEISHU_DOCS_APP_ID')
    secret = os.environ.get('FEISHU_DOCS_APP_SECRET')
    if not app_id or not secret:
        raise ValueError('prd_document_credentials_required')
    auth = _api('/auth/v3/tenant_access_token/internal',
                body={'app_id': app_id, 'app_secret': secret})
    token = auth.get('tenant_access_token')
    if not isinstance(token, str) or not token:
        raise ValueError('prd_document_token_missing')
    return token


def read_wiki_clauses(link):
    if isinstance(link, dict):
        link = link.get('link') or link.get('url')
    url = urlsplit(link if isinstance(link, str) else '')
    host = os.environ.get('MIDSCENE_FEISHU_DOCS_HOST', '').lower()
    match = re.fullmatch(r'/(wiki|docx)/([A-Za-z0-9]+)/*', url.path)
    if not (re.fullmatch(r'[a-z0-9-]+\.feishu\.cn', host) and
            url.scheme == 'https' and url.netloc.lower() == host and match):
        raise ValueError('prd_document_out_of_scope')
    kind, document = match.groups()
    identity = os.environ.get('MIDSCENE_FEISHU_DOCS_IDENTITY', 'app')
    if identity == 'user':
        approved = set(os.environ.get('MIDSCENE_FEISHU_DOCS_USER_DOCUMENTS', '').split())
        if kind + '/' + document not in approved:
            raise ValueError('prd_document_out_of_scope')
        get = _personal_get
    elif identity == 'app':
        token = _app_token()
        def get(path):
            return _api(path, token=token)
    else:
        raise ValueError('prd_document_identity_invalid')
    if kind == 'wiki':
        result = get('/wiki/v2/spaces/get_node?token=' + document)
        node = (result.get('data') or {}).get('node') or {}
        document = node.get('obj_token')
        if node.get('obj_type') != 'docx' or not isinstance(document, str) or not re.fullmatch(r'[A-Za-z0-9]+', document):
            raise ValueError('prd_document_type_unsupported')
    result = get('/docx/v1/documents/' + document + '/raw_content')
    return _clauses((result.get('data') or {}).get('content'))
