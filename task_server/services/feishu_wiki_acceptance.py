"""Read current PRD text from the configured tenant's Feishu Wiki or Docs link."""
import http.client
import json
import os
import re
from urllib.parse import urlsplit


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


def read_wiki_clauses(link):
    if isinstance(link, dict):
        link = link.get('link') or link.get('url')
    url = urlsplit(link if isinstance(link, str) else '')
    host = os.environ.get('MIDSCENE_FEISHU_DOCS_HOST', '').lower()
    match = re.fullmatch(r'/(wiki|docx)/([A-Za-z0-9]+)/*', url.path)
    if not (re.fullmatch(r'[a-z0-9-]+\.feishu\.cn', host) and
            url.scheme == 'https' and url.netloc.lower() == host and match):
        raise ValueError('prd_document_out_of_scope')
    app_id = os.environ.get('FEISHU_DOCS_APP_ID')
    secret = os.environ.get('FEISHU_DOCS_APP_SECRET')
    if not app_id or not secret:
        raise ValueError('prd_document_credentials_required')
    auth = _api('/auth/v3/tenant_access_token/internal',
                body={'app_id': app_id, 'app_secret': secret})
    token = auth.get('tenant_access_token')
    if not isinstance(token, str) or not token:
        raise ValueError('prd_document_token_missing')
    kind, document = match.groups()
    if kind == 'wiki':
        result = _api('/wiki/v2/spaces/get_node?token=' + document, token=token)
        node = (result.get('data') or {}).get('node') or {}
        document = node.get('obj_token')
        if node.get('obj_type') != 'docx' or not isinstance(document, str) or not re.fullmatch(r'[A-Za-z0-9]+', document):
            raise ValueError('prd_document_type_unsupported')
    result = _api('/docx/v1/documents/' + document + '/raw_content', token=token)
    return _clauses((result.get('data') or {}).get('content'))
