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
    def loader(request,capacity,*,owned_time_range_ms):
        loaded.append((request,capacity));return [dict(frame_id='frame-091',timestamp_ms=3150)]
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:audit_reply(reply(*a,**kw)) if kw['version']=='audit' else reply(*a,**kw))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=fs,duration_ms=9001)},None,lambda:False,revisit_bounded=loader)
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
                               revisit_bounded=lambda *a,**kw:[dict(frame_id='frame-091',timestamp_ms=3249)])
    assert sum('frame-091' in p['owned_frame_ids'] for p in audits)==1
    assert result['events'][0]['start_ms']==3249


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


@pytest.mark.parametrize('stage',['observe','audit'])
def test_bad_revisit_request_receives_shared_correction_on_same_owned_images(monkeypatch,stage):
    from task_server.services import ai_skill_service,ui_video_windows
    calls=[];loads=[];records=[];invalid=True
    def reply(name,payload,**kw):
        nonlocal invalid
        calls.append((kw['version'],copy.deepcopy(payload)))
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-001':raw['events']=[event(['frame-001','frame-003'])]
        if kw['version']=='audit':raw=audit_reply({**raw,'checks':[dict(event_id=x['track']['event_id'],status='confirmed',frame_refs=[],question='') for x in payload['trajectory']]})
        if kw['version']==stage and invalid:
            invalid=False;raw['revisit']=[dict(first_frame='frame-030',last_frame='frame-035',question='未来上下文需要补帧')]
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(*args):loads.append(args);return []
    result=vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=loader,record=lambda key,value:records.append((key,value)))
    retried=[p for v,p in calls if v==stage and p.get('packet_id')=='packet-001']
    assert len(retried)==2 and retried[0]['frames']==retried[1]['frames']
    assert all(p['owned_time_range_ms']==[100,3250] for p in retried)
    assert 'validation_feedback' in retried[1] and 'owned' in retried[1]['validation_feedback']
    assert loads==[] and len(result['events'])==1
    rejected=[v for k,v in records if k.endswith('contract-rejection')]
    assert rejected[0]['reason']=='revisit_outside_owned_interval'
    assert rejected[0]['requests'][0]['last_frame']=='frame-035'


def test_all_model_revisit_requests_are_prevalidated_before_any_decode(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    calls=[];loads=[]
    def reply(name,payload,**kw):
        calls.append(kw['version'])
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe':raw['revisit']=[dict(first_frame='frame-001',last_frame='frame-003',question='valid'),dict(first_frame='unknown',last_frame='frame-003',question='bad')]
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(*args):loads.append(args);return []
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=loader)
    assert exc.value.trajectory_reason=='revisit_reference'
    assert calls==['overview','observe','observe','observe'] and loads==[]


@pytest.mark.parametrize('message',['video_cancelled','video_vision_limit','video_decoder_missing'])
def test_actual_revisit_resource_or_service_errors_are_not_corrected(monkeypatch,message):
    from task_server.services import ai_skill_service,ui_video_windows
    calls=[]
    def reply(name,payload,**kw):
        calls.append(kw['version'])
        return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,
                    **({'revisit':[dict(first_frame='frame-001',last_frame='frame-003',question='核实')]} if kw['version']=='observe' else {}))
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(*args):raise ValueError(message)
    with pytest.raises(ValueError,match=message):
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=loader)
    assert calls==['overview','observe']


@pytest.mark.parametrize('change,reason',[
    ({'last_frame':'frame-001'},'revisit_span'),
    ({'first_frame':'frame-003','last_frame':'frame-001'},'revisit_span'),
    ({'question':' '},'revisit_question'),
    ({'first_frame':'unknown'},'revisit_reference')])
def test_model_revisit_contract_reasons_are_separate_from_resource_budget(change,reason):
    packet=vt.make_packets(frames(),[])[0]
    request=dict(first_frame='frame-001',last_frame='frame-003',question='核实');request.update(change)
    with pytest.raises(ValueError) as exc:vt.validate_revisit_requests([request],packet)
    assert exc.value.trajectory_reason==reason


def test_revisit_and_schema_errors_share_two_global_corrections_across_stages(monkeypatch):
    from task_server.services import ai_skill_service,ui_video_windows
    calls=[];observations=0;loads=[]
    bad=dict(first_frame='unknown',last_frame='frame-003',question='核实')
    def reply(name,payload,**kw):
        nonlocal observations
        calls.append(kw['version'])
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe':
            observations+=1
            if observations==1:raw['checks']=[{}]
            elif observations==2:raw['revisit']=[bad]
            else:raw['events']=[event(['frame-001','frame-003'])]
        if kw['version']=='audit':raw=dict(checks=[],issues=[],multiple_scenarios=False,revisit=[bad])
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(*args):loads.append(args);return []
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':dict(frames=frames(),duration_ms=9001)},None,lambda:False,revisit=loader)
    assert exc.value.trajectory_reason=='revisit_reference'
    assert calls==['overview','observe','observe','observe','audit'] and loads==[]



def test_revisit_end_boundary_brackets_gap_but_is_not_reassigned_to_current_owner():
    packet=vt.make_packets(frames(),[])[0]
    assert packet['owned_start_ms']==100 and packet['owned_end_ms']==3250
    assert 'frame-033' not in packet['owned_frame_ids'] and 'frame-033' in packet['context_frame_ids']
    with pytest.raises(ValueError):
        vt.validate_revisit_requests([dict(first_frame='frame-032',last_frame='frame-033',question='边界间隙')],packet)
    assert vt.validate_revisit_requests([dict(first_frame='frame-032',last_frame='frame-033',question='边界间隙')],packet,allow_context_anchors=True)is None
    with pytest.raises(ValueError) as exc:
        vt.validate_revisit_requests([dict(first_frame='frame-032',last_frame='frame-034',question='未来上下文')],packet)
    assert exc.value.trajectory_reason=='revisit_outside_owned_interval'


@pytest.mark.parametrize('stage',['observe','audit'])
@pytest.mark.parametrize('prior_reference',[False,True])
def test_malformed_revisit_shape_before_envelope_return_is_corrected_without_stale_raw(monkeypatch,stage,prior_reference):
    from task_server.services import ai_skill_service,ui_video_windows
    failures=([[dict(first_frame='unknown',last_frame='frame-003',question='bad reference')]] if prior_reference else [])+['not a list'];records=[]
    def reply(name,payload,**kw):
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe':raw['events']=[event(['frame-001','frame-003'])]
        if kw['version']=='audit':raw=dict(checks=[dict(event_id=x['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for x in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        if kw['version']==stage and failures:raw['revisit']=failures.pop(0)
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    result=vt.observe_trajectory({'media':dict(frames=frames(20),duration_ms=2001)},None,lambda:False,revisit=lambda *a:[],record=lambda key,value:records.append((key,value)))
    assert len(result['events'])==1
    rejections=[v for k,v in records if k.endswith('contract-rejection')]
    assert len(rejections)==(2 if prior_reference else 1)
    rejection=rejections[-1]
    assert rejection['reason']=='revisit_shape'
    assert 'requests' not in rejection


def capacity_observer(monkeypatch, *, first_count=None, exhausted_first=False, packet_size=44):
    """Exercise real packet expansion; only the AI and image IO are stubbed."""
    from task_server.services import ai_skill_service,ui_video_windows
    original=frames();loads=[];observations=[];raw_replies=[];packets=vt.make_packets(original,[])
    packet=packets[1]
    if packet_size!=44:
        refs={f['frame_id'] for f in packet['frames']}
        packet['frames']+=copy.deepcopy([f for f in original if f['frame_id'] not in refs][:packet_size-44])
        packet['frames'].sort(key=lambda f:f['timestamp_ms'])
        owned=set(packet['owned_frame_ids'])
        packet['context_frame_ids']=[f['frame_id'] for f in packet['frames'] if f['frame_id'] not in owned]
        monkeypatch.setattr(vt,'make_packets',lambda *a:copy.deepcopy(packets))
    requests=[dict(first_frame='frame-033',last_frame='frame-035',question='第一处独立触发证据'),
              dict(first_frame='frame-036',last_frame='frame-038',question='第二处独立触发证据')]
    def reply(name,payload,**kw):
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if kw['version']=='observe' and payload['packet_id']=='packet-002':
            observations.append(copy.deepcopy(payload))
            raw['events']=[event(['frame-033','frame-034'])]
            if len(observations)==1:raw['revisit']=[requests[1]] if exhausted_first else requests
            elif exhausted_first and len(observations)==2:raw['revisit']=requests
            raw_replies.append(copy.deepcopy(raw))
        if kw['version']=='audit':
            raw=dict(checks=[dict(event_id=x['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for x in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        return raw
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',reply)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def loader(request,capacity):
        loads.append((copy.deepcopy(request),capacity))
        if exhausted_first and request==requests[1]:return []
        count=first_count if first_count is not None and len(loads)==1 else capacity
        start=3300 if request==requests[0] else 3600
        offset=0 if request==requests[0] else 20
        return [dict(frame_id=f'frame-{91+offset+i:03d}',timestamp_ms=start+i+1) for i in range(count)]
    return original,packets,requests,loads,observations,raw_replies,loader


@pytest.mark.parametrize('first_count,capacities,final_size',[(None,[8,8],60),(3,[8,13],60),(0,[8,16],60)])
def test_two_revisit_windows_share_capacity_and_roll_unused_slots_forward(monkeypatch,first_count,capacities,final_size):
    original,packets,requests,loads,observations,replies,loader=capacity_observer(monkeypatch,first_count=first_count)
    result=vt.observe_trajectory({'media':dict(frames=original,duration_ms=9001)},None,lambda:False,revisit=loader)
    assert [capacity for _,capacity in loads]==capacities
    assert [request for request,_ in loads]==requests
    assert len(observations[-1]['frames'])==final_size
    packet=result['trajectory_audit']['packets'][1]['packet']
    assert all(f in packet['frames'] for f in packets[1]['frames'])
    assert all(f in result['media']['frames'] for f in original)
    owners=[r for p in result['trajectory_audit']['packets'] for r in p['packet']['owned_frame_ids']]
    assert len(owners)==len(set(owners))==len(result['media']['frames'])
    assert set(owners)=={f['frame_id'] for f in result['media']['frames']}
    assert packet['context_frame_ids']==packets[1]['context_frame_ids']
    assert result['trajectory_audit']['packets'][1]['raw']==replies[-1]
    assert replies[-1]['events'][0]['frame_refs']==['frame-033','frame-034']
    assert observations[0]['frames']==packets[1]['frames']
    if first_count==0:
        assert any(requests[0]['question'] in q for q in result['uncertainties'])


def test_exhausted_revisit_neither_reserves_slots_nor_decodes_or_consumes_attempt_again(monkeypatch):
    original,_,requests,loads,observations,_,loader=capacity_observer(monkeypatch,exhausted_first=True)
    result=vt.observe_trajectory({'media':dict(frames=original,duration_ms=9001)},None,lambda:False,revisit=loader)
    assert loads==[(requests[1],16),(requests[0],16)]
    assert len(result['trajectory_audit']['revisit_attempts'])==2
    assert len(observations[-1]['frames'])==60
    assert any(requests[1]['question'] in q for q in result['uncertainties'])


@pytest.mark.parametrize('packet_size',[57,58,59,60])
def test_two_revisit_windows_require_minimum_capacity_before_any_loader(monkeypatch,packet_size):
    original,_,_,loads,_,_,loader=capacity_observer(monkeypatch,packet_size=packet_size)
    with pytest.raises(ValueError,match='video_vision_limit'):
        vt.observe_trajectory({'media':dict(frames=original,duration_ms=9001)},None,lambda:False,revisit=loader)
    assert loads==[]


@pytest.fixture
def captured_ordering(monkeypatch):
    """Immutable receipts, original PTS and frozen ownership from failed run10."""
    import hashlib
    import json
    from pathlib import Path
    from task_server.services.ai_skill_service import normalize_model_json
    from task_server.services import ui_video_windows
    frozen=json.loads((Path(__file__).parent/'fixtures/ui_video/run10-ordering.json').read_text())
    replies={}
    for entry in frozen['entries']:
        logical={k:entry[k] for k in ('entry','kind','value')}
        assert hashlib.sha256(json.dumps(logical,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==entry['sha256']
        replies[entry['entry']]=normalize_model_json(entry['value']['content'])
    original=[f for f in frozen['media']['frames'] if f['frame_id'] not in {f'frame-{i:03d}' for i in range(300,323)}]
    packets=vt.make_packets(original,replies[78]['states'])[:2]
    for p in packets:
        extras=[f for f in frozen['media']['frames'] if f not in original and p['owned_start_ms']<=f['timestamp_ms']<p['owned_end_ms']]
        p['frames']=sorted(p['frames']+extras,key=lambda f:f['timestamp_ms'])
        p['owned_frame_ids']+= [f['frame_id'] for f in extras]
    assert [len(p['frames']) for p in packets]==[60,60]  # captured53+7 and44+16
    monkeypatch.setattr(vt,'make_packets',lambda *a:copy.deepcopy(packets))
    monkeypatch.setattr(vt,'overview_frames',lambda fs:fs)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    return frozen,replies,packets


@pytest.mark.parametrize('receipt,reason,expanded',[(90,'event_evidence',False),(90,'event_evidence',True),(96,'input_continuation',True)])
def test_captured_invalid_claim_with_revisit_corrects_before_loader(captured_ordering,receipt,reason,expanded):
    frozen,replies,packets=captured_ordering
    if not expanded:
        packets[1]['frames']=[f for f in packets[1]['frames'] if int(f['frame_id'].split('-')[1])<307]
        packets[1]['owned_frame_ids']=[fid for fid in packets[1]['owned_frame_ids'] if int(fid.split('-')[1])<307]
    calls=[];loads=[];diagnostics=[]
    def runner(skill,payload,**kw):
        calls.append((kw['version'],copy.deepcopy(payload)))
        if kw['version']=='overview':return copy.deepcopy(replies[78])
        if kw['version']=='audit':return {'checks':[dict(event_id=c['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for c in payload['trajectory']], 'issues':[], 'multiple_scenarios':False,'revisit':[]}
        return copy.deepcopy(replies[86 if payload['packet_id']=='packet-001' else receipt])
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':frozen['media']},None,lambda:False,skill_runner=runner,
            revisit_bounded=lambda *a,**k:loads.append(a) or [],diagnostics=diagnostics)
    assert getattr(exc.value,'trajectory_reason',None)==reason
    assert not loads
    rejected=[r for r in diagnostics if r['kind']=='rejection']
    assert [r['reason'] for r in rejected]==[reason]*3
    assert all(r['packet_id']=='packet-002' for r in rejected)
    assert len([p for v,p in calls if v=='observe' and p['packet_id']=='packet-002'])==3
    assert reason in calls[-1][1]['validation_feedback']
    assert all(r['questions_ref']['revisit_count']==len(replies[receipt]['revisit']) for r in rejected)


def test_malformed_audit_revisit_corrects_before_acquisition(monkeypatch):
    from task_server.services import ui_video_windows
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    loads=[];calls=[]
    def runner(skill,payload,**kw):
        calls.append((kw['version'],copy.deepcopy(payload)))
        raw={'states':[],'events':[],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
        if kw['version']=='observe':raw['events']=[event(['frame-001','frame-002'])]
        if kw['version']=='audit':return {'checks':[dict(event_id='wrong',trigger_verdict='supported',outcome_verdict='supported',question='')], 'issues':[], 'multiple_scenarios':False,'revisit':[dict(first_frame='frame-001',last_frame='frame-002',question='pending')]}
        return raw
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':{'frames':frames(10),'duration_ms':1001}},None,lambda:False,
            skill_runner=runner,revisit=lambda *a:loads.append(a) or [])
    assert getattr(exc.value,'trajectory_reason',None)=='audit_identity'
    assert not loads
    assert len([v for v,p in calls if v=='audit'])==3


@pytest.mark.parametrize('capacity_stop',[True,False])
def test_capacity_refusal_differs_from_successful_empty_decode(captured_ordering,capacity_stop):
    frozen,replies,packets=captured_ordering
    if not capacity_stop:
        packets[1]['frames']=[f for f in packets[1]['frames'] if int(f['frame_id'].split('-')[1])<307]
        packets[1]['owned_frame_ids']=[f for f in packets[1]['owned_frame_ids'] if int(f.split('-')[1])<307]
    loads=[];diagnostics=[];counts={}
    def runner(skill,payload,**kw):
        if kw['version']=='overview':return {'states':[],'events':[],'checks':[],'uncertainties':[], 'multiple_scenarios':False}
        if kw['version']=='audit':return {'checks':[dict(event_id=c['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for c in payload['trajectory']], 'issues':[], 'multiple_scenarios':False,'revisit':[]}
        if payload['packet_id']=='packet-001':return copy.deepcopy(replies[86])
        pid=payload['packet_id'];counts[pid]=counts.get(pid,0)+1
        return {'states':[],'events':[],'checks':[],'uncertainties':['unverified observation'], 'multiple_scenarios':False,
                'revisit':copy.deepcopy(replies[96]['revisit']) if pid=='packet-002' and counts[pid]==1 else []}
    def run():return vt.observe_trajectory({'media':frozen['media']},None,lambda:False,skill_runner=runner,
        revisit_bounded=lambda *a,**k:loads.append(a) or [],diagnostics=diagnostics)
    if capacity_stop:
        with pytest.raises(ValueError,match='video_vision_limit'):run()
        row=next(r for r in diagnostics if r['kind']=='capacity_refusal')
        assert row['reason']=='request_image_capacity'
        assert row['packet_frames']==60 and row['capacity']==0 and row['revisits']==0
        assert row['pending_request_indexes']==[0,1]
        assert not loads
        assert not any(r.get('status')=='no_new_frames' for r in diagnostics)
    else:
        owners=set(packets[0]['owned_frame_ids'])|set(packets[1]['owned_frame_ids'])
        frozen['media']['frames']=[f for f in frozen['media']['frames'] if f['frame_id'] in owners]
        result=run()
        assert len(loads)==2
        assert len(result['trajectory_audit']['revisit_attempts'])==2
        assert all(r['status']=='no_new_frames' for r in result['trajectory_audit']['revisit_attempts'])
        assert not any(r['kind']=='capacity_refusal' for r in diagnostics)
    responses=[r for r in diagnostics if r['kind']=='response']
    assert responses and all(r['questions_ref']['status']=='unverified_model_claims' for r in responses)
    assert all('raw_response' not in r for r in diagnostics)


def test_captured_44_plus_16_then_two_pending_requests_preserve_capacity_counters(captured_ordering):
    frozen,replies,packets=captured_ordering
    all_frames=copy.deepcopy(frozen['media']['frames'])
    for p in packets:
        p['frames']=[f for f in p['frames'] if int(f['frame_id'].split('-')[1])<300]
        p['owned_frame_ids']=[fid for fid in p['owned_frame_ids'] if int(fid.split('-')[1])<300]
    assert [len(p['frames']) for p in packets]==[53,44]
    frozen['media']['frames']=[f for f in all_frames if int(f['frame_id'].split('-')[1])<300]
    counts={};loads=[];diagnostics=[]
    def runner(skill,payload,**kw):
        if kw['version']=='overview':return copy.deepcopy(replies[78])
        if kw['version']=='audit':return {'checks':[dict(event_id=c['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for c in payload['trajectory']], 'issues':[], 'multiple_scenarios':False,'revisit':[]}
        pid=payload['packet_id'];counts[pid]=counts.get(pid,0)+1
        if pid=='packet-001':return copy.deepcopy(replies[80 if counts[pid]==1 else 86])
        return dict(states=[],events=[],checks=[],uncertainties=['unverified pending'],multiple_scenarios=False,
                    revisit=copy.deepcopy(replies[90 if counts[pid]==1 else 96]['revisit']))
    times={f['frame_id']:f['timestamp_ms'] for f in all_frames}
    def loader(request,capacity,*,owned_time_range_ms):
        loads.append((request,capacity))
        return [f for f in all_frames if int(f['frame_id'].split('-')[1])>=300 and
                times[request['first_frame']]<=f['timestamp_ms']<=times[request['last_frame']] and
                owned_time_range_ms[0]<=f['timestamp_ms']<owned_time_range_ms[1]][:capacity]
    with pytest.raises(ValueError,match='video_vision_limit'):
        vt.observe_trajectory({'media':frozen['media']},None,lambda:False,skill_runner=runner,
            revisit_bounded=loader,diagnostics=diagnostics)
    row=diagnostics[-1]
    assert row['kind']=='capacity_refusal' and row['reason']=='request_image_capacity'
    assert row['packet_frames']==60 and row['revisits']==3 and row['acquired_frames']==23
    assert row['pending_request_indexes']==[0,1] and row['questions_ref']['revisit_count']==2
    assert len(loads)==3 and all(r.get('status')!='no_new_frames' for r in diagnostics)


@pytest.mark.parametrize('stage',['observe','audit'])
def test_valid_uncertain_claim_and_audit_with_revisit_still_acquire(monkeypatch,stage):
    from task_server.services import ui_video_windows
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    counts={};loads=[];diagnostics=[]
    def runner(skill,payload,**kw):
        version=kw['version'];counts[version]=counts.get(version,0)+1
        raw=dict(states=[],events=[],checks=[],uncertainties=['pending observation'],multiple_scenarios=False,revisit=[])
        if version=='observe':
            item=event(['frame-001','frame-002']);item['uncertainties']=['pending trigger']
            raw['events']=[item]
        if version=='audit':
            raw=dict(checks=[dict(event_id=c['track']['event_id'],trigger_verdict='unclear',outcome_verdict='supported',question='pending trigger') for c in payload['trajectory']],issues=[],multiple_scenarios=False,revisit=[])
        if version==stage and counts[version]==1:
            raw['revisit']=[dict(first_frame='frame-001',last_frame='frame-002',question='unverified request')]
        return raw
    def loader(*a):loads.append(a);return [{'frame_id':'frame-011','timestamp_ms':150}]
    result=vt.observe_trajectory({'media':{'frames':frames(10),'duration_ms':1001}},None,lambda:False,
        skill_runner=runner,revisit=loader,diagnostics=diagnostics)
    assert len(loads)==1 and result['trajectory_audit']['checks'][0]['status']=='needs_review'
    assert result['events'][0]['uncertainties']
    assert not any(r['kind']=='rejection' for r in diagnostics)
    assert any(r['questions_ref'].get('revisit_count')==1 for r in diagnostics)


@pytest.mark.parametrize('loader_fails',[False,True])
def test_attempt_and_loader_refusals_never_become_empty_decode(monkeypatch,loader_fails):
    from task_server.services import ui_video_windows
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    count=0;loads=[];diagnostics=[]
    def runner(skill,payload,**kw):
        nonlocal count
        raw=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False,revisit=[])
        if kw['version']=='observe':
            count+=1
            raw['revisit']=[dict(first_frame=f'frame-{count:03d}',last_frame=f'frame-{count+1:03d}',question='pending')]
        return raw
    def loader(*a):
        loads.append(a)
        if loader_fails:raise ValueError('video_revisit_limit')
        return []
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':{'frames':frames(10),'duration_ms':1001}},None,lambda:False,
            skill_runner=runner,revisit=loader,diagnostics=diagnostics)
    row=diagnostics[-1]
    if loader_fails:
        assert str(exc.value)=='video_revisit_limit'
        assert row['kind']=='acquisition_failure' and row['reason']=='loader_capacity' and row['revisits']==0
        assert len(loads)==1 and not any(r.get('status')=='no_new_frames' for r in diagnostics)
    else:
        assert exc.value.trajectory_reason=='revisit_budget'
        assert row['kind']=='capacity_refusal' and row['reason']=='revisit_attempts' and row['revisits']==4
        assert len(loads)==4 and len([r for r in diagnostics if r.get('status')=='no_new_frames'])==4


def test_captured_correction_acquires_then_reobserves_without_committing_tentative_claims(captured_ordering):
    frozen,replies,packets=captured_ordering
    overlay=[f for f in frozen['media']['frames'] if int(f['frame_id'].split('-')[1])>=307]
    frozen['media']['frames']=[f for f in frozen['media']['frames'] if f not in overlay]
    packets[1]['frames']=[f for f in packets[1]['frames'] if f not in overlay]
    packets[1]['owned_frame_ids']=[fid for fid in packets[1]['owned_frame_ids'] if fid not in {f['frame_id'] for f in overlay}]
    observations=[];loads=[];diagnostics=[]
    def runner(skill,payload,**kw):
        if kw['version']=='overview':return copy.deepcopy(replies[78])
        if kw['version']=='audit':return {'checks':[dict(event_id=c['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='') for c in payload['trajectory']], 'issues':[], 'multiple_scenarios':False,'revisit':[]}
        if payload['packet_id']=='packet-001':return copy.deepcopy(replies[86])
        observations.append(copy.deepcopy(payload))
        if len(observations)==1:return copy.deepcopy(replies[90])
        if len(observations)==2:return dict(states=[],events=[],checks=[],uncertainties=['pending request'],multiple_scenarios=False,revisit=copy.deepcopy(replies[90]['revisit']))
        return copy.deepcopy(replies[96])
    def loader(*a,**kw):
        assert len(observations)==2
        loads.append(a);return copy.deepcopy(overlay)
    with pytest.raises(ValueError) as exc:
        vt.observe_trajectory({'media':frozen['media']},None,lambda:False,skill_runner=runner,
            revisit_bounded=loader,diagnostics=diagnostics)
    assert exc.value.trajectory_reason=='input_continuation'
    assert len(loads)==1 and len(observations)==4
    assert [len(p['frames']) for p in observations]==[44,44,60,60]
    assert all(p['prior_events']==observations[0]['prior_events'] for p in observations)
    assert [r['reason'] for r in diagnostics if r['kind']=='rejection']==['event_evidence','input_continuation','input_continuation']
