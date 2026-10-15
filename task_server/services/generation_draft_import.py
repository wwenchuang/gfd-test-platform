"""Trusted stored generation summaries adapted to the shared requirement snapshot."""
import re
from .operation_attribution import current_actor
from .ui_execution_contract import AccessDenied
from .requirement_draft_import import case_snapshot


def trusted_source(summary, user_id, app_id):
    from . import yaml_service
    job_id = summary.get('generation_job_id')
    if not isinstance(job_id, str) or not re.fullmatch(r'gen[_-][a-zA-Z0-9_-]+', job_id):
        raise ValueError('requirement_import_owner_missing')
    job = yaml_service.load_generate_job(job_id)
    if not job or not job.get('ownerUserId'): raise ValueError('requirement_import_owner_missing')
    request = job.get('request_data') or {}
    if (job.get('job_id') != job_id or job.get('case_set_id') != summary.get('case_set_id')
            or job['ownerUserId'] != user_id or summary.get('app_package') != app_id
            or (request.get('app_package') or request.get('appPackage')) != app_id):
        raise AccessDenied('requirement_import_scope')
    cases = summary.get('cases')
    if not isinstance(cases, list) or len(cases) > 100: raise ValueError('requirement_import_size')
    return {'runId': 'generation:' + job_id + ':' + summary['case_set_id'],
            'artifacts': {'generatedCases': {'cases': cases, 'manual_cases': summary.get('manual_cases', [])}}}


def source_refs(summary):
    try:
        run = trusted_source(summary, current_actor().get('user_id'), summary.get('app_package'))
    except (ValueError, PermissionError, KeyError):
        return []
    refs = []
    for index, item in enumerate(summary['cases']):
        try:
            snapshot = case_snapshot(run, item, index)
            refs.append({'case_index': index, 'case_hash': snapshot['snapshot_id']})
        except (ValueError, TypeError, KeyError):
            pass
    return refs
