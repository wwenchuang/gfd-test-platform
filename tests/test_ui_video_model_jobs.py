"""Loopback HTTP exercises spawned normal run_ai_skill, ACK and ownership."""
import copy
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from task_server.services import ui_video_model_jobs as jobs
from task_server.services.ui_video_records import RecordStore
from task_server.services.ui_video_stage_receipts import make_expected_attestation
from task_server.services.ai_skill_service import SkillSchemaError
from test_ui_video_stage_runtime import request


class Gateway:
    def __init__(self, content='{}', hold=False):
        self.content, self.hold = content, hold
        self.calls, self.active, self.peak, self.closed = [], 0, 0, 0
        self.lock, self.release = threading.Lock(), threading.Event()
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with outer.lock:
                    outer.calls.append(body); outer.active += 1; outer.peak = max(outer.peak, outer.active)
                try:
                    if outer.hold:
                        while not outer.release.wait(.01):
                            if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                                outer.closed += 1
                                return
                    else:
                        time.sleep(.08)
                    content = outer.content(body) if callable(outer.content) else outer.content
                    data = json.dumps({'success': True, 'content': content, 'model': body['model'],
                                       'providerId': 'official', 'fallbackUsed': False, 'finishReason': 'stop',
                                       'usage': {'totalTokens': 4}, 'videoPolicy': make_expected_attestation(body)}).encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    outer.closed += 1
                finally:
                    with outer.lock: outer.active -= 1
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join()


@pytest.fixture
def gateway():
    value = Gateway()
    yield value
    value.close()


def invocation(trace=None):
    skill, kw = request('overview')
    payload = kw.pop('payload')
    kw['runtime_trace'] = trace if trace is not None else {}
    return skill, payload, kw


def manager(tmp_path, gateway, **kw):
    store = RecordStore(tmp_path / 'records')
    return jobs.ModelJobs(store, deadline=time.monotonic() + 10, reserve=kw.pop('reserve', lambda: None),
                          check=kw.pop('check', lambda: None), gateway_url=gateway.url, **kw)


def test_real_http_two_peak_lazy_refill_and_trace(tmp_path, gateway):
    built, reserved, traces = [], [], [{} for _ in range(5)]
    with manager(tmp_path, gateway, reserve=lambda: reserved.append(len(reserved))) as owner:
        def builders():
            for i in range(5):
                def build(i=i):
                    built.append(i)
                    assert len(built) <= len([r for r in owner.accounting if r['admitted']]) + 2
                    return invocation(traces[i])
                yield build
        results = list(owner.map(builders()))
        assert sorted(i for i, _ in results) == list(range(5))
        assert len(gateway.calls) == len(reserved) == 5 and gateway.peak == 2
        assert all(t['usage']['totalTokens'] == 4 for t in traces)
        assert all(r['receipt_persisted'] and r['admitted'] and r['cleanup'] for r in owner.accounting)
        assert not owner.active_pids
    assert all(r['cleanup'] for r in owner.accounting)


def test_proof_writer_failure_zero_http_and_latched(tmp_path, gateway, monkeypatch):
    with manager(tmp_path, gateway) as owner:
        monkeypatch.setattr(owner.records, 'append', lambda *a: (_ for _ in ()).throw(OSError('writer sentinel')))
        with pytest.raises(OSError): owner.run(*invocation()[:2], **invocation()[2])
        assert not gateway.calls and not owner.active_pids
        with pytest.raises(jobs.ModelJobError, match='latched'): owner.run(*invocation()[:2], **invocation()[2])
        assert owner.accounting[0]['reserved'] and not owner.accounting[0]['dispatch_intent']


def test_raw_writer_failure_retains_observed_receipt_no_admission_or_refill(tmp_path, gateway, monkeypatch):
    built = []
    with manager(tmp_path, gateway) as owner:
        append = owner.records.append
        def write(kind, value):
            if kind == 'receipt': raise OSError('disk failed')
            return append(kind, value)
        monkeypatch.setattr(owner.records, 'append', write)
        def build(): built.append(True); return invocation()
        with pytest.raises(OSError): list(owner.map(iter([build] * 8)))
        assert len(built) == 2 and len(gateway.calls) <= 2
        assert any(r['raw_received'] and not r['receipt_persisted'] for r in owner.accounting)
        assert not any(r['admitted'] for r in owner.accounting)
        assert owner.pending_receipts and not owner.active_pids


@pytest.mark.parametrize('failure', ['cancel', 'permission', 'deadline'])
def test_http_wait_cancel_permissions_deadline_close_and_join(tmp_path, failure):
    gateway = Gateway(hold=True)
    def check():
        if gateway.calls and failure != 'deadline':
            if failure == 'permission': raise PermissionError('revoked')
            return False
    try:
        with manager(tmp_path, gateway, check=check) as owner:
            skill, payload, kw = invocation()
            if failure == 'deadline': kw['timeout'] = 1.5
            expected = TimeoutError if failure == 'deadline' else PermissionError if failure == 'permission' else jobs.ModelJobCancelled
            with pytest.raises(expected): owner.run(skill, payload, **kw)
            assert not owner.active_pids and all(r['cleanup'] for r in owner.accounting)
            assert owner.accounting[0]['transport_status'] == 'unknown'
            assert kw['runtime_trace']['error'] == ('video_job_timeout' if failure == 'deadline' else 'video_job_cancelled')
        end = time.monotonic() + 2
        while not gateway.closed and time.monotonic() < end: time.sleep(.01)
        assert gateway.closed == len(gateway.calls) == 1
    finally: gateway.close()


def test_cancel_before_builder_and_callbacks_are_not_pickled(tmp_path, gateway):
    built = []
    with manager(tmp_path, gateway, check=lambda: False) as owner:
        with pytest.raises(jobs.ModelJobCancelled): list(owner.map([lambda: built.append(True)]))
    assert not built and not gateway.calls


def test_schema_type_timeout_and_private_error_redaction(tmp_path, gateway):
    skills = tmp_path / 'skills'; (skills / 'schemas').mkdir(parents=True); (skills / 'prompts').mkdir()
    (skills / 'prompts' / 'ui_video_trajectory.overview.md').write_text('custom test prompt')
    (skills / 'schemas' / 'ui_video_trajectory.schema.json').write_text(json.dumps({'type': 'object', 'required': ['states']}))
    with manager(tmp_path, gateway, skills_dir=skills) as owner:
        skill, payload, kw = invocation()
        with pytest.raises(SkillSchemaError) as caught: owner.run(skill, payload, **kw)
        assert caught.value.schema_path == '$.states' and 'schema' in caught.value.model_feedback
        assert kw['runtime_trace']['usage']['totalTokens'] == 4
        gateway.content = '{"states": []}'
        assert owner.run(skill, payload, **kw) == {'states': []}
        assert 'error' not in kw['runtime_trace']
        gateway.content = 'PRIVATE_PROVIDER_SENTINEL'
        with pytest.raises(jobs.ModelJobError) as caught: owner.run(skill, payload, **kw)
        assert 'PRIVATE_PROVIDER_SENTINEL' not in str(caught.value)
        assert 'PRIVATE_PROVIDER_SENTINEL' not in json.dumps(kw['runtime_trace'])


def test_child_environment_excludes_runner_keys_and_env_file(tmp_path, monkeypatch, gateway):
    envfile = tmp_path / 'env'; envfile.write_text('MIDSCENE_API_KEY=FILE_PRIVATE\n'); envfile.chmod(0o600)
    monkeypatch.setenv('MIDSCENE_ENV_FILE', str(envfile)); monkeypatch.setenv('SONIC_TOKEN', 'RUNNER_PRIVATE')
    monkeypatch.setenv('OPENAI_API_KEY', 'KEY_PRIVATE')
    before = dict(os.environ)
    with manager(tmp_path, gateway) as owner:
        env = owner.child_environment
        assert env['MIDSCENE_ENV_FILE'] == '' and env['AI_GATEWAY_URL'] == gateway.url
        assert not {'SONIC_TOKEN', 'OPENAI_API_KEY', 'HOME', 'PYTHONPATH'} & set(env)
        root = str(Path(__file__).resolve().parents[1])
        code = 'import sys,json,os;sys.path.insert(0,sys.argv[1]);import task_server.config as c;print(json.dumps([c.MIDSCENE_ENV_FILE,c.dashscope_api_key(False),os.getenv("SONIC_TOKEN"),c.AI_GATEWAY_URL]))'
        result = subprocess.run([sys.executable, '-E', '-c', code, root], env=env, capture_output=True, check=True)
        assert json.loads(result.stdout) == ['', '', None, gateway.url]
        skill, payload, kw = invocation(); assert owner.run(skill, payload, **kw) == {}
    assert dict(os.environ) == before


def test_run_reusable_after_success_and_parent_hooks_never_serialized(tmp_path, gateway):
    with manager(tmp_path, gateway) as owner:
        proof, raw = [], []
        skill, payload, kw = invocation()
        kw.update(request_proof_hook=lambda p: proof.append(p), raw_receipt=lambda r: raw.append(r))
        assert owner.run(skill, payload, **kw) == {}
        assert owner.run(skill, payload, **kw) == {}
        assert len(proof) == len(raw) == len(gateway.calls) == 2
        assert gateway.peak == 1


def fake_child(monkeypatch, owner, body):
    """Protocol adversary only. All HTTP tests above use the real normal child."""
    def launch():
        parent, child = socket.socketpair()
        code = '''import socket,struct,json,sys,time,os
s=socket.socket(fileno=int(sys.argv[1]))
def recv():
 h=b''
 while len(h)<4:h+=s.recv(4-len(h))
 n=struct.unpack('!I',h)[0];b=b''
 while len(b)<n:b+=s.recv(n-len(b))
 return json.loads(b)
def send(event,value):
 b=json.dumps(dict(event=event,value=value,trace={})).encode();s.sendall(struct.pack('!I',len(b))+b)
r=recv()
''' + body
        process = subprocess.Popen([sys.executable, '-E', '-c', code, str(child.fileno())], pass_fds=(child.fileno(),),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=owner.child_environment)
        child.close(); parent.setblocking(False)
        return process, parent
    monkeypatch.setattr(owner, '_launch', launch)


@pytest.mark.parametrize('body,code', [
    ("s.sendall(struct.pack('!I',1048577));time.sleep(5)", 'message_limit'),
    ("s.sendall(struct.pack('!I',2)+b'[]')", 'message_invalid'),
    ("s.sendall(struct.pack('!I',8)+b'{bad')", 'child_exit'),
    ("os._exit(3)", 'child_exit'),
    ("send('result',{})", 'protocol_invalid'),
    ("send('error',{'code':'PRIVATE_SENTINEL'})", 'protocol_invalid'),
])
def test_bounded_malformed_ipc_and_premature_exit(tmp_path, gateway, monkeypatch, body, code):
    with manager(tmp_path, gateway) as owner:
        fake_child(monkeypatch, owner, body)
        skill, payload, kw = invocation()
        with pytest.raises(jobs.ModelJobError, match=code) as caught: owner.run(skill, payload, **kw)
        assert 'PRIVATE' not in str(caught.value)
        assert not owner.active_pids and all(r['cleanup'] for r in owner.accounting)
        assert not gateway.calls


@pytest.mark.parametrize('ending', ['os._exit(9)', 'time.sleep(3)', 'time.sleep(.08)'])
def test_final_result_is_not_admitted_until_clean_exit(tmp_path, gateway, monkeypatch, ending):
    with manager(tmp_path, gateway) as owner:
        body = '''
p={'stage':'overview','skill':'ui_video_trajectory','skill_version':'overview','correlation':'a'*32,'gateway_request_sha256':'a'*64,'timeout_ms':1000}
send('proof',p);recv()
send('receipt',{'content':'{}','gateway_request_sha256':'a'*64});recv()
send('result',{})
''' + ending
        fake_child(monkeypatch, owner, body)
        skill, payload, kw = invocation()
        if ending == 'time.sleep(.08)': assert owner.run(skill, payload, **kw) == {}
        else:
            with pytest.raises(jobs.ModelJobError, match='child_exit'): owner.run(skill, payload, **kw)
            assert not owner.accounting[0]['admitted']
        assert owner.accounting[0]['raw_received'] and owner.accounting[0]['receipt_persisted']
        assert not owner.active_pids


def test_large_request_send_is_cancellable_with_nonreading_child(tmp_path, gateway, monkeypatch):
    calls = []
    with manager(tmp_path, gateway, check=lambda: False if len(calls) > 1 else None) as owner:
        def launch():
            parent, child = socket.socketpair(); parent.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            p = subprocess.Popen([sys.executable, '-E', '-c', 'import time;time.sleep(10)'], pass_fds=(child.fileno(),),
                                 env=owner.child_environment)
            child.close(); parent.setblocking(False); calls.extend([1,2]); return p, parent
        monkeypatch.setattr(owner, '_launch', launch)
        skill, payload, kw = invocation(); payload['bounded_test'] = 'a' * (4 * 1024 * 1024)
        before = time.monotonic()
        with pytest.raises(jobs.ModelJobCancelled): owner.run(skill, payload, **kw)
        assert time.monotonic() - before < 2 and not owner.active_pids


def test_budget_and_engine_deadline_callbacks_not_reset(tmp_path, gateway):
    reservations = []
    def reserve():
        if reservations: raise ValueError('outer_request_limit')
        reservations.append(1)
    with manager(tmp_path, gateway, reserve=reserve) as owner:
        skill, payload, kw = invocation(); owner.run(skill, payload, **kw)
        with pytest.raises(ValueError, match='outer_request_limit'): owner.run(skill, payload, **kw)
        assert len(gateway.calls) == 1
    with RecordStore(tmp_path / 'engine_records') as store:
        with jobs.ModelJobs(store, deadline=time.monotonic()+10, engine_deadline=lambda:time.monotonic()-.1,
                            reserve=lambda:reservations.append(2), check=lambda:None, gateway_url=gateway.url) as owner:
            with pytest.raises(TimeoutError): owner.run(skill, payload, **kw)
    assert reservations == [1]


def test_parent_revalidates_schema_path_and_does_not_export_private_path(tmp_path, gateway, monkeypatch):
    with manager(tmp_path, gateway) as owner:
        fake_child(monkeypatch, owner, "send('error',{'code':'schema','path':'$.PRIVATE_PROVIDER_SENTINEL'})")
        skill, payload, kw = invocation()
        with pytest.raises(SkillSchemaError) as caught: owner.run(skill, payload, **kw)
        assert caught.value.schema_path == '$'
        assert 'PRIVATE_PROVIDER_SENTINEL' not in str(caught.value)


def test_parent_rejects_wrong_stage_proof_before_ack(tmp_path, gateway, monkeypatch):
    with manager(tmp_path, gateway) as owner:
        fake_child(monkeypatch, owner, "send('proof',dict(stage='inputs',skill='ui_video_observe',skill_version='inputs',correlation='a'*32,gateway_request_sha256='a'*64,timeout_ms=1000));time.sleep(3)")
        skill, payload, kw = invocation()
        with pytest.raises(jobs.ModelJobError, match='protocol'): owner.run(skill, payload, **kw)
        assert not owner.accounting[0]['proof_persisted']


def test_actual_child_schema_failure_reaches_original_two_correction_pool(tmp_path, gateway, monkeypatch):
    from task_server.services import ui_video_trajectory as trajectory, ui_video_windows
    from test_ui_video_trajectory import frames
    skills = tmp_path / 'skills'; (skills / 'schemas').mkdir(parents=True); (skills / 'prompts').mkdir()
    (skills / 'prompts' / 'ui_video_trajectory.overview.md').write_text('original correction pool test')
    (skills / 'schemas' / 'ui_video_trajectory.schema.json').write_text(json.dumps({'type':'object','required':['states']}))
    monkeypatch.setattr(ui_video_windows, 'load_assets', lambda fs, *_: [
        {'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'], 'mime': 'image/jpeg', 'base64': 'YQ=='} for f in fs])
    with manager(tmp_path, gateway, skills_dir=skills) as owner:
        def run(skill, payload, **kwargs):
            assert kwargs['version'] == 'overview'
            return owner.run(skill, payload, **kwargs, video_stage='overview')
        with pytest.raises(SkillSchemaError):
            trajectory.observe_trajectory({'media': {'frames': frames(3), 'duration_ms': 401}}, None, lambda: False, skill_runner=run)
        assert len(gateway.calls) == len(owner.accounting) == 3
        assert all(r['receipt_persisted'] and not r['admitted'] and r['cleanup'] for r in owner.accounting)
        assert 'validation_feedback' in gateway.calls[1]['payload']


def test_cancel_during_proof_ack_prevents_actual_http(tmp_path, gateway):
    cancelled = []
    with manager(tmp_path, gateway, check=lambda: False if cancelled else None) as owner:
        skill, payload, kw = invocation()
        kw['request_proof_hook'] = lambda _: cancelled.append(True)
        with pytest.raises(jobs.ModelJobCancelled): owner.run(skill, payload, **kw)
        assert not gateway.calls and not owner.active_pids
        row = owner.accounting[0]
        assert row['proof_persisted'] and not row['dispatch_intent']


def test_reservation_before_build_and_oversized_request_never_dispatch(tmp_path, gateway):
    order = []
    with manager(tmp_path, gateway, reserve=lambda:order.append('reserve')) as owner:
        def build():
            order.append('build'); skill, payload, kw = invocation()
            payload['large'] = 'x' * jobs.MAX_REQUEST
            return skill, payload, kw
        with pytest.raises(jobs.ModelJobError, match='limit'): list(owner.map([build]))
        assert order == ['reserve', 'build'] and not gateway.calls and not owner.active_pids
        assert owner.accounting[0]['cleanup']


def test_raw_ack_timeout_prevents_parse_and_preserves_receipt(tmp_path, gateway):
    with manager(tmp_path, gateway) as owner:
        skill, payload, kw = invocation()
        kw['timeout'] = 1.5
        kw['raw_receipt'] = lambda _: time.sleep(1.5)
        with pytest.raises(TimeoutError): owner.run(skill, payload, **kw)
        assert len(gateway.calls) == 1
        row = owner.accounting[0]
        assert row['receipt_persisted'] and not row['admitted'] and row['cleanup']


def test_accounting_indices_remain_unique_across_serial_and_map_calls(tmp_path, gateway):
    with manager(tmp_path, gateway) as owner:
        skill, payload, kw = invocation(); owner.run(skill, payload, **kw)
        assert sorted(i for i, _ in owner.map([invocation, invocation])) == [0, 1]
        assert [r['index'] for r in owner.accounting] == [0, 1, 2]


def test_cancellation_records_terminal_reason_and_kills_unresponsive_children(tmp_path, gateway, monkeypatch):
    proofs, cancelled_at = [], []
    with manager(tmp_path, gateway, check=lambda: False if len(proofs) == 2 else None) as owner:
        body = '''
import signal
signal.signal(signal.SIGTERM,signal.SIG_IGN)
send('proof',dict(stage='overview',skill='ui_video_trajectory',skill_version='overview',correlation='a'*32,gateway_request_sha256='a'*64,timeout_ms=1000))
recv();time.sleep(10)
'''
        fake_child(monkeypatch, owner, body)
        def hook(_):
            proofs.append(True)
            if len(proofs) == 2: cancelled_at.append(time.monotonic())
        def build():
            skill, payload, kw = invocation(); kw['request_proof_hook'] = hook; return skill, payload, kw
        with pytest.raises(jobs.ModelJobCancelled): list(owner.map([build, build]))
        assert time.monotonic() - cancelled_at[0] < .6
        assert not owner.active_pids and not gateway.calls
        assert all('cancelled' in r['events'] and r['cleanup'] for r in owner.accounting)


def test_38_producer_builders_are_lazy_and_abandonment_has_no_late_writes(tmp_path, gateway):
    built = []
    with manager(tmp_path, gateway) as owner:
        def builders():
            for i in range(38):
                def build(i=i): built.append(i); return invocation()
                yield build
        iterator = owner.map(builders())
        next(iterator)
        assert built == [0, 1] and len(gateway.calls) == 2
        iterator.close()
        before = owner.records.snapshot()
        time.sleep(.1)
        assert owner.records.snapshot() == before and not owner.active_pids
        assert all(r['cleanup'] for r in owner.accounting)


class CleanupProcess:
    """Fault injection for operations that cannot be induced reliably in an OS."""
    pid = 123456789

    def __init__(self, failure):
        self.failure, self.calls = failure, []

    def poll(self):
        self.calls.append('poll')
        return None

    def terminate(self):
        self.calls.append('terminate')
        if self.failure == 'terminate': raise OSError('PRIVATE_CLEANUP_SENTINEL')

    def wait(self, timeout):
        self.calls.append('wait')
        if self.failure == 'wait': raise OSError('PRIVATE_CLEANUP_SENTINEL')
        if self.failure == 'kill': raise subprocess.TimeoutExpired('private', timeout)
        return 0

    def kill(self):
        self.calls.append('kill')
        if self.failure == 'kill': raise OSError('PRIVATE_CLEANUP_SENTINEL')


class CleanupChannel:
    def __init__(self, failure):
        self.failure, self.calls = failure, []

    def close(self):
        self.calls.append('close')
        if self.failure == 'close': raise OSError('PRIVATE_CLEANUP_SENTINEL')


def add_cleanup_double(owner, failure):
    row = dict(index=len(owner.accounting), cleanup=False, terminal=False, events=[])
    job = dict(process=CleanupProcess(failure), channel=CleanupChannel(failure), row=row, trace={})
    owner.accounting.append(row); owner._active.append(job)
    return job


@pytest.mark.parametrize('failure', ['terminate', 'close', 'wait', 'kill'])
def test_cleanup_failure_isolated_per_child_and_fixed_metadata(tmp_path, gateway, failure):
    owner = manager(tmp_path, gateway)
    first, sibling = add_cleanup_double(owner, failure), add_cleanup_double(owner, None)
    try:
        if failure == 'terminate':
            owner.close()  # The first wait still proves a successful reap.
            assert first['row']['cleanup']
        else:
            with pytest.raises(jobs.ModelJobError, match='^video_job_cleanup_failed$'):
                owner.close()
            assert not first['row']['cleanup']
        assert first['channel'].calls and 'terminate' in first['process'].calls
        assert 'wait' in first['process'].calls
        if failure in ('wait', 'kill'): assert 'kill' in first['process'].calls
        assert sibling['channel'].calls and 'terminate' in sibling['process'].calls
        assert 'wait' in sibling['process'].calls and sibling['row']['cleanup']
        assert sibling not in owner._active
        expected = {'wait': ['wait', 'reap'], 'kill': ['kill', 'reap']}.get(failure, [failure])
        assert owner.cleanup_failures == [dict(index=0, operations=expected)]
        assert 'PRIVATE' not in json.dumps(owner.cleanup_failures)
    finally:
        first['process'].failure = first['channel'].failure = None
        owner.close()


@pytest.mark.parametrize('entrypoint', ['close', 'dispose'])
def test_actual_two_child_cleanup_reaps_sibling_after_first_terminate_error(tmp_path, gateway, monkeypatch, entrypoint):
    owner = manager(tmp_path, gateway)
    fake_child(monkeypatch, owner, 'time.sleep(10)')
    owner._start(invocation, 0); owner._start(invocation, 1)
    children = [job['process'] for job in owner._active]
    monkeypatch.setattr(children[0], 'terminate', lambda: (_ for _ in ()).throw(OSError('PRIVATE_SIGNAL')))
    try:
        if entrypoint == 'dispose': owner._dispose(owner._active[0])
        owner.close()
        assert not owner.active_pids and all(child.poll() is not None for child in children)
        assert all(row['cleanup'] for row in owner.accounting)
        assert owner.cleanup_failures == [dict(index=0, operations=['terminate'])]
    finally:
        for child in children:
            if child.poll() is None: child.kill()
            child.wait(timeout=1)
        owner.close()


@pytest.mark.parametrize('terminal,late', [
    ('error', 'proof'), ('error', 'receipt'),
    ('result', 'proof'), ('result', 'receipt'), ('result', 'raw'),
    ('result', 'result'), ('result', 'error'), ('result', 'unknown'),
])
def test_post_terminal_ipc_never_writes_calls_hooks_acks_or_admits(tmp_path, gateway, monkeypatch, terminal, late):
    with manager(tmp_path, gateway) as owner:
        ack_file = tmp_path / 'late-ack'
        proof = dict(stage='overview', skill='ui_video_trajectory', skill_version='overview',
                     correlation='a'*32, gateway_request_sha256='a'*64, timeout_ms=1000)
        receipt = dict(content='{}', gateway_request_sha256='a'*64)
        body = 'p=' + repr(proof) + '\nv=' + repr(receipt) + '\n'
        prefix = []
        if terminal == 'result' or late == 'receipt':
            body += "send('proof',p);recv()\n"
            prefix.append('proof')
        if terminal == 'result':
            body += "send('receipt',v);recv()\n"
            prefix.append('receipt')
        body += "send(" + repr(terminal) + "," + ("{'code':'failed'}" if terminal == 'error' else '{}') + ");time.sleep(.08)\n"
        value = {'proof': proof, 'receipt': receipt, 'error': {'code':'failed'}}.get(late, {})
        body += "b=json.dumps(dict(event=" + repr(late) + ",value=" + repr(value) + ",trace={'usage':{'totalTokens':999}})).encode()\n"
        body += "s.sendall(struct.pack('!I',len(b))+b)\n"
        body += "try:\n a=recv()\n open(" + repr(str(ack_file)) + ",'w').write(json.dumps(a))\nexcept Exception:pass\n"
        fake_child(monkeypatch, owner, body)
        writes, hooks = [], []
        append = owner.records.append
        def write(kind, value):
            writes.append(kind)
            return append(kind, value)
        monkeypatch.setattr(owner.records, 'append', write)
        skill, payload, kw = invocation()
        kw.update(request_proof_hook=lambda _: hooks.append('proof'), raw_receipt=lambda _: hooks.append('receipt'))
        with pytest.raises(jobs.ModelJobError, match='^video_job_protocol_invalid$'):
            owner.run(skill, payload, **kw)
        assert writes == hooks == prefix
        assert not ack_file.exists()
        assert kw['runtime_trace'].get('usage', {}).get('totalTokens') != 999
        row = owner.accounting[0]
        assert not row['admitted'] and row['cleanup'] and not owner.active_pids
        assert row['proof_persisted'] == ('proof' in prefix)
        assert row['dispatch_intent'] == ('proof' in prefix)
        assert row['raw_received'] == row['receipt_persisted'] == ('receipt' in prefix)
        assert not owner.pending_receipts and not gateway.calls
        with pytest.raises(jobs.ModelJobError, match='latched'):
            owner.run(skill, payload, **kw)


@pytest.mark.parametrize('operation,original', [
    ('close', OSError('local writer failure')),
    ('poll', PermissionError('local authorization failure')),
    ('terminate', jobs.ModelJobCancelled('local cancellation')),
    ('wait', TimeoutError('local deadline')),
    ('kill', OSError('local callback failure')),
])
def test_recovered_cleanup_operation_preserves_original_parent_failure(tmp_path, gateway, monkeypatch, operation, original):
    owner = manager(tmp_path, gateway)
    first, sibling = add_cleanup_double(owner, None), add_cleanup_double(owner, None)
    target = first['channel'] if operation == 'close' else first['process']
    method = getattr(target, operation)
    calls = []
    def fail_once(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1: raise OSError('PRIVATE_TRANSIENT_FAILURE')
        return method(*args, **kwargs)
    monkeypatch.setattr(target, operation, fail_once)
    if operation == 'kill':
        waits = []
        def wait(timeout):
            waits.append(True)
            if len(waits) == 1: raise subprocess.TimeoutExpired('private', timeout)
            return 0  # Concurrent child exit can make reap succeed despite kill failure.
        monkeypatch.setattr(first['process'], 'wait', wait)
    owner.check = lambda: (_ for _ in ()).throw(original)
    try:
        with pytest.raises(type(original)) as caught: list(owner.map([]))
        assert caught.value is original
        assert first['row']['cleanup'] and sibling['row']['cleanup'] and not owner.active_pids
        assert owner.cleanup_failures == [dict(index=0, operations=[operation])]
    finally:
        owner.close()


@pytest.mark.parametrize('concurrency', [True, False, 0, 3, 5, -1, 1.0, 2.0, 4.0, '4', None])
def test_concurrency_rejected_before_any_builder_reservation_or_dispatch(tmp_path, gateway, concurrency):
    touched = []
    with manager(tmp_path, gateway, reserve=lambda: touched.append('reserve')) as owner:
        def builders():
            touched.append('iterate')
            yield lambda: touched.append('build')
        with pytest.raises(jobs.ModelJobError, match='owner_invalid'):
            list(owner.map(builders(), concurrency=concurrency))
        assert not touched and not owner.accounting and not gateway.calls


def test_real_http_four_peak_38_lazy_refill_and_ack_order(tmp_path, gateway):
    built, reserved = [], []
    with manager(tmp_path, gateway, reserve=lambda: reserved.append(True)) as owner:
        owner.deadline = time.monotonic() + 30
        def builders():
            for i in range(38):
                def build(i=i):
                    built.append(i)
                    assert len(built) <= sum(r['admitted'] for r in owner.accounting) + 4
                    return invocation()
                yield build
        iterator = owner.map(builders(), concurrency=4)
        first = next(iterator)
        assert built == [0, 1, 2, 3]
        results = [first, *iterator]
        assert sorted(i for i, _ in results) == list(range(38))
        assert len(reserved) == len(gateway.calls) == 38 and gateway.peak == 4
        for row in owner.accounting:
            events = row['events']
            assert events.index('proof_persisted') < events.index('dispatch_intent')
            assert events.index('raw_received') < events.index('receipt_persisted') < events.index('admitted')
            assert row['cleanup']
        assert not owner.active_pids
        # Same owner lifecycle as initial perception -> reasoning -> revisit -> reasoning.
        for revisit in (False, True, False):
            if revisit:
                assert sorted(i for i, _ in owner.map([invocation] * 4, concurrency=4)) == list(range(4))
            else:
                skill, payload, kw = invocation()
                assert owner.run(skill, payload, **kw) == {}
            assert owner._cleanup_deadline is None and not owner._busy and not owner.active_pids
        assert len(owner.accounting) == len(gateway.calls) == 44
        assert all(row['cleanup'] and row['admitted'] for row in owner.accounting)


@pytest.mark.parametrize('failure', ['cancel', 'permission', 'deadline', 'proof', 'receipt', 'schema', 'abandon'])
def test_four_siblings_fail_closed_without_refill_or_late_records(tmp_path, monkeypatch, failure):
    gateway = Gateway(hold=failure in ('cancel', 'permission', 'deadline'))
    built = []
    def check():
        if len(gateway.calls) == 4:
            if failure == 'cancel': return False
            if failure == 'permission': raise PermissionError('revoked')
    try:
        with manager(tmp_path, gateway, check=check) as owner:
            if failure in ('proof', 'receipt'):
                append = owner.records.append
                def write(kind, value):
                    if kind == failure: raise OSError('writer sentinel')
                    return append(kind, value)
                monkeypatch.setattr(owner.records, 'append', write)
            if failure == 'schema':
                skills = tmp_path / 'skills'
                (skills / 'schemas').mkdir(parents=True)
                (skills / 'prompts').mkdir()
                (skills / 'prompts' / 'ui_video_trajectory.overview.md').write_text('schema test')
                (skills / 'schemas' / 'ui_video_trajectory.schema.json').write_text(
                    json.dumps({'type': 'object', 'required': ['states']}))
                owner.child_environment['AI_SKILLS_DIR'] = str(skills)
            def build():
                built.append(True)
                skill, payload, kw = invocation()
                if failure == 'deadline': kw['timeout'] = 2
                return skill, payload, kw
            iterator = owner.map(iter([build] * 38), concurrency=4)
            if failure == 'abandon':
                next(iterator); iterator.close()
            else:
                expected = {'cancel': jobs.ModelJobCancelled, 'permission': PermissionError,
                            'deadline': TimeoutError, 'proof': OSError, 'receipt': OSError,
                            'schema': SkillSchemaError}[failure]
                with pytest.raises(expected): list(iterator)
            assert len(built) == 4 and not owner.active_pids
            assert all(row['cleanup'] for row in owner.accounting)
            snapshot = owner.records.snapshot()
            time.sleep(.1)
            assert snapshot == owner.records.snapshot()
            if failure != 'schema':
                with pytest.raises(jobs.ModelJobError, match='latched'):
                    list(owner.map([invocation]))
            if failure == 'proof': assert not gateway.calls
            if failure == 'receipt': assert owner.pending_receipts
    finally:
        gateway.close()


@pytest.mark.parametrize('context', [False, True])
def test_four_unreapable_children_share_cleanup_reserve_and_keep_original_error(tmp_path, gateway, monkeypatch, context):
    owner = manager(tmp_path, gateway)
    siblings = [add_cleanup_double(owner, None) for _ in range(4)]
    waits = []
    for job in siblings:
        def wait(timeout, job=job):
            waits.append((job['row']['index'], timeout))
            time.sleep(timeout)
            raise subprocess.TimeoutExpired('private', timeout)
        monkeypatch.setattr(job['process'], 'wait', wait)
    original = PermissionError('original parent failure')
    owner.check = lambda: (_ for _ in ()).throw(original)
    before = time.monotonic()
    try:
        with pytest.raises(PermissionError) as caught:
            if context:
                with owner: list(owner.map([]))
            else:
                list(owner.map([]))
        assert caught.value is original
        assert time.monotonic() - before < jobs.CLEANUP_SECONDS + .12
        assert not owner._busy
        assert {index for index, _ in waits} == set(range(4))
        assert all('kill' in job['process'].calls and job['channel'].calls for job in siblings)
        assert all(not row['cleanup'] for row in owner.accounting) and owner._latched
        assert len(owner.cleanup_failures) == 4
    finally:
        for job in siblings: monkeypatch.setattr(job['process'], 'wait', lambda timeout: 0)
        owner.close()


def test_four_actual_sigterm_resistant_children_join_with_one_reserve(tmp_path, gateway, monkeypatch):
    owner = manager(tmp_path, gateway)
    ready = tmp_path / 'ready'
    fake_child(monkeypatch, owner, 'import signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
               + 'open(' + repr(str(ready)) + ',"a").write("1")\ntime.sleep(10)')
    for i in range(4): owner._start(invocation, i)
    children = [job['process'] for job in owner._active]
    # Drain the startup request so every child installs its handler before close.
    end = time.monotonic() + 3
    try:
        while (not ready.exists() or len(ready.read_text()) < 4) and time.monotonic() < end:
            owner._step()
        assert ready.read_text() == '1111'
        before = time.monotonic()
        owner.close()
        assert time.monotonic() - before < jobs.CLEANUP_SECONDS + .12
        assert not owner.active_pids and all(p.poll() is not None for p in children)
        assert all(row['cleanup'] for row in owner.accounting)
    finally:
        for process in children:
            if process.poll() is None: process.kill()
            process.wait(timeout=1)
        owner.close()
