"""Review blockers must retain their whole text and source attribution."""
import copy
import pytest
from task_server.services import ui_video_analysis as analysis
from task_server.services.ui_case_draft_contract import build_draft_yaml
from test_ui_video_question_groups import shaped
from test_ui_video_runtime import worker_env

NOTICE='同一问题可能涉及视频多处，请核对完整视频中所有相关操作后再解除。'


def ordinary(*,root=None,event=None):
    return {'source_id':'uvs_'+'a'*32,'analysis_version':11,'app_id':'com.kfb.model','goal':'',
        'media':{'frames':[{'frame_id':'frame-001','timestamp_ms':100}],'duration_ms':1000},
        'events':[{'action':'aiAssert','target':'首页可见','value':'','frame_refs':['frame-001'],
                   'start_ms':100,'end_ms':100,'evidence_kind':'observed','observation':'首页可见',
                   'uncertainties':[] if event is None else [event]}],
        'uncertainties':[] if root is None else [root]}


def assert_blocked(proposal,app):
    assert all(len(q)<=2000 for q in proposal['questions'])
    assert any(NOTICE in q for q in proposal['questions'])
    gate=build_draft_yaml(proposal,app)
    assert gate['ready'] is False and any(NOTICE in q for q in gate['questions'])


@pytest.mark.parametrize('length',[1968,2000])
def test_long_ordinary_root_question_preserved_with_blocking_notice(length):
    q='甲'*length;doc=ordinary(root=q);before=copy.deepcopy(doc)
    proposal,snapshot=analysis.draft_proposal(doc)
    assert q in proposal['questions']
    assert NOTICE in proposal['questions']
    assert_blocked(proposal,doc['app_id'])
    assert snapshot['events'][0]['frame_refs']==['frame-001'] and doc==before


def test_unrepresentable_ordinary_event_question_fails_closed_without_mutation():
    q='乙'*2000;doc=ordinary(event=q);before=copy.deepcopy(doc)
    with pytest.raises(ValueError,match='video_question_anchor_unavailable'):
        analysis.draft_proposal(doc)
    assert doc==before


@pytest.mark.parametrize('length',[1900,1968])
def test_business_group_preserves_long_issue_and_ledger_matches_visible_questions(length):
    q='丙'*length;doc=shaped(True);doc['events'][2]['uncertainties']=[q]
    before=copy.deepcopy(doc);proposal,snapshot=analysis.draft_proposal(doc)
    assert_blocked(proposal,doc['app_id'])
    assert all(g['question'] in proposal['questions'] for g in snapshot['review_question_groups'])
    g=next(g for g in snapshot['review_question_groups'] if q in g['issues'])
    assert q in g['question'] and len(g['question'])<=2000
    assert g['event_index']==2 and g['source_ref'].endswith(':2')
    assert g['step_id']==snapshot['events'][2]['draft_step_id']
    assert g['step_source_refs'][g['step_id']]==g['source_ref']
    assert snapshot['events'][2]['frame_refs']==doc['events'][2]['frame_refs'] and doc==before


def test_shared_unrepresentable_issue_fails_closed():
    q='丁'*2000;doc=shaped(True)
    doc['uncertainties']=[q];doc['events'][2]['uncertainties']=[q];doc['events'][7]['uncertainties']=[q]
    before=copy.deepcopy(doc)
    with pytest.raises(ValueError,match='video_question_anchor_unavailable'):
        analysis.draft_proposal(doc)
    assert doc==before


def test_capacity_overflow_never_drops_original_problem_to_fit():
    doc=ordinary(root='甲'*2000)
    doc['uncertainties']=[str(i)+'甲'*(1999-len(str(i))) for i in range(100)]
    before=copy.deepcopy(doc)
    with pytest.raises(ValueError):analysis.draft_proposal(doc)
    assert doc==before


def test_notice_can_trigger_safe_grouping_without_dropping_questions():
    q='长'*1968;doc=ordinary(event=q)
    doc['video_runtime_selection']={'mode':'video-business-first-v1'}
    doc['uncertainties']=[f'独立根问题{i}' for i in range(97)]
    doc['events'][0]['uncertainties']=[q,'需核对第一项','需核对第二项']
    proposal,snapshot=analysis.draft_proposal(doc)
    assert_blocked(proposal,doc['app_id'])
    assert len(proposal['questions'])<=100
    assert all(any(root in item for item in proposal['questions']) for root in doc['uncertainties'])
    assert all(any(issue in g['issues'] and g['question'] in proposal['questions']
                   for g in snapshot['review_question_groups']) for issue in doc['events'][0]['uncertainties'])


def test_print_cancel_group_keeps_original_evidence_and_surviving_step_mapping():
    q='打印确认需核对'+'打'*1961;assert len(q)==1968
    doc=shaped(True);doc['print_recovery']={'submit':0,'cancel':1,'confirm':2}
    doc['events'][0]['target']='确认打印';doc['events'][1]['target']='取消打印';doc['events'][2]['target']='确认取消'
    doc['events'][2]['uncertainties']=[q]
    proposal,snapshot=analysis.draft_proposal(doc)
    assert_blocked(proposal,doc['app_id'])
    g=next(g for g in snapshot['review_question_groups'] if q in g['issues'])
    cancel=snapshot['events'][1]['draft_step_id'];removed=snapshot['events'][2]['draft_step_id']
    assert g['original_step_id']==removed and g['step_id']==cancel
    assert g['source_ref'].endswith(':2') and g['step_source_refs'][cancel].endswith(':1')
    assert q in g['question'] and g['question'] in proposal['questions']
    assert snapshot['events'][2]['frame_refs']==doc['events'][2]['frame_refs']


def test_long_issue_present_at_root_and_two_event_occurrences_preserves_both_sources():
    q='复'*1968;doc=ordinary(root=q,event=q)
    second=copy.deepcopy(doc['events'][0]);second['start_ms']=second['end_ms']=200
    second['frame_refs']=['frame-002'];doc['events'].append(second)
    doc['media']['frames'].append({'frame_id':'frame-002','timestamp_ms':200})
    proposal,snapshot=analysis.draft_proposal(doc)
    assert_blocked(proposal,doc['app_id'])
    assert any(q in item for item in proposal['questions']) and snapshot['raw_root_questions']==[q]
    groups=[g for g in snapshot['review_question_groups'] if q in g['issues']]
    assert {g['event_index'] for g in groups}=={0,1}
    assert all(q in g['question'] and g['question'] in proposal['questions'] and
               g['source_ref'].endswith(':'+str(g['event_index'])) for g in groups)


@pytest.mark.parametrize('runtime_mode',[False,True])
def test_unrepresentable_event_fails_source_with_clear_message(worker_env,monkeypatch,runtime_mode):
    from task_server.services import ui_video_runtime as runtime, ui_video_business_inputs as inputs
    worker,store,source,_=worker_env
    result=ordinary(event='测'*2000)
    result={k:v for k,v in result.items() if k in ('events','uncertainties')}
    result['multiple_scenarios']=False
    if runtime_mode:
        monkeypatch.setattr(runtime,'run_runtime',lambda *a,**kw:copy.deepcopy(result))
    else:
        monkeypatch.setattr(runtime,'selected',lambda *a:False)
        monkeypatch.setattr(analysis,'analyze_whole_frames',lambda *a:copy.deepcopy(result))
        monkeypatch.setattr(inputs,'recover_business_inputs',lambda result,*a:result)
    worker.analyze_video_source(store,source['source_id'])
    saved=store.get(source['source_id'],'u',source['app_id'])
    assert saved['status']=='failed' and saved['error_code']=='video_question_anchor_unavailable'
    assert '核对问题过长' in saved['error'] and '对应视频片段' in saved['error']
    assert not saved.get('draft_id')


def test_long_event_question_below_notice_threshold_retains_visible_event_locator():
    q='丙'*1950;doc=ordinary(event=q)
    proposal,snapshot=analysis.draft_proposal(doc)
    groups=[g for g in snapshot['review_question_groups'] if q in g['issues']]
    assert len(groups)==1 and groups[0]['question'] in proposal['questions']
    assert groups[0]['question'].startswith('事件 1（片段 0.1–0.1 秒）：')
    assert q in groups[0]['question'] and len(groups[0]['question'])<=2000
    assert_blocked(proposal,doc['app_id'])
