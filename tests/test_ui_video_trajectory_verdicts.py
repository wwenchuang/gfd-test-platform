"""Audit judges evidence; immutable fragment references belong to the platform."""
import copy
import json
from pathlib import Path

import pytest
from task_server.services import ui_video_trajectory as vt


def frames():return [{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(4)]
def tracks(same=False):return [{'event_id':'e1','trigger_frame':'f1','frame_refs':['f1','f2'],
                               'outcome':{'frame_refs':['f1' if same else 'f3']}}]
def check(**kw):return dict(event_id='e1',trigger_verdict='supported',outcome_verdict='supported',question='',**kw)


@pytest.mark.parametrize('same',[False,True])
def test_platform_binds_fragment_anchors_and_preserves_verdicts(same):
    raw=[check()];before=copy.deepcopy(raw)
    bound=vt.bind_audit_checks(raw,tracks(same),frames())
    assert bound==[{**raw[0],'status':'confirmed','frame_refs':['f1'] if same else ['f1','f3'],
                    'reference_binding':'platform_immutable_fragment'}]
    assert raw==before


@pytest.mark.parametrize('field,value',[('trigger_verdict',None),('outcome_verdict',True),
    ('trigger_verdict','true'),('outcome_verdict','confirmed'),('trigger_verdict','unsupported')])
def test_verdict_contract_rejects_missing_types_and_enums(field,value):
    c=check();c[field]=value
    if value is None:c.pop(field)
    with pytest.raises(ValueError):vt.bind_audit_checks([c],tracks(),frames())


@pytest.mark.parametrize('changes',[{'question':'仍不确定'}, {'trigger_verdict':'unclear'},
                                   {'outcome_verdict':'contradicted'}, {'status':'confirmed'},
                                   {'frame_refs':['f1','f3']}])
def test_inconsistent_question_or_old_fields_rejected(changes):
    with pytest.raises(ValueError):vt.bind_audit_checks([{**check(),**changes}],tracks(),frames())


@pytest.mark.parametrize('verdict',['unclear','contradicted'])
@pytest.mark.parametrize('field',['trigger_verdict','outcome_verdict'])
def test_negative_verdict_is_visible_needs_review(field,verdict):
    raw={**check(),field:verdict,'question':'触发或结果证据不支持当前主张'}
    bound=vt.bind_audit_checks([raw],tracks(),frames())[0]
    assert bound['status']=='needs_review' and bound[field]==verdict
    assert bound['question']==raw['question']


@pytest.mark.parametrize('checks',[[],[check(),check()],[{**check(),'event_id':'unknown'}],
    [dict(event_id='e1',status='confirmed',frame_refs=['f1','f3'],question='')]])
def test_missing_duplicate_foreign_and_old_checks_rejected(checks):
    with pytest.raises(ValueError):vt.bind_audit_checks(checks,tracks(),frames())


def test_audit_schema_is_exclusive_and_keeps_other_stage_contract():
    import jsonschema
    schema=json.loads((Path(__file__).resolve().parents[1]/'ai_skills/schemas/ui_video_trajectory.schema.json').read_text())
    audit=dict(checks=[check()],issues=[],multiple_scenarios=False,revisit=[])
    jsonschema.validate(audit,schema)
    for patch in ({'states':[]},{'events':[]},{'multiple_scenarios':'true'},
                  {'checks':[dict(event_id='e1',status='confirmed',frame_refs=['f1'],question='')]}):
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate({**audit,**patch},schema)
    other=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
    jsonschema.validate(other,schema)
    for key in ('states','events','checks'):
        missing=copy.deepcopy(other);missing.pop(key)
        if key=='checks':
            with pytest.raises(jsonschema.ValidationError):jsonschema.validate(missing,schema)


@pytest.mark.parametrize('bad',[{'kind':'missing_action','owner_frame':'f0','frame_refs':['f0'],'question':'遗漏'},
    {'kind':'missing_action','owner_frame':'f1','frame_refs':['f0'],'question':'遗漏'},
    {'kind':'invented','owner_frame':'f1','frame_refs':['f1'],'question':'遗漏'},
    {'kind':'unresolved_evidence','owner_frame':'f1','frame_refs':['f1'],'question':''}])
def test_issue_owner_kind_and_question_are_strict(bad):
    with pytest.raises(ValueError):vt.validate_audit_issues([bad],{'frames':frames(),'owned_frame_ids':['f1','f2']})


def run_native(monkeypatch,mode):
    from task_server.services import ai_skill_service as ai,ui_video_windows
    from test_ui_video_trajectory import frames as timeline,event
    calls=[];audit_calls={};hypothesis='总览无法确认frame-050的触发'
    def reply(name,payload,**kw):
        stage=kw['version'];calls.append((stage,copy.deepcopy(payload)))
        if stage=='audit':
            key=payload['packet_id'];audit_calls[key]=audit_calls.get(key,0)+1
            r={'checks':[dict(event_id=p['track']['event_id'],trigger_verdict='supported',outcome_verdict='supported',question='')
                         for p in payload['trajectory']], 'issues':[],'multiple_scenarios':False,'revisit':[]}
            if mode=='runtime':raise RuntimeError('private server detail')
            if mode=='shared' and key=='packet-001':r['checks']=[]
            if mode=='old' and key=='packet-001' and audit_calls[key]==1:
                return dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
            if mode=='context' and key=='packet-001' and audit_calls[key]==1:
                r['issues']=[dict(kind='missing_action',owner_frame='frame-050',frame_refs=['frame-050'],question='上下文不能充当本包遗漏')]
            if mode in ('context','unresolved') and key=='packet-002':
                r['issues']=[dict(kind='unresolved_evidence',owner_frame='frame-050',frame_refs=['frame-050'],question='完整原图仍无法确认frame-050触发')]
            return r
        r=dict(states=[],events=[],checks=[],uncertainties=[],multiple_scenarios=False)
        if stage=='overview':
            r['states']=[dict(state_id='edit',first_frame='frame-001',last_frame='frame-047',kind='input',description='连续编辑状态')]
            r['uncertainties']=[hypothesis]
        if stage=='observe' and payload['packet_id']=='packet-001':
            if mode=='shared' and len([1 for s,p in calls if s=='observe'])==1:r['checks']=[{}]
            else:
                r['events']=[event(['frame-001','frame-002'])]
                r['events'][0]['uncertainties']=['观察者真实事件疑问']
                r['uncertainties']=['观察者真实顶层疑问']
        return r
    monkeypatch.setattr(ai,'run_ai_skill',reply);monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    doc={'media':dict(frames=timeline(60),duration_ms=6001)}
    return lambda:vt.observe_trajectory(doc,None,lambda:False),calls,hypothesis


@pytest.mark.parametrize('mode',['clear','unresolved','context'])
def test_owned_issue_and_overview_hypothesis_lifecycle(monkeypatch,mode):
    run,calls,hypothesis=run_native(monkeypatch,mode);result=run()
    assert hypothesis not in result['uncertainties']
    assert result['trajectory_audit']['overview_hypotheses']==[hypothesis]
    assert '观察者真实顶层疑问' in result['uncertainties']
    assert '观察者真实事件疑问' in result['events'][0]['uncertainties']
    for stage,payload in calls:
        if stage!='overview':assert payload['overview_hypotheses']==[hypothesis]
    assert ('完整原图仍无法确认frame-050触发' in result['uncertainties'])==(mode!='clear')
    if mode=='context':
        assert len([1 for s,p in calls if s=='audit' and p['packet_id']=='packet-001'])==2
        assert '上下文不能充当本包遗漏' not in result['uncertainties']


def test_old_audit_root_is_rejected_then_corrected(monkeypatch):
    run,calls,_=run_native(monkeypatch,'old');result=run()
    assert result['events']
    assert len([1 for s,p in calls if s=='audit' and p['packet_id']=='packet-001'])==2


def test_shared_observation_and_audit_correction_budget_unchanged(monkeypatch):
    run,calls,_=run_native(monkeypatch,'shared')
    with pytest.raises(ValueError) as exc:run()
    assert exc.value.trajectory_reason=='audit_missing_event'
    assert [s for s,p in calls]==['overview','observe','observe','audit','audit']


def test_runtime_failure_never_consumes_audit_correction(monkeypatch):
    run,calls,_=run_native(monkeypatch,'runtime')
    with pytest.raises(RuntimeError):run()
    assert [s for s,p in calls]==['overview','observe','audit']
