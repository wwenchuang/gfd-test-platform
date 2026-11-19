"""Run-bound, currently authorized facts for the read-only failure investigator.

No runner, network, report-generation or historical AI-analysis calls belong here.
"""
import copy
import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from task_server.api_testing import access
from task_server.api_testing.executor import discover_sensitive_values, redact
from task_server.api_testing.models.case import ApiCase, ApiCaseVersion
from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
from .operation_attribution import current_actor


CASE_LIMIT = 25
SECRET_CONTEXT_BYTES = 8 * 1024 * 1024
SECRET_CONTEXT_ROWS = 500
_FAILED_API = ('FAILED', 'BROKEN')
_FAILED_UI = ('failed', 'blocked', 'interrupted', 'timeout')
_INLINE_SECRET = re.compile(
    r'''(?ix)(["']?(?:authorization|cookie|password|passwd|secret|token|api[-_]?key|access[-_]?token)["']?\s*[:=]\s*)
    (?:"[^"\n]*"|'[^'\n]*'|[^\s&;,}\]]+)''')
_HEADER_LINE = re.compile(r'(?im)\b((?:proxy-)?authorization|(?:set-)?cookie)\s*[:=][^\r\n]+')


def _identifier(value, source):
    if not isinstance(value, str):
        raise ValueError('identifier_invalid')
    if source == 'api':
        try:
            valid = str(uuid.UUID(value)) == value
        except ValueError:
            valid = False
    else:
        valid = bool(re.fullmatch(r'uib_[0-9a-f]{32}', value))
    if not valid:
        raise ValueError('identifier_invalid')
    return value


def _bounded(value, depth=0):
    if depth > 12:
        return '[内容过深，已省略]'
    if isinstance(value, Mapping):
        return {str(k)[:120]: _bounded(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple)):
        return [_bounded(v, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        return _INLINE_SECRET.sub(r'\1***', _HEADER_LINE.sub(r'\1: ***', value))[:4000]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:4000]


def _clean(value, secrets=()):
    sanitized = _bounded(redact(value, tuple(secrets) + discover_sensitive_values(value)))
    encoded = json.dumps(sanitized, ensure_ascii=False)
    if len(encoded.encode('utf-8')) > 16000:
        return {'truncated': True, 'preview': encoded[:5000]}
    return sanitized


def _ui_secrets(batch):
    """Input values are unnecessary for diagnosis, including their repeats in logs."""
    import yaml
    try:
        if len(json.dumps(batch, ensure_ascii=False).encode('utf-8')) > SECRET_CONTEXT_BYTES:
            return None
    except (TypeError, ValueError, RecursionError):
        return None
    values = list(discover_sensitive_values(batch))
    if len(values) >= 1000:
        return None
    for snapshot in batch.get('snapshots') or []:
        source = snapshot.get('source_yaml') or ''
        if not isinstance(source, str) or len(source) > 1_000_000:
            return None
        try:
            document = yaml.safe_load(source) or {}
            for task in document.get('tasks') or []:
                for flow in task.get('flow') or []:
                    item = flow.get('aiInput') if isinstance(flow, dict) else None
                    value = item.get('value') if isinstance(item, dict) else item
                    if isinstance(value, str) and value:
                        values.append(value)
                        if len(values) >= 1000:
                            return None
        except (yaml.YAMLError, AttributeError, TypeError, RecursionError):
            return None
    return values


class EvidenceSource:
    def __init__(self, actor, source, run_id, *, session_factory=None):
        if source not in ('api', 'ui'):
            raise ValueError('source_invalid')
        if not isinstance(actor, Mapping):
            raise PermissionError('actor_untrusted')
        self.actor = copy.deepcopy(dict(actor))
        self.source = source
        self.run_id = _identifier(run_id, source)
        self.session_factory = session_factory

    def _factory(self):
        if self.session_factory is None:
            from task_server.api_testing.db import _session_factory
            self.session_factory = _session_factory()
        return self.session_factory

    def _username(self):
        trusted = current_actor()
        fields = ('kind', 'username', 'user_id', 'display_name')
        if (not isinstance(self.actor, Mapping) or trusted.get('kind') != 'user'
                or not trusted.get('username') or not trusted.get('user_id')
                or any(self.actor.get(key) != trusted.get(key) for key in fields)):
            raise PermissionError('actor_untrusted')
        profile = access.get_access_profile(trusted['username'])
        if (not profile or profile.get('user_id') != trusted['user_id']
                or profile.get('status') != 'active' or profile.get('must_change_password')):
            raise PermissionError('account_unavailable')
        access.require_permission(trusted['username'], 'api.view')
        return trusted['username']

    def _run_query(self, run_id, actor):
        return select(ApiExecution.id, ApiExecution.project_id, ApiExecution.source_revision_id,
                      ApiExecution.environment_revision_id, ApiExecution.state, ApiExecution.created_at,
                      ApiExecution.started_at, ApiExecution.finished_at).where(
            ApiExecution.id == run_id, access.resource_predicate(actor, ApiExecution))

    def authorize(self):
        if self.source == 'ui':
            from .ui_execution_service import get_ui_batch
            batch = get_ui_batch(self.actor, self.run_id)
            if not batch:
                raise LookupError('run_not_found')
            return batch
        actor = self._username()
        with self._factory()() as db:
            row = db.execute(self._run_query(self.run_id, actor)).mappings().one_or_none()
            if row is None:
                raise LookupError('run_not_found')
            return dict(row)

    def _case_query(self, actor, *, full=False):
        columns = [ApiExecutionCase.id.label('case_id'), ApiExecutionCase.execution_id,
                   ApiExecutionCase.case_version_id, ApiExecutionCase.endpoint_id,
                   ApiExecutionCase.environment_revision_id, ApiExecutionCase.status,
                   ApiExecutionCase.failure_category, ApiExecutionCase.duration_ms,
                   ApiCase.name]
        if full:
            columns.append(ApiExecutionCase.sanitized_result)
        else:
            columns.extend([ApiExecutionCase.sanitized_result['assertions'].label('assertions'),
                            ApiExecutionCase.sanitized_result['error_message'].label('error_message')])
        return (select(*columns).join(ApiExecution, ApiExecution.id == ApiExecutionCase.execution_id)
                .join(ApiCaseVersion, ApiCaseVersion.id == ApiExecutionCase.case_version_id)
                .join(ApiCase, ApiCase.id == ApiCaseVersion.case_id)
                .where(ApiExecutionCase.environment_revision_id == ApiExecution.environment_revision_id,
                       ApiCaseVersion.endpoint_id == ApiExecutionCase.endpoint_id,
                       ApiCase.project_id == ApiExecution.project_id,
                       access.resource_predicate(actor, ApiExecution),
                       access.resource_predicate(actor, ApiExecutionCase)))

    def _api_case(self, case_id, *, full=False):
        _identifier(case_id, 'api')
        actor = self._username()
        with self._factory()() as db:
            row = db.execute(self._case_query(actor, full=full).where(
                ApiExecutionCase.id == case_id, ApiExecutionCase.execution_id == self.run_id)).mappings().one_or_none()
        if row is None:
            raise LookupError('case_not_found')
        return dict(row)

    def _api_secrets(self, run_id):
        """Discover before projection; incomplete context never permits free text.

        A setup case can issue a secret later repeated in another case's error.
        This collection is request-local and never persisted or sent to the model.
        """
        actor = self._username()
        query = (select(ApiExecutionCase.sanitized_result, ApiExecutionCase.environment_revision_id,
                        ApiExecution.environment_revision_id.label('run_environment'))
                 .join(ApiExecution, ApiExecution.id == ApiExecutionCase.execution_id)
                 .where(ApiExecution.id == run_id, access.resource_predicate(actor, ApiExecution),
                        access.resource_predicate(actor, ApiExecutionCase))
                 .order_by(ApiExecutionCase.ordinal).limit(SECRET_CONTEXT_ROWS + 1)
                 .execution_options(yield_per=20))
        values, size = set(), 0
        with self._factory()() as db:
            for index, row in enumerate(db.execute(query).mappings()):
                if index == SECRET_CONTEXT_ROWS or row['environment_revision_id'] != row['run_environment']:
                    return None
                raw = row['sanitized_result'] or {}
                try:
                    size += len(json.dumps(raw, ensure_ascii=False).encode('utf-8'))
                except (TypeError, ValueError, RecursionError):
                    return None
                if size > SECRET_CONTEXT_BYTES:
                    return None
                # discover_sensitive_values deliberately stops at depth 32 and
                # 1000 values. Reject incomplete traversal instead of treating
                # that bound as proof that remaining free text contains no secret.
                stack, nodes = [(raw, 0)], 0
                while stack:
                    item, depth = stack.pop()
                    nodes += 1
                    if depth > 24 or nodes > 100000:
                        return None
                    if isinstance(item, dict):
                        stack.extend((value, depth + 1) for value in item.values())
                    elif isinstance(item, list):
                        stack.extend((value, depth + 1) for value in item)
                discovered = discover_sensitive_values(raw)
                if len(discovered) >= 1000:
                    return None
                values.update(discovered)
                if len(values) >= 1000:
                    return None
        return tuple(values)

    def _ui_case(self, batch, case_id):
        if not isinstance(case_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', case_id):
            raise ValueError('case_id_invalid')
        attempt = next((row for row in batch.get('case_attempts') or [] if row['attempt_id'] == case_id), None)
        if attempt is None:
            raise LookupError('case_not_found')
        return attempt

    def _href(self, run_id=None):
        run_id = run_id or self.run_id
        return ('/api-test/#/runs?executionId=' if self.source == 'api'
                else '/task-manager.html?ui_batch=') + run_id

    def _evidence(self, kind, case_id, title, data, *, run_id=None, secrets=()):
        run_id = run_id or self.run_id
        return {'id': f'{self.source}:{run_id}:{case_id}:{kind}', 'kind': kind,
                'title': str(_clean(title, secrets))[:120], 'href': self._href(run_id), 'data': _clean(data, secrets)}

    def _finish(self, result):
        # A revoked scope during a slow evidence read must not leak its result.
        self.authorize()
        while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 32000 and result['evidence']:
            result['evidence'].pop()
            if 'evidence_output_truncated' not in result['missing']:
                result['missing'].append('evidence_output_truncated')
        return result

    def get_device_logs(self, case_id):
        """Read a sanitized, execution-correlated projection in the platform database."""
        run = self.authorize()
        if self.source != 'api':
            return self._finish({'evidence': [], 'missing': ['当前 UI 记录尚未关联打印任务。']})
        self._api_case(case_id)
        def missing(reason):
            return self._finish({'evidence': [], 'missing': [reason]})

        if os.environ.get('MIDSCENE_DEVICE_LOG_VIEW_ENABLED') != '1':
            return missing('尚未接入设备日志只读视图。')
        secrets = self._api_secrets(self.run_id)
        if secrets is None:
            return missing('缺少完整的脱敏上下文。')
        query = text("""SELECT event_id, event_time, stage, error_code
                        FROM agent_device_log_events
                        WHERE project_id = :project AND environment_revision_id = :environment
                          AND execution_id = :run AND execution_case_id = :case_id
                        ORDER BY event_time, event_id LIMIT 26""")
        try:
            with self._factory()() as db:
                rows = db.execute(query, dict(project=run['project_id'],
                    environment=run['environment_revision_id'], run=self.run_id,
                    case_id=case_id)).mappings().all()
        except SQLAlchemyError:
            return missing('设备日志视图读取失败，请核对连接、视图和只读权限。')
        if not rows:
            return missing('未找到与本次用例关联的设备日志，无法确认设备是否收到任务。')
        records = [dict(row) for row in rows[:25]]
        return self._finish({'evidence': [self._evidence('device_logs', case_id,
            '打印任务设备日志', records, secrets=secrets)],
            'missing': ['仅显示最早的25条设备事件，后续状态待核对。'] if len(rows) > 25 else []})

    def get_failed_steps(self):
        run = self.authorize()
        result = {'evidence': [], 'missing': [], 'cases': []}
        if self.source == 'api':
            secrets = self._api_secrets(self.run_id)
            with self._factory()() as db:
                rows = db.execute(self._case_query(self._username()).where(
                    ApiExecutionCase.execution_id == self.run_id,
                    ApiExecutionCase.status.in_(_FAILED_API)).order_by(ApiExecutionCase.ordinal).limit(CASE_LIMIT + 1)).mappings().all()
            for row in rows[:CASE_LIMIT]:
                if secrets is None:
                    # Only bounded server categories and counts may cross this boundary.
                    failures = [a for a in (row['assertions'] or []) if isinstance(a, dict) and a.get('passed') is False]
                    kinds = sorted({a['type'] for a in failures if isinstance(a.get('type'), str)
                                    and a['type'] in {'json_path', 'business_code', 'status_code', 'json_schema'}})
                    data = {'status': row['status'], 'failed_assertion_count': len(failures), 'assertion_types': kinds}
                    result['evidence'].append(self._evidence('failed_steps', row['case_id'], '用例失败断言统计', data))
                    result['cases'].append({'case_id': row['case_id'], 'name': 'API 用例', 'status': row['status']})
                    continue
                assertions = row['assertions'] if isinstance(row['assertions'], list) else []
                failed = [item for item in assertions if isinstance(item, dict) and item.get('passed') is False]
                data = {key: row[key] for key in ('case_id', 'status', 'failure_category', 'duration_ms', 'error_message')}
                data['failed_assertions'] = failed
                result['evidence'].append(self._evidence('failed_steps', row['case_id'], row['name'] or 'API 用例', data, secrets=secrets))
                result['cases'].append({'case_id': row['case_id'], 'name': str(_clean(row['name'], secrets))[:120], 'status': row['status']})
            if secrets is None:
                result['missing'].append('secret_context_incomplete')
        else:
            rows = [row for row in run.get('case_attempts') or [] if row.get('status') in _FAILED_UI]
            names = {row['case_id']: (row.get('metadata') or {}).get('task_name') for row in run.get('snapshots') or []}
            secrets = _ui_secrets(run)
            for row in rows[:CASE_LIMIT]:
                case_id, name = row['attempt_id'], names.get(row['case_id']) or 'UI 用例'
                if secrets is None:
                    result['cases'].append({'case_id': case_id, 'name': 'UI 用例', 'status': row['status']})
                    continue
                data = {key: row.get(key) for key in ('status', 'block_code', 'block_message', 'step_progress_known', 'step_progress_truncated')}
                data['failed_steps'] = [step for step in row.get('steps') or [] if step.get('status') == 'failed'] if row.get('step_progress_known') else []
                if not row.get('step_progress_known'):
                    result['missing'].append('step_progress_not_recorded:' + case_id)
                result['evidence'].append(self._evidence('failed_steps', case_id, name, data, secrets=secrets))
                result['cases'].append({'case_id': case_id, 'name': str(_clean(name, secrets))[:120], 'status': row['status']})
            if secrets is None:
                result['missing'].append('secret_context_incomplete')
        if len(rows) > CASE_LIMIT:
            result['missing'].append('failed_cases_truncated_at_25')
        if not rows:
            result['missing'].append('failed_case_not_recorded')
        return self._finish(result)

    @staticmethod
    def _trace(row):
        raw = row.get('sanitized_result') or {}
        if not isinstance(raw, dict):
            raw = {}
        # Previous AI conclusions, templates and arbitrary sibling fields are excluded.
        return {key: raw[key] for key in ('request', 'response', 'assertions', 'trace', 'error_message', 'timing') if key in raw}

    def get_request_trace(self, case_id):
        run = self.authorize()
        result = {'evidence': [], 'missing': []}
        if self.source == 'api':
            row = self._api_case(case_id, full=True)
            secrets = self._api_secrets(self.run_id)
            if secrets is None:
                result['missing'].append('secret_context_incomplete')
                return self._finish(result)
            trace = self._trace(row)
            for key in ('request', 'response', 'trace'):
                if not trace.get(key):
                    result['missing'].append(key + '_not_recorded')
            if trace:
                result['evidence'].append(self._evidence('request_trace', case_id, row['name'] or 'API 请求证据', trace, secrets=secrets))
        else:
            row = self._ui_case(run, case_id)
            raw = row.get('result') or {}
            logs = {key: raw[key] for key in ('stdout', 'stderr', 'report_missing_reason', 'report_upload_error') if raw.get(key)}
            result['missing'].append('network_trace_not_collected')
            secrets = _ui_secrets(run)
            if secrets is None:
                result['missing'].append('secret_context_incomplete')
                return self._finish(result)
            if logs:
                result['evidence'].append(self._evidence('runner_logs', case_id, '本次 UI 执行日志', logs, secrets=secrets))
            else:
                result['missing'].append('runner_logs_not_recorded')
        return self._finish(result)

    def compare_last_pass(self, case_id):
        run = self.authorize()
        result = {'evidence': [], 'missing': [], 'comparable': False}
        if self.source == 'ui':
            self._ui_case(run, case_id)
            result['missing'].append('environment_identity_unavailable')
            return self._finish(result)
        current = self._api_case(case_id, full=True)
        secrets = self._api_secrets(self.run_id)
        if secrets is None:
            result['missing'].append('secret_context_incomplete')
            return self._finish(result)
        if not run.get('created_at'):
            result['missing'].append('run_timestamp_not_recorded')
            return self._finish(result)
        actor = self._username()
        with self._factory()() as db:
            previous = db.execute(self._case_query(actor, full=True).where(
                ApiExecution.project_id == run['project_id'],
                ApiExecution.source_revision_id == run['source_revision_id'],
                ApiExecution.environment_revision_id == run['environment_revision_id'],
                ApiExecution.created_at < run['created_at'], ApiExecution.state == 'DONE',
                ApiExecution.finished_at < run['created_at'],
                ApiExecutionCase.status == 'PASSED',
                ApiExecutionCase.case_version_id == current['case_version_id'],
                ApiExecutionCase.endpoint_id == current['endpoint_id'])
                .order_by(ApiExecution.finished_at.desc(), ApiExecution.created_at.desc(),
                          ApiExecution.id.desc(), ApiExecutionCase.ordinal).limit(1)).mappings().one_or_none()
            if previous is not None:
                previous = dict(previous)
                # Reauthorize the compared run as well as the originally bound run.
                if db.execute(self._run_query(previous['execution_id'], self._username())).first() is None:
                    raise LookupError('run_not_found')
        if previous is None:
            result['missing'].append('prior_comparable_pass_not_found')
        else:
            previous_secrets = self._api_secrets(previous['execution_id'])
            if previous_secrets is None:
                result['missing'].append('secret_context_incomplete')
                return self._finish(result)
            secrets += previous_secrets
            result.update(comparable=True, previous_run_id=previous['execution_id'])
            for row, label in ((current, '本次执行'), (previous, '同用例版本和环境的上次成功')):
                data = {'case_id': row['case_id'], 'status': row['status'], 'duration_ms': row['duration_ms'],
                        'case_version_id': row['case_version_id'], 'environment_revision_id': row['environment_revision_id'],
                        'recorded': self._trace(row)}
                result['evidence'].append(self._evidence('comparison', row['case_id'], label, data, run_id=row['execution_id'], secrets=secrets))
        return self._finish(result)
