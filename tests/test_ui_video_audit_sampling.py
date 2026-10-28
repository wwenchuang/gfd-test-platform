"""Fine sampling is bounded selection of supplied evidence, never event inference."""
import copy
import importlib

import pytest


def select(*args):
    module = importlib.import_module('task_server.services.ui_video_audit_sampling')
    return module.select_audit_fine_frames(*args)


def frames(n=20):
    return [{'frame_id': f'frame-{i*3:03}', 'timestamp_ms': i*100} for i in range(n)]


def claim(a, b):
    return {'track': {'event_id': f'{a}:{b}', 'required_frame_refs': [a, b],
                      'trigger_frame': a, 'outcome': {'frame_refs': [b]}}}


def marks(fs, owned, descriptions=None):
    return [{'frame_id': f['frame_id'], 'role': 'owned' if f['frame_id'] in owned else 'context',
             'timestamp_ms': f['timestamp_ms'], 'transient_marks':
             (descriptions or {}).get(f['frame_id'], [])} for f in fs]


def mark(label):
    return {'shape': 'circle', 'color': label, 'nearest_visible_control': 'input',
            'description': 'click twice then hide keyboard'}


def test_mandatory_shared_anchors_and_chronological_existing_neighbors():
    fs = frames(); ids = [f['frame_id'] for f in fs]
    claims = [claim(ids[5], ids[8]), claim(ids[8], ids[11])]
    result = select(fs, claims, marks(fs, ids), ids)
    assert result == [ids[i] for i in [4, 5, 6, 7, 8, 9, 10, 11, 12]]
    assert set(result) <= set(ids)  # IDs are intentionally non-successive.


def test_owned_neighbors_beat_context_and_ime_mark_diversity():
    fs = frames(30); ids = [f['frame_id'] for f in fs]; owned = ids[5:25]
    claims = [claim(ids[i], ids[i+2]) for i in (5, 9, 13, 17, 21)]
    descriptions = {fid: [mark(str(i))] for i, fid in enumerate(ids)}
    result = select(fs, claims, marks(fs, owned, descriptions), owned)
    required = {r for c in claims for r in c['track']['required_frame_refs']}
    assert len(result) == 12 and required <= set(result)
    assert set(result) - required == {ids[6], ids[8]}
    assert ids[4] not in result and ids[0] not in result


def test_mandatory_twelve_has_no_anchor_loss_or_neighbor_room():
    fs = frames(); ids = [f['frame_id'] for f in fs]
    claims = [claim(ids[i], ids[i+1]) for i in range(2, 14, 2)]
    assert select(fs, claims, marks(fs, ids), ids) == ids[2:14]


def test_overcapacity_fails_closed():
    fs = frames(); ids = [f['frame_id'] for f in fs]
    claims = [claim(ids[i], ids[i+1]) for i in range(0, 14, 2)]
    with pytest.raises(ValueError, match='video_audit_sampling_invalid'):
        select(fs, claims, marks(fs, ids), ids)


def test_empty_claims_represent_owned_prototype_diversity_before_context():
    fs = frames(20); ids = [f['frame_id'] for f in fs]; owned = ids[4:18]
    descriptions = {fid: [mark('same')] for fid in ids}
    descriptions[ids[17]] = [mark('different')]
    assert select(fs, [], marks(fs, owned, descriptions), owned) == [ids[0], ids[4], ids[17]]
    assert select(fs, [], marks(fs, owned), owned) == []
    assert select([], [], [], []) == []


def test_deterministic_dedup_immutable_and_descriptions_never_become_actions():
    fs = frames(); ids = [f['frame_id'] for f in fs]; owned = ids[2:18]
    facts = marks(fs, owned, {ids[8]: [mark('red'), mark('red')]})
    claims = [claim(ids[5], ids[8])]; before = copy.deepcopy((fs, claims, facts, owned))
    first = select(fs, claims, facts, owned)
    assert first == select(fs, claims, list(reversed(facts)), owned)
    assert len(first) == len(set(first)) and all(type(x) is str for x in first)
    assert (fs, claims, facts, owned) == before
    facts[8]['transient_marks'][0]['description'] = 'not an action'
    assert select(fs, claims, facts, owned) == first


@pytest.mark.parametrize('kind', ['duplicate_frame', 'descending_time', 'equal_time', 'bool_time', 'negative_time',
                                 'unknown_ref', 'duplicate_ref', 'unknown_owned', 'duplicate_owned', 'owned_order',
                                 'unknown_fact', 'duplicate_fact', 'wrong_role', 'wrong_time', 'bad_mark',
                                 'unknown_trigger', 'unknown_outcome', 'missing_required_anchor'])
def test_invalid_evidence_fails_closed(kind):
    fs = frames(); ids = [f['frame_id'] for f in fs]; owned = ids[2:18]
    claims = [claim(ids[5], ids[8])]; facts = marks(fs, owned)
    if kind == 'duplicate_frame': fs[1]['frame_id'] = ids[0]
    if kind == 'descending_time': fs[1]['timestamp_ms'] = -1
    if kind == 'equal_time': fs[1]['timestamp_ms'] = 0
    if kind == 'bool_time': fs[0]['timestamp_ms'] = False
    if kind == 'negative_time': fs[0]['timestamp_ms'] = -1
    if kind == 'unknown_ref': claims[0]['track']['required_frame_refs'].append('unknown')
    if kind == 'duplicate_ref': claims[0]['track']['required_frame_refs'].append(ids[5])
    if kind == 'unknown_owned': owned.append('unknown')
    if kind == 'duplicate_owned': owned.append(owned[-1])
    if kind == 'owned_order': owned.reverse()
    if kind == 'unknown_fact': facts[0]['frame_id'] = 'unknown'
    if kind == 'duplicate_fact': facts.append(copy.deepcopy(facts[0]))
    if kind == 'wrong_role': facts[5]['role'] = 'context'
    if kind == 'wrong_time': facts[5]['timestamp_ms'] += 1
    if kind == 'bad_mark': facts[5]['transient_marks'] = ['click']
    if kind == 'unknown_trigger': claims[0]['track']['trigger_frame'] = 'unknown'
    if kind == 'unknown_outcome': claims[0]['track']['outcome']['frame_refs'] = ['unknown']
    if kind == 'missing_required_anchor': claims[0]['track']['required_frame_refs'] = [ids[5]]
    with pytest.raises(ValueError, match='video_audit_sampling_invalid'):
        select(fs, claims, facts, owned)
