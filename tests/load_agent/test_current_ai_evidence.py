"""Old diagnosis text cannot be presented as advice for today's report evidence."""
import pytest

from task_server.api_testing.models.load_testing import ApiLoadAiAnalysis, ApiLoadRun
from task_server.api_testing.services.load_ai_analysis_service import LoadAiAnalysisService, PROMPT_VERSION, build_evidence_package, _hash
from task_server.api_testing.services.load_report_service import LoadReportService
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard
from tests.api_testing.test_load_testing_http import users, _call


def _analysis(factory, run_id, prompt, evidence, state='completed'):
    with factory.begin() as session:
        session.get(ApiLoadRun, run_id).state = 'finished'
        record = ApiLoadAiAnalysis(run_id=run_id, prompt_version=prompt, evidence_hash=evidence, state=state,
                                   result={'conclusion': '过期证据不能展示'}, owner_id='load-owner', created_by='load-owner', updated_by='load-owner')
        session.add(record); session.flush()
        return record.id


@pytest.mark.parametrize('stale_field', ['prompt', 'hash'])
def test_get_analysis_hides_old_prompt_or_changed_evidence(load_factory, load_run_with_shard, users, stale_field):
    _, run, _ = load_run_with_shard
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).state = 'finished'
    evidence = _hash(build_evidence_package(LoadReportService(load_factory).build(run.id, 'viewer')))
    _analysis(load_factory, run.id, 'api-load-analysis.v1' if stale_field == 'prompt' else PROMPT_VERSION,
              '0' * 64 if stale_field == 'hash' else evidence)
    response, _ = _call(load_factory, 'GET', f'/load-runs/{run.id}/ai-analysis', 'viewer')
    assert response['analysis'] is None
    current_id = _analysis(load_factory, run.id, PROMPT_VERSION, evidence, state='queued')
    response, _ = _call(load_factory, 'GET', f'/load-runs/{run.id}/ai-analysis', 'viewer')
    assert response['analysis']['id'] == current_id
    assert response['analysis']['state'] == 'queued'


def test_request_does_not_reuse_old_prompt_with_matching_numeric_evidence(load_factory, load_run_with_shard, users):
    _, run, _ = load_run_with_shard
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).state = 'finished'
    reports = LoadReportService(load_factory)
    evidence = _hash(build_evidence_package(reports.build(run.id, 'viewer')))
    old_id = _analysis(load_factory, run.id, 'api-load-analysis.v1', evidence)
    current = LoadAiAnalysisService(load_factory, report_service=reports).request(run.id, 'viewer')
    assert current.id != old_id
    assert current.prompt_version == PROMPT_VERSION
    assert current.state == 'queued'
