"""Lossless business-only draft presentation under the unchanged100 question gate."""
import copy
import io
import pytest
from task_server.services import ui_video_analysis as analysis, ui_video_source_service as video
from task_server.services.operation_attribution import actor_context
from test_ui_video_question_aggregation import aggregate
from test_ui_case_draft_service import env, A, APP
from test_ui_video_runtime import worker_env


def document(business=True):
    raw,fs=aggregate(39,0)
    event_questions=[f'事件保留问题 {i}' for i in range(33)]
    for i in range(35):raw['events'][i]['uncertainties']=[event_questions[i%33]]
    raw['events'][-1].update(action='aiAssert',target='目标页面标题可见',observation='目标页面标题可见')
    raw['uncertainties']+=event_questions
    return dict(source_id='uvs_'+'a'*32,analysis_version=1,app_id=APP,goal='',media={'frames':fs,'duration_ms':4000},
        video_runtime_selection={'mode':'video-business-first-v1' if business else 'video-perception-bank-v1'},**raw)


def test_107_plain_plus_prefixed_blockers_become72_lossless_anchored_questions():
    doc=document();before=copy.deepcopy(doc)
    assert len(doc['uncertainties'])+sum(len(e['uncertainties']) for e in doc['events'])==107
    proposal,snapshot=analysis.draft_proposal(doc)
    assert len(proposal['questions'])==72
    for q in doc['uncertainties']:
        matches=[text for text in proposal['questions'] if text==q or text.endswith('：'+q)]
        assert len(matches)==1
        for index,event in enumerate(doc['events']):
            if q in event['uncertainties']:
                assert f'事件 {index+1}（片段 {event["start_ms"]/1000:g}–{event["end_ms"]/1000:g} 秒）' in matches[0]
    assert snapshot['raw_root_questions']==doc['uncertainties']
    assert [e['uncertainties'] for e in snapshot['events']]==[e['uncertainties'] for e in doc['events']]
    assert doc==before and all(s['review_state']=='needs_review' for s in proposal['steps'])


def test_exact_text_only_duplicates_preserve_all_associations_and_placeholder():
    doc=document();placeholder='请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件'
    doc['uncertainties']=['相同问题','相同问题','不同问题','相同问题？',placeholder]
    for event in doc['events']:event['uncertainties']=[]
    doc['events'][0]['uncertainties']=['相同问题','相同问题',placeholder]
    doc['events'][1]['uncertainties']=['相同问题','仅事件问题']
    proposal,snapshot=analysis.draft_proposal(doc)
    shared=next(q for q in proposal['questions'] if q.endswith('：相同问题'))
    assert shared.count('事件 1（')==1 and shared.count('事件 2（')==1
    assert '不同问题' in proposal['questions'] and '相同问题？' in proposal['questions']
    assert '片段 0.2–0.2 秒：仅事件问题' in proposal['questions']
    assert any(q.endswith('：'+placeholder) for q in proposal['questions'])
    assert snapshot['events'][0]['uncertainties']==['相同问题','相同问题',placeholder]
    assert all(step['review_state']=='needs_review' for step in proposal['steps'])


@pytest.mark.parametrize('questions',[[f'q{i}' for i in range(129)],['x'*2001],[{}],{}])
def test_business_root_snapshot_copy_is_bounded_and_typed(questions):
    doc=document();doc['uncertainties']=questions
    with pytest.raises(ValueError,match='video_model_invalid') as error:analysis.draft_proposal(doc)
    if isinstance(questions,list) and len(questions)==129:
        assert error.value.analysis_reason=='root_question_limit'
        assert (error.value.question_count,error.value.question_limit)==(129,128)


def test_final100_question_gate_still_blocks_distinct_business_questions():
    doc=document();doc['uncertainties']=[f'独立疑问 {i}' for i in range(101)]
    with pytest.raises(ValueError,match='最多100项'):analysis.draft_proposal(doc)


def test_legacy_keeps_original_plain_and_prefixed_question_presentation():
    doc=document(False);doc['uncertainties']=['事件保留问题 0']
    proposal,snapshot=analysis.draft_proposal(doc)
    assert '事件保留问题 0' in proposal['questions']
    assert '片段 0.1–0.1 秒：事件保留问题 0' in proposal['questions']
    assert 'raw_root_questions' not in snapshot
    with pytest.raises(ValueError,match='最多100项'):analysis.draft_proposal(document(False))


def test_normal_business_worker_ready_and_draft_validation_use_lossless_presentation(worker_env,monkeypatch):
    from task_server.services import ui_video_runtime as runtime
    worker,store,source,_=worker_env
    monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST','1')
    result=document();result={k:v for k,v in result.items() if k in ('events','uncertainties','media','multiple_scenarios')}
    monkeypatch.setattr(runtime,'run_runtime',lambda *a,**kw:copy.deepcopy(result))
    worker.analyze_video_source(store,source['source_id'])
    saved=store.get(source['source_id'],'u',source['app_id'])
    assert saved['status']=='ready' and len(saved['uncertainties'])==72
    proposal,snapshot=analysis.draft_proposal(saved)
    assert len(proposal['questions'])==72 and snapshot['raw_root_questions']==saved['uncertainties']


def test_normal_import_uses_same_presentation_and_immutable_raw_question_snapshot(env,tmp_path,monkeypatch):
    store=video.VideoStore(tmp_path/'videos');monkeypatch.setattr(video,'service_store',lambda:store)
    source=store.upload(A,APP,'a.mp4',io.BytesIO(b'mp4'),3,actor=A)
    doc=document();fields={k:v for k,v in doc.items() if k in ('events','uncertainties','media','multiple_scenarios','video_runtime_selection')}
    store.transition(source['source_id'],{'queued'},'ready',**fields)
    with actor_context(A):draft=video.import_video_draft(A,source['source_id'],APP)
    assert len(draft['questions'])==72 and draft['ready'] is False
    assert draft['source_snapshot']['raw_root_questions']==doc['uncertainties']
    assert [e['uncertainties'] for e in draft['source_snapshot']['events']]==[e['uncertainties'] for e in doc['events']]
