"""Fixed, permission-scoped first-week Qwen probes; no user-supplied network targets."""
import base64
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from task_server.config import (dashscope_api_key as get_dashscope_api_key, dashscope_base_url as get_dashscope_base_url, dashscope_text_model as get_text_model, dashscope_vl_model as get_vl_model)
from task_server import identity

FIXTURE = Path(__file__).resolve().parent.parent / 'fixtures' / 'week1_vision.png'
HOSTS = {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'}

class ProbeBusy(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def validate_base_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path.rstrip('/') != '/compatible-mode/v1'
            or parsed.query or parsed.fragment):
        raise ValueError('unsupported_endpoint')
    return url.rstrip('/')

def redact(value, key):
    value = str(value or '')[:4096]
    if key:
        value = value.replace(key, '[已隐藏]')
    value = re.sub(r'(?i)\bsk-[a-z0-9_-]+', '[已隐藏]', value)
    return ''.join(c for c in value if c.isprintable() or c in '\n\t')[:500]

def _db(path=None):
    path = Path(path) if path else identity.default_db_path().with_name('week1-probes.sqlite3')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if hasattr(os, 'geteuid') and os.fstat(fd).st_uid != os.geteuid():
            raise PermissionError('invalid owner')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK(id=1), until_time REAL NOT NULL, token TEXT NOT NULL)')
    return conn

def _now():
    return datetime.now(timezone.utc).isoformat()

def _claim(path):
    token = uuid.uuid4().hex
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT until_time FROM lease WHERE id=1').fetchone()
        if row and row[0] > time.time():
            raise ProbeBusy()
        db.execute('INSERT OR REPLACE INTO lease VALUES (1, ?, ?)', (time.time()+300, token))
    return token

def _release(path, token):
    with _db(path) as db:
        # A short cooldown protects against double clicks after quick responses.
        db.execute('UPDATE lease SET until_time=? WHERE id=1 AND token=?', (time.time()+10, token))

def _request_model(mode):
    try:
        key = get_dashscope_api_key()
    except ValueError:
        raise ValueError('missing_key') from None
    url = validate_base_url(get_dashscope_base_url()) + '/chat/completions'
    model = (get_text_model() if mode == 'text' else get_vl_model())
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', model):
        raise ValueError('invalid_model')
    content = '只回复 CONNECTED。' if mode == 'text' else [
        {'type':'image_url','image_url':{'url':'data:image/png;base64,' + base64.b64encode(FIXTURE.read_bytes()).decode('ascii')}},
        {'type':'text','text':'请描述图中左右两侧图形的颜色和形状。'}]
    payload = {'model': model, 'messages':[{'role':'user','content':content}], 'max_tokens':128}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','Authorization':'Bearer '+key}, method='POST')
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(req, timeout=25) as response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('response_too_large')
        return json.loads(raw)

def run_probe(owner, mode, path=None):
    if not owner or mode not in ('text','vision'):
        raise ValueError('invalid_request')
    token = _claim(path)
    model = get_text_model() if mode == 'text' else get_vl_model()
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', model or ''):
        model = 'invalid'
    result = {'id': uuid.uuid4().hex, 'mode':mode, 'created_at':_now(), 'model':model,
              'source':'platform_direct', 'status':'failed', 'answer':'',
              'manual_check':'pending' if mode == 'vision' else None, 'text_check':None, 'error_code':None}
    start = time.monotonic()
    try:
        response = _request_model(mode)
        answer = response['choices'][0]['message']['content']
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('invalid_response')
        try:
            key = get_dashscope_api_key()
        except ValueError:
            key = ''
        result.update(status='success', answer=redact(answer, key))
        if mode == 'text':
            result['text_check'] = 'passed' if answer.strip() == 'CONNECTED' else 'failed'
        usage = response.get('usage')
        if isinstance(usage, dict):
            result['usage'] = {k: v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and 0 <= v <= 10000000}
    except urllib.error.HTTPError as exc:
        result['error_code'] = 'model_http_' + str(exc.code) if 100 <= exc.code <= 599 else 'model_http_error'
    except ValueError as exc:
        result['error_code'] = str(exc) if str(exc) in ('missing_key','unsupported_endpoint','invalid_model','invalid_response','response_too_large') else 'configuration_or_response_error'
    except Exception:
        result['error_code'] = 'model_request_failed'
    finally:
        result['elapsed_ms'] = round((time.monotonic()-start)*1000)
        try:
            with _db(path) as db:
                db.execute('INSERT INTO results VALUES (?,?,?,?)', (result['id'],owner,time.time(),json.dumps(result, ensure_ascii=False)))
        finally:
            _release(path, token)
    return result

def list_results(owner, path=None):
    if not owner:
        raise ValueError('invalid_owner')
    with _db(path) as db:
        rows = db.execute('SELECT payload FROM results WHERE owner=? ORDER BY created DESC LIMIT 50', (owner,)).fetchall()
    return [json.loads(row[0]) for row in rows]

def confirm(owner, result_id, correct, path=None):
    if not owner or not re.fullmatch('[0-9a-f]{32}', str(result_id)) or type(correct) is not bool:
        raise ValueError('invalid_request')
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM results WHERE id=? AND owner=?', (result_id,owner)).fetchone()
        if not row:
            raise ValueError('not_found')
        result = json.loads(row[0])
        if result['mode'] != 'vision' or result['status'] != 'success':
            raise ValueError('not_reviewable')
        result.update(manual_check='passed' if correct else 'failed', confirmed_by=owner, confirmed_at=_now())
        db.execute('UPDATE results SET payload=? WHERE id=? AND owner=?', (json.dumps(result,ensure_ascii=False),result_id,owner))
    return result
