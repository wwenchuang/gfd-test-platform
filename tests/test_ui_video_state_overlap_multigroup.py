"""A bounded overlap retry must keep earlier and later repair groups intact."""
import copy
import pytest
from task_server.services import ui_video_reconcile as r
from test_ui_video_state_overlap_repair import event,reply


def sample(group_index=3):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(70)]
    taps=[event('aiTap',f'按钮{i}',[f'f{i}']) for i in range(5,65,10)]
    t=5+group_index*10
    old=event('aiAssert','原状态',[f'f{t-1}',f'f{t}'],'旧疑问')
    new=event('aiWaitFor','新状态',[f'f{t}'],'新疑问')
    checked=sorted([old]+copy.deepcopy(taps),key=lambda e:(e['start_ms'],e['end_ms']))
    return frames,taps,old,new,checked


@pytest.mark.parametrize('group_index',[0,3,5])
def test_one_overlap_retries_only_its_group_and_preserves_others(monkeypatch,tmp_path,group_index):
    frames,taps,old,new,checked=sample(group_index);calls=[];target=5+group_index*10
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append((ids,kw['attempt_limit']))
        out=copy.deepcopy(local['events'])
        if f'f{target}' in ids:out.append(copy.deepcopy(new))
        return reply(out,['该组首次疑问'] if ids==[f'f{target}'] else [],ids==[f'f{target}'])
    monkeypatch.setattr(r,'_repair_window',model)
    result=r._repair_coverage(taps,{'events':checked,'uncertainties':['原始问题'],'multiple_scenarios':False},
                               frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert len(calls)==7
    assert calls[group_index][0]==[f'f{target}'] and f'f{target-1}' in calls[group_index+1][0]
    assert all(sum(e==candidate for candidate in result['events'])==1 for e in taps+[old,new])
    assert {'原始问题','该组首次疑问'}<=set(result['uncertainties']) and result['multiple_scenarios']
    assert any('局部状态证据存在重叠' in q for q in result['uncertainties'])


def test_expanded_group_cannot_consume_extra_call_reserved_for_later_groups(monkeypatch,tmp_path):
    frames,taps,old,new,checked=sample(3);calls=[]
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append(ids)
        return reply(local['events']+([new] if 'f35' in ids else []),attempts=2 if ids==['f5'] else 1)
    monkeypatch.setattr(r,'_repair_window',model)
    with pytest.raises(ValueError,match='video_required_action_missing') as caught:
        r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert calls==[['f5'],['f15'],['f25'],['f35']]
    assert not hasattr(caught.value,'overlap_expanded_window_failure_kind')


@pytest.mark.parametrize('refs', [['f24','f35'],['f34','f45']])
def test_conflict_expansion_may_not_touch_previous_or_next_group(monkeypatch,tmp_path,refs):
    frames,taps,_,new,checked=sample(3);old=event('aiAssert','原状态',refs)
    checked=[e for e in checked if e['action']!='aiAssert']+[old]
    checked.sort(key=lambda e:(e['start_ms'],e['end_ms']));calls=[]
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append(ids)
        return reply(local['events']+([new] if 'f35' in ids else []))
    monkeypatch.setattr(r,'_repair_window',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert calls==[['f5'],['f15'],['f25'],['f35']]


def test_cancel_after_conflicting_response_never_calls_other_groups(monkeypatch,tmp_path):
    frames,taps,old,new,checked=sample(3);calls=[]
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append(ids)
        return reply(local['events']+([new] if 'f35' in ids else []))
    monkeypatch.setattr(r,'_repair_window',model)
    with pytest.raises(ValueError,match='video_cancelled'):
        r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:len(calls)>=4,[],{e['frame_refs'][0] for e in taps})
    assert calls==[['f5'],['f15'],['f25'],['f35']]


def test_second_group_conflict_cannot_trigger_another_retry(monkeypatch,tmp_path):
    frames,taps,old,new,checked=sample(1)
    second_old=event('aiAssert','另一旧状态',['f34','f35']);second_new=event('aiWaitFor','另一新状态',['f35'])
    checked=sorted(checked+[second_old],key=lambda e:(e['start_ms'],e['end_ms']));calls=[]
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append(ids)
        extra=[new] if 'f15' in ids else ([second_new] if 'f35' in ids else [])
        return reply(local['events']+extra)
    monkeypatch.setattr(r,'_repair_window',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert calls==[['f5'],['f15'],['f14','f15'],['f25'],['f35']]


def test_six_groups_use_real_window_calls_and_preserve_first_reply(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as details,ai_skill_service as ai
    frames,taps,old,new,checked=sample(3);calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda frames,*a:[{'name':f['frame_id']} for f in frames])
    monkeypatch.setattr(details,'append_details',lambda *a,**kw:[])
    def model(skill,payload,**kw):
        ids={f['frame_id'] for f in payload['frames']};calls.append(ids)
        out=[copy.deepcopy(e) for e in taps if set(e['frame_refs'])<=ids]
        if 'f34' in ids:out.insert(0,copy.deepcopy(old))
        if 'f35' in ids:out.append(copy.deepcopy(new))
        out.sort(key=lambda e:(e['start_ms'],e['end_ms']))
        return {'events':[{k:v for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in out],
                'uncertainties':['首次问题'] if ids=={'f35'} else [],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert len(calls)==7 and '首次问题' in result['uncertainties']
    assert sum(e['action']=='aiTap' for e in result['events'])==6
    assert 'state_overlap_first_repair' in result['reconciliation_audit']


def test_later_group_cannot_overlap_expanded_groups_completed_output(monkeypatch,tmp_path):
    frames,taps,old,new,checked=sample(3);calls=[]
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append(ids);out=copy.deepcopy(local['events'])
        if 'f35' in ids:out.append(copy.deepcopy(new))
        if ids==['f45']:
            out[0]['start_ms']=3500
        return reply(out)
    monkeypatch.setattr(r,'_repair_window',model)
    with pytest.raises(ValueError,match='video_required_action_missing') as caught:
        r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert calls[-1]==['f45'] and len(calls)==6
    assert hasattr(caught.value,'state_overlap_evidence')
    assert not hasattr(caught.value,'overlap_expanded_window_failure_kind')


def test_previous_group_audit_questions_and_late_secret_are_preserved(monkeypatch,tmp_path):
    frames,taps,old,new,checked=sample(3);calls=[];secret='late-secret-987'
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append(ids)
        out=copy.deepcopy(local['events'])+([new] if 'f35' in ids else [])
        answer=reply(out,['此前问题 '+secret] if ids==['f5'] else [],ids==['f5'])
        if ids==['f5']:
            answer['reconciliation_audit']['redundant_assert_claims']=[{'source_anchor_ids':['first_pass:0','independent_review:0'],'resolution':'existing_claim'}]
        if ids==['f34','f35']:args[3].append(secret)
        return answer
    monkeypatch.setattr(r,'_repair_window',model)
    result=r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert secret not in repr(result) and result['multiple_scenarios']
    assert any('此前问题' in q for q in result['uncertainties'])
    claim=result['reconciliation_audit']['redundant_assert_claims'][0]
    assert claim['source_anchor_ids']==['first_pass:0','independent_review:0'] and claim['resolution']=='existing_claim'


from test_ui_case_draft_service import env


@pytest.mark.parametrize('groups',[1,8])
@pytest.mark.parametrize('kind',['model','private'])
def test_retry_failure_worker_keeps_first_conflict_without_error_body(env,monkeypatch,tmp_path,groups,kind):
    import io,json
    from test_ui_video_state_overlap_diagnostics import fixture
    from test_ui_case_draft_service import A,APP
    from task_server.services import ui_video_source_service as source,ui_video_analysis as analysis,ui_video_runtime as runtime
    from task_server.workers import ui_video_worker as worker
    frames,all_events,old,new=fixture(False);events=all_events[:groups];calls=[]
    checked={'events':[old]+copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False}
    store=source.VideoStore(tmp_path/'videos');doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'black':False,'static':False,'frames':frames,'duration_ms':11000})
    monkeypatch.setattr(runtime,'selected',lambda *a:False)
    def model(original,local,group,*args,**kw):
        calls.append(1)
        if len(calls)>1:
            if kind=='model':raise ValueError('video_model_invalid')
            raise RuntimeError('PRIVATE_MODEL_TEXT')
        return reply(local['events']+[new])
    monkeypatch.setattr(r,'_repair_window',model)
    monkeypatch.setattr(analysis,'analyze_whole_frames',lambda *a:r._repair_coverage(events,checked,frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events}))
    worker.analyze_video_source(store,doc['source_id'])
    saved=store.get(doc['source_id'],A['user_id'],APP)
    assert saved['status']=='failed' and not saved.get('events') and len(calls)==2
    assert saved['error_code']==('video_model_invalid' if kind=='model' else 'video_analysis_failed')
    diagnostic=saved['failure_diagnostic']
    assert diagnostic['state_overlap_evidence']['group_count']==groups
    assert diagnostic['overlap_expanded_window_failure_kind']==('video_model_invalid' if kind=='model' else 'other')
    assert 'PRIVATE' not in json.dumps(diagnostic)


def test_state_retry_competes_with_return_retry_without_weakening_gate(monkeypatch,tmp_path):
    frames,taps,old,new,checked=sample(0)
    taps[4].update(action='navigateBack',target='上一页面',observation='返回上一页面')
    checked=copy.deepcopy([old]+taps);calls=[]
    def model(original,local,group,*args,**kw):
        ids=[f['frame_id'] for f in group];calls.append((ids,kw['attempt_limit'],kw['retry_return_gesture_only']))
        if ids==['f45']:
            assert kw['attempt_limit']==1 and kw['retry_return_gesture_only']
            raise ValueError('video_required_action_missing')
        return reply(local['events']+([new] if 'f5' in ids else []))
    monkeypatch.setattr(r,'_repair_window',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        r._repair_coverage(taps,{'events':checked,'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in taps})
    assert len(calls)==6 and calls[-1][0]==['f45'] and all(c[2] for c in calls)


def test_forged_retry_kind_is_not_saved_or_interpreted():
    from task_server.workers import ui_video_worker as worker
    from test_ui_video_state_overlap_diagnostics import fixture,sample_evidence
    frames,_,_,_=fixture(False);doc={'media':{'frames':frames,'duration_ms':11000}}
    error=ValueError('video_model_invalid');error.state_overlap_evidence=sample_evidence();error.overlap_expanded_window_failure_kind=['PRIVATE']
    assert worker._private_target_failure(error,'video_model_invalid',doc)=={}


def test_failed_expansion_preflight_is_not_reported_as_model_retry(monkeypatch,tmp_path):
    from test_ui_video_state_overlap_diagnostics import fixture
    frames,events,old,new=fixture(False);events=events[:1];calls=[];validations=[]
    original=r._validate_repair_group_ownership
    def ownership(*args):
        validations.append(1)
        if len(validations)==2:raise ValueError('video_required_action_missing')
        return original(*args)
    def model(original,local,group,*args,**kw):calls.append(1);return reply(local['events']+[new])
    monkeypatch.setattr(r,'_repair_window',model);monkeypatch.setattr(r,'_validate_repair_group_ownership',ownership)
    with pytest.raises(ValueError,match='video_required_action_missing') as caught:
        r._repair_coverage(events,{'events':[old]+events,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{'frame-005'})
    assert calls==[1] and hasattr(caught.value,'state_overlap_evidence')
    assert not hasattr(caught.value,'overlap_expanded_window_failure_kind')
