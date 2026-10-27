"""Evidence ownership, edit identity and independent review contracts."""
import copy
import pytest
from task_server.services import ui_video_trajectory as vt


def audit_reply(raw):
    """Build the current audit fixture contract from concise mock decisions."""
    out={'checks':[],'issues':[],'multiple_scenarios':raw['multiple_scenarios'],'revisit':raw.get('revisit',[])}
    for c in raw['checks']:
        out['checks'].append({'event_id':c['event_id'],'trigger_verdict':'supported',
                             'outcome_verdict':'supported' if c['status']=='confirmed' else 'unclear','question':c['question']})
    for key in ('states','events'):
        if raw[key]:out[key]=raw[key]  # Intentional malformed stage fixture.
    for q in raw['uncertainties']:
        out['issues'].append({'kind':'missing_action','owner_frame':'frame-080','frame_refs':['frame-080'],'question':q})
    return out


def frames(n=90):
    return [{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,n+1)]


def event(refs, action='aiTap', **kw):
    return dict(action=action,target='备注输入框' if action=='aiInput' else '进入按钮',value='',
                frame_refs=refs,observation='目标页面标题可见',uncertainties=[],trigger_frame=refs[0],
                trigger_kind='edit' if action=='aiInput' else 'touch',page_before='page-a',page_after='page-a',
                continuation_of='',edit_boundary='none',outcome={'frame_refs':[refs[-1]],'observation':'目标页面标题可见'},**kw)


def test_packets_cover_every_original_once_and_bound_context():
    fs=frames(299); packets=vt.make_packets(fs,[])
    assert [r for p in packets for r in p['owned_frame_ids']]==[f['frame_id'] for f in fs]
    assert all(2<=len(p['frames'])<=60 for p in packets)
    assert all(set(p['owned_frame_ids']).isdisjoint(p['context_frame_ids']) for p in packets)
    assert all(p['frames']==sorted(p['frames'],key=lambda f:f['timestamp_ms']) for p in packets)


def test_packet_boundary_keeps_overview_edit_round_whole_if_it_fits():
    fs=frames(80)
    states=[{'state_id':'page-a','first_frame':'frame-026','last_frame':'frame-047','kind':'input','description':'搜索编辑'}]
    p=vt.make_packets(fs,states)[0]
    assert set(f'frame-{i:03d}' for i in range(26,48))<=set(p['owned_frame_ids'])


def test_overview_rejects_unknown_or_reversed_evidence():
    fs=frames()
    item={'state_id':'page-a','first_frame':'frame-001','last_frame':'frame-002','kind':'page','description':'首页'}
    assert vt.validate_states([item],fs)==[item]
    for first,last in [('unknown','frame-002'),('frame-003','frame-002')]:
        with pytest.raises(ValueError):vt.validate_states([{**item,'first_frame':first,'last_frame':last}],fs)


def test_trigger_must_belong_to_core_and_result_must_not_precede_trigger():
    fs=frames();p=vt.make_packets(fs,[])[1]
    raw=event(['frame-033','frame-034'])
    out,tracks=vt.bind_packet_events([raw],p,fs,[],[])
    assert len(out)==1 and tracks[0]['event_id'].startswith(p['packet_id'])
    for changed in [{**raw,'trigger_frame':'frame-032'}, {**raw,'outcome':{'frame_refs':['frame-032'],'observation':'标题可见'}}]:
        with pytest.raises(ValueError):vt.bind_packet_events([changed],p,fs,[],[])


def test_structured_continuation_merges_input_without_parsing_chinese_word_order():
    fs=frames();ps=vt.make_packets(fs,[])
    first=event(['frame-026','frame-032'],'aiInput')
    out,tracks=vt.bind_packet_events([first],ps[0],fs,[],[])
    second=event(['frame-033','frame-047'],'aiInput')
    second.update(continuation_of=tracks[0]['event_id'],observation='退格键点击和候选词选择，最终文字状态可见')
    joined,history=vt.bind_packet_events([second],ps[1],fs,out,tracks)
    assert len(joined)==1 and joined[0]['start_ms']==2600 and joined[0]['end_ms']==4700
    assert set(['frame-026','frame-032','frame-033','frame-047'])<=set(joined[0]['frame_refs'])
    assert len(history[0]['fragments'])==2
    assert joined[0]['value']==''


@pytest.mark.parametrize('change',[{'target':'另一个输入框'},{'page_before':'page-b'},{'edit_boundary':'submission'},
                                  {'action':'aiTap','trigger_kind':'touch'},{'continuation_of':'unseen'}])
def test_continuation_cannot_cross_field_page_submission_or_unknown_identity(change):
    fs=frames();ps=vt.make_packets(fs,[])
    out,tracks=vt.bind_packet_events([event(['frame-026','frame-032'],'aiInput')],ps[0],fs,[],[])
    second=event(['frame-033','frame-047'],'aiInput');second['continuation_of']=tracks[0]['event_id'];second.update(change)
    with pytest.raises(ValueError):vt.bind_packet_events([second],ps[1],fs,out,tracks)


def test_intervening_business_tap_prevents_continuation():
    fs=frames();ps=vt.make_packets(fs,[])
    out,tracks=vt.bind_packet_events([event(['frame-026','frame-030'],'aiInput'),event(['frame-031','frame-032'])],ps[0],fs,[],[])
    second=event(['frame-033','frame-047'],'aiInput');second['continuation_of']=tracks[0]['event_id']
    with pytest.raises(ValueError):vt.bind_packet_events([second],ps[1],fs,out,tracks)


def test_nonempty_input_value_is_not_accepted_as_replay_data():
    fs=frames();p=vt.make_packets(fs,[])[0];raw=event(['frame-002','frame-004'],'aiInput');raw['value']='sensitive-value'
    with pytest.raises(ValueError):vt.bind_packet_events([raw],p,fs,[],[])


def run_observer(monkeypatch, n=90, continuation=False, missing=False, cancelled=lambda:False, reject_fragment=False, corrections=0, fragment_questions=False):
    from task_server.services import ai_skill_service, ui_video_windows
    calls=[]
    def reply(name,payload,**kwargs):
        assert payload['reasoning_mode']=='bounded'
        assert kwargs['timeout']==120 and kwargs['retry_count']==0
        assert kwargs['respect_global_timeout'] is False
        calls.append((kwargs['version'],copy.deepcopy(payload)))
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kwargs['version']=='observe' and corrections and sum(v=='observe' for v,_ in calls)<=corrections:
            raw['checks']=[{}];return raw
        if kwargs['version']=='observe':
            if payload['packet_id']=='packet-001':
                raw['events']=[event(['frame-026','frame-032'],'aiInput') if continuation else event(['frame-001','frame-002'])]
            elif continuation and payload['packet_id']=='packet-002':
                e=event(['frame-033','frame-047'],'aiInput');e['continuation_of']='packet-001:event-0';raw['events']=[e]
        if kwargs['version']=='observe' and fragment_questions:
            for item in raw['events']:
                if item['action']=='aiInput':item['uncertainties']=['本包末编辑未结束' if payload['packet_id']=='packet-001' else '候选点击未被采样帧捕捉']
        if kwargs['version']=='audit':
            for p in payload['trajectory']:
                t=p['track'];raw['checks'].append(dict(event_id=t['event_id'],status='confirmed',frame_refs=list(dict.fromkeys([t['trigger_frame'],t['outcome']['frame_refs'][-1]])),question=''))
            if reject_fragment:
                for c in raw['checks']:
                    if c['event_id'].endswith('fragment-2'):c.update(status='needs_review',question='frame-033 触发不明确')
            if missing and payload.get('packet_id')=='packet-003':raw['uncertainties']=['frame-080 存在遗漏的返回操作']
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':{'frames':frames(n),'duration_ms':n*100+1}},None,cancelled)
    return result,calls


def test_audit_independently_reviews_all_original_packets_including_empty(monkeypatch):
    result,calls=run_observer(monkeypatch,missing=True)
    audits=[p for v,p in calls if v=='audit']
    assert [r for p in audits for r in p['owned_frame_ids']]==[f['frame_id'] for f in frames()]
    assert len(audits)==3 and audits[-1]['trajectory']==[]
    assert 'frame-080 存在遗漏的返回操作' in result['uncertainties']
    for p,packet in zip(audits,vt.make_packets(frames(),[])):
        assert p['frames']==packet['frames']


def test_merged_input_audits_each_immutable_fragment_trigger_and_outcome(monkeypatch):
    result,calls=run_observer(monkeypatch,continuation=True)
    reviewed=[p for v,p in calls if v=='audit']
    fragments=[x['track'] for p in reviewed for x in p['trajectory']]
    assert [t['event_id'] for t in fragments]==['packet-001:event-0:fragment-1','packet-001:event-0:fragment-2']
    assert [(t['trigger_frame'],t['outcome']['frame_refs']) for t in fragments]==[('frame-026',['frame-032']),('frame-033',['frame-047'])]
    assert len(result['trajectory_audit']['fragment_checks'])==2
    assert len(result['events'])==1


def test_full_300_frame_orchestrator_stays_within_budget(monkeypatch):
    result,calls=run_observer(monkeypatch,n=300)
    assert len(calls)==21
    assert len(result['trajectory_audit']['calls'])==21
    assert all(len(p['frames'])<=60 for _,p in calls)


def test_cancelled_before_any_model_call(monkeypatch):
    with pytest.raises(ValueError,match='video_cancelled'):
        run_observer(monkeypatch,cancelled=lambda:True)


def test_unconfirmed_continuation_blocks_canonical_event(monkeypatch):
    result,_=run_observer(monkeypatch,continuation=True,reject_fragment=True)
    assert result['trajectory_audit']['checks'][0]['status']=='needs_review'
    assert any('frame-033' in q for q in result['events'][0]['uncertainties'])


def test_300_frames_allow_two_corrections_and_complete_all_audits(monkeypatch):
    result,calls=run_observer(monkeypatch,n=300,corrections=2)
    assert len(calls)==23
    assert len([p for v,p in calls if v=='audit'])==10
    assert result['trajectory_audit']['checks'][0]['status']=='confirmed'


def test_third_invalid_observation_stops_at_correction_limit(monkeypatch):
    with pytest.raises(ValueError,match='video_model_invalid'):
        run_observer(monkeypatch,n=300,corrections=3)


def test_cancellation_after_model_response_stops_without_next_call(monkeypatch):
    count=0
    def cancelled():
        nonlocal count
        count+=1
        return count==2
    with pytest.raises(ValueError,match='video_cancelled'):
        run_observer(monkeypatch,cancelled=cancelled)


def test_entirely_empty_trajectory_still_audits_each_owned_interval(monkeypatch):
    from task_server.services import ai_skill_service, ui_video_windows
    calls=[]
    def reply(name,payload,**kwargs):
        calls.append((kwargs['version'],payload))
        return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    with pytest.raises(ValueError,match='video_model_invalid') as exc:
        vt.observe_trajectory({'media':{'frames':frames(),'duration_ms':9001}},None,lambda:False)
    assert exc.value.trajectory_reason=='empty_trajectory'
    assert len([p for v,p in calls if v=='audit'])==3


def test_requested_original_segment_is_reloaded_without_mutating_source_metadata(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    fs=frames();original=copy.deepcopy(fs);calls=[];loaded=[];asked=False
    def reply(name,payload,**kw):
        nonlocal asked
        calls.append((kw['version'],copy.deepcopy(payload)))
        r=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-001':
            if not asked:
                asked=True;r['revisit']=[dict(first_frame='frame-030',last_frame='frame-033',question='触发处在采样间隙')]
            else:r['events']=[event(['frame-091','frame-032'])]
        if kw['version']=='audit':
            for p in payload['trajectory']:
                t=p['track'];r['checks'].append(dict(event_id=t['event_id'],status='confirmed',frame_refs=[t['trigger_frame'],t['outcome']['frame_refs'][-1]],question=''))
        return r
    def loader(request,capacity):
        loaded.append((request,capacity));return [dict(frame_id='frame-091',timestamp_ms=3150)]
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=fs,duration_ms=9001)},None,lambda:False,revisit=loader)
    assert len(loaded)==1 and loaded[0][1]<=20
    assert result['events'][0]['frame_refs']==['frame-091','frame-032']
    assert dict(frame_id='frame-091',timestamp_ms=3150) in result['media']['frames']
    assert fs==original
    audit=next(p for version,p in calls if version=='audit')
    assert 'frame-091' in audit['owned_frame_ids']


def test_phase_shape_failure_uses_shared_correction_budget(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    calls=[];bad=True
    def reply(name,payload,**kw):
        nonlocal bad
        calls.append(kw['version']);r=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-001':r['events']=[event(['frame-001','frame-002'])]
        if kw['version']=='audit':
            for p in payload['trajectory']:
                t=p['track'];r['checks'].append(dict(event_id=t['event_id'],status='confirmed',frame_refs=['frame-001','frame-002'],question=''))
            if bad:bad=False;r['states']=[dict(state_id='s1',description='复制了输入记忆')]
        return r
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw));monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False)
    assert len(calls)==8 and result['events']


def test_automatic_state_action_cannot_claim_a_touch_or_navigation_trigger():
    fs=frames();p=vt.make_packets(fs,[])[0]
    for kind in ('touch','pressed_control','navigation','edit'):
        raw=event(['frame-001','frame-002'],'aiWaitFor');raw['trigger_kind']=kind
        with pytest.raises(ValueError):vt.bind_packet_events([raw],p,fs,[],[])


def test_supplement_between_original_cores_has_exactly_one_trigger_owner(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    asked=False;audits=[]
    def reply(name,payload,**kw):
        nonlocal asked
        r=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-001':
            if not asked:
                asked=True;r['revisit']=[dict(first_frame='frame-032',last_frame='frame-033',question='边界间隙缺少触发')]
            else:r['events']=[event(['frame-091','frame-033'])]
        if kw['version']=='audit':
            audits.append(copy.deepcopy(payload))
            for p in payload['trajectory']:
                t=p['track'];r['checks'].append(dict(event_id=t['event_id'],status='confirmed',frame_refs=[t['trigger_frame'],t['outcome']['frame_refs'][-1]],question=''))
        return r
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw));monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,
                               revisit=lambda *a:[dict(frame_id='frame-091',timestamp_ms=3250)])
    assert sum('frame-091' in p['owned_frame_ids'] for p in audits)==1
    assert result['events'][0]['start_ms']==3250


def test_revisit_cannot_orphan_pixels_inside_already_processed_time(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    def reply(name,payload,**kw):
        r=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-001':r['events']=[event(['frame-001','frame-002'])]
        if kw['version']=='observe' and payload['packet_id']=='packet-002':r['revisit']=[dict(first_frame='frame-030',last_frame='frame-031',question='已处理区间触发不明确')]
        if kw['version']=='audit':
            for p in payload['trajectory']:
                t=p['track'];r['checks'].append(dict(event_id=t['event_id'],status='confirmed',frame_refs=['frame-001','frame-002'],question=''))
        return r
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw));monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,
                              revisit=lambda *a:[dict(frame_id='frame-091',timestamp_ms=3050)])
    assert getattr(exc.value,'trajectory_reason','')=='revisit_outside_owned_interval'


@pytest.mark.parametrize('prior_question,new_question',[
    ('本包末编辑未结束，最终上屏词与提交方式需在后续包确认',''),
    ('','frame-038到frame-041候选点击未被采样帧捕捉'),
    ('本包末编辑未结束','候选点击未被采样帧捕捉')])
def test_continuation_preserves_questions_without_using_them_as_edit_identity(prior_question,new_question):
    fs=frames();packets=vt.make_packets(fs,[])
    first=event(['frame-026','frame-032'],'aiInput');first['uncertainties']=[prior_question] if prior_question else []
    previous,tracks=vt.bind_packet_events([first],packets[0],fs,[],[])
    second=event(['frame-033','frame-047'],'aiInput');second.update(continuation_of=tracks[0]['event_id'],uncertainties=[new_question] if new_question else [])
    untouched=copy.deepcopy((first,second,previous,tracks))
    merged,history=vt.bind_packet_events([second],packets[1],fs,previous,tracks)
    assert len(merged)==1 and len(history[0]['fragments'])==2
    expected=list(dict.fromkeys(previous[0]['uncertainties']+second['uncertainties']))
    assert merged[0]['uncertainties']==expected
    assert history[0]['fragments']==[first,second]
    assert (first,second,previous,tracks)==untouched


@pytest.mark.parametrize('change',[{'target':'另一个输入框'},{'page_before':'page-b'},
    {'page_after':'page-b'},{'edit_boundary':'submission'},{'edit_boundary':'field_change'},
    {'edit_boundary':'navigation'},{'continuation_of':'unknown'},{'trigger_kind':'uncertain'}])
def test_uncertain_continuations_still_require_all_structural_identity_boundaries(change):
    fs=frames();packets=vt.make_packets(fs,[])
    first=event(['frame-026','frame-032'],'aiInput');first['uncertainties']=['编辑未结束']
    previous,tracks=vt.bind_packet_events([first],packets[0],fs,[],[])
    second=event(['frame-033','frame-047'],'aiInput')
    second.update(continuation_of=tracks[0]['event_id'],uncertainties=['候选点击缺帧']);second.update(change)
    with pytest.raises(ValueError):vt.bind_packet_events([second],packets[1],fs,previous,tracks)



def test_confirmed_fragment_audits_do_not_erase_observer_input_questions(monkeypatch):
    result,calls=run_observer(monkeypatch,continuation=True,fragment_questions=True)
    questions=result['events'][0]['uncertainties']
    assert '本包末编辑未结束' in questions and '候选点击未被采样帧捕捉' in questions
    track=result['trajectory_audit']['tracks'][0]
    assert [f['uncertainties'] for f in track['fragments']]==[['本包末编辑未结束'],['候选点击未被采样帧捕捉']]
    assert len(result['trajectory_audit']['fragment_checks'])==2
    # Independent trigger/outcome support is not a resolution of separate questions.
    assert all(c['status']=='confirmed' for c in result['trajectory_audit']['fragment_checks'])


def test_multiple_question_fragments_keep_all_questions_and_input_placeholder():
    fs=frames(120);packets=vt.make_packets(fs,[]);previous=[];tracks=[]
    for i,refs in enumerate([['frame-026','frame-032'],['frame-033','frame-047'],['frame-065','frame-070']]):
        raw=event(refs,'aiInput');raw['uncertainties']=['编辑证据问题'+str(i)]
        if tracks:raw['continuation_of']=tracks[-1]['event_id']
        previous,tracks=vt.bind_packet_events([raw],packets[i],fs,previous,tracks)
    assert len(previous)==1 and len(tracks[0]['fragments'])==3
    assert previous[0]['uncertainties']==['编辑证据问题0','请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件','编辑证据问题1','编辑证据问题2']


@pytest.mark.parametrize('repeat',[False,True])
def test_exhausted_revisit_is_recorded_and_rechecked_without_loop_or_confirmation(monkeypatch,repeat):
    from task_server.services import ai_skill_service,ui_video_windows
    calls=[];loads=[];records=[]
    request=dict(first_frame='frame-001',last_frame='frame-003',question='触发不明确')
    def reply(name,payload,**kw):
        calls.append((kw['version'],copy.deepcopy(payload)))
        if kw['version']=='audit':
            out=dict(checks=[dict(event_id=x['track']['event_id'],trigger_verdict='unclear',outcome_verdict='supported',question='原帧仍不能确认按下') for x in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
            if payload['packet_id']=='packet-001' and (repeat or not payload.get('exhausted_revisit_ranges')):out['revisit']=[request]
            return out
        out=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-001':out['events']=[event(['frame-001','frame-003'])]
        return out
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(req,capacity):loads.append((req,capacity));return []
    result=vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=loader,record=lambda key,value:records.append((key,value)))
    assert len(loads)==1 and len(calls)==9
    assert result['trajectory_audit']['checks'][0]['status']=='needs_review'
    assert '独立证据复核：原帧仍不能确认按下' in result['events'][0]['uncertainties']
    assert any('触发不明确' in q for q in result['uncertainties'])
    attempts=[v for k,v in records if k.startswith('revisit-') and v.get('status')=='no_new_frames']
    assert len(attempts)==1 and attempts[0]['new_frames']==[]
    later=[p for v,p in calls if p.get('exhausted_revisit_ranges')]
    assert {p['exhausted_revisit_ranges'][0]['status'] for p in later}=={'no_new_frames'}
    assert all(p['frames']==frames()[:38] for p in later if p.get('packet_id')=='packet-001')


def test_empty_revisit_attempts_consume_global_four_attempt_budget(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    asked=0;loaded=[]
    def reply(name,payload,**kw):
        nonlocal asked
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe':
            asked+=1;raw['revisit']=[dict(first_frame=f'frame-{asked:03d}',last_frame=f'frame-{asked+2:03d}',question='需核实不同区间')]
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(request,capacity):loaded.append(request);return []
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=loader)
    assert exc.value.trajectory_reason=='revisit_budget' and len(loaded)==4


@pytest.mark.parametrize('invalid',[None,{},'empty',()])
def test_nonlist_revisit_result_remains_invalid(monkeypatch,invalid):
    from task_server.services import ai_skill_service,ui_video_windows
    def reply(name,payload,**kw):
        return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,
                    **({'revisit':[dict(first_frame='frame-001',last_frame='frame-003',question='需核实')]} if kw['version']=='observe' else {}))
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=lambda *a:invalid)
    assert exc.value.trajectory_reason=='revisit_result'


@pytest.mark.parametrize('issue',[
    dict(kind='missing_action',owner_frame='frame-040',frame_refs=['frame-040'],question='上下文不能作为本包新动作'),
    dict(kind='missing_action',owner_frame='frame-002',frame_refs=['unknown'],question='未知帧'),
    dict(kind='missing_action',owner_frame='frame-002',frame_refs=['frame-002']),
])
def test_repeated_exhausted_request_cannot_bypass_audit_issue_validation(monkeypatch,issue):
    from task_server.services import ai_skill_service,ui_video_windows
    audit_calls=0
    def reply(name,payload,**kw):
        nonlocal audit_calls
        if kw['version']=='audit':
            audit_calls+=1
            return dict(checks=[dict(event_id=x['track']['event_id'],trigger_verdict='unclear',outcome_verdict='supported',question='证据不明确') for x in payload['trajectory']],
                        issues=[] if audit_calls==1 else [issue],multiple_scenarios=False,
                        revisit=[dict(first_frame='frame-001',last_frame='frame-003',question='触发需核实')])
        return dict(states=[],events=[event(['frame-001','frame-003'])] if kw['version']=='observe' else [],checks=[],uncertainties=[],multiple_scenarios=False)
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':dict(frames=frames(20),duration_ms=2001)},None,lambda:False,revisit=lambda *a:[])
    assert exc.value.trajectory_reason in {'audit_issue_owner','frame_reference','audit_issue_shape'}
