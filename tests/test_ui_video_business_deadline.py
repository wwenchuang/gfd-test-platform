"""Fake-clock controls for bounded business source/engine/tail time."""
import pytest
from task_server.services import ui_video_runtime as runtime, ui_video_trajectory as vt
from test_ui_video_runtime_evidence_reuse import BusinessEngine
from test_ui_video_audit_batches import Engine
from test_ui_video_business_runtime import harness
from test_ui_video_runtime import worker_env
from test_ui_video_trajectory import frames


def test_business_late_calls_share_original_source_clock_and_keep_tail():
    now=[100.];budget=runtime.RuntimeBudget(0,business=True,clock=lambda:now[0])
    assert budget.deadline==4600.
    now[0]=220.;budget.reserve()  # native work consumes original source time
    budget.start_engine(business=True)
    assert budget.engine_deadline()==budget.deadline-480.5==4119.5
    now[0]=1659.5  # previous source1800 -240.5 cutoff, from source start100
    budget.reserve()
    now[0]=4119.;budget.reserve()
    assert budget.reserved==3 and budget.deadline==4600.
    with pytest.raises(ValueError,match='engine_started'):budget.start_engine(business=True)
    now[0]=4119.5
    with pytest.raises(TimeoutError,match='video_runtime_deadline'):budget.reserve()
    budget.end_engine();budget.reserve()
    now[0]=4599.49;budget.reserve()
    now[0]=4599.5
    with pytest.raises(TimeoutError,match='video_runtime_deadline'):budget.reserve()
    now[0]=4600.
    with pytest.raises(TimeoutError):budget.check()


def test_custom_legacy_budget_injected_into_business_is_never_extended():
    now=[100.];budget=runtime.RuntimeBudget(0,clock=lambda:now[0]);original=budget.deadline
    now[0]=200.;budget.start_engine(business=True)
    assert budget.deadline==original==1900.
    assert budget.engine_deadline()==1419.5
    now[0]=1419.5
    with pytest.raises(TimeoutError):budget.reserve()
    budget.end_engine();budget.reserve();assert budget.deadline==original


def test_normal_business_entrypoint_creates_long_budget_once(harness,monkeypatch):
    made=[];real=runtime.RuntimeBudget
    def create(*a,**kw):
        budget=real(*a,**kw,clock=lambda:100.);made.append(budget);return budget
    monkeypatch.setattr(runtime,'RuntimeBudget',create)
    harness['run']()
    assert len(made)==1 and made[0].deadline==4600.


@pytest.mark.parametrize('business',[False,True])
def test_normal_worker_creates_mode_clock_before_runtime(worker_env,monkeypatch,business):
    worker,store,doc,profile=worker_env
    monkeypatch.setenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST','1' if business else '0')
    made=[];real=runtime.RuntimeBudget
    def create(*a,**kw):
        budget=real(*a,**kw,clock=lambda:100.);made.append(budget);return budget
    monkeypatch.setattr(runtime,'RuntimeBudget',create)
    def run(document,workspace,path,check,publish,**kw):
        assert kw['mode']==(runtime.BUSINESS_MODE if business else runtime.MODE)
        assert len(made)==1 and kw['budget'] is made[0] and kw['budget'].deadline==(4600. if business else 1900.)
        check()
        return {'media':document['media'],'events':[],'multiple_scenarios':False,'uncertainties':[]}
    monkeypatch.setattr(runtime,'run_runtime',run)
    worker.analyze_video_source(store,doc['source_id'])
    assert len(made)==1 and store.get(doc['source_id'],'u',doc['app_id'])['status']=='review_needed'


@pytest.mark.parametrize('business,deadline',[(False,1560.),(True,4020.)])
def test_explicit_mode_maximum_allows_late_valid_engine_calls(monkeypatch,business,deadline):
    now=[0.];monkeypatch.setattr(vt.time,'monotonic',lambda:now[0])
    engine=(BusinessEngine if business else Engine)(monkeypatch,count=2)
    old=engine.model
    def model(*a,**kw):
        raw=old(*a,**kw);now[0]=deadline-1;return raw
    engine.model=model
    if business:result=engine.run(engine_deadline=deadline)
    else:
        result=vt.observe_trajectory({'media':{'frames':frames(2),'duration_ms':201}},None,lambda:False,skill_runner=model,engine_deadline=deadline)
    assert len(result['trajectory_audit']['calls'])==3


@pytest.mark.parametrize('business',[False,True])
@pytest.mark.parametrize('value',[float('inf'),float('nan'),True,'4020',None])
def test_mode_specific_explicit_deadline_validation(monkeypatch,business,value):
    monkeypatch.setattr(vt.time,'monotonic',lambda:0.)
    options={'business_timeline':{}} if business else {}
    value=(4020.001 if business else 1560.001) if value is None else value
    with pytest.raises(ValueError,match='video_runtime_engine_deadline_invalid'):
        vt.observe_trajectory({'media':{'frames':[],'duration_ms':1000}},None,lambda:False,engine_deadline=value,**options)


def test_business_explicit_engine_rejects_at_new_cutoff(monkeypatch):
    now=[0.];monkeypatch.setattr(vt.time,'monotonic',lambda:now[0])
    engine=BusinessEngine(monkeypatch,count=2);old=engine.model
    def model(*a,**kw):
        raw=old(*a,**kw);now[0]=4020.;return raw
    engine.model=model
    with pytest.raises(ValueError,match='video_vision_limit'):engine.run(engine_deadline=4020.)
    assert len(engine.calls)==1
