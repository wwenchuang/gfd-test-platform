import copy
import pytest
from task_server.services import ui_video_coverage_resolution as cr


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


def test_three_disjoint_windows_fail_before_any_model_call(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ai_skill_service as ai
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(60)]
    original=[{'action':'aiTap','target':'入口','value':'','frame_refs':[f'f{i}'],'start_ms':i*100,'end_ms':i*100,'observation':'点击入口','uncertainties':[]} for i in (5,25,45)]
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:pytest.fail('over budget model call'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr._repair_coverage(original,{'events':[],'uncertainties':[],'multiple_scenarios':False},frames,6000,tmp_path,lambda:False,[],{'f5','f25','f45'})


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
