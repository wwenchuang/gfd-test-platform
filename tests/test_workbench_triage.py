import json
import uuid
from unittest.mock import patch

import pytest

from task_server.services import platform_mcp
from task_server.services import workbench_triage as triage


ACTOR = ('owner','name','private-session')


def row(status, finished, suite='feedback-categories', attempts=None, error=None, evidence=None):
    rid = str(uuid.uuid4())
    runner, case_id = platform_mcp.SUITES[suite][1:]
    attempts = attempts or [(1,status,error)]
    return {'run_id':rid,'request_id':str(uuid.uuid4()),'environment':'platform','suite_id':suite,
            'status':status,'created_at':finished-1,'finished_at':finished,'runner_id':'a'*32,
            'cases':[{'run_id':rid,'case_id':case_id,'runner':runner,'attempt':n,'status':s,
                      'error_kind':kind,'duration_ms':25,'worker_exit_code':0,'exit_code':{'passed':0,'failed':1}.get(s),
                      'version':'v22.0.0' if runner == 'node' else '3.11.0','evidence':evidence or {}}
                     for n,s,kind in attempts]}


def history(rows):
    return patch.object(platform_mcp,'history',return_value=rows)


def test_explicit_attempts_counts_duration_and_drill_exclusion():
    recovered = row('passed',100,attempts=[(2,'passed',None),(1,'failed','business')])
    new = row('failed',101,error='business')
    drill = row('failed',102,suite='catalog-failure-drill',error='drill_assertion')
    cancelled = row('cancelled',103,error='cancelled')
    interrupted = row('interrupted',104,error='worker_crash')
    queued = dict(row('passed',105),status='queued',cases=[],finished_at=None)
    with history([queued,interrupted,cancelled,drill,new,recovered]) as get:
        report = triage.report(ACTOR)
    get.assert_called_once_with(ACTOR)
    m = report['metrics']
    assert m['counts'] == dict(passed=1,failed=1,skipped=1,errors=1,total=4)
    assert (m['first_pass_rate']['numerator'],m['first_pass_rate']['denominator']) == (0,4)
    assert (m['final_pass_rate']['value'],m['retry_recovery_rate']['value']) == (25.0,100.0)
    assert m['duration'] == dict(sample_count=5,total_ms=125,mean_ms=25.0)
    assert (report['drill_runs'],report['active_runs'],report['history_count']) == (1,1,6)
    assert all('drill' not in incident['fingerprint'] for incident in report['incidents'])


def test_independent_runs_do_not_become_retries_and_recovery_reopens():
    a, b, c, d = [row(s,t,error=e) for s,t,e in (
        ('failed',100,'business'),('failed',105,'business'),('passed',140,None),('failed',150,'business'))]
    with history([d,c,b,a]):
        r = triage.report(ACTOR)
    assert r['metrics']['retry_recovery_rate']['value'] is None
    assert r['metrics']['flake_rate']['value'] is None
    assert r['metrics']['first_pass_rate'] == r['metrics']['final_pass_rate']
    closed, opened = sorted(r['incidents'],key=lambda item:item['opened_at'])
    assert (closed['occurrences'],closed['status'],closed['recovery_ms'],closed['recovery_run_id']) == (2,'recovered',40000,c['run_id'])
    assert opened['status'] == 'open' and opened['recovered_at'] is None
    assert closed['fingerprint'] == opened['fingerprint']
    assert r['metrics']['average_recovery_ms'] == 40000


def test_empty_and_missing_first_observation_are_not_synthetic_first_attempts():
    with history([]):
        report = triage.report(ACTOR)
    rate = report['metrics']['first_pass_rate']
    assert rate['value'] is None and rate['denominator'] == 0 and rate['note']
    only_second = row('passed',500,attempts=[(2,'passed',None)])
    with history([only_second]):
        m = triage.report(ACTOR)['metrics']
    assert m['first_pass_rate']['value'] is None and m['final_pass_rate']['value'] == 100
    assert m['retry_recovery_rate']['value'] is None


def test_safe_typed_facts_authorization_and_drill_draft_references():
    r = row('failed',100,suite='catalog-failure-drill',error='drill_assertion',evidence={
        'expected_total':0,'total':36,'screenshot':'/api/ui-executions/web-check/artifact?id='+'a'*32+'&item=screenshot',
        'trace':'https://evil.site/secret','cookie':'very-secret','data_is_list':'very-secret'})
    r.update(owner='very-secret',raw_logs='very-secret')
    r['cases'][0]['version']='very-secret'
    with history([r]):
        bundle = triage.evidence_bundle(ACTOR,r['run_id'])
        draft = triage.defect_draft(ACTOR,r['run_id'])
    assert bundle['is_drill'] and draft['is_drill']
    assert bundle['facts']['observed.total'] == '36' and bundle['facts']['artifact.screenshot']=='available'
    assert 'artifact.trace' not in bundle['facts']
    assert not any(s in json.dumps([bundle,draft]) for s in ('very-secret','evil.site','private-session'))
    assert draft['refs'] and set(draft['refs']) <= set(bundle['facts'])
    assert draft['expected'] and draft['actual'] and draft['reproduction'] and draft['to_confirm']
    with history([]), pytest.raises(platform_mcp.ToolError) as exc:
        triage.defect_draft(ACTOR,r['run_id'])
    assert exc.value.status == 404


def test_draft_rejects_success_and_does_not_claim_unknown_business_result():
    successful = row('passed',100)
    blocked = row('blocked',110,error='authentication')
    with history([successful,blocked]):
        with pytest.raises(platform_mcp.ToolError) as exc:
            triage.defect_draft(ACTOR, successful['run_id'])
        assert exc.value.status == 409
        d = triage.defect_draft(ACTOR,blocked['run_id'])
        assert '业务断言' in ' '.join(d['actual'])


def test_bundle_reports_clock_and_partial_attempt_coverage_without_claiming_raw_log_completeness():
    r = row('failed',30,attempts=[(1,'failed','business'),(2,'failed','business')])
    r['started_at'] = 27.5
    with history([r]):
        b = triage.evidence_bundle(ACTOR,r['run_id'])
    assert (b['started_at'],b['finished_at']) == (27.5,30)
    assert b['coverage'] == {'source':'structured_case_result','attempts_observed':2,'attempts_included':1,
                             'truncated':True,'raw_logs_included':False,'source_logs_truncated':None}


def test_independent_simultaneous_observations_cannot_prove_temporal_recovery():
    a,b = row('failed',100,error='business'),row('passed',100)
    b['run_id'] = b['cases'][0]['run_id'] = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
    with history([b,a]):
        r = triage.report(ACTOR)
    assert r['incidents'][0]['status'] == 'open'
    assert r['metrics']['average_recovery_ms'] is None


def test_candidate_sampling_collects_only_observed_unique_failures_without_labels():
    eligible = [row('failed',100+i,error='business') for i in range(22)]
    dormant = row('blocked',200,error='execution_disabled')
    dormant['cases'][0]['worker_exit_code']=None
    dormant['runner_id']=None
    drill = row('failed',210,suite='catalog-failure-drill',error='drill_assertion')
    with history([drill,dormant,eligible[0],*eligible]):
        report = triage.report(ACTOR)
    ids=[c['run_id'] for c in report['gold_candidates']]
    assert ids == [r['run_id'] for r in eligible[:20]]
    assert report['methodology'] and all(not c.keys() & {'gold','category','label'} for c in report['gold_candidates'])
    assert all('case.status' in c['refs'] and c['case_title'] and c['case_id']=='feedback_categories' for c in report['gold_candidates'])


def test_legacy_business_failure_with_confirmed_artifact_can_enter_candidate_pool():
    r = row('failed',300,suite='published-catalog',error='business')
    c = r['cases'][0]
    c['worker_exit_code'] = None
    c['evidence'] = {'screenshot':'/api/ui-executions/web-check/artifact?id='+'a'*32+'&item=screenshot'}
    with history([r]):
        candidate, = triage.report(ACTOR)['gold_candidates']
        evidence = triage.evidence_bundle(ACTOR,r['run_id'])
    assert 'artifact.screenshot' in candidate['refs'] and set(candidate['refs']) <= set(evidence['facts'])
