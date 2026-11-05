"""Validate matching platform execution/case and the known read-only endpoint."""
import json
import uuid
from urllib.parse import urlsplit


class EvidenceError(ValueError):
    def __init__(self, reason, source_failed=False):
        super().__init__(reason)
        self.source_failed = source_failed


def _identifier(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise EvidenceError('invalid_evidence_id') from None


def parse_evidence(payload, expected_execution_id=None, expected_case_id=None):
    if not isinstance(payload, dict): raise EvidenceError('invalid_evidence')
    execution, case = payload.get('execution'), payload.get('case_result')
    if not isinstance(execution, dict) or not isinstance(case, dict): raise EvidenceError('missing_execution_or_case')
    execution_id, case_id = _identifier(execution.get('id')), _identifier(case.get('execution_case_id'))
    if expected_execution_id and execution_id != _identifier(expected_execution_id): raise EvidenceError('execution_mismatch')
    if expected_case_id and case_id != _identifier(expected_case_id): raise EvidenceError('case_mismatch')
    entries = execution.get('case_results')
    if not isinstance(entries, list) or sum(isinstance(item, dict) and item.get('execution_case_id') == case.get('execution_case_id') for item in entries) != 1:
        raise EvidenceError('case_not_in_execution')
    if execution.get('state') not in ('DONE', 'PASSED', 'FAILED'):
        raise EvidenceError('execution_not_terminal')
    result = case.get('sanitized_result')
    if not isinstance(result, dict) or result.get('status') not in ('PASSED', 'FAILED') or result.get('status') != case.get('status'):
        raise EvidenceError('case_not_terminal')
    source_failed = result['status'] == 'FAILED'
    source = result.get('request')
    if not isinstance(source, dict) or source.get('method') != 'GET' or not isinstance(source.get('url'), str):
        raise EvidenceError('request_not_feedback_get')
    path = urlsplit(source['url']).path
    if path not in ('/app/print3d/api/v1/feedback/category/first', '/print3d/api/v1/feedback/category/first'):
        raise EvidenceError('request_not_feedback_get')
    response = result.get('response')
    if not isinstance(response, dict) or type(response.get('status_code')) is not int:
        raise EvidenceError('missing_response', source_failed)
    body = response.get('body')
    if isinstance(body, str):
        try: body = json.loads(body)
        except ValueError: raise EvidenceError('invalid_response_json', source_failed) from None
    if not isinstance(body, dict): raise EvidenceError('invalid_response_json', source_failed)
    assertions = result.get('assertions')
    if not isinstance(assertions, list) or not assertions or any(not isinstance(item, dict) or type(item.get('passed')) is not bool for item in assertions):
        raise EvidenceError('missing_assertions', source_failed)
    return {'execution_id': execution_id, 'case_id': case_id, 'http_status': response['status_code'],
            'body': body, 'assertions': assertions, 'source_status': result['status']}
