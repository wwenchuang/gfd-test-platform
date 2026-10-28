"""Generic inventory metadata projection; no source-video answers or network use."""
import copy
import hashlib
import json

import pytest


def fixture():
    packet = {
        'packet_id': 'packet-test',
        # Chronology deliberately differs from frame-number order.
        'frames': [
            {'frame_id': 'frame-001', 'timestamp_ms': 0},
            {'frame_id': 'frame-004', 'timestamp_ms': 10},
            {'frame_id': 'frame-002', 'timestamp_ms': 20},
        ],
        'owned_frame_ids': ['frame-004', 'frame-002'],
        'context_frame_ids': ['frame-001'],
    }
    raw = {'packet_id': packet['packet_id'], 'frames': [
        {
            'frame_id': frame_id, 'role': 'owned',
            'transient_marks': [{'shape': 'unknown', 'color': 'gray',
                                 'position': 'near field',
                                 'nearest_visible_control': 'field',
                                 'uncertainty': 'unresolved'}],
            'persistent_ui': ['field', 'field'], 'field_state': 'unknown',
            'keyboard_state': 'hidden', 'changes': 'unknown',
            'context_refs': [{'frame_id': 'frame-001', 'role': 'context'}],
            'uncertainties': ['question?', 'question?', 'another?'],
        } for frame_id in packet['owned_frame_ids']
    ], 'uncertainties': ['root?', 'root?']}
    return raw, packet


def bind(raw, packet, **kwargs):
    from task_server.services.ui_video_perception_binding import bind_inventory
    return bind_inventory(raw, packet, **kwargs)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def test_exact_bank_preserves_visuals_questions_and_inputs_without_qualification():
    raw, packet = fixture()
    before_raw, before_packet = encoded(raw), encoded(packet)
    bank = bind(raw, packet)
    assert set(bank) == {'packet_id', 'frames', 'uncertainties', 'binding_audit'}
    assert bank['packet_id'] == packet['packet_id']
    assert bank['uncertainties'] == raw['uncertainties']
    assert [r['frame_id'] for r in bank['frames']] == packet['owned_frame_ids']
    for original, row in zip(raw['frames'], bank['frames']):
        assert set(row) == set(original) - {'context_refs'} | {'evidence_refs'}
        for key in set(original) - {'context_refs'}:
            assert row[key] == original[key]
        assert row['evidence_refs'] == original['context_refs']
    assert encoded(raw) == before_raw and encoded(packet) == before_packet
    assert bank['binding_audit']['warnings'] == []
    assert bank['binding_audit']['pixel_verified'] is False
    assert bank['binding_audit']['qualified'] is False
    bank['frames'][0]['transient_marks'][0]['shape'] = 'changed'
    bank['frames'][0]['uncertainties'].clear()
    bank['uncertainties'].clear()
    assert encoded(raw) == before_raw and encoded(packet) == before_packet


def test_suffix_aliases_are_explicit_and_each_projection_is_audited():
    raw, packet = fixture()
    raw['frames'][0]['frame_id'] = '004'
    raw['frames'][0]['context_refs'][0]['frame_id'] = '001'
    original = encoded(raw)
    with pytest.raises(ValueError):
        bind(raw, packet)
    bank = bind(raw, packet, allow_numeric_suffix_aliases=True)
    assert bank['frames'][0]['frame_id'] == 'frame-004'
    assert bank['frames'][0]['evidence_refs'] == [
        {'frame_id': 'frame-001', 'role': 'context'}]
    assert bank['binding_audit']['warnings'] == [
        {'path': 'frames[0].frame_id', 'original': '004',
         'canonical': 'frame-004', 'reason': 'numeric_suffix_alias'},
        {'path': 'frames[0].context_refs[0].frame_id', 'original': '001',
         'canonical': 'frame-001', 'reason': 'numeric_suffix_alias'},
    ]
    assert encoded(raw) == original


@pytest.mark.parametrize('label', ['002', '001', '999', '4', '04', '0004',
                                    'prefix-004', 'frame-4', ' 004', 4, None])
def test_row_aliases_never_guess_pad_coerce_or_reorder(label):
    raw, packet = fixture()
    raw['frames'][0]['frame_id'] = label
    with pytest.raises(ValueError):
        bind(raw, packet, allow_numeric_suffix_aliases=True)


@pytest.mark.parametrize('mutation', ['invented', 'duplicate', 'reordered', 'missing', 'extra'])
def test_rows_must_exactly_cover_owned_in_chronology(mutation):
    raw, packet = fixture()
    if mutation == 'invented':
        raw['frames'][0]['frame_id'] = 'frame-999'
    elif mutation == 'duplicate':
        raw['frames'][1] = copy.deepcopy(raw['frames'][0])
    elif mutation == 'reordered':
        raw['frames'].reverse()
    elif mutation == 'missing':
        raw['frames'].pop()
    else:
        raw['frames'].append(copy.deepcopy(raw['frames'][0]))
    with pytest.raises(ValueError):
        bind(raw, packet, allow_numeric_suffix_aliases=True)


def test_every_reference_role_and_row_role_comes_from_source_partition():
    raw, packet = fixture()
    raw['frames'][0]['role'] = 'context'
    raw['frames'][0]['context_refs'] = [
        {'frame_id': 'frame-002', 'role': 'context'},
        {'frame_id': 'frame-001', 'role': 'owned'},
    ]
    before = encoded(raw)
    bank = bind(raw, packet)
    assert bank['frames'][0]['role'] == 'owned'
    assert bank['frames'][0]['evidence_refs'] == [
        {'frame_id': 'frame-002', 'role': 'owned'},
        {'frame_id': 'frame-001', 'role': 'context'},
    ]
    assert bank['binding_audit']['warnings'] == [
        {'path': 'frames[0].role', 'original': 'context',
         'canonical': 'owned', 'reason': 'source_partition_role'},
        {'path': 'frames[0].context_refs[0].role', 'original': 'context',
         'canonical': 'owned', 'reason': 'source_partition_role'},
        {'path': 'frames[0].context_refs[1].role', 'original': 'owned',
         'canonical': 'context', 'reason': 'source_partition_role'},
    ]
    assert encoded(raw) == before
    assert len(bank['frames']) == len(packet['owned_frame_ids'])
    assert 'actions' not in bank


@pytest.mark.parametrize('refs', [
    [{'frame_id': 'frame-999', 'role': 'context'}],
    [{'frame_id': '001', 'role': 'context'}, {'frame_id': 'frame-001', 'role': 'context'}],
    [{'frame_id': 'frame-001', 'role': 'context'}] * 2,
    [{'frame_id': 1, 'role': 'context'}],
    [{'frame_id': 'frame-001', 'role': 'other'}],
    [{'frame_id': 'frame-001', 'role': None}],
    [{'frame_id': 'frame-001'}],
    [{'frame_id': 'frame-001', 'role': 'context', 'action': 'tap'}],
    ['frame-001'], None, {},
])
def test_references_reject_unknown_duplicate_after_binding_or_invalid_shapes(refs):
    raw, packet = fixture()
    raw['frames'][0]['context_refs'] = refs
    with pytest.raises(ValueError):
        bind(raw, packet, allow_numeric_suffix_aliases=True)


@pytest.mark.parametrize('mutation', [
    'duplicate_id', 'duplicate_time', 'reversed_time', 'negative_time', 'boolean_time',
    'string_time', 'float_time', 'missing_time', 'invalid_id', 'ambiguous_suffix',
    'overlap', 'unpartitioned', 'unknown_owned', 'duplicate_owned', 'reordered_owned',
    'unknown_context', 'duplicate_context', 'invalid_owned_type', 'invalid_context_type',
    'empty_frames', 'wrong_packet', 'invalid_packet_id', 'missing_partition',
])
def test_invalid_authoritative_metadata_is_rejected(mutation):
    raw, packet = fixture()
    if mutation == 'duplicate_id':
        packet['frames'][1]['frame_id'] = 'frame-001'
    elif mutation == 'duplicate_time':
        packet['frames'][1]['timestamp_ms'] = 0
    elif mutation == 'reversed_time':
        packet['frames'].reverse()
    elif mutation in {'negative_time', 'boolean_time', 'string_time', 'float_time'}:
        packet['frames'][0]['timestamp_ms'] = {
            'negative_time': -1, 'boolean_time': True, 'string_time': '0', 'float_time': 0.0,
        }[mutation]
    elif mutation == 'missing_time':
        del packet['frames'][0]['timestamp_ms']
    elif mutation == 'invalid_id':
        packet['frames'][0]['frame_id'] = '001'
    elif mutation == 'ambiguous_suffix':
        packet['frames'].append({'frame_id': 'frame-1001', 'timestamp_ms': 30})
        packet['context_frame_ids'].append('frame-1001')
    elif mutation == 'overlap':
        packet['context_frame_ids'].append('frame-002')
    elif mutation == 'unpartitioned':
        packet['context_frame_ids'] = []
    elif mutation == 'unknown_owned':
        packet['owned_frame_ids'][0] = 'frame-999'
    elif mutation == 'duplicate_owned':
        packet['owned_frame_ids'].append('frame-002')
    elif mutation == 'reordered_owned':
        packet['owned_frame_ids'].reverse()
    elif mutation == 'unknown_context':
        packet['context_frame_ids'] = ['frame-999']
    elif mutation == 'duplicate_context':
        packet['context_frame_ids'] *= 2
    elif mutation == 'invalid_owned_type':
        packet['owned_frame_ids'] = ['frame-004', 2]
    elif mutation == 'invalid_context_type':
        packet['context_frame_ids'] = 'frame-001'
    elif mutation == 'empty_frames':
        packet['frames'] = []
    elif mutation == 'wrong_packet':
        raw['packet_id'] = 'other-packet'
    elif mutation == 'invalid_packet_id':
        packet['packet_id'] = None
    else:
        del packet['context_frame_ids']
    with pytest.raises(ValueError):
        bind(raw, packet, allow_numeric_suffix_aliases=True)


def test_context_partition_order_is_validated_too():
    raw, packet = fixture()
    packet['frames'].append({'frame_id': 'frame-003', 'timestamp_ms': 30})
    packet['context_frame_ids'] = ['frame-003', 'frame-001']
    with pytest.raises(ValueError):
        bind(raw, packet)


@pytest.mark.parametrize('target,key,value', [
    ('root', 'extra', []), ('root', 'uncertainties', 'question'),
    ('root', 'uncertainties', [False]), ('root', 'uncertainties', ['q'] * 129),
    ('row', 'actions', []), ('row', 'role', 'invalid'), ('row', 'role', []),
    ('row', 'transient_marks', {}), ('row', 'transient_marks', [{}]),
    ('row', 'transient_marks', ['circle']),
    ('row', 'persistent_ui', [3]), ('row', 'persistent_ui', ['x'] * 129),
    ('row', 'field_state', {}), ('row', 'keyboard_state', False),
    ('row', 'changes', 'x' * 8193), ('row', 'uncertainties', None),
    ('mark', 'action', 'tap'), ('mark', 'shape', 3),
    ('mark', 'uncertainty', ['question']),
])
def test_inventory_bounded_shapes_and_unknown_fields_are_strict(target, key, value):
    raw, packet = fixture()
    container = {'root': raw, 'row': raw['frames'][0],
                 'mark': raw['frames'][0]['transient_marks'][0]}[target]
    container[key] = value
    with pytest.raises(ValueError):
        bind(raw, packet)


@pytest.mark.parametrize('target,key', [('root', 'uncertainties'), ('row', 'changes'),
                                       ('mark', 'uncertainty')])
def test_inventory_required_fields_cannot_disappear(target, key):
    raw, packet = fixture()
    container = {'root': raw, 'row': raw['frames'][0],
                 'mark': raw['frames'][0]['transient_marks'][0]}[target]
    del container[key]
    with pytest.raises(ValueError):
        bind(raw, packet)


def test_audit_hashes_are_deterministic_parsed_json_proofs_and_bind_index_times():
    raw, packet = fixture()
    bank = bind(raw, packet)
    assert bank == bind(copy.deepcopy(raw), copy.deepcopy(packet))
    audit = bank['binding_audit']
    assert audit['hash_definition'] == 'sha256(UTF-8 JSON; sorted keys; compact separators; ensure_ascii=false; allow_nan=false)'
    assert audit['raw_inventory_sha256'] == digest(raw)
    index = {'packet_id': packet['packet_id'], 'frames': [
        dict(frame, role='owned' if frame['frame_id'] in packet['owned_frame_ids'] else 'context')
        for frame in packet['frames']
    ]}
    assert audit['authoritative_index_sha256'] == digest(index)
    assert audit['normalized_bank_sha256'] == digest({
        k: v for k, v in bank.items() if k != 'binding_audit'})
    packet['frames'][-1]['timestamp_ms'] += 1
    changed = bind(raw, packet)
    assert changed['binding_audit']['authoritative_index_sha256'] != audit['authoritative_index_sha256']
    assert changed['binding_audit']['raw_inventory_sha256'] == audit['raw_inventory_sha256']
    raw['uncertainties'].append('still unresolved')
    assert bind(raw, packet)['binding_audit']['raw_inventory_sha256'] != audit['raw_inventory_sha256']


@pytest.mark.parametrize('option', ['true', 1, None])
def test_alias_compatibility_requires_an_explicit_boolean(option):
    raw, packet = fixture()
    with pytest.raises(ValueError):
        bind(raw, packet, allow_numeric_suffix_aliases=option)
