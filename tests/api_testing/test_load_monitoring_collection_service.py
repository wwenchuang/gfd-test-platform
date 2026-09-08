"""Synthetic Prometheus fixtures; these tests do not verify a deployed exporter."""
from datetime import datetime, timezone
import pytest
from task_server.api_testing.services.load_monitoring_collection_service import LoadMonitoringCollectionService

class Source:
    def __init__(self, stale=False, empty=False, fail=False):
        self.stale, self.empty, self.fail = stale, empty, fail
        self.calls = []
    def query_range(self, query, start, end, step_seconds):
        self.calls.append(query)
        if self.fail:
            raise RuntimeError('SECRET token')
        if self.empty:
            return []
        points = []
        for t in range(int(start), int(end) + 1, int(step_seconds)):
            value = (t - 1000 if self.stale else t - 2) if 'timestamp(' in query else 25
            points.append({'timestamp': t, 'value': value})
        return [{'labels': {'instance': 'fixture-host'}, 'points': points}]

class Config:
    def __init__(self, source): self.source = source
    def _client_for_revision(self, revision_id): return self.source
    def _definition_for_revision(self, revision_id):
        return {'name': 'Fixture host', 'deployment': 'host', 'source_url': 'https://approved.invalid',
                'metrics': ['cpu_percent', 'memory_percent'], 'labels': {'instance': 'fixture-host'}, 'step_seconds': 5}

def collect(source, **kwargs):
    svc = LoadMonitoringCollectionService(None, monitoring_service=Config(source))
    return svc.collect_window([{'revision_id': 'r1', 'required': True, 'source_url': 'http://evil.invalid'}],
                              start=1000, end=1010, terminal=kwargs.get('terminal', True), now=1010)

def test_fresh_metrics_keep_scope_denominators_and_actual_source_timestamps():
    result = collect(Source())
    assert result['complete'] and result['terminal']
    service = result['services'][0]
    assert service['scope'] == 'host'
    assert service['source_url'] == 'https://approved.invalid'
    assert service['metrics'][0]['denominator'] == 'host_cpu_cores'
    assert service['metrics'][0]['series'][0]['points'][0]['source_timestamp'] == 998
    assert service['metrics'][0]['peak'] == 25

def test_stale_evaluation_points_never_become_real_measurements():
    result = collect(Source(stale=True))
    assert not result['complete'] and result['terminal']
    metric = result['services'][0]['metrics'][0]
    assert metric['peak'] is None
    assert all(p['value'] is None for p in metric['series'][0]['points'])

def test_empty_is_missing_not_zero():
    result = collect(Source(empty=True))
    assert result['services'][0]['state'] == 'missing'
    assert result['services'][0]['metrics'][0]['peak'] is None

def test_failed_source_does_not_leak_error_or_stall_finalization():
    result = collect(Source(fail=True))
    assert result['terminal'] and not result['complete']
    assert result['services'][0]['state'] == 'failed'
    assert 'SECRET' not in str(result)

def test_running_collection_never_claims_final():
    result = collect(Source(), terminal=False)
    assert result['state'] == 'collecting' and not result['terminal']

def test_excessive_window_fails_bounded_without_queries():
    source = Source()
    svc = LoadMonitoringCollectionService(None, monitoring_service=Config(source))
    result = svc.collect_window([{'revision_id': 'r1'}], start=1, end=100000, terminal=True, now=100000)
    assert result['terminal'] and result['state'] == 'failed'
    assert source.calls == []

from contextlib import contextmanager
from types import SimpleNamespace

class Sessions:
    def __init__(self, run): self.run = run
    @contextmanager
    def begin(self): yield self
    def get(self, *args): return self.run
    def scalar(self, query):
        assert query._for_update_arg is not None
        return self.run

def test_collect_merges_concurrent_summary_and_freezes_terminal_result():
    run = SimpleNamespace(started_at=datetime.fromtimestamp(1000, timezone.utc),
        finished_at=datetime.fromtimestamp(1010, timezone.utc), state='finished',
        configuration={'monitoring': {'services': [{'revision_id': 'r1'}], 'before_seconds': 0, 'after_seconds': 0}},
        summary={'transport': {'requests': 5}})
    source = Source()
    orig = source.query_range
    def query(*args):
        run.summary['concurrent_update'] = 'preserved'
        return orig(*args)
    source.query_range = query
    service = LoadMonitoringCollectionService(Sessions(run), monitoring_service=Config(source),
                    now=lambda: datetime.fromtimestamp(1040, timezone.utc))
    result = service.collect('run')
    assert result['terminal'] and result['complete']
    assert run.summary['concurrent_update'] == 'preserved'
    assert run.summary['transport']['requests'] == 5
    count = len(source.calls)
    assert service.collect('run') == result
    assert len(source.calls) == count

def test_cancel_waits_afterwindow_then_terminates():
    run = SimpleNamespace(started_at=datetime.fromtimestamp(1000, timezone.utc),
        finished_at=datetime.fromtimestamp(1010, timezone.utc), state='cancelled',
        configuration={'monitoring': {'services': [{'revision_id': 'r1'}], 'before_seconds': 0, 'after_seconds': 60}}, summary={})
    now = [1020]
    service = LoadMonitoringCollectionService(Sessions(run), monitoring_service=Config(Source()),
                now=lambda: datetime.fromtimestamp(now[0], timezone.utc))
    assert service.collect('run')['terminal'] is False
    now[0] = 1100
    assert service.collect('run')['terminal'] is True

def test_missing_core_capacity_blocks_cpu_percent_claim():
    source = Source()
    orig = source.query_range
    source.query_range = lambda query, *args: [] if query.startswith('count without') else orig(query, *args)
    result = collect(source)
    assert not result['complete']
    assert result['services'][0]['metrics'][0]['peak'] is None

def test_sparse_one_instance_cannot_hide_behind_other_instance():
    source = Source()
    orig = source.query_range
    def query(*args):
        rows = orig(*args)
        rows.append({'labels': {'instance': 'fixture-host-2'}, 'points': rows[0]['points'][:1]})
        return rows
    source.query_range = query
    result = collect(source)
    assert result['services'][0]['state'] == 'missing'
    assert not result['complete']

@pytest.mark.parametrize('state', ['starting', 'queued'])
def test_start_dispatch_without_started_timestamp_remains_pending(state):
    run = SimpleNamespace(started_at=None, finished_at=None, created_at=datetime.fromtimestamp(1000, timezone.utc),
        state=state, configuration={'monitoring': {'services': [{'revision_id': 'r1', 'required': True}]}}, summary={})
    source = Source()
    service = LoadMonitoringCollectionService(Sessions(run), monitoring_service=Config(source),
                now=lambda: datetime.fromtimestamp(1010, timezone.utc))
    result = service.collect('run')
    assert result['state'] == 'collecting' and result['terminal'] is False
    assert result['services'][0]['required'] is True
    assert source.calls == []


def test_start_pending_expires_after_fifteen_minutes():
    run = SimpleNamespace(started_at=None, finished_at=None, created_at=datetime.fromtimestamp(1000, timezone.utc),
        state='starting', configuration={'monitoring': {'services': [{'revision_id': 'r1', 'required': True}]}}, summary={})
    service = LoadMonitoringCollectionService(Sessions(run), monitoring_service=Config(Source()),
                now=lambda: datetime.fromtimestamp(1901, timezone.utc))
    result = service.collect('run')
    assert result['terminal'] and result['state'] == 'failed'
    assert result['services'][0]['state'] == 'failed'


def test_cancelled_without_actual_start_is_explicit_terminal_failure():
    run = SimpleNamespace(started_at=None, finished_at=datetime.fromtimestamp(1010, timezone.utc),
        created_at=datetime.fromtimestamp(1000, timezone.utc), state='cancelled',
        configuration={'monitoring': {'services': [{'revision_id': 'r1'}]}}, summary={})
    service = LoadMonitoringCollectionService(Sessions(run), monitoring_service=Config(Source()),
                now=lambda: datetime.fromtimestamp(1010, timezone.utc))
    result = service.collect('run')
    assert result['terminal'] and result['state'] == 'failed'
    assert '开始时间' in result['message']


def test_finalization_waits_ingestion_grace_without_extending_query_window():
    run = SimpleNamespace(started_at=datetime.fromtimestamp(1000, timezone.utc),
        finished_at=datetime.fromtimestamp(1010, timezone.utc), state='finished',
        configuration={'monitoring': {'services': [{'revision_id': 'r1'}], 'before_seconds': 0, 'after_seconds': 0}}, summary={})
    now = [1010]
    service = LoadMonitoringCollectionService(Sessions(run), monitoring_service=Config(Source()),
                now=lambda: datetime.fromtimestamp(now[0], timezone.utc))
    first = service.collect('run')
    assert first['terminal'] is False
    now[0] = 1040
    last = service.collect('run')
    assert last['terminal'] is True
    assert last['window_end'] == first['window_end']


def test_window_limit_retains_selected_required_service_failures():
    service = LoadMonitoringCollectionService(None, monitoring_service=Config(Source()))
    result = service.collect_window([{'revision_id': 'r1', 'required': True}],
                                   start=1, end=100000, terminal=True, now=100000)
    assert result['terminal'] and result['services'][0]['required']
    assert result['services'][0]['state'] == 'failed'


def test_probe_uses_integer_second_grid_with_microsecond_clock():
    source = Source()
    original = source.query_range
    def query(query, start, end, step):
        assert isinstance(start, int) and isinstance(end, int)
        return original(query, start, end, step)
    source.query_range = query
    service = LoadMonitoringCollectionService(None, monitoring_service=Config(source),
                now=lambda: datetime.fromtimestamp(1010.123456, timezone.utc))
    assert service.probe([{'revision_id': 'r1'}])['complete']


def test_memory_timestamp_query_keeps_raw_metric_names_separate():
    from task_server.api_testing.services.load_monitoring_collection_service import _queries
    _, freshness, _ = _queries('memory_percent', Config(Source())._definition_for_revision('r1'))
    assert '__name__=~' not in freshness
    assert 'timestamp(node_memory_MemAvailable_bytes{' in freshness
    assert 'timestamp(node_memory_MemTotal_bytes{' in freshness
    assert 'abs(' in freshness


def test_cpu_denominator_requires_every_core_to_have_rate_evidence():
    from task_server.api_testing.services.load_monitoring_collection_service import _queries
    _, _, capacity = _queries('cpu_percent', Config(Source())._definition_for_revision('r1'))
    assert 'rate(node_cpu_seconds_total{' in capacity
    assert '==' in capacity and ' and ' in capacity


def test_collection_global_point_budget_is_enforced(monkeypatch):
    from task_server.api_testing.services import load_monitoring_collection_service as module
    monkeypatch.setattr(module, 'MAX_OUTPUT_POINTS', 4)
    source = Source()
    result = collect(source)
    assert not result['complete']
    assert sum(len(s['points']) for item in result['services'] for m in item['metrics'] for s in m['series']) <= 4
    assert result['services'][0]['state'] == 'failed'


def test_cpu_denominator_requires_fresh_history_for_each_current_core():
    from task_server.api_testing.services.load_monitoring_collection_service import _queries
    _, _, capacity = _queries('cpu_percent', Config(Source())._definition_for_revision('r1'))
    assert 'offset 2m' in capacity
    assert 'time() - 120 - 60' in capacity
    assert 'node_cpu_seconds_total{instance="fixture-host",mode="idle"} and ' in capacity

@pytest.mark.parametrize('key', ['network_receive_bytes_per_second', 'network_transmit_bytes_per_second', 'disk_read_bytes_per_second', 'disk_write_bytes_per_second', 'disk_read_iops', 'disk_write_iops'])
def test_host_device_rates_are_absolute_scoped_and_fresh(key):
    source = Source()
    config = Config(source)
    original = config._definition_for_revision
    config._definition_for_revision = lambda revision: dict(original(revision), metrics=[key])
    svc = LoadMonitoringCollectionService(None, monitoring_service=config)
    result = svc.probe([{'revision_id': 'r1'}])
    assert result['complete']
    metric = result['services'][0]['metrics'][0]
    assert metric['unit'] in ('bytes/s', 'IOPS')
    assert metric['series'][0]['points'][0]['denominator_value'] is None
    assert metric['series'][0]['points'][0]['used_value'] == 25


def test_container_without_quota_preserves_actual_usage_and_marks_percentage_unknown():
    class ContainerSource(Source):
        def query_range(self, query, *args):
            if 'container_spec_cpu_' in query: return []
            return super().query_range(query, *args)
    source = ContainerSource()
    config = Config(source)
    config._definition_for_revision = lambda _: {'name':'worker', 'deployment':'container', 'source_url':'https://approved.invalid', 'metrics':['cpu_cores','memory_working_set_bytes'], 'labels':{'instance':'a','id':'/docker/abc'}, 'step_seconds':15}
    result = LoadMonitoringCollectionService(None, monitoring_service=config).probe([{'revision_id':'r'}])
    assert result['complete']
    service = result['services'][0]
    assert service['scope'] == 'container'
    assert service['metrics'][0]['series'][0]['points'][0]['value'] == 25
    assert service['metrics'][0]['series'][0]['points'][0]['utilization_percent'] is None
    assert service['metrics'][1]['unit'] == 'bytes'


def test_idle_disk_latency_is_null_with_explicit_reason_not_zero_or_stale():
    class IdleSource(Source):
        def query_range(self, query, start, end, step):
            if 'timestamp(' in query: value = start - 2
            elif query.startswith('1000 *'): value = None
            else: value = 0
            return [{'labels':{'instance':'node','device':'sda'},'points':[{'timestamp':start,'value':value}]}]
    config = Config(IdleSource())
    config._definition_for_revision = lambda _: {'name':'host','deployment':'host','source_url':'https://approved.invalid','labels':{'instance':'node'},'metrics':['disk_read_latency_ms'],'step_seconds':15}
    result = LoadMonitoringCollectionService(None, monitoring_service=config).probe([{'revision_id':'r'}])
    point = result['services'][0]['metrics'][0]['series'][0]['points'][0]
    assert point['value'] is None and point['missing_reason'] == 'no_operations'
    assert result['services'][0]['metrics'][0]['peak'] is None


def test_postgres_series_retains_database_scope_without_resource_percentage():
    config = Config(Source())
    config._definition_for_revision = lambda _: {'name':'db','deployment':'postgres','source_url':'https://approved.invalid','labels':{'instance':'pg:9187','datname':'app'},'metrics':['postgres_connections','postgres_commits_per_second'],'step_seconds':15}
    result = LoadMonitoringCollectionService(None, monitoring_service=config).probe([{'revision_id':'r'}])
    assert result['complete'] and result['services'][0]['scope'] == 'postgres'
    assert result['services'][0]['metrics'][0]['unit'] == 'connections'
    assert result['services'][0]['metrics'][1]['unit'] == 'transactions/s'


@pytest.mark.parametrize('ready,restarts,stale', [(0, 0, False), (1, 2.5, False), (1, 0, True), (2, -1, False)])
def test_pod_state_preserves_zero_and_rejects_stale_or_invalid_values(ready, restarts, stale):
    class PodSource:
        def query_range(self, query, start, end, step):
            is_ready = 'kube_pod_status_ready' in query
            labels = {'instance': 'ksm:8080', 'namespace': 'qa', 'pod': 'api-1', 'uid': 'uid-1'}
            labels.update({'condition':'true'} if is_ready else {'container':'api'})
            value = (start - (1000 if stale else 2)) if 'timestamp(' in query else ready if is_ready else restarts
            return [{'labels': labels, 'points': [{'timestamp':start,'value':value}]}]
    config = Config(PodSource())
    config._definition_for_revision = lambda _: {'name':'Pod 状态', 'deployment':'pod_state', 'source_url':'https://approved.invalid', 'labels':{'instance':'ksm:8080','namespace':'qa','pod':'api-1'}, 'metrics':['pod_ready','pod_restarts_increase_2m'], 'step_seconds':15}
    result = LoadMonitoringCollectionService(None, monitoring_service=config).collect_window([{'revision_id':'r'}], start=1000,end=1000,terminal=True,now=1000)
    metrics = result['services'][0]['metrics']
    assert result['services'][0]['instance_count'] == 1
    assert len(metrics) == 2
    valid = not stale and ready in (0, 1) and restarts >= 0
    assert result['complete'] is valid
    assert metrics[0]['peak'] == (ready if valid else None)
    assert metrics[1]['peak'] == (restarts if valid else None)
    assert metrics[1]['window_seconds'] == 120
    assert '不可相加' in metrics[1]['semantics']
    assert metrics[0]['series'][0]['labels']['uid'] == 'uid-1'


def test_pod_restart_freshness_requires_counter_history():
    from task_server.api_testing.services.load_monitoring_collection_service import _queries
    definition = {'deployment':'pod_state','labels':{'instance':'ksm','namespace':'qa','pod':'api'},'step_seconds':15}
    _, freshness, capacity = _queries('pod_restarts_increase_2m', definition)
    assert 'offset 2m' in freshness and 'time() - 120 - 60' in freshness
    assert capacity is None
