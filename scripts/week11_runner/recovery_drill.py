"""Exercise the workbench state machine with isolated SQLite and child processes."""
import argparse
from contextlib import ExitStack, contextmanager
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import time
from unittest.mock import patch
import uuid
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from task_server import identity
from task_server.services import platform_mcp as platform, platform_runner as runner

ACTOR = ('isolated-owner', 'isolated-user', 'isolated-session')
PROFILE = {'user_id':ACTOR[0], 'status':'active', 'permissions':['ui.view'], 'scope':{'ui_apps':[]}}
KINDS = ('worker_crash', 'cancel', 'timeout', 'service_restart')
# Stand-in for the fixed feedback script only. Real workers never accept a path or command from clients.
FIXTURE = """import os, sys, time
from pathlib import Path
work = Path(__file__).parent
parent = os.getppid()
(work / 'ready').write_text(str(os.getpid()))
until = time.monotonic() + 15
while time.monotonic() < until:
    if os.getppid() != parent:
        (work / 'parent-exited').touch()
        sys.exit(0)
    if (work / 'crash').exists():
        os._exit(23)
    time.sleep(.02)
sys.exit(24)
"""


def wait_for(check, label):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.03)
    raise RuntimeError('timed out waiting for ' + label)


@contextmanager
def isolated(root, kind):
    with ExitStack() as stack:
        for target, name, value in (
                (identity, 'default_db_path', lambda:root/'identity.sqlite3'),
                (platform.web, '_profile', lambda *args:PROFILE),
                (runner, 'feedback_authorized', lambda actor:True),
                (runner, '_feedback_credentials', lambda actor:'isolated-fixture'),
                (runner, '_FEEDBACK', root/kind/'fixed-worker.py')):
            stack.enter_context(patch.object(target, name, value))
        if kind == 'timeout':
            stack.enter_context(patch.object(platform, '_DEADLINE_SECONDS', 1.5))
        yield


def call(name, args):
    return platform.call(ACTOR, {'name':name, 'arguments':args})


def start():
    return call('run_suite', {'suite_id':'feedback-categories', 'environment':'platform', 'request_id':str(uuid.uuid4())})


def host(root, kind, connection):
    with isolated(root, kind):
        connection.send(start()['run_id'])
        connection.close()
        # The supervisor terminates only this disposable service process.
        time.sleep(30)


def stopped(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A just orphaned fixture can briefly be a zombie until PID 1 collects it.
    return platform._process_start(pid) == 'dead'


def write_private(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def run_drill(root, kind):
    directory = root/kind
    directory.mkdir(mode=0o700)
    (directory/'fixed-worker.py').write_text(FIXTURE)
    process = None
    with isolated(root, kind):
        try:
            if kind == 'service_restart':
                ctx = multiprocessing.get_context('spawn')
                with_read, with_write = ctx.Pipe(duplex=False)
                process = ctx.Process(target=host, args=(root, kind, with_write))
                process.start()
                with_write.close()
                try:
                    if not with_read.poll(10):
                        raise RuntimeError('isolated service did not start a run')
                    run_id = with_read.recv()
                finally:
                    with_read.close()
            else:
                run_id = start()['run_id']
            ident = {'run_id':run_id}
            ready = directory/'ready'
            wait_for(lambda:ready.is_file() and ready.read_text(), 'worker handshake')
            pid = int(ready.read_text())
            before = call('get_run', ident)['status']
            if before != 'running':
                raise RuntimeError('worker was not observed running')
            if kind == 'worker_crash':
                (directory/'crash').touch()
            elif kind == 'cancel':
                before = call('cancel_run', ident)['status']
            elif kind == 'service_restart':
                process.kill()
                process.join(5)
                if process.exitcode != -signal.SIGKILL:
                    raise RuntimeError('isolated service termination unconfirmed')
                wait_for(lambda:(directory/'parent-exited').is_file(), 'fixture acknowledged parent exit')
            def terminal():
                run = call('get_run', ident)
                return run if run['cases'] else None
            run = wait_for(terminal, 'terminal run')
            wait_for(lambda:stopped(pid), 'child termination')
            case, = run['cases']
            xml = platform.junit(ACTOR, run_id)
            suite = ElementTree.fromstring(xml)
            history = platform.history(ACTOR)
            test = suite.find('testcase')
            tag = 'skipped' if kind == 'cancel' else 'error'
            expected = {'worker_crash':('interrupted','worker_crash',23),
                        'timeout':('timed_out','worker_timeout',-signal.SIGKILL),
                        'cancel':('cancelled','cancelled',-signal.SIGKILL),
                        'service_restart':('interrupted','service_restarted',None)}[kind]
            verified = (case['run_id'] == run_id and (run['status'],run['error_kind'],case['worker_exit_code']) == expected
                        and case['exit_code'] is None and case['duration_ms'] >= 0
                        and any(row == run for row in history) and suite.get('id') == run_id
                        and test is not None and test.find(tag) is not None and test.find(tag).get('type') == run['status'])
            write_private(directory/'junit.xml', xml)
            write_private(directory/'run.json', json.dumps(run, ensure_ascii=False, indent=2).encode())
            return {'kind':kind,'run_id':run_id,'before':before,'status':run['status'],'error_kind':run['error_kind'],
                    'worker_exit_code':case['worker_exit_code'],'duration_ms':case['duration_ms'],'verified':verified}
        finally:
            if process is not None and process.is_alive():
                process.kill()
                process.join(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    root = parser.parse_args().output_dir.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('output directory must be empty')
    checks = [run_drill(root, kind) for kind in KINDS]
    receipt = {'version':1,'environment':'isolated','checked_at':time.time(),'checks':checks}
    write_private(root/'recovery-drill.json', json.dumps(receipt, indent=2).encode())
    for check in checks:
        print(check['kind'], check['status'], check['error_kind'], check['worker_exit_code'], check['verified'])
    return 0 if all(check['verified'] for check in checks) else 1


if __name__ == '__main__':
    sys.exit(main())
