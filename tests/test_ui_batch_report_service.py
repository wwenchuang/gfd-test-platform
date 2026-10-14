"""Batch reporting never looks up mutable filenames or another executor's latest run."""
import io
import zipfile
import pytest
from test_ui_batch_dispatch import dispatch_env, create
from test_ui_execution_service import environment, ACTORS
from task_server.services.operation_attribution import actor_context


def completed(name='a', key='one', status='passed'):
    from task_server.services import ui_batch_dispatch as dispatch
    from task_server.services import job_service
    batch = create(name, key)
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    job.update(dispatch.claim_ui_job(job))
    event = {k:job[k] for k in ('ui_claim_token','device_id')}
    event.update(runner_id='mac', status=status, execution_stopped=True, type='result')
    dispatch.record_ui_attempt_event(job['job_id'],1,event)
    return batch, job, event


def test_frozen_identity_and_result_are_not_latest_filename(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_report_service as reports, case_service
    a, _, _ = completed(status='failed')
    completed('b')
    monkeypatch.setattr(case_service,'task_case_yaml',lambda _: pytest.fail('mutable source read'))
    with actor_context(ACTORS['a']):
        result = reports.build_ui_batch_report(None,a['ui_batch_id'])
        assert result['status'] == 'ready'
        snapshot = result['snapshot']
        assert snapshot['owner_user_id'] == 'u-a'
        assert snapshot['counts'] == {'failed':1}
        assert snapshot['attempts'][0]['case_revision'] == a['case_attempts'][0]['case_revision']
        assert reports.build_ui_batch_report(None,a['ui_batch_id'])['report_revision'] == result['report_revision']
        assert reports.get_ui_batch_summary(None,a['ui_batch_id'])['summary'] == snapshot['summary']
        body, mime, filename = reports.download_ui_batch_report(None,a['ui_batch_id'],'docx')
        assert mime.endswith('wordprocessingml.document') and filename.endswith('.docx')
        assert filename.count('测试报告') == 1
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            assert '[Content_Types].xml' in archive.namelist()
            text = archive.read('word/document.xml').decode()
            assert '作者A' in text and '未通过' in text


def test_unsettled_and_stop_pending_cannot_build(dispatch_env):
    from task_server.services import ui_batch_report_service as reports
    batch = create()
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError,match='execution_not_settled'):
            reports.build_ui_batch_report(None,batch['ui_batch_id'])


def test_late_evidence_new_revision_preserves_old_snapshot(dispatch_env):
    from task_server.services import ui_batch_report_service as reports
    batch, job, event = completed()
    with actor_context(ACTORS['a']):
        first = reports.build_ui_batch_report(None,batch['ui_batch_id'])
        artifact = reports.save_ui_artifact(job['job_id'],event,b'<html>private</html>','html')
        second = reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert first['report_revision'] != second['report_revision']
        assert first['snapshot']['artifacts'] == []
        assert second['snapshot']['artifacts'][0]['url'] == artifact['url']
        old = reports.get_ui_batch_report(None,batch['ui_batch_id'],first['report_revision'])
        assert old['snapshot'] == first['snapshot']
        assert reports.save_ui_artifact(job['job_id'],event,b'<html>private</html>','html') == artifact
        assert reports.build_ui_batch_report(None,batch['ui_batch_id'])['report_revision'] == second['report_revision']


def test_render_failure_retry_does_not_execute(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_report_service as reports, job_service
    batch,_,_ = completed()
    original = reports._render_docx
    monkeypatch.setattr(reports,'_render_docx',lambda _: (_ for _ in ()).throw(TimeoutError('timeout')))
    with actor_context(ACTORS['a']):
        assert reports.build_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'failed'
        jobs = job_service.load_jobs(limit=None)
        assert reports.request_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'pending'
        monkeypatch.setattr(reports,'_render_docx',original)
        assert reports.build_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'ready'
        assert jobs == job_service.load_jobs(limit=None)


def test_late_callback_marks_pending_only_when_frozen_result_changes(dispatch_env):
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch
    batch,job,event=completed()
    with actor_context(ACTORS['a']):
        first=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        dispatch.record_ui_attempt_event(job['job_id'],2,{**event,'type':'report_ready','report_upload_error':'upload failed'})
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'pending'
        second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert second['report_revision'] != first['report_revision']
        dispatch.record_ui_attempt_event(job['job_id'],3,{**event,'type':'report_ready','report_upload_error':'upload failed'})
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'ready'
        assert reports.build_ui_batch_report(None,batch['ui_batch_id'])['report_revision'] == second['report_revision']


def test_evidence_arriving_during_render_keeps_next_revision_pending(dispatch_env,monkeypatch):
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    original=reports._render_docx
    def render(markdown):
        reports.save_ui_artifact(job['job_id'],event,b'late','html')
        return original(markdown)
    monkeypatch.setattr(reports,'_render_docx',render)
    with actor_context(ACTORS['a']):
        first=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert first['status'] == 'ready'
        assert first['snapshot']['artifacts'] == []
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'pending'
        second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert second['snapshot']['artifacts'] and second['report_revision'] != first['report_revision']


def test_cancelled_unexecuted_and_rerun_remain_separate(dispatch_env):
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch
    from task_server.services.ui_execution_store import UiExecutionStore
    batch,job,event=completed(status='failed')
    with actor_context(ACTORS['a']):
        first=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        rerun=dispatch.rerun_ui_batch(None,batch['ui_batch_id'],'failed','retry')
        dispatch.cancel_ui_batch(None,rerun['ui_batch_id'])
        retry_report=reports.build_ui_batch_report(None,rerun['ui_batch_id'])
        assert retry_report['snapshot']['counts'] == {'cancelled':1}
        assert retry_report['snapshot']['attempts'][0]['started_ns'] is None
        assert retry_report['snapshot']['selection']['rerun_of'] == batch['ui_batch_id']
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['snapshot'] == first['snapshot']


def test_same_filename_different_source_revision_does_not_replace_snapshot(dispatch_env,monkeypatch):
    import hashlib
    from task_server.services import ui_batch_report_service as reports, case_service, ui_execution_service as service
    from test_ui_execution_service import SOURCE, request
    batch,job,event=completed()
    source=SOURCE.replace('我的页面','新的页面')
    monkeypatch.setattr(case_service,'task_case_yaml',lambda _:source)
    selected=request(); selected['cases'][0]['revision']=hashlib.sha256(source.encode()).hexdigest()
    with actor_context(ACTORS['a']):
        other=service.create_ui_batch(None,selected,'v2')
        report=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert report['snapshot']['attempts'][0]['case_revision'] != other['case_attempts'][0]['case_revision']


def test_worker_is_async_single_build_and_does_not_poll_device(dispatch_env,monkeypatch):
    import threading
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    with actor_context(ACTORS['a']):
        reports.request_ui_batch_report(None,batch['ui_batch_id'])
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def render(markdown):
        calls.append(markdown); entered.set()
        assert release.wait(3)
        return b'fake docx only for bounded worker scheduling test'
    monkeypatch.setattr(reports,'_render_docx',render)
    monkeypatch.setattr(reports,'_WORKER',None)
    try:
        reports.tick_ui_report_worker()
        assert entered.wait(2)
        first=reports._WORKER
        reports.tick_ui_report_worker()
        assert reports._WORKER is first and len(calls) == 1
    finally:
        release.set()
        reports._WORKER.join(3)
    with actor_context(ACTORS['a']):
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'ready'


def test_renderer_timeout_terminates_child_contract(monkeypatch):
    import subprocess
    from task_server.services import ui_batch_report_service as reports
    def run(command,**kwargs):
        assert kwargs['timeout'] == 30 and kwargs['check'] is True
        raise subprocess.TimeoutExpired(command,30)
    monkeypatch.setattr(reports.subprocess,'run',run)
    with pytest.raises(subprocess.TimeoutExpired): reports._render_docx('# test')


def test_appended_attempt_invalidates_current_report_and_preserves_failure(dispatch_env):
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    from test_ui_execution_service import TARGET
    batch,job,event=completed(status='failed')
    with actor_context(ACTORS['a']):
        first=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        original=batch['case_attempts'][0]
        next_attempt=UiExecutionStore().append_case_attempt(batch['ui_batch_id'],original['case_id'],original['case_revision'],TARGET,original['compiled_case'])
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['status'] == 'pending'
        with pytest.raises(ValueError,match='execution_not_settled'):
            reports.build_ui_batch_report(None,batch['ui_batch_id'])
        dispatch.dispatch_ui_batch(batch['ui_batch_id'])
        new_job=job_service.get_job(next_attempt['dispatch_key'])
        payload=dispatch.claim_ui_job(new_job)
        dispatch.record_ui_attempt_event(new_job['job_id'],1,{**event,'ui_claim_token':payload['ui_claim_token'],'status':'passed'})
        current=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert current['snapshot']['counts'] == {'failed':1,'passed':1}
        assert [a['attempt_no'] for a in current['snapshot']['attempts']] == [1,2]
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'],first['report_revision'])['snapshot']['counts'] == {'failed':1}


def test_revisiting_existing_digest_updates_current_report_head(dispatch_env):
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch
    batch,job,event=completed()
    with actor_context(ACTORS['a']):
        dispatch.record_ui_attempt_event(job['job_id'],2,{**event,'type':'report_ready','report_upload_error':''})
        first=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        dispatch.record_ui_attempt_event(job['job_id'],3,{**event,'type':'report_ready','report_upload_error':'transient error'})
        second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        dispatch.record_ui_attempt_event(job['job_id'],4,{**event,'type':'report_ready','report_upload_error':''})
        assert reports.build_ui_batch_report(None,batch['ui_batch_id'])['report_revision'] == first['report_revision']
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'])['report_revision'] == first['report_revision']
        assert b'transient error' not in reports.download_ui_batch_report(None,batch['ui_batch_id'],'md')[0]
        assert reports.get_ui_batch_report(None,batch['ui_batch_id'],second['report_revision'])['snapshot']['attempts'][0]['result']['report_upload_error'] == 'transient error'
        assert b'transient error' not in reports.download_ui_batch_report(None,batch['ui_batch_id'],'md',second['report_revision'])[0]
