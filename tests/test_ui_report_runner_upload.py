import pytest
from test_ui_runner_events import runner, job


def test_direct_upload_carries_job_bound_headers(runner,monkeypatch,tmp_path):
    runner.initialize_ui_delivery(job(runner))
    path=tmp_path/'same.html'; path.write_text('private')
    calls=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return b'/api/ui-executions/uib_test/artifacts/digest'
    monkeypatch.setattr(runner.urllib.request,'urlopen',lambda req,**kw:calls.append(req) or Response())
    runner.http_upload_report(path,'same.html',job_id='uib_test_1_1')
    headers={k.lower():v for k,v in calls[0].header_items()}
    assert headers['x-ui-job-id'] == 'uib_test_1_1'
    assert headers['x-ui-claim-token'] == 'secret'
    assert headers['x-ui-device-id'] == 'selected'


def test_chunk_retry_and_finish_keep_claim(runner,monkeypatch,tmp_path):
    runner.initialize_ui_delivery(job(runner))
    path=tmp_path/'same.html'; path.write_text('private')
    calls=[]
    monkeypatch.setattr(runner,'http_json',lambda method,url,body,**kw:calls.append((url,body)) or {'url':'private-url'})
    runner.http_upload_report_chunked(path,'中文用例.html',chunk_size=3,job_id='uib_test_1_1')
    import re
    assert all(re.fullmatch(r'[A-Za-z0-9_.-]{1,240}',body['upload_id']) for _,body in calls)
    assert len(calls) == 4
    assert all(body['job_id'] == 'uib_test_1_1' and body['ui_claim_token'] == 'secret' for _,body in calls)


def test_private_upload_cannot_fall_back_without_claim_context(runner,tmp_path):
    path=tmp_path/'same.html'; path.write_text('private')
    with pytest.raises(ValueError): runner.http_upload_report(path,'same.html',job_id='uib_missing')


def test_immediate_upload_forwards_explicit_job_identity(runner,monkeypatch,tmp_path):
    calls=[]
    path=tmp_path/'same.html'; path.write_text('private')
    runner.initialize_ui_delivery(job(runner))
    monkeypatch.setattr(runner,'http_upload_report',lambda path,name,**kw:calls.append(kw) or 'private-url')
    monkeypatch.setattr(runner,'post_job_report_ready',lambda *a:None)
    runner.upload_report_before_result('uib_test_1_1',path,'same.html')
    assert calls == [{'job_id':'uib_test_1_1'}]


def test_restart_outbox_restores_report_upload_identity(runner,monkeypatch,tmp_path):
    runner.initialize_ui_delivery(job(runner))
    monkeypatch.setattr(runner,'CALLBACK_OUTBOX_DIR',tmp_path/'outbox')
    payload={'status':'failed','report_upload_pending':True}
    runner.ui_callback_payload('uib_test_1_1',payload,result=True)
    runner.queue_failed_result_callback('uib_test_1_1',payload,'/private/report.html','same.html')
    runner._UI_DELIVERIES.clear()
    observed=[]
    monkeypatch.setattr(runner,'post_job_result',lambda *a:None)
    monkeypatch.setattr(runner,'enqueue_report_upload',lambda job_id,*a:observed.append(runner.ui_report_upload_context(job_id)))
    assert runner.replay_pending_result_callbacks() == 1
    assert observed[0]['job_id'] == 'uib_test_1_1' and observed[0]['ui_claim_token'] == 'secret'
