import copy
import pytest
from task_server.services.ui_video_causal_recheck import prepare, combine


def sample():
    fs=[{'frame_id':f'f{i}', 'timestamp_ms':i*100} for i in range(10)]
    def claim(eid,action,kind,refs):
        return {'track':{'event_id':eid,'action':action,'target':'visible target','value':'',
            'trigger_frame':refs[0],'trigger_kind':kind,'continuation_of':'',
            'frame_refs':refs,'required_frame_refs':[refs[0],refs[-1]],
            'allowed_frame_refs':refs,'outcome':{'frame_refs':[refs[-1]],'observation':'state'}}}
    claims=[claim('a','aiTap','pressed_control',['f1','f2']),
            claim('b','aiWaitFor','automatic',['f2','f3','f4'])]
    packet={'packet_id':'p','frames':fs,'owned_frame_ids':[f['frame_id'] for f in fs],
            'context_frame_ids':[],'owned_start_ms':0,'owned_end_ms':1000}
    audit={'checks':[{'event_id':c['track']['event_id'],'trigger_verdict':'supported',
        'outcome_verdict':'supported','question':''} for c in claims],
        'issues':[{'kind':'unresolved_evidence','owner_frame':'f3',
                   'frame_refs':['f2','f3'],'question':'causal ownership unknown'}],
        'multiple_scenarios':False,'revisit':[]}
    return packet,claims,audit


def test_selector_preserves_causal_predecessor_and_originals():
    p,c,a=sample(); original=copy.deepcopy((p,c,a)); r=prepare(p,c,[],a)
    assert [x['track']['event_id'] for x in r['trajectory']]==['b']
    assert [x['track']['event_id'] for x in r['context_trajectory']]==['a']
    assert [f['frame_id'] for f in r['frames']]==['f0','f1','f2','f3','f4','f5']
    assert (p,c,a)==original
    a['issues']=[]
    assert prepare(p,c,[],a) is None


def test_contradiction_is_not_overwritten_by_new_support():
    _,_,a=sample(); b=copy.deepcopy(a); b['checks']=b['checks'][1:]
    b['checks'][0].update(trigger_verdict='contradicted',question='different causal source')
    once=combine(a,b); again=combine(once,a)
    assert again['checks'][1]['trigger_verdict']=='contradicted'
    assert 'different causal source' in again['checks'][1]['question']
    assert again['issues']==a['issues']




def test_runtime_admission_preserves_limit_and_deadline():
    from task_server.services.ui_video_runtime import RuntimeBudget
    b=RuntimeBudget(0,clock=lambda:0,business=True);b.freeze_packets(9,business=True)
    b.start_engine(business=True);b.reserved=29
    before=(b.limit,b.deadline,b.engine_deadline(),b.reserved)
    assert b.preflight_causal_recheck(2,0)
    assert (b.limit,b.deadline,b.engine_deadline(),b.reserved)==before
    b.reserved=32
    assert not b.preflight_causal_recheck(2,0)
    assert b.limit==38 and b.deadline==4500


def test_partial_does_not_replace_full_audit_and_unclear_stays_review(monkeypatch):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,10)]
    seen=[]
    def runner(name,payload,**options):
        version=options['version'];partial='causal_recheck' in payload
        seen.append((version,partial,copy.deepcopy(payload)))
        if version=='overview':
            return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if version=='observe':
            e=dict(action='aiWaitFor',target='visible state',value='',frame_refs=['f2','f3'],
                observation='state changes',uncertainties=[],trigger_frame='f2',trigger_kind='automatic',
                page_before='p',page_after='p',continuation_of='',edit_boundary='none',
                outcome={'frame_refs':['f3'],'observation':'state visible'})
            return dict(states=[],events=[e],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        checks=[dict(event_id=c['track']['event_id'],trigger_verdict='unclear' if partial else 'supported',
            outcome_verdict='supported',question='causality remains uncertain' if partial else '')
            for c in payload['trajectory']]
        return dict(checks=checks,issues=[dict(kind='unresolved_evidence',owner_frame='f2',
            frame_refs=['f2','f3'],question='mechanism cannot be determined')],multiple_scenarios=False,revisit=[])
    result=vt.observe_trajectory({'media':{'frames':fs,'duration_ms':1000}},None,lambda:False,
        skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,
        business_timeline={},causal_recheck_preflight=lambda *args,**kwargs:True)
    a=result['trajectory_audit']
    assert a['normal_count']==3 and a['causal_recheck_count']==1 and a['total_calls']==4
    assert [(v,p) for v,p,_ in seen]==[('overview',False),('observe',False),('audit',False),('audit',True)]
    assert a['checks'][0]['status']=='needs_review'
    assert 'mechanism cannot be determined' in result['uncertainties']
    partial=seen[-1][2]
    assert not {'state_memory','overview_hypotheses'} & partial.keys()
    assert len(a['packets'])==1 and len(a['causal_rechecks'])==1


def test_refutation_identity_survives_target_and_modality_paraphrase():
    from task_server.services.ui_video_causal_recheck import identity
    _,claims,_=sample(); original=claims[0]['track']; changed=copy.deepcopy(original)
    changed.update(target='renamed equivalent control',trigger_kind='navigation')
    assert identity(original)==identity(changed)
    changed['trigger_frame']='f3'
    assert identity(original)!=identity(changed)


def test_capacity_refusal_and_noneligible_claim_do_not_seed_actions():
    packet,claims,audit=sample()
    claims[1]['track']['action']='aiAssert'
    assert prepare(packet,claims,[],audit) is None
    claims[1]['track']['action']='aiWaitFor'
    claims[1]['track']['frame_refs'].append('absent-original')
    result=prepare(packet,claims,[],audit)
    assert result['status']=='review' and result['reason']=='causal_context_unavailable'
    assert 'trajectory' not in result


def test_partial_refutation_runs_full_observer_and_every_audit_shard(monkeypatch):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,10)]
    seen=[];refuted=False
    def runner(name,payload,**options):
        nonlocal refuted
        version=options['version'];partial='causal_recheck' in payload
        seen.append((version,partial,len(payload.get('trajectory',[]))))
        if version=='overview':
            return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if version=='observe':
            def event(action,kind,refs,target):
                return dict(action=action,target=target,value='',frame_refs=refs,observation='state changes',
                    uncertainties=[],trigger_frame=refs[0],trigger_kind=kind,page_before='p',page_after='p',
                    continuation_of='',edit_boundary='none',outcome={'frame_refs':[refs[-1]],'observation':'state visible'})
            first=event('aiTap' if refuted else 'aiWaitFor','touch' if refuted else 'automatic',['f2','f3'],'first')
            second=event('aiTap','touch',['f6','f7'],'preserved independent target')
            return dict(states=[],events=[first,second],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        checks=[dict(event_id=c['track']['event_id'],trigger_verdict='contradicted' if partial else 'supported',
            outcome_verdict='supported',question='independent trigger visible' if partial else '') for c in payload['trajectory']]
        issues=[] if refuted else [dict(kind='unresolved_evidence',owner_frame='f2',frame_refs=['f2','f3'],question='causal ambiguity')]
        if partial:refuted=True
        return dict(checks=checks,issues=issues,multiple_scenarios=False,revisit=[])
    result=vt.observe_trajectory({'media':{'frames':fs,'duration_ms':1000}},None,lambda:False,
        skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,business_timeline={},
        audit_batcher=lambda claims:[[c] for c in claims],
        causal_recheck_preflight=lambda *args,**kwargs:True)
    assert seen==[('overview',False,0),('observe',False,0),('audit',False,1),('audit',False,1),
                  ('audit',True,1),('observe',False,0),('audit',False,1),('audit',False,1)]
    a=result['trajectory_audit']
    assert (a['normal_count'],a['causal_recheck_count'],a['total_calls'])==(7,1,8)
    assert [e['action'] for e in result['events']]==['aiTap','aiTap']
    assert result['events'][1]['target']=='preserved independent target'
    assert len(a['packets'][0]['audit_shards'])==2 and len(a['causal_rechecks'])==1
    assert 'causal ambiguity' in result['uncertainties']


@pytest.mark.parametrize('healthy',[True,False])
def test_partial_schema_rejection_requires_healthy_owner(monkeypatch,healthy):
    from task_server.services import ui_video_trajectory as vt,ui_video_windows
    from task_server.services.ai_skill_service import SkillSchemaError
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,10)]
    partial_calls=[]
    def runner(name,payload,**options):
        if 'causal_recheck' in payload:
            partial_calls.append(payload)
            raise SkillSchemaError('$.checks','schema rejection fixture')
        version=options['version']
        if version=='audit':
            return dict(checks=[dict(event_id=c['track']['event_id'],trigger_verdict='supported',
                outcome_verdict='supported',question='') for c in payload['trajectory']],
                issues=[dict(kind='unresolved_evidence',owner_frame='f2',frame_refs=['f2','f3'],
                             question='original uncertainty')],multiple_scenarios=False,revisit=[])
        events=[]
        if version=='observe':
            events=[dict(action='aiWaitFor',target='visible state',value='',frame_refs=['f2','f3'],
                observation='state changes',uncertainties=[],trigger_frame='f2',trigger_kind='automatic',
                page_before='p',page_after='p',continuation_of='',edit_boundary='none',
                outcome={'frame_refs':['f3'],'observation':'state visible'})]
        return dict(states=[],events=events,checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
    def run():
        return vt.observe_trajectory({'media':{'frames':fs,'duration_ms':1000}},None,lambda:False,
            skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,business_timeline={},
            causal_recheck_preflight=lambda *args,**kwargs:True,
            causal_recheck_safe_review=lambda:healthy)
    if healthy:
        result=run()
        assert 'original uncertainty' in result['uncertainties']
        assert result['trajectory_audit']['causal_rechecks'][0]['reason']=='causal_format_rejection'
    else:
        with pytest.raises(SkillSchemaError):run()
    assert len(partial_calls)==1


def test_unselected_visible_trigger_retains_unchanged_declaration_context():
    packet,claims,audit=sample()
    # f5 is an immediate neighbor, while this declaration's outcome is not imaged.
    declared=copy.deepcopy(claims[0]); declared['track'].update(event_id='later',
        trigger_frame='f5',frame_refs=['f5','f9'],required_frame_refs=['f5','f9'],
        allowed_frame_refs=['f5','f9'],outcome={'frame_refs':['f9'],'observation':'state'})
    claims.append(declared)
    original=copy.deepcopy((packet,claims,audit))
    result=prepare(packet,claims,[],audit)
    assert result['context_trajectory'][-1]==declared
    assert [c['track']['event_id'] for c in result['trajectory']]==['b']
    assert [f['frame_id'] for f in result['frames']]==['f0','f1','f2','f3','f4','f5']
    assert 'f9' not in result['owned_frame_ids']
    assert (packet,claims,audit)==original


def _controller(monkeypatch, *, count=9, admit=True, partial_mode='unclear', correction='changed', partial_output=None, extra_questions=False, prior_lock=False, diagnostics=None, healthy=True, canonical=False):
    from task_server.services import ui_video_trajectory as vt, ui_video_windows
    from task_server.services.ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *args:[])
    fs=[{'frame_id':f'frame-{i:03d}' if canonical else f'f{i}','timestamp_ms':i*100} for i in range(1,count+1)]
    seen=[]; repaired=set();normal_audits=0
    def runner(name,payload,**options):
        nonlocal normal_audits
        version=options['version']; partial='causal_recheck' in payload
        seen.append((version,partial,copy.deepcopy(payload)))
        pid=payload.get('packet_id')
        if version in {'overview','observe'}:
            events=[]
            if version=='observe':
                refs=payload['owned_frame_ids'][2:4] if count>32 else payload['owned_frame_ids'][1:3]
                fixed=pid in repaired
                events=[dict(action='aiTap' if fixed and correction=='changed' else 'aiWaitFor',
                    target='renamed' if fixed else 'visible state',value='',frame_refs=refs,
                    observation='state changes',uncertainties=[],trigger_frame=refs[0],
                    trigger_kind='touch' if fixed and correction=='changed' else 'automatic',
                    page_before='p',page_after='p',continuation_of='',edit_boundary='none',
                    outcome={'frame_refs':[refs[-1]],'observation':'visible state'})]
                if prior_lock:
                    events[0].update(action='aiTap',trigger_kind='navigation')
                if extra_questions:
                    second=copy.deepcopy(events[0]);second.update(action='aiTap',target='independent',
                        trigger_kind='touch',trigger_frame='f6',frame_refs=['f6','f7'],
                        outcome={'frame_refs':['f7'],'observation':'visible state'})
                    events.append(second)
                if count>32:
                    predecessor=copy.deepcopy(events[0]); prior_refs=payload['owned_frame_ids'][:2]
                    predecessor.update(action='aiTap',trigger_kind='touch',target='preceding control',
                        trigger_frame=prior_refs[0],frame_refs=prior_refs,
                        outcome={'frame_refs':[prior_refs[-1]],'observation':'visible state'})
                    events.insert(0,predecessor)
            return dict(states=[],events=events,checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        refs=payload['trajectory'][0 if extra_questions else -1]['track']['frame_refs']
        issue=dict(kind='unresolved_evidence',owner_frame=refs[0],frame_refs=refs,question='original question '+pid)
        checks=[dict(event_id=c['track']['event_id'],trigger_verdict=partial_mode if partial else 'supported',
                     outcome_verdict='supported',question='partial finding' if partial else '') for c in payload['trajectory']]
        if partial:repaired.add(pid)
        result=dict(checks=checks,issues=[] if partial or pid in repaired else [issue],multiple_scenarios=False,revisit=[])
        if not partial:
            normal_audits+=1
            if prior_lock and normal_audits==1:result['issues'][0]['kind']='missing_action'
            if extra_questions and pid not in repaired:
                result['checks'][1].update(outcome_verdict='unclear',question='other check question')
                result['issues'].append(dict(kind='unresolved_evidence',owner_frame='f6',
                    frame_refs=['f6','f7'],question='unselected issue question'))
        if partial and partial_output is not None:partial_output(result,payload)
        return result
    def run():
        return vt.observe_trajectory({'media':{'frames':fs,'duration_ms':(count+1)*100}},None,lambda:False,
            skill_runner=runner,execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,business_timeline={},
            causal_recheck_preflight=lambda *a,**k:admit,causal_recheck_safe_review=lambda:healthy,diagnostics=diagnostics)
    return run,seen


def test_partial_repair_cannot_clear_original_full_audit_question(monkeypatch):
    run,seen=_controller(monkeypatch,partial_mode='contradicted')
    result=run()
    assert any(q.startswith('original question') for q in result['uncertainties'])
    assert [v for v,p,_ in seen if not p]==['overview','observe','audit','observe','audit']


def test_two_source_rechecks_and_one_per_packet_remain_separate_from_full_coverage(monkeypatch):
    run,seen=_controller(monkeypatch,count=70)
    result=run();a=result['trajectory_audit']
    assert (a['normal_count'],a['causal_recheck_count'],a['total_calls'])==(7,2,9)
    assert len(a['packets'])==3
    assert [n['status'] for n in a['causal_rechecks']]==['checked','checked','review']
    assert len({p['packet_id'] for _,partial,p in seen if partial})==2
    assert [c['audit_scope'] for c in a['calls'] if c['stage']=='audit'].count('packet_complete')==3


def test_global_capacity_refusal_keeps_question_without_partial_dispatch(monkeypatch):
    run,seen=_controller(monkeypatch,admit=False)
    result=run()
    assert not any(p for _,p,_ in seen)
    assert result['trajectory_audit']['causal_rechecks'][0]['reason']=='causal_capacity'
    assert any(q.startswith('original question') for q in result['uncertainties'])


def test_refuted_automatic_claim_cannot_reappear_with_renamed_target(monkeypatch):
    run,seen=_controller(monkeypatch,partial_mode='contradicted',correction='repeat')
    with pytest.raises(ValueError) as error:run()
    assert error.value.trajectory_reason=='causal_refuted_claim_reappeared'
    assert len([1 for _,p,_ in seen if p])==1


def test_runtime_admission_checks_time_future_work_and_invalid_values():
    from task_server.services.ui_video_runtime import RuntimeBudget
    now=[0];b=RuntimeBudget(0,clock=lambda:now[0],business=True)
    b.freeze_packets(9,business=True);b.start_engine(business=True)
    b.reserved=29
    assert b.preflight_causal_recheck(2,1)
    assert not b.preflight_causal_recheck(2,2)
    now[0]=b.engine_deadline()-360
    assert not b.preflight_causal_recheck(2,0)
    assert b.reserved==29 and b.limit==38 and b.deadline==4500
    for args in [(True,0),(1,-1),(1,0,2)]:
        with pytest.raises(ValueError,match='video_causal_capacity_invalid'):b.preflight_causal_recheck(*args)


def test_partial_revisit_is_retained_without_acquisition_or_full_coverage(monkeypatch):
    def request(result,payload):
        refs=payload['trajectory'][0]['track']['frame_refs']
        result['revisit']=[dict(first_frame=refs[0],last_frame=refs[-1],question='unexecuted original request')]
    run,seen=_controller(monkeypatch,partial_output=request)
    result=run();a=result['trajectory_audit']
    assert a['revisit_attempts']==[] and a['normal_count']==3
    assert 'unexecuted original request' in result['uncertainties']
    partial=next(p for _,flag,p in seen if flag)
    assert partial['revisit_context_anchors'] is False and 'acquisition_budget' not in partial
    assert a['calls'][-1]['audit_scope']=='causal_partial'


def test_partial_owner_violation_is_fatal_even_with_healthy_owner(monkeypatch):
    def invalid(result,payload):
        result['issues']=[dict(kind='unresolved_evidence',owner_frame='not-provided',
            frame_refs=['not-provided'],question='invalid ownership')]
    run,seen=_controller(monkeypatch,partial_output=invalid)
    with pytest.raises(ValueError) as error:run()
    assert error.value.trajectory_reason=='audit_issue_owner'
    assert len([1 for _,p,_ in seen if p])==1


def test_image_capacity_refuses_without_authoring_claims():
    packet,claims,audit=sample()
    packet['frames']=[dict(frame_id=f'f{i}',timestamp_ms=i*100) for i in range(40)]
    packet['owned_frame_ids']=[f['frame_id'] for f in packet['frames']]
    claims[1]['track']['allowed_frame_refs']=[f'f{i}' for i in range(1,30)]
    result=prepare(packet,claims,[],audit)
    assert result['status']=='review' and result['reason']=='causal_image_capacity'
    assert 'trajectory' not in result and 'frames' not in result


def test_full_repair_keeps_unselected_full_audit_questions(monkeypatch):
    run,_=_controller(monkeypatch,partial_mode='contradicted',extra_questions=True)
    result=run()
    assert {'other check question','unselected issue question'}<=set(result['uncertainties'])


def test_partial_refutation_cannot_revoke_inherited_normal_repair_lock(monkeypatch):
    run,seen=_controller(monkeypatch,partial_mode='contradicted',prior_lock=True)
    with pytest.raises(ValueError) as error:run()
    assert error.value.trajectory_reason=='causal_prior_lock_conflict'
    assert [(v,p) for v,p,_ in seen]==[('overview',False),('observe',False),('audit',False),
        ('observe',False),('audit',False),('audit',True)]


@pytest.mark.parametrize('completed_calls',[1,3],ids=['normal','partial'])
def test_engine_deadline_is_not_misattributed_to_call_quota(monkeypatch,completed_calls):
    from task_server.services import ui_video_trajectory as vt
    diagnostics=[]
    run,seen=_controller(monkeypatch,diagnostics=diagnostics)
    monkeypatch.setattr(vt.time,'monotonic',lambda:1200 if len(seen)>=completed_calls else 0)
    with pytest.raises(ValueError,match='video_vision_limit'):run()
    refusal=next(d for d in reversed(diagnostics) if d['kind']=='capacity_refusal')
    assert refusal['reason']=='trajectory_deadline'
    assert len(seen)==completed_calls


@pytest.mark.parametrize('field,path',[
    ('owner_frame','$.issues[0].owner_frame'),
    ('frame_refs','$.issues[0].frame_refs[1]'),
    ('event_id','$.checks[0].event_id'),
    ('first_frame','$.revisit[0].first_frame'),
    ('last_frame','$.revisit[0].last_frame'),
    ('missing_owner','$.issues[0]'),
    ('shape','$.checks'),
    ('root_shape','$'),
    ('collapsed_owner','$'),
    ('verdict','$.checks[0].trigger_verdict'),
])
@pytest.mark.parametrize('healthy',[True,False])
def test_real_stage_schema_conversion_keeps_reference_failures_fatal(monkeypatch,tmp_path,field,path,healthy):
    import json
    from task_server.services import ai_skill_service as ai
    from task_server.services.ui_video_runtime import _audit_schema
    from task_server.services.ui_video_model_jobs import _schema_path
    failures=[]
    def actual_skill(result,payload):
        refs=payload['trajectory'][0]['track']['frame_refs']
        issue=dict(kind='unresolved_evidence',owner_frame=refs[0],frame_refs=list(refs),question='unverified question')
        if field in {'owner_frame','frame_refs','missing_owner'}:
            result['issues']=[issue]
            if field=='missing_owner':del issue['owner_frame']
            elif field=='frame_refs':issue[field][-1]='not-provided'
            else:issue[field]='not-provided'
        elif field=='event_id':result['checks'][0][field]='not-provided'
        elif field in {'first_frame','last_frame'}:
            request=dict(first_frame=refs[0],last_frame=refs[-1],question='unexecuted request')
            request[field]='not-provided';result['revisit']=[request]
        elif field=='root_shape':del result['multiple_scenarios']
        elif field=='shape':result['checks']='invalid array shape'
        elif field=='collapsed_owner':
            result['issues']=[copy.deepcopy(issue) for _ in range(31)]
            result['issues'][-1]['owner_frame']='not-provided'
        else:result['checks'][0]['trigger_verdict']='invalid-verdict'
        # Mock only raw transport. Production admission, stage output validator,
        # JSON parsing and VideoStageOutputError -> SkillSchemaError all execute.
        monkeypatch.setattr(ai,'ai_gateway_skill_content',lambda *a,**k:json.dumps(result))
        assets=[dict(name=f['frame_id'],timestamp_ms=f['timestamp_ms'],mime='image/jpeg',base64='YQ==')
                for f in payload['frames']]
        try:
            if field=='collapsed_owner':
                from test_ui_video_model_jobs import Gateway,manager
                gateway=Gateway(json.dumps(result))
                try:
                    with manager(tmp_path,gateway) as owner:
                        owner.run('ui_video_trajectory',payload,version='audit',video_stage='audit',
                            image_assets=assets,output_schema=_audit_schema(payload),retry_count=0)
                finally:gateway.close()
            else:
                ai.run_ai_skill('ui_video_trajectory',payload,version='audit',video_stage='audit',
                    image_assets=assets,output_schema=_audit_schema(payload),retry_count=0)
        except ai.SkillSchemaError as error:
            failures.append(error)
            assert error.schema_path==path
            assert _schema_path(error.schema_path,'ui_video_trajectory')==path
            assert 'not-provided' not in error.model_feedback and 'invalid-verdict' not in error.model_feedback
            raise
        pytest.fail('production schema did not reject malformed audit')
    run,seen=_controller(monkeypatch,partial_output=actual_skill,healthy=healthy,canonical=True)
    expected_review=healthy and field in {'shape','verdict'}
    if expected_review:
        result=run()
        assert result['trajectory_audit']['causal_rechecks'][0]['reason']=='causal_format_rejection'
        assert any(q.startswith('original question') for q in result['uncertainties'])
    else:
        with pytest.raises(ai.SkillSchemaError):run()
    assert len(failures)==1 and len([1 for _,partial,_ in seen if partial])==1
