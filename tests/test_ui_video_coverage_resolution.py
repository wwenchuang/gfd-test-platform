import copy
import pytest
from task_server.services import ui_video_coverage_resolution as cr


def tap_return_conflict_fixture():
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,5)]
    refs=['f1','f2','f3']
    back={'action':'navigateBack','target':'上一页面','value':'','frame_refs':refs,'start_ms':100,'end_ms':300,'observation':'返回上一级页面','uncertainties':[],'evidence_kind':'inferred'}
    tap={**copy.deepcopy(back),'action':'aiTap','target':'确认打印按钮','observation':'点击确认打印'}
    claim={'source_anchor_ids':['first_pass:0','independent_review:0'],'resolution':'tap_return_conflict','reviewed_event_index':0,
           'evidence':{'before_frame':'f1','interaction_frame':'f2','after_frame':'f3','examined_frame_refs':refs,
                       'before_state':'打印预览页','interaction':'未见点击确认打印按钮，实际执行返回操作',
                       'after_state':'退回模型编辑页','why_not_separate_actions':'同一交互，未见第二次独立点击'}}
    return frames,back,tap,claim


def test_cross_source_tap_return_conflict_requires_review_and_preserves_mapping():
    from task_server.services import ui_video_reconcile as vr
    frames,back,tap,claim=tap_return_conflict_fixture()
    out=cr.validate([claim],{'first_pass':[back],'independent_review':[tap]},[back],frames)
    assert cr.positions(out,'first_pass',[back])=={0:0}
    assert cr.positions(out,'independent_review',[back])=={0:0}
    vr.check_coverage([tap],[back],resolutions=out,source='independent_review')
    assert out[0]['resolution']=='tap_return_conflict'


def test_cross_source_conflict_is_reobserved_and_blocked_for_human_review(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,back,tap,claim=tap_return_conflict_fixture()
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:({'events':[copy.deepcopy(tap)],'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    def model(skill,payload,**kwargs):
        assert any(g.get('kind')=='tap_return_conflict' for g in payload['eligible_resolution_groups'])
        return {'events':[{k:v for k,v in back.items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[copy.deepcopy(claim)],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile([copy.deepcopy(back)],frames,1000,tmp_path,lambda:False,[])
    assert result['events'][0]['action']=='navigateBack'
    assert any('核对' in q and '点击' in q and '返回' in q for q in result['uncertainties'])


def test_conflict_retry_requires_examining_actual_disputed_control(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,back,tap,claim=tap_return_conflict_fixture()
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload))
        candidate=copy.deepcopy(claim)
        if len(calls)==1:
            candidate['evidence']['interaction']='未见针对返回箭头的点击，实际执行返回操作'
        else:
            assert '不能仅排除其他控件的点击' in payload['validation_feedback']
        return {'events':[{k:v for k,v in back.items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[candidate],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr._repair_window([back],{'events':[tap],'uncertainties':[],'multiple_scenarios':False},frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3'})
    assert len(calls)==2
    assert '点击与返回识别冲突' in result['events'][0]['uncertainties'][0]


def test_unresolved_tap_return_hypotheses_reach_review_without_becoming_verified(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,back,tap,claim=tap_return_conflict_fixture()
    candidate={**copy.deepcopy(tap),'target':'确认按钮','observation':'画面变化','uncertainties':['触点位置不确定']}
    claim['evidence']['interaction']='可能是点击或返回，无法确认触点'
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:({'events':[copy.deepcopy(tap)],'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[{k:v for k,v in candidate.items() if k not in {'start_ms','end_ms','evidence_kind'}}],
        'coverage_resolutions':[copy.deepcopy(claim)],'uncertainties':[],'multiple_scenarios':False})
    result=vr.reconcile([copy.deepcopy(back)],frames,1000,tmp_path,lambda:False,[])
    assert result['events'][0]['action']=='aiTap'
    assert any('点击与返回' in q and '核对' in q for q in result['uncertainties'])
    resolved=result['reconciliation_audit']['coverage_resolutions'][0]
    assert resolved['review_required'] is True
    assert {e['action'] for e in resolved['source_anchors'].values()}=={'aiTap','navigateBack'}


def test_unproven_return_is_preserved_only_as_unresolved_review(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,back,tap,claim=tap_return_conflict_fixture()
    claim['evidence']['interaction']='未见点击返回箭头，实际执行返回操作'
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[{k:v for k,v in back.items() if k not in {'start_ms','end_ms','evidence_kind'}}],
        'coverage_resolutions':[copy.deepcopy(claim)],'uncertainties':[],'multiple_scenarios':False})
    result=vr._repair_window([back],{'events':[tap],'uncertainties':[],'multiple_scenarios':False},frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3'})
    audit=result['reconciliation_audit']['coverage_resolutions'][0]
    assert audit['resolution_status']=='unresolved' and audit['review_required'] is True
    assert {e['action'] for e in audit['source_anchors'].values()}=={'aiTap','navigateBack'}
    assert any('点击与返回识别冲突' in q for q in result['events'][0]['uncertainties'])


@pytest.mark.parametrize('mutation',['missing_frame','intervening','different_frames','different_time','missing_candidate_frame'])
def test_unresolved_tap_candidate_never_waives_structural_evidence(mutation):
    frames,back,tap,claim=tap_return_conflict_fixture()
    candidate=copy.deepcopy(tap)
    sources={'first_pass':[back],'independent_review':[tap]}
    if mutation=='missing_frame': claim['evidence']['examined_frame_refs']=['f1','f2']
    if mutation=='intervening': sources['first_pass'].append({**tap,'frame_refs':['f2'],'start_ms':200,'end_ms':200})
    if mutation=='different_frames': tap['frame_refs']=['f2','f3','f4'];tap['end_ms']=400
    if mutation=='different_time': tap['start_ms']=200
    if mutation=='missing_candidate_frame': candidate['frame_refs']=['f1','f2'];candidate['end_ms']=200
    with pytest.raises(ValueError):cr.validate([claim],sources,[candidate],frames)


@pytest.mark.parametrize('mutation',['missing_frame','not_same_source_frame','same_source','no_explicit_negative','wrong_target','intervening','second_click','uncertain','double_negative','result_click'])
def test_cross_source_tap_return_conflict_rejects_unsafe_evidence(mutation):
    frames,back,tap,claim=tap_return_conflict_fixture()
    sources={'first_pass':[back],'independent_review':[tap]}
    if mutation=='missing_frame': claim['evidence']['examined_frame_refs']=['f1','f2']
    if mutation=='not_same_source_frame': tap['frame_refs']=['f2','f3','f4']; tap['end_ms']=400
    if mutation=='same_source': claim['source_anchor_ids']=['first_pass:0','first_pass:1']; sources['first_pass'].append(tap)
    if mutation=='no_explicit_negative': claim['evidence']['interaction']='页面跳转，执行返回操作'
    if mutation=='wrong_target': back['target']='其他页面'
    if mutation=='intervening': sources['first_pass'].append({**tap,'frame_refs':['f2'],'start_ms':200,'end_ms':200})
    if mutation=='second_click': claim['evidence']['interaction']+='。随后点击确认打印按钮'
    if mutation=='uncertain': claim['evidence']['interaction']+='，可能是返回'
    if mutation=='double_negative': claim['evidence']['interaction']='并非未见点击确认打印按钮，实际执行返回操作'
    if mutation=='result_click': back['observation']='用户点击确认打印按钮后返回编辑页面'
    with pytest.raises(ValueError):cr.validate([claim],sources,[back],frames)


def fixture():
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,5)]
    old=[{'action':'aiScroll','target':'弹窗','frame_refs':['f1','f2'],'start_ms':100,'end_ms':200,'uncertainties':[]},
         {'action':'aiTap','target':'确认','frame_refs':['f2','f3'],'start_ms':200,'end_ms':300,'uncertainties':[]}]
    new=[{'action':'aiTap','target':'确认','frame_refs':['f1','f2','f3'],'start_ms':100,'end_ms':300,'uncertainties':[]}]
    claim={'source_anchor_ids':['first_pass:0','first_pass:1'],'resolution':'same_interaction_with_action_correction','reviewed_event_index':0,
           'evidence':{'before_frame':'f1','interaction_frame':'f2','after_frame':'f3','examined_frame_refs':['f1','f2','f3'],
                       'before_state':'弹窗显示','interaction':'未见独立滚动，触点点击确认按钮','after_state':'弹窗消失','why_not_separate_actions':'同一触点及转场'}}
    return frames,old,new,claim


def test_explicit_common_touch_mapping_retains_auditable_source_anchors():
    frames,old,new,claim=fixture()
    out=cr.validate([claim],{'first_pass':old,'independent_review':[]},new,frames)
    assert out[0]['source_anchor_ids']==claim['source_anchor_ids']
    assert cr.positions(out,'first_pass',new)=={0:0,1:0}
    assert old[0]['action']=='aiScroll'


def test_rejected_mapping_feedback_preserves_anchor_responsibility_without_mutation():
    frames,old,new,claim=fixture()
    claim['reviewed_event_index']=0
    new.insert(0,{'action':'aiAssert','target':'自动加载','frame_refs':['f1'],'uncertainties':[]})
    claim['evidence']['examined_frame_refs']=['f1']
    before=copy.deepcopy((old,new,claim))
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    anchor=next(x for x in feedback['anchors_to_recheck'] if x['anchor_id']=='first_pass:0')
    assert anchor['missing_declared_frame_refs']==['f2']
    assert anchor['structural_groups'][0]['anchor_ids']==['first_pass:0','first_pass:1']
    assert feedback['rejected_claims']==[0]
    assert (old,new,claim)==before
    with pytest.raises(ValueError):
        cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('boundary,has_mapping,allowed',[
    ({'f1'},True,True),({'f2'},True,False),({'f3'},True,False),
    ({'f1','f3'},True,False),({'f1'},False,False),
])
def test_boundary_only_allows_validated_mapping_shared_before(boundary,has_mapping,allowed):
    frames,old,new,claim=fixture()
    resolutions=cr.validate([claim],{'first_pass':old},new,frames) if has_mapping else []
    assert cr.boundary_safe(new[0],boundary,resolutions,frames)==allowed


@pytest.mark.parametrize('label',['same_interaction','same_interaction_with_action_correction'])
@pytest.mark.parametrize('mutation', ['missing_frame','foreign_frame','no_common_touch','input','uncertain','no_scroll_explanation','duplicate_anchor','wrong_index','intervening'])
def test_invalid_mapping_rejected(mutation,label):
    frames,old,new,claim=fixture();claim['resolution']=label
    if mutation=='missing_frame':claim['evidence']['examined_frame_refs']=['f2','f3']
    if mutation=='foreign_frame':claim['evidence']['examined_frame_refs'].append('forged')
    if mutation=='no_common_touch':old[0]['frame_refs']=['f1']
    if mutation=='input':old[0]['action']='aiInput'
    if mutation=='uncertain':new[0]['uncertainties']=['不确定']
    if mutation=='no_scroll_explanation':claim['evidence']['interaction']='页面变化'
    if mutation=='duplicate_anchor':claim['source_anchor_ids'].append('first_pass:0')
    if mutation=='wrong_index':claim['reviewed_event_index']=9
    if mutation=='intervening':old.append({'action':'aiTap','target':'另一入口','frame_refs':['f2'],'start_ms':200,'end_ms':200})
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
        cr.validate([claim],{'first_pass':old,'independent_review':[]},new,frames)


@pytest.mark.parametrize('label',['same_interaction','same_interaction_with_action_correction'])
def test_platform_derives_action_correction_only_with_full_evidence(label):
    frames,old,new,claim=fixture();claim['resolution']=label
    result=cr.validate([claim],{'first_pass':old},new,frames)
    assert result[0]['resolution']=='same_interaction_with_action_correction'
    claim['evidence']['interaction']='页面变化，没有确定的操作'
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_mapping_does_not_waive_unmapped_or_missing_result():
    from task_server.services import ui_video_reconcile as vr
    frames,old,new,claim=fixture()
    resolutions=cr.validate([claim],{'first_pass':old},new,frames)
    vr.check_coverage(old,new,resolutions=resolutions,source='first_pass')
    with pytest.raises(ValueError):vr.check_coverage(old,[],resolutions=resolutions,source='first_pass')
    extra={**old[1],'frame_refs':['f4'],'start_ms':400,'end_ms':400}
    with pytest.raises(ValueError):vr.check_coverage(old+[extra],new,resolutions=resolutions,source='first_pass')


def test_reconcile_mapping_survives_outer_check_and_redacts_entire_audit(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=fixture()
    for e in old+new:e.update(value='',observation='弹窗状态',evidence_kind='inferred')
    original=copy.deepcopy(old)
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:({'events':new,'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(windows,'context_frames',lambda *a,**kw:[])
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    # All hypotheses occupying the same interaction must be included.
    claim['source_anchor_ids'].append('independent_review:0')
    claim['evidence']['before_state']='弹窗中显示 PRIVATE_INPUT'
    def model(*args,**kw):
        return {'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[claim],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(old,frames,1000,tmp_path,lambda:False,['PRIVATE_INPUT'])
    assert result['reconciliation_audit']['coverage_resolutions']
    assert 'PRIVATE_INPUT' not in str(result)
    assert old==original


def test_mapping_cannot_merge_scrolls_even_with_shared_frames():
    frames,old,new,claim=fixture()
    for e in old+new:e['action']='aiScroll';e['value']={'direction':'up'}
    old[1]['value']={'direction':'down'}
    claim['resolution']='same_interaction'
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('why,accepted',[
    ('同一次手势，未见第二次按下或新的触点',True),
    ('先滑动一次，随后再次滑动，属于连续操作',False),
    ('同一次手势，但随后重新滑动',False),
])
def test_scroll_requires_single_gesture_evidence_without_contradiction(why,accepted):
    frames,old,new,claim=fixture()
    for e in old+new:e['action']='aiScroll';e['value']={'direction':'up','scrollType':'singleAction'}
    claim['resolution']='same_interaction'
    claim['evidence']['interaction']='画面内容连续向上移动'
    claim['evidence']['why_not_separate_actions']=why
    if accepted:assert cr.validate([claim],{'first_pass':old},new,frames)
    else:
        with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)

@pytest.mark.parametrize('second_fails',[False,True])
def test_disjoint_repair_translates_ids_and_is_atomic(monkeypatch,tmp_path,second_fails):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(30)]
    original=[];reviewed=[];responses=[];secrets=[];calls=[]
    for offset in (0,20):
        _,old,new,claim=fixture()
        for e in old+new:
            e.update(value='',observation='弹窗状态',evidence_kind='inferred',target='SECOND_WINDOW_SECRET入口')
            e['frame_refs']=[f'f{int(r[1:])+offset}' for r in e['frame_refs']]
            e['start_ms']+=offset*100;e['end_ms']+=offset*100
        for key in ('before_frame','interaction_frame','after_frame'):claim['evidence'][key]=f'f{int(claim["evidence"][key][1:])+offset}'
        claim['evidence']['examined_frame_refs']=[f'f{int(r[1:])+offset}' for r in claim['evidence']['examined_frame_refs']]
        claim['evidence']['before_state']='显示 SECOND_WINDOW_SECRET'
        claim['source_anchor_ids'].append('independent_review:0')
        original.extend(old);reviewed.extend(new)
        responses.append({'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                          'coverage_resolutions':[claim],'uncertainties':[],'multiple_scenarios':False})
    checked={'events':reviewed,'uncertainties':[],'multiple_scenarios':False};saved=copy.deepcopy(checked)
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    def model(*a,**kw):
        calls.append(1)
        if len(calls)==2:
            if second_fails:raise RuntimeError('provider unavailable')
            secrets.append('SECOND_WINDOW_SECRET')
        return copy.deepcopy(responses[len(calls)-1])
    monkeypatch.setattr(ai,'run_ai_skill',model)
    if second_fails:
        with pytest.raises(RuntimeError):vr._repair_coverage(original,checked,frames,3000,tmp_path,lambda:False,secrets,{'f2','f22'})
    else:
        result=vr._repair_coverage(original,checked,frames,3000,tmp_path,lambda:False,secrets,{'f2','f22'})
        resolutions=result['reconciliation_audit']['coverage_resolutions']
        assert cr.positions(resolutions,'first_pass',result['events'])=={0:0,1:0,2:1,3:1}
        assert cr.positions(resolutions,'independent_review',result['events'])=={0:0,1:1}
        assert 'SECOND_WINDOW_SECRET' not in str(result)
    assert len(calls)==2 and checked==saved


def test_six_disjoint_windows_fail_before_any_model_call(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ai_skill_service as ai
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(70)]
    original=[{'action':'aiTap','target':'入口','value':'','frame_refs':[f'f{i}'],'start_ms':i*100,'end_ms':i*100,'observation':'点击入口','uncertainties':[]} for i in (5,15,25,35,45,55)]
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:pytest.fail('over budget model call'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr._repair_coverage(original,{'events':[],'uncertainties':[],'multiple_scenarios':False},frames,7000,tmp_path,lambda:False,[],{'f5','f15','f25','f35','f45','f55'})


def test_production_skill_schema_accepts_only_structured_resolution():
    from task_server.services import ai_skill_service as ai
    from pathlib import Path
    import json
    frames,old,new,claim=fixture()
    schema=json.loads((Path(__file__).parents[1]/'ai_skills/schemas/ui_video_observe.schema.json').read_text())
    import jsonschema
    sample={'events':[{'action':'aiTap','target':'确认','value':'','frame_refs':['f1','f2','f3'],'observation':'按钮点击'}],
            'coverage_resolutions':[claim],'uncertainties':[],'multiple_scenarios':False}
    jsonschema.validate(sample,schema)
    sample['coverage_resolutions'][0]['evidence']['extra']='forged'
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(sample,schema)

@pytest.mark.parametrize('action',['aiTap','aiAssert'])
def test_preserved_boundary_cannot_be_reexecuted(monkeypatch,tmp_path,action):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=fixture()
    preserved={'action':'aiTap','target':'打开弹窗','value':'','frame_refs':['f1','f2'],'start_ms':100,'end_ms':200,'observation':'点击打开弹窗','evidence_kind':'inferred','uncertainties':[]}
    scroll={**preserved,'action':'aiScroll','target':'弹窗','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f2','f3'],'start_ms':200,'end_ms':300}
    checked={'events':[preserved],'uncertainties':[],'multiple_scenarios':False};calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    def model(*args,**kw):
        calls.append(1)
        assert args[1]['preserved_interaction_boundary_frames']==['f2']
        assert 'f1' in args[1]['context_frame_ids']
        assert 'f1' not in {f['frame_id'] for f in args[1]['frames']}
        return {'events':[{'action':action,'target':'弹窗内容','value':'','frame_refs':['f2','f3'],'observation':'未见滚动，实际为自动加载','uncertainties':[]}],'coverage_resolutions':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    if action=='aiTap':
        with pytest.raises(ValueError):vr._repair_coverage([preserved,scroll],checked,frames,1000,tmp_path,lambda:False,[],{'f3'})
        assert len(calls)==2
    else:
        result=vr._repair_coverage([preserved,scroll],checked,frames,1000,tmp_path,lambda:False,[],{'f3'})
        assert [e['action'] for e in result['events']]==['aiTap','aiAssert']
        assert result['events'][0]['target']=='打开弹窗'


def test_diagnostics_identify_foreign_touch_and_undeclared_state_frames():
    frames,old,new,claim=fixture()
    claim['evidence']['interaction_frame']='f1'
    claim['evidence']['examined_frame_refs']=['f2','f3']
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    anchors={a['anchor_id']:a for a in feedback['anchors_to_recheck']}
    assert anchors['first_pass:0']['interaction_frame_belongs_to_anchor'] is True
    assert anchors['first_pass:1']['interaction_frame_belongs_to_anchor'] is False
    assert anchors['first_pass:1']['undeclared_state_frames']==['f1','f1']
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_diagnostics_keep_all_anchors_when_claims_split_one_result():
    frames,old,new,claim=fixture()
    second=copy.deepcopy(claim);second['source_anchor_ids']=['first_pass:1']
    first=copy.deepcopy(claim);first['source_anchor_ids']=['first_pass:0']
    feedback=cr.diagnose([first,second],{'first_pass':old},new,frames)
    assert all(a['same_result_claim_anchor_ids']==['first_pass:0','first_pass:1'] for a in feedback['anchors_to_recheck'])
    with pytest.raises(ValueError):cr.validate([first,second],{'first_pass':old},new,frames)


@pytest.mark.parametrize('value',[[],{}])
def test_diagnostics_handle_non_string_state_frame(value):
    frames,old,new,claim=fixture()
    claim['evidence']['before_frame']=value
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    assert feedback['rejected_claims']==[0]
    assert feedback['anchors_to_recheck'][0]['undeclared_state_frames']==[value]


def return_gesture_fixture():
    frames,old,new,claim=fixture()
    old[0].update(action='aiTap',target='左上角返回箭头',frame_refs=['f1','f3'],start_ms=100,end_ms=300,value='')
    old[1].update(target='左上角返回箭头',frame_refs=['f2','f3','f4'],start_ms=200,end_ms=400,value='')
    new[0].update(target='左上角返回箭头',frame_refs=['f1','f2','f4'],value='')
    for e in old:e['observation']='原片为连续返回手势，页面横移进入上一级'
    claim.update(resolution='same_return_gesture')
    claim['evidence'].update(after_frame='f4',examined_frame_refs=['f1','f2','f3','f4'],before_state='模型详情页左上角返回箭头可见',interaction='同一次连续返回手势，箭头可见，页面横移',after_state='进入上一级列表页面',why_not_separate_actions='未见第二次按下或新的手势，仅同一次返回导航')
    return frames,old,new,claim


@pytest.mark.parametrize('outcome',['corrected','still_unclear','second_navigation'])
def test_return_only_retry_asks_for_grounded_explanation_and_fails_closed(monkeypatch,tmp_path,outcome):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=return_gesture_fixture()
    for e in old+new:e.update(action='navigateBack',target='上一页面')
    for e in old:e['evidence_kind']='inferred'
    claim['evidence']['before_state']='模型详情页可见'
    claim['source_anchor_ids']+=['independent_review:0','independent_review:1']
    new[0]['observation']='一次连续返回手势，页面回到上一级'
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload))
        candidate=copy.deepcopy(claim)
        if len(calls)==1 or outcome=='still_unclear':
            candidate['evidence']['interaction']='同一次页面横移'
        elif outcome=='second_navigation':
            candidate['evidence']['interaction']='同一次返回手势，随后再次返回'
        return {'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[candidate],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    args=(copy.deepcopy(old),{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3','f4'})
    if outcome=='corrected':
        result=vr._repair_window(*args,attempt_limit=2,retry_return_gesture_only=True)
        assert len(result['reconciliation_audit']['coverage_resolutions'])==1
    else:
        with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
            vr._repair_window(*args,attempt_limit=2,retry_return_gesture_only=True)
    assert len(calls)==2
    assert any(x['reason']=='return_gesture_not_explicit' for x in calls[1]['resolution_diagnostics']['result_evidence_to_recheck'])
    assert 'interaction' in calls[1]['validation_feedback'] and '不能' in calls[1]['validation_feedback']


def test_return_only_extra_call_is_not_used_for_other_rejections(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=return_gesture_fixture()
    for e in old+new:e.update(action='navigateBack',target='上一页面')
    for e in old:e['evidence_kind']='inferred'
    claim['source_anchor_ids']+=['independent_review:0','independent_review:1']
    claim['evidence']['interaction']='同一次返回手势，随后再次返回'
    new[0]['observation']='一次连续返回手势，页面回到上一级'
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[claim],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
        vr._repair_window(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3','f4'},attempt_limit=2,retry_return_gesture_only=True)
    assert len(calls)==1


def test_continuous_return_accepts_actual_touch_inside_overlapping_anchor_interval():
    frames,old,new,claim=return_gesture_fixture()
    result=cr.validate([claim],{'first_pass':old},new,frames)
    assert result[0]['resolution']=='same_return_gesture'
    assert result[0]['evidence']['interaction_frame']=='f2'
    assert result[0]['source_anchors']['first_pass:0']['frame_refs']==['f1','f3']


def test_native_return_requires_confirmed_single_gesture_without_invented_arrow():
    frames,old,new,claim=return_gesture_fixture()
    for event in old+new:event.update(action='navigateBack',target='上一页面')
    for event in old:event['evidence_kind']='inferred'
    claim['evidence']['before_state']='模型详情页可见'
    assert cr.validate([claim],{'first_pass':old},new,frames)[0]['resolution']=='same_return_gesture'
    claim['evidence']['why_not_separate_actions']='先返回一次，随后再次返回'
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('mutation',['changed_target','mixed_action','no_shared_frame','uncertain','missing_frame','intervening_return'])
def test_native_return_merge_rejects_missing_or_conflicting_evidence(mutation):
    frames,old,new,claim=return_gesture_fixture()
    for event in old+new:event.update(action='navigateBack',target='上一页面')
    claim['evidence']['before_state']='模型详情页可见'
    if mutation=='changed_target':old[1]['target']='其他页面'
    if mutation=='mixed_action':old[0]['action']='aiTap'
    if mutation=='no_shared_frame':old[0]['frame_refs']=['f1']
    if mutation=='uncertain':new[0]['uncertainties']=['是否两次返回不清楚']
    if mutation=='missing_frame':claim['evidence']['examined_frame_refs'].remove('f3')
    if mutation=='intervening_return':old.append({**old[1],'frame_refs':['f3'],'start_ms':300,'end_ms':300})
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('source',['first_pass','independent_review'])
def test_native_return_merge_rejects_intervening_interaction_extending_past_returns(source):
    frames,old,new,claim=return_gesture_fixture()
    frames.append({'frame_id':'f5','timestamp_ms':500})
    for event in old+new:event.update(action='navigateBack',target='上一页面')
    claim['evidence']['before_state']='模型详情页可见'
    other={'action':'aiTap','target':'另一控件','frame_refs':['f2','f5'],'start_ms':200,'end_ms':500}
    sources={'first_pass':old,'independent_review':[]};sources[source].append(other)
    with pytest.raises(ValueError):cr.validate([claim],sources,new,frames)


@pytest.mark.parametrize('source',['first_pass','independent_review'])
def test_native_return_merge_rejects_unmapped_interaction_at_first_frame(source):
    frames,old,new,claim=return_gesture_fixture()
    for event in old+new:event.update(action='navigateBack',target='上一页面')
    claim['evidence']['before_state']='模型详情页可见'
    other={'action':'aiTap','target':'另一控件','frame_refs':['f1','f3'],'start_ms':100,'end_ms':300}
    sources={'first_pass':old,'independent_review':[]};sources[source].append(other)
    with pytest.raises(ValueError):cr.validate([claim],sources,new,frames)


def test_native_return_reconcile_uses_frame_review_and_preserves_coverage(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=return_gesture_fixture()
    for event in old+new:event.update(action='navigateBack',target='上一页面')
    for event in old:event['evidence_kind']='inferred'
    claim['evidence']['before_state']='模型详情页可见'
    claim['source_anchor_ids']+=['independent_review:0','independent_review:1']
    new[0]['observation']='一次连续返回手势，页面回到上一级'
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:({'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(windows,'context_frames',lambda *a,**kw:[])
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kw):
        calls.append(payload)
        return {'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[claim],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(copy.deepcopy(old),frames,1000,tmp_path,lambda:False,[])
    assert len(calls)==1 and calls[0]['eligible_resolution_groups']
    assert [e['action'] for e in result['events']]==['navigateBack']
    assert result['reconciliation_audit']['coverage_resolutions'][0]['resolution']=='same_return_gesture'


@pytest.mark.parametrize('mutation',['generic','different_target','input','outside_interval','no_overlap','no_arrow','second_gesture','uncertain','missing_frame','intervening'])
def test_return_gesture_does_not_relax_other_interaction_guards(mutation):
    frames,old,new,claim=return_gesture_fixture()
    if mutation=='generic':claim['resolution']='same_interaction'
    if mutation=='different_target':old[0]['target']='删除按钮'
    if mutation=='input':old[0]['action']='aiInput'
    if mutation=='outside_interval':old[0]['start_ms']=250
    if mutation=='no_overlap':old[0]['frame_refs']=['f1']
    if mutation=='no_arrow':claim['evidence']['before_state']='模型详情页面显示'
    if mutation=='second_gesture':claim['evidence']['why_not_separate_actions']='再次按下并完成第二次返回'
    if mutation=='uncertain':claim['evidence']['interaction']='可能为一次返回手势'
    if mutation=='missing_frame':claim['evidence']['examined_frame_refs'].remove('f3')
    if mutation=='intervening':old.append({'action':'aiTap','target':'其他按钮','start_ms':250,'end_ms':260,'frame_refs':['f3']})
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid'):
        cr.validate([claim],{'first_pass':old},new,frames)


def test_return_again_may_refer_to_separate_preceding_navigation():
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,7)]
    earlier={'action':'navigateBack','target':'上一页面','value':'','frame_refs':['f1','f2'],
             'start_ms':100,'end_ms':200,'observation':'从预览页返回编辑页','uncertainties':[]}
    full={**earlier,'frame_refs':['f3','f4','f5'],'start_ms':300,'end_ms':500,
          'observation':'用户再次执行返回操作，f3可见触点，页面右滑至详情页'}
    suffix={**earlier,'frame_refs':['f4','f5'],'start_ms':400,'end_ms':500,
            'observation':'页面右滑转场并退回详情页'}
    new=[copy.deepcopy(full)]
    claim={'source_anchor_ids':['independent_review:1','independent_review:2'],
           'resolution':'same_return_gesture','reviewed_event_index':0,
           'evidence':{'before_frame':'f3','interaction_frame':'f4','after_frame':'f5','examined_frame_refs':['f3','f4','f5'],
                       'before_state':'编辑页面','interaction':'同一次连续返回手势','after_state':'退回详情页',
                       'why_not_separate_actions':'未见第二次按下或新的手势'}}
    result=cr.validate([claim],{'independent_review':[earlier,full,suffix]},new,frames)
    assert result[0]['review_required'] is True and result[0]['resolution_status']=='unresolved'
    claim['source_anchor_ids']=['independent_review:0','independent_review:1']
    with pytest.raises(ValueError):
        cr.validate([claim],{'independent_review':[full,suffix]},new,frames)


def test_return_gesture_single_interaction_can_be_explained_in_rationale():
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='原片为返回手势，映射可见返回箭头'
    claim['evidence']['why_not_separate_actions']='同一次连续手势，未见第二次按下或导航'
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('mutation',['contradictory_rationale','explicit_second_return','no_gesture_evidence'])
def test_return_gesture_rejects_contradictions_and_ineligible_old_anchors(mutation):
    frames,old,new,claim=return_gesture_fixture()
    if mutation=='contradictory_rationale':claim['evidence']['why_not_separate_actions']='未见第二次按下，但随后再次返回并完成第二次导航'
    if mutation=='explicit_second_return':old[1]['observation']='再次点击左上角返回箭头，完成第二次返回'
    if mutation=='no_gesture_evidence':
        for e in old:e['observation']='点击返回按钮'
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_return_repeat_contradiction_is_reported_for_targeted_retry():
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='原片为一次返回手势，随后再次返回'
    with pytest.raises(ValueError) as caught:
        cr.validate([claim],{'first_pass':old},new,frames)
    assert caught.value.coverage_reason=='return_repeat_contradiction'
    assert cr.diagnose([claim],{'first_pass':old},new,frames)['result_evidence_to_recheck'][0]['reason']=='return_repeat_contradiction'


def test_native_return_contradiction_retries_with_reason_and_original_frames(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=return_gesture_fixture()
    for event in old+new:event.update(action='navigateBack',target='上一页面')
    for event in old:event['evidence_kind']='inferred'
    claim['evidence']['before_state']='模型详情页可见'
    claim['source_anchor_ids']+=['independent_review:0','independent_review:1']
    new[0]['observation']='一次连续返回手势，页面回到上一级'
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:({'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},[]))
    monkeypatch.setattr(windows,'context_frames',lambda *a,**kw:[])
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload)); c=copy.deepcopy(claim)
        if len(calls)==1:c['evidence']['interaction']='原片为一次返回手势，随后再次返回'
        else:
            assert 'return_repeat_contradiction' in str(payload['resolution_diagnostics'])
            assert '确有第二次手势则保留独立操作' in payload['validation_feedback']
            assert '原生navigateBack无需可见返回箭头' in payload['validation_feedback']
            assert payload['frames']==calls[0]['frames']
        return {'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[c],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(copy.deepcopy(old),frames,1000,tmp_path,lambda:False,[])
    assert len(calls)==2 and [e['action'] for e in result['events']]==['navigateBack']


def test_return_gesture_can_explain_old_two_click_misclassification():
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='原片为返回手势。旧识别将其误判为两次独立的点击操作，实际为同一次连续返回导航。'
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('note',['点击返回按钮，触点没有位移轨迹','点击返回按钮，未见触点轨迹','触点无位移','未见返回手势','触点不存在位移轨迹','这不属于返回手势'])
def test_negated_gesture_description_cannot_authorize_exception(note):
    frames,old,new,claim=return_gesture_fixture()
    for event in old:event['observation']=note
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('explanation', [
    '旧锚点将此单次手势的不同阶段（起始与转场）误识别为两次独立点击。',
    '首轮识别把同一手势误拆成两次返回操作。',
    '旧识别将其拆分为多次点击或重复记录。实际仅发生一次导航交互。',
    '旧识别将其误判为点击或拆分为多次操作。实际为同一次导航手势。',
    '旧识别将其误判为两次独立的点击操作。',
    '旧锚点将此手势分别识别为两次点击或重复操作，实际为同一次连续返回手势。',
])
def test_return_misclassification_explanation_does_not_claim_new_action(explanation):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='同一次返回手势。'+explanation
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('tail', [
    '但随后再次按下返回。', '，实际又进行了第二次返回。',
    '；之后重新点击返回。', '，随后再次导航。',
])
def test_old_misclassification_cannot_hide_new_return(tail):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='同一次返回手势。旧锚点将起始与转场误识别为两次独立点击'+tail
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)

@pytest.mark.parametrize('note', [
    '旧锚点将此手势分别识别为两次点击或重复操作，实际又进行了第二次返回。',
    '实际分别识别为两次点击，之后同一次返回手势结束。',
])
def test_historical_wording_does_not_hide_observed_repeated_action(note):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='同一次返回手势。'+note
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)

@pytest.mark.parametrize('state', ['模型详情页，左上角有返回箭头', '模型详情页显示返回按钮'])
def test_return_control_visibility_accepts_explicit_presence(state):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['before_state']=state
    assert cr.validate([claim],{'first_pass':old},new,frames)

@pytest.mark.parametrize('state', ['左上角没有返回箭头', '未显示返回按钮', '返回箭头不可见', '返回按钮没有显示'])
def test_return_control_negation_is_not_visibility_evidence(state):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['before_state']=state
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_rejected_return_mapping_reports_time_mismatch_without_approving_claim():
    frames,old,new,claim=return_gesture_fixture()
    old[1]['start_ms']=250
    old[1]['frame_refs']=['f3','f4']
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    anchor=next(x for x in feedback['anchors_to_recheck'] if x['anchor_id']=='first_pass:1')
    assert anchor['interaction_frame_within_anchor_time'] is False
    assert anchor['anchor_start_ms']==250
    assert anchor['anchor_end_ms']==old[1]['end_ms']
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)

@pytest.mark.parametrize('state', ['界面只有返回箭头的说明文字', '左上角有返回箭头，但返回箭头不可见', '左上角有返回按钮，但实际控件隐藏'])
def test_description_or_contradiction_cannot_prove_visible_return(state):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['before_state']=state
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('field,text,reason',[
    ('before_state','模型详情页面显示','return_control_not_visible'),
    ('interaction','同一次页面横移','return_gesture_not_explicit'),
    ('why_not_separate_actions','同一次连续返回导航','return_second_press_not_excluded'),
])
def test_return_rejection_has_content_free_reason(field,text,reason):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence'][field]=text
    with pytest.raises(ValueError) as caught:
        cr.validate([claim],{'first_pass':old},new,frames)
    assert caught.value.coverage_reason==reason
    assert str(caught.value)=='video_coverage_resolution_invalid'


@pytest.mark.parametrize('explanation',[
    '旧识别将其分为两次点击，实际为同一次连续手势导致的页面转场。',
    '旧识别将其分为两次点击（分别对应前后帧的触点状态），实际为同一次连续手势导致的页面转场。',
])
def test_historical_partition_with_immediate_single_gesture_correction(explanation):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='同一次返回手势。'+explanation
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('explanation',[
    '旧识别将其分为两次点击，实际发生第二次返回。',
    '旧识别将其分为两次点击（随后再次按下），实际为同一次连续手势。',
    '旧识别将其分为两次点击，实际为同一次连续手势。随后重新点击返回。',
    '原片发生两次点击，实际为同一次连续手势。',
])
def test_historical_partition_cannot_swallow_actual_repeat(explanation):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='同一次返回手势。'+explanation
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_return_motion_and_navigation_can_be_separate_clauses():
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='画面显示从右向左的长距离滑动手势，触发返回导航。'
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('interaction',[
    '同一次滑动手势，页面内容滚动。',
    '未见滑动手势，页面发生返回导航。',
    '同一次滑动手势，但未触发返回导航。',
    '同一次点击按钮，触发返回导航。',
    '同一次触点位移，页面内容滚动。',
    '同一次侧滑，但未触发返回导航。',
    '同一次返回手势，但未触发返回导航。',
])
def test_return_equivalent_wording_still_requires_motion_and_navigation(interaction):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']=interaction
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_return_started_before_overlapping_suffix_keeps_original_touch():
    frames,old,new,claim=return_gesture_fixture()
    old[0].update(frame_refs=['f1','f2','f3'],start_ms=100,end_ms=300)
    old[1].update(frame_refs=['f3','f4'],start_ms=300,end_ms=400)
    new[0]['frame_refs']=['f1','f2','f3','f4']
    before=copy.deepcopy((old,new,claim))
    result=cr.validate([claim],{'first_pass':old},new,frames)
    assert result[0]['evidence']['interaction_frame']=='f2'
    assert (old,new,claim)==before


@pytest.mark.parametrize('mutation',['bad_time','no_bridge','bridge_is_after','touch_before_all','late_touch','second_press','intervening'])
def test_return_suffix_exception_needs_continuous_shared_evidence(mutation):
    frames,old,new,claim=return_gesture_fixture()
    old[0].update(frame_refs=['f1','f2','f3'],start_ms=100,end_ms=300)
    old[1].update(frame_refs=['f3','f4'],start_ms=300,end_ms=400)
    new[0]['frame_refs']=['f1','f2','f3','f4']
    if mutation=='bad_time':old[1]['start_ms']=250
    if mutation=='no_bridge':new[0]['frame_refs'].remove('f3')
    if mutation=='bridge_is_after':claim['evidence']['after_frame']='f3'
    if mutation=='touch_before_all':
        frames.insert(0,{'frame_id':'f0','timestamp_ms':0})
        claim['evidence'].update(before_frame='f0',interaction_frame='f0')
        claim['evidence']['examined_frame_refs'].insert(0,'f0')
        new[0]['frame_refs'].insert(0,'f0')
    if mutation=='late_touch':
        claim['evidence']['interaction_frame']='f4'
    if mutation=='second_press':old[1]['observation']='再次点击返回，完成第二次导航'
    if mutation=='intervening':old.append({'action':'aiInput','target':'搜索','start_ms':250,'end_ms':260,'frame_refs':['f3']})
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_diagnostics_identify_state_evidence_missing_from_result():
    frames,old,new,claim=fixture()
    new[0]['frame_refs']=['f2','f3']
    before=copy.deepcopy((old,new,claim))
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    detail=feedback['result_evidence_to_recheck'][0]
    assert detail['claim_index']==0
    assert detail['missing_result_state_frames']==['f1']
    assert detail['has_same_source_duplicates'] is True
    assert (old,new,claim)==before
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_diagnostics_reject_cross_source_one_to_one_merge():
    frames,old,new,claim=fixture()
    claim['source_anchor_ids']=['first_pass:0','independent_review:0']
    feedback=cr.diagnose([claim],{'first_pass':[old[0]],'independent_review':[old[1]]},new,frames)
    assert feedback['result_evidence_to_recheck'][0]['has_same_source_duplicates'] is False


@pytest.mark.parametrize('index',[None,True,-1,99,[],{}])
def test_diagnostics_invalid_result_index_is_bounded(index):
    frames,old,new,claim=fixture();claim['reviewed_event_index']=index
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    assert feedback['result_evidence_to_recheck'][0]['valid_result_index'] is False


@pytest.mark.parametrize('single',['一次连续的返回手势','一次连续返回手势','一次完整的返回手势'])
def test_return_gesture_accepts_explicit_singular_equivalent(single):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']='原片显示'+single+'，页面横移'
    claim['evidence']['why_not_separate_actions']='未见第二次按下或再次导航'
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('single',['第一次返回手势','有一次返回手势后又返回','不是一次连续的返回手势','未见一次完整的返回手势'])
def test_return_gesture_singular_wording_does_not_accept_multiple_or_negation(single):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']=single
    claim['evidence']['why_not_separate_actions']='未见第二次按下或再次导航'
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('negative',['非两次独立点击','并非两次独立点击','不是两次单独的返回操作'])
def test_return_explicit_negation_is_not_a_positive_repeat(negative):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']+='。'+negative+'。'
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('tail',['，随后再次点击返回','。实际发生第二次返回','，但确实有两次点击'])
def test_return_negation_cannot_hide_a_later_positive_repeat(tail):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']+='。非两次独立点击'+tail
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_return_bridge_diagnostic_identifies_missing_real_frame_without_accepting():
    frames,old,new,claim=return_gesture_fixture()
    old[0].update(frame_refs=['f1','f2','f3'],start_ms=100,end_ms=300)
    old[1].update(frame_refs=['f3','f4'],start_ms=300,end_ms=400)
    new[0]['frame_refs']=['f1','f2','f4']
    before=copy.deepcopy((old,new,claim))
    feedback=cr.diagnose([claim],{'first_pass':old},new,frames)
    item=feedback['result_evidence_to_recheck'][0]
    assert item['reason']=='return_shared_intermediate_frame_missing'
    assert item['shared_intermediate_frame_candidates']==['f3']
    assert (old,new,claim)==before
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_return_bridge_diagnostic_does_not_offer_after_state_as_bridge():
    frames,old,new,claim=return_gesture_fixture()
    old[0].update(frame_refs=['f1','f2','f3'],start_ms=100,end_ms=300)
    old[1].update(frame_refs=['f3','f4'],start_ms=300,end_ms=400)
    new[0]['frame_refs']=['f1','f2','f3','f4']
    claim['evidence']['after_frame']='f3'
    item=cr.diagnose([claim],{'first_pass':old},new,frames)['result_evidence_to_recheck'][0]
    assert item['reason']=='return_shared_intermediate_frame_missing'
    assert item['shared_intermediate_frame_candidates']==[]


@pytest.mark.parametrize('statement',[
    '并非两次独立点击，而是三次独立点击',
    '非两次独立点击后又点击返回',
    '并非两次独立点击，实际第三次返回',
])
def test_return_negation_keeps_other_positive_repeat_counts(statement):
    frames,old,new,claim=return_gesture_fixture()
    claim['evidence']['interaction']+='。'+statement
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_nonshared_press_frame_returns_shared_candidates_without_authorizing_merge():
    frames,old,new,claim=fixture()
    old[0].update(action='aiTap',target='确认',frame_refs=['f1','f3'],end_ms=300)
    old[1].update(frame_refs=['f2','f3','f4'],end_ms=400)
    new[0].update(frame_refs=['f1','f2','f4'],end_ms=400)
    claim.update(resolution='same_interaction')
    claim['evidence'].update(interaction_frame='f2',after_frame='f4',examined_frame_refs=['f1','f2','f3','f4'])
    before=copy.deepcopy((old,new,claim))
    with pytest.raises(ValueError) as caught:cr.validate([claim],{'first_pass':old},new,frames)
    assert caught.value.coverage_reason=='interaction_frame_not_shared'
    diagnostic=cr.diagnose([claim],{'first_pass':old},new,frames)
    assert diagnostic['result_evidence_to_recheck'][0]['shared_interaction_frame_candidates']==['f3']
    assert (old,new,claim)==before
    # A later frame of the same held press is legal only after the model
    # explicitly rechecks it and includes it in the actual result evidence.
    claim['evidence']['interaction_frame']='f3';new[0]['frame_refs']=['f1','f3','f4']
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('motion_review',[False,True])
def test_failed_return_merge_keeps_categorical_candidates_without_model_text(monkeypatch,tmp_path,motion_review):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    frames,old,new,claim=return_gesture_fixture()
    for e in old+new:
        e.update(action='navigateBack',target='上一页面')
        e['observation']='连续返回手势，触点位移 secret-model-observation'
    claim['evidence']['before_state']='模型详情页可见'
    claim['evidence']['interaction']='同一次导航回到上页'
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    def model(skill,payload,**kw):
        if motion_review:
            assert all('action' not in item for group in payload['coverage_checks'].values() for item in group)
        return {'events':[{k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[copy.deepcopy(claim)],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid') as caught:
        vr._repair_window(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},
            frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3'},attempt_limit=1,
            motion_review_refs={'f1'} if motion_review else ())
    assert caught.value.coverage_reason=='return_gesture_not_explicit'
    evidence=caught.value.coverage_candidates
    assert set(evidence)=={'first_pass','independent_review'}
    assert all(set(item)=={'action','frame_refs'} for group in evidence.values() for item in group)
    assert 'secret-model-observation' not in str(evidence)
    assert len(evidence['first_pass'])==len(old)


@pytest.mark.parametrize('blind_motion',[False,True])
def test_f834_return_window_failed_merge_retains_all_ordered_candidate_frames(monkeypatch,tmp_path,blind_motion):
    """Anonymized action and timestamp shape from the failed f834 v2 segment."""
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai,ui_video_touch_detail as detail
    from task_server.workers import ui_video_worker as worker
    ms=[187567,187700,188000,188500,188667,189000,189233,189400,189700,190100,190267,190900]
    frames=[{'frame_id':f'frame-{332+i}','timestamp_ms':t} for i,t in enumerate(ms)]
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    def event(action,nums):
        refs=[f'frame-{i}' for i in nums]
        return {'action':action,'target':'上一页面' if action=='navigateBack' else '测试按钮',
                'value':'','frame_refs':refs,'start_ms':times[refs[0]],'end_ms':times[refs[-1]],
                'observation':'连续返回手势，触点位移 sensitive-marker','uncertainties':[]}
    first=[event('navigateBack',r) for r in ([332,333,334],[334,335,336,337],[337,338,339,340],[340,341,342])]
    review=[event('aiTap',[332,333,334]),event('navigateBack',[335,336,337]),
            event('navigateBack',[338,339,340]),event('navigateBack',[341,342]),
            event('navigateBack',[341,342,343])]
    merged=review[-1]
    claim={'source_anchor_ids':['independent_review:3','independent_review:4'],
           'resolution':'same_return_gesture','reviewed_event_index':0,
           'evidence':{'before_frame':'frame-341','interaction_frame':'frame-341','after_frame':'frame-343',
                       'examined_frame_refs':['frame-341','frame-342','frame-343'],
                       'before_state':'测试页面','interaction':'同一次导航回到上页',
                       'after_state':'退回上一级','why_not_separate_actions':'未见第二次按下或新的手势'}}
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    monkeypatch.setattr(detail,'append_details',lambda *a,**kw:[])
    def model(skill,payload,**kwargs):
        if blind_motion:
            assert all('action' not in item for group in payload['coverage_checks'].values() for item in group)
        return {'events':[{k:v for k,v in merged.items() if k not in {'start_ms','end_ms','evidence_kind'}}],
                'coverage_resolutions':[claim],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_coverage_resolution_invalid') as caught:
        vr._repair_window(first,{'events':review,'uncertainties':[],'multiple_scenarios':False},
                          frames,200000,tmp_path,lambda:False,[],set(times),attempt_limit=1,
                          motion_review_refs={'frame-332'} if blind_motion else ())
    assert caught.value.coverage_reason=='return_gesture_not_explicit'
    expected={name:[{'action':e['action'],'frame_refs':e['frame_refs']} for e in group]
              for name,group in [('first_pass',first),('independent_review',review)]}
    assert caught.value.coverage_candidates==expected
    saved=worker._private_target_failure(caught.value,'video_coverage_resolution_invalid',{'media':{'frames':frames}})
    assert saved['coverage_candidates']==expected
    assert 'sensitive-marker' not in str(saved) and '测试按钮' not in str(saved)
