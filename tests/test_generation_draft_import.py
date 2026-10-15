import copy
import pytest
from test_ui_case_draft_service import env, A, B, APP
from task_server.services.operation_attribution import actor_context
from task_server.services import ui_case_draft_service as service, yaml_service

@pytest.fixture
def source(env, monkeypatch):
    summary = {'case_set_id':'case-123', 'generation_job_id':'gen_123_00001','app_package':APP,'cases':[{'title':'我的入口','steps':['点击我的'],'expected':['个人中心显示']}]}
    job = {'job_id':summary['generation_job_id'],'case_set_id':summary['case_set_id'],'ownerUserId':A['user_id'],'request_data':{'app_package':APP}}
    monkeypatch.setattr(yaml_service, 'read_json_file', lambda *a, **kw: copy.deepcopy(summary))
    monkeypatch.setattr(yaml_service, 'load_generate_job', lambda _:copy.deepcopy(job))
    return summary,job

def body(source):
    from task_server.services.generation_draft_import import source_refs
    with actor_context(A): refs=source_refs(source[0])
    return {'app_id':APP,'case_set_id':'case-123',**refs[0]}

def test_import_retry_and_stale(source,env):
    request=body(source)
    with actor_context(A):
        draft=service.import_generation_draft(A,request)
        assert service.import_generation_draft(A,request)['draft_id']==draft['draft_id']
        assert len(env[1])==1
        env[1].pop()()
        loaded=service.get_draft(A,draft['draft_id'])
        edit={k:loaded[k] for k in ('preconditions','steps','questions')}
        service.update_draft(A,draft['draft_id'],dict(edit,title='保留修改',revision=loaded['revision']))
        assert service.import_generation_draft(A,request)['title']=='保留修改'
        source[0]['cases'][0]['steps']=['新的操作']
        with pytest.raises(service.DraftConflict): service.import_generation_draft(A,request)

@pytest.mark.parametrize('change',[{'ownerUserId':''},{'ownerUserId':B['user_id']},{'case_set_id':'other'},{'job_id':'other'},{'request_data':{'app_package':'other'}}])
def test_untrusted_job(source,change):
    request=body(source);source[1].update(change)
    with actor_context(A),pytest.raises((ValueError,PermissionError)): service.import_generation_draft(A,request)

@pytest.mark.parametrize('field',['ownerUserId','steps','path','generation_job_id'])
def test_forged_payload(source,field):
    request=body(source);request[field]='bad'
    with actor_context(A),pytest.raises(ValueError):service.import_generation_draft(A,request)

def test_manual_and_missing_link(source):
    request=body(source)
    source[0]['manual_cases']=copy.deepcopy(source[0]['cases'])
    with actor_context(A),pytest.raises(ValueError):service.import_generation_draft(A,request)
    source[0].pop('generation_job_id')
    with actor_context(A),pytest.raises(ValueError,match='owner_missing'):service.import_generation_draft(A,request)

def test_enqueue_owner_from_authenticated_actor(monkeypatch):
    from task_server import router
    monkeypatch.setattr(router,'save_generate_job',lambda job:None)
    monkeypatch.setattr(router,'enqueue_persisted_background_job',lambda _:True)
    job={'job_id':'gen_1','ownerUserId':B['user_id'],'request_data':{'ownerUserId':B['user_id']}}
    with actor_context(A): assert router._persist_and_enqueue_background_job(None,job)
    assert job['ownerUserId']==A['user_id']

def test_summary_link_uses_internal_job_and_persists_reverse_link():
    import ast,inspect
    for function in (yaml_service.generate_ui_yaml_from_request,yaml_service.generate_mindmap_from_request):
        text=inspect.getsource(function)
        assert 'summary["generation_job_id"] = job_id or ""' in text
        assert 'update_generate_job(job_id, case_set_id=case_set_id)' in text
