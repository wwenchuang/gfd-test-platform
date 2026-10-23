"""Consolidation references platform-owned evidence instead of copying times."""
import copy
import json
from pathlib import Path
import pytest


def sample():
    candidate={'action':'aiTap','target':'我的','value':'','start_ms':3933,'end_ms':4467,
        'frame_refs':['frame-010','frame-011'],'observation':'我的页面出现',
        'evidence_kind':'inferred','uncertainties':[]}
    item={'candidate_index':0,'action':'aiTap','target':'首页我的入口','value':'',
        'observation':'我的页面出现','uncertainties':[]}
    return [candidate],{'events':[item],'uncertainties':[],'multiple_scenarios':False,'title':'查看个人中心页面入口'}


def test_bind_exact_source_without_mutating_audit():
    from task_server.services.ui_video_analysis import _bind_candidate_evidence,validate_analysis
    candidates,result=sample();before=copy.deepcopy((candidates,result))
    actual=_bind_candidate_evidence(result,candidates)
    frames=[{'frame_id':ref,'timestamp_ms':stamp} for ref,stamp in zip(candidates[0]['frame_refs'],[3933,4467])]
    checked=validate_analysis(actual,frames,5000)
    for key in ('action','start_ms','end_ms','frame_refs','evidence_kind'):
        assert checked['events'][0][key]==candidates[0][key]
    assert checked['events'][0]['target']=='首页我的入口'
    assert (candidates,result)==before


@pytest.mark.parametrize('index',[-1,1,True,'0',None])
def test_reject_unknown_source(index):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence
    candidates,result=sample();result['events'][0]['candidate_index']=index
    with pytest.raises(ValueError):_bind_candidate_evidence(result,candidates)


@pytest.mark.parametrize('key,value',[('start_ms',0),('end_ms',9999),('frame_refs',['forged']),('evidence_kind','observed'),('action','aiInput')])
def test_reject_source_overrides(key,value):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence
    candidates,result=sample();result['events'][0][key]=value
    with pytest.raises(ValueError):_bind_candidate_evidence(result,candidates)


@pytest.mark.parametrize('indexes',[[0,0],[1,0]])
def test_no_repeated_or_reordered_sources(indexes):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence
    candidates,result=sample();candidates.append({**candidates[0],'start_ms':4500,'end_ms':4800})
    result['events']=[{**result['events'][0],'candidate_index':index} for index in indexes]
    with pytest.raises(ValueError):_bind_candidate_evidence(result,candidates)


def test_legacy_evidence_not_normalized():
    from task_server.services.ui_video_analysis import _bind_candidate_evidence,validate_analysis
    candidates,result=sample();result['events']=[{**candidates[0],'end_ms':4000}]
    actual=_bind_candidate_evidence(result,candidates)
    assert actual==result
    with pytest.raises(ValueError):validate_analysis(actual,[{'frame_id':'frame-010','timestamp_ms':3933},{'frame_id':'frame-011','timestamp_ms':4467}],5000)


def test_schema_accepts_indexed_but_not_mixed_provenance():
    import jsonschema
    schema=json.loads((Path(__file__).resolve().parents[1]/'ai_skills/schemas/ui_video_steps.schema.json').read_text())
    candidates,result=sample();jsonschema.validate(result,schema)
    result['events'][0]['start_ms']=0
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(result,schema)


def test_no_mixed_protocol_or_missing_required_candidate():
    from task_server.services.ui_video_analysis import _bind_candidate_evidence
    candidates,result=sample();candidates.append(copy.deepcopy(candidates[0]))
    with pytest.raises(ValueError):_bind_candidate_evidence(result,candidates,[1])
    result['events'].append(copy.deepcopy(candidates[1]))
    with pytest.raises(ValueError):_bind_candidate_evidence(result,candidates)


def test_discard_wait_but_keep_required_interaction():
    from task_server.services.ui_video_analysis import _bind_candidate_evidence
    candidates,result=sample();candidates.insert(0,{**candidates[0],'action':'aiWaitFor'})
    result['events'][0]['candidate_index']=1
    assert len(_bind_candidate_evidence(result,candidates,[1])['events'])==1


@pytest.mark.parametrize('repair',[False,True])
def test_real_pipeline_binds_once_and_preserves_shared_correction_budget(monkeypatch,tmp_path,repair):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    candidates,planned=sample();raw=candidates[0]
    frames=[{'frame_id':ref,'timestamp_ms':stamp} for ref,stamp in zip(raw['frame_refs'],[3933,4467])]
    (tmp_path/'frames').mkdir()
    for f in frames:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'frame')
    calls=[]
    def model(name,payload,**kwargs):
        calls.append(payload['phase'])
        if payload['phase']=='batch':return {'events':candidates,'uncertainties':[],'multiple_scenarios':False}
        assert payload['candidate_events']==candidates
        result=copy.deepcopy(planned)
        if repair and not payload.get('validation_feedback'):result['events'][0]['candidate_index']=9
        return result
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6)
    result=analysis.analyze_frames({'app_id':'app','goal':'','media':{'duration_ms':5000,'frames':frames}},tmp_path,lambda:False)
    assert result['raw_events']==candidates
    assert result['events'][0]['frame_refs']==raw['frame_refs']
    assert calls==['batch']+['consolidate']*(2 if repair else 1)


def test_indexed_invalid_input_retains_secret_redaction_on_correction(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    candidates,planned=sample();candidates[0].update(action='aiInput',target='密码框')
    planned['events'][0].update(action='aiInput',target='密码框')
    frames=[{'frame_id':ref,'timestamp_ms':stamp} for ref,stamp in zip(candidates[0]['frame_refs'],[3933,4467])]
    (tmp_path/'frames').mkdir()
    for f in frames:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'frame')
    def model(name,payload,**kwargs):
        if payload['phase']=='batch':return {'events':candidates,'uncertainties':[],'multiple_scenarios':False}
        assert 'private-code' not in str(payload)
        result=copy.deepcopy(planned)
        if not payload.get('validation_feedback'):result['events'][0].update(candidate_index=9,value='private-code')
        else:result['title']='核对private-code输入结果'
        return result
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6)
    result=analysis.analyze_frames({'app_id':'app','goal':'','media':{'duration_ms':5000,'frames':frames}},tmp_path,lambda:False)
    assert 'private-code' not in str(result)

@pytest.mark.parametrize('observation',[
    '不想打了选项被选中，与前一选项同时高亮',
    '模型脱落选项被选中高亮',
    '复选框处于选中状态',
    '提交反馈按钮已禁用，原因选项仍保持选中',
])
def test_persistent_form_state_is_a_visible_result(observation):
    from task_server.services.ui_video_analysis import _expected_candidate
    e={'observation':observation,'frame_refs':['f1']}
    assert _expected_candidate(e,'')['text']

@pytest.mark.parametrize('observation',[
    '点击按钮短暂高亮','按钮按下态','显示反馈成功Toast提示',
    '选项短暂高亮，无法确认是否选中',
    '选项短暂处于选中高亮状态，随后恢复未选中',
    '选项被选中高亮，仅持续一帧后消失',
    '提交按钮短暂不可用',
])
def test_transient_form_feedback_is_not_a_stable_result(observation):
    from task_server.services.ui_video_analysis import _expected_candidate
    assert not _expected_candidate({'observation':observation,'frame_refs':['f1']},'')['text']


@pytest.mark.parametrize('text',[
    '触发下拉刷新，顶部显示刷新状态提示',
    '顶部显示刷新完成提示',
    '页面显示刷新动画',
    '页面顶部出现释放立即刷新提示',
])
def test_refresh_feedback_is_not_a_required_stable_result(text):
    from task_server.services.ui_video_analysis import _stable_visible_observation
    assert _stable_visible_observation(text)==''


def test_refresh_result_preserves_visible_content_after_feedback():
    from task_server.services.ui_video_analysis import _stable_visible_observation
    assert _stable_visible_observation('顶部显示刷新完成提示，试卷列表可见')=='试卷列表可见'
    assert _stable_visible_observation('刷新后试卷列表可见')=='刷新后试卷列表可见'
