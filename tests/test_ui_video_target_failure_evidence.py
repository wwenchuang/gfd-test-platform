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
