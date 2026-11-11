"""Project the private, operator-published JMeter summary into display fields."""
from datetime import datetime
import hashlib
import math
import re

from task_server.services import requirement_design as design
from task_server.services import requirement_evaluation_view as evaluation
from task_server.services.ui_execution_contract import AccessDenied

# Public summaries contain measurements and descriptions, never URLs, identifiers or logs.
_PRIVATE_TEXT = re.compile(
    r'https?[:/]|[/\\][a-z0-9_.-]+[/\\]|(?:^|\s)/[a-z]|[?&]\w+=|'
    r'\b(?:cookie|bearer|authorization|password|token|secret|api[_-]?key)\b|'
    r'\b(?:select\s+.+?\s+from|insert\s+into|delete\s+from|update\s+.+?\s+set)\b|'
    r'(?:\d{1,3}\.){3}\d{1,3}|\b[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+\b|'
    r'[a-z0-9_-]{32,}|@|[<>{}\x00-\x1f\x7f]', re.I | re.ASCII)


def _summary(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000 or _PRIVATE_TEXT.search(value):
        raise evaluation.Unavailable()
    return value.strip()


def _number(value, *, integer=False, signed=False):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 2**53-1:
        raise evaluation.Unavailable()
    if (not signed and value < 0) or (integer and type(value) is not int):
        raise evaluation.Unavailable()
    return value


def _rows(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise evaluation.Unavailable()
    return value


def _monitor(row):
    if row['source'] not in ('generator', 'server', 'database'):
        raise evaluation.Unavailable()
    result = {'source': row['source'], 'metric': _summary(row['metric']), 'unit': _summary(row['unit']),
              'samples': _number(row['samples'], integer=True)}
    for key in ('min', 'max', 'mean'):
        result[key] = _number(row[key], signed=True)
    if not result['samples'] or result['min'] > result['max']:
        raise evaluation.Unavailable()
    return result


def _stage(row):
    if row['phase'] not in ('baseline', 'retest') or not re.fullmatch('[a-f0-9]{64}', row['jtl_sha256']):
        raise evaluation.Unavailable()
    result = {'phase': row['phase'], 'jtl_sha256': row['jtl_sha256']}
    for key in ('threads', 'samples', 'http_successes', 'business_successes', 'errors'):
        result[key] = _number(row[key], integer=True)
    for key in ('ramp_s', 'warmup_s', 'steady_s', 'elapsed_s', 'tps'):
        result[key] = _number(row[key])
    samples = result['samples']
    if (not result['threads'] or not result['steady_s'] or
            not result['business_successes'] <= result['http_successes'] <= samples or
            result['errors'] != samples - result['business_successes']):
        raise evaluation.Unavailable()
    for key in ('p50_ms', 'p95_ms', 'p99_ms'):
        result[key] = None if samples == 0 and row[key] is None else _number(row[key])
    if samples and not result['p50_ms'] <= result['p95_ms'] <= result['p99_ms']:
        raise evaluation.Unavailable()
    if not samples and (result['tps'] or any(result[k] is not None for k in ('p50_ms', 'p95_ms', 'p99_ms'))):
        raise evaluation.Unavailable()
    if 'error_counts' in row:
        counts = {key: _number(row['error_counts'][key], integer=True) for key in ('transport', 'http', 'business')}
        if sum(counts.values()) != result['errors'] or counts['business'] != result['http_successes'] - result['business_successes']:
            raise evaluation.Unavailable()
        result['error_counts'] = counts
    result['monitor'] = [_monitor(item) for item in _rows(row['monitor'], 128)]
    return result


def published(allowed_apps):
    doc = evaluation._published_file('jmeter-readonly.json')
    try:
        scope = doc['scope']['app_ids']
        if not isinstance(scope, list) or not scope or any(app not in allowed_apps for app in scope):
            raise AccessDenied('report_forbidden')
        if type(doc['schema_version']) is not int or doc['schema_version'] != 1 or doc['environment'] not in ('local', 'qa', 'isolated'):
            raise evaluation.Unavailable()
        timestamp = datetime.fromisoformat(doc['created_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise evaluation.Unavailable()
        thresholds = doc['thresholds']
        if not isinstance(thresholds, dict):
            raise evaluation.Unavailable()
        limits = {key: _number(thresholds[key]) for key in ('p95_ms', 'p99_ms', 'error_pct', 'business_success_pct',
                                                            'http_success_pct', 'tps_min') if key in thresholds}
        if any(value > 100 for key, value in limits.items() if key.endswith('_pct')):
            raise evaluation.Unavailable()
        metadata = {}
        if 'evidence' in doc:
            item = doc['evidence']
            size = _number(item['bytes'], integer=True)
            if not 1 <= size <= 8 * 1024 * 1024 or not re.fullmatch('[a-f0-9]{64}', item['sha256']):
                raise evaluation.Unavailable()
            metadata['evidence'] = {'sha256': item['sha256'], 'bytes': size}
        return {**metadata, **{key: _summary(doc[key]) for key in ('scenario', 'conditions', 'stop_reason', 'retest')},
                'created_at': timestamp.isoformat(), 'environment': doc['environment'], 'thresholds': limits,
                'stages': [_stage(row) for row in _rows(doc['stages'], 100)],
                'resources': {key: _summary(doc['resources'][key]) for key in ('generator', 'server', 'database')},
                'limitations': [_summary(v) for v in _rows(doc['limitations'], 40)]}
    except (KeyError, AttributeError, ValueError, TypeError, OverflowError, RecursionError):
        raise evaluation.Unavailable() from None


def evidence(allowed_apps, digest):
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('证据编号无效')
    metadata = published(allowed_apps).get('evidence')
    if not metadata or metadata['sha256'] != digest:
        raise design.Missing()
    blob = evaluation._published_bytes('jmeter-readonly-evidence.zip', limit=metadata['bytes'])
    if len(blob) != metadata['bytes'] or hashlib.sha256(blob).hexdigest() != digest:
        raise evaluation.Unavailable()
    return 'application/zip', 'jmeter-evidence-' + digest[:12] + '.zip', blob
