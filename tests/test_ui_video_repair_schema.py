import copy
import pytest
from task_server.services import ai_skill_service as ai, ui_video_reconcile as rec


def setup_case(tmp_path):
    frames=[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}]
    event={'action':'aiTap','target':'打开详情','value':'','frame_refs':['f1','f2'],'start_ms':100,'end_ms':200,'observation':'点击后详情可见','uncertainties':[],'evidence_kind':'inferred'}
    result={'events':[{k:v for k,v in event.items() if k not in ('start_ms','end_ms','evidence_kind')}],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    (tmp_path/'frames').mkdir()
    for f in frames:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    return frames,event,result


def invoke(tmp_path,frames,event,cancelled=lambda:False,limit=2):
    return rec._repair_window([event],{'events':[copy.deepcopy(event)],'uncertainties':[],'multiple_scenarios':False},frames,300,tmp_path,cancelled,[],{'f1','f2'},attempt_limit=limit)


def test_schema_correction_reuses_pixels_and_existing_budget(monkeypatch,tmp_path):
    frames,event,result=setup_case(tmp_path);calls=[]
    def model(name,payload,**kwargs):
        calls.append((copy.deepcopy(payload),copy.deepcopy(kwargs)))
        if len(calls)==1:raise ai.SkillSchemaError('$.uncertainties','为必填字段')
        return result
    monkeypatch.setattr(ai,'run_ai_skill',model)
    out=invoke(tmp_path,frames,event)
    assert len(calls)==2
    assert calls[0][1]['image_assets']==calls[1][1]['image_assets']
    assert '$.uncertainties' in calls[1][0]['validation_feedback']
    assert out['reconciliation_audit']['model_attempts']==2


@pytest.mark.parametrize('limit',[1,2])
def test_repeated_schema_failure_stops_at_original_budget(monkeypatch,tmp_path,limit):
    frames,event,_=setup_case(tmp_path);calls=[]
    def model(*args,**kwargs):
        calls.append(1);raise ai.SkillSchemaError('$.events','为必填字段')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ai.SkillSchemaError):invoke(tmp_path,frames,event,limit=limit)
    assert len(calls)==limit


def test_service_failure_is_not_schema_retry(monkeypatch,tmp_path):
    frames,event,_=setup_case(tmp_path);calls=[]
    def model(*args,**kwargs):
        calls.append(1);raise RuntimeError('provider timeout')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(RuntimeError):invoke(tmp_path,frames,event)
    assert len(calls)==1


def test_cancellation_after_schema_failure_wins(monkeypatch,tmp_path):
    frames,event,_=setup_case(tmp_path);calls=[]
    def model(*args,**kwargs):
        calls.append(1);raise ai.SkillSchemaError('$.events','为必填字段')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_cancelled'):invoke(tmp_path,frames,event,cancelled=lambda:bool(calls))
    assert len(calls)==1
