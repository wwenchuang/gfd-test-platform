"""Anonymous v3-shaped neighboring input and keyboard evidence."""
import copy

import pytest

from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_coverage_resolution as coverage
from task_server.services import ui_video_delayed_response as delayed
from task_server.services import ui_video_reconcile as reconcile
from task_server.services import ui_video_touch_detail as detail
from task_server.services import ui_video_windows as windows


TIMES = [13900, 14500, 14933, 16100, 16467, 17467, 18533, 19033,
         19467, 19600, 19633, 20300, 21000, 21500, 22267, 22967,
         23967, 24467, 25000, 25567, 26400, 26467, 26533]


def evidence():
    frames = [{'frame_id': f'frame-{i:03}', 'timestamp_ms': t}
              for i, t in enumerate(TIMES, 45)]
    timestamps = {f['frame_id']: f['timestamp_ms'] for f in frames}

    def event(action, target, numbers):
        refs = [f'frame-{n:03}' for n in numbers]
        return {'action': action, 'target': target, 'value': '', 'frame_refs': refs,
                'start_ms': timestamps[refs[0]], 'end_ms': timestamps[refs[-1]],
                'observation': '原图显示页面操作', 'uncertainties': [], 'evidence_kind': 'inferred'}

    font = event('aiTap', '选项', [45, 46, 47])
    focus = event('aiTap', '文本框', [47, 48, 49])
    original_input = event('aiInput', '文本框', [49, 53, 54, 56, 60, 62, 64, 66])
    reviewed_input = event('aiInput', '文本框', [48, 49, 53, 54, 56, 62, 64])
    keyboard = event('dismissKeyboard', '当前页面的软键盘', [65, 66, 67])
    return frames, [font, focus, original_input], [copy.deepcopy(font), reviewed_input, keyboard]


def reply(events):
    return {'events': [{k: copy.deepcopy(v) for k, v in e.items()
                        if k not in {'start_ms', 'end_ms', 'evidence_kind'}} for e in events],
            'coverage_resolutions': [], 'uncertainties': [], 'multiple_scenarios': False}


def setup(monkeypatch, responses):
    monkeypatch.setattr(windows, 'load_assets', lambda *a, **k: [])
    monkeypatch.setattr(detail, 'append_details', lambda *a, **k: [])
    calls = []
    def run(skill, payload, **kwargs):
        calls.append(copy.deepcopy(payload))
        return copy.deepcopy(responses[min(len(calls) - 1, len(responses) - 1)])
    monkeypatch.setattr(ai, 'run_ai_skill', run)
    return calls


def run_window(tmp_path, first, reviewed, frames, **kwargs):
    window = frames[:-1]
    ids = {f['frame_id'] for f in window}
    return reconcile._repair_window(first, {'events': reviewed[:2], 'uncertainties': [], 'multiple_scenarios': False},
        window, 195698, tmp_path, lambda: False, [], ids, {'frame-065', 'frame-066'},
        context_frames=frames, attempt_limit=2, **kwargs)


def test_v3_boundary_retry_reports_actual_rejected_input_then_preserves_both_coverage_gates(monkeypatch, tmp_path):
    frames, first, reviewed = evidence()
    calls = setup(monkeypatch, [reply([first[0], first[2]]), reply([reviewed[0], first[1], reviewed[1]])])
    result = run_window(tmp_path, first, reviewed, frames)
    assert len(calls) == 2
    assert calls[1]['boundary_conflicts'] == [{'action': 'aiInput', 'frame_refs': ['frame-066']}]
    assert '原' in calls[1]['validation_feedback']
    assert '上次声明无效' not in calls[1]['validation_feedback']
    assert '文本框' not in str(calls[1]['boundary_conflicts'])
    assert all(coverage.boundary_safe(e, {'frame-065', 'frame-066'}, [], frames) for e in result['events'])
    reconcile.check_coverage(first, copy.deepcopy(result['events']), 195698)
    reconcile.check_coverage(reviewed[:2], copy.deepcopy(result['events']), 195698)


def test_v3_full_repair_preserves_separate_keyboard_action(monkeypatch, tmp_path):
    frames, first, reviewed = evidence()
    calls = setup(monkeypatch, [reply([first[0], first[2]]), reply([reviewed[0], first[1], reviewed[1]])])
    checked = {'events': reviewed, 'uncertainties': [], 'multiple_scenarios': False}
    result = reconcile._repair_coverage(first, checked, frames, 195698, tmp_path, lambda: False, [],
                                        {'frame-047', 'frame-048', 'frame-049'})
    assert len(calls) == 2
    assert calls[1]['boundary_conflicts'] == [{'action': 'aiInput', 'frame_refs': ['frame-066']}]
    assert [e['action'] for e in result['events']] == ['aiTap', 'aiTap', 'aiInput', 'dismissKeyboard']
    assert result['events'][-1]['frame_refs'] == ['frame-065', 'frame-066', 'frame-067']
    assert result['events'][2]['frame_refs'] == reviewed[1]['frame_refs']
    reconcile.check_coverage(first, copy.deepcopy(result['events']), 195698)
    reconcile.check_coverage(reviewed, copy.deepcopy(result['events']), 195698)


def test_second_reply_still_cannot_skip_focus_or_escape_boundary(monkeypatch, tmp_path):
    frames, first, reviewed = evidence()
    for bad_second in (reply([reviewed[0], reviewed[1]]), reply([first[0], first[1], first[2]])):
        calls = setup(monkeypatch, [reply([first[0], first[2]]), bad_second])
        with pytest.raises(ValueError):
            run_window(tmp_path, first, reviewed, frames)
        assert len(calls) == 2


@pytest.mark.parametrize('mode', ['motion', 'delayed', 'return_only'])
def test_boundary_does_not_extend_blind_or_return_only_budget(monkeypatch, tmp_path, mode):
    frames, first, reviewed = evidence()
    if mode == 'delayed':
        monkeypatch.setattr(delayed, 'review_refs', lambda *args: {'frame-047'})
    calls = setup(monkeypatch, [reply([first[0], first[2]])])
    options = {'motion_review_refs': {'frame-047'}} if mode == 'motion' else {}
    if mode == 'return_only': options['retry_return_gesture_only'] = True
    with pytest.raises(ValueError, match='video_reference_invalid'):
        run_window(tmp_path, first, reviewed, frames, **options)
    assert len(calls) == 1
