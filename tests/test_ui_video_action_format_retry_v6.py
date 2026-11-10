import copy
import json

import pytest

from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_reconcile as reconcile
from task_server.services import ui_video_touch_detail as detail
from task_server.services import ui_video_windows as windows
from task_server.services.ui_video_analysis import validate_analysis
from task_server.workers.ui_video_worker import _log_failure, _private_target_failure


def fixture(count=7):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,90)]
    events=[{'action':'aiTap','target':f'控件{i}','value':'','frame_refs':[f'frame-{i:03d}'],
             'start_ms':i*100,'end_ms':i*100,'observation':'点击后页面变化',
             'uncertainties':[],'evidence_kind':'inferred'} for i in range(5,5+count*10,10)]
    return frames,events


def answer(event,value=''):
    return {'events':[{'action':event['action'],'target':event['target'],'value':value,
                       'frame_refs':event['frame_refs'],'observation':event['observation'],
                       'uncertainties':[]}], 'coverage_resolutions':[], 'uncertainties':[],
            'multiple_scenarios':False}


def test_seven_groups_can_use_only_shared_spare_for_format_retry(monkeypatch,tmp_path):
    frames,events=fixture(); checked={'events':copy.deepcopy(events),'uncertainties':[], 'multiple_scenarios':False}
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload));refs={f['frame_id'] for f in payload['frames']}
        event=next(e for e in events if set(e['frame_refs'])<=refs)
        return answer(event,'private-value-NEVER-ECHO' if len(calls)==1 else '')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_coverage(events,checked,frames,9000,tmp_path,lambda:False,[],
                                      {e['frame_refs'][0] for e in events})
    assert len(calls)==8
    assert [c['frames'][0]['frame_id'] for c in calls[:2]]==['frame-005','frame-005']
    assert 'aiTap' in calls[1]['validation_feedback'] and 'aiAssert' in calls[1]['validation_feedback']
    assert 'private-value-NEVER-ECHO' not in json.dumps(calls[1],ensure_ascii=False)
    assert [(e['action'],e['frame_refs']) for e in result['events']]==[(e['action'],e['frame_refs']) for e in events]
    assert checked['events']==events


def test_seven_groups_spare_used_once_then_fails_closed(monkeypatch,tmp_path):
    frames,events=fixture();checked={'events':copy.deepcopy(events),'uncertainties':[], 'multiple_scenarios':False}
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload));refs={f['frame_id'] for f in payload['frames']}
        event=next(e for e in events if set(e['frame_refs'])<=refs)
        value='secret' if len(calls) in {1,3} else ''
        return answer(event,value)
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_action_invalid'):
        reconcile._repair_coverage(events,checked,frames,9000,tmp_path,lambda:False,[],
                                   {e['frame_refs'][0] for e in events})
    assert len(calls)==3
    assert checked['events']==events


def test_incorrect_value_never_appears_in_diagnostic_or_log(caplog):
    frames,events=fixture(1);events[0]['value']='private-value-NEVER-ECHO'
    with pytest.raises(ValueError,match='video_action_invalid') as caught:
        validate_analysis({'events':events,'uncertainties':[],'multiple_scenarios':False},frames,9000)
    failure=caught.value
    evidence=_private_target_failure(failure,'video_action_invalid',{'media':{'frames':frames}})
    assert evidence=={'action_failure_evidence':{'reason':'unexpected_value','action':'aiTap',
                                                  'frame_refs':['frame-005']}}
    _log_failure('uvs_'+'a'*32,'analysis',failure,'video_action_invalid')
    assert 'private-value-NEVER-ECHO' not in caplog.text
    assert 'private-value-NEVER-ECHO' not in json.dumps(evidence)


def test_untrusted_action_diagnostic_attributes_are_not_published_or_logged(caplog):
    frames,_=fixture(1);error=ValueError('video_action_invalid')
    error.action_reason='unexpected_value'
    error.action_failure_evidence={'reason':'unexpected_value','action':'aiTap',
                                   'frame_refs':['private-value-NEVER-ECHO']}
    assert _private_target_failure(error,'video_action_invalid',{'media':{'frames':frames}})=={}
    _log_failure('uvs_'+'a'*32,'analysis',error,'video_action_invalid')
    assert 'private-value-NEVER-ECHO' not in caplog.text


def test_valid_new_action_from_rejected_response_cannot_silently_disappear(monkeypatch,tmp_path):
    frames,events=fixture(1); group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload))
        candidate=answer(events[0],'private' if len(calls)==1 else '')
        if len(calls)==1:
            candidate['events'].append({'action':'aiTap','target':'另一个控件','value':'',
                'frame_refs':['frame-006'],'observation':'另一次点击','uncertainties':[]})
        return candidate
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                 group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                 attempt_limit=2,retry_return_gesture_only=True)
    assert len(calls)==2


def test_malformed_attempt_cannot_erase_valid_uncertainty_or_multiple_scenarios(monkeypatch,tmp_path):
    frames,events=fixture(1);group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload))
        candidate=answer(events[0],'private' if len(calls)==1 else '')
        if len(calls)==1:
            candidate['uncertainties']=['画面是否有遮挡'];candidate['multiple_scenarios']=True
        return candidate
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                    group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                    attempt_limit=2,retry_return_gesture_only=True)
    assert '画面是否有遮挡' in result['uncertainties']
    assert result['multiple_scenarios'] is True


def test_malformed_untrusted_diagnostic_cannot_crash_worker():
    frames,_=fixture(1);error=ValueError('video_action_invalid')
    error.action_failure_evidence={'reason':'unexpected_value','action':'aiTap','frame_refs':[{}]}
    assert _private_target_failure(error,'video_action_invalid',{'media':{'frames':frames}})=={}


def test_rejected_value_repeated_in_uncertainty_is_redacted(monkeypatch,tmp_path):
    frames,events=fixture(1);group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload)); candidate=answer(events[0],'secret-INPUT' if len(calls)==1 else '')
        if len(calls)==1:candidate['uncertainties']=['是否输入 secret-INPUT']
        return candidate
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                    group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                    attempt_limit=2,retry_return_gesture_only=True)
    assert 'secret-INPUT' not in json.dumps(result,ensure_ascii=False)
    assert result['uncertainties']


def test_conflicting_value_is_flagged_for_review_even_after_format_retry(monkeypatch,tmp_path):
    frames,events=fixture(1);group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload));return answer(events[0],'private' if len(calls)==1 else '')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                    group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                    attempt_limit=2,retry_return_gesture_only=True)
    assert any('动作' in question and '输入' in question for question in result['uncertainties'])
    assert 'private' not in json.dumps(result,ensure_ascii=False)


@pytest.mark.parametrize('return_only',[False,True])
@pytest.mark.parametrize('bad_value',['secret-X',{'text':'secret-X'},['secret-X']])
def test_unexpected_value_redacts_all_nested_value_strings(monkeypatch,tmp_path,return_only,bad_value):
    frames,events=fixture(1);group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload));candidate=answer(events[0])
        if len(calls)==1:
            candidate['events'].append({'action':'aiTap','target':'另一个控件','value':bad_value,
                'frame_refs':['frame-006'],'observation':'点击','uncertainties':[]})
        else:candidate['uncertainties']=['可能存在 secret-X']
        return candidate
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                    group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                    attempt_limit=2,retry_return_gesture_only=return_only)
    assert 'secret-X' not in json.dumps(result,ensure_ascii=False)
    assert any('动作' in q and '输入' in q for q in result['uncertainties'])


def test_schema_exception_without_redaction_values_fails_closed(monkeypatch,tmp_path):
    frames,events=fixture(1);group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(*args,**kwargs):
        calls.append(1);raise ai.SkillSchemaError('$.events','必须是 array')
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ai.SkillSchemaError):
        reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                 group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                 attempt_limit=2,retry_return_gesture_only=True)
    assert len(calls)==1



def test_redaction_values_are_bounded_and_include_nested_keys():
    assert set(reconcile._rejected_action_values({'events':[{'value':{'secret-key':['secret-X']}}]}))=={'secret-key','secret-X'}
    with pytest.raises(ValueError,match='video_action_invalid'):
        reconcile._rejected_action_values({'events':[{'value':str(i)} for i in range(129)]})


@pytest.mark.parametrize('bad_value',[123456789,{'code':123456789},[True]])
def test_numeric_unexpected_value_fails_closed_before_retry(monkeypatch,tmp_path,bad_value):
    frames,events=fixture(1);group=frames[4:6];calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *args,**kwargs:[])
    monkeypatch.setattr(detail,'append_details',lambda *args,**kwargs:[])
    def model(skill,payload,**kwargs):
        calls.append(1);return answer(events[0],bad_value)
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_action_invalid'):
        reconcile._repair_window(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},
                                 group,9000,tmp_path,lambda:False,[],{'frame-005'},
                                 attempt_limit=2,retry_return_gesture_only=True)
    assert len(calls)==1
