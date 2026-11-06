import pytest
from task_server.services import ui_video_touch_targets as targets,ui_video_windows as windows
from task_server.workers import ui_video_worker as worker


def test_conflict_keeps_bounded_private_target_pair_without_changing_decision():
    evidence={};frames=[{'frame_id':'frame-001','timestamp_ms':0}]
    event={'action':'aiTap','target':'底部确认按钮','frame_refs':['frame-001']}
    hint={'frame_id':'frame-001','target':'取消按钮','observation':'private observation'}
    assert targets.conflict([event],[hint],frames,evidence=evidence)
    assert evidence=={'frame_id':'frame-001','observed_target':'底部确认按钮','probe_target':'取消按钮'}
    assert 'private observation' not in str(evidence)


def test_window_failure_redacts_input_and_preserves_final_pair(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai,ui_video_touch_detail as detail
    frames=[{'frame_id':'frame-001','timestamp_ms':0}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','target':'取消'}])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[{'action':'aiTap','target':'确认secret-value','value':'','frame_refs':['frame-001'],'observation':'点击','uncertainties':[]}],'uncertainties':[],'multiple_scenarios':False})
    with pytest.raises(ValueError) as caught:windows.observe_windows(frames,100,tmp_path,lambda:False,prior_secrets=['secret-value'])
    pair=caught.value.target_failure_evidence
    assert 'secret-value' not in str(pair)
    assert pair['probe_target']=='取消'


@pytest.mark.parametrize('mutation',['unknown_frame','too_long','unknown_field','wrong_code'])
def test_worker_private_evidence_rejects_invalid_payload(mutation):
    error=ValueError('video_model_invalid');error.target_failure_evidence={'frame_id':'frame-001','observed_target':'确定','probe_target':'取消'}
    if mutation=='unknown_frame':error.target_failure_evidence['frame_id']='frame-999'
    if mutation=='too_long':error.target_failure_evidence['observed_target']='x'*401
    if mutation=='unknown_field':error.target_failure_evidence['token']='private'
    code='video_analysis_failed' if mutation=='wrong_code' else 'video_model_invalid'
    assert worker._private_target_failure(error,code,{'media':{'frames':[{'frame_id':'frame-001'}]}})=={}


def test_worker_private_evidence_is_content_whitelist_and_not_logged(caplog):
    error=ValueError('video_model_invalid');error.target_failure_evidence={'frame_id':'frame-001','observed_target':'PRIVATE-A','probe_target':'PRIVATE-B'}
    doc={'media':{'frames':[{'frame_id':'frame-001'}]}}
    assert worker._private_target_failure(error,'video_model_invalid',doc)==error.target_failure_evidence
    worker._log_failure('uvs_'+'a'*32,'analysis',error,'video_model_invalid')
    assert 'PRIVATE-' not in caplog.text


def test_worker_keeps_only_known_bounded_missing_frame_refs():
    error=ValueError('video_required_action_missing')
    error.missing_frame_refs={'frame-001','frame-002'}
    doc={'media':{'frames':[{'frame_id':'frame-001'},{'frame_id':'frame-002'}]}}
    assert worker._private_target_failure(error,'video_required_action_missing',doc)=={'missing_frame_refs':['frame-001','frame-002']}
    error.missing_frame_refs.add('frame-999')
    assert worker._private_target_failure(error,'video_required_action_missing',doc)=={}
    error.missing_frame_refs={'private-payload'}
    assert worker._private_target_failure(error,'video_required_action_missing',doc)=={}


@pytest.mark.parametrize('reason',['template','selection','unsupported'])
def test_worker_keeps_only_bounded_outcome_gate_classification(reason):
    error=ValueError('video_expected_missing')
    error.outcome_failure_evidence={'candidate_index':7,'reason':reason}
    assert worker._private_target_failure(error,'video_expected_missing',{})==error.outcome_failure_evidence
    error.outcome_failure_evidence['sensitive']='not allowed'
    assert worker._private_target_failure(error,'video_expected_missing',{})=={}


@pytest.mark.parametrize('index,reason',[(-1,'selection'),(120,'selection'),(True,'selection'),(0,[]),(0,'other')])
def test_worker_rejects_invalid_outcome_gate_classification(index,reason):
    error=ValueError('video_expected_missing')
    error.outcome_failure_evidence={'candidate_index':index,'reason':reason}
    assert worker._private_target_failure(error,'video_expected_missing',{})=={}


def test_coverage_failure_records_only_known_frame_ids():
    error=ValueError('video_coverage_resolution_invalid')
    error.coverage_frame_refs={'frame-001','frame-002'}
    doc={'media':{'frames':[{'frame_id':'frame-001'},{'frame_id':'frame-002'}]}}
    assert worker._private_target_failure(error,'video_coverage_resolution_invalid',doc)=={'coverage_frame_refs':['frame-001','frame-002']}
    error.coverage_frame_refs.add('frame-999')
    assert worker._private_target_failure(error,'video_coverage_resolution_invalid',doc)=={}


def test_coverage_failure_keeps_only_bounded_actions_and_original_refs():
    error=ValueError('video_coverage_resolution_invalid')
    error.coverage_frame_refs={'frame-001','frame-002'}
    error.coverage_candidates={
        'first_pass':[{'action':'navigateBack','frame_refs':['frame-001','frame-002']}],
        'independent_review':[{'action':'aiTap','frame_refs':['frame-001']}]}
    doc={'media':{'frames':[{'frame_id':'frame-001'},{'frame_id':'frame-002'}]}}
    result=worker._private_target_failure(error,'video_coverage_resolution_invalid',doc)
    assert result=={'coverage_frame_refs':['frame-001','frame-002'], 'coverage_candidates':error.coverage_candidates}
    error.coverage_candidates['first_pass'][0]['observation']='secret model text'
    assert 'secret model text' not in str(worker._private_target_failure(error,'video_coverage_resolution_invalid',doc))


@pytest.mark.parametrize('bad',[
    {'first_pass':[{'action':'untrusted-action','frame_refs':['frame-001']}], 'independent_review':[]},
    {'first_pass':[{'action':'navigateBack','frame_refs':['frame-999']}], 'independent_review':[]},
    {'first_pass':[{'action':'navigateBack','frame_refs':['frame-001'],'value':'secret'}], 'independent_review':[]},
])
def test_coverage_failure_rejects_untrusted_candidate_payload(bad):
    error=ValueError('video_coverage_resolution_invalid');error.coverage_frame_refs={'frame-001'};error.coverage_candidates=bad
    doc={'media':{'frames':[{'frame_id':'frame-001'}]}}
    assert worker._private_target_failure(error,'video_coverage_resolution_invalid',doc)=={'coverage_frame_refs':['frame-001']}


def test_boundary_failure_keeps_only_bounded_whitelisted_overlap():
    error=ValueError('video_reference_invalid');error.reference_reason='boundary_overlap'
    error.boundary_failure_evidence={'events':[{'action':'navigateBack','frame_refs':['frame-001','frame-end'],
                                               'overlap_frame_refs':['frame-end']}]}
    doc={'media':{'frames':[{'frame_id':'frame-001'},{'frame_id':'frame-end'}]}}
    result=worker._private_target_failure(error,'video_reference_invalid',doc)
    assert result=={'boundary_failure_evidence':error.boundary_failure_evidence}
    worker._log_failure('uvs_'+'a'*32,'analysis',error,'video_reference_invalid')


@pytest.mark.parametrize('mutation',['reason','wrong_code','unknown_frame','extra_text','unknown_action','empty_overlap','wrong_overlap','too_many','duplicate'])
def test_boundary_failure_rejects_other_errors_or_forged_evidence(mutation):
    error=ValueError('video_reference_invalid');error.reference_reason='boundary_overlap'
    error.boundary_failure_evidence={'events':[{'action':'aiTap','frame_refs':['frame-001'], 'overlap_frame_refs':['frame-001']}]}
    item=error.boundary_failure_evidence['events'][0]
    if mutation=='reason':error.reference_reason='outside_window'
    if mutation=='unknown_frame':item['frame_refs']=['frame-999']
    if mutation=='extra_text':item['observation']='secret marker'
    if mutation=='unknown_action':item['action']='exec'
    if mutation=='empty_overlap':item['overlap_frame_refs']=[]
    if mutation=='wrong_overlap':item['overlap_frame_refs']=['frame-end']
    if mutation=='too_many':error.boundary_failure_evidence['events']=[dict(item) for _ in range(17)]
    if mutation=='duplicate':item['frame_refs']=['frame-001','frame-001']
    doc={'media':{'frames':[{'frame_id':'frame-001'},{'frame_id':'frame-end'}]}}
    code='video_model_invalid' if mutation=='wrong_code' else 'video_reference_invalid'
    assert worker._private_target_failure(error,code,doc)=={}
