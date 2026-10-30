"""Business intermediate root questions are bounded without truncating review blockers."""
import copy
import math
import pytest
from task_server.services.ui_video_analysis import validate_analysis
from task_server.services import ui_video_trajectory as vt
from test_ui_video_audit_batches import Engine
from test_ui_video_trajectory import frames


def aggregate(count=39,event_questions=4):
    fs=frames(39)
    events=[{'action':'aiTap','target':'可见控件','value':'','start_ms':f['timestamp_ms'],
             'end_ms':f['timestamp_ms'],'frame_refs':[f['frame_id']],'observation':'可见控件响应',
             'evidence_kind':'observed','uncertainties':[f'事件疑问 {i}-{j}' for j in range(event_questions)]}
            for i,f in enumerate(fs)]
    return {'events':events,'uncertainties':[f'保留疑问 {i}' for i in range(count)],'multiple_scenarios':False},fs


def test_original26_aggregation_shape_39_events_39_questions_is_preserved():
    raw,fs=aggregate();before=copy.deepcopy(raw)
    result=validate_analysis(raw,fs,4000,max_events=120,max_questions=128)
    assert result==before and raw==before
    assert len(result['events'])==39 and len(result['uncertainties'])==39
    assert max(map(lambda e:len(e['uncertainties']),result['events']))==4


def test_default_final_policy_remains_30():
    raw,fs=aggregate(30);assert len(validate_analysis(raw,fs,4000)['uncertainties'])==30
    raw,fs=aggregate(31)
    with pytest.raises(ValueError,match='video_model_invalid') as error:validate_analysis(raw,fs,4000)
    assert error.value.analysis_reason=='root_question_limit'
    assert error.value.question_count==31 and error.value.question_limit==30


@pytest.mark.parametrize('count',[128,129])
def test_explicit_business_bound_is_exact(count):
    raw,fs=aggregate(count);before=copy.deepcopy(raw)
    if count==128:
        assert validate_analysis(raw,fs,4000,max_questions=128)['uncertainties']==raw['uncertainties']
    else:
        with pytest.raises(ValueError,match='video_model_invalid') as error:
            validate_analysis(raw,fs,4000,max_questions=128)
        assert error.value.analysis_reason=='root_question_limit'
        assert (error.value.question_count,error.value.question_limit)==(129,128)
        assert '保留疑问' not in str(error.value)
    assert raw==before


@pytest.mark.parametrize('policy',[True,False,None,'128',128.,31,0,-1,129,float('nan'),float('inf')])
def test_only_closed_integer_question_policies_are_accepted(policy):
    raw,fs=aggregate(0)
    with pytest.raises(ValueError,match='video_analysis_options_invalid'):
        validate_analysis(raw,fs,4000,max_questions=policy)


@pytest.mark.parametrize('mutation',['event11','unknown_frame','action','late_event','event51','badquestion','longquestion','nonarray'])
def test_explicit_question_policy_does_not_relax_other_contracts(mutation):
    raw,fs=aggregate(39)
    if mutation=='event11':raw['events'][0]['uncertainties']=['q']*11
    if mutation=='unknown_frame':raw['events'][0]['frame_refs']=['foreign']
    if mutation=='action':raw['events'][0]['action']='runAdbShell'
    if mutation=='late_event':raw['events'][0]['start_ms']=10000
    if mutation=='event51':raw['events']=[raw['events'][0]]*51
    if mutation=='badquestion':raw['uncertainties'][0]={}
    if mutation=='longquestion':raw['uncertainties'][0]='q'*2001
    if mutation=='nonarray':raw['uncertainties']={}
    with pytest.raises(ValueError):validate_analysis(raw,fs,4000,max_questions=128)


def run_engine(monkeypatch,count,business,diagnostics=None):
    questions=[f'待核实问题 {i}' for i in range(count)]
    def mutate(stage,payload,raw,calls):
        if stage=='observe':
            packet=int(payload['packet_id'].split('-')[1])-1
            raw['uncertainties']=questions[packet*26:(packet+1)*26]
    engine=Engine(monkeypatch,count=max(2,math.ceil(count/26)*32),mutate=mutate)
    result=vt.observe_trajectory({'media':{'frames':frames(engine.count),'duration_ms':engine.count*100+1}},
        None,lambda:False,skill_runner=engine.model,audit_batcher=engine.batches,
        diagnostics=diagnostics,**({'business_timeline':{'classification':'unverified_business_hypotheses'}} if business else {}))
    return result,engine,questions


@pytest.mark.parametrize('count',[39,128])
def test_business_full_trajectory_collects_every_question_after_independent_audit(monkeypatch,count):
    result,engine,questions=run_engine(monkeypatch,count,True)
    assert result['uncertainties']==questions
    assert any(stage=='audit' for stage,*_ in engine.calls)
    assert len(result['trajectory_audit']['checks'])==len(result['events'])
    # Intermediate allowance must never be implicitly inherited by a final caller.
    with pytest.raises(ValueError,match='video_model_invalid'):
        validate_analysis({k:result[k] for k in ('events','uncertainties','multiple_scenarios')},result['media']['frames'],result['media']['duration_ms'],max_events=120)


@pytest.mark.parametrize('business,count,limit',[(False,39,30),(True,129,128)])
def test_trajectory_overflow_retains_safe_precise_diagnostic(monkeypatch,business,count,limit):
    diagnostics=[]
    with pytest.raises(ValueError,match='video_model_invalid') as error:run_engine(monkeypatch,count,business,diagnostics)
    assert error.value.analysis_reason=='root_question_limit'
    assert diagnostics[-1]['kind']=='capacity_refusal' and diagnostics[-1]['reason']=='trajectory_questions'
    assert diagnostics[-1]['question_count']==count and diagnostics[-1]['question_limit']==limit
    assert '待核实问题' not in str(diagnostics[-1])


def test_event10_and_explicit120_bound_remain_exact():
    raw,fs=aggregate(39,10)
    assert len(validate_analysis(raw,fs,4000,max_questions=128)['events'][0]['uncertainties'])==10
    raw['events']=[raw['events'][0]]*120
    assert len(validate_analysis(raw,fs,4000,max_events=120,max_questions=128)['events'])==120
    raw['events'].append(raw['events'][0])
    with pytest.raises(ValueError,match='video_model_invalid'):
        validate_analysis(raw,fs,4000,max_events=120,max_questions=128)


def test_model_payload_cannot_select_internal_question_policy():
    raw,fs=aggregate(39);raw['max_questions']=128
    with pytest.raises(ValueError,match='video_model_invalid'):validate_analysis(raw,fs,4000)
