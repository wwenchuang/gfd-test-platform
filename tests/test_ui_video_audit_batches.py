"""Fine-anchor capacity is sharded inside the original counted audit engine."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from task_server.services import ui_video_trajectory as vt, ui_video_windows, ai_skill_service as ai
from test_ui_video_trajectory import frames, event


def partition(claims):
    from task_server.services.ui_video_audit_batches import partition_audit_claims
    return partition_audit_claims(claims)


def claims(count=7, shared=False):
    return [{'track': {'event_id': f'claim-{i}', 'required_frame_refs':
                      ['frame-001', f'frame-{i+2:03d}'] if shared else
                      [f'frame-{2*i+1:03d}', f'frame-{2*i+2:03d}'],
                      'nested': {'questions': ['preserve']}}} for i in range(count)]


def test_greedy_partitions_seven_claims_fourteen_anchors_without_mutation():
    original = claims(); before = copy.deepcopy(original)
    result = partition(original)
    assert [len(batch) for batch in result] == [6, 1]
    assert [item for batch in result for item in batch] == before
    result[0][0]['track']['nested']['questions'].append('changed')
    assert original == before


def test_shared_anchors_count_once_and_twelve_exact_stays_single():
    assert [len(batch) for batch in partition(claims(11, shared=True))] == [11]
    assert [len(batch) for batch in partition(claims(6))] == [6]
    assert partition([]) == [[]]


def test_single_thirteen_anchor_claim_fails_closed():
    data = claims(1); data[0]['track']['required_frame_refs'] = [f'frame-{i:03d}' for i in range(13)]
    with pytest.raises(ValueError, match='video_audit_batches_invalid'):
        partition(data)


class Engine:
    def __init__(self, monkeypatch, count=90, batches=partition, mutate=None, revisit=None):
        self.calls = []; self.records = []; self.mutate = mutate; self.count = count
        self.batches = batches; self.revisit = revisit
        monkeypatch.setattr(ui_video_windows, 'load_assets', lambda fs, *args:
                            [{'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'],
                              'mime': 'image/jpeg', 'base64': 'YQ=='} for f in fs])
        monkeypatch.setattr(ai, 'run_ai_skill', lambda *args, **kw: pytest.fail('callback isolation lost'))

    def model(self, name, payload, **kw):
        stage = kw['version']; self.calls.append((stage, copy.deepcopy(payload), copy.deepcopy(kw['image_assets'])))
        if stage == 'audit':
            result = {'checks': [{'event_id': p['track']['event_id'], 'trigger_verdict': 'supported',
                                 'outcome_verdict': 'supported', 'question': ''} for p in reversed(payload['trajectory'])],
                      'issues': [], 'multiple_scenarios': False, 'revisit': []}
        else:
            ev = []
            if stage == 'observe':
                if payload['packet_id'] == 'packet-001': ev = [event(['frame-001', 'frame-002'])]
                if payload['packet_id'] == 'packet-002':
                    ev = [event([f'frame-{i:03d}', f'frame-{i+1:03d}']) for i in range(33, 47, 2)]
            result = dict(states=[], events=ev, checks=[], uncertainties=[], multiple_scenarios=False)
        if self.mutate: self.mutate(stage, payload, result, self.calls)
        return result

    def run(self):
        return vt.observe_trajectory({'media': {'frames': frames(self.count), 'duration_ms': self.count*100+1}},
                                     None, lambda: False, skill_runner=self.model, audit_batcher=self.batches,
                                     revisit=self.revisit, record=lambda name, data: self.records.append((name, copy.deepcopy(data))))

    def audits(self, packet='packet-002'):
        return [(p, assets) for stage, p, assets in self.calls if stage == 'audit' and p['packet_id'] == packet]


def test_original_engine_two_actual_audits_keep_all_44_images_and_global_check_order(monkeypatch):
    engine = Engine(monkeypatch); result = engine.run(); audits = engine.audits()
    assert [len(p['trajectory']) for p, _ in audits] == [6, 1]
    original = vt.make_packets(frames(), [])[1]
    for payload, assets in audits:
        assert payload['frames'] == payload['image_frames'] == original['frames']
        assert len(assets) == 44 and [a['name'] for a in assets] == [f['frame_id'] for f in original['frames']]
        assert payload['owned_frame_ids'] == original['owned_frame_ids']
        assert payload['context_frame_ids'] == original['context_frame_ids']
        assert len(payload['context_trajectory']) == 7
        assert all('observation' not in p['track'] for p in payload['context_trajectory'])
        required = {r for c in payload['trajectory'] for r in c['track']['required_frame_refs']}
        assert len(required) <= 12
    audit = result['trajectory_audit']; packet = audit['packets'][1]
    ids = [p['track']['event_id'] for p in audits[0][0]['context_trajectory']]
    assert [c['event_id'] for c in packet['audit']['checks']] == ids
    assert [c['event_id'] for c in audit['fragment_checks'][1:]] == ids
    assert len(audit['calls']) == len(engine.calls) == 8
    assert len([c for c in audit['calls'] if c['stage'] == 'audit']) == 4
    assert packet['audit']['derived'] is True and len(packet['audit_shards']) == 2
    raw_calls = [c for c in audit['calls'] if c['stage'] == 'audit']
    assert all('raw_response' in c for c in raw_calls)
    assert sum(n.endswith('-audit-result') for n, _ in engine.records) == 4
    assert sum(n.endswith('-audit-combined') for n, _ in engine.records) == 3


@pytest.mark.parametrize('kind', ['duplicate', 'omit', 'rewrite', 'id', 'empty', 'missing_refs', 'overcapacity', 'mutate_input'])
def test_malformed_callback_rejected_before_current_packet_audit(monkeypatch, kind):
    def bad(original):
        if len(original) < 7: return [original]
        output = copy.deepcopy(original)
        if kind == 'duplicate': return [output, output[:1]]
        if kind == 'omit': return [output[:-1]]
        if kind == 'rewrite': output[0]['track']['target'] = 'rewritten'
        if kind == 'id': output[0]['track']['event_id'] = 'unmatched'
        if kind == 'empty': return [output[:6], [], output[6:]]
        if kind == 'missing_refs': output[0]['track']['required_frame_refs'].pop()
        if kind == 'overcapacity': return [output]
        if kind == 'mutate_input': original[0]['track']['target'] = 'mutated'; return [original[:6], original[6:]]
        return [output[:6], output[6:]]
    engine = Engine(monkeypatch, batches=bad)
    with pytest.raises(ValueError, match='video_audit_batches_invalid'): engine.run()
    assert engine.audits() == []


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'unmatched', 'issue_owner'])
def test_later_shard_contract_failure_rejects_whole_audit_with_global_two_corrections(monkeypatch, kind):
    def mutate(stage, payload, raw, calls):
        if stage != 'audit' or payload['packet_id'] != 'packet-002' or len(payload['trajectory']) != 1: return
        if kind == 'missing': raw['checks'] = []
        if kind == 'duplicate': raw['checks'] *= 2
        if kind == 'unmatched': raw['checks'][0]['event_id'] = 'unknown'
        if kind == 'issue_owner': raw['issues'] = [dict(kind='missing_action', owner_frame='frame-032', frame_refs=['frame-032'], question='must reject')]
    engine = Engine(monkeypatch, mutate=mutate)
    with pytest.raises(ValueError, match='video_model_invalid'): engine.run()
    assert len(engine.audits()) == 4  # first shard, then second shard plus exactly two corrections
    assert not any(n.endswith('-audit-combined') and '002' in n for n, _ in engine.records)


def test_conservative_merge_preserves_all_questions_issues_raw_and_multiple_scenarios(monkeypatch):
    def mutate(stage, payload, raw, calls):
        if stage != 'audit' or payload['packet_id'] != 'packet-002': return
        for c in raw['checks']: c.update(outcome_verdict='unclear', question='same actual question')
        raw['issues'] = [dict(kind='unresolved_evidence', owner_frame='frame-040', frame_refs=['frame-040'], question='same issue')]
        raw['multiple_scenarios'] = len(payload['trajectory']) == 1
    engine = Engine(monkeypatch, mutate=mutate); result = engine.run(); packet = result['trajectory_audit']['packets'][1]
    assert packet['audit']['issues'] == [packet['audit']['issues'][0]] * 2
    assert len(packet['audit']['checks']) == 7 and all(c['question'] == 'same actual question' for c in packet['audit']['checks'])
    assert all(c['status'] == 'needs_review' for c in result['trajectory_audit']['fragment_checks'][1:])
    assert result['multiple_scenarios'] is True
    assert len(packet['audit_shards']) == 2 and all(s['raw']['issues'] for s in packet['audit_shards'])


def test_revisit_in_second_shard_reobserves_whole_packet_and_reruns_earlier_shard(monkeypatch):
    requested = False
    def mutate(stage, payload, raw, calls):
        nonlocal requested
        if stage == 'audit' and payload['packet_id'] == 'packet-002' and len(payload['trajectory']) == 1 and not requested:
            requested = True; raw['revisit'] = [dict(first_frame='frame-033', last_frame='frame-034', question='real source gap')]
            raw['checks'][0].update(trigger_verdict='unclear', question='old evidence remains')
    engine = Engine(monkeypatch, mutate=mutate, revisit=lambda req, limit: [dict(frame_id='frame-301', timestamp_ms=3350)])
    result = engine.run(); audits = engine.audits(); packet = result['trajectory_audit']['packets'][1]
    assert [len(p['frames']) for p, _ in audits] == [44, 44, 45, 45]
    assert len([p for stage, p, _ in engine.calls if stage == 'observe' and p['packet_id'] == 'packet-002']) == 2
    assert all('frame-301' in [f['frame_id'] for f in p['frames']] for p, _ in audits[2:])
    assert len(packet['audit_shards']) == 2 and all(not s['raw']['revisit'] for s in packet['audit_shards'])
    history = [c['raw_response'] for c in result['trajectory_audit']['calls'] if c['stage'] == 'audit']
    assert any(c['revisit'] and c['checks'][0]['question'] == 'old evidence remains' for c in history)
    assert all(c['status'] == 'confirmed' for c in result['trajectory_audit']['fragment_checks'])


def test_no_new_frames_cannot_bypass_invalid_later_issue(monkeypatch):
    def mutate(stage, payload, raw, calls):
        if stage != 'audit' or payload['packet_id'] != 'packet-002' or len(payload['trajectory']) != 1: return
        raw['revisit'] = [dict(first_frame='frame-033', last_frame='frame-034', question='source gap')]
        raw['issues'] = [dict(kind='missing_action', owner_frame='frame-032', frame_refs=['frame-032'], question='bad owner')]
    engine = Engine(monkeypatch, mutate=mutate, revisit=lambda req, limit: [])
    with pytest.raises(ValueError, match='video_model_invalid') as exc: engine.run()
    assert exc.value.trajectory_reason == 'audit_issue_owner'


def test_original_twenty_four_counter_refuses_before_actual_twenty_fifth_request(monkeypatch):
    # One target per shard adds six calls in packet two to the original 21 calls.
    engine = Engine(monkeypatch, count=300, batches=lambda cs: [[c] for c in cs] if cs else [[]])
    with pytest.raises(ValueError, match='video_vision_limit'): engine.run()
    assert len(engine.calls) == 24
    assert [n for n, _ in engine.records if n.endswith('-request')][-1].startswith('24-')


def test_default_callback_keeps_original_audit_payload_and_packet_shape(monkeypatch):
    engine = Engine(monkeypatch, batches=None); result = engine.run()
    assert len(engine.audits()) == 1
    assert all('context_trajectory' not in p for stage, p, _ in engine.calls)
    assert all('audit_shards' not in p and 'derived' not in p['audit'] for p in result['trajectory_audit']['packets'])
    assert all('raw_response' not in c for c in result['trajectory_audit']['calls'])


def test_empty_packet_still_has_one_full_timeline_audit(monkeypatch):
    engine = Engine(monkeypatch); result = engine.run(); audit = engine.audits('packet-003')
    assert len(audit) == 1 and audit[0][0]['trajectory'] == audit[0][0]['context_trajectory'] == []
    assert len(audit[0][1]) == len(vt.make_packets(frames(), [])[2]['frames'])
    assert result['trajectory_audit']['packets'][2]['audit_shards'][0]['event_ids'] == []


def test_concurrent_invocations_keep_their_own_batcher(monkeypatch):
    engine_a = Engine(monkeypatch); engine_b = Engine(monkeypatch, batches=None)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda engine: engine.run(), [engine_a, engine_b]))
    assert len(engine_a.audits()) == 2 and len(engine_b.audits()) == 1
    assert 'audit_shards' in results[0]['trajectory_audit']['packets'][1]
    assert 'audit_shards' not in results[1]['trajectory_audit']['packets'][1]


def test_generic_prompt_explains_full_claim_context_without_source_specific_answers(monkeypatch):
    prompt = (Path(__file__).resolve().parents[1] / 'ai_skills/prompts/ui_video_trajectory.audit.md').read_text()
    monkeypatch.setattr(ai, 'load_ai_skill_prompt', lambda *a: prompt)
    rendered = ai.render_ai_skill_prompt('ui_video_trajectory', dict(trajectory=[], context_trajectory=[]), version='audit')
    assert '其他尚未核实的主张' in rendered and '仅为trajectory' in rendered
    assert '不能仅因' in rendered and '完整owned' in rendered
    for answer in ('frame-110', 'frame-005', '14帧', '7条', '两次点击'): assert answer not in prompt


def test_each_actual_shard_call_keeps_target_and_packet_provenance_in_history(monkeypatch):
    engine = Engine(monkeypatch); result = engine.run()
    entries = [c for c in result['trajectory_audit']['calls'] if c['stage'] == 'audit']
    assert [c['packet_id'] for c in entries] == ['packet-001', 'packet-002', 'packet-002', 'packet-003']
    for entry, (payload, _) in zip(entries, [engine.audits('packet-001')[0], *engine.audits(), engine.audits('packet-003')[0]]):
        assert entry['audit_target_event_ids'] == [c['track']['event_id'] for c in payload['trajectory']]
        assert entry['audit_context_event_ids'] == [c['track']['event_id'] for c in payload['context_trajectory']]


@pytest.mark.parametrize('verdict', ['unclear', 'contradicted'])
def test_negative_verdict_in_either_shard_stays_unresolved(monkeypatch, verdict):
    def mutate(stage, payload, raw, calls):
        if stage == 'audit' and payload['packet_id'] == 'packet-002':
            raw['checks'][0].update(trigger_verdict=verdict, question='actual unresolved evidence')
    engine = Engine(monkeypatch, mutate=mutate); result = engine.run()
    checks = result['trajectory_audit']['fragment_checks'][1:]
    assert sum(c['trigger_verdict'] == verdict and c['status'] == 'needs_review' for c in checks) == 2
    assert sum('独立证据复核：actual unresolved evidence' in e['uncertainties'] for e in result['events']) == 2


def test_correction_cap_shared_between_observer_and_later_audit_shard(monkeypatch):
    def mutate(stage, payload, raw, calls):
        if stage == 'observe' and payload['packet_id'] == 'packet-001' and sum(s == 'observe' for s, _, _ in calls) == 1:
            raw['checks'] = [{}]
        if stage == 'audit' and payload['packet_id'] == 'packet-002' and len(payload['trajectory']) == 1:
            raw['checks'] = []
    engine = Engine(monkeypatch, mutate=mutate)
    with pytest.raises(ValueError, match='video_model_invalid'): engine.run()
    assert len(engine.audits()) == 3  # six-claim shard, one-claim shard, one remaining correction
    assert len([n for n, _ in engine.records if n.endswith('contract-rejection')]) == 2


def test_shared_injected_runner_actual_budget_is_consumed_once_per_audit_subrequest(monkeypatch):
    engine = Engine(monkeypatch); original_model = engine.model; actual = []
    def budgeted(*args, **kw):
        if len(actual) >= 5: raise ValueError('shared_actual_budget')
        actual.append(kw['version'])
        return original_model(*args, **kw)
    engine.model = budgeted
    with pytest.raises(ValueError, match='shared_actual_budget'): engine.run()
    assert actual == ['overview', 'observe', 'audit', 'observe', 'audit']
    # The next actual call is the second shard, refused by the same shared budget.
    requests = [v for n, v in engine.records if n.endswith('-request')]
    assert len(requests[-1]['trajectory']) == 1


def test_valid_custom_partition_order_still_merges_in_original_claim_order(monkeypatch):
    engine = Engine(monkeypatch, batches=lambda cs: list(reversed(partition(cs))))
    result = engine.run(); packet = result['trajectory_audit']['packets'][1]
    assert [len(p['trajectory']) for p, _ in engine.audits()] == [1, 6]
    ids = [p['track']['event_id'] for p in engine.audits()[0][0]['context_trajectory']]
    assert [c['event_id'] for c in packet['audit']['checks']] == ids
    assert [c['event_id'] for c in result['trajectory_audit']['fragment_checks'][1:]] == ids


@pytest.mark.parametrize('output', [None, {}, [], [[], []], [[None]], [True]])
def test_malformed_empty_packet_partition_does_not_dispatch_empty_audit(monkeypatch, output):
    def batcher(cs): return partition(cs) if cs else output
    engine = Engine(monkeypatch, batches=batcher)
    with pytest.raises(ValueError, match='video_audit_batches_invalid'): engine.run()
    assert not engine.audits('packet-003')


def test_noncallable_batcher_rejected_before_any_actual_call(monkeypatch):
    engine = Engine(monkeypatch, batches=True)
    with pytest.raises(ValueError, match='video_audit_batches_invalid'): engine.run()
    assert engine.calls == []
