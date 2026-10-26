"""Conflict review must retain original-bound visual detail without new actions."""
import pytest
from task_server.services import ui_video_reconcile as rec
from task_server.services import ui_video_windows as windows
from task_server.services import ui_video_touch_detail as detail
from task_server.services import ai_skill_service as ai


@pytest.mark.parametrize('invalid_ref', [False, True])
def test_repair_transports_detail_but_only_original_frames_are_evidence(monkeypatch,tmp_path,invalid_ref):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)]
    event={'action':'aiTap','target':'控件','value':'','frame_refs':['f2','f3'],
           'start_ms':200,'end_ms':300,'observation':'点击后出现页面','uncertainties':[],'evidence_kind':'inferred'}
    monkeypatch.setattr(windows,'load_assets',lambda *a:[{'name':f['frame_id'],'base64':'img'} for f in frames])
    details=[];calls=[]
    def append(frames_,root,assets,cancelled,local_ids=None):
        assert local_ids=={'f1','f2','f3'}
        assert not cancelled()
        details.append(True);assets.append({'name':'f2-detail','base64':'crop'})
        return [{'frame_id':'f2-detail','source_frame_id':'f2','timestamp_ms':200}]
    monkeypatch.setattr(detail,'append_details',append)
    def model(skill,payload,**kw):
        calls.append(True)
        assert payload['detail_frames'][0]['source_frame_id']=='f2'
        assert [a['name'] for a in kw['image_assets']]==['f1','f2','f3','f2-detail']
        assert payload['frames']==frames
        out={k:event[k] for k in ('action','target','value','frame_refs','observation','uncertainties')}
        if invalid_ref:out['frame_refs']=['f2-detail']
        return {'events':[out],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    def run():return rec._repair_window([event],{'events':[event],'uncertainties':[],'multiple_scenarios':False},frames,400,tmp_path,lambda:False,[],{'f2','f3'})
    if invalid_ref:
        with pytest.raises(ValueError):run()
    else:
        result=run();assert result['events'][0]['frame_refs']==['f2','f3']
    assert len(details)==1
    assert len(calls)<=2
