"""Capacity-shaped fixtures contain no private video/export text."""
import copy
import pytest
from task_server.services import ui_video_analysis as analysis
from task_server.services.ui_case_draft_contract import build_draft_yaml
from test_ui_video_question_presentation import document
from test_ui_case_draft_service import env
from test_ui_video_runtime import worker_env

SAFETY='含删除/支付/打印等有副作用动作，请核对测试数据与授权后再手工试跑'
COUNTS=[0,0,3,0,1,0,0,3,2,1,0,2,1,0,2,1,2,1,0,0,1,1,1,1,2,2,1,1,1,1,1,2,2,2,3,3,3,1]
SIDE={10,11,12,13,14,19,20,26,27}


def shaped(reuse=False):
    doc=document();doc['events']=doc['events'][:38]
    doc['uncertainties']=[f'根部独立完整疑问 {i}' for i in range(45)]
    serial=0
    for i,(event,count) in enumerate(zip(doc['events'],COUNTS)):
        event.update(target=f'具体控件 {i}'+('打印' if i in SIDE else ''),observation=f'页面显示明确状态 {i}',action='aiTap',uncertainties=[])
        for _ in range(count):
            event['uncertainties'].append(f'独立证据复核：完整事件疑问 {serial%45}；仍需核对全部证据')
            serial+=1
        if reuse and i<36:event['reusable_expected']=f'页面显示复用候选 {i}'
    doc['events'][-1].update(action='aiAssert',target=doc['events'][-1]['observation'])
    return doc


def assert_ledger(doc,proposal,snapshot):
    ledger=snapshot['review_question_groups']
    for i,event in enumerate(doc['events']):
        members=[g for g in ledger if g['event_index']==i]
        required=list(dict.fromkeys(event['uncertainties']))
        if event.get('reusable_expected'):required.append('请确认复用后的结果检查与原录屏一致：'+event['reusable_expected'])
        if i in SIDE:required.append(SAFETY)
        assert {q for g in members for q in g['issues']}==set(required)
        for g in members:
            assert g['source_ref'].endswith(':'+str(i))
            assert g['step_id']==snapshot['events'][i]['draft_step_id']
            assert (g['start_ms'],g['end_ms'])==(event['start_ms'],event['end_ms'])
            assert g['question'] in proposal['questions']
            assert all(q in g['question'] for q in g['issues'])
    assert snapshot['raw_root_questions']==doc['uncertainties']
    for old,new in zip(doc['events'],snapshot['events']):
        assert all(new[k]==v for k,v in old.items())
    assert all(s['review_state']=='needs_review' for s in proposal['steps'])
    assert len(proposal['questions'])<=100 and all(len(q)<=2000 for q in proposal['questions'])
    for q in doc['uncertainties']:
        assert any(q in text for text in proposal['questions'])


@pytest.mark.parametrize('reuse',[False,True])
def test_actual27_shape_groups_complete_event_issues_without_mutation(reuse):
    doc=shaped(reuse);before=copy.deepcopy(doc)
    assert len(doc['uncertainties'])==45 and sum(COUNTS)==48
    proposal,snapshot=analysis.draft_proposal(doc)
    assert_ledger(doc,proposal,snapshot)
    assert doc==before
    again,other=analysis.draft_proposal(doc)
    assert proposal['questions']==again['questions']
    assert [(x['event_index'],x['issues'],x['question']) for x in snapshot['review_question_groups']]==[(x['event_index'],x['issues'],x['question']) for x in other['review_question_groups']]
    confirmed={**proposal,'steps':[{**s,'review_state':'confirmed'} for s in proposal['steps']]}
    compiled=build_draft_yaml(confirmed,doc['app_id'])
    assert compiled['ready'] is False and set(proposal['questions'])<=set(compiled['questions'])


def test_groups_keep_shared_root_anchors_similar_text_manual_placeholder_and_unmatched_roots():
    doc=shaped(True);shared='相同完整疑问';placeholder='请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件'
    doc['uncertainties'] += [shared,'相同完整疑问？',placeholder]
    doc['events'][2]['uncertainties'] += [shared,placeholder]
    doc['events'][7]['uncertainties'].append(shared)
    proposal,snapshot=analysis.draft_proposal(doc)
    assert_ledger(doc,proposal,snapshot)
    assert '相同完整疑问？' in proposal['questions']
    assert {g['event_index'] for g in snapshot['review_question_groups'] if shared in g['issues']}=={2,7}


@pytest.mark.parametrize('length',[1900,1968,2000])
def test_atomic_boundary_preserves_full_issue_and_metadata(length):
    doc=shaped(True);doc['events'][2]['uncertainties']=['甲'*length,'乙'*100,'丙'*100]
    proposal,snapshot=analysis.draft_proposal(doc)
    assert_ledger(doc,proposal,snapshot)
    groups=[g for g in snapshot['review_question_groups'] if g['event_index']==2]
    assert len(groups)>=2
    assert any('甲'*length in g['question'] for g in groups)


def test_single_oversize_issue_and_unrelated101_roots_fail_without_mutation():
    doc=shaped(True);doc['events'][2]['uncertainties']=['甲'*2001]
    before=copy.deepcopy(doc)
    with pytest.raises(ValueError):analysis.draft_proposal(doc)
    assert doc==before
    doc=shaped(True);doc['uncertainties']=[f'不相关根问题 {i}' for i in range(101)]
    with pytest.raises(ValueError,match='最多100项'):analysis.draft_proposal(doc)


def test_actual26_shape_all35_occurrences_and_reuse_survive():
    doc=document();doc['uncertainties']=[f'原片26根问题 {i}' for i in range(39)]
    placeholder='请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件'
    counts=[0,1,3,0,0,1,0,1,4,2,0,1,2,0,2,0,1,1,0,0,0,1,0,1,1,2,0,0,0,1,0,0,3,2,2,0,1,2,0]
    for i,(event,count) in enumerate(zip(doc['events'],counts)):
        event.update(action='aiTap',target=f'原片26具体控件 {i}'+('打印' if i<9 else ''),observation=f'页面显示原片26状态 {i}',uncertainties=[f'原片26完整疑问 {i}-{j}' for j in range(count)])
        if i in (2,7):event['uncertainties'][-1]=placeholder
        if i<36:event['reusable_expected']=f'页面显示原片26复用候选 {i}'
    doc['events'][-1].update(action='aiAssert',target=doc['events'][-1]['observation'])
    assert sum(len(e['uncertainties']) for e in doc['events'])==35
    before=copy.deepcopy(doc);proposal,snapshot=analysis.draft_proposal(doc)
    assert doc==before and len(proposal['questions'])<=100
    for i,event in enumerate(doc['events']):
        groups=[g for g in snapshot['review_question_groups'] if g['event_index']==i]
        required=set(event['uncertainties'])
        if i<9:required.add(SAFETY)
        if i<36:required.add('请确认复用后的结果检查与原录屏一致：'+event['reusable_expected'])
        assert {q for g in groups for q in g['issues']}==required
    assert all(s['review_state']=='needs_review' for s in proposal['steps'])


def test_final_normalizer_questions_cannot_overflow100():
    doc=shaped();doc['uncertainties']=[f'独立根问题 {i}' for i in range(68)]
    proposal,_=analysis.draft_proposal(doc)
    assert len(proposal['questions'])==100
    doc['events'][0].update(action='aiInput',value='')
    with pytest.raises(ValueError,match='最多100项'):analysis.draft_proposal(doc)


def test_grouping_preserves_selection_and_assertion_companion_source_ids():
    doc=shaped(True);doc['events'][2]['selection']='仅本次创建的测试对象';doc['events'][2]['target']='{{本次对象}}详情入口';doc['events'][2]['reusable_expected']='页面显示{{本次对象}}与{{保留对象}}均保留'
    doc['events'][-1].update(action='aiTap')
    proposal,snapshot=analysis.draft_proposal(doc)
    event=snapshot['events'][2]
    groups=[g for g in snapshot['review_question_groups'] if g['event_index']==2]
    selection=next(s for s in proposal['steps'] if s['step_id']==event['selection_step_id'])
    assert any('请确认本次测试对象的选取范围：仅本次创建的测试对象' in q for g in groups for q in g['issues'])
    assert selection['source_ref']==groups[0]['source_ref']
    assert proposal['steps'][-1]['action']=='aiAssert'
    assert proposal['steps'][-1]['step_id']==snapshot['events'][-1]['expected_check_step_id']
    assert all(s['review_state']=='needs_review' for s in proposal['steps'])


# Exercise the normal persistence/import path, without provider/device activity.
def test_grouped_normal_import_keeps_every_source_fact_and_blocks_readiness(env,tmp_path,monkeypatch):
    import io
    from task_server.services import ui_video_source_service as video
    from task_server.services.operation_attribution import actor_context
    from test_ui_case_draft_service import A,APP
    store=video.VideoStore(tmp_path/'videos');monkeypatch.setattr(video,'service_store',lambda:store)
    source=store.upload(A,APP,'capacity.mp4',io.BytesIO(b'mp4'),3,actor=A)
    doc=shaped(True)
    fields={k:v for k,v in doc.items() if k in ('events','uncertainties','media','multiple_scenarios','video_runtime_selection')}
    store.transition(source['source_id'],{'queued'},'ready',**fields)
    with actor_context(A):draft=video.import_video_draft(A,source['source_id'],APP)
    assert draft['ready'] is False and all(s['review_state']=='needs_review' for s in draft['steps'])
    assert draft['source_snapshot']['raw_root_questions']==doc['uncertainties']
    assert 'review_question_groups' in draft['source_snapshot']
    assert len(draft['questions'])<=100


def test_grouped_normal_worker_ready_is_still_only_reviewable(worker_env,monkeypatch):
    from task_server.services import ui_video_runtime as runtime
    worker,store,source,_=worker_env
    monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST','1')
    result=shaped(True)
    result={k:v for k,v in result.items() if k in ('events','uncertainties','media','multiple_scenarios')}
    monkeypatch.setattr(runtime,'run_runtime',lambda *a,**kw:copy.deepcopy(result))
    worker.analyze_video_source(store,source['source_id'])
    saved=store.get(source['source_id'],'u',source['app_id'])
    assert saved['status']=='ready'
    proposal,snapshot=analysis.draft_proposal(saved)
    assert len(proposal['questions'])<=100
    assert all(step['review_state']=='needs_review' for step in proposal['steps'])
    assert snapshot['raw_root_questions']==saved['uncertainties']


def test_exact2000_rendered_group_boundary_and_one_character_split():
    doc=shaped(True)
    event=doc['events'][2]
    prefix=f'事件 3（片段 {event["start_ms"]/1000:g}–{event["end_ms"]/1000:g} 秒）：'
    first='甲'*(2000-len(prefix)-101)
    event['uncertainties']=[first,'乙'*100]
    event.pop('reusable_expected')
    proposal,snapshot=analysis.draft_proposal(doc)
    groups=[g for g in snapshot['review_question_groups'] if g['event_index']==2]
    assert len(groups)==1 and len(groups[0]['question'])==2000
    event['uncertainties'][1]+='乙'
    proposal,snapshot=analysis.draft_proposal(doc)
    groups=[g for g in snapshot['review_question_groups'] if g['event_index']==2]
    assert len(groups)==2
    assert [g['issues'] for g in groups]==[[first],['乙'*101]]


def test_legacy_capacity_shape_still_rejects_without_grouping():
    doc=shaped(True);doc['video_runtime_selection']['mode']='video-perception-bank-v1'
    before=copy.deepcopy(doc)
    with pytest.raises(ValueError,match='最多100项'):analysis.draft_proposal(doc)
    assert doc==before


def test_normal_print_recovery_rebinds_confirm_review_to_surviving_cancel_step():
    doc=shaped(True);doc['print_recovery']={'submit':0,'cancel':1,'confirm':2}
    doc['events'][0]['target']='确认打印'
    doc['events'][1]['target']='取消打印'
    doc['events'][2]['target']='确认取消'
    before=copy.deepcopy(doc)
    proposal,snapshot=analysis.draft_proposal(doc)
    assert doc==before and len(proposal['steps'])==37
    current={s['step_id']:s for s in proposal['steps']}
    assert current[snapshot['events'][0]['draft_step_id']]['action']=='submitPrint'
    cancel=snapshot['events'][1]['draft_step_id']
    assert current[cancel]['action']=='cancelPrint'
    original=snapshot['events'][2]['draft_step_id']
    assert original not in current
    groups=[g for g in snapshot['review_question_groups'] if g['event_index']==2]
    assert groups and sum(len(g['issues']) for g in groups)==4
    for g in groups:
        assert g['original_step_id']==original and g['step_id']==cancel
        assert g['step_ids']==[cancel]
        assert g['replacement_event_index']==1
        assert g['source_ref'].endswith(':2')
        assert g['step_source_refs']=={cancel:current[cancel]['source_ref']}
        assert current[cancel]['source_ref'].endswith(':1')
        assert (g['start_ms'],g['end_ms'])==(doc['events'][2]['start_ms'],doc['events'][2]['end_ms'])
        assert g['question'] in proposal['questions']
    for g in snapshot['review_question_groups']:
        assert g['step_id'] in current and set(g['step_ids'])<=set(current)
    assert all(s['review_state']=='needs_review' for s in proposal['steps'])
    for old,new in zip(doc['events'],snapshot['events']):
        assert all(new[k]==v for k,v in old.items())


def test_group_mapping_includes_selection_and_final_assertion_companions():
    doc=shaped(True)
    doc['events'][2].update(selection='仅本次创建的测试对象',target='{{本次对象}}详情入口',reusable_expected='页面显示{{本次对象}}与{{保留对象}}均保留')
    doc['events'][-1].update(action='aiTap',uncertainties=['末尾完整审阅问题'])
    proposal,snapshot=analysis.draft_proposal(doc)
    current={s['step_id'] for s in proposal['steps']}
    for index,companion in ((2,'selection_step_id'),(37,'expected_check_step_id')):
        event=snapshot['events'][index]
        groups=[g for g in snapshot['review_question_groups'] if g['event_index']==index]
        assert groups
        for g in groups:
            assert g['original_step_id']==event['draft_step_id']
            assert set(g['step_ids'])=={event['draft_step_id'],event[companion]}
            assert set(g['step_ids'])<=current
            assert set(g['step_source_refs'])==set(g['step_ids'])
            assert set(g['step_source_refs'].values())=={g['source_ref']}
            assert 'replacement_event_index' not in g
