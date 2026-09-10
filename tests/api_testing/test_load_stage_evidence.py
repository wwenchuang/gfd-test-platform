from task_server.api_testing.services.load_report_service import LoadReportService

def test_ramp_integer_schedule_matches_real_k6_without_hiding_live_drop():
    config = {'executor': 'ramping-arrival-rate', 'start_rate': 1,
              'stages': [{'duration_seconds': 15, 'target': target} for target in (2, 4, 2)]}
    aggregate = {'totals': {'iterations': 112}, 'duration_seconds': 45,
                 'stage_starts': {'0': 22, '1': 45, '2': 45}}
    result = LoadReportService._load_goal(config, aggregate)
    assert result['reached']
    assert [s['expected_iterations'] for s in result['stages']] == [22.5, 45, 45]
    assert [s['scheduled_iterations'] for s in result['stages']] == [22, 45, 45]
    aggregate['totals']['iterations'] = 111
    aggregate['stage_starts']['2'] = 44
    result = LoadReportService._load_goal(config, aggregate)
    assert result['stages'][0]['reached']
    assert not result['stages'][2]['reached'] and not result['reached']


def test_ramp_carries_fractional_iterations_between_stages_and_keeps_zero_recovery():
    config = {'executor': 'ramping-arrival-rate', 'start_rate': 30, 'time_unit': '1m',
              'stages': [{'duration_seconds': 1, 'target': target} for target in (30, 30, 0, 0)]}
    aggregate = {'totals': {'iterations': 1}, 'duration_seconds': 4,
                 'stage_starts': {'0': 0, '1': 1, '2': 0, '3': 0}}
    result = LoadReportService._load_goal(config, aggregate)
    assert [s['scheduled_iterations'] for s in result['stages']] == [0, 1, 0, 0]
    assert result['reached']
    aggregate['stage_starts']['1'] = 0
    assert not LoadReportService._load_goal(config, aggregate)['reached']


def test_ramp_with_no_complete_scheduled_iteration_cannot_prove_load():
    result = LoadReportService._load_goal(
        {'executor': 'ramping-arrival-rate', 'start_rate': 1, 'time_unit': '1m',
         'stages': [{'duration_seconds': 1, 'target': 1}]},
        {'totals': {'iterations': 0}, 'duration_seconds': 1, 'stage_starts': {'0': 0}})
    assert not result['reached']


def test_integer_boundary_missing_start_is_not_silently_forgiven():
    result = LoadReportService._load_goal(
        {'executor': 'ramping-arrival-rate', 'start_rate': 2,
         'stages': [{'duration_seconds': 10, 'target': 2}]},
        {'totals': {'iterations': 19}, 'duration_seconds': 10, 'stage_starts': {'0': 19}})
    assert not result['reached']

def test_stage_count_uses_actual_starts_and_does_not_hide_bad_stage_with_total():
    config={'executor':'ramping-arrival-rate','start_rate':1,'time_unit':'1s','stages':[{'duration_seconds':10,'target':9},{'duration_seconds':10,'target':1}]}
    aggregate={'totals':{'iterations':100},'duration_seconds':20,'stage_starts':{'0':30,'1':70}}
    result=LoadReportService._load_goal(config,aggregate)
    assert result['expected_iterations']==100
    assert not result['reached'] and not result['requires_stage_evidence']
    assert result['stages'][0]['actual_started_iterations']==30
    aggregate['stage_starts']={'0':50,'1':50}
    assert LoadReportService._load_goal(config,aggregate)['reached']

def test_old_runs_do_not_get_synthetic_stage_starts():
    result=LoadReportService._load_goal({'executor':'ramping-arrival-rate','start_rate':2,'stages':[{'duration_seconds':10,'target':2}]},{'totals':{'iterations':20},'duration_seconds':10})
    assert result['requires_stage_evidence'] and not result['reached']



def test_ramping_vus_uses_stage_aligned_gauge_evidence():
    config = {
        'executor': 'ramping-vus',
        'start_vus': 1,
        'stages': [
            {'duration_seconds': 15, 'target': 2},
            {'duration_seconds': 15, 'target': 4},
            {'duration_seconds': 15, 'target': 1},
        ],
    }
    evidence = {
        'requires_stage_evidence': False,
        'reached': True,
        'reason': '各节点实际并发在每个阶段达到目标。',
        'shards': [{
            'shard_id': 'a',
            'stages': [
                {'start_vus': 1, 'target_vus': 2, 'planned_average_vus': 1.5, 'actual_average_vus': 1.5, 'reached': True},
                {'start_vus': 2, 'target_vus': 4, 'planned_average_vus': 3.0, 'actual_average_vus': 3.0, 'reached': True},
                {'start_vus': 4, 'target_vus': 1, 'planned_average_vus': 2.5, 'actual_average_vus': 2.5, 'reached': True},
            ],
        }],
    }

    result = LoadReportService._load_goal(
        config,
        {'totals': {'iterations': 39}, 'duration_seconds': 45, 'vu_stage_evidence': evidence},
    )

    assert result['reached']
    assert not result['requires_stage_evidence']
    assert [stage['target_vus'] for stage in result['stages']] == [2, 4, 1]
    assert [stage['start_vus'] for stage in result['stages']] == [1, 2, 4]
    assert [stage['planned_average_vus'] for stage in result['stages']] == [1.5, 3.0, 2.5]
    assert [stage['actual_average_vus'] for stage in result['stages']] == [1.5, 3.0, 2.5]
    assert all(stage['reached'] for stage in result['stages'])
