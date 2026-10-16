import pytest
import yaml
from task_server.services.ui_case_draft_contract import build_draft_yaml


def draft():
    return {'title':'删除一条测试记录','steps':[
        {'action':'selectRecord','target':'当前打印记录列表中第一条已完成且允许删除的测试记录'},
        {'action':'aiTap','target':'{{本次对象}}卡片内的删除按钮','expected':'删除确认弹窗可见'},
        {'action':'aiTap','target':'删除确认弹窗的确认按钮','expected':'{{本次对象}}消失，{{保留对象}}仍在列表中'},
        {'action':'aiAssert','target':'{{本次对象}}消失，{{保留对象}}仍在列表中'}]}


def test_dynamic_selection_compiles_to_official_query_and_stable_references():
    d=draft()
    for step in d['steps']:step['review_state']='confirmed'
    built=build_draft_yaml(d,'com.kfb.model')
    assert built['ready'], built['questions']
    flow=yaml.safe_load(built['yaml_content'])['tasks'][0]['flow']
    assert flow[1]['aiQuery'].startswith('平台运行时对象选择v1：')
    assert '{{本次对象}}' in built['yaml_content']
    assert len(flow)==len(built['step_ids'][0])


@pytest.mark.parametrize('change',['no_selection','second_selection','fixed_identifier','unknown_reference'])
def test_dynamic_selection_cannot_be_bypassed(change):
    d=draft()
    if change=='no_selection':d['steps'].pop(0)
    if change=='second_selection':d['steps'].insert(2,dict(d['steps'][0]))
    if change=='fixed_identifier':d['steps'][1]['target']='打印编号1554081049356550144的删除按钮'
    if change=='unknown_reference':d['steps'][1]['target']='{{其他对象}}的删除按钮'
    for step in d['steps']:step['review_state']='confirmed'
    with pytest.raises(ValueError):build_draft_yaml(d,'com.kfb.model')


def test_old_runner_cannot_receive_dynamic_case():
    from task_server.services.ui_case_compiler import compile_ui_case
    d=draft()
    source=build_draft_yaml(d,'com.kfb.model')['yaml_content']
    target={'ok':True,'platform':'android','target_package':'com.kfb.model','app_id':'com.kfb.model',
            'platform_mappings':{'android':'com.kfb.model'},'capabilities':{'android_baseline':True,'ui_actions':['launch','aiQuery','aiTap','aiWaitFor','aiAssert']}}
    assert compile_ui_case({'yaml_content':source},target)['block_code']=='dynamic_object_unsupported'
    target['capabilities']['ui_dynamic_object_v1']=True
    assert compile_ui_case({'yaml_content':source},target)['ok']
    target.update(platform='ios',target_package='com.kfb.3d')
    target['platform_mappings']['ios']='com.kfb.3d'
    assert compile_ui_case({'yaml_content':source},target)['ok']


def test_video_import_builds_selection_without_changing_observed_evidence():
    from task_server.services.ui_video_analysis import draft_proposal
    document={'source_id':'uvs_'+'a'*32,'analysis_version':1,'app_id':'com.kfb.model','goal':'',
      'media':{'duration_ms':1000,'frames':[]},'uncertainties':[],
      'events':[{'action':'aiTap','target':'{{本次对象}}卡片内的删除按钮','value':'','start_ms':0,'end_ms':100,
       'frame_refs':['f1'],'evidence_kind':'inferred','uncertainties':[],
       'selection':'当前打印记录列表中第一条已完成且允许删除的测试记录',
       'observation':'点击编号1554081049356550144记录后删除确认框可见','reusable_expected':'删除确认框可见'},
       {'action':'aiTap','target':'确认弹窗的确认按钮','value':'','start_ms':200,'end_ms':300,
       'frame_refs':['f2'],'evidence_kind':'inferred','uncertainties':[],
       'observation':'目标记录消失，其他记录保留',
       'reusable_expected':'{{本次对象}}消失，{{保留对象}}仍显示'}]}
    normalized,snapshot=draft_proposal(document)
    assert normalized['steps'][0]['action']=='selectRecord'
    assert normalized['steps'][2]['expected']=='{{本次对象}}消失，{{保留对象}}仍显示'
    assert '1554081049356550144' in snapshot['events'][0]['observation']
    assert any('选取范围' in q for q in normalized['questions'])


def test_dynamic_yaml_can_be_edited_and_converted_back():
    from task_server.services.ui_yaml_candidate import parse_candidate
    built=build_draft_yaml(draft(),'com.kfb.model')
    parsed=parse_candidate(built['yaml_content'],'com.kfb.model',[])
    assert parsed['steps'][0]['action']=='selectRecord'
    assert yaml.safe_load(build_draft_yaml(parsed,'com.kfb.model')['yaml_content'])==yaml.safe_load(built['yaml_content'])
