"""Synthetic scheduling controls; authentic run20 captures remain failed."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from task_server.services import ui_video_trajectory as vt, ui_video_runtime as runtime
from test_ui_video_audit_batches import Engine
from test_ui_video_trajectory import frames
from test_ui_video_business_runtime import harness
from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1


class BusinessEngine(Engine):
    def run(self, **options):
        return vt.observe_trajectory({'media': {'frames': frames(self.count), 'duration_ms': self.count*100+1}},
            None, lambda: False, skill_runner=self.model, audit_batcher=self.batches,
            revisit=self.revisit, record=lambda name,data:self.records.append((name,copy.deepcopy(data))),
            business_timeline={'classification':'unverified_business_hypotheses'}, **options)


def request(first='frame-034',last='frame-035'):
    return dict(first_frame=first,last_frame=last,question='synthetic unresolved evidence')


@pytest.mark.parametrize('shard',[0,1])
@pytest.mark.parametrize('empty',[False,True])
def test_business_audit_empty_acquisition_reuses_candidate_and_shard(monkeypatch,shard,empty):
    loads=[];target=1 if empty else shard
    def mutate(stage,payload,raw,calls):
        if empty and stage=='observe' and payload['packet_id']=='packet-002':raw['events']=[]
        if stage!='audit' or payload['packet_id']!='packet-002':return
        which=0 if empty or len(payload['trajectory'])==6 else 1
        if (empty or which==target):
            raw['revisit']=[request()]
            raw['issues']=[dict(kind='unresolved_evidence',owner_frame='frame-034',frame_refs=['frame-034'],question='original unresolved question')]
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:loads.append(a) or [])
    result=engine.run();audit=engine.audits()
    assert sum(s=='observe' and p.get('packet_id')=='packet-002' for s,p,_ in engine.calls)==1
    assert len(loads)==1 and len(audit)==(2 if empty else 3)
    repeated=[(p,a) for p,a in audit if p.get('exhausted_revisit_ranges')]
    assert len(repeated)==(2 if not empty and shard==0 else 1)
    original=next((p,a) for p,a in audit if p['trajectory']==repeated[0][0]['trajectory'])
    assert repeated[0][0]['frames']==original[0]['frames'] and repeated[0][1]==original[1]
    assert repeated[0][0]['trajectory']==original[0]['trajectory']
    assert result['trajectory_audit']['revisit_attempts'][0]['status']=='no_new_frames'
    assert any('original unresolved question' in q for q in result['uncertainties'])
    assert result['trajectory_audit']['corrections_used']==0


@pytest.mark.parametrize('order',[False,True])
def test_mixed_audit_acquisitions_any_new_frame_forces_reobservation(monkeypatch,order):
    loads=[]
    def mutate(stage,payload,raw,calls):
        if stage=='audit' and payload['packet_id']=='packet-002' and not loads:
            raw['revisit']=[request(),request('frame-036','frame-037')]
    def load(req,capacity):
        loads.append(req)
        return [dict(frame_id='frame-091',timestamp_ms=3450 if not order else 3650)] if (len(loads)==1)!=order else []
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=load);engine.run()
    observations=[p for s,p,_ in engine.calls if s=='observe' and p.get('packet_id')=='packet-002']
    assert len(observations)==2 and len(observations[1]['frames'])==len(observations[0]['frames'])+1
    assert len(engine.audits())==3  # restart first shard after fresh pixels


def test_observer_no_new_still_reconsiders(monkeypatch):
    loaded=[]
    def mutate(stage,payload,raw,calls):
        if stage=='observe' and payload['packet_id']=='packet-001' and not loaded:raw['revisit']=[request('frame-001','frame-002')]
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:loaded.append(a) or [])
    engine.run()
    assert sum(s=='observe' and p.get('packet_id')=='packet-001' for s,p,_ in engine.calls)==2


def test_business_deadline_derived_from_source_not_engine_start():
    now=[100.];budget=runtime.RuntimeBudget(0,clock=lambda:now[0]);now[0]=200.
    budget.start_engine(business=True)
    assert budget.engine_deadline()==budget.deadline-480.5==1419.5
    now[0]=budget.engine_deadline()
    with pytest.raises(TimeoutError):budget.reserve()
    budget.end_engine();budget.reserve()  # tail still source-bound, not an extra480 guarantee
    now[0]=budget.deadline-.5
    with pytest.raises(TimeoutError):budget.reserve()


def test_legacy_deadline_unchanged():
    now=[100.];budget=runtime.RuntimeBudget(1,clock=lambda:now[0]);now[0]=200.;budget.start_engine()
    assert budget.engine_deadline()==1400.
    with pytest.raises(ValueError,match='engine_started'):budget.start_engine()


@pytest.mark.parametrize('value',[float('inf'),float('nan'),True,'4020',4020.001])
def test_standalone_explicit_clock_rejects_invalid_or_unbounded(monkeypatch,value):
    monkeypatch.setattr(vt.time,'monotonic',lambda:0.)
    with pytest.raises(ValueError,match='deadline'):
        BusinessEngine(monkeypatch,count=2).run(engine_deadline=value)


@pytest.mark.parametrize('explicit',[False,True])
def test_actual_trajectory_single_clock_survives_packet_dispatches(monkeypatch,explicit):
    now=[0.];monkeypatch.setattr(vt.time,'monotonic',lambda:now[0])
    engine=BusinessEngine(monkeypatch)
    old=engine.model
    def model(*a,**kw):
        raw=old(*a,**kw);now[0]+=190.;return raw
    engine.model=model
    if explicit:
        result=engine.run(engine_deadline=1560.)
        assert len(result['trajectory_audit']['calls'])==8
    else:
        with pytest.raises(ValueError,match='video_vision_limit'):engine.run()
        assert len(engine.calls)==7


def test_run20_canonical_original_entries_and_terminal_failure():
    data=json.loads((Path(__file__).parent/'fixtures/ui_video_v20_runtime_limits.json').read_text())
    for entry in data['entries']:
        raw=json.dumps({k:v for k,v in entry.items() if k!='sha256'},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
        assert hashlib.sha256(raw).hexdigest()==entry['sha256']
    byid={e['entry']:e for e in data['entries']}
    assert byid[39]['kind']=='proof' and byid[39]['value']['timeout_ms']==82205
    assert byid[59]['value']['cleanup'] is True and byid[59]['value']['admitted'] is False
    assert data['source']['status']=='failed' and data['source']['draft_id']==''


@pytest.mark.parametrize('kind',['missing_action','contradicted'])
def test_no_new_does_not_erase_findings_or_bounded_observation_repair(monkeypatch,kind):
    loaded=[]
    def mutate(stage,payload,raw,calls):
        if stage=='audit' and payload['packet_id']=='packet-002' and not loaded:
            raw['revisit']=[request()]
            if kind=='missing_action':
                raw['issues']=[dict(kind=kind,owner_frame='frame-034',frame_refs=['frame-034'],question='real unresolved finding')]
            else:
                raw['checks'][0].update(outcome_verdict='contradicted',question='real unresolved finding')
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:loaded.append(a) or [])
    result=engine.run(execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1)
    observations=[p for s,p,_ in engine.calls if s=='observe' and p.get('packet_id')=='packet-002']
    assert len(observations)==2 and len(engine.audits())==3
    assert observations[1]['audit_feedback']['positive_trigger_locks']
    assert observations[1]['frames']==observations[0]['frames']
    assert result['trajectory_audit']['corrections_used']==1


def test_runtime_passes_exact_source_deadline_to_trajectory(harness,monkeypatch):
    previous=vt.observe_trajectory;seen=[]
    budget=runtime.RuntimeBudget(0)
    def inspect(*args,**kwargs):
        seen.append(kwargs['engine_deadline'])
        assert kwargs['engine_deadline']==budget.engine_deadline()==budget.deadline-480.5
        return previous(*args,**kwargs)
    monkeypatch.setattr(vt,'observe_trajectory',inspect)
    harness['run'](budget=budget)
    assert len(seen)==1 and budget.engine_deadline() is None


def test_original20_export_dictionary_and_manifest_equality_when_available():
    data=json.loads((Path(__file__).parent/'fixtures/ui_video_v20_runtime_limits.json').read_text())
    export=Path(data['provenance']['export'])
    if not export.exists():pytest.skip('private original export unavailable; canonical pins covered separately')
    assert json.loads((export/'source.json').read_text())==data['source']
    assert hashlib.sha256((export/'source.json').read_bytes()).hexdigest()==data['provenance']['source_sha256']
    entries=[]
    for item in data['provenance']['manifest']:
        raw=(export/item['path']).read_bytes()
        assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']
        entries.extend(json.loads(raw)['entries'])
    assert entries==data['entries'] and len(entries)==60


def test_expired_explicit_clock_refuses_before_dispatch(monkeypatch):
    monkeypatch.setattr(vt.time,'monotonic',lambda:0.)
    engine=BusinessEngine(monkeypatch,count=2)
    with pytest.raises(ValueError,match='video_vision_limit'):engine.run(engine_deadline=-1)
    assert engine.calls==[]


def test_authentic20_replay_stops_at_same_candidate_reaudit_without_source_success(monkeypatch):
    """Original replies only through receipt22; no invented successful remainder."""
    from task_server.services import ui_video_windows
    from task_server.services.ui_video_audit_batches import partition_audit_claims
    data=json.loads((Path(__file__).parent/'fixtures/ui_video_v20_runtime_limits.json').read_text())
    source=data['source'];media=source['media'];all_frames=media['frames']
    originals=[f for f in all_frames if int(f['frame_id'][6:])<=299]
    entries={e['entry']:e for e in data['entries']};receipts=iter(range(4,24,2));calls=[];loaded=[]
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda fs,*a:[dict(name=f['frame_id'],jpeg='synthetic transport only') for f in fs])
    class ReauditReached(Exception):pass
    def run(skill,payload,**kwargs):
        calls.append((kwargs['version'],copy.deepcopy(payload)))
        if len(calls)==11:
            assert kwargs['version']=='audit' and payload['packet_id']=='packet-004'
            previous=calls[-2][1]
            assert payload['trajectory']==previous['trajectory'] and payload['frames']==previous['frames']
            assert payload['exhausted_revisit_ranges'][0]['request']==json.loads(entries[22]['value']['content'])['revisit'][0]
            raise ReauditReached()
        return json.loads(entries[next(receipts)]['value']['content'])
    times={f['frame_id']:f['timestamp_ms'] for f in all_frames}
    def load(req,capacity,*,owned_time_range_ms):
        loaded.append(copy.deepcopy(req));left=times[req['first_frame']];right=times[req['last_frame']]
        return [f for f in all_frames if 300<=int(f['frame_id'][6:])<=339 and left<=f['timestamp_ms']<=right]
    with pytest.raises(ReauditReached):
        vt.observe_trajectory({'media':{**media,'frames':originals}},None,lambda:False,
            skill_runner=run,revisit_bounded=load,audit_batcher=partition_audit_claims,
            execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,business_timeline=source['video_runtime_private']['business_timeline'])
    assert [s for s,p in calls if p.get('packet_id')=='packet-004']==['observe','observe','audit','audit']
    assert len(loaded)==3
    current=calls[-1][1]
    with pytest.raises(ValueError) as rejected:
        vt.validate_audit_issues(json.loads(entries[26]['value']['content'])['issues'],current)
    assert rejected.value.trajectory_reason=='frame_reference'
    assert vt.validate_audit_issues(json.loads(entries[28]['value']['content'])['issues'],current)
    diagnostics=source['video_runtime_private']['trajectory_diagnostics']
    assert [(d['status'],d['new_frame_count']) for d in diagnostics if d['kind']=='acquisition']==[
        ('frames_added',20),('frames_added',20),('no_new_frames',0),('frames_added',10)]
    assert [(d['reason'],d['receipt_ref']['entry']) for d in diagnostics if d['kind']=='rejection']==[('frame_reference',26)]
    assert diagnostics[-1]['reason']=='model_job_deadline' and diagnostics[-1]['call']==19


@pytest.mark.parametrize('limit',['deadline','corrections'])
def test_no_reset_after_exhaustion_or_schema_correction(monkeypatch,limit):
    now=[0.];monkeypatch.setattr(vt.time,'monotonic',lambda:now[0]);loaded=[];diagnostics=[]
    def mutate(stage,payload,raw,calls):
        now[0]+=200 if limit=='deadline' else 0
        if stage=='audit' and payload['packet_id']=='packet-002':
            if not loaded:raw['revisit']=[request()]
            elif limit=='corrections':raw['checks']=[]
    engine=BusinessEngine(monkeypatch,mutate=mutate,revisit=lambda *a:loaded.append(a) or [])
    with pytest.raises(ValueError) as refused:
        engine.run(engine_deadline=1560.,diagnostics=diagnostics)
    assert len(engine.calls)==8
    assert len(loaded)==1
    if limit=='deadline':
        assert str(refused.value)=='video_vision_limit' and now[0]==1600
        assert diagnostics[-1]['kind']=='capacity_refusal'
        assert diagnostics[-1]['reason']=='trajectory_deadline'
    else:
        assert str(refused.value)=='video_model_invalid' and now[0]==0
        assert refused.value.trajectory_reason=='audit_missing_event'
        assert diagnostics[-1]['kind']=='rejection'
        assert diagnostics[-1]['reason']=='audit_missing_event'
        assert diagnostics[-1]['correction_scope_used']==2
