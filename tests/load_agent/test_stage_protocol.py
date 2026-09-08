"""Regression coverage for frozen compiler versions and real stage ingestion."""
import copy
import inspect
import os

import pytest

from task_server.api_testing.services.load_scenario_compiler import compile_scenario, LoadScenarioCompileError
from task_server.api_testing.services.load_metric_service import LoadMetricError, LoadMetricService
from tests.api_testing.test_load_scenario_compiler import SEARCH_CHAIN, FIXED_RATE
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard
from tests.api_testing.test_load_metric_service import _running
from task_server.api_testing.models.load_testing import ApiLoadRun
from load_agent.k6_metrics import MetricAggregator


def test_frozen_v1_http_handoff_keeps_its_original_compiler(load_factory, load_records, monkeypatch):
    from task_server.api_testing import load_agent_http
    from task_server.api_testing.repositories.load_testing_repository import LoadTestingRepository
    from task_server.api_testing.models.load_testing import ApiLoadScenarioVersion
    from tests.api_testing.test_load_agent_http import _executable_run
    repository = LoadTestingRepository.from_factory(load_factory)
    run = _executable_run(repository, load_factory, load_records, 'legacy-handoff', 'search', '/search',
                          FIXED_RATE, compiler_version='k6-safe-v1')
    shard = repository.create_shard(run.id, load_records['agent'].id, 0, {'vus': 20, 'rate': 20}, 'load-owner')
    monkeypatch.setattr(load_agent_http, '_factory', lambda: load_factory)
    with load_factory() as session:
        version = session.get(ApiLoadScenarioVersion, run.scenario_version_id)
        payload = load_agent_http._execution_payload(session, run, shard, version)
    assert 'workflow_iteration_started' not in payload['script']
    assert payload['script'] == compile_scenario(version.definition, payload['workload'], compiler_version='k6-safe-v1').script


@pytest.mark.parametrize('compiler_version', ['k6-safe-v1', 'k6-safe-v2', 'k6-safe-v3'])
def test_actual_k6_stage_counters_reach_database_without_losing_http_metrics(tmp_path, load_factory, load_run_with_shard, compiler_version):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from sqlalchemy import select
    from task_server.api_testing.models.load_testing import ApiLoadMetricBucket
    from tests.api_testing.test_load_agent_http import _definition
    from load_agent.runtime import K6Runtime
    binary = os.getenv('K6_TEST_BINARY')
    if not binary:
        pytest.skip('K6_TEST_BINARY required for real local k6 acceptance')
    class Handler(BaseHTTPRequestHandler):
        requests = 0
        def do_GET(self):
            Handler.requests += 1
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self, *_args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    _, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    workload = {'executor': 'ramping-arrival-rate', 'start_rate': 2, 'time_unit': '1s', 'pre_allocated_vus': 2, 'max_vus': 2,
                'stages': [{'duration_seconds': 3, 'target': 2}, {'duration_seconds': 3, 'target': 2}]}
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).configuration = {'workload': workload}
    definition = _definition('search', '/readonly')
    compiled = compile_scenario(definition, workload, compiler_version=compiler_version)
    service = LoadMetricService(load_factory, publisher=lambda *_args: None)
    class Sink:
        def post_metrics(self, payload, batch_id):
            service.ingest(shard.agent_id, shard.id, {**payload, 'batch_id': batch_id})
        def post_samples(self, *_args, **_kwargs):
            raise AssertionError('No HTTP/business failures expected in local acceptance')
    try:
        result = K6Runtime(tmp_path, k6_binary=binary, poll_interval=.01).run({
            'id': shard.id, 'script': compiled.script, 'run': {'configuration': {'workload': workload}},
            'environment': {'BASE_URL_DEFAULT': f'http://127.0.0.1:{server.server_port}'}, 'dataset_rows': [],
        }, lambda: [], Sink())
        assert result.state == 'finished', result.error_message
        with load_factory() as session:
            rows = list(session.scalars(select(ApiLoadMetricBucket).where(ApiLoadMetricBucket.shard_id == shard.id)))
        assert sum(row.metrics['requests'] for row in rows) == Handler.requests
        assert sum(row.metrics['latency_histogram']['count'] for row in rows) == Handler.requests
        assert sum(row.metrics['iterations'] for row in rows) == Handler.requests
        stages = {stage: sum(row.metrics['workflow_starts'] for row in rows if row.scenario_step_id == f'__load_stage_{stage}') for stage in (0, 1)}
        if compiler_version in {'k6-safe-v2', 'k6-safe-v3'}:
            outside = sum(row.metrics['workflow_starts'] for row in rows if row.scenario_step_id == '__load_stage_outside')
            assert sum(stages.values()) + outside == Handler.requests
            assert outside <= 1  # Last scheduled iteration can begin just after the final boundary.
            assert all(4 <= count <= 7 for count in stages.values()), stages
        else:
            assert stages == {0: 0, 1: 0}
        assert result.load_generator_resources['samples'][0]['memory_used_bytes'] > 0
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)


def test_frozen_v1_preserves_original_script_hash_and_version():
    assert 'compiler_version' in inspect.signature(compile_scenario).parameters
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE, compiler_version='k6-safe-v1')
    assert compiled.content_hash == '395c07db057a1238336de833550cd7462fda4c44f6da64a461b86d80658a6fe7'
    assert compiled.compiler_version == 'k6-safe-v1'
    assert compile_scenario(SEARCH_CHAIN, FIXED_RATE).compiler_version == 'k6-safe-v3'
    with pytest.raises(LoadScenarioCompileError):
        compile_scenario(SEARCH_CHAIN, FIXED_RATE, compiler_version='untrusted-version')
    with pytest.raises(LoadScenarioCompileError):
        compile_scenario(SEARCH_CHAIN, FIXED_RATE, compiler_version='k6-safe-v1', stop_policy={'http_error_rate': .1, 'grace_seconds': 10})


def test_user_scenario_cannot_collide_with_reserved_stage_ids():
    definition = copy.deepcopy(SEARCH_CHAIN)
    definition['steps'][0]['id'] = '__load_stage_0'
    with pytest.raises(LoadScenarioCompileError):
        compile_scenario(definition, FIXED_RATE)


def test_generated_stage_metric_is_accepted_only_for_configured_stages(load_factory, load_run_with_shard):
    _, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).configuration = {'workload': {'executor': 'ramping-arrival-rate', 'stages': [{'duration_seconds': 5, 'target': 2}]}}
    aggregator = MetricAggregator()
    aggregator.accept({'type': 'Point', 'metric': 'workflow_iteration_started', 'data': {'time': '2026-09-08T04:00:00Z', 'value': 1, 'tags': {'step_id': '__load_stage_0'}}})
    buckets = [{key: value for key, value in row.items() if key != 'samples'} for row in aggregator.flush_all()]
    service = LoadMetricService(load_factory, publisher=lambda *_args: None)
    assert service.ingest(shard.agent_id, shard.id, {'batch_id': 'stage-positive', 'buckets': buckets})['accepted'] == 1
    invalid = copy.deepcopy(buckets)
    invalid[0]['step_id'] = '__load_stage_1'
    with pytest.raises(LoadMetricError):
        service.ingest(shard.agent_id, shard.id, {'batch_id': 'stage-invalid', 'buckets': invalid})
    contaminated = copy.deepcopy(buckets)
    contaminated[0]['metrics']['requests'] = 1
    with pytest.raises(LoadMetricError):
        service.ingest(shard.agent_id, shard.id, {'batch_id': 'stage-contaminated', 'buckets': contaminated})
