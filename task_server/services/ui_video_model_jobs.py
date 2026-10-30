"""Owned, bounded normal-Gateway subprocesses for the opt-in video runtime.

ModelJobs(records, deadline=<outer monotonic>, reserve=<shared no-arg callback>,
          check=<auth/cancel no-arg callback>, engine_deadline=<optional callable>)
is a single-parent-thread context manager. check may raise its original local
error or return False to cancel. reserve owns ALL request caps; no refund or
second request budget exists here. Engine deadline returns an absolute time or
None. Both deadlines include cleanup. run has the run_ai_skill keyword shape,
but requires an explicit stage, forbids fallback/retry and never chooses stages.

map receives a lazy iterable of zero-argument builders returning (skill, payload,
kwargs); defaults to two owned jobs, with explicit one/four-job options. It
yields (input_index, result) in completion order. Refill occurs only when the caller resumes iteration, after
its own binding/publication. Builders/check/reserve/hooks stay in the parent,
never pickled. Builders must be bounded and cooperatively call check themselves
when doing long work; checks bracket every build. Caller must use the context
manager and close an abandoned iterator before starting another invocation.

Only the parent appends evidence. Proof and raw persistence ACK precede child
HTTP and parsing respectively. accounting is private bounded metadata, not a
receipt counter: dispatch ACK without a receipt means transport UNKNOWN. Pending
raw receipts on writer failure remain in bounded parent memory for diagnosis;
failed storage cannot be claimed durable. Terminate/wait proves local cleanup,
not provider billing cessation. Gateway 5a supplies downstream AbortSignal.
"""
import copy
import json
import math
import os
from pathlib import Path
import re
import selectors
import socket
import struct
import subprocess
import sys
import threading
import time

from .ui_video_records import safe_record
from .ui_video_stage_policy import STAGES, video_stage_image_limit

MAX_REQUEST = 16 * 1024 * 1024
MAX_MESSAGE = 1024 * 1024
POLL_SECONDS = .025
CLEANUP_SECONDS = .5


class ModelJobError(RuntimeError):
    """Closed safe child/IPC classification, never arbitrary provider text."""


class ModelJobCancelled(ModelJobError):
    pass


def _encode(value, limit):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        raise ModelJobError('video_job_message_invalid') from None
    if not 0 < len(data) <= limit:
        raise ModelJobError('video_job_message_limit')
    return struct.pack('!I', len(data)) + data


def _decode(data):
    try:
        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError()
                out[key] = value
            return out
        value = json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ModelJobError('video_job_message_invalid') from None


def _safe_trace(trace, stage=None):
    if not isinstance(trace, dict):
        raise ModelJobError('video_job_message_invalid')
    receipt = safe_record('receipt', {**trace, 'content': '', 'gateway_request_sha256': '0' * 64})
    out = {key: receipt[key] for key in ('providerId', 'model', 'fallbackUsed', 'finishReason', 'usage')}
    if trace.get('source') == 'ai_gateway':
        out['source'] = 'ai_gateway'
    for key in ('selectedModel',):
        if trace.get(key) in ('qwen3.8-max', 'qwen3.8-flash'):
            out[key] = trace[key]
    if type(trace.get('imageCount')) is int and 0 <= trace['imageCount'] <= video_stage_image_limit(stage):
        out['imageCount'] = trace['imageCount']
    return out


def _schema_path(path, skill, skills_dir=None):
    """Only schema-owned property paths cross IPC; no rejected model values."""
    from .ai_skill_service import load_ai_skill_schema
    if type(path) is not str or len(path) > 512 or not re.fullmatch(r'\$(?:\.[a-zA-Z_][a-zA-Z_0-9]*)*', path):
        return '$'
    if skills_dir is None:
        schema = load_ai_skill_schema(skill)
    else:
        try:
            with open(Path(skills_dir) / 'schemas' / (skill + '.schema.json'), 'rb') as source:
                raw = source.read(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE:
                return '$'
            schema = json.loads(raw)
        except (OSError, ValueError, RecursionError):
            return '$'
    for key in path.split('.')[1:]:
        if key not in schema.get('properties', {}) and key not in schema.get('required', []):
            return '$'
        schema = schema.get('properties', {}).get(key, {})
    return path


def _child_main(fd):
    """Fresh interpreter imports config only after Popen's sanitized env exists."""
    from .ai_skill_service import run_ai_skill, SkillSchemaError
    channel = socket.socket(fileno=fd)
    deadline, trace, skill, stage = time.monotonic() + 120, {}, None, None

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('video_job_deadline')
        channel.settimeout(left)

    def receive(limit):
        def exact(size):
            out = bytearray()
            while len(out) < size:
                remaining()
                chunk = channel.recv(min(size - len(out), 65536))
                if not chunk:
                    raise ModelJobError('video_job_parent_closed')
                out.extend(chunk)
            return bytes(out)
        size = struct.unpack('!I', exact(4))[0]
        if not 0 < size <= limit:
            raise ModelJobError('video_job_message_limit')
        return _decode(exact(size))

    def send(value):
        data = memoryview(_encode(value, MAX_MESSAGE))
        while data:
            remaining()
            written = channel.send(data[:65536])
            if not written:
                raise ModelJobError('video_job_parent_closed')
            data = data[written:]

    def hook(event, value):
        send({'event': event, 'value': value, 'trace': _safe_trace(trace, stage)})
        if receive(256) != {'ack': event}:
            raise ModelJobError('video_job_ack_invalid')
        remaining()

    try:
        request = receive(MAX_REQUEST)
        if set(request) != {'skill', 'payload', 'kwargs', 'deadline'}:
            raise ModelJobError('video_job_message_invalid')
        deadline, skill = request['deadline'], request['skill']
        remaining()
        kwargs = request['kwargs']
        stage = kwargs.get('video_stage')
        kwargs['timeout'] = min(kwargs['timeout'], deadline - time.monotonic())
        result = run_ai_skill(skill, request['payload'], **kwargs, runtime_trace=trace,
                              request_proof_hook=lambda value: hook('proof', value),
                              raw_receipt=lambda value: hook('receipt', value))
        send({'event': 'result', 'value': result, 'trace': _safe_trace(trace, stage)})
    except Exception as exc:
        code = 'schema' if isinstance(exc, SkillSchemaError) else 'timeout' if isinstance(exc, TimeoutError) else 'failed'
        value = {'code': code}
        if code == 'schema':
            value['path'] = _schema_path(exc.schema_path, skill)
        try:
            send({'event': 'error', 'value': value, 'trace': _safe_trace(trace, stage)})
        except Exception:
            pass
    finally:
        channel.close()


class ModelJobs:
    def __init__(self, records, *, deadline, reserve, check, engine_deadline=None,
                 gateway_url=None, skills_dir=None):
        from task_server.config import AI_GATEWAY_URL, AI_SKILLS_DIR
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or not callable(reserve) or not callable(check):
            raise ValueError('video_job_options_invalid')
        self.records, self.deadline = records, deadline
        self.reserve, self.check, self.engine_deadline = reserve, check, engine_deadline
        self._thread = (os.getpid(), threading.get_ident())
        self._active, self.accounting, self.pending_receipts = [], [], []
        self.cleanup_failures = []  # At most four rows, closed operation names only.
        self._closed, self._latched, self._busy = False, False, False
        self._cleanup_deadline = None  # Failed cleanup cannot renew its reserve on unwind.
        # No HOME/PYTHONPATH/proxy/Runner/provider key inheritance, prefix copies,
        # config-file access, or process-global environment changes.
        self.child_environment = {k: os.environ[k] for k in ('LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SYSTEMROOT') if k in os.environ}
        self.child_environment.update(MIDSCENE_ENV_FILE='', AI_GATEWAY_URL=str(gateway_url or AI_GATEWAY_URL),
                                      AI_SKILLS_DIR=str(skills_dir or AI_SKILLS_DIR), PYTHONIOENCODING='utf-8')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            self.close()
        except ModelJobError:
            if exc_type is None:
                raise  # Preserve an existing parent error; diagnostics retain cleanup failure.

    @property
    def active_pids(self):
        return [job['process'].pid for job in self._active]

    def _guard(self):
        if self._closed or self._latched:
            raise ModelJobError('video_job_latched')
        if self._thread != (os.getpid(), threading.get_ident()):
            raise ModelJobError('video_job_owner_invalid')
        if self.check() is False:
            raise ModelJobCancelled('video_job_cancelled')
        deadline = self.deadline
        if self.engine_deadline is not None:
            engine = self.engine_deadline()
            if engine is not None:
                if type(engine) not in (int, float) or not math.isfinite(engine):
                    raise ModelJobError('video_job_deadline_invalid')
                deadline = min(deadline, engine)
        deadline -= CLEANUP_SECONDS
        if time.monotonic() >= deadline:
            raise TimeoutError('video_job_deadline')
        return deadline

    def _launch(self):
        parent, child = socket.socketpair()
        root = str(Path(__file__).resolve().parents[2])
        code = 'import sys;sys.path.insert(0,sys.argv[1]);from task_server.services.ui_video_model_jobs import _child_main;_child_main(int(sys.argv[2]))'
        try:
            process = subprocess.Popen([sys.executable, '-E', '-c', code, root, str(child.fileno())],
                                       env=dict(self.child_environment), cwd=root, pass_fds=(child.fileno(),),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            parent.close()
            raise
        finally:
            child.close()
        parent.setblocking(False)
        return process, parent

    def _start(self, builder, index):
        deadline = self._guard()
        self.reserve()  # No refund: even a failed build consumed this reservation.
        row = dict(index=len(self.accounting), reserved=True, proof_persisted=False, dispatch_intent=False,
                   raw_received=False, receipt_persisted=False, admitted=False, terminal=False,
                   cleanup=False, transport_status='not_dispatched', events=['reserved'])
        self.accounting.append(row)
        self._guard()
        skill, payload, kwargs = builder()
        deadline = min(deadline, self._guard())
        kwargs = dict(kwargs)
        trace = kwargs.pop('runtime_trace', {})
        hooks = {event: kwargs.pop(key, None) for event, key in (('proof', 'request_proof_hook'), ('receipt', 'raw_receipt'))}
        if not isinstance(trace, dict) or any(v is not None and not callable(v) for v in hooks.values()):
            raise ValueError('video_job_options_invalid')
        trace.pop('error', None)
        allowed = {'version', 'temperature', 'timeout', 'image_assets', 'respect_global_timeout', 'retry_count',
                   'max_tokens', 'repair_invalid_json', 'video_stage', 'output_schema', 'model_config',
                   'fallback_prompt', 'output_defaults'}
        stage = kwargs.get('video_stage')
        if (set(kwargs) - allowed or type(stage) is not str or stage not in STAGES or skill != STAGES[stage][0] or
                kwargs.get('version', 'v1') != STAGES[stage][1] or kwargs.get('retry_count') not in (None, 0) or
                kwargs.get('repair_invalid_json', False) is not False or kwargs.get('fallback_prompt') or
                kwargs.get('output_defaults') is not None):
            raise ValueError('video_job_options_invalid')
        timeout = kwargs.get('timeout', 120)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('video_job_options_invalid')
        deadline = min(deadline, time.monotonic() + min(120, timeout))
        kwargs.update(timeout=deadline - time.monotonic(), respect_global_timeout=False, retry_count=0, repair_invalid_json=False)
        wire = _encode({'skill': skill, 'payload': payload, 'kwargs': kwargs, 'deadline': deadline}, MAX_REQUEST)
        self._guard()
        process, channel = self._launch()
        job = dict(process=process, channel=channel, send=memoryview(wire), buffer=bytearray(), size=None,
                   row=row, deadline=deadline, trace=trace, hooks=hooks, skill=skill,
                   phase='proof', final=None, eof=False, final_at=None, stage=stage, input_index=index)
        self._active.append(job)

    def _event(self, job, message):
        if job['final'] is not None or set(message) != {'event', 'value', 'trace'}:
            raise ModelJobError('video_job_protocol_invalid')
        event, value, row = message['event'], message['value'], job['row']
        job['trace'].update(_safe_trace(message['trace'], job['stage']))
        if event in ('proof', 'receipt'):
            if job['phase'] != event or job['send']:
                raise ModelJobError('video_job_protocol_invalid')
            safe = safe_record(event, value)
            if event == 'proof' and (safe['stage'] != job['stage'] or safe['skill'] != job['skill']):
                raise ModelJobError('video_job_protocol_invalid')
            if event == 'receipt':
                if safe['gateway_request_sha256'] != job['proof']['gateway_request_sha256']:
                    raise ModelJobError('video_job_protocol_invalid')
                row.update(raw_received=True, transport_status='receipt_received')
                row['events'].append('raw_received')
                self.pending_receipts.append(safe)
            self._guard()
            ref = self.records.append(event, safe)
            row[event + '_ref'] = ref
            row['proof_persisted' if event == 'proof' else 'receipt_persisted'] = True
            row['events'].append(event + '_persisted')
            if event == 'receipt':
                self.pending_receipts.remove(safe)
            if job['hooks'][event] is not None:
                job['hooks'][event](copy.deepcopy(safe))
            self._guard()
            if time.monotonic() >= job['deadline']:
                raise TimeoutError('video_job_deadline')
            job['send'] = memoryview(_encode({'ack': event}, 256))
            job['phase'] = 'receipt' if event == 'proof' else 'final'
            if event == 'proof':
                job['proof'] = safe
                row.update(dispatch_intent=True, transport_status='unknown')
                row['events'].append('dispatch_intent')
        elif event in ('result', 'error'):
            if event == 'result' and job['phase'] != 'final':
                raise ModelJobError('video_job_protocol_invalid')
            if event == 'error' and (type(value) is not dict or value.get('code') not in ('schema', 'timeout', 'failed') or set(value) - {'code', 'path'}):
                raise ModelJobError('video_job_protocol_invalid')
            job['final'], job['final_at'] = (event, value), time.monotonic()
        else:
            raise ModelJobError('video_job_protocol_invalid')

    def _step(self):
        self._guard()
        with selectors.DefaultSelector() as selector:
            for job in self._active:
                if time.monotonic() >= job['deadline']:
                    raise TimeoutError('video_job_deadline')
                selector.register(job['channel'], selectors.EVENT_READ | (selectors.EVENT_WRITE if job['send'] else 0), job)
            for key, mask in selector.select(POLL_SECONDS):
                job = key.data
                if mask & selectors.EVENT_WRITE and job['send']:
                    try:
                        count = job['channel'].send(job['send'][:65536])
                        job['send'] = job['send'][count:]
                    except BlockingIOError:
                        pass
                if mask & selectors.EVENT_READ:
                    try:
                        chunk = job['channel'].recv(65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        job['eof'] = True
                    job['buffer'].extend(chunk)
                    if job['size'] is None and len(job['buffer']) >= 4:
                        job['size'] = struct.unpack('!I', job['buffer'][:4])[0]
                        del job['buffer'][:4]
                        if not 0 < job['size'] <= MAX_MESSAGE:
                            raise ModelJobError('video_job_message_limit')
                    if job['size'] is not None and len(job['buffer']) >= job['size']:
                        if len(job['buffer']) != job['size']:
                            raise ModelJobError('video_job_protocol_invalid')
                        message = _decode(job['buffer'])
                        job['buffer'], job['size'] = bytearray(), None
                        self._event(job, message)
        for job in list(self._active):
            code = job['process'].poll()
            if job['eof'] and code is not None:
                if job['buffer'] or job['size'] is not None or code != 0 or job['final'] is None:
                    raise ModelJobError('video_job_child_exit')
                return job
            if job['final_at'] is not None and time.monotonic() - job['final_at'] > 1:
                raise ModelJobError('video_job_child_exit')
        return None

    def _cleanup_failure(self, job, operation):
        # Keep only the latest four owned jobs; never export exception text.
        index = job['row']['index']
        entry = next((row for row in self.cleanup_failures if row['index'] == index), None)
        if entry is None:
            entry = dict(index=index, operations=[])
            self.cleanup_failures.append(entry)
            self.cleanup_failures[:] = self.cleanup_failures[-4:]
        if operation not in entry['operations']:
            entry['operations'].append(operation)

    def _cleanup_call(self, job, operation, callback):
        try:
            callback()
            return True
        except Exception:
            self._cleanup_failure(job, operation)
            return False

    def _signal(self, job):
        if not job.get('channel_closed'):
            job['channel_closed'] = self._cleanup_call(job, 'close', job['channel'].close)
        process = job['process']
        try:
            alive = process.poll() is None
        except Exception:
            self._cleanup_failure(job, 'poll')
            alive = True
        if alive:
            self._cleanup_call(job, 'terminate', process.terminate)

    def _dispose(self, job, *, signal=True, deadline=None, grace_deadline=None):
        if deadline is None:
            deadline = self._cleanup_deadline or time.monotonic() + CLEANUP_SECONDS
        if grace_deadline is None:
            grace_deadline = min(deadline, time.monotonic() + .1)
        if signal:
            self._signal(job)
        elif not job.get('channel_closed'):
            job['channel_closed'] = self._cleanup_call(job, 'close', job['channel'].close)
        process, reaped = job['process'], False
        try:
            process.wait(timeout=max(0, min(.1, grace_deadline - time.monotonic())))
            reaped = True
        except subprocess.TimeoutExpired:
            pass  # Expected escalation, not an unsuccessful cleanup by itself.
        except Exception:
            self._cleanup_failure(job, 'wait')
        if not reaped:
            self._cleanup_call(job, 'kill', process.kill)
            reaped = self._cleanup_call(job, 'reap', lambda: process.wait(timeout=max(0, min(.1, deadline - time.monotonic()))))
        job['row']['terminal'] = True
        if not reaped or not job['channel_closed']:
            self._latched = True
            self._cleanup_deadline = deadline
            raise ModelJobError('video_job_cleanup_failed') from None
        job['row']['cleanup'] = True
        job['row']['events'].append('cleanup')
        self._active.remove(job)

    def _stop_all(self):
        # All siblings share the same reserve, including the graceful wait.
        deadline = self._cleanup_deadline or time.monotonic() + CLEANUP_SECONDS
        grace_deadline = min(deadline, time.monotonic() + .1)
        # Every socket/signal is attempted before any child's bounded wait.
        for job in self._active:
            self._signal(job)
        failures = []
        for job in list(self._active):
            try:
                self._dispose(job, signal=False, deadline=deadline, grace_deadline=grace_deadline)
            except ModelJobError:
                failures.append(job)
        for row in self.accounting:
            if not row['terminal'] and not any(job['row'] is row for job in failures):
                row.update(terminal=True, cleanup=True)
                row['events'].append('cancelled_before_launch')
        if failures:
            raise ModelJobError('video_job_cleanup_failed') from None
        self._cleanup_deadline = None

    def close(self):
        self._closed = True
        self._stop_all()

    def run(self, skill, payload=None, **kwargs):
        iterator = self.map([lambda: (skill, payload, kwargs)], concurrency=1)
        try:
            result = next(iterator)[1]
            next(iterator, None)
            return result
        finally:
            iterator.close()

    def map(self, builders, *, concurrency=2):
        """Yield indexed results lazily; explicit concurrency is integer 1, 2 or 4."""
        if type(concurrency) is not int or concurrency not in (1, 2, 4) or self._busy:
            raise ModelJobError('video_job_owner_invalid')
        self._busy = True
        iterator, index, exhausted = iter(builders), 0, False
        try:
            while True:
                self._guard()
                while not exhausted and len(self._active) < concurrency:
                    self._guard()
                    try:
                        builder = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    self._start(builder, index)
                    index += 1
                if not self._active:
                    return
                job = self._step()
                if job is None:
                    continue
                event, value = job['final']
                self._guard()
                self._dispose(job)
                if event == 'error':
                    code = value['code']
                    job['trace']['error'] = 'video_job_' + code
                    job['row']['events'].append(code)
                    if code == 'schema':
                        from .ai_skill_service import SkillSchemaError
                        # Child validated against the configured schema; syntax is
                        # checked again here. Fixed feedback never embeds values.
                        path = _schema_path(value.get('path'), job['skill'], self.child_environment['AI_SKILLS_DIR'])
                        raise SkillSchemaError(path, '输出不符合 schema')
                    if code == 'timeout':
                        raise TimeoutError('video_job_deadline')
                    raise ModelJobError('video_job_failed')
                job['row']['admitted'] = True
                job['row']['events'].append('admitted')
                yield job['input_index'], value
        except BaseException as exc:
            from .ai_skill_service import SkillSchemaError
            if not isinstance(exc, SkillSchemaError):
                self._latched = True
            reason = ('cancelled' if isinstance(exc, SkillSchemaError) else 'timeout' if isinstance(exc, TimeoutError)
                      else 'cancelled' if isinstance(exc, (ModelJobCancelled, PermissionError, GeneratorExit)) else 'failed')
            for job in self._active:
                job['trace']['error'] = 'video_job_' + reason
            for row in self.accounting:
                if not row['terminal']:
                    row['events'].append(reason)
            raise
        finally:
            original = sys.exc_info()[1]
            try:
                self._stop_all()
            except ModelJobError:
                if original is None:
                    raise
            finally:
                self._busy = False
