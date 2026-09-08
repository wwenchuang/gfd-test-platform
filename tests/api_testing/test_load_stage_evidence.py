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
