from task_server.services.ui_video_windows import temporal_context_frames


def test_dense_window_has_before_and_after_context():
    frames=[{'frame_id':str(i),'timestamp_ms':i*100} for i in range(100)]
    window=frames[30:35]
    selected=temporal_context_frames(window,frames)
    times=[f['timestamp_ms'] for f in selected]
    assert any(t<3000 for t in times)
    assert any(t>=6000 for t in times)
    assert not set(f['frame_id'] for f in selected)&set(f['frame_id'] for f in window)
    assert len(selected)<=8
    assert times==sorted(set(times))


def test_context_does_not_relabel_or_mutate_frames():
    frames=[{'frame_id':str(i),'timestamp_ms':i*1000} for i in range(5)]
    assert temporal_context_frames(frames,frames)==[]
    selected=temporal_context_frames(frames[:1],frames)
    assert all(item in frames[1:] for item in selected)
    assert frames[0]=={'frame_id':'0','timestamp_ms':0}


def test_reference_context_cannot_be_used_as_action_evidence():
    import pytest
    from task_server.services.ui_video_analysis import _bind_whole_evidence
    frames=[{'frame_id':str(i),'timestamp_ms':i*100} for i in range(20)]
    window=frames[10:12]
    ref=temporal_context_frames(window,frames)[0]['frame_id']
    with pytest.raises(ValueError,match='video_reference_invalid'):
        _bind_whole_evidence({'events':[{'action':'aiTap','target':'入口','value':'','frame_refs':[ref],'observation':'跳转'}]},window)


def test_window_transport_keeps_local_evidence_separate_and_bounded(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ai_skill_service as ai
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(100)]
    (tmp_path/'frames').mkdir()
    for f in frames:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kwargs):
        local={f['frame_id'] for f in payload['frames']}
        context=set(payload['context_frame_ids'])
        images=[f['frame_id'] for f in payload['image_frames']]
        assert not local & context
        assert set(images)==local|context
        assert len(images)==len(set(images))<=54
        assert [a['name'] for a in kwargs['image_assets']]==images
        assert set(payload['temporal_context_frame_ids'])<=context
        assert all('frame_refs' not in e for e in payload.get('prior_events',[]))
        calls.append(payload)
        return {'events':[{'action':'aiAssert','target':'页面可见','value':'',
            'frame_refs':[payload['frames'][0]['frame_id']],'observation':'页面静止'}],
            'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    # Exercise both ordinary and recovery review transports, including prior context.
    for review in (None,[]):
        windows.observe_windows(frames,10000,tmp_path,lambda:False,review_events=review)
    assert len(calls)==8
    assert any(set(p['temporal_context_frame_ids']) for p in calls)
