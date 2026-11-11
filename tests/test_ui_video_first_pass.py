import io

import pytest

from test_ui_case_draft_service import A, B, APP, TARGET, env
from task_server.services import ui_case_draft_service as drafts
from task_server.services import ui_video_source_service as video
from task_server.services.operation_attribution import actor_context
from task_server.workers import ui_video_worker as worker


def observation(action, index):
    return {'action': action, 'target': '个人中心入口', 'value': '', 'start_ms': index, 'end_ms': index,
            'frame_refs': [f'frame-{index:03}'], 'observation': '页面显示个人中心入口',
            'evidence_kind': 'inferred', 'uncertainties': []}


def source(tmp_path, monkeypatch, events):
    store = video.VideoStore(tmp_path/'videos')
    monkeypatch.setattr(video, 'service_store', lambda: store)
    doc = store.upload(A, APP, 'clip.mp4', io.BytesIO(b'video'), 5, actor=A)
    media = {'duration_ms': 200, 'black': False, 'static': False,
             'frames': [{'frame_id': f'frame-{i:03}', 'timestamp_ms': i} for i in range(max(len(events),2))]}
    return store, doc['source_id'], media


def test_ordinary_finishes_with_first_pass_without_more_model_calls(env, tmp_path, monkeypatch):
    from task_server.services import ui_video_windows as windows, ui_video_reconcile as reconcile
    events = [observation('aiTap', 0), observation('aiAssert', 1)]
    store, identifier, media = source(tmp_path, monkeypatch, events)
    monkeypatch.setattr(worker, 'decode_video', lambda *a: media)
    def observe(frames, duration, root, cancelled, progress, *args):
        progress('observing',0,1,[])
        progress('planning',1,1,events)
        return {'events':events, 'uncertainties':['请核对归属'], 'multiple_scenarios':False}, []
    monkeypatch.setattr(windows, 'observe_windows', observe)
    monkeypatch.setattr(reconcile, 'needs_reconciliation', lambda *a: pytest.fail('second pass'))
    monkeypatch.setattr(drafts, '_top3_examples', lambda *a: pytest.fail('retrieved planning baselines'))
    worker.analyze_video_source(store, identifier)
    saved = video.public_source(store.get(identifier,A['user_id'],APP))
    assert saved['status']=='review_needed' and saved['analysis_mode']=='first_pass'
    assert saved['first_pass_events']==events and saved['first_pass_importable']
    assert saved['analysis_progress']=={'phase':'first_pass_complete','completed':1,'total':1}
    with actor_context(A):
        draft=video.import_video_draft(A,identifier,APP)
    assert [s['action'] for s in draft['steps']]==['aiTap']
    assert any('请核对归属' in q for q in draft['questions'])


@pytest.mark.parametrize('status', ['cancelled','failed','interrupted'])
def test_historical_complete_first_pass_only_imports_actions(env,tmp_path,monkeypatch,status):
    events=[observation('aiTap' if i<38 else 'aiAssert',i) for i in range(61)]
    events[-1]['uncertainties']=['末尾状态待核实']
    store,identifier,media=source(tmp_path,monkeypatch,events)
    doc=store.transition(identifier,{'queued'},status,analysis_mode=None,media=media,
                         first_pass_events=events,raw_events=[events[0]],error='历史原因')
    public=video.public_source(doc)
    assert public['first_pass_event_count']==61 and public['first_pass_importable']
    with actor_context(B),pytest.raises(KeyError):
        video.import_video_draft(B,identifier,APP)
    with actor_context(A):
        draft=video.import_video_draft(A,identifier,APP)
        assert len(draft['steps'])==38
        assert draft['source_snapshot']['first_pass_events']==events
        assert all(step['review_state']=='needs_review' for step in draft['steps'])
        assert any('末尾状态待核实' in question for question in draft['questions'])
        assert not drafts.validate_draft(A,draft['draft_id'],{'revision':draft['revision'],'target':TARGET})['ready']
        assert video.import_video_draft(A,identifier,APP)['draft_id']==draft['draft_id']
    after=store.get(identifier,A['user_id'],APP)
    assert after['status']==status and after['error']=='历史原因' and after['first_pass_events']==events


@pytest.mark.parametrize('case', ['partial','too_many','empty','multiple','invalid_ref'])
def test_first_pass_rejects_unusable_draft_but_keeps_evidence(env,tmp_path,monkeypatch,case):
    events=[observation('aiTap',i) for i in range(51 if case=='too_many' else 2)]
    store,identifier,media=source(tmp_path,monkeypatch,events)
    if case=='invalid_ref':events[-1]['frame_refs']=['invented']
    first=events if case not in {'partial','empty'} else []
    doc=store.transition(identifier,{'queued'},'failed',media=media,first_pass_events=first,raw_events=events,
                         multiple_scenarios=case=='multiple')
    public=video.public_source(doc)
    assert not public['first_pass_importable']
    assert public['first_pass_events']==first
    with actor_context(A),pytest.raises(ValueError):video.import_video_draft(A,identifier,APP)


def test_history_removal_of_cancelled_source_wins_before_import(env,tmp_path,monkeypatch):
    events=[observation('aiTap',0)]
    store,identifier,media=source(tmp_path,monkeypatch,events)
    store.transition(identifier,{'queued'},'cancelled',media=media,first_pass_events=events)
    transition=store.transition
    def remove_before_reservation(*args,**fields):
        if fields.get('retained'):store.remove_history([identifier],A['user_id'],APP)
        return transition(*args,**fields)
    monkeypatch.setattr(store,'transition',remove_before_reservation)
    with actor_context(A):
        with pytest.raises(ValueError):video.import_video_draft(A,identifier,APP)
        assert not drafts.list_drafts(A,APP)['items']


def test_first_pass_touch_decision_requires_matching_retained_origin(env,tmp_path,monkeypatch):
    import hashlib
    from task_server.services.ui_video_touch_review import read_verified_touch_frame
    events=[observation('aiTap',0)]
    store,identifier,media=source(tmp_path,monkeypatch,events)
    monkeypatch.setenv('TASK_UI_VIDEO_ROOT',str(store.root))
    data=b'original frame data'
    directory=store.root/identifier/'frames'
    directory.mkdir()
    (directory/'frame-001.jpg').write_bytes(data)
    ambiguity={'frame_id':'frame-001','frame_sha256':hashlib.sha256(data).hexdigest(),
               'reason':'no_match','phase':'observe','target':'入口','observation':'位置未知','questions':['请核对']}
    store.transition(identifier,{'queued'},'review_needed',media=media,first_pass_events=events,touch_ambiguities=[ambiguity])
    with actor_context(A):
        draft=video.import_video_draft(A,identifier,APP)
        assert read_verified_touch_frame(draft,0)==data
        reviewed=drafts.resolve_touch_ambiguity(A,draft['draft_id'],{'revision':1,'index':0,
            'frame_id':'frame-001','disposition':'not_action','step_id':''})
        assert reviewed['revision']==2
        store.transition(identifier,{'review_needed'},'review_needed',first_pass_events=[])
        with pytest.raises(ValueError):read_verified_touch_frame(reviewed,0)


def test_reviewed_first_pass_can_reach_the_ordinary_trial_gate(env,tmp_path,monkeypatch):
    from test_ui_case_draft_service import confirm
    from task_server.services.ui_case_draft_execution import _source
    events=[observation('aiTap',0),observation('aiAssert',1)]
    store,identifier,media=source(tmp_path,monkeypatch,events)
    store.transition(identifier,{'queued'},'review_needed',media=media,first_pass_events=events)
    with actor_context(A):
        draft=video.import_video_draft(A,identifier,APP)
        assert not drafts.validate_draft(A,draft['draft_id'],{'revision':1,'target':TARGET})['ready']
        body=confirm(draft)
        body['questions']=[]
        body['steps'][0]['expected']='个人中心显示账号入口'
        body['steps'].append({'action':'aiAssert','target':'个人中心显示账号入口','expected':'个人中心显示账号入口',
                              'value':'','review_state':'confirmed','source_ref':'text'})
        edited=drafts.update_draft(A,draft['draft_id'],body)
        gate=drafts.validate_draft(A,draft['draft_id'],{'revision':edited['revision'],'target':TARGET})
        assert gate['ready'], gate['blockers']
        assert _source(edited)['yaml_content']==edited['yaml_content']
