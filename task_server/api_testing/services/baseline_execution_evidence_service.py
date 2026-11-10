"""Bounded, read-only execution provenance for an authorized API baseline."""

import copy
from collections.abc import Mapping

from sqlalchemy import select

from .. import access
from ..models.case import ApiBaseline, ApiCase, ApiCaseVersion
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.execution import ApiExecution, ApiExecutionCase
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from .case_service import CaseNotFoundError


SCAN_LIMIT = 100
RESULT_LIMIT = 3
MAX_ASSERTIONS = 100
_CASE_STATES = frozenset({'QUEUED', 'RUNNING', 'PASSED', 'FAILED', 'BROKEN', 'CANCELLED', 'SKIPPED'})
_EXECUTION_STATES = frozenset({'QUEUED', 'RUNNING', 'DONE', 'CANCELLED'})


def _summary(status, execution_state, failure_category, response_code, assertions):
    # Failed or cancelled setup can be copied into the top-level response and
    # assertions. Cancellation cannot prove which stage was interrupted.
    main_could_have_run = failure_category not in {'setup', 'cancelled'} and status != 'CANCELLED'
    response_recorded = main_could_have_run and type(response_code) is int and 100 <= response_code <= 599
    valid_assertions = main_could_have_run and isinstance(assertions, list) and 0 < len(assertions) <= MAX_ASSERTIONS and all(
        isinstance(item, Mapping) and isinstance(item.get('type'), str)
        and type(item.get('passed')) is bool for item in assertions
    )
    inconsistent = bool(status == 'PASSED' and valid_assertions and any(not item['passed'] for item in assertions))
    return {
        'status': status if status in _CASE_STATES else 'UNKNOWN',
        'execution_state': execution_state if execution_state in _EXECUTION_STATES else 'UNKNOWN',
        'response_recorded': response_recorded,
        'assertions_recorded': len(assertions) if valid_assertions else 0,
        'assertions_passed': sum(item['passed'] for item in assertions) if valid_assertions else 0,
        'inconsistent': inconsistent,
        # This is evidence availability, not a judgement of business coverage.
        'evidence_state': 'recorded' if response_recorded and valid_assertions and not inconsistent else 'insufficient',
    }


class BaselineExecutionEvidenceService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get(self, baseline_id, actor):
        access.require_permission(actor, 'api.view')
        profile = copy.deepcopy(access.get_access_profile(actor))
        with self.session_factory() as session:
            baseline_query = (
                select(ApiBaseline.id, ApiBaseline.project_id, ApiBaseline.case_version_id,
                       ApiBaseline.environment_revision_id, ApiSourceEndpoint.id,
                       ApiSourceEndpoint.revision_id)
                .join(ApiCaseVersion, ApiCaseVersion.id == ApiBaseline.case_version_id)
                .join(ApiCase, ApiCase.id == ApiCaseVersion.case_id)
                .join(ApiSourceEndpoint, ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id)
                .join(ApiSourceRevision, ApiSourceRevision.id == ApiSourceEndpoint.revision_id)
                .join(ApiSource, ApiSource.id == ApiSourceRevision.source_id)
                .join(ApiEnvironmentRevision, ApiEnvironmentRevision.id == ApiBaseline.environment_revision_id)
                .join(ApiEnvironment, ApiEnvironment.id == ApiEnvironmentRevision.environment_id)
                .where(
                    ApiBaseline.id == baseline_id, ApiBaseline.status == 'active',
                    ApiBaseline.case_id == ApiCase.id,
                    ApiCase.project_id == ApiBaseline.project_id,
                    ApiSource.project_id == ApiBaseline.project_id,
                    ApiEnvironment.project_id == ApiBaseline.project_id,
                    access.resource_predicate(actor, ApiBaseline),
                    access.resource_predicate(actor, ApiCaseVersion),
                    access.resource_predicate(actor, ApiSourceEndpoint),
                    access.resource_predicate(actor, ApiSourceRevision),
                    access.resource_predicate(actor, ApiEnvironmentRevision),
                )
            )
            baseline_row = session.execute(baseline_query).one_or_none()
            if baseline_row is None:
                raise CaseNotFoundError('API baseline was not found')
            _, project_id, version_id, environment_revision_id, endpoint_id, source_revision_id = baseline_row
            rows = session.execute(
                select(ApiExecution.id, ApiExecution.created_at, ApiExecution.state,
                       ApiExecutionCase.id, ApiExecutionCase.status)
                .join(ApiExecutionCase, ApiExecutionCase.execution_id == ApiExecution.id)
                .where(
                    ApiExecution.project_id == project_id,
                    ApiExecution.source_revision_id == source_revision_id,
                    ApiExecution.environment_revision_id == environment_revision_id,
                    ApiExecution.state != 'ARCHIVED',
                    ApiExecution.requested_case_ids.contains([version_id]),
                    ApiExecutionCase.case_version_id == version_id,
                    ApiExecutionCase.endpoint_id == endpoint_id,
                    ApiExecutionCase.environment_revision_id == ApiExecution.environment_revision_id,
                    access.resource_predicate(actor, ApiExecution),
                    access.resource_predicate(actor, ApiExecutionCase),
                )
                .order_by(ApiExecution.created_at.desc(), ApiExecution.id.desc(), ApiExecutionCase.ordinal.asc())
                .limit(SCAN_LIMIT)
            ).all()
            seen, selected = set(), []
            for execution_id, created_at, execution_state, case_id, status in rows:
                if execution_id in seen:
                    continue
                seen.add(execution_id)
                selected.append((execution_id, created_at, execution_state, case_id, status))
                if len(selected) == RESULT_LIMIT:
                    break
            # Avoid loading all 100 saved response/analysis payloads just to
            # display three summaries. The request and body are never selected.
            evidence = {
                case_id: (state, status, category, code, assertions)
                for case_id, state, status, category, code, assertions in session.execute(
                    select(ApiExecutionCase.id, ApiExecution.state, ApiExecutionCase.status, ApiExecutionCase.failure_category,
                           ApiExecutionCase.sanitized_result['response']['status_code'],
                           ApiExecutionCase.sanitized_result['assertions'])
                    .join(ApiExecution, ApiExecutionCase.execution_id == ApiExecution.id)
                    .where(
                        ApiExecutionCase.id.in_([item[3] for item in selected]),
                        ApiExecution.project_id == project_id,
                        ApiExecution.source_revision_id == source_revision_id,
                        ApiExecution.environment_revision_id == environment_revision_id,
                        ApiExecution.state != 'ARCHIVED',
                        ApiExecution.requested_case_ids.contains([version_id]),
                        ApiExecutionCase.case_version_id == version_id,
                        ApiExecutionCase.endpoint_id == endpoint_id,
                        ApiExecutionCase.environment_revision_id == ApiExecution.environment_revision_id,
                        access.resource_predicate(actor, ApiExecution),
                        access.resource_predicate(actor, ApiExecutionCase),
                    )
                ).all()
            }
            items = []
            for execution_id, created_at, _execution_state, case_id, _status in selected:
                # A removed, archived or revoked row is not returned with stale data.
                if case_id not in evidence:
                    continue
                execution_state, status, failure_category, response_code, assertions = evidence[case_id]
                items.append({
                    'execution_id': execution_id, 'execution_case_id': case_id,
                    'created_at': created_at.isoformat() if created_at else None,
                    **_summary(status, execution_state, failure_category, response_code, assertions),
                })
            access.require_permission(actor, 'api.view')
            if profile != access.get_access_profile(actor):
                raise CaseNotFoundError('API baseline was not found')
            if session.execute(baseline_query).one_or_none() != baseline_row:
                raise CaseNotFoundError('API baseline was not found')
            access.require_permission(actor, 'api.view')
            if profile != access.get_access_profile(actor):
                raise CaseNotFoundError('API baseline was not found')
            return {
                'baseline_id': baseline_id,
                'history_complete': False,
                'scan_limit': SCAN_LIMIT,
                'items': items,
            }
