"""Offline injected contracts for the enrolled business runtime."""
import copy
import pytest
from task_server.services import ui_video_runtime as runtime
from test_ui_video_runtime import frames, fake_receipted_job, worker_env

BUSINESS = 'video-business-first-v1'
TIMELINE = {'scenes':[{'kind':'input','start_ms':0,'end_ms':900,'description':'unverified scene'}],
            'questions':['native unresolved hypothesis']}


def test_mode_exact_flag_and_default_call_stays_bank(monkeypatch):
    for value in ('', 'true', '01', ' 1', '1 '):
        monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST',value)
        assert getattr(runtime,'runtime_mode',lambda:None)() == runtime.MODE
    monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST','1')
    assert runtime.runtime_mode() == BUSINESS
    # Mode was frozen by the worker, never re-read inside an active runtime.
    import inspect
    assert inspect.signature(runtime.run_runtime).parameters['mode'].default is None


@pytest.fixture
def harness(tmp_path,monkeypatch):
    from task_server.services import ui_video_model_jobs as jm, ui_video_windows as windows
    from task_server.services import ui_video_trajectory as trajectory, ui_video_revisit as revisit
    from task_server.services import ui_video_analysis as analysis, ui_video_business_inputs as inputs
    path=tmp_path/'source.mp4';path.write_bytes(b'\0\0\0\x18ftypisom'+b'original')
    calls=[];published=[];instances=[];timeline=copy.deepcopy(TIMELINE);closed=[]
    class Jobs:
        def __init__(self,records,**kw):
            self.accounting=[];self.records=records;self.kw=kw;instances.append(self)
        def __enter__(self):return self
        def __exit__(self,*a):closed.append(True);return False
        def run(self,skill,payload,**kw):
            self.kw['check']();self.kw['reserve']()
            self.accounting.append(fake_receipted_job(self.records,kw['video_stage'],len(self.accounting)))
            calls.append((copy.deepcopy(payload),copy.deepcopy(kw)))
            return copy.deepcopy(timeline) if kw['video_stage']=='timeline' else {}
        def map(self,*a,**kw):pytest.fail('perception producer must not run')
    monkeypatch.setattr(jm,'ModelJobs',Jobs)
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id'],'jpeg':'exact'} for f in fs])
    original=frames(10);extra={'frame_id':'frame-010','timestamp_ms':450}
    def factory(source,root,fs,cancelled):
        assert source==path and fs==original
        def loader(request,capacity,*,owned_time_range_ms):
            assert owned_time_range_ms==(0,1000) and capacity==2
            return [extra]
        return loader
    monkeypatch.setattr(revisit,'make_revisit_loader',factory)
    def engine(doc,root,cancelled,progress,**kw):
        assert kw['dense_input_tail_sampling'] is True
        assert kw['forward_context_issues'] is True
        assert kw['business_timeline']['classification']=='unverified_business_hypotheses'
        assert kw['business_timeline']['timeline']==timeline
        assert kw['business_timeline']['instruction_authority'] is False
        run=kw['skill_runner'];images=windows.load_assets(original,root,cancelled)
        base={'phase':'whole-trajectory','frames':original,'image_frames':original}
        run('ui_video_trajectory',base,version='overview',image_assets=images)
        progress('observing',0,1,[])
        packet={**base,'packet_id':'packet-001','owned_frame_ids':[f['frame_id'] for f in original],
                'context_frame_ids':[],'prior_events':[],'owned_time_range_ms':[0,1000]}
        run('ui_video_trajectory',packet,version='observe',image_assets=images)
        added=kw['revisit_bounded']({'first_frame':'frame-004','last_frame':'frame-005','question':'retain question'},2,owned_time_range_ms=(0,1000))
        augmented=sorted(original+added,key=lambda f:f['timestamp_ms'])
        run('ui_video_trajectory',{**packet,'frames':augmented,'image_frames':augmented,'owned_frame_ids':[f['frame_id'] for f in augmented],'trajectory':[]},version='audit',image_assets=windows.load_assets(augmented,root,cancelled))
        progress('planning',1,1,[{'frame_refs':['frame-010']}])
        return {'media':{**doc['media'],'frames':augmented},'events':[],'uncertainties':['observer unresolved'],
                'multiple_scenarios':False,'trajectory_audit':{'original':True}}
    monkeypatch.setattr(trajectory,'observe_trajectory',engine)
    def consolidate(doc,root,cancelled,observed,first_pass,secrets,progress,*,skill_runner,bounded_business_planning=False,planning_preflight=None):
        assert bounded_business_planning is True and callable(planning_preflight)
        planning_preflight([({}, {})])
        assert doc['trajectory_audit']=={'original':True} and first_pass==[]
        assert 'business_timeline' not in doc
        skill_runner('ui_video_steps',{'phase':'whole-consolidate'},version='consolidate')
        skill_runner('ui_video_observe',{'phase':'whole-observe'},version='outcome')
        return {'events':[],'multiple_scenarios':False,'uncertainties':['observer unresolved']}
    monkeypatch.setattr(analysis,'consolidate_observed_frames',consolidate)
    def recover(result,doc,root,cancelled,*,skill_runner):
        skill_runner('ui_video_observe',{'phase':'whole-observe'},version='inputs')
        return result
    monkeypatch.setattr(inputs,'recover_business_inputs',recover)
    doc={'media':{'frames':original,'duration_ms':1000}}
    def run(check=lambda:None,budget=None):
        return runtime.run_runtime(doc,tmp_path,path,check,lambda **kw:published.append(copy.deepcopy(kw)),budget=budget,mode=BUSINESS)
    return locals()


def test_business_reuses_jobs_original_trajectory_and_tail_without_bank(harness):
    h=harness;budget=runtime.RuntimeBudget(0);result=h['run'](budget=budget)
    stages=[kw['video_stage'] for _,kw in h['calls']]
    assert stages==['timeline','overview','grounding','audit','consolidate','outcome','inputs']
    timeline_payload,options=h['calls'][0]
    assert set(timeline_payload)=={'phase','video'} and options['image_assets'][0]['mime']=='video/mp4'
    assert all(options[k]==v for k,v in {'timeout':120,'retry_count':0,'repair_invalid_json':False,'max_tokens':10000}.items())
    grounding,opts=h['calls'][2]
    assert grounding['image_frames']==h['original'] and 'perception_bank' not in grounding
    assert opts['image_assets']==[{'name':f['frame_id'],'jpeg':'exact'} for f in h['original']]
    assert opts['output_schema']==runtime._semantic_schema(grounding)
    audit,opts=h['calls'][3]
    assert not {'business_timeline','perception_bank','perception_mode','perception_frame_ids'} & audit.keys()
    assert len(opts['image_assets'])==11
    assert budget.reserved==7 and budget.limit==22 and budget.engine_deadline() is None
    assert h['instances'][0].kw['deadline']==budget.deadline and h['closed']==[True,True]
    private=result['video_runtime_private'];assert private['mode']==BUSINESS
    assert private['business_timeline']['timeline']==TIMELINE
    assert private['business_timeline']['proof_ref'] and private['business_timeline']['receipt_ref']
    assert result['uncertainties']==['observer unresolved']  # native questions are private hypotheses
    assert result['video_runtime_summary']['original_windows']==0
    for pub in h['published']:
        if pub.get('raw_events'):assert h['extra'] in pub['media']['frames']


@pytest.mark.parametrize('bad',[
    {'scenes':[],'questions':[]},
    {'scenes':[{**TIMELINE['scenes'][0],'end_ms':1001}],'questions':[]},
    {'scenes':[{**TIMELINE['scenes'][0],'description':'  '}],'questions':[]},
    {'scenes':[{**TIMELINE['scenes'][0],'start_ms':10},TIMELINE['scenes'][0]],'questions':[]},
])
def test_invalid_timeline_stops_before_trajectory_but_preserves_receipts(harness,bad):
    h=harness;h['timeline'].clear();h['timeline'].update(bad)
    with pytest.raises(ValueError,match='video_timeline_output_invalid'):h['run']()
    assert [kw['video_stage'] for _,kw in h['calls']]==['timeline']
    assert h['closed']==[True,True]
    assert h['published'][-1]['video_runtime_summary']['receipts']==1


@pytest.mark.parametrize('when',['before','after_receipt'])
def test_stale_binding_stops_model_and_publishing(harness,when):
    h=harness
    def check():
        if when=='before' or h['calls']:raise ValueError('video_cancelled')
    with pytest.raises(ValueError,match='video_cancelled'):h['run'](check)
    assert len(h['calls'])==(when=='after_receipt')
    assert all(pub.get('analysis_progress',{}).get('completed')==0 for pub in h['published'])
    assert not any('video_runtime_private' in pub for pub in h['published'])


def test_worker_freezes_business_mode_budget_and_source_selection(worker_env,monkeypatch):
    worker,store,doc,profile=worker_env
    monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST','1')
    seen=[]
    def run(document,workspace,path,check,publish,**kw):
        selection=store.get(doc['source_id'],'u',doc['app_id'])['video_runtime_selection']
        assert selection['mode']==kw['mode']==BUSINESS
        assert kw['budget'].original_count==0
        monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST','0');check()
        seen.append(True)
        return {'media':document['media'],'events':[],'multiple_scenarios':False,'uncertainties':[]}
    monkeypatch.setattr(runtime,'run_runtime',run)
    worker.analyze_video_source(store,doc['source_id'])
    assert seen==[True] and store.get(doc['source_id'],'u',doc['app_id'])['status']=='review_needed'


def test_business_flag_is_allowed_in_private_startup_env(tmp_path,monkeypatch):
    from task_server import config
    monkeypatch.delenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST',raising=False)
    path=tmp_path/'env';path.write_text('UI_VIDEO_RUNTIME_BUSINESS_FIRST=1\n');path.chmod(0o600)
    assert 'UI_VIDEO_RUNTIME_BUSINESS_FIRST' in config.load_startup_env(str(path))['loaded_keys']


def test_real_trajectory_keeps_native_hypotheses_private_and_budget_global(monkeypatch):
    from task_server.services import ui_video_trajectory as vt, ui_video_windows as windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    calls=[];acquisitions=[];diagnostics=[]
    hypothesis={'classification':'unverified_business_hypotheses','instruction_authority':False,'timeline':TIMELINE}
    original=frames(90)
    def run(skill,payload,**kw):
        version=kw['version'];calls.append((version,copy.deepcopy(payload)))
        if version in ('overview','observe'):
            assert payload['business_timeline']==hypothesis
        else:
            assert 'business_timeline' not in payload
        if version=='audit':return {'checks':[{'event_id':p['track']['event_id'],'trigger_verdict':'supported','outcome_verdict':'supported','question':''} for p in payload['trajectory']], 'issues':[],'multiple_scenarios':False,'revisit':[]}
        raw={'states':[],'events':[],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
        if version=='observe' and payload['packet_id']=='packet-001' and not acquisitions:
            raw['revisit']=[{'first_frame':'frame-001','last_frame':'frame-003','question':'actual action order?'}]
        if version=='observe' and payload['packet_id']=='packet-001':
            from test_ui_video_trajectory import event
            raw['events']=[event(['frame-001','frame-002'])]
        return raw
    def load(request,capacity,*,owned_time_range_ms):
        acquisitions.append((copy.deepcopy(request),capacity,owned_time_range_ms))
        return [{'frame_id':'frame-090','timestamp_ms':150}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':{'frames':original,'duration_ms':9001}},None,lambda:False,
        skill_runner=run,revisit_bounded=load,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,
        diagnostics=diagnostics,business_timeline=hypothesis)
    observations=[p for v,p in calls if v=='observe'];audits=[p for v,p in calls if v=='audit']
    first=observations[0]['acquisition_budget']
    assert first=={'remaining_revisit_attempts':4,'remaining_new_frames':64,'packet_image_slots':128-len(observations[0]['frames']),
                   'max_requests_per_response':2,'max_frames_per_request':20,'max_request_span_ms':3000}
    for payload in observations[1:]+audits:
        assert payload['acquisition_budget']['remaining_revisit_attempts']==3
        assert payload['acquisition_budget']['remaining_new_frames']==63
        assert payload['acquisition_budget']['packet_image_slots']==128-len(payload['frames'])
    assert result['uncertainties']==[]
    assert acquisitions[0][0]['question']=='actual action order?'
    assert result['trajectory_audit']['revisit_attempts'][0]['new_frames']==[{'frame_id':'frame-090','timestamp_ms':150}]
    assert any(d['kind']=='acquisition' for d in diagnostics)
    assert hypothesis['timeline']['questions']==['native unresolved hypothesis']


def test_business_budget_refusal_does_not_decode_or_confirm(monkeypatch):
    from task_server.services import ui_video_trajectory as vt, ui_video_windows as windows
    calls=[];loads=[];diagnostics=[]
    def run(skill,payload,**kw):
        if kw['version']=='overview':return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        calls.append(copy.deepcopy(payload))
        # Distinct real ranges, so no successful-decode/no_new_frames dedup applies.
        i=len(loads)+1
        return dict(states=[],events=[],checks=[],uncertainties=['preserve actual observer question'],multiple_scenarios=False,
                    revisit=[{'first_frame':f'frame-{i:03d}','last_frame':f'frame-{i+1:03d}','question':'real unresolved action'}])
    def load(request,capacity,**kw):loads.append(request);return []
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    with pytest.raises(ValueError,match='video_model_invalid'):
        vt.observe_trajectory({'media':{'frames':frames(10),'duration_ms':1001}},None,lambda:False,
            skill_runner=run,revisit_bounded=load,diagnostics=diagnostics,business_timeline={'classification':'unverified_business_hypotheses','timeline':TIMELINE})
    assert len(loads)==4
    assert [p['acquisition_budget']['remaining_revisit_attempts'] for p in calls]==[4,3,2,1,0,0,0,0]
    deferred=[d for d in diagnostics if d['kind']=='acquisition_deferred']
    assert len(deferred)==1 and deferred[0]['reason']=='revisit_attempts'
    assert calls[5]['deferred_revisit_requests'][0]['request']['question']=='real unresolved action'
    assert diagnostics[-1]['kind']=='rejection' and diagnostics[-1]['reason']=='audit_shape'
    assert sum(d['kind']=='acquisition' for d in diagnostics)==4


def test_business_renderer_bounds_hypotheses_and_acquisition_without_changing_legacy():
    from task_server.services.ai_skill_service import render_ai_skill_prompt
    base={'phase':'whole-trajectory'}
    legacy=render_ai_skill_prompt('ui_video_trajectory',base,version='observe')
    business=render_ai_skill_prompt('ui_video_trajectory',{**base,'business_timeline':TIMELINE,
        'acquisition_budget':{'remaining_revisit_attempts':0}},version='observe')
    assert '【业务时间线核验】' not in legacy
    assert '【业务时间线核验】' in business
    assert 'native questions' in business and 'acquisition_budget' in business
    audit=render_ai_skill_prompt('ui_video_trajectory',{'acquisition_budget':{}},version='audit')
    assert '【剩余取证容量】' in audit and '【业务时间线核验】' not in audit


def test_tail_failure_retains_native_provenance_and_cleanup(harness,monkeypatch):
    from task_server.services import ui_video_business_inputs as inputs
    from task_server.services.ui_video_records import RecordStore
    h=harness
    def fail(*a,**kw):raise ValueError('video_required_action_missing')
    monkeypatch.setattr(inputs,'recover_business_inputs',fail)
    with pytest.raises(ValueError,match='video_required_action_missing'):h['run']()
    private=h['published'][-1]['video_runtime_private']
    assert private['business_timeline']['timeline']==TIMELINE and h['closed']==[True,True]
    with RecordStore.open_snapshot(h['tmp_path']/'model-records',private['records']) as records:
        assert records.read(private['business_timeline']['proof_ref'])['stage']=='timeline'
        assert records.read(private['business_timeline']['receipt_ref'])['content']=='{}'
        rows=[records.read(ref) for ref in records.read(private['accounting_ref'])['refs']]
        assert all(row['cleanup'] for row in rows)


def test_original_fixture_grounding_uses_existing_continuation_and_binder_contract():
    import hashlib
    import json
    from pathlib import Path
    from task_server.services.ai_skill_service import normalize_model_json
    from task_server.services import ui_video_trajectory as vt
    from task_server.services.ui_video_stage_policy import validate_stage_output
    fixture=Path(__file__).parent/'fixtures/ui_video/run10-ordering.json'
    frozen=json.loads(fixture.read_text());replies={}
    for entry in frozen['entries']:
        logical={k:entry[k] for k in ('entry','kind','value')}
        assert hashlib.sha256(json.dumps(logical,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==entry['sha256']
        replies[entry['entry']]=normalize_model_json(entry['value']['content'])
    fs=frozen['media']['frames']
    original=[f for f in fs if f['frame_id'] not in {f'frame-{i:03d}' for i in range(300,323)}]
    packets=vt.make_packets(original,replies[78]['states'])
    first={**packets[0],'frames':fs,'owned_frame_ids':[f['frame_id'] for f in fs],'prior_events':[]}
    first_scope={**first,'frames':[f for f in fs if f in packets[0]['frames'] or (f not in original and packets[0]['owned_start_ms']<=f['timestamp_ms']<packets[0]['owned_end_ms'])]}
    first_scope['owned_frame_ids']=[f['frame_id'] for f in first_scope['frames']]
    validate_stage_output(replies[86],runtime._semantic_schema(first_scope))
    events,tracks=vt.bind_packet_events(replies[86]['events'],first,fs,[],[])
    assert events[-1]['action']=='aiTap'
    prior=[{**t,'event':e} for t,e in zip(tracks,events)]
    # Raw96 illegally resumes an earlier input over the actual intervening tap.
    second={**packets[1],'frames':[f for f in fs if f in packets[1]['frames'] or (f not in original and packets[1]['owned_start_ms']<=f['timestamp_ms']<packets[1]['owned_end_ms'])],'prior_events':prior}
    with pytest.raises(ValueError,match='continuation_of'):
        validate_stage_output(replies[96],runtime._semantic_schema(second))


def test_business_multi_packet_edit_keyboard_confirmation_and_navigation_stay_separate(monkeypatch):
    from test_ui_video_executor_repair import Session
    from test_ui_video_trajectory import event
    def mutate(stage,payload,raw,session):
        if stage!='observe':return
        if payload['packet_id']=='packet-001':
            raw['events']=[event(['frame-026','frame-032'],'aiInput')]
        elif payload['packet_id']=='packet-002':
            continuation=event(['frame-033','frame-040'],'aiInput')
            continuation['continuation_of']='packet-001:event-0'
            keyboard=event(['frame-042','frame-043']);keyboard['target']='收起键盘'
            confirm=event(['frame-044','frame-045']);confirm['target']='确认按钮'
            navigation=event(['frame-046','frame-047']);navigation.update(target='下一步按钮',edit_boundary='navigation')
            raw['events']=[continuation,keyboard,confirm,navigation]
        else:raw['events']=[]
    session=Session(monkeypatch,mutate,count=90)
    result=session.run(business_timeline={'classification':'unverified_business_hypotheses','timeline':TIMELINE})
    assert [e['action'] for e in result['events']]==['aiInput','aiTap','aiTap','aiTap']
    assert [e['target'] for e in result['events']][1:]==['收起键盘','确认按钮','下一步按钮']
    assert len(result['trajectory_audit']['tracks'][0]['fragments'])==2
    assert all('business_timeline' not in payload for stage,payload in session.calls if stage=='audit')


def test_business_context_does_not_weaken_positive_trigger_locks(monkeypatch):
    from test_ui_video_executor_repair import Session
    def mutate(stage,payload,raw,session):
        if stage=='audit':raw['checks'][0].update(outcome_verdict='contradicted',question='actual outcome unresolved')
        if stage=='observe' and payload.get('audit_feedback'):raw['events']=[]
    session=Session(monkeypatch,mutate,count=6)
    with pytest.raises(ValueError) as caught:
        session.run(business_timeline={'classification':'unverified_business_hypotheses','timeline':TIMELINE})
    assert caught.value.trajectory_reason=='audit_positive_trigger_exhausted'
    assert [stage for stage,payload in session.calls]==['overview','observe','audit','observe','observe']


def test_business_cannot_inherit_producer_allowance(harness):
    with pytest.raises(ValueError,match='video_runtime_budget_invalid'):
        harness['run'](budget=runtime.RuntimeBudget(38))
    assert not harness['calls']


def test_unknown_mode_is_rejected_before_jobs(harness):
    h=harness
    with pytest.raises(ValueError,match='video_runtime_mode_invalid'):
        runtime.run_runtime(h['doc'],h['tmp_path'],h['path'],lambda:None,lambda **kw:None,mode='public-choice')
    assert not h['calls']


def test_business_bounded_allocation_private_links_each_physical_status_and_proof(harness,monkeypatch):
    from task_server.services import ui_video_analysis as analysis
    h=harness
    def consolidate(doc,root,cancelled,observed,*args,skill_runner,**options):
        assert options['bounded_business_planning'] is True
        options['planning_preflight']([({},{}),({},{})])
        for group in range(2):
            scope={'group_index':group,'group_count':2,'owned_candidate_indexes':[group]}
            trace={'planning_pass':1,'planning_group':scope}
            skill_runner('ui_video_steps',{'phase':'whole-consolidate','planning_scope':scope},
                         version='consolidate',runtime_trace=trace)
            trace['planning_gate_failure']='video_reference_invalid'
        return observed
    monkeypatch.setattr(analysis,'consolidate_observed_frames',consolidate)
    result=h['run']()
    links=result['video_runtime_private']['bounded_planning']
    assert len(links)==2 and [p['planning_group']['group_index'] for p in links]==[0,1]
    assert all(p['planning_pass']==1 and p['status_ref'] and p['proof_ref'] and p['receipt_ref'] for p in links)
    assert links[0]['proof_ref']!=links[1]['proof_ref']
    assert all(p['planning_gate_failure']=='video_reference_invalid' for p in links)


def test_partial_runtime_routes_exact_schema_and_consumes_one_attempt_option(harness,monkeypatch):
    from task_server.services import ui_video_trajectory as trajectory
    from task_server.services.ui_video_stage_policy import validate_video_stage
    from task_server.services.ui_video_model_jobs import encode_request
    from task_server.services.ui_video_stage_receipts import canonical_sha as schema_sha256
    h=harness; observed=[]
    def engine(doc,root,cancelled,progress,**kw):
        assert callable(kw['causal_recheck_preflight']) and callable(kw['causal_recheck_safe_review'])
        progress('observing',0,1,[])
        fs=h['original'][:4]
        claim={'track':{'event_id':'selected','trigger_frame':fs[1]['frame_id'],
                       'frame_refs':[f['frame_id'] for f in fs[1:3]],
                       'outcome':{'frame_refs':[fs[2]['frame_id']]}}}
        context={'track':{'event_id':'declared','trigger_frame':fs[-1]['frame_id'],
                         'frame_refs':[fs[-1]['frame_id'],h['original'][-1]['frame_id']]}}
        p=dict(phase='whole-trajectory',frames=fs,image_frames=fs,reasoning_mode='bounded',
            packet_id='packet-001',trajectory=[claim],context_trajectory=[context],prior_events=[],
            owned_frame_ids=[f['frame_id'] for f in fs],context_frame_ids=[],owned_time_range_ms=[0,1000],
            causal_recheck={'version':'causal-question-recheck-v1','scope':'partial'})
        kw['skill_runner']('ui_video_trajectory',p,version='audit',image_assets=[])
        actual,options=h['calls'][-1]
        observed.append((actual,options))
        assert actual['context_trajectory']==[context] and actual['frames']==fs
        assert options['video_stage']=='audit' and options['model_config']=={'providerId':'qwen_plus','model':'qwen3.8-max'}
        assert 'allow_transport_retry' not in options
        schema=options['output_schema'];props=schema['properties']
        assert props['checks']['items']['properties']['event_id']['enum']==['selected']
        assert props['issues']['items']['properties']['owner_frame']['enum']==p['owned_frame_ids']
        assert props['issues']['items']['properties']['frame_refs']['items']['enum']==p['owned_frame_ids']
        assert actual['reference_contract']['version']=='packet-audit-reference-v1'
        assert 'future_issue_owner_frame_ids' not in actual['reference_contract']
        assert schema==runtime._audit_schema(actual,forward_context_issues=False)
        full={**actual,'owned_time_range_ms':[0,400],'context_frame_ids':[h['original'][-1]['frame_id']],
              'frames':fs+[h['original'][-1]],'image_frames':fs+[h['original'][-1]]}
        assert schema_sha256(schema)!=schema_sha256(runtime._audit_schema(full,forward_context_issues=True))
        assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='b3JpZ2luYWw=') for f in fs]
        assert validate_video_stage('ui_video_trajectory',actual,assets,'audit',schema,version='audit')=='audit'
        assert encode_request('ui_video_trajectory',actual,dict(version='audit',video_stage='audit',image_assets=assets,output_schema=schema),120)
        # The actual safe-review callback inspects durable terminal records and owner health.
        owner=h['instances'][0]
        owner._closed=False;owner._latched=False;owner.active_pids=[]
        owner.cleanup_failures=[];owner.pending_receipts=[]
        assert kw['causal_recheck_safe_review']()
        for attr,value in [('_closed',True),('_latched',True),('active_pids',[123]),
                           ('cleanup_failures',['fixture']),('pending_receipts',[{}])]:
            previous=getattr(owner,attr);setattr(owner,attr,value)
            assert not kw['causal_recheck_safe_review']()
            setattr(owner,attr,previous)
        for key in ('terminal','cleanup','proof_ref','receipt_ref'):
            previous=owner.accounting[-1][key];owner.accounting[-1][key]=False
            assert not kw['causal_recheck_safe_review']()
            owner.accounting[-1][key]=previous
        progress('planning',1,1,[])
        return {'media':doc['media'],'events':[],'uncertainties':[],'multiple_scenarios':False,
                'trajectory_audit':{'original':True}}
    monkeypatch.setattr(trajectory,'observe_trajectory',engine)
    h['run']()
    assert len(observed)==1
