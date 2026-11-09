"""A coarse no-touch observation is never permission to silently erase a click."""
import copy

from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_reconcile as rec
from task_server.services import ui_video_windows as windows
from task_server.services.ui_case_draft_contract import build_draft_yaml
from task_server.services.ui_video_analysis import draft_proposal


def test_sparse_tap_to_auto_assert_keeps_human_gate_through_real_repair(monkeypatch, tmp_path):
    frames = [{'frame_id': 'frame-079', 'timestamp_ms': 31133},
              {'frame_id': 'frame-080', 'timestamp_ms': 31900},
              {'frame_id': 'frame-081', 'timestamp_ms': 32900},
              {'frame_id': 'frame-082', 'timestamp_ms': 33900}]
    tap = {'action': 'aiTap', 'target': '去打印按钮', 'value': '',
           'frame_refs': ['frame-080', 'frame-082'], 'start_ms': 31900,
           'end_ms': 33900, 'observation': '点击去打印，随后出现准备提示',
           'uncertainties': [], 'evidence_kind': 'inferred'}
    auto = {'action': 'aiAssert', 'target': '加载状态', 'value': '',
            'frame_refs': ['frame-079', 'frame-080', 'frame-082'],
            'start_ms': 31133, 'end_ms': 33900,
            'observation': '页面自动加载，未见新的用户点击操作',
            'uncertainties': [], 'evidence_kind': 'inferred'}
    checked = {'events': [copy.deepcopy(auto)], 'uncertainties': [], 'multiple_scenarios': False}
    monkeypatch.setattr(windows, 'load_assets', lambda *args: [])
    def model(*args, **kwargs):
        event = {k: v for k, v in auto.items() if k not in {'start_ms', 'end_ms', 'evidence_kind'}}
        return {'events': [copy.deepcopy(event)], 'uncertainties': [],
                'multiple_scenarios': False, 'coverage_resolutions': []}
    monkeypatch.setattr(ai, 'run_ai_skill', model)
    result = rec._repair_coverage([tap], checked, frames, 40000, tmp_path, lambda: False, [],
                                  {'frame-079', 'frame-080', 'frame-082'})
    assert any('首轮点击' in q and '核对' in q
               for event in result['events'] for q in event['uncertainties'])
    document = {'source_id': 'uvs_' + 'a' * 32, 'app_id': 'com.kfb.model',
                'analysis_version': 4, 'goal': '', 'media': {'frames': frames, 'duration_ms': 40000},
                **result}
    proposal, _ = draft_proposal(document)
    for step in proposal['steps']:
        step['review_state'] = 'confirmed'
    gate = build_draft_yaml(proposal, 'com.kfb.model')
    assert not gate['ready']
    assert any('首轮点击' in q and '核对' in q for q in gate['questions'])
