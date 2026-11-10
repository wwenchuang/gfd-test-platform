"""Only a confirmed provider timeout may be retried, with the same evidence."""
import copy
import io
import json
import urllib.error
from email.message import Message

import pytest

from task_server.services import ai_skill_service as ai, ui_video_windows as windows
from task_server.services import ui_video_touch_detail as detail, ui_video_touch_targets as targets


def http_error(status=500, error='Request timed out.', *, body=None, mime='application/json', raw=None):
    headers=Message(); headers['Content-Type']=mime
    return urllib.error.HTTPError('http://gateway/ai/skill', status, 'error', headers,
                                  io.BytesIO(raw if raw is not None else json.dumps({'success':False,'error':error} if body is None else body).encode()))


class Response(io.BytesIO):
    status=200
    headers=Message()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def setup(monkeypatch,tmp_path,outcomes,cancelled=None):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,5)]
    (tmp_path/'frames').mkdir()
    for f in frames:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    monkeypatch.setattr(detail,'append_details',lambda *a,**kw:[])
    monkeypatch.setattr(targets,'probe',lambda *a,**kw:[])
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    calls=[]
    def urlopen(req,**kw):
        calls.append(json.loads(req.data))
        result=outcomes[min(len(calls)-1,len(outcomes)-1)]
        if callable(result): result=result()
        if isinstance(result,Exception):raise result
        return Response(json.dumps({'success':True,'content':json.dumps(result)}).encode())
    monkeypatch.setattr(ai.urllib.request,'urlopen',urlopen)
    return frames,calls


def valid():return {'events':[],'uncertainties':[],'multiple_scenarios':False}


@pytest.mark.parametrize('review',[False,True])
def test_confirmed_provider_timeout_retries_identical_window(monkeypatch,tmp_path,review):
    frames,calls=setup(monkeypatch,tmp_path,[http_error(),valid()])
    result,_=windows.observe_windows(frames,500,tmp_path,lambda:False,review_events=[] if review else None)
    assert len(calls)==2
    assert calls[0]==calls[1]
    assert result['events']==[]


def test_one_retry_shared_across_windows_in_a_phase(monkeypatch,tmp_path):
    frames,calls=setup(monkeypatch,tmp_path,[http_error(),valid(),http_error()])
    monkeypatch.setattr(windows,'observation_windows',lambda f:[f[:2],f[2:]])
    monkeypatch.setattr(windows,'temporal_context_frames',lambda *a:[])
    with pytest.raises(RuntimeError):windows.observe_windows(frames,500,tmp_path,lambda:False)
    assert len(calls)==3
    assert calls[0]==calls[1]


def test_format_retry_timeout_does_not_consume_format_correction(monkeypatch,tmp_path):
    frames,calls=setup(monkeypatch,tmp_path,[{'private':'sensitive'},http_error(),valid()])
    result,_=windows.observe_windows(frames,500,tmp_path,lambda:False)
    assert len(calls)==3
    assert calls[1]==calls[2]
    assert 'validation_feedback' in calls[1]['payload']
    assert 'sensitive' not in str(calls[1])
    assert result['events']==[]


def test_cancelled_after_provider_timeout_does_not_retry(monkeypatch,tmp_path):
    stop=[False]
    def fail():
        stop[0]=True
        return http_error()
    frames,calls=setup(monkeypatch,tmp_path,[fail,valid()])
    with pytest.raises(ValueError,match='video_cancelled'):
        windows.observe_windows(frames,500,tmp_path,lambda:stop[0])
    assert len(calls)==1


@pytest.mark.parametrize('failure',[http_error(502),http_error(500,'request timed out.'),
    http_error(500,'Request timed out. extra'),http_error(mime='text/plain'),
    http_error(body={'success':False,'message':'Request timed out.'}),
    http_error(body={'success':False,'error':'Request timed out.','message':'other'}),
    http_error(raw=b'{"success":true,"success":false,"error":"Request timed out."}'),
    http_error(raw=b'{"success":false,"error":"other","error":"Request timed out."}'),
    http_error(body={'error':'Request timed out.'}),
    http_error(body={'success':True,'error':'Request timed out.'}),TimeoutError('local deadline')])
def test_other_gateway_failures_and_local_timeout_are_not_retried(monkeypatch,tmp_path,failure):
    frames,calls=setup(monkeypatch,tmp_path,[failure,valid()])
    with pytest.raises((RuntimeError,TimeoutError)):windows.observe_windows(frames,500,tmp_path,lambda:False)
    assert len(calls)==1


def test_previous_window_events_survive_retry(monkeypatch,tmp_path):
    def tap(frame,target):
        return {'events':[{'action':'aiTap','target':target,'value':'','frame_refs':[frame],
                           'observation':'页面显示完成','uncertainties':[]}],
                'uncertainties':[],'multiple_scenarios':False}
    frames,calls=setup(monkeypatch,tmp_path,[tap('frame-001','入口'),http_error(),tap('frame-003','确认')])
    monkeypatch.setattr(windows,'observation_windows',lambda f:[f[:2],f[2:]])
    monkeypatch.setattr(windows,'temporal_context_frames',lambda *a:[])
    result,_=windows.observe_windows(frames,500,tmp_path,lambda:False)
    assert [e['target'] for e in result['events']]==['入口','确认']
    assert calls[1]==calls[2]
    assert calls[1]['payload']['prior_events'][0]['target']=='入口'


def test_probe_budget_is_independent_of_main_observation(monkeypatch,tmp_path):
    frames,calls=setup(monkeypatch,tmp_path,[http_error(),valid()])
    seen=[]
    def used_probe(*args):
        budget=args[5]; budget['used']=True; seen.append(budget); return []
    monkeypatch.setattr(targets,'probe',used_probe)
    result,_=windows.observe_windows(frames,500,tmp_path,lambda:False)
    assert seen[0]['used'] is True
    assert len(calls)==2 and calls[0]==calls[1]
    assert result['events']==[]


def test_local_timeout_never_retries_with_background_call_still_alive(monkeypatch,tmp_path):
    import threading
    frames,calls=setup(monkeypatch,tmp_path,[valid()])
    unblock=threading.Event(); done=threading.Event(); started=[]
    worker=threading.Thread(target=lambda:(unblock.wait(),done.set()),daemon=True)
    def local_timeout(*args,**kwargs):
        started.append(1); worker.start()
        raise TimeoutError('local hard timeout')
    monkeypatch.setattr(ai,'run_ai_skill',local_timeout)
    try:
        with pytest.raises(TimeoutError,match='local hard timeout'):
            windows.observe_windows(frames,500,tmp_path,lambda:False)
        assert len(started)==1 and worker.is_alive()
    finally:
        unblock.set()
        assert done.wait(1)


def test_observation_and_review_share_one_bounded_retry(monkeypatch,tmp_path):
    frames,calls=setup(monkeypatch,tmp_path,[lambda:http_error(),valid(),lambda:http_error(),valid()])
    budget={'used':False}
    first,_=windows.observe_windows(frames,500,tmp_path,lambda:False,
                                    observation_timeout_retry_budget=budget)
    with pytest.raises(RuntimeError):
        windows.observe_windows(frames,500,tmp_path,lambda:False,review_events=[],
                                observation_timeout_retry_budget=budget)
    assert len(calls)==3
    assert calls[0]==calls[1] and first['events']==[]


def test_repeated_timeout_stops_after_one_retry(monkeypatch,tmp_path):
    frames,calls=setup(monkeypatch,tmp_path,[lambda:http_error(),lambda:http_error(),valid()])
    with pytest.raises(RuntimeError):windows.observe_windows(frames,500,tmp_path,lambda:False)
    assert len(calls)==2 and calls[0]==calls[1]


def test_full_analysis_shares_budget_across_phases_and_isolates_parallel_jobs(monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from task_server.services import ui_video_analysis as analysis, ui_video_reconcile as reconcile
    barrier=threading.Barrier(2)
    budgets={}
    lock=threading.Lock()
    class Done(Exception):pass
    def observe(frames,duration,root,cancelled,progress,review_events,prior_secrets,budget):
        label=frames[0]['frame_id']
        with lock:budgets[label]=budget
        barrier.wait(timeout=5)
        if label=='first':budget['used']=True
        return {'events':[{'target':label}],'uncertainties':[],'multiple_scenarios':False},[]
    def review(events,frames,duration,root,cancelled,secrets,progress,budget):
        label=events[0]['target']
        assert budget is budgets[label]
        assert budget['used']==(label=='first')
        raise Done()
    monkeypatch.setattr(windows,'observe_windows',observe)
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:True)
    monkeypatch.setattr(reconcile,'reconcile',review)
    def run(label):
        doc={'media':{'frames':[{'frame_id':label},{'frame_id':label+'2'}],'duration_ms':500}}
        with pytest.raises(Done):analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run,['first','second']))
    assert budgets['first'] is not budgets['second']


def test_real_reconcile_forwards_the_same_budget_to_review(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as reconcile
    class Stop(Exception):pass
    budget={'used':True}; seen=[]
    def capture(*args,**kwargs):
        seen.append(kwargs['observation_timeout_retry_budget'])
        raise Stop()
    monkeypatch.setattr(windows,'observe_windows',capture)
    with pytest.raises(Stop):
        reconcile.reconcile([],[],500,tmp_path,lambda:False,[],None,budget)
    assert seen==[budget] and seen[0] is budget


def test_timeout_during_dialog_correction_retains_first_answer_evidence_and_questions(monkeypatch,tmp_path):
    from test_ui_video_dialog_zero_retry import FRAMES,HINTS
    from test_ui_video_dialog_target_v7 import row,QUALIFIED
    original=row(uncertainties=['首答事件问题'])
    first={'events':[original],'uncertainties':['首答根问题'],'multiple_scenarios':True}
    corrected={'events':[row('关闭',['frame-118']),row()],'uncertainties':[],'multiple_scenarios':False}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':'YQ=='} for f in FRAMES]
    monkeypatch.setattr(windows,'load_assets',lambda *a:copy.deepcopy(assets))
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:copy.deepcopy(HINTS))
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    calls=[]
    def urlopen(req,**kw):
        calls.append(json.loads(req.data))
        if len(calls)==2:raise http_error()
        item=first if len(calls)==1 else corrected
        return Response(json.dumps({'success':True,'content':json.dumps(item)}).encode())
    monkeypatch.setattr(ai.urllib.request,'urlopen',urlopen)
    observed,_=windows.observe_windows(FRAMES,50000,tmp_path,lambda:False)
    assert len(calls)==3 and calls[1]==calls[2]
    assert [e['target'] for e in observed['events']]==['关闭',QUALIFIED]
    kept=observed['events'][1]
    assert kept['frame_refs']==original['frame_refs'] and kept['observation']==original['observation']
    assert '首答事件问题' in kept['uncertainties'] and '首答根问题' in observed['uncertainties']
    assert observed['multiple_scenarios'] is True
    assert any('frame-118' in q and '核对' in q for q in observed['uncertainties'])


def test_cancel_between_timeout_handling_and_retry_is_checked(monkeypatch,tmp_path):
    state={'timed_out':False,'checks_after_timeout':0}
    def cancelled():
        if state['timed_out']:
            state['checks_after_timeout']+=1
            return state['checks_after_timeout']>=2
        return False
    def fail():
        state['timed_out']=True
        return http_error()
    frames,calls=setup(monkeypatch,tmp_path,[fail,valid()])
    with pytest.raises(ValueError,match='video_cancelled'):
        windows.observe_windows(frames,500,tmp_path,cancelled)
    assert len(calls)==1


def test_full_analysis_does_not_retry_in_review_when_first_pass_used_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis, ui_video_reconcile as reconcile
    frames,calls=setup(monkeypatch,tmp_path,[lambda:http_error(),valid(),lambda:http_error(),valid()])
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:True)
    document={'media':{'frames':frames,'duration_ms':500}}
    with pytest.raises(RuntimeError):
        analysis.analyze_whole_frames(document,tmp_path,lambda:False)
    assert len(calls)==3 and calls[0]==calls[1]
    assert calls[2]['payload']['review_mode']=='full_flow_after_recovery'
