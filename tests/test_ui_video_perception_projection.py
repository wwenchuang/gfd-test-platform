"""Lossless runtime-only format projection; generic fixtures, no user source text."""
import copy
import json

import pytest
from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_perception_binding as binding
from task_server.services.ui_video_runtime import PerceptionBank
from test_ui_video_stage_wire import evidence, inventory, capture


def malformed_shape(payload):
    value = inventory({**payload, 'context_frame_ids': payload['context_frame_ids'] or payload['owned_frame_ids'][:1]})
    value['uncertainties'] = '  root question\n未确认  '
    for i, row in enumerate(value['frames']):
        row['persistent_ui'] = '区域：原文\\literal\n第二行 😀'
        row['uncertainties'] = '' if i == 0 else '无'
        row['role'] = 'context'
        row['context_refs'] = [{'frame_id': payload['owned_frame_ids'][0], 'role': 'context'}]
    return value


def test_stage_losslessly_projects_only_three_paths_after_exact_raw_receipt(monkeypatch):
    p, assets = evidence(); raw = malformed_shape(p); before = copy.deepcopy(raw)
    wire = json.dumps(raw, ensure_ascii=False, indent=2); receipts = []
    capture(monkeypatch, content=wire)
    value = ai.run_ai_skill('ui_video_perception', p, image_assets=assets,
                            video_stage='perception', raw_receipt=receipts.append)
    expected = copy.deepcopy(before); expected['uncertainties'] = [before['uncertainties']]
    for row in expected['frames']:
        for key in ('persistent_ui', 'uncertainties'): row[key] = [row[key]]
    assert value == expected and raw == before
    assert receipts[0]['content'] == wire
    assert ai.validate_ai_skill_output('ui_video_perception', value) == expected
    bank = PerceptionBank(p['frames']); bank.admit(p, value, {})
    entry = bank.entries[0]
    assert entry['uncertainties'] == expected['uncertainties']
    assert len(entry['binding_audit']['warnings']) == len(value['frames']) * 2
    assert {w['reason'] for w in entry['binding_audit']['warnings']} == {'source_partition_role'}
    assert entry['binding_audit']['pixel_verified'] is False
    assert entry['binding_audit']['qualified'] is False
    for original, row in zip(expected['frames'], entry['frames']):
        assert row['role'] == 'owned' and row['evidence_refs'][0]['role'] == 'owned'
        assert all(row[k] == original[k] for k in original if k not in {'role', 'context_refs'})


def test_projection_audit_is_bounded_content_free_and_arrays_unchanged():
    p, _ = evidence(); raw = malformed_shape(p); before = copy.deepcopy(raw)
    projected, audit = binding.project_inventory_format(raw)
    assert raw == before and len(audit) == 1 + 2 * len(raw['frames'])
    assert audit[0] == {'path': '$.uncertainties', 'reason': 'singleton_string_array',
                        'from_type': 'string', 'to_type': 'array'}
    assert [a['path'] for a in audit[1:]] == [f'$.frames[{i}].{key}' for i in range(2)
                                                    for key in ('persistent_ui', 'uncertainties')]
    assert 'root question' not in json.dumps(audit) and 'literal' not in json.dumps(audit)
    again, none = binding.project_inventory_format(projected)
    assert again == projected and none == []
    canonical = inventory(p)
    assert binding.project_inventory_format(canonical) == (canonical, [])


@pytest.mark.parametrize('change', ['unknown', 'missing', 'object', 'number', 'null', 'bool',
    'marks_string', 'refs_string', 'field_array', 'long', 'fake_id', 'alias', 'dropped', 'duplicate', 'order', 'fake_ref'])
def test_nonallowlisted_errors_still_fail_after_projection(monkeypatch, change):
    p, assets = evidence(); raw = malformed_shape(p); row = raw['frames'][0]
    if change == 'unknown': row['action'] = 'tap'
    elif change == 'missing': del row['changes']
    elif change in {'object', 'number', 'null', 'bool'}: row['persistent_ui'] = {'object': {}, 'number': 1, 'null': None, 'bool': True}[change]
    elif change == 'marks_string': row['transient_marks'] = 'none'
    elif change == 'refs_string': row['context_refs'] = 'none'
    elif change == 'field_array': row['field_state'] = ['text']
    elif change == 'long': row['persistent_ui'] = 'x' * 8193
    elif change == 'fake_id': row['frame_id'] = 'frame-999'
    elif change == 'alias': row['frame_id'] = '000'
    elif change == 'dropped': raw['frames'].pop()
    elif change == 'duplicate': raw['frames'][1]['frame_id'] = row['frame_id']
    elif change == 'order': raw['frames'].reverse()
    elif change == 'fake_ref': row['context_refs'][0]['frame_id'] = 'frame-999'
    capture(monkeypatch, content=json.dumps(raw))
    with pytest.raises((ValueError, RuntimeError)):
        ai.run_ai_skill('ui_video_perception', p, image_assets=assets, video_stage='perception')


def test_invalid_json_still_rejects_after_raw_without_repair(monkeypatch):
    p, assets = evidence(); receipts = []; calls = capture(monkeypatch, content='{"frames":[')
    with pytest.raises(RuntimeError):
        ai.run_ai_skill('ui_video_perception', p, image_assets=assets, video_stage='perception', raw_receipt=receipts.append)
    assert len(calls) == 1 and receipts[0]['content'] == '{"frames":['


def test_canonical_schema_itself_still_rejects_strings():
    p, _ = evidence()
    with pytest.raises(ai.SkillSchemaError):
        ai.validate_ai_skill_output('ui_video_perception', malformed_shape(p))


@pytest.mark.parametrize('invalid_schema', [False, True])
def test_private_projection_audit_links_exact_durable_raw_even_on_failure(tmp_path, monkeypatch, invalid_schema):
    from test_ui_video_model_jobs import Gateway
    from task_server import config
    from task_server.services import ui_video_runtime as runtime, ui_video_trajectory
    from task_server.services.ui_video_records import RecordStore
    p, _ = evidence(); frames = p['frames']; captured = []; publications = []
    (tmp_path / 'frames').mkdir()
    for f in frames: (tmp_path / 'frames' / (f['frame_id'] + '.jpg')).write_bytes(b'synthetic original')
    def response(body):
        raw = malformed_shape(body['payload'])
        if invalid_schema: raw['frames'][0]['unrecognized'] = 'keep and reject'
        content = json.dumps(raw, ensure_ascii=False, indent=2); captured.append(content)
        return content
    def engine(*args, **kwargs): raise ValueError('stop_after_bank')
    monkeypatch.setattr(ui_video_trajectory, 'observe_trajectory', engine)
    gateway = Gateway(response)
    try:
        monkeypatch.setattr(config, 'AI_GATEWAY_URL', gateway.url)
        with pytest.raises(Exception):
            runtime.run_runtime({'media': {'frames': frames}}, tmp_path, tmp_path / 'source.mp4',
                                lambda: None, lambda **kw: publications.append(kw))
        private = next(row['video_runtime_private'] for row in publications if 'video_runtime_private' in row)
        assert len(private['perception_format_projections']) == 1
        audit = private['perception_format_projections'][0]
        assert audit['window_id'] == 'producer-original-001' and len(audit['projections']) == 7
        assert all(set(v) == {'path', 'reason', 'from_type', 'to_type'} for v in audit['projections'])
        with RecordStore.open_snapshot(tmp_path / 'model-records', private['records']) as records:
            receipt = records.read(audit['receipt_ref'])
            assert receipt['content'] == captured[0]
        assert len(private['windows']) == (0 if invalid_schema else 1)
        if not invalid_schema:
            entry = private['windows'][0]
            assert entry['receipt_ref'] == audit['receipt_ref']
            assert len(entry['binding_audit']['warnings']) == 6
            assert not entry['binding_audit']['qualified']
        assert len(gateway.calls) == 1
    finally: gateway.close()


def test_projection_does_not_walk_unbounded_wrong_row_counts_or_fill_fields():
    raw = {'frames': [{'persistent_ui': 'literal', 'uncertainties': 'question'}] * 13}
    value, audit = binding.project_inventory_format(raw)
    assert value == raw and audit == [] and 'uncertainties' not in value


def test_bank_refuses_any_other_binding_warning(monkeypatch):
    from task_server.services import ui_video_runtime as runtime
    p, _ = evidence(); raw = inventory(p); real_bind = binding.bind_inventory
    def unapproved(*args, **kwargs):
        result = real_bind(*args, **kwargs)
        result['binding_audit']['warnings'].append({'reason': 'numeric_suffix_alias'})
        return result
    monkeypatch.setattr(runtime, 'bind_inventory', unapproved)
    with pytest.raises(ValueError, match='video_perception_binding_invalid'):
        PerceptionBank(p['frames']).admit(p, raw, {})


def test_ordinary_skill_does_not_project_string_lists(monkeypatch):
    monkeypatch.setattr(ai, 'render_ai_skill_prompt', lambda *a, **kw: 'ordinary skill')
    monkeypatch.setattr(ai, 'load_ai_skill_schema', lambda *a, **kw: {
        'type': 'object', 'properties': {'uncertainties': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['uncertainties'], 'additionalProperties': False})
    monkeypatch.setattr(ai, 'ai_gateway_skill_content', lambda *a, **kw: '{"uncertainties":"retain error"}')
    with pytest.raises((ai.SkillSchemaError, RuntimeError)):
        ai.run_ai_skill('ordinary_test', {}, model_config={'model':'explicit'})


def test_prompt_explicit_arrays_and_authoritative_ref_roles():
    p, _ = evidence(); prompt = ai.render_ai_skill_prompt('ui_video_perception', p)
    assert '必须始终为 JSON 数组' in prompt and '只有一项也用方括号' in prompt
    assert '引用 owned_frame_ids 中的前一帧也必须写 role:"owned"' in prompt
    assert '不得为预算省略控件' in prompt
