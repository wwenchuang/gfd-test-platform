import pytest
from task_server.services import ui_video_trajectory as vt


def test_keyboard_semantic_action_contract():
    contract=vt.action_contract()
    assert 'dismissKeyboard' in vt.INTERACTIONS
    assert contract['allowed_trigger_kinds']['dismissKeyboard']==['touch','pressed_control','navigation','uncertain']
    assert contract['keyboard_target']=='当前页面的软键盘'
    assert contract['keyboard_value']==''


def test_refinement_is_pending_and_requires_complete_support():
    import copy
    fragment=dict(action='aiTap',target='关闭',value='',trigger_frame='f1',trigger_kind='touch',continuation_of='')
    history=[{'fragments':[fragment]}]
    proposed=copy.deepcopy(history)
    proposed[0]['fragments'][0]['trigger_kind']='navigation'
    packet={'owned_frame_ids':['f1']}
    pending=vt._propose_trigger_refinement([fragment],history,proposed,packet)
    assert fragment['trigger_kind']=='touch'
    assert pending[0]['trigger_kind']=='navigation'
    claim={'track':dict(proposed[0]['fragments'][0],event_id='e1')}
    audit={'checks':[dict(event_id='e1',trigger_verdict='supported',outcome_verdict='unclear')], 'issues':[], 'revisit':[], 'multiple_scenarios':False}
    assert not vt._refinement_audit_complete([claim],audit)
    audit['checks'][0]['outcome_verdict']='supported'
    assert vt._refinement_audit_complete([claim],audit)


@pytest.mark.parametrize('field,value',[('target','other'),('value','x'),('action','aiScroll'),('trigger_frame','f2'),('continuation_of','e0'),('trigger_kind','uncertain')])
def test_refinement_cannot_escape_identity(field,value):
    import copy
    fragment=dict(action='aiTap',target='关闭',value='',trigger_frame='f1',trigger_kind='touch',continuation_of='')
    history=[{'fragments':[fragment]}]
    proposed=copy.deepcopy(history)
    proposed[0]['fragments'][0][field]=value
    with pytest.raises(ValueError):
        vt._propose_trigger_refinement([fragment],history,proposed,{'owned_frame_ids':['f1']})


@pytest.mark.parametrize('mutation',['count','order','prefix'])
def test_refinement_preserves_sequence_and_accepted_prefix(mutation):
    import copy
    a=dict(action='aiTap',target='关闭',value='',trigger_frame='f1',trigger_kind='touch',continuation_of='')
    b=dict(a,target='另一个',trigger_frame='f2')
    history=[{'fragments':[a]},{'fragments':[b]}]
    proposed=copy.deepcopy(history)
    proposed[1]['fragments'][0]['trigger_kind']='navigation'
    if mutation=='count':proposed.append(copy.deepcopy(proposed[-1]))
    if mutation=='order':proposed.reverse()
    if mutation=='prefix':proposed[0]['fragments'][0]['observation']='changed'
    before=copy.deepcopy(history)
    with pytest.raises(ValueError):
        vt._propose_trigger_refinement([b],history,proposed,{'owned_frame_ids':['f2']})
    assert history==before


def test_back_navigation_semantic_contract():
    contract=vt.action_contract()
    assert 'navigateBack' in vt.INTERACTIONS
    assert contract['allowed_trigger_kinds']['navigateBack']==['navigation','uncertain']
    assert contract['back_target']=='上一页面'
    assert contract['back_value']==''
