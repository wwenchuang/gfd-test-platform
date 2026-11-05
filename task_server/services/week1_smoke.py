"""Bounded first-week anonymous smoke checks with owner-scoped history.

All programs, arguments and network targets are server controlled. Browser
artifacts are private and are not served as static files.
"""
import json
import os
import re
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from task_server import identity

_SCRIPTS = Path(__file__).resolve().parent.parent / 'fixtures' / 'week1_smoke'
_LEASE_SECONDS = 150
_TIMEOUT_SECONDS = 45
_ARTIFACTS = {
    'positive-screenshot': ('positive', 'screenshot.png', 'image/png', 8 * 1024 * 1024),
    'negative-screenshot': ('intentional-failure', 'screenshot.png', 'image/png', 8 * 1024 * 1024),
    'positive-trace': ('positive', 'trace.zip', 'application/zip', 25 * 1024 * 1024),
    'negative-trace': ('intentional-failure', 'trace.zip', 'application/zip', 25 * 1024 * 1024),
}


class SmokeBusy(Exception):
    pass


def _db(path=None):
    path = Path(path) if path else identity.default_db_path().with_name('week1-smoke.sqlite3')
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


def _claim(path):
    token = uuid.uuid4().hex
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT until_time FROM lease WHERE id=1').fetchone()
        if row and row[0] > time.time():
            raise SmokeBusy()
        db.execute('INSERT OR REPLACE INTO lease VALUES (1, ?, ?)', (time.time() + _LEASE_SECONDS, token))
    return token


def _release(path, token):
    with _db(path) as db:
        db.execute('UPDATE lease SET until_time=? WHERE id=1 AND token=?', (time.time() + 3, token))


def _configured_path(name, *, executable=False):
    value = os.environ.get(name, '')
    if not value or not os.path.isabs(value):
        raise ValueError('dependency_unavailable')
    path = Path(value)
    if executable:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError('dependency_unavailable')
    elif not path.is_dir():
        raise ValueError('dependency_unavailable')
    return str(path)


def _run_process(kind, mode, out):
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    if kind == 'api':
        argv = [sys.executable, str(_SCRIPTS / 'api_smoke.py'), '--scenario', 'anonymous']
        if mode == 'negative':
            argv += ['--expected-code', '999']
    else:
        node = _configured_path('TASK_WEEK1_NODE', executable=True)
        env.update(NODE_PATH=_configured_path('TASK_WEEK1_NODE_PATH'),
                   PLAYWRIGHT_BROWSERS_PATH=_configured_path('TASK_WEEK1_BROWSERS_PATH'),
                   W01_UI_ARTIFACTS=str(out), HOME=str(out))
        library = os.environ.get('TASK_WEEK1_LIBRARY_PATH', '')
        if library:
            env['LD_LIBRARY_PATH'] = _configured_path('TASK_WEEK1_LIBRARY_PATH')
        argv = [node, str(_SCRIPTS / 'ui_smoke.js'),
                'positive' if mode == 'positive' else 'intentional-failure']
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env, start_new_session=True)
    try:
        stdout, stderr = child.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            for pipe in (child.stdout, child.stderr):
                if pipe:
                    pipe.close()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    child.kill()
                    child.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        raise TimeoutError('smoke_timeout') from None
    return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def _check(kind, mode, result):
    name = '正向断言' if mode == 'positive' else '故意失败'
    expected = ('HTTP 200、业务码 4009' if mode == 'positive' else '业务码 999（故意错误）') if kind == 'api' else ('功夫豆测试平台' if mode == 'positive' else '故意设置不存在的标题')
    check = {'name': name, 'expected': expected, 'actual': '未取得有效结果', 'exit_code': None, 'status': 'blocked'}
    if result.returncode not in (0, 1):
        check['exit_code'] = result.returncode
        check['actual'] = '进程异常退出，未完成有效断言'
        return check
    check['exit_code'] = result.returncode
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or type(data.get('passed')) is not bool:
            return check
        if kind == 'api':
            code = data.get('actual_code')
            if type(code) is not int or data.get('scenario') != 'anonymous' or type(data.get('http_status')) is not int:
                return check
            check['actual'] = 'HTTP %s、业务码 %s' % (data['http_status'], code)
            good = (data['http_status'] == 200 and code == 4009 and
                    data.get('expected_code') == (4009 if mode == 'positive' else 999))
        else:
            if data.get('mode') != ('positive' if mode == 'positive' else 'intentional-failure'):
                return check
            actual = data.get('actual')
            if not isinstance(actual, str):
                return check
            check['actual'] = '功夫豆测试平台' if actual == '功夫豆测试平台' else '页面文案不匹配'
            good = actual == '功夫豆测试平台' and data.get('expected') == expected
        if mode == 'positive':
            good = good and result.returncode == 0 and data['passed'] is True
        else:
            good = good and result.returncode == 1 and data['passed'] is False
        check['status'] = 'passed' if good else 'failed'
    except (TypeError, ValueError):
        pass
    return check


def _read_artifact(root, result_id, item, *, content=True):
    mode, filename, mime, limit = _ARTIFACTS[item]
    opened = []
    try:
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        opened.append(os.open(str(root), flags))
        opened.append(os.open(result_id, flags, dir_fd=opened[-1]))
        opened.append(os.open(mode, flags, dir_fd=opened[-1]))
        fd = os.open(filename, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=opened[-1])
        opened.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > limit or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
            raise ValueError('not_found')
        if not content:
            return None, mime
        with os.fdopen(os.dup(fd), 'rb') as source:
            data = source.read(limit + 1)
        if not data or len(data) > limit:
            raise ValueError('not_found')
        return data, mime
    except (OSError, ValueError):
        raise ValueError('not_found') from None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _artifact_state(out):
    count = 0
    for item in _ARTIFACTS:
        try:
            _read_artifact(out.parent, out.name, item, content=False)
            count += 1
        except ValueError:
            pass
    return 'saved' if count == len(_ARTIFACTS) else 'partial' if count else 'unavailable'


def _environment_checks():
    checks = []
    def add(name, expected, actual, status, code=None):
        checks.append({'name': name, 'expected': expected, 'actual': actual,
                       'exit_code': code, 'status': status})
    try:
        info = os.uname()
        values = (info.sysname, info.release, info.machine)
        if not all(re.fullmatch(r'[A-Za-z0-9._+\-]{1,100}', value) for value in values):
            raise ValueError('invalid_system_info')
        add('系统与架构', '可读取', '%s %s；%s' % values, 'passed')
    except Exception:
        add('系统与架构', '可读取', '系统信息不可用', 'blocked')
    try:
        mem = {}
        with open('/proc/meminfo', encoding='ascii') as source:
            for line in source:
                match = re.fullmatch(r'(MemTotal|MemAvailable):\s*(\d+)\s+kB\s*', line)
                if match:
                    mem[match.group(1)] = int(match.group(2))
        if not {'MemTotal', 'MemAvailable'} <= set(mem):
            raise ValueError('memory_unavailable')
        add('内存', '可读取', '总计 %.1f GiB；可用 %.1f GiB' %
            (mem['MemTotal'] / 1048576, mem['MemAvailable'] / 1048576), 'passed')
    except Exception:
        add('内存', '可读取', '内存信息不可用', 'blocked')
    try:
        disk = shutil.disk_usage(identity.default_db_path().parent)
        add('磁盘', '可读取', '总计 %.1f GiB；可用 %.1f GiB' %
            (disk.total / 1073741824, disk.free / 1073741824), 'passed')
    except Exception:
        add('磁盘', '可读取', '磁盘信息不可用', 'blocked')
    try:
        import pip
        same = Path(pip.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        add('Python/pip', '来自同一运行环境', 'Python %s；pip %s；%s' %
            ('.'.join(map(str, sys.version_info[:3])), pip.__version__, '同一环境' if same else '环境不一致'),
            'passed' if same else 'failed')
    except Exception:
        add('Python/pip', '来自同一运行环境', 'pip 不可用', 'blocked')
    try:
        node = _configured_path('TASK_WEEK1_NODE', executable=True)
        done = subprocess.run([node, '--version'], capture_output=True, text=True,
                              timeout=5, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        version = done.stdout.strip()
        if done.returncode or not re.fullmatch(r'v\d+\.\d+\.\d+', version):
            add('Node.js', '可运行', '进程执行失败或版本不可识别', 'blocked', done.returncode)
        else:
            add('Node.js', '可运行', version, 'passed', done.returncode)
    except Exception:
        add('Node.js', '可运行', '未配置、不可用或超时；未取得退出码', 'blocked')
    for name, command, pattern in (('Git', 'git', r'git version \d+\.\d+\.\d+(?:[^\r\n]{0,30})?'),
                                    ('npm', 'npm', r'\d+\.\d+\.\d+')):
        try:
            executable = shutil.which(command, path='/usr/bin:/bin')
            if not executable:
                raise ValueError('unavailable')
            done = subprocess.run([executable, '--version'], capture_output=True, text=True,
                                  timeout=5, env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})
            version = done.stdout.strip()
            valid = done.returncode == 0 and re.fullmatch(pattern, version)
            add(name, '可运行', version if valid else '进程执行失败或版本不可识别',
                'passed' if valid else 'blocked', done.returncode)
        except Exception:
            add(name, '可运行', '不可用或超时；未取得退出码', 'blocked')
    try:
        _configured_path('TASK_WEEK1_NODE_PATH')
        _configured_path('TASK_WEEK1_BROWSERS_PATH')
        if os.environ.get('TASK_WEEK1_LIBRARY_PATH'):
            _configured_path('TASK_WEEK1_LIBRARY_PATH')
        add('浏览器依赖配置', '路径已配置', '路径已配置；可通过 UI 冒烟继续验证运行', 'passed')
    except Exception:
        add('浏览器依赖配置', '路径已配置', '未配置或目录不可用', 'blocked')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    for name, url, service in (
        ('平台服务', 'http://127.0.0.1:8088/api/health', False),
        ('业务域名', 'https://print.wisebeginner3d.com/', False),
        ('PyPI', 'https://pypi.org/simple/pip/', False),
        ('npm 依赖源', 'https://registry.npmjs.org/playwright', False),
        ('千问端点', 'https://dashscope.aliyuncs.com/compatible-mode/v1', True),
    ):
        try:
            try:
                with opener.open(urllib.request.Request(url, method='GET'), timeout=5) as response:
                    code = response.status
                    if name == '平台服务':
                        payload = response.read(262145)
                        if len(payload) > 262144 or json.loads(payload).get('ok') is not True:
                            add(name, 'HTTP 200 且健康状态正常', 'HTTP %d；健康状态不正常' % code, 'failed')
                            continue
            except urllib.error.HTTPError as error:
                code = error.code
            actual = 'HTTP %d' % code
            if service:
                actual += '（仅说明有 HTTP 响应，不代表模型鉴权成功）'
            passed = (100 <= code <= 599) if service else code == 200
            add(name, '有 HTTP 响应' if service else 'HTTP 200', actual,
                'passed' if passed else 'failed')
        except Exception:
            add(name, '有 HTTP 响应' if service else 'HTTP 200', '连接失败或超时', 'blocked')
    return checks


def _save(path, owner, result, created):
    with _db(path) as db:
        db.execute('INSERT OR REPLACE INTO results VALUES (?,?,?,?)',
                   (result['id'], owner, created, json.dumps(result, ensure_ascii=False)))


def run(owner, kind, path=None):
    if not isinstance(owner, str) or not owner or kind not in ('api', 'ui', 'environment'):
        raise ValueError('invalid_request')
    token = _claim(path)
    created = time.time()
    result = {'id': uuid.uuid4().hex, 'kind': kind, 'status': 'running',
              'created_at': datetime.now(timezone.utc).isoformat(), 'checks': [],
              'artifacts': 'unavailable', 'message': '正在执行，刷新可查看状态。'}
    db_path = Path(path) if path else identity.default_db_path().with_name('week1-smoke.sqlite3')
    out = db_path.parent / 'week1-smoke-artifacts' / result['id']
    try:
        _save(path, owner, result, created)
        if kind == 'ui':
            try:
                out.mkdir(mode=0o700, parents=True, exist_ok=False)
                os.chmod(out.parent, 0o700)
            except Exception:
                result.update(status='blocked', message='无法创建受控证据目录，请检查服务端配置。')
                _save(path, owner, result, created)
                return result
        for mode in (() if kind == 'environment' else ('positive', 'negative')):
            try:
                check = _check(kind, mode, _run_process(kind, mode, out))
            except Exception:
                check = {'name': '正向断言' if mode == 'positive' else '故意失败',
                         'expected': '真实预期' if mode == 'positive' else '故意错误预期',
                         'actual': '未取得退出码；执行环境不可用或超时', 'exit_code': None, 'status': 'blocked'}
            result['checks'].append(check)
        if kind == 'environment':
            try:
                result['checks'] = _environment_checks()
            except Exception:
                result['checks'] = [{'name':'环境检查', 'expected':'可执行', 'actual':'检查未完成',
                                     'exit_code':None, 'status':'blocked'}]
        if kind == 'ui':
            result['artifacts'] = _artifact_state(out)
        statuses = {c['status'] for c in result['checks']}
        result['status'] = 'blocked' if 'blocked' in statuses or (kind == 'ui' and result['artifacts'] != 'saved') else 'failed' if 'failed' in statuses else 'passed'
        if kind == 'api':
            result['message'] = '匿名 API 验证未登录拒绝，不代表已认证业务成功。'
        elif kind == 'ui':
            evidence = {'saved':'截图和 trace 已完整保存到服务器受控目录',
                        'partial':'截图和 trace 仅部分保存', 'unavailable':'截图和 trace 未保存'}
            result['message'] = '匿名登录页检查；' + evidence[result['artifacts']] + '。'
        else:
            result['message'] = '连通性检查不代表业务或千问模型调用成功。'
        _save(path, owner, result, created)
        return result
    finally:
        _release(path, token)


def list_results(owner, path=None):
    if not isinstance(owner, str) or not owner:
        raise ValueError('invalid_owner')
    with _db(path) as db:
        rows = db.execute('SELECT created, payload FROM results WHERE owner=? ORDER BY created DESC LIMIT 50', (owner,)).fetchall()
    results = []
    for created, payload in rows:
        result = json.loads(payload)
        if result['status'] == 'running' and created + _LEASE_SECONDS < time.time():
            result.update(status='blocked', message='执行中断或超时，原结果不可用；请重新运行。')
        results.append(result)
    return results


def get_artifact(owner, result_id, item, path=None):
    if (not isinstance(owner, str) or not owner or not isinstance(result_id, str)
            or not re.fullmatch(r'[a-f0-9]{32}', result_id) or item not in _ARTIFACTS):
        raise ValueError('not_found')
    with _db(path) as db:
        row = db.execute('SELECT payload FROM results WHERE owner=? AND id=?', (owner, result_id)).fetchone()
    if not row or json.loads(row[0]).get('kind') != 'ui':
        raise ValueError('not_found')
    db_path = Path(path) if path else identity.default_db_path().with_name('week1-smoke.sqlite3')
    root = db_path.parent / 'week1-smoke-artifacts'
    return _read_artifact(root, result_id, item)
