#!/usr/bin/env python3
"""Read-only, whitelisted diagnostics for an earlier scheduled scope snapshot."""
import json
import os
import re
import sys
from uuid import UUID

STATUSES = frozenset(('PASSED', 'FAILED', 'BROKEN', 'CANCELLED', 'SKIPPED', 'RUNNING', 'QUEUED', 'DONE'))
CATEGORIES = frozenset(('condition_not_met', 'missing_variables', 'timeout', 'transport', 'parser',
                        'environment', 'cleanup', 'cleanup_dependency', 'assertion', 'cancelled',
                        'assertion_definition', 'host_policy', 'response_limit', 'redirect_limit'))
METHODS = frozenset(('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'))


def safe_name(value):
    if not isinstance(value, str):
        return None
    text = re.sub(r'https?://\S+|\S+@\S+', '[隐藏]', value[:200], flags=re.I)
    text = re.sub(r'(?i)(token|secret|password|apikey|api[_-]?key|密码|密钥)\s*[:=]\s*[^\s,;，；]+', r'\1=[隐藏]', text)
    text = re.sub(r'[A-Za-z0-9_-]{32,}|\d{7,}', '[隐藏]', text)
    return ''.join(c for c in text if c.isprintable())


def cleanup_structure(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get('cleanup_steps', []), list):
        return None
    result = []
    for i, step in enumerate(spec.get('cleanup_steps', [])):
        if not isinstance(step, dict) or not isinstance(step.get('enabled', True), bool):
            return None
        extract = step.get('extractions', [])
        polling = step.get('polling') or {}
        request = step.get('request') or {}
        if not isinstance(extract, list) or any(not isinstance(x, dict) for x in extract) or not isinstance(polling, dict) or not isinstance(request, dict):
            return None
        max_attempts = polling.get('max_attempts', 1)
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1 or max_attempts > 1000:
            return None
        method = request.get('method')
        method = method if method in METHODS else 'UNKNOWN'
        if any(not isinstance(item.get('required', True), bool) for item in extract):
            return None
        optional = sum(item.get('required', True) is False or item.get('default') is not None for item in extract)
        result.append({'index': i, 'enabled': step.get('enabled', True), 'method': method,
                       'conditional': bool(step.get('only_if_variable')), 'extraction_count': len(extract),
                       'optional_or_default_count': optional, 'poll_max_attempts': max_attempts})
    return {'steps': result}


def output_state(value):
    if not isinstance(value, dict):
        return 'unknown'
    if not value:
        return 'none_recorded'
    def redacted(item):
        if isinstance(item, str):
            return '***' in item or '[REDACTED]' in item.upper()
        if isinstance(item, list):
            return any(redacted(x) for x in item)
        if isinstance(item, dict):
            return any(redacted(x) for x in item.values())
        return False
    if redacted(value):
        return 'unknown_redacted'
    return 'nonempty_recorded' if any(v is not None and v != '' and v != [] and v != {} for v in value.values()) else 'empty_recorded'


def trace_summary(result):
    if not isinstance(result, dict) or not isinstance(result.get('trace'), list):
        return None
    steps = {}
    for event in result['trace']:
        if not isinstance(event, dict) or event.get('phase') != 'workflow_step' or event.get('stage') != 'cleanup':
            continue
        index = event.get('index')
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > 999:
            return None
        attempt = event.get('attempt', 1)
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1 or attempt > 1000:
            return None
        status = event.get('status') if event.get('status') in STATUSES else 'UNKNOWN'
        category = event.get('failure_category') if event.get('failure_category') in CATEGORIES else None
        steps.setdefault(index, []).append({'attempt': attempt, 'status': status, 'failure_category': category,
                                           'output_state': output_state(event.get('extracted_variables'))})
    return [{'index': index, 'attempts': attempts} for index, attempts in sorted(steps.items())]


def scheduled_hits(report):
    if not isinstance(report, dict) or report.get('scope') != 'enabled_schedules_access_unfiltered_superset' or report.get('permissions_resolved') is not False or not isinstance(report.get('schedules'), list):
        raise ValueError('invalid report')
    found = set()
    for job in report['schedules']:
        if not isinstance(job, dict) or not isinstance(job.get('versions'), list):
            raise ValueError('invalid schedule')
        job_id = str(UUID(str(job.get('job_id'))))
        for version in job['versions']:
            if not isinstance(version, dict) or not isinstance(version.get('earlier_output'), bool) or version.get('indeterminate') is not False:
                raise ValueError('incomplete version flags')
            version_id = str(UUID(str(version.get('case_version_id'))))
            if version['earlier_output']:
                found.add((job_id, version_id))
    return sorted(found)


def diagnose(connection, report):
    hits = scheduled_hits(report)
    data = []
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '5s'")
        for job_id, version_id in hits:
            version = connection.execute('SELECT c.name, v.processing_spec FROM api_case_versions v JOIN api_cases c ON c.id=v.case_id WHERE v.id=%s', (version_id,)).fetchone()
            if version is None:
                raise ValueError('version missing')
            latest = connection.execute(
                'SELECT ec.execution_id, ec.id, ec.status, ex.state, ex.created_at, ec.sanitized_result '
                'FROM api_scheduled_job_runs r JOIN api_executions ex ON ex.id=r.execution_id '
                'JOIN api_execution_cases ec ON ec.execution_id=ex.id '
                'WHERE r.job_id=%s AND ec.case_version_id=%s '
                'ORDER BY ex.created_at DESC, ec.created_at DESC, ec.id DESC LIMIT 1', (job_id, version_id)).fetchone()
            history = None
            if latest:
                execution_id, case_id, case_status, execution_state, created, result = latest
                history = {'execution_id': str(UUID(str(execution_id))), 'execution_case_id': str(UUID(str(case_id))),
                           'case_status': case_status if case_status in STATUSES else 'UNKNOWN',
                           'execution_state': execution_state if execution_state in STATUSES else 'UNKNOWN',
                           'created_at': created.isoformat() if created else None, 'cleanup_trace': trace_summary(result)}
            data.append({'job_id': job_id, 'case_version_id': version_id, 'current_case_name': safe_name(version[0]),
                         'cleanup_structure': cleanup_structure(version[1]), 'latest_scheduled_history': history})
    return {'scope': 'snapshot_hits_only', 'membership_revalidated': False, 'history_source': 'same_schedule_and_exact_version',
            'count': len(data), 'items': data}


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError('report path required')
        with open(sys.argv[1], encoding='utf-8') as f:
            report = json.load(f)
        import psycopg
        dsn = os.environ['API_TESTING_DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            result = diagnose(connection, report)
    except Exception:
        print('诊断未完成：请检查输入、数据库连接、权限、结构与超时。', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
