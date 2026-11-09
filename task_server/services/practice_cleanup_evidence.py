"""Authorized, read-only projection of a private cleanup diagnostic snapshot."""

import copy
import json
import os
import stat
import sys
from datetime import datetime
from uuid import UUID

from task_server import identity
from task_server.services import week2_real


_MAX_BYTES = 1024 * 1024


def _same_environment(username, execution_id, case_id):
    from sqlalchemy import select
    from task_server.api_testing import access, http
    from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
    factory = http._factory()
    with factory() as session:
        return session.scalar(select(ApiExecutionCase.id).join(
            ApiExecution, ApiExecution.id == ApiExecutionCase.execution_id
        ).where(
            ApiExecution.id == execution_id, ApiExecutionCase.id == case_id,
            ApiExecutionCase.environment_revision_id == ApiExecution.environment_revision_id,
            access.resource_predicate(username, ApiExecution),
            access.resource_predicate(username, ApiExecutionCase),
        )) is not None


def _verify_sources(owner, username, sources):
    verified = week2_real._verify_sources(owner, username, sources)
    return [allowed and _same_environment(username, execution_id, case_id)
            for allowed, (execution_id, case_id) in zip(verified, sources)]


def _invoke_authorization(owner, username, sources, timeout, slot_fd):
    body = json.dumps({'owner': owner, 'username': username, 'sources': sources})
    raw = week2_real._run_isolated([sys.executable, '-m', 'task_server.services.practice_cleanup_evidence'],
                                   body, timeout, slot_fd=slot_fd)
    result = json.loads(raw)
    if not isinstance(result, list) or len(result) != len(sources) or any(type(item) is not bool for item in result):
        raise ValueError('invalid authorization result')
    return result


def _private_report():
    path = os.environ.get('TASK_PRACTICE_CLEANUP_EVIDENCE', '')
    if not os.path.isabs(path):
        raise ValueError('missing private snapshot')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        # Group read is allowed for a service group; group write and any world
        # access are not.  Reject symlinks, special files and unbounded input.
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027 or info.st_size > _MAX_BYTES:
            raise ValueError('unsafe snapshot')
        with os.fdopen(fd, 'rb') as handle:
            fd = None
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError('large snapshot')
        return json.loads(raw.decode('utf-8'))
    finally:
        if fd is not None:
            os.close(fd)


def _project(report):
    if not isinstance(report, dict) or report.get('scope') != 'snapshot_hits_only' or report.get('membership_revalidated') is not False or report.get('history_source') != 'same_schedule_and_exact_version':
        raise ValueError('invalid snapshot')
    items = report.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 200 or type(report.get('count')) is not int or report['count'] != len(items):
        raise ValueError('incomplete snapshot')
    execution_id = observed_at = None
    cases = []
    extraction_steps = 0
    for item in items:
        history = item['latest_scheduled_history']
        structure = item['cleanup_structure']['steps']
        trace = history['cleanup_trace']
        current_id = str(UUID(history['execution_id']))
        case_id = str(UUID(history['execution_case_id']))
        created = history['created_at']
        parsed = datetime.fromisoformat(created.replace('Z', '+00:00'))
        if parsed.tzinfo is None or history['case_status'] != 'PASSED' or case_id in cases:
            raise ValueError('invalid history')
        if execution_id is not None and (current_id != execution_id or created != observed_at):
            raise ValueError('multiple executions')
        execution_id, observed_at = current_id, created
        cases.append(case_id)
        if not isinstance(trace, list) or not trace or not isinstance(structure, list):
            raise ValueError('missing cleanup trace')
        by_index = {}
        for step in trace:
            index, attempts = step['index'], step['attempts']
            if type(index) is not int or index in by_index or not isinstance(attempts, list) or not attempts:
                raise ValueError('invalid trace')
            if any(attempt.get('status') != 'PASSED' for attempt in attempts):
                raise ValueError('nonpassing cleanup')
            by_index[index] = attempts
        for step in structure:
            if type(step.get('enabled')) is not bool or type(step.get('extraction_count')) is not int or step['extraction_count'] < 0:
                raise ValueError('invalid cleanup structure')
            if step['enabled'] and step['extraction_count'] > 0:
                extraction_steps += 1
                if not any(attempt.get('output_state') == 'nonempty_recorded' for attempt in by_index.get(step['index'], [])):
                    raise ValueError('unverified extraction')
    if not extraction_steps:
        raise ValueError('no extraction evidence')
    summary = (f'该次定时执行中，已核查的 {len(cases)} 项用例及这些用例中所有已记录的清理步骤均通过；'
               '每个已启用且含提取的步骤均记录至少一个非空值。该正常路径不证明数据归属、资源物理清理、'
               '异常恢复或清理链候选已上线；非空值也不证明所有变量有效。')
    return execution_id, observed_at, cases, summary


def get_evidence(owner, username):
    """Never return identifiers or counts unless current source scope permits all cases."""
    try:
        if not week2_real.authorized_actor(owner, username):
            return None
        profile = copy.deepcopy(identity.get_access_profile(username))
        execution_id, observed_at, cases, summary = _project(_private_report())
        sources = [(execution_id, case_id) for case_id in cases]
        with week2_real._history_slot(None) as slot:
            verified = _invoke_authorization(
                owner, username, sources, week2_real._HISTORY_TIMEOUT_SECONDS, slot_fd=slot)
        if len(verified) != len(sources) or any(value is not True for value in verified):
            return None
        if not week2_real.authorized_actor(owner, username) or profile != identity.get_access_profile(username):
            return None
        return {'execution_id': execution_id, 'observed_at': observed_at, 'summary': summary}
    except Exception:
        # A missing source, stale/deleted execution, scope failure, DB outage,
        # or malformed file all fail closed without leaking private details.
        return None


if __name__ == '__main__':
    try:
        body = json.load(sys.stdin)
        result = _verify_sources(body['owner'], body['username'], body['sources'])
        print(json.dumps(result))
    except Exception:
        print('[]')
