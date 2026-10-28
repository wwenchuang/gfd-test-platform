"""Private parent-only evidence shards; no source state or device writes.

Entry refs pin immutable logical JSON, not a mutable shard filename/hash. The
parent's current manifest pins full shard bytes. snapshot() returns a detached,
immutable-by-contract manifest: persist it in the version-owned private result
only after all writes finish. open_snapshot() verifies every file and entry
before allowing reads; an older snapshot fails after a shard has changed.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from .ui_video_stage_policy import STAGES
from .ui_video_stage_receipts import safe_attestation

MAX_FILES = 64
MAX_TOTAL = 16 * 1024 * 1024
MAX_FILE = 1024 * 1024
GROUP_ENTRIES = 16


class RecordError(RuntimeError):
    """Static safe evidence failure; the store is latched after any failure."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _ref(value):
    return (type(value) is dict and set(value) == {'entry', 'sha256'} and
            type(value['entry']) is int and 1 <= value['entry'] <= 1024 and _digest(value['sha256']))


def safe_record(kind, value):
    """Closed metadata projection. Original content string is never rewritten."""
    if not isinstance(value, dict):
        raise RecordError('video_record_invalid')
    if kind == 'aggregate':
        if set(value) != {'refs'} or not isinstance(value['refs'], list) or len(value['refs']) > 1024 or not all(map(_ref, value['refs'])):
            raise RecordError('video_record_invalid')
        return copy.deepcopy(value)
    if kind == 'status':
        if type(value.get('index')) is not int or not 0 <= value['index'] < 80:
            raise RecordError('video_record_invalid')
        out = {'index': value['index']}
        for key in ('reserved', 'proof_persisted', 'dispatch_intent', 'raw_received',
                    'receipt_persisted', 'admitted', 'terminal', 'cleanup'):
            if key in value:
                if type(value[key]) is not bool:
                    raise RecordError('video_record_invalid')
                out[key] = value[key]
        status = value.get('transport_status')
        if type(status) is not str or status not in {'not_dispatched', 'unknown', 'receipt_received'}:
            raise RecordError('video_record_invalid')
        out['transport_status'] = status
        events = value.get('events')
        allowed = {'reserved', 'proof_persisted', 'dispatch_intent', 'raw_received', 'receipt_persisted',
                   'cleanup', 'admitted', 'schema', 'timeout', 'failed', 'cancelled', 'cancelled_before_launch'}
        if not isinstance(events, list) or len(events) > 16 or any(type(e) is not str or e not in allowed for e in events):
            raise RecordError('video_record_invalid')
        out['events'] = list(events)
        for key in ('proof_ref', 'receipt_ref'):
            if key in value:
                if not _ref(value[key]):
                    raise RecordError('video_record_invalid')
                out[key] = copy.deepcopy(value[key])
        return out
    if kind == 'proof':
        stage = value.get('stage')
        if (type(stage) is not str or stage not in STAGES or
                value.get('skill') != STAGES[stage][0] or value.get('skill_version') != STAGES[stage][1] or
                type(value.get('correlation')) is not str or not re.fullmatch('[0-9a-f]{32}', value['correlation']) or
                not _digest(value.get('gateway_request_sha256')) or
                type(value.get('timeout_ms')) is not int or not 1 <= value['timeout_ms'] <= 120000):
            raise RecordError('video_record_invalid')
        return {k: value[k] for k in ('stage', 'skill', 'skill_version', 'correlation', 'gateway_request_sha256', 'timeout_ms')}
    if kind != 'receipt' or type(value.get('content')) is not str or not _digest(value.get('gateway_request_sha256')):
        raise RecordError('video_record_invalid')
    out = {'content': value['content'], 'gateway_request_sha256': value['gateway_request_sha256']}
    # IDs are policy metadata, never arbitrary provider strings/actor IDs.
    out['providerId'] = value.get('providerId') if value.get('providerId') in ('official', 'qwen_plus') else None
    out['model'] = value.get('model') if value.get('model') in ('qwen3.8-max', 'qwen3.8-flash') else None
    out['fallbackUsed'] = value.get('fallbackUsed') if type(value.get('fallbackUsed')) is bool else None
    out['finishReason'] = value.get('finishReason') if type(value.get('finishReason')) is str and value['finishReason'] in {'stop', 'length', 'content_filter', 'tool_calls', 'function_call', ''} else None
    usage = value.get('usage')
    out['usage'] = {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                    if k in {'promptTokens', 'completionTokens', 'reasoningTokens', 'totalTokens'} and
                    type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 2**53}
    out['videoPolicy'] = safe_attestation(value.get('videoPolicy'))
    if type(value.get('http_status')) is int and 400 <= value['http_status'] <= 599:
        out['http_status'] = value['http_status']
    if type(value.get('rejection_code')) is str and value['rejection_code'] in {
            'video_stage_gateway_rejected', 'video_stage_actual_model_invalid', 'video_stage_provider_failed',
            'video_stage_cancelled', 'video_stage_deadline', 'video_perception_reasoning_usage_invalid'}:
        out['rejection_code'] = value['rejection_code']
    return out


def _open_directory(path, create):
    """Walk each component with no-follow dirfds, including all ancestors."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            if create and index == len(path.parts) - 2:
                os.mkdir(part, 0o700, dir_fd=fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise RecordError('video_record_permissions')
        return fd
    except BaseException:
        os.close(fd)
        raise


class RecordStore:
    """One owning process/thread. Limits may only be lowered, including temps."""

    def __init__(self, path, *, max_files=MAX_FILES, max_total=MAX_TOTAL, max_file=MAX_FILE):
        self._init(max_files, max_total, max_file)
        try:
            self._fd = _open_directory(path, True)
        except Exception:
            raise RecordError('video_record_directory_invalid') from None

    def _init(self, files, total, per_file):
        if any(type(v) is not int or not 1 <= v <= limit for v, limit in
               ((files, MAX_FILES), (total, MAX_TOTAL), (per_file, MAX_FILE))):
            raise RecordError('video_record_limit')
        self.max_files, self.max_total, self.max_file = files, total, per_file
        self._owner = (os.getpid(), threading.get_ident())
        self._files, self._entries = [], {}
        self._fd, self._failed, self._readonly = None, False, False

    @classmethod
    def open_snapshot(cls, path, snapshot):
        store = cls.__new__(cls)
        store._init(MAX_FILES, MAX_TOTAL, MAX_FILE)
        store._readonly = True
        try:
            if type(snapshot) is not dict or set(snapshot) != {'version', 'files'} or type(snapshot['version']) is not int or snapshot['version'] != 1:
                raise RecordError('video_record_snapshot_invalid')
            store._files = copy.deepcopy(snapshot['files'])
            store._fd = _open_directory(path, False)
            store._verify()
            return store
        except Exception:
            store.close()
            raise RecordError('video_record_snapshot_invalid') from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _check(self):
        if self._failed or self._fd is None or self._owner != (os.getpid(), threading.get_ident()):
            raise RecordError('video_record_unavailable')
        if stat.S_IMODE(os.fstat(self._fd).st_mode) != 0o700:
            raise RecordError('video_record_permissions')

    def _verify(self):
        self._check()
        if not isinstance(self._files, list) or len(self._files) > self.max_files:
            raise RecordError('video_record_limit')
        expected, entries, total = [], {}, 0
        for index, item in enumerate(self._files):
            name = f'shard-{index:03d}.json'
            if (type(item) is not dict or set(item) != {'name', 'size', 'sha256'} or item['name'] != name or
                    type(item['size']) is not int or not 0 < item['size'] <= self.max_file or not _digest(item['sha256'])):
                raise RecordError('video_record_manifest_invalid')
            expected.append(name)
            total += item['size']
            if total > self.max_total:
                raise RecordError('video_record_limit')
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != item['size']:
                    raise RecordError('video_record_file_invalid')
                raw = source.read(self.max_file + 1)
            if len(raw) != item['size'] or _sha(raw) != item['sha256']:
                raise RecordError('video_record_hash_invalid')
            group = json.loads(raw)
            if type(group) is not dict or set(group) != {'version', 'entries'} or type(group['version']) is not int or group['version'] != 1 or not isinstance(group['entries'], list) or not 1 <= len(group['entries']) <= GROUP_ENTRIES:
                raise RecordError('video_record_file_invalid')
            for entry in group['entries']:
                if type(entry) is not dict or set(entry) != {'entry', 'kind', 'value', 'sha256'} or type(entry['entry']) is not int or entry['entry'] != len(entries) + 1:
                    raise RecordError('video_record_entry_invalid')
                logical = {k: entry[k] for k in ('entry', 'kind', 'value')}
                if _sha(_json(logical)) != entry['sha256'] or safe_record(entry['kind'], entry['value']) != entry['value']:
                    raise RecordError('video_record_hash_invalid')
                entries[entry['entry']] = (index, entry)
        if sorted(os.listdir(self._fd)) != sorted(expected):
            raise RecordError('video_record_unknown_file')
        for _, entry in entries.values():
            if entry['kind'] in ('aggregate', 'status'):
                refs = entry['value']['refs'] if entry['kind'] == 'aggregate' else [entry['value'][k] for k in ('proof_ref', 'receipt_ref') if k in entry['value']]
                for ref in refs:
                    if ref['entry'] >= entry['entry'] or ref['entry'] not in entries or entries[ref['entry']][1]['sha256'] != ref['sha256']:
                        raise RecordError('video_record_reference_invalid')
        self._entries = entries

    def append(self, kind, value):
        temp = None
        try:
            self._verify()
            if self._readonly:
                raise RecordError('video_record_readonly')
            value = safe_record(kind, value)
            if kind in ('aggregate', 'status'):
                refs = value['refs'] if kind == 'aggregate' else [value[k] for k in ('proof_ref', 'receipt_ref') if k in value]
                for ref in refs:
                    self._lookup(ref)
            entry = {'entry': len(self._entries) + 1, 'kind': kind, 'value': value}
            entry['sha256'] = _sha(_json(entry))
            index = len(self._files) - 1
            group = [v for i, v in self._entries.values() if i == index] if index >= 0 else []
            encoded = _json({'version': 1, 'entries': group + [entry]})
            if not group or len(group) >= GROUP_ENTRIES or len(encoded) > self.max_file:
                index = len(self._files)
                encoded = _json({'version': 1, 'entries': [entry]})
            # Existing files plus the whole new temporary file are the peak.
            if (len(encoded) > self.max_file or len(self._files) + 1 > self.max_files or
                    sum(f['size'] for f in self._files) + len(encoded) > self.max_total):
                raise RecordError('video_record_limit')
            name, temp = f'shard-{index:03d}.json', '.pending-' + uuid.uuid4().hex
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd)
            with os.fdopen(fd, 'wb') as target:
                target.write(encoded)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            temp = None
            os.fsync(self._fd)
            info = {'name': name, 'size': len(encoded), 'sha256': _sha(encoded)}
            if index == len(self._files):
                self._files.append(info)
            else:
                self._files[index] = info
            self._entries[entry['entry']] = (index, entry)
            return {'entry': entry['entry'], 'sha256': entry['sha256']}
        except Exception as exc:
            self._failed = True
            if isinstance(exc, RecordError):
                raise
            raise RecordError('video_record_write_failed') from None
        finally:
            if temp is not None:
                try:
                    os.unlink(temp, dir_fd=self._fd)
                except OSError:
                    pass

    def _lookup(self, ref):
        if not _ref(ref) or ref['entry'] not in self._entries or self._entries[ref['entry']][1]['sha256'] != ref['sha256']:
            raise RecordError('video_record_reference_invalid')
        return copy.deepcopy(self._entries[ref['entry']][1]['value'])

    def read(self, ref):
        try:
            self._verify()
            return self._lookup(ref)
        except Exception:
            self._failed = True
            raise RecordError('video_record_read_failed') from None

    def snapshot(self):
        try:
            self._verify()
            return {'version': 1, 'files': copy.deepcopy(self._files)}
        except Exception:
            self._failed = True
            raise RecordError('video_record_read_failed') from None
