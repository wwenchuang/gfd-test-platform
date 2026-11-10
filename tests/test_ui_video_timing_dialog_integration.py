"""The opt-in timing overlay must not alter controlled dialog retry."""
import copy,json,logging
from task_server.services import ui_video_ordinary_timing as timing
from test_ui_video_dialog_target_v7 import row,QUALIFIED
from test_ui_video_dialog_zero_retry import run


def test_timing_on_and_off_preserve_dialog_reobservation_and_gate(monkeypatch,tmp_path,caplog):
    prior=row(uncertainties=['首答事件问题']);new=row();new['observation']='与首答描述不同'
    outputs=[];payloads=[]
    for enabled in (False,True):
        if enabled:monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1')
        else:monkeypatch.delenv('TASK_UI_VIDEO_TIMING_LOG',raising=False)
        with timing.session('uvs_'+'a'*32,10):
            result,calls=run(monkeypatch,tmp_path,[row('关闭',['frame-118']),new],first=[prior],questions=['首答根问题'])
        assert not isinstance(result,ValueError) and len(calls)==2
        outputs.append(copy.deepcopy(result));payloads.append(copy.deepcopy(calls))
    assert outputs[0]==outputs[1] and payloads[0]==payloads[1]
    source=outputs[1][0]
    assert [e['target'] for e in source['events']]==['关闭',QUALIFIED]
    assert '首答事件问题' in source['events'][1]['uncertainties']
    assert any('frame-118' in q and '核对' in q for q in source['uncertainties'])
    assert any('两次画面描述不同' in q for q in source['uncertainties'])
    assert source['multiple_scenarios'] is True
    records=[json.loads(r.message.removeprefix('ui_video_timing ')) for r in caplog.records if r.message.startswith('ui_video_timing ')]
    model=[r for r in records if r['event']=='end' and r['kind']=='observe_model']
    total=[r for r in records if r['event']=='end' and r['kind']=='window_total']
    assert [r['call_attempt'] for r in model]==[1,2] and len(total)==1
    assert '首答事件问题' not in caplog.text and QUALIFIED not in caplog.text
