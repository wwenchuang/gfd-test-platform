"""Model contract failures must use the existing per-window correction budget."""
import copy
import json
import pytest
from task_server.services import ai_skill_service as ai, ui_video_windows as windows


def setup(monkeypatch, tmp_path, responses):
    fs=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    from task_server.services import ui_video_touch_targets as targets
    monkeypatch.setattr(targets,'probe',lambda *args:[])
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    calls=[]
    def gateway(*args,**kwargs):
        calls.append(copy.deepcopy(kwargs['payload']))
        item=responses[min(len(calls)-1,len(responses)-1)]
        if isinstance(item,Exception):raise item
        return json.dumps(item)
    monkeypatch.setattr(ai,'ai_gateway_skill_content',gateway)
    return fs,calls


def valid():
    return {'events':[{'action':'aiTap','target':'订单入口','value':'','frame_refs':['frame-001','frame-002'],'observation':'订单列表可见'}], 'uncertainties':[], 'multiple_scenarios':False}


def test_missing_required_field_gets_one_evidence_preserving_correction(monkeypatch,tmp_path):
    fs,calls=setup(monkeypatch,tmp_path,[{'private':'secret'},valid()])
    result,_=windows.observe_windows(fs,200,tmp_path,lambda:False)
    assert result['events'][0]['target']=='订单入口'
    assert len(calls)==2
    assert calls[0]['frames']==calls[1]['frames']
    assert 'events' in calls[1]['validation_feedback']
    assert 'secret' not in str(calls[1])


def test_invalid_schema_stops_after_two_calls_with_safe_diagnostics(monkeypatch,tmp_path,caplog):
    fs,calls=setup(monkeypatch,tmp_path,[{'private':'secret'}])
    with pytest.raises(ValueError) as caught:
        windows.observe_windows(fs,200,tmp_path,lambda:False)
    assert len(calls)==2
    from task_server.workers.ui_video_worker import _log_failure,_failure_code
    _log_failure('uvs_'+'a'*32,'analysis',caught.value,_failure_code(caught.value))
    detail=json.loads(caplog.records[-1].getMessage().split(' ',1)[1])
    assert detail['category']=='model_schema'
    assert detail['schema_path']=='$.events'
    assert 'secret' not in caplog.text


@pytest.mark.parametrize('error',[RuntimeError('provider unavailable'),TimeoutError('timeout')])
def test_provider_failure_is_not_retried_as_bad_model_schema(monkeypatch,tmp_path,error):
    fs,calls=setup(monkeypatch,tmp_path,[error])
    with pytest.raises((RuntimeError,TimeoutError)):
        windows.observe_windows(fs,200,tmp_path,lambda:False)
    assert len(calls)==1


def test_non_video_keeps_gateway_failure_contract(monkeypatch):
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    monkeypatch.setattr(ai,'AI_SKILLS_STRICT_MODEL',True)
    monkeypatch.setattr(ai,'render_ai_skill_prompt',lambda *args,**kwargs:'prompt')
    monkeypatch.setattr(ai,'load_ai_skill_schema',lambda *args:{'type':'object','required':['answer']})
    monkeypatch.setattr(ai,'ai_gateway_skill_content',lambda *args,**kwargs:'{}')
    with pytest.raises(RuntimeError) as caught:
        ai.run_ai_skill('other',{})
    assert isinstance(caught.value.__cause__,ai.SkillSchemaError)
