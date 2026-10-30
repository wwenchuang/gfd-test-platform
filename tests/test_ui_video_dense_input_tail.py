"""Synthetic offline controls for bounded, unverified input-tail sampling."""
import copy
import pytest
from task_server.services import ui_video_trajectory as vt
from test_ui_video_trajectory import event, frames


def hint(last, **kw):
    return dict(state_id='hint', first_frame=last, last_frame=last, kind='input', description='unverified', **kw)


def select(fs, states, packets=None):
    return vt.select_input_tail(fs, states, vt.make_packets(fs, []) if packets is None else packets)


def test_selector_temporal_neighbors_not_id_order_and_immutable():
    fs=[{'frame_id':name,'timestamp_ms':i*400} for i,name in enumerate(['z','a','y','b','x'])]
    states=[hint('a'),hint('a'),hint('b')];packets=vt.make_packets(fs,[])
    before=copy.deepcopy((fs,states,packets));plan=select(fs,states,packets)
    assert plan['request']['first_frame']=='z' and plan['request']['last_frame']=='y'
    assert plan['requested_time_range_ms']==[0,800] and plan['owned_time_range_ms']==[0,1601]
    assert plan['origin']=='platform_input_tail' and plan['policy_version']=='input-tail-v1'
    plan['request']['first_frame']='mutated'
    assert (fs,states,packets)==before


@pytest.mark.parametrize('span,eligible',[(499,False),(500,True),(3000,True),(3001,False)])
def test_selector_inclusive_span(span,eligible):
    fs=[{'frame_id':str(i),'timestamp_ms':t} for i,t in enumerate([0,100,span])]
    assert (select(fs,[hint('1')]) is not None)==eligible


@pytest.mark.parametrize('last',['frame-001','frame-006','unknown',None,[]])
def test_selector_edges_and_invalid_hints_skip(last):
    assert select(frames(6),[hint(last)]) is None


def test_selector_largest_eligible_before_ranking_and_description_independent():
    fs=frames(70)
    for f in fs:f['timestamp_ms']*=4
    fs[41]['timestamp_ms']+=200
    states=[hint('frame-010'),hint('frame-041')]
    plan=select(fs,states)
    assert plan['packet_id']=='packet-002' and plan['requested_time_range_ms']==[16000,17000]
    assert select(fs,[{**s,'description':'different source filename target keyboard answer'} for s in states])==plan
    ps=vt.make_packets(fs,[]);ps[1]['frames']=[f for f in ps[1]['frames'] if f['frame_id']!='frame-042']
    assert select(fs,states,ps)['packet_id']=='packet-001'
    ps[0]['owned_frame_ids']=['frame-010']  # Neither locator owned.
    assert select(fs,states,ps) is None


def test_selector_context_cut_nonempty_and_owner_required():
    fs=frames(6)
    for f in fs:f['timestamp_ms']*=4
    ps=vt.make_packets(fs,[])
    ps[0].update(owned_start_ms=1200,owned_end_ms=1600,owned_frame_ids=['frame-003'])
    assert select(fs,[hint('frame-003')],ps) is None
    ps[0]['owned_frame_ids']=['frame-003','frame-004']
    plan=select(fs,[hint('frame-003')],ps)
    assert plan['decoded_time_range_ms']==[1200,1600]
    ps[0]['owned_start_ms']=1600
    assert select(fs,[hint('frame-003')],ps) is None


@pytest.fixture
def engine(monkeypatch):
    from task_server.services import ui_video_windows as windows
    fs=frames(80)
    for f in fs:f['timestamp_ms']*=4
    # The overview must be a validated subset; use an actual overview anchor.
    tail=vt.overview_frames(fs)[len(vt.overview_frames(fs))//2]['frame_id']
    state=hint(tail);calls=[];loads=[];diagnostics=[];records=[];mutation=[]
    def run(skill,payload,**kw):
        calls.append((kw['version'],copy.deepcopy(payload)))
        if kw['version']=='audit':
            return dict(checks=[dict(event_id=p['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for p in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        raw=dict(states=[state] if kw['version']=='overview' else [],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if kw['version']=='observe' and payload['packet_id']=='packet-001':raw['events']=[event(['frame-001','frame-002'])]
        for fn in mutation:fn(kw['version'],payload,raw)
        return raw
    def loader(request,capacity,**kw):
        loads.append((copy.deepcopy(request),capacity,kw))
        left=next(f['timestamp_ms'] for f in fs if f['frame_id']==request['first_frame'])
        return [{'frame_id':f'new-{len(loads)}-{i}', 'timestamp_ms':left+i+1} for i in range(capacity)]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def observe(**kw):
        return vt.observe_trajectory({'media':{'frames':fs,'duration_ms':33000}},None,lambda:False,
            skill_runner=run,revisit_bounded=loader,business_timeline={},diagnostics=diagnostics,
            record=lambda name,value:records.append((name,copy.deepcopy(value))),**kw)
    return locals()


def test_enabled_first_owner_observe_augmented_once_no_extra_calls_or_proof(engine):
    h=engine;result=h['observe'](dense_input_tail_sampling=True)
    plan=select(h['fs'],[h['state']]);owner=plan['packet_id']
    assert len(h['loads'])==1 and h['loads'][0][1]==20
    observations=[p for v,p in h['calls'] if v=='observe']
    assert len(observations)==3
    original=vt.make_packets(h['fs'],[])
    for p,old in zip(observations,original):
        assert p['owned_time_range_ms']==[old['owned_start_ms'],old['owned_end_ms']]
        assert all(f in p['frames'] for f in old['frames'])
        assert p['context_frame_ids']==old['context_frame_ids']
        assert sum(f['frame_id'].startswith('new-') for f in p['frames'])==(20 if p['packet_id']==owner else 0)
    audit=result['trajectory_audit'];assert audit['corrections_used']==0
    assert audit['dense_input_tail']['status']=='frames_added'
    assert audit['revisit_attempts'][0]['origin']=='platform_input_tail'
    assert result['uncertainties']==[]
    for d in h['diagnostics']:
        if d.get('origin')=='platform_input_tail':assert not {'call','stage','proof_ref','receipt_ref'} & d.keys()


def test_direct_default_off_and_false_parity(engine):
    h=engine;first=h['observe']();assert h['loads']==[] and 'dense_input_tail' not in first['trajectory_audit']
    second=h['observe'](dense_input_tail_sampling=False)
    assert first['events']==second['events'] and h['loads']==[]


@pytest.mark.parametrize('bad',[None,1,'true',{}])
def test_flag_malformed_fails_before_dispatch(engine,bad):
    with pytest.raises(ValueError,match='video_dense_input_tail_invalid'):engine['observe'](dense_input_tail_sampling=bad)
    assert engine['calls']==[]


def test_no_new_charged_but_not_semantic_question_or_exhausted_model_feedback(engine):
    h=engine
    def empty(*a,**kw):h['loads'].append(True);return []
    # Replace only the injected bounded callback through the public engine seam.
    result=vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
        skill_runner=h['run'],revisit_bounded=empty,business_timeline={},dense_input_tail_sampling=True,diagnostics=h['diagnostics'])
    assert len(h['loads'])==1 and result['uncertainties']==[]
    assert result['trajectory_audit']['revisit_attempts'][0]['status']=='no_new_frames'
    later=[p for v,p in h['calls'] if v=='observe' and p['packet_id']=='packet-003'][0]
    assert later['acquisition_budget']['remaining_revisit_attempts']==3
    assert not any('exhausted_revisit_ranges' in p for _,p in h['calls'])


@pytest.mark.parametrize('remaining,acquire',[(270.5,True),(270.499,False)])
def test_deadline_reserve_equality_and_below(engine,monkeypatch,remaining,acquire):
    monkeypatch.setattr(vt.time,'monotonic',lambda:100.)
    h=engine;out=h['observe'](dense_input_tail_sampling=True,engine_deadline=100+remaining)
    assert bool(h['loads'])==acquire
    assert out['uncertainties']==[]
    if not acquire:assert out['trajectory_audit']['dense_input_tail']['reason']=='engine_deadline'


def test_actual_manifest_durable_in_diagnostics_on_later_failure(engine):
    h=engine
    def fail_after(stage,payload,raw):
        if stage=='observe' and payload['packet_id']=='packet-003':raise ValueError('later_failure')
    h['mutation'].append(fail_after)
    with pytest.raises(ValueError,match='later_failure'):h['observe'](dense_input_tail_sampling=True)
    acquired=[d for d in h['diagnostics'] if d['kind']=='acquisition' and d.get('origin')=='platform_input_tail']
    assert len(acquired)==1
    assert len(acquired[0]['attempt']['new_frames'])==20
    assert acquired[0]['attempt']['owned_time_range_ms']==select(h['fs'],[h['state']])['owned_time_range_ms']


@pytest.mark.parametrize('business,bounded',[(None,True),({},False)])
def test_enabled_requires_business_and_bounded_loader(engine,business,bounded):
    h=engine
    with pytest.raises(ValueError,match='video_dense_input_tail_invalid'):
        vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
            skill_runner=h['run'],revisit_bounded=h['loader'] if bounded else None,
            business_timeline=business,dense_input_tail_sampling=True)
    assert h['calls']==[] and h['loads']==[]


@pytest.mark.parametrize('failure',[ValueError('video_cancelled'),ValueError('source_changed'),TimeoutError('decode_timeout')])
def test_automatic_loader_failures_remain_fatal(engine,failure):
    h=engine
    def fail(*a,**kw):raise failure
    with pytest.raises(type(failure),match=str(failure)):
        vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
            skill_runner=h['run'],revisit_bounded=fail,business_timeline={},dense_input_tail_sampling=True)
    assert not any(v=='observe' and p['packet_id']=='packet-002' for v,p in h['calls'])


@pytest.mark.parametrize('bad',[[{'frame_id':'frame-001','timestamp_ms':15000}],
                               [{'frame_id':'new','timestamp_ms':1}],None])
def test_bad_decode_fatal_before_owner_observe(engine,bad):
    h=engine
    with pytest.raises(ValueError,match='video_model_invalid'):
        vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
            skill_runner=h['run'],revisit_bounded=lambda *a,**kw:bad,business_timeline={},dense_input_tail_sampling=True)
    assert not any(v=='observe' and p['packet_id']=='packet-002' for v,p in h['calls'])


@pytest.mark.parametrize('counts,acquire,reason',[
    ([12,12],True,None),([13,12],False,'new_frame_capacity'),
    ([0,0,0],False,'revisit_attempts'),([0,0],True,None),
    ([20,20,20,4],False,'revisit_attempts'),
])
def test_prior_model_requests_priority_no_reservation_and_shared_caps(engine,counts,acquire,reason):
    h=engine;requests=[{'first_frame':f'frame-{i*3+2:03d}','last_frame':f'frame-{i*3+3:03d}',
                       'question':f'original unresolved {i}'} for i in range(len(counts))]
    seen=[]
    def mutate(stage,payload,raw):
        if stage=='observe' and payload['packet_id']=='packet-001' and len(seen)<len(counts):
            raw['revisit']=[requests[len(seen)]]
        if stage=='observe' and payload['packet_id']=='packet-002':
            # Later answer-derived hints must not replace the frozen winner.
            raw['states']=[hint('frame-060')]
    def loader(request,capacity,**kw):
        seen.append(copy.deepcopy(request));index=len(seen)-1
        n=counts[index] if index<len(counts) else 20
        assert capacity>=n
        left=next(f['timestamp_ms'] for f in h['fs'] if f['frame_id']==request['first_frame'])
        return [{'frame_id':f'extra-{index}-{i}','timestamp_ms':left+i+1} for i in range(n)]
    h['mutation'].append(mutate)
    out=vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
        skill_runner=h['run'],revisit_bounded=loader,business_timeline={},dense_input_tail_sampling=True,
        diagnostics=h['diagnostics'])
    assert seen[:len(counts)]==requests and len(seen)==len(counts)+int(acquire)
    assert len(out['trajectory_audit']['revisit_attempts'])<=4
    assert sum(len(a['new_frames']) for a in out['trajectory_audit']['revisit_attempts'])<=64
    for q,n in zip(requests,counts):
        if n==0:assert q['question'] in out['uncertainties']
    plan=out['trajectory_audit']['dense_input_tail']
    if not acquire:assert plan['status']=='not_acquired' and plan['reason']==reason


@pytest.mark.parametrize('slots,acquire',[(20,True),(19,False)])
def test_optional_image_slots_exact_twenty(engine,monkeypatch,slots,acquire):
    h=engine;owner=select(h['fs'],[h['state']])['packet_id']
    size=len(next(p for p in vt.make_packets(h['fs'],[]) if p['packet_id']==owner)['frames'])
    monkeypatch.setattr(vt,'video_stage_image_limit',lambda *a:size+slots)
    out=h['observe'](dense_input_tail_sampling=True)
    assert bool(h['loads'])==acquire
    if not acquire:assert out['trajectory_audit']['dense_input_tail']['reason']=='request_image_capacity'


@pytest.mark.parametrize('timeline',[True,[], 'timeline'])
def test_enabled_rejects_malformed_timeline(engine,timeline):
    h=engine
    with pytest.raises(ValueError,match='video_dense_input_tail_invalid'):
        vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
            skill_runner=h['run'],revisit_bounded=h['loader'],business_timeline=timeline,dense_input_tail_sampling=True)
    assert h['calls']==[]


def test_model_exhaustion_after_automatic_keeps_full_deferred_question(engine):
    h=engine;count=[];question=('original model question '*60).rstrip()
    def mutate(stage,payload,raw):
        if stage=='observe' and payload['packet_id']=='packet-002':
            i=len(count)
            raw['revisit']=[dict(first_frame=f'frame-{34+i*2:03d}',last_frame=f'frame-{35+i*2:03d}',question=question)]
    def loader(request,capacity,**kw):
        count.append(request)
        return []
    h['mutation'].append(mutate)
    # Continue with no claims on this packet: existing empty/deferred logic must preserve questions.
    out=vt.observe_trajectory({'media':{'frames':h['fs'],'duration_ms':33000}},None,lambda:False,
        skill_runner=h['run'],revisit_bounded=loader,business_timeline={},dense_input_tail_sampling=True,
        diagnostics=h['diagnostics'])
    assert len(count)==4
    assert question in out['uncertainties']
    deferred=out['trajectory_audit']['deferred_revisit_requests']
    assert len(deferred)==1 and deferred[0]['request']['question']==question
    assert deferred[0]['reason']=='revisit_attempts'
    assert 'origin' not in deferred[0]
    assert all(p['acquisition_budget']['remaining_revisit_attempts']>=0 for v,p in h['calls'] if v=='observe')


def test_augmented_assets_fail_closed_before_provider_without_dropping_originals(engine,monkeypatch):
    from task_server.services import ui_video_windows as windows
    h=engine;loaded=[]
    def load(fs,*a):
        loaded.append(copy.deepcopy(fs))
        if any(f['frame_id'].startswith('new-') for f in fs):raise ValueError('video_vision_limit')
        return []
    monkeypatch.setattr(windows,'load_assets',load)
    with pytest.raises(ValueError,match='video_vision_limit'):h['observe'](dense_input_tail_sampling=True)
    assert len(h['loads'])==1 and len(loaded[-1])==64  # original owner44 plus20, no truncation
    assert not any(v=='observe' and p['packet_id']=='packet-002' for v,p in h['calls'])
    owner=vt.make_packets(h['fs'],[])[1]
    assert all(f in loaded[-1] for f in owner['frames'])


def test_later_packet_answer_cannot_create_an_initial_plan(engine):
    h=engine;h['state']['kind']='page'
    def mutate(stage,payload,raw):
        if stage=='observe' and payload['packet_id']=='packet-002':raw['states']=[hint(h['state']['last_frame'])]
    h['mutation'].append(mutate)
    out=h['observe'](dense_input_tail_sampling=True)
    assert h['loads']==[] and out['trajectory_audit']['dense_input_tail'] is None
