"""A task-local cache must preserve original visual evidence and safety gates."""
import os

import pytest
from PIL import Image

from task_server.services import ui_video_touch_detail as detail


def frames_at(root, colors=('red', 'blue', 'green', 'yellow')):
    (root / 'frames').mkdir(exist_ok=True)
    frames = []
    for index, color in enumerate(colors, 1):
        name = f'frame-{index:03d}'
        Image.new('RGB', (320, 700), color).save(root / 'frames' / f'{name}.jpg', format='PNG')
        frames.append({'frame_id': name, 'timestamp_ms': index * 100})
    return frames


def run(root, frames, *, cancelled=lambda: False, local_ids=None):
    assets = [{'name': item['frame_id'], 'base64': 'a'} for item in frames]
    ds = detail.append_details(frames, root, assets, cancelled, local_ids)
    return ds, assets


def test_identical_result_and_fresh_decision_on_cache_hit(tmp_path, monkeypatch):
    frames = frames_at(tmp_path, ('red', 'blue'))
    calls = []
    def detector(a, b):
        calls.append((a.getpixel((0, 0)), b.getpixel((0, 0))))
        return (160, 350)
    monkeypatch.setattr(detail, 'locate_ring', detector)
    expected = run(tmp_path, frames)
    assert len(calls) == 1
    with detail.pixel_cache_scope() as cache:
        actual1 = run(tmp_path, frames)
        actual2 = run(tmp_path, frames)
        assert actual1 == actual2 == expected
        assert run(tmp_path, frames, local_ids={'frame-001'})[0] == []
        assert len(calls) == 2
        assert len(cache) == 1
    assert len(cache) == 0


def test_changed_content_same_filename_and_mtime_is_not_stale(tmp_path, monkeypatch):
    frames = frames_at(tmp_path, ('red', 'blue'))
    target = tmp_path / 'frames' / 'frame-002.jpg'
    stamp = target.stat().st_mtime_ns
    calls = []
    def detector(a, b):
        calls.append(b.getpixel((0, 0)))
        return (160, 350)
    monkeypatch.setattr(detail, 'locate_ring', detector)
    with detail.pixel_cache_scope():
        first = run(tmp_path, frames)
        Image.new('RGB', (320, 700), 'green').save(target, format='PNG')
        os.utime(target, ns=(stamp, stamp))
        second = run(tmp_path, frames)
    assert calls == [(0, 0, 255), (0, 128, 0)]
    assert first[1][-1]['base64'] != second[1][-1]['base64']


def test_replacement_during_detection_cannot_change_decoded_evidence(tmp_path, monkeypatch):
    frames = frames_at(tmp_path, ('red', 'blue'))
    target = tmp_path / 'frames' / 'frame-002.jpg'
    pixels = []
    def detector(a, b):
        pixels.append(b.getpixel((0, 0)))
        if len(pixels) == 1:
            Image.new('RGB', (320, 700), 'green').save(target, format='PNG')
        return (160, 350)
    monkeypatch.setattr(detail, 'locate_ring', detector)
    with detail.pixel_cache_scope():
        first = run(tmp_path, frames)
        second = run(tmp_path, frames)
    assert pixels == [(0, 0, 255), (0, 128, 0)]
    assert first[1][-1]['base64'] != second[1][-1]['base64']


def test_corrupt_or_missing_source_does_not_use_old_cached_result(tmp_path, monkeypatch):
    frames = frames_at(tmp_path, ('red', 'blue'))
    calls = []
    monkeypatch.setattr(detail, 'locate_ring', lambda a,b: calls.append(True) or (160, 350))
    target = tmp_path / 'frames' / 'frame-002.jpg'
    with detail.pixel_cache_scope():
        assert run(tmp_path, frames)[0]
        target.write_bytes(b'broken')
        assert not run(tmp_path, frames)[0]
        target.unlink()
        assert not run(tmp_path, frames)[0]
    assert len(calls) == 1


def test_failed_detection_is_not_cached_and_cleanup_on_exception(tmp_path, monkeypatch):
    frames = frames_at(tmp_path, ('red', 'blue'))
    calls = []
    def detector(a,b):
        calls.append(True)
        raise ValueError('injected')
    monkeypatch.setattr(detail, 'locate_ring', detector)
    with pytest.raises(ValueError, match='injected'):
        with detail.pixel_cache_scope() as cache:
            try:
                run(tmp_path, frames)
            finally:
                assert len(cache) == 0
    assert len(cache) == 0
    with pytest.raises(ValueError, match='injected'):
        with detail.pixel_cache_scope(): run(tmp_path, frames)
    assert len(calls) == 2


def test_lru_evicts_and_recomputes_without_changing_result(tmp_path, monkeypatch):
    frames = frames_at(tmp_path)
    calls = []
    monkeypatch.setattr(detail, 'locate_ring', lambda a,b: calls.append(True) or (160, 350))
    with detail.pixel_cache_scope(max_entries=2) as cache:
        result = run(tmp_path, frames[:2])
        run(tmp_path, frames[1:3]); run(tmp_path, frames[2:])
        assert len(cache) == 2
        assert run(tmp_path, frames[:2]) == result
        assert len(calls) == 4
        assert len(cache) == 2


def test_cache_hit_still_checks_cancellation_and_scope_isolation(tmp_path, monkeypatch):
    frames = frames_at(tmp_path, ('red', 'blue'))
    calls=[]
    monkeypatch.setattr(detail, 'locate_ring', lambda a,b: calls.append(True) or (160, 350))
    with detail.pixel_cache_scope() as outer:
        run(tmp_path, frames)
        with pytest.raises(ValueError, match='video_cancelled'):
            run(tmp_path, frames, cancelled=lambda: True)
        with detail.pixel_cache_scope() as inner:
            run(tmp_path, frames)
            assert len(inner) == 1
        assert len(inner) == 0
        run(tmp_path, frames)
        assert len(outer) == 1
    assert len(outer) == 0
    assert len(calls) == 2


def test_same_analysis_reuses_pixels_but_calls_model_for_both_passes(tmp_path, monkeypatch):
    from task_server.services import ui_video_analysis as analysis, ui_video_reconcile as reconcile
    from task_server.services import ui_video_windows as windows, ui_video_touch_targets as touch
    from task_server.services import ui_video_delayed_response as delayed, ai_skill_service as ai
    frames = frames_at(tmp_path, ('red', 'blue'))
    detector_calls=[]; model_calls=[]; cache_instances=[]
    monkeypatch.setattr(detail, 'locate_ring', lambda a,b: detector_calls.append(True) or (160,350))
    monkeypatch.setattr(touch, 'probe', lambda *a, **kw: [])
    def model(skill,payload,**kwargs):
        cache_instances.append(detail._PIXEL_CACHE.get());model_calls.append(payload.get('review_mode'))
        return {'events':[{'action':'aiTap','target':'按钮','value':'','frame_refs':['frame-002'],
                           'observation':'点击按钮','uncertainties':[]}],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai, 'run_ai_skill', model)
    monkeypatch.setattr(reconcile, 'needs_reconciliation', lambda *a: True)
    def second_pass(events, frames, duration, root, cancelled, secrets, progress):
        reviewed,_ = windows.observe_windows(frames,duration,root,cancelled,progress,
                                               review_events=events,prior_secrets=secrets)
        return reviewed
    monkeypatch.setattr(reconcile, 'reconcile', second_pass)
    monkeypatch.setattr(delayed, 'review_pending_responses', lambda observed,*a: observed)
    monkeypatch.setattr(analysis, 'consolidate_observed_frames', lambda *a: a[3])
    source={'media':{'frames':frames,'duration_ms':300}}
    result=analysis.analyze_whole_frames(source,tmp_path,lambda:False)
    assert len(result['events']) == 1
    assert len(model_calls)==2 and model_calls == [None,'full_flow_after_recovery']
    assert len(detector_calls)==1
    assert cache_instances[0] is cache_instances[1] and cache_instances[0] is not None
    assert len(cache_instances[0])==0 and detail._PIXEL_CACHE.get() is None


def test_large_files_fall_back_to_existing_detection(tmp_path, monkeypatch):
    frames=frames_at(tmp_path,('red','blue'));calls=[]
    monkeypatch.setattr(detail, '_MAX_CACHED_IMAGE_BYTES', 1)
    monkeypatch.setattr(detail, 'locate_ring', lambda a,b: calls.append(True) or (160,350))
    baseline=run(tmp_path,frames)
    with detail.pixel_cache_scope() as cache:
        assert run(tmp_path,frames)==baseline
        assert run(tmp_path,frames)==baseline
        assert len(cache)==0
    assert len(calls)==3


def test_concurrent_analysis_scopes_do_not_share_cache(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    frames=frames_at(tmp_path,('red','blue'));calls=[];barrier=Barrier(2);lock=Lock()
    def detector(a,b):
        with lock:calls.append(True)
        return (160,350)
    monkeypatch.setattr(detail, 'locate_ring', detector)
    def worker():
        with detail.pixel_cache_scope() as cache:
            run(tmp_path,frames);barrier.wait(timeout=5);run(tmp_path,frames)
            return len(cache)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert results==[1,1]
    assert len(calls)==2
    assert detail._PIXEL_CACHE.get() is None


def test_oserror_from_detector_is_not_cached_or_promoted(tmp_path, monkeypatch):
    frames=frames_at(tmp_path,('red','blue'));calls=[]
    def detector(a,b):
        calls.append(True)
        if len(calls)==1:raise OSError('injected')
        return (160,350)
    monkeypatch.setattr(detail, 'locate_ring', detector)
    with detail.pixel_cache_scope() as cache:
        assert run(tmp_path,frames)[0] == []
        assert len(cache)==0
        assert len(run(tmp_path,frames)[0])==1
    assert len(calls)==2


def test_cache_limit_must_be_positive_integer():
    for invalid in (0, -1, True, 1.0, None):
        with pytest.raises(ValueError,match='video_pixel_cache_invalid'):
            with detail.pixel_cache_scope(invalid):pass
