import pytest

"""Load attainment must be supported by measured, sustained pressure."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from task_server.api_testing.services.load_report_service import LoadReportService

START = datetime(2026, 9, 7, tzinfo=timezone.utc)


def test_bucket_percentiles_never_exceed_observed_maximum():
    from task_server.api_testing.services.load_report_service import _percentile
    histogram = {'count': 19, 'bounds_ms': [10, 250, 500],
                 'counts': [18, 0, 1, 0], 'max_ms': 292.47}
    assert _percentile(histogram, .95) == 292.47
    assert _percentile(histogram, .50) == 10


def test_shortfall_explanation_does_not_claim_attainment():
    result = LoadReportService._load_goal(
        {'workload': {'executor': 'constant-arrival-rate', 'rate': 1}},
        {'totals': {'iterations': 19}, 'duration_seconds': 20})
    assert not result['reached']
    assert '未达到' in result['explanation']
    assert '95.00%' in result['explanation']


def test_configured_vus_and_one_iteration_do_not_prove_attainment():
    result = LoadReportService._load_goal(
        {"workload": {"executor": "constant-vus", "vus": 100, "duration_seconds": 60}},
        {"totals": {"iterations": 1}, "duration_seconds": 60},
    )
    assert result["reached"] is False
    assert "并发" in result["explanation"]


def test_ramp_does_not_compare_average_with_start_rate():
    result = LoadReportService._load_goal(
        {"workload": {"executor": "ramping-arrival-rate", "start_rate": 1, "time_unit": "1s", "stages": [{"target": 101, "duration_seconds": 10}]}},
        {"totals": {"iterations": 20}, "duration_seconds": 10},
    )
    assert result["reached"] is False
    assert result["expected_iterations"] == 510
    assert result["target_iterations_per_second"] == 51


def test_high_ramp_total_without_stage_evidence_stays_inconclusive():
    result = LoadReportService._load_goal(
        {"workload": {"executor": "ramping-arrival-rate", "start_rate": 0, "time_unit": "1s", "stages": [{"target": 10, "duration_seconds": 10}]}},
        {"totals": {"iterations": 1000}, "duration_seconds": 10},
    )
    assert not result["reached"]
    assert result["requires_stage_evidence"]

from task_server.api_testing.services.load_statistics import measured_vu_stages, measured_vus


def test_http_integrity_does_not_confuse_multi_request_workflows_or_window_boundaries():
    from task_server.api_testing.services.load_report_service import _http_sample_integrity
    rows = [SimpleNamespace(shard_id='a', scenario_step_id='search', metrics={
        'requests': 3, 'iterations': 1, 'latency_histogram': {'count': 2}}),
        SimpleNamespace(shard_id='a', scenario_step_id='search', metrics={
            'requests': 1, 'iterations': 1, 'latency_histogram': {'count': 2}})]
    assert _http_sample_integrity(rows)['consistent']


def test_opposite_sampling_errors_on_two_nodes_do_not_cancel_out():
    from task_server.api_testing.services.load_report_service import _http_sample_integrity
    rows = [SimpleNamespace(shard_id='a', scenario_step_id='search', metrics={
        'requests': 21, 'latency_histogram': {'count': 19}}),
        SimpleNamespace(shard_id='b', scenario_step_id='search', metrics={
            'requests': 19, 'latency_histogram': {'count': 21}})]
    result = _http_sample_integrity(rows)
    assert not result['consistent']
    assert len(result['mismatches']) == 2


def gauge(shard, offset, minimum=5, maximum=5, count=5):
    start = START + timedelta(seconds=offset)
    return SimpleNamespace(shard_id=shard, scenario_step_id='all', bucket_started_at=start,
        metrics={'vu_gauge': {'count': count, 'min': minimum, 'max': maximum, 'sum': minimum * count, 'max_gap_seconds': 1,
                             'first_at': start.isoformat(), 'last_at': (start + timedelta(seconds=4)).isoformat()}})


def varying_gauge(shard, offset, values):
    row = gauge(shard, offset, minimum=min(values), maximum=max(values), count=len(values))
    row.metrics['vu_gauge']['sum'] = sum(values)
    return row


def test_measured_vus_require_simultaneous_sustained_pressure_on_every_node():
    shards = [SimpleNamespace(id='a', allocation={'vus': 5}), SimpleNamespace(id='b', allocation={'vus': 5})]
    good = [gauge(s, t) for s in ('a', 'b') for t in (0, 5)]
    assert measured_vus(good, shards, 10, 10)['reached']
    shifted = [gauge('a', t) for t in (0, 5)] + [gauge('b', t) for t in (10, 15)]
    assert not measured_vus(shifted, shards, 10, 10)['reached']
    assert not measured_vus(good[:2], shards, 10, 10)['reached']


def test_constant_vus_allows_one_outer_five_second_bucket_at_run_boundary():
    shards = [SimpleNamespace(id='a', allocation={'vus': 4})]
    rows = [gauge('a', offset, minimum=4, maximum=4) for offset in (0, 5, 10, 15, 20)]

    result = measured_vus(rows, shards, 4, 30)

    assert result['reached']
    assert result['sustained_seconds'] == 24
    assert result['sampling_tolerance_seconds'] == 6


def test_constant_vus_does_not_hide_more_than_one_missing_boundary_bucket():
    shards = [SimpleNamespace(id='a', allocation={'vus': 4})]
    rows = [gauge('a', offset, minimum=4, maximum=4) for offset in (0, 5, 10, 15)]

    result = measured_vus(rows, shards, 4, 30)

    assert not result['reached']


def test_ramping_vus_require_each_continuous_stage_to_follow_its_trajectory():
    shards = [SimpleNamespace(id='a', allocation={'vus': 4})]
    workload = {'executor': 'ramping-vus', 'start_vus': 1, 'stages': [
        {'duration_seconds': 15, 'target': 2},
        {'duration_seconds': 15, 'target': 4},
        {'duration_seconds': 15, 'target': 1},
    ]}
    rows = [
        varying_gauge('a', 0, [1, 1, 1, 1, 1]),
        varying_gauge('a', 5, [1, 1, 2, 2, 2]),
        varying_gauge('a', 10, [2, 2, 2, 2, 2]),
        varying_gauge('a', 15, [2, 2, 2, 3, 3]),
        varying_gauge('a', 20, [3, 3, 3, 3, 4]),
        varying_gauge('a', 25, [4, 4, 4, 4, 4]),
        varying_gauge('a', 30, [4, 4, 3, 3, 3]),
        varying_gauge('a', 35, [3, 2, 2, 2, 2]),
        varying_gauge('a', 40, [2, 1, 1, 1, 1]),
    ]

    result = measured_vu_stages(rows, shards, {'a': workload})

    assert result['available']
    assert result['reached']
    assert [stage['reached'] for stage in result['shards'][0]['stages']] == [True, True, True]


def test_ramping_vus_rejects_a_peak_that_does_not_follow_the_middle_stage():
    shards = [SimpleNamespace(id='a', allocation={'vus': 4})]
    workload = {'executor': 'ramping-vus', 'start_vus': 1, 'stages': [
        {'duration_seconds': 15, 'target': 2},
        {'duration_seconds': 15, 'target': 4},
        {'duration_seconds': 15, 'target': 1},
    ]}
    rows = [varying_gauge('a', offset, [1, 1, 1, 1, 1]) for offset in range(0, 45, 5)]
    rows[5] = varying_gauge('a', 25, [1, 1, 1, 1, 4])

    result = measured_vu_stages(rows, shards, {'a': workload})

    assert result['available']
    assert not result['reached']
    assert not result['shards'][0]['stages'][1]['reached']


def test_gauge_peak_and_sparse_samples_cannot_prove_sustained_load():
    shards = [SimpleNamespace(id='a', allocation={'vus': 5})]
    assert not measured_vus([gauge('a', 0, minimum=1, maximum=5), gauge('a', 5)], shards, 5, 10)['reached']
    assert not measured_vus([gauge('a', 0, count=1), gauge('a', 5, count=1)], shards, 5, 10)['reached']
    assert not measured_vus([gauge('a', 0), gauge('a', 10)], shards, 5, 10)['reached']


def test_no_business_assertions_cannot_pass_a_zero_failure_threshold():
    items = LoadReportService._thresholds(
        {'business_failure_rate': {'operator': 'less_than_or_equal', 'value': 0}},
        {'business': {'assertions': 0, 'failure_rate': 0}},
    )
    assert items[0]['actual'] is None
    assert items[0]['passed'] is False
    assert items[0]['status_label'] == '无样本，无法判断'


def test_ramp_minute_units_are_normalized_before_integrating():
    result = LoadReportService._load_goal(
        {'workload': {'executor': 'ramping-arrival-rate', 'start_rate': 0, 'time_unit': '1m',
                      'stages': [{'duration_seconds': 60, 'target': 60}]}},
        {'totals': {'iterations': 30}, 'duration_seconds': 60})
    assert result['expected_iterations'] == 30
    assert result['target_iterations_per_second'] == .5


def test_gauges_with_internal_sampling_gaps_cannot_pass():
    shards = [SimpleNamespace(id='a', allocation={'vus': 5})]
    rows = [gauge('a', 0), gauge('a', 5)]
    for row in rows:
        row.metrics['vu_gauge']['max_gap_seconds'] = 3.99
    assert not measured_vus(rows, shards, 5, 10)['reached']

@pytest.mark.parametrize('requests,samples,accepted', [(21,19,True),(19,21,True),(1000,999,True),(1000,997,False),(5,3,False),(1,0,False),(20,20,True)])
def test_http_sample_count_tolerance(requests, samples, accepted):
    from task_server.api_testing.services.load_report_service import _http_sample_integrity
    row = SimpleNamespace(shard_id='a', scenario_step_id='s', metrics={'requests': requests, 'latency_histogram': {'count': samples}})
    result = _http_sample_integrity([row])
    assert result['acceptable'] is accepted
    assert result['consistent'] is (requests == samples)
