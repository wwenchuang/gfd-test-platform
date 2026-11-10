"""Safe opt-in timing for ordinary video calls without touching their payload."""
import json
import logging
import threading

import pytest

from task_server.services import ui_video_ordinary_timing as timing

SID='uvs_'+'a'*32


def rows(caplog):
    return [json.loads(record.message.removeprefix('ui_video_timing ')) for record in caplog.records if record.message.startswith('ui_video_timing ')]


def test_disabled_is_transparent(monkeypatch,caplog):
    monkeypatch.delenv('TASK_UI_VIDEO_TIMING_LOG',raising=False)
    with timing.session(SID,1):
        timing.window('observe',1,15)
        assert timing.measure('observe_model',lambda:'secret')=='secret'
    assert not rows(caplog)


def test_scoped_log_fields_and_sticky_exception_do_not_leak(monkeypatch,caplog):
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1')
    caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    class SecretError(RuntimeError):pass
    with timing.session(SID,7):
        timing.window('review',2,15)
        assert timing.measure('touch_model',lambda:'SECRET_MODEL_TEXT',detail=3,attempt=1)=='SECRET_MODEL_TEXT'
        with pytest.raises(SecretError,match='SECRET_INPUT'):
            timing.measure('observe_model',lambda:(_ for _ in ()).throw(SecretError('SECRET_INPUT')),attempt=2)
    result=rows(caplog);assert len(result)==4
    for r in result:
        assert r['source_id']==SID and r['version']==7 and r['window']==2 and r['phase']=='review'
        assert set(r)<= {'source_id','version','attempt_id','phase','window','windows','kind','detail','call_attempt','event','elapsed_ms','outcome','call_id'}
        assert len(r['attempt_id'])==32 and len(r['call_id'])==32
    assert 'SECRET' not in caplog.text
    assert result[0]['call_id']==result[1]['call_id'] and result[2]['call_id']==result[3]['call_id']
    assert result[0]['call_id']!=result[2]['call_id']
    assert [r.get('outcome') for r in result]==[None,'returned',None,'error']


def test_concurrent_and_duplicate_source_attempts_are_isolated(monkeypatch,caplog):
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    barrier=threading.Barrier(2)
    def run(phase):
        with timing.session(SID,4):
            timing.window(phase,1,2)
            timing.measure('observe_model',lambda:barrier.wait(timeout=5))
    threads=[threading.Thread(target=run,args=(phase,)) for phase in ('observe','review')]
    for t in threads:t.start()
    for t in threads:t.join(5)
    records=rows(caplog);assert len(records)==4
    assert len({r['attempt_id'] for r in records})==2
    assert len({r['call_id'] for r in records})==2
    for attempt_id in {r['attempt_id'] for r in records}:
        group=[r for r in records if r['attempt_id']==attempt_id]
        assert len({r['phase'] for r in group})==1 and len({r['call_id'] for r in group})==1


def test_logging_failure_never_changes_result_or_exception(monkeypatch):
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1')
    monkeypatch.setattr(timing._LOG,'warning',lambda *a,**k:(_ for _ in ()).throw(OSError('log failure')))
    with timing.session(SID,1):
        timing.window('observe',1,1)
        assert timing.measure('observe_model',lambda:42)==42
        with pytest.raises(ValueError,match='original'):
            timing.measure('observe_model',lambda:(_ for _ in ()).throw(ValueError('original')))


def test_invalid_metadata_or_kind_never_enters_log(monkeypatch,caplog):
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    with timing.session(SID+'\nSECRET',True):
        timing.window('observe',1,1)
        assert timing.measure('observe_model',lambda:5)==5
    with timing.session(SID,1):
        timing.window('SECRET',1,1)
        assert timing.measure('SECRET',lambda:6)==6
    assert not rows(caplog)


def test_real_window_and_probe_log_calls_without_changing_requests(monkeypatch,tmp_path,caplog):
    import copy
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail,ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    touch={'frame_id':'frame-001-detail','source_frame_id':'frame-001','timestamp_ms':0}
    monkeypatch.setattr(windows,'load_assets',lambda fs,*args:[{'name':f['frame_id'],'base64':'IMAGE_SECRET','mime':'image/jpeg'} for f in fs])
    def details(fs,root,assets,cancelled,local_ids=None):
        assets.append({'name':touch['frame_id'],'base64':'CROP_SECRET','mime':'image/jpeg'})
        return [touch]
    monkeypatch.setattr(detail,'append_details',details)
    requests=[]
    def model(skill,payload,**kw):
        requests.append((skill,copy.deepcopy(payload),copy.deepcopy(kw)))
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    monkeypatch.delenv('TASK_UI_VIDEO_TIMING_LOG',raising=False)
    with timing.session(SID,2):baseline=windows.observe_windows(frames,200,tmp_path,lambda:False)
    before=copy.deepcopy(requests);requests.clear()
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    with timing.session(SID,2):actual=windows.observe_windows(frames,200,tmp_path,lambda:False)
    assert actual==baseline and requests==before and len(requests)==2
    ends=[r for r in rows(caplog) if r['event']=='end']
    assert [r['kind'] for r in ends]==['assets','detail','touch_model','observe_model','window_total']
    assert all(r['phase']=='observe' and r['window']==1 for r in ends)
    assert ends[2]['detail']==1 and ends[2]['call_attempt']==1
    assert 'IMAGE_SECRET' not in caplog.text and 'CROP_SECRET' not in caplog.text


def test_timeout_and_cancel_record_fixed_outcome_without_exception_text(monkeypatch,caplog):
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    with timing.session(SID,2):
        timing.window('observe',1,1)
        for exc in (TimeoutError('TOP_SECRET'),ValueError('video_cancelled')):
            with pytest.raises(type(exc)):
                timing.measure('observe_model',lambda:(_ for _ in ()).throw(exc))
    assert [r['outcome'] for r in rows(caplog) if r['event']=='end']==['timeout_observed','cancelled']
    assert 'TOP_SECRET' not in caplog.text


def test_probe_retry_and_review_window_are_distinct(monkeypatch,tmp_path,caplog):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail,ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    touch={'frame_id':'frame-001-detail','source_frame_id':'frame-001','timestamp_ms':0}
    monkeypatch.setattr(windows,'load_assets',lambda fs,*args:[{'name':f['frame_id']} for f in fs])
    monkeypatch.setattr(windows,'context_frames',lambda *args:[])
    def details(fs,root,assets,cancelled,local_ids=None):assets.append({'name':touch['frame_id']});return [touch]
    monkeypatch.setattr(detail,'append_details',details)
    calls=[]
    def model(skill,payload,**kw):
        calls.append(kw.get('version','observe'))
        if len(calls)==1:raise ai.TouchTargetProviderTimeoutError('SECRET')
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    with timing.session(SID,3):windows.observe_windows(frames,200,tmp_path,lambda:False,review_events=[])
    records=[r for r in rows(caplog) if r['event']=='end' and r['kind'].endswith('_model')]
    assert [(r['phase'],r['kind'],r['detail'],r['call_attempt'],r['outcome']) for r in records]==[
        ('review','touch_model',1,1,'timeout_observed'),('review','touch_model',1,2,'returned'),('review','observe_model',0,1,'returned')]
    assert calls==['touch-target','touch-target','observe'] and 'SECRET' not in caplog.text


def test_ordinary_worker_establishes_session_at_actual_analysis_boundary(monkeypatch,tmp_path,caplog):
    import copy,io
    from task_server import identity
    from task_server.services import ui_video_source_service as source,ui_video_analysis as analysis,ui_video_business_inputs as inputs,ui_case_draft_service as drafts
    from task_server.workers import ui_video_worker as worker
    app='com.example.app';actor={'kind':'user','user_id':'u','username':'u','display_name':'U'}
    profile={**actor,'status':'active','permissions':['ui.edit','ui.view'],'scope':{'ui_apps':[app]}}
    store=source.VideoStore(tmp_path/'videos');doc=store.upload(profile,app,'a.mp4',io.BytesIO(b'a'),1,actor=actor)
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');monkeypatch.delenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',raising=False)
    monkeypatch.setattr(identity,'get_access_profile',lambda _:copy.deepcopy(profile))
    monkeypatch.setattr(drafts,'_application',lambda *a:None);monkeypatch.setattr(drafts,'_top3_examples',lambda *a:[])
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'frames':[{'frame_id':'frame-001','timestamp_ms':0}],'duration_ms':100,'black':False,'static':False})
    def run(document,root,cancelled,progress):
        timing.window('observe',1,1);timing.measure('observe_model',lambda:None)
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(analysis,'analyze_whole_frames',run)
    monkeypatch.setattr(inputs,'recover_business_inputs',lambda result,*a:result)
    caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    worker.analyze_video_source(store,doc['source_id'])
    records=rows(caplog);assert len(records)==2
    assert all(r['source_id']==doc['source_id'] and r['version']==1 for r in records)
    assert store.get(doc['source_id'],'u',app)['status']=='review_needed'


@pytest.mark.parametrize('mode',['disabled','bad_window','bad_kind','invalid_source'])
def test_inactive_instrumentation_never_repeats_failing_call(monkeypatch,mode):
    if mode=='disabled':monkeypatch.delenv('TASK_UI_VIDEO_TIMING_LOG',raising=False)
    else:monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1')
    calls=[];failure=ValueError('user failure')
    def fail():calls.append(1);raise failure
    with timing.session('bad' if mode=='invalid_source' else SID,1):
        timing.window('invalid' if mode=='bad_window' else 'observe',1,1)
        with pytest.raises(ValueError) as caught:
            timing.measure('invalid' if mode=='bad_kind' else 'observe_model',fail)
    assert caught.value is failure and calls==[1]


def test_window_total_closes_on_model_exception_without_retrying_call(monkeypatch,tmp_path,caplog):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail,ui_video_touch_targets as touch,ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    monkeypatch.setattr(windows,'load_assets',lambda *args:[])
    monkeypatch.setattr(detail,'append_details',lambda *args:[])
    monkeypatch.setattr(touch,'probe',lambda *args:[])
    calls=[];failure=RuntimeError('PRIVATE_ERROR')
    def fail(*args,**kwargs):calls.append(1);raise failure
    monkeypatch.setattr(ai,'run_ai_skill',fail)
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1');caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    with timing.session(SID,5):
        with pytest.raises(RuntimeError) as caught:windows.observe_windows(frames,200,tmp_path,lambda:False)
    assert caught.value is failure and calls==[1]
    finished=[r for r in rows(caplog) if r['event']=='end']
    assert [(r['kind'],r['outcome']) for r in finished[-2:]]==[('observe_model','error'),('window_total','error')]
    assert 'PRIVATE_ERROR' not in caplog.text


def test_main_observation_timeout_retry_has_separate_redacted_timing(monkeypatch,tmp_path,caplog):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as detail,ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    monkeypatch.setattr(windows,'load_assets',lambda fs,*args:[{'name':f['frame_id']} for f in fs])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    calls=[]
    def model(skill,payload,**kw):
        calls.append(1)
        if len(calls)==1:raise ai.VideoObservationProviderTimeoutError('SECRET')
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    monkeypatch.setenv('TASK_UI_VIDEO_TIMING_LOG','1')
    caplog.set_level(logging.WARNING,logger='task_server.services.ui_video_ordinary_timing')
    with timing.session(SID,3):windows.observe_windows(frames,200,tmp_path,lambda:False)
    records=[r for r in rows(caplog) if r['event']=='end' and r['kind']=='observe_model']
    assert len(calls)==2 and [r['outcome'] for r in records]==['timeout_observed','returned']
    assert records[0]['call_id']!=records[1]['call_id']
    assert 'SECRET' not in caplog.text
