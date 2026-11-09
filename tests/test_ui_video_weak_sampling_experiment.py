import pytest
import io

from task_server.services import ui_video_source_service as video
from test_ui_case_draft_service import env, A, APP


def test_weak_candidates_keep_original_frames_and_capture_short_change():
    times = [0, 100, 133, 250, 500]
    changes = [0, .05, .06, .1, 0]
    original = {0, 250, 500}
    result = video._add_weak_evidence(times, changes, original, 24)
    assert result == {0, 100, 133, 250, 500}
    assert original == {0, 250, 500}


def test_weak_candidates_over_budget_fail_instead_of_dropping_evidence():
    with pytest.raises(ValueError, match='video_weak_evidence_limit'):
        video._add_weak_evidence([0, 100, 133, 250, 500], [0, .05, .06, .1, 0], {0, 250, 500}, 1)


def test_weak_candidates_reject_invalid_time_and_honor_cancel():
    with pytest.raises(ValueError, match='video_frames_invalid'):
        video._add_weak_evidence([0, 100, 100], [0, .05, .06], {0}, 24)
    with pytest.raises(ValueError, match='video_cancelled'):
        video._add_weak_evidence([0, 100], [0, 0], {0}, 24, lambda: True)


def test_weak_scan_batches_cancellation_checks_and_still_stops():
    times = list(range(2049))
    calls = []
    def cancelled():
        calls.append(1)
        return False
    assert video._add_weak_evidence(times, [0] * len(times), {0}, 24, cancelled) == {0}
    assert len(calls) <= 12
    def stop_at_second_check():
        calls.append(1)
        return len(calls) == 2
    calls.clear()
    with pytest.raises(ValueError, match='video_cancelled'):
        video._add_weak_evidence(times, [0] * len(times), {0}, 24, stop_at_second_check)
    assert len(calls) == 2


def test_ordinary_frame_budget_does_not_expand_trajectory():
    from task_server.services.ui_video_trajectory import make_packets
    assert video.MAX_FRAMES == 360
    assert video.MAX_ORDINARY_FRAMES == 384
    frames = [{'frame_id': f'f{i}', 'timestamp_ms': i} for i in range(361)]
    with pytest.raises(ValueError):
        make_packets(frames, [])


def test_ordinary_extracted_byte_limit_has_actionable_error(tmp_path):
    frame = tmp_path / 'frame.jpg'
    with frame.open('wb') as stream:
        stream.truncate(video.MAX_EXTRACTED_FRAME_BYTES + 1)
    with pytest.raises(ValueError, match='video_frame_bytes_limit'):
        video._check_extracted_frame_bytes([frame], 'video_frame_bytes_limit')


@pytest.mark.parametrize('code,message', [
    ('video_weak_evidence_limit', '细微画面变化过多'),
    ('video_frame_bytes_limit', '抽取画面超过大小上限'),
])
def test_ordinary_worker_fails_before_model_when_evidence_budget_exceeded(env, tmp_path, monkeypatch, code, message):
    from task_server.workers import ui_video_worker as worker
    from task_server.services import ui_video_runtime as runtime
    from task_server.services import ui_video_analysis as analysis
    store = video.VideoStore(tmp_path / 'videos')
    doc = store.upload(A, APP, 'clip.mp4', io.BytesIO(b'video'), 5, actor=A)
    monkeypatch.setattr(runtime, 'selected', lambda identifier: False)
    calls = []
    def decode(source, output, cancelled, weak):
        calls.append(('decode', weak))
        raise ValueError(code)
    monkeypatch.setattr(worker, 'decode_video', decode)
    monkeypatch.setattr(analysis, 'analyze_whole_frames', lambda *args: calls.append(('model',)))
    worker.analyze_video_source(store, doc['source_id'])
    saved = store.get(doc['source_id'], A['user_id'], APP)
    assert calls == [('decode', True)]
    assert saved['status'] == 'failed'
    assert saved['error_code'] == code
    assert message in saved['error']
    assert '操作变化' not in saved['error']
