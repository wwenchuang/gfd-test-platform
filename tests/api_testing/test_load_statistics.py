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

from task_server.api_testing.services.load_statistics import measured_vus


def gauge(shard, offset, minimum=5, maximum=5, count=5):
    start = START + timedelta(seconds=offset)
    return SimpleNamespace(shard_id=shard, scenario_step_id='all', bucket_started_at=start,
        metrics={'vu_gauge': {'count': count, 'min': minimum, 'max': maximum, 'sum': minimum * count, 'max_gap_seconds': 1,
                             'first_at': start.isoformat(), 'last_at': (start + timedelta(seconds=4)).isoformat()}})


def test_measured_vus_require_simultaneous_sustained_pressure_on_every_node():
    shards = [SimpleNamespace(id='a', allocation={'vus': 5}), SimpleNamespace(id='b', allocation={'vus': 5})]
    good = [gauge(s, t) for s in ('a', 'b') for t in (0, 5)]
    assert measured_vus(good, shards, 10, 10)['reached']
    shifted = [gauge('a', t) for t in (0, 5)] + [gauge('b', t) for t in (10, 15)]
    assert not measured_vus(shifted, shards, 10, 10)['reached']
    assert not measured_vus(good[:2], shards, 10, 10)['reached']


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
