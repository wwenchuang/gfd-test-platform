"""Fixed read-only Python and Node adapters for the authenticated test workbench."""
import json
import re
import sys
from pathlib import Path

from task_server import auth, identity
from task_server.services import week2_real, week5_web_service as web

# Share process group cancellation, deadlines and the observed return code with W05.
ProcessControl = web.ProcessControl
Cancelled = web.Cancelled

_FEEDBACK = Path(__file__).resolve().parents[1] / 'fixtures' / 'week11_runner' / 'feedback.py'
_FEEDBACK_BASE = 'https://print.wisebeginner3d.com/app'
_FEEDBACK_PATH = '/print3d/api/v1/feedback/category/first'
_CATALOG = {'published-catalog': 'ui_catalog_count', 'catalog-failure-drill': 'ui_catalog_failure_drill'}


def _feedback_context(actor):
    """Resolve only the existing W02 source and that same environment's active revision."""
    from task_server.api_testing import access, http
    from task_server.api_testing.config import ApiTestingSettings

    owner, username, token = actor
    session = auth.verify_session_token(token)
    profile = identity.get_access_profile(username) if session and session.get('user') == username else None
    if (not profile or session.get('user_id') != owner or profile.get('user_id') != owner
            or profile.get('status') != 'active' or profile.get('must_change_password')
            or not ApiTestingSettings.from_env().enabled):
        raise PermissionError()
    access.require_permission(username, 'api.view')
    access.require_permission(username, 'api.execute')
    factory = http._factory()
    execution = http._scope_execution(factory, week2_real.EXECUTION_ID, username)
    child = http._scope_execution_case(factory, week2_real.CASE_ID, username)
    if child.execution_id != execution.id or child.environment_revision_id != execution.environment_revision_id:
        raise PermissionError()
    endpoint = http._scope_endpoint(factory, child.endpoint_id, username)
    version = http._scope_case_version(factory, child.case_version_id, username)
    case = http._scope_case(factory, version.case_id, username)
    revision = http._scope_environment_revision(factory, execution.environment_revision_id, username)
    env = http._scope_environment(factory, revision.environment_id, username)
    if (endpoint.method != 'GET' or endpoint.path != _FEEDBACK_PATH or endpoint.revision_id != execution.source_revision_id
            or endpoint.id != version.endpoint_id or case.project_id != execution.project_id
            or env.project_id != execution.project_id or env.status != 'active' or not env.active_revision_id):
        raise PermissionError()
    with factory() as db:
        access.require_execution_environment(db, env.active_revision_id, username, project_id=execution.project_id)
    envelope = version.request_template
    request = envelope.get('request', envelope)
    if request.get('method') != 'GET' or request.get('path') != _FEEDBACK_PATH:
        raise PermissionError()
    return factory, env.active_revision_id, request


def feedback_authorized(actor):
    try:
        _feedback_context(actor)
        return True
    except Exception:
        return False


def _feedback_credentials(actor):
    from task_server.api_testing.services.environment_service import EnvironmentService

    factory, revision_id, template = _feedback_context(actor)
    service = template.get('service', 'default')
    runtime = EnvironmentService(factory).resolve_runtime(revision_id, {}, service_name=service)
    if runtime.base_url_for(service).rstrip('/') != _FEEDBACK_BASE:
        raise ValueError('wrong_environment_destination')
    auth_value = next((value for name, value in runtime.headers.items() if name.lower() == 'authorization'), None)
    # Only resolve the pinned case's authorization header. Do not execute its scripts or data rows.
    for name, value in (template.get('headers') or {}).items():
        if name.lower() == 'authorization':
            auth_value = runtime.render(value)
    if not isinstance(auth_value, str) or not auth_value.strip() or '\r' in auth_value or '\n' in auth_value:
        raise PermissionError()
    return auth_value


def _feedback(actor, control):
    try:
        credential = _feedback_credentials(actor)
    except PermissionError:
        return 'blocked', 'authentication', {}
    payload = json.dumps({'authorization':credential}).encode()
    argv = web._core_limited_args([sys.executable, '-I', str(_FEEDBACK)])
    rc, stdout = web._communicate(argv, {'PATH':'/usr/bin:/bin'}, payload, 22, control=control)
    if rc not in (0, 1, 2) or len(stdout) > 4096:
        return 'interrupted', 'worker_crash', {}
    try:
        report = json.loads(stdout)
        status, kind, evidence = report['status'], report['error_kind'], report['evidence']
        if (status, kind, rc) not in (('passed', None, 0), ('failed', 'business', 1), ('blocked', 'authentication', 2),
                                      ('blocked', 'environment', 2), ('timed_out', 'timeout', 2)):
            raise ValueError()
        if not isinstance(evidence, dict):
            raise ValueError()
        if status in ('passed', 'failed') or evidence:
            if (set(evidence) != {'http_status','actual_code','expected_code','data_is_list','request_id','checks'}
                    or type(evidence['http_status']) is not int or not 100 <= evidence['http_status'] <= 599
                    or type(evidence['actual_code']) not in (int, type(None)) or type(evidence['expected_code']) is not int
                    or evidence['expected_code'] != 0 or type(evidence['data_is_list']) is not bool
                    or not isinstance(evidence['request_id'], str) or not re.fullmatch(r'[a-f0-9-]{36}', evidence['request_id'])):
                raise ValueError()
            checks = {'http_200': evidence['http_status'] == 200, 'code_zero':evidence['actual_code'] == 0,
                      'data_list': evidence['data_is_list']}
            if checks != evidence['checks'] or (status == 'passed') != all(checks.values()):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        return 'interrupted', 'worker_crash', {}
    if not feedback_authorized(actor):
        return 'blocked', 'authentication', {}
    return status, kind, evidence


def _catalog(suite_id, actor, control):
    scenario = _CATALOG[suite_id]
    result = web.run(*actor, scenario=scenario, control=control)
    if control.stop_reason == 'resource_limit':
        return 'blocked', 'environment', {}
    if control.worker_exit_code not in (0, None):
        return 'interrupted', 'worker_crash', {}
    status, kind = result.get('status'), result.get('error_kind')
    if status == 'blocked':
        if result.get('detail') == 'browser_timeout':
            return 'timed_out', 'timeout', {}
        return 'blocked', kind if kind in ('authentication', 'environment', 'evidence') else 'environment', {}
    if (control.worker_exit_code != 0 or status not in ('passed', 'failed') or result.get('scenario') != scenario
            or result.get('version') != 1 or not re.fullmatch('[0-9a-f]{32}', str(result.get('id')))
            or result.get('artifacts') != ['screenshot', 'trace'] or result.get('deletion_pending')
            or (status == 'passed' and (kind is not None or type(result.get('total')) is not int or result['total'] < 0))
            or (status == 'failed' and kind not in ('business', 'drill_assertion'))):
        return 'blocked', 'evidence', {}
    allowed = ('id', 'artifacts', 'scenario', 'status', 'version', 'phase', 'total', 'expected_total', 'error_kind')
    return status, kind, {k:result[k] for k in allowed if k in result}


def execute(suite_id, actor, cancel_requested):
    if suite_id != 'feedback-categories' and suite_id not in _CATALOG:
        raise ValueError('unknown_suite')
    control = ProcessControl(cancel_requested)
    python = suite_id == 'feedback-categories'
    status, kind, evidence = 'blocked', 'environment', {}
    try:
        if cancel_requested():
            status, kind = 'cancelled', 'cancelled'
        elif python:
            status, kind, evidence = _feedback(actor, control)
        else:
            status, kind, evidence = _catalog(suite_id, actor, control)
    except web.week1_smoke.SmokeBusy:
        status, kind = 'blocked', 'check_busy'
    except TimeoutError:
        status, kind = 'timed_out', 'timeout'
    except Cancelled:
        pass  # _communicate records whether it actually stopped the child.
    except Exception:
        pass  # Environment, database and dependency errors may contain secrets.
    if control.stop_reason in ('cancelled', 'timeout'):
        if control.worker_exit_code is None:
            status, kind = 'interrupted', 'worker_crash'
        else:
            status = 'cancelled' if control.stop_reason == 'cancelled' else 'timed_out'
            kind = control.stop_reason
        evidence = {}
    return {'runner':'python' if python else 'node',
            'runner_version':sys.version.split()[0] if python else control.node_version,
            'status':status, 'error_kind':kind, 'evidence':evidence,
            'exit_code':{'passed':0, 'failed':1}.get(status), 'worker_exit_code':control.worker_exit_code}
