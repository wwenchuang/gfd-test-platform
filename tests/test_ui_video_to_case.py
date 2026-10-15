import tempfile
import unittest
from pathlib import Path

class VideoCandidateTests(unittest.TestCase):
    def test_references_and_platform_actions_are_enforced(self):
        from task_server.services.ui_video_analysis import validate_analysis
        frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-end','timestamp_ms':1900}]
        event={'action':'aiTap','target':'首页的我的入口','value':'','start_ms':0,'end_ms':1900,'frame_refs':['frame-001','frame-end'],'observation':'我的页面出现','evidence_kind':'inferred','uncertainties':[]}
        result=validate_analysis({'events':[event],'uncertainties':[],'multiple_scenarios':False},frames,2000)
        self.assertEqual(result['events'][0]['evidence_kind'],'inferred')
        for key,value in [('action','runAdbShell'),('frame_refs',['invented']),('end_ms',3000),('evidence_kind','certain')]:
            bad={**event,key:value}
            with self.assertRaises(ValueError):validate_analysis({'events':[bad],'uncertainties':[],'multiple_scenarios':False},frames,2000)

    def test_repeat_events_retained_secret_input_cleared(self):
        from task_server.services.ui_video_analysis import validate_analysis, draft_proposal
        frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':1000},{'frame_id':'frame-end','timestamp_ms':1900}]
        event={'action':'aiInput','target':'密码输入框','value':'secret123','start_ms':0,'end_ms':1000,'frame_refs':['frame-001','frame-002'],'observation':'键盘出现','evidence_kind':'inferred','uncertainties':[]}
        result=validate_analysis({'events':[event,{**event,'start_ms':1000,'end_ms':1900,'frame_refs':['frame-002','frame-end']}],'uncertainties':[],'multiple_scenarios':False},frames,2000)
        self.assertEqual(len(result['events']),2)
        self.assertEqual(result['events'][0]['value'],'')
        proposal,snapshot=draft_proposal({'source_id':'uvs_'+'a'*32,'app_id':'com.example.app','analysis_version':1,'goal':'','media':{'frames':frames,'duration_ms':2000},**result})
        self.assertEqual(len(proposal['steps']),2)
        self.assertTrue(all(s['review_state']=='needs_review' and s['expected']=='' for s in proposal['steps']))
        self.assertNotIn('secret123',str(snapshot))
        self.assertTrue(proposal['questions'])

if __name__=='__main__':unittest.main()

# Service integration: real private draft store, explicitly mocked identity/model.
import copy
import io
import pytest
from test_ui_case_draft_service import env, A, B, APP
from task_server.services.operation_attribution import actor_context


def test_video_import_is_private_idempotent_and_preserves_edits(env,tmp_path,monkeypatch):
    from task_server.services import ui_video_source_service as video, ui_case_draft_service as drafts
    store=video.VideoStore(tmp_path/'videos');monkeypatch.setattr(video,'service_store',lambda:store)
    doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'mp4'),3,actor=A)
    frame=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-end','timestamp_ms':1900}]
    event={'action':'aiTap','target':'首页我的入口','value':'','start_ms':0,'end_ms':1900,'frame_refs':['frame-001','frame-end'],'observation':'个人页面出现','evidence_kind':'inferred','uncertainties':[]}
    store.transition(doc['source_id'],{'queued'},'ready',media={'duration_ms':2000,'frames':frame},events=[event],uncertainties=[])
    with actor_context(A):
        draft=video.import_video_draft(A,doc['source_id'],APP)
        body={key:copy.deepcopy(draft[key]) for key in ('title','preconditions','steps','questions')}
        body.update(revision=1,title='人工核对标题')
        edited=drafts.update_draft(A,draft['draft_id'],body)
        reopened=video.import_video_draft(A,doc['source_id'],APP)
        assert reopened['revision']==2 and reopened['title']=='人工核对标题'
        assert reopened['source_snapshot']==draft['source_snapshot']
        assert reopened['steps'][0]['source_ref'].startswith('video:')
        forged=copy.deepcopy(body);forged['revision']=2;forged['steps'][0]['source_ref']='text'
        with pytest.raises(ValueError,match='draft_source_invalid'):drafts.update_draft(A,draft['draft_id'],forged)
    with actor_context(B):
        with pytest.raises(KeyError):video.import_video_draft(B,doc['source_id'],APP)
    assert len(store.list(A['user_id'],APP))==1


def test_visual_batches_keep_entire_timeline_and_overlap(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    from task_server.services.ui_video_analysis import analyze_frames
    frames=[{'frame_id':f'frame-{i:03}','timestamp_ms':i*1000} for i in range(9)]
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'fixture image model mocked')
    calls=[]
    def model(name,payload,**kwargs):
        calls.append((payload,kwargs));return {'events':[],'uncertainties':['无可靠动作'],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',4);monkeypatch.setattr(ai,'run_ai_skill',model)
    analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':9000,'frames':frames}},tmp_path,lambda:False)
    assert [[f['timestamp_ms'] for f in call[0]['frames']] for call in calls]==[[0,1000,2000,3000],[3000,4000,5000,6000],[6000,7000,8000]]
    assert all(len(call[1]['image_assets'])<=4 for call in calls)


def test_video_permissions_do_not_require_global_configuration():
    from task_server.access_control import MainAccess,AccessDenied
    access=MainAccess({'permissions':['ui.view','ui.edit'],'scope':{'ui_apps':[APP]}},[])
    identifier='uvs_'+'a'*32
    for method,path in [('POST','/api/ui-video-sources/upload'),('GET','/api/ui-video-sources'),('GET',f'/api/ui-video-sources/{identifier}/video'),('GET',f'/api/ui-video-sources/{identifier}/frames/frame-001'),('POST',f'/api/ui-video-sources/{identifier}/import')]:access.check(method,path)
    viewer=MainAccess({'permissions':['ui.view'],'scope':{'ui_apps':[APP]}},[])
    with pytest.raises(AccessDenied):viewer.check('POST','/api/ui-video-sources/upload')


def test_model_input_value_never_survives_in_observations_or_questions():
    from task_server.services.ui_video_analysis import validate_analysis
    raw={'action':'aiInput','target':'密码框 secret123','value':'secret123','start_ms':0,'end_ms':1,'frame_refs':['f'],'observation':'输入了 secret123','evidence_kind':'inferred','uncertainties':['请核对 secret123']}
    result=validate_analysis({'events':[raw],'uncertainties':['secret123 是否正确'],'multiple_scenarios':False},[{'frame_id':'f','timestamp_ms':0}],2)
    assert 'secret123' not in str(result)


def test_cancel_retry_during_model_does_not_publish_old_analysis(env,tmp_path,monkeypatch):
    from task_server.services import ui_video_source_service as video,ui_video_analysis as analysis
    from task_server.workers import ui_video_worker as worker
    store=video.VideoStore(tmp_path/'videos');doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *args:{'black':False,'static':False,'frames':[],'duration_ms':100})
    cancellation=[]
    def model(document,root,cancelled):
        store.cancel(doc['source_id']);store.transition(doc['source_id'],{'cancelled'},'queued',analysis_version=2)
        cancellation.append(cancelled())
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(analysis,'analyze_frames',model)
    worker.analyze_video_source(store,doc['source_id'])
    assert store.get(doc['source_id'],A['user_id'],APP)['status']=='queued'
    assert cancellation==[True]

from test_operation_http import operation_server,events
from test_identity import identity_db


def test_real_http_stream_upload_private_media_and_oversize_close(operation_server,identity_db,tmp_path,monkeypatch):
    import http.client,json,threading,urllib.parse
    from task_server import auth
    from task_server.services import business_line_service
    from task_server.workers import ui_video_worker
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1');monkeypatch.setenv('TASK_UI_VIDEO_ROOT',str(tmp_path/'video-http'))
    monkeypatch.setattr(business_line_service,'configured_test_application',lambda app:{'enabled':True,'package':APP} if app==APP else None)
    monkeypatch.setattr(ui_video_worker,'ensure_worker',lambda store:threading.Event())
    server,audit=operation_server;token=auth.create_session_token()
    def call(method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        c.request(method,path,body=body,headers={'Authorization':'Bearer '+token,**(headers or {})});r=c.getresponse();out=(r.status,dict(r.getheaders()),r.read());c.close();return out
    status,_,body=call('POST','/api/ui-video-sources/upload?'+urllib.parse.urlencode({'app_id':APP,'filename':'PRIVATE_SENTINEL.mp4'}),b'fixture-not-decoded',{'Content-Type':'application/octet-stream'})
    assert status==202,body
    source=json.loads(body)['source'];assert source['status']=='queued';assert 'actor' not in source
    status,headers,body=call('GET',f"/api/ui-video-sources/{source['source_id']}/video?app_id={APP}")
    assert status==200 and body==b'fixture-not-decoded';assert headers['Cache-Control']=='private, no-store'
    assert call('GET',f"/api/ui-video-sources/{source['source_id']}?app_id=com.other.app")[0] in {400,403,404}
    # A second real authenticated user has the same app and UI permissions.
    # Their denials must be ownership failures, not a missing global permission.
    from test_identity import create_member,activate
    from task_server.services.ui_video_source_service import VideoStore
    identity,identities=identity_db
    create_member(identities,'video-second');activate(identities,'video-second')
    identities.update_user('admin','video-second',{'scope':{'ui_apps':[APP],'api_projects':[],'api_environments':[]}})
    second_token=auth.create_session_token('video-second')
    private=VideoStore(tmp_path/'video-http')
    frames=private.root/source['source_id']/'frames';frames.mkdir()
    from PIL import Image
    Image.new('RGB',(20,20),'blue').save(frames/'frame-001.jpg')
    private.transition(source['source_id'],{'queued'},'queued',media={'frames':[{'frame_id':'frame-001','timestamp_ms':0}]})
    before=private.list(source_owner:=identity.get_access_profile('admin')['user_id'],APP)[0]
    assert call('GET',f"/api/ui-video-sources/{source['source_id']}/frames/frame-001?app_id={APP}")[0]==200
    prefix=f"/api/ui-video-sources/{source['source_id']}"
    for suffix in ('','/video','/frames/frame-001'):
        status,_,payload=call('GET',prefix+suffix+'?app_id='+APP,headers={'Authorization':'Bearer '+second_token})
        assert status==404,payload
        assert b'fixture-not-decoded' not in payload and b'PRIVATE_SENTINEL' not in payload
    status,_,payload=call('GET','/api/ui-video-sources?app_id='+APP,headers={'Authorization':'Bearer '+second_token})
    assert status==200 and json.loads(payload)['sources']==[]
    for verb in ('cancel','retry','import'):
        status,_,payload=call('POST',prefix+'/'+verb,json.dumps({'app_id':APP}),{'Authorization':'Bearer '+second_token,'Content-Type':'application/json'})
        assert status==404,payload
    assert private.get(source['source_id'],source_owner,APP)==before
    # Header-only rejection must close the connection without reading 200MB.
    c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    c.putrequest('POST','/api/ui-video-sources/upload?app_id='+APP+'&filename=x.mp4');c.putheader('Authorization','Bearer '+token);c.putheader('Content-Length',str(200*1024**2+1));c.putheader('Content-Type','application/octet-stream');c.endheaders()
    response=c.getresponse();assert response.status==400;assert json.loads(response.read())['code']=='video_size';assert response.will_close;c.close()
    rows=events(audit,4);assert any(r['resource_id']==source['source_id'] and r['resource_type']=='ui_video' for r in rows)
    assert 'PRIVATE_SENTINEL' not in str(rows)


def test_expected_candidates_keep_provenance_and_need_confirmation():
    from task_server.services.ui_video_analysis import draft_proposal
    event={'action':'aiTap','target':'首页我的入口','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'个人页面出现','evidence_kind':'inferred','uncertainties':[]}
    document={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'点击我的入口后检查个人页面出现','media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event],'uncertainties':[]}
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']=='个人页面出现'
    assert content['steps'][0]['review_state']=='needs_review'
    assert not any('补充明确的预期' in q for q in content['questions'])
    assert snapshot['events'][0]['expected_candidate']=={'text':'个人页面出现','basis':'user_goal','frame_refs':['f0','f1'],'requires_confirmation':True}
    document['goal']=''
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']=='个人页面出现'
    assert snapshot['events'][0]['expected_candidate']['basis']=='visible_candidate'
    document['events'][0]['observation']='页面变化，结果不明确'
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']==''
    assert snapshot['events'][0]['expected_candidate']['basis']=='unresolved'


@pytest.mark.parametrize('value',[{'distance':-10,'invented':True},{'distance':float('inf')},{'direction':'diagonal'},{'scrollType':'scrollToBottom','direction':'down'},{'distance':0}])
def test_invalid_scroll_rejected_before_ready(value):
    from task_server.services.ui_video_analysis import validate_analysis
    event={'action':'aiScroll','target':'个人页面列表','value':value,'start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'列表底部显示帮助入口','evidence_kind':'inferred','uncertainties':[]}
    with pytest.raises(ValueError,match='video_action_invalid'):
        validate_analysis({'events':[event],'uncertainties':[],'multiple_scenarios':False},[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}],1000)


def test_explicit_goal_is_candidate_even_when_video_ending_differs():
    from task_server.services.ui_video_analysis import draft_proposal
    event={'action':'aiTap','target':'提交按钮','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'页面显示提交失败弹窗','evidence_kind':'inferred','uncertainties':[]}
    document={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'点击提交，检查订单页面显示已提交状态','media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event],'uncertainties':[]}
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']=='订单页面显示已提交状态'
    assert content['steps'][-1]['target']=='订单页面显示已提交状态'
    assert snapshot['events'][0]['observation']=='页面显示提交失败弹窗'
    assert snapshot['events'][0]['expected_candidate']['basis']=='user_goal'
    assert all(step['review_state']=='needs_review' for step in content['steps'])


def test_worker_rejects_unimportable_scroll_before_ready(env,tmp_path,monkeypatch):
    from task_server.services import ui_video_source_service as video,ui_video_analysis as analysis
    from task_server.workers import ui_video_worker as worker
    store=video.VideoStore(tmp_path/'videos');doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *args:{'black':False,'static':False,'frames':[{'frame_id':'f','timestamp_ms':0}],'duration_ms':100})
    invalid={'action':'aiScroll','target':'个人页面列表','value':{'invented':True},'start_ms':0,'end_ms':0,'frame_refs':['f'],'observation':'个人页面显示帮助入口','evidence_kind':'inferred','uncertainties':[]}
    monkeypatch.setattr(analysis,'analyze_frames',lambda *args:{'events':[invalid],'uncertainties':[],'multiple_scenarios':False})
    worker.analyze_video_source(store,doc['source_id'])
    result=store.get(doc['source_id'],A['user_id'],APP)
    assert result['status']=='failed' and result['draft_id']==''
    assert store.retry(doc['source_id'],A['user_id'],APP)['status']=='queued'
