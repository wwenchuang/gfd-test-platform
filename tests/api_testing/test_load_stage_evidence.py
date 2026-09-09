from task_server.api_testing.services.load_report_service import LoadReportService

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
