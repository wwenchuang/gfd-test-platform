"""Authorization integration against the isolated API-testing PostgreSQL fixture."""
import pytest

from task_server.services import week2_real
from task_server.api_testing.http import ApiHttpError
from tests.api_testing.test_access import api_context, owned_records, identities, _passing_evidence


def test_existing_platform_scope_is_enforced_for_week2(api_context, owned_records, identities):
    evidence_id = _passing_evidence(api_context['factory'], owned_records)
    execution_id = owned_records['execution'].id
    week2_real._scope_authorized('reader', execution_id, evidence_id)
    identities['reader']['scope']['api_environments'] = []
    with pytest.raises(ApiHttpError) as denied:
        week2_real._scope_authorized('reader', execution_id, evidence_id)
    assert denied.value.status == 404
    identities['reader']['scope']['api_environments'] = [owned_records['environment'].id]
    identities['reader']['scope']['api_projects'] = [owned_records['other_project'].id]
    with pytest.raises(ApiHttpError) as denied:
        week2_real._scope_authorized('reader', execution_id, evidence_id)
    assert denied.value.status == 404
    identities['reader']['scope']['api_projects'] = [owned_records['project'].id]
    with pytest.raises(ValueError):
        week2_real._scope_authorized('reader', owned_records['second_execution'].id, evidence_id)
