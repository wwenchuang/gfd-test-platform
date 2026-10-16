import tempfile
import unittest
from pathlib import Path

class VideoCandidateTests(unittest.TestCase):
    def test_generated_title_is_accepted_validated_and_redacted(self):
        from task_server.services.ui_video_analysis import validate_analysis
        result={'events':[],'uncertainties':[],'multiple_scenarios':False,
                'title':'删除打印记录并验证列表更新'}
        self.assertEqual(validate_analysis(result,[],1000)['title'],result['title'])
        with self.assertRaises(ValueError):validate_analysis({**result,'title':'x'*81},[],1000)
        with self.assertRaises(ValueError):validate_analysis({**result,'title':{}},[],1000)
        cleaned=validate_analysis({**result,'title':'验证private-input'},[],1000,['private-input'])
        self.assertNotIn('private-input',cleaned['title'])
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
    assert snapshot['events'][0]['expected_candidate']=={'text':'个人页面出现','basis':'visible_candidate','frame_refs':['f0','f1'],'requires_confirmation':True}
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


def test_explicit_goal_does_not_overwrite_differing_video_ending():
    from task_server.services.ui_video_analysis import draft_proposal
    event={'action':'aiTap','target':'提交按钮','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'页面显示提交失败弹窗','evidence_kind':'inferred','uncertainties':[]}
    document={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'点击提交，检查订单页面显示已提交状态','media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event],'uncertainties':[]}
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']=='页面显示提交失败弹窗'
    assert content['steps'][-1]['target']=='页面显示提交失败弹窗'
    assert len(content['questions'])==1 and document['goal'] in content['questions'][0]
    assert snapshot['events'][0]['observation']=='页面显示提交失败弹窗'
    assert snapshot['events'][0]['expected_candidate']['basis']=='visible_candidate'
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


@pytest.mark.parametrize('goal',[
    '点击提交，检查订单页面显示已提交状态',
    '不要检查订单页面显示已提交状态，只核对失败弹窗',
    '验证订单页面显示已提交状态不是本次预期，只检查失败提示',
])
def test_goal_context_never_rewrites_failed_observation_or_assertion(goal):
    from task_server.services.ui_video_analysis import draft_proposal
    failure='页面显示提交失败弹窗'
    event={'action':'aiTap','target':'提交按钮','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':failure,'evidence_kind':'inferred','uncertainties':[]}
    final={**event,'action':'aiAssert','target':failure,'start_ms':1000,'frame_refs':['f1']}
    document={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':goal,'media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event,final],'uncertainties':[]}
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']==failure
    assert content['steps'][-1]['target']==failure
    assert all(item['expected_candidate']['text']==failure for item in snapshot['events'])
    assert len(content['questions'])==1
    assert goal in content['questions'][0] and failure in content['questions'][0]
    assert '确认' in content['questions'][0] and '目标' in content['questions'][0]
    assert snapshot['goal']==goal


def test_undisputed_visible_checks_do_not_require_retyping():
    from task_server.services.ui_video_analysis import draft_proposal
    event={'action':'aiTap','target':'首页我的入口','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'个人页面出现','evidence_kind':'inferred','uncertainties':[]}
    document={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'','media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event],'uncertainties':[]}
    content,snapshot=draft_proposal(document)
    assert content['steps'][0]['expected']=='个人页面出现'
    assert content['steps'][-1]['target']=='个人页面出现'
    assert content['questions']==[]
    assert all(step['review_state']=='needs_review' for step in content['steps'])


def test_assertion_candidate_disagrees_with_its_observation_needs_confirmation():
    from task_server.services.ui_video_analysis import draft_proposal
    event={'action':'aiAssert','target':'订单页面显示已提交状态','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'页面显示提交失败弹窗','evidence_kind':'inferred','uncertainties':[]}
    doc={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'订单页面显示已提交状态','media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event],'uncertainties':[]}
    content,snapshot=draft_proposal(doc)
    assert content['questions'] and '末尾检查候选' in content['questions'][0]
    assert event['target'] in content['questions'][0] and event['observation'] in content['questions'][0]
    assert snapshot['goal_review']['requires_confirmation']


def test_long_goal_preserved_without_exceeding_question_contract():
    from task_server.services.ui_video_analysis import draft_proposal
    event={'action':'aiTap','target':'首页我的入口','value':'','start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'个人页面出现','evidence_kind':'inferred','uncertainties':[]}
    doc={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'目标'*1000,'media':{'duration_ms':1000,'frames':[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]},'events':[event],'uncertainties':[]}
    content,snapshot=draft_proposal(doc)
    assert snapshot['goal']==doc['goal']
    assert len(content['questions'])==1 and len(content['questions'][0])<=2000


def test_real_prompt_supplies_complete_json_envelope_and_event_fields():
    import json,re
    from task_server.services import ai_skill_service as ai
    prompt=ai.render_ai_skill_prompt('ui_video_steps',{'app_id':APP,'frames':[]})
    # Exercise the actual template and existing schema validator, not a mocked skill.
    examples=[json.loads(block) for block in re.findall(r'```json\s*(.*?)\s*```',prompt,re.S)]
    for example in examples:
        ai.validate_ai_skill_output('ui_video_steps',example)
        assert set(example)=={'events','uncertainties','multiple_scenarios'}
    event=next(example['events'][0] for example in examples if example['events'])
    assert set(event)=={'action','target','value','start_ms','end_ms','frame_refs','observation','evidence_kind','uncertainties'}
    assert '结构示例' in prompt and '不能照抄' in prompt


def test_default_image_batches_respect_gateway_six_image_contract(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    from task_server.services.ui_video_analysis import analyze_frames
    frames=[{'frame_id':f'frame-{i:03}','timestamp_ms':i*1000} for i in range(14)]
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image fixture model mocked')
    calls=[]
    def model(name,payload,**kwargs):calls.append((payload,kwargs));return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',40);monkeypatch.setattr(ai,'run_ai_skill',model)
    analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':14000,'frames':frames}},tmp_path,lambda:False)
    assert [[f['timestamp_ms'] for f in c[0]['frames']] for c in calls]==[list(range(0,6000,1000)),list(range(5000,11000,1000)),list(range(10000,14000,1000))]
    assert all(len(c[1]['image_assets'])<=6 for c in calls)
    assert {f['frame_id'] for c in calls for f in c[0]['frames']}=={f['frame_id'] for f in frames}


@pytest.mark.parametrize('stage,error_kind', [('analysis','runtime'),('analysis','schema'),('draft_validation','contract'),('decode','decoder')])
def test_worker_diagnostics_are_stage_specific_and_never_include_exception_text(env,tmp_path,monkeypatch,caplog,stage,error_kind):
    import json,logging
    from task_server.services import ui_video_source_service as video,ui_video_analysis as analysis,ui_case_draft_service as drafts
    from task_server.workers import ui_video_worker as worker
    store=video.VideoStore(tmp_path/'videos');doc=store.upload(A,APP,'PRIVATE_FILENAME.mp4',io.BytesIO(b'video'),5,actor=A,goal='PRIVATE_GOAL')
    sentinel='sk-PRIVATE_TOKEN raw-model-response PRIVATE_VIDEO'
    class NoStringError(RuntimeError):
        def __str__(self):raise AssertionError('must not stringify exception')
    error=NoStringError(sentinel) if error_kind=='runtime' else ValueError('video_decode_invalid '+sentinel) if error_kind=='decoder' else ValueError(sentinel)
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr(worker,'decode_video',lambda *args:{'black':False,'static':False,'frames':[{'frame_id':'f','timestamp_ms':0}],'duration_ms':100})
    valid={'events':[{'action':'aiAssert','target':'个人页面显示帮助入口','value':'','start_ms':0,'end_ms':0,'frame_refs':['f'],'observation':'个人页面显示帮助入口','evidence_kind':'inferred','uncertainties':[]}],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(analysis,'analyze_frames',lambda *args:valid)
    if stage=='analysis':monkeypatch.setattr(analysis,'analyze_frames',fail)
    elif stage=='draft_validation':monkeypatch.setattr(analysis,'draft_proposal',fail)
    else:monkeypatch.setattr(worker,'decode_video',fail)
    caplog.set_level(logging.WARNING)
    worker.analyze_video_source(store,doc['source_id'])
    logged=[r.getMessage() for r in caplog.records if r.name==worker.__name__]
    assert len(logged)==1
    detail=json.loads(logged[0].split(' ',1)[1])
    assert detail['source_id']==doc['source_id'] and detail['stage']==stage
    assert detail['exception_class']==type(error).__name__
    assert detail['category'] in {'model_service','model_schema','draft_contract','decoder'}
    assert detail['traceback'] and all(set(frame)=={'file','function','line'} for frame in detail['traceback'])
    assert all('/' not in frame['file'] for frame in detail['traceback'])
    assert not any(value in logged[0] for value in [sentinel,'PRIVATE_FILENAME','PRIVATE_GOAL','raw-model-response'])
    result=store.get(doc['source_id'],A['user_id'],APP)
    assert result['status']=='failed' and sentinel not in str(result)
    assert result['error_code']=='video_analysis_failed'


def test_video_schema_value_type_is_bound_to_action():
    from jsonschema import Draft202012Validator
    from task_server.services import ai_skill_service as ai
    schema=ai.load_ai_skill_schema('ui_video_steps');Draft202012Validator.check_schema(schema)
    validator=Draft202012Validator(schema)
    base={'action':'aiScroll','target':'页面列表','value':{'direction':'down','scrollType':'singleAction'},'start_ms':0,'end_ms':1000,'frame_refs':['f0','f1'],'observation':'页面列表显示底部入口','evidence_kind':'inferred','uncertainties':[]}
    def errors(event):return list(validator.iter_errors({'events':[event],'uncertainties':[],'multiple_scenarios':False}))
    assert not errors(base)
    assert errors({**base,'value':'down'})
    assert errors({**base,'value':'{"direction":"down"}'})
    assert errors({**base,'action':'aiTap'})
    assert not errors({**base,'action':'aiTap','value':''})
    for value in [{'direction':'diagonal'},{'distance':0},{'scrollType':'scrollToBottom','direction':'down'},{'scrollType':'scrollToTop','distance':1},{'invented':True}]:assert errors({**base,'value':value})
    assert not errors({**base,'value':{'scrollType':'scrollToBottom'}})


def test_video_prompt_has_real_scroll_object_example_and_full_parameter_contract():
    import json,re
    from jsonschema import Draft202012Validator
    from task_server.services import ai_skill_service as ai
    prompt=ai.render_ai_skill_prompt('ui_video_steps',{'app_id':APP,'frames':[]})
    objects=[json.loads(block) for block in re.findall(r'```json\s*(.*?)\s*```',prompt,re.S)]
    events=[event for obj in objects for event in obj['events']]
    scrolls=[event for event in events if event['action']=='aiScroll']
    assert scrolls and all(isinstance(event['value'],dict) for event in scrolls)
    validator=Draft202012Validator(ai.load_ai_skill_schema('ui_video_steps'))
    for obj in objects:validator.validate(obj)
    for term in ['JSON 对象','字符串','down/up/right/left','singleAction','scrollToBottom','scrollToTop','scrollToRight','scrollToLeft','有限正数','不能带 direction 或 distance']:assert term in prompt


@pytest.mark.parametrize('value',['down','向上滚动','{"direction":"down","shell":"forbidden"}'])
def test_arbitrary_scroll_strings_remain_rejected(value):
    from task_server.services.ui_video_analysis import validate_analysis
    event={'action':'aiScroll','target':'页面列表','value':value,'start_ms':0,'end_ms':0,'frame_refs':['f0'],'observation':'页面列表显示底部入口','evidence_kind':'inferred','uncertainties':[]}
    with pytest.raises(ValueError,match='video_action_invalid'):
        validate_analysis({'events':[event],'uncertainties':[],'multiple_scenarios':False},[{'frame_id':'f0','timestamp_ms':0}],1000)


@pytest.mark.parametrize('value',[{'direction':'down','scrollType':'singleAction'},{'scrollType':'scrollToBottom'},{}])
def test_scroll_json_object_representation_normalizes_without_extra_questions(value):
    import json
    from task_server.services.ui_video_analysis import validate_analysis
    event={'action':'aiScroll','target':'页面列表','value':json.dumps(value),'start_ms':0,'end_ms':0,'frame_refs':['f0'],'observation':'页面列表显示底部入口','evidence_kind':'inferred','uncertainties':[]}
    result=validate_analysis({'events':[event],'uncertainties':[],'multiple_scenarios':False},[{'frame_id':'f0','timestamp_ms':0}],1000)
    assert result['events'][0]['value']==value
    assert result['events'][0]['uncertainties']==[] and result['uncertainties']==[]


@pytest.mark.parametrize('value',[
    '{"direction":"down","invented":true}',
    '[{"direction":"down"}]','null','"down"','1','true',
    '{"direction":{"value":"down"}}','{"distance":{"value":10}}',
    '{"direction":"down","scrollType":"scrollToTop"}',
    '{"direction":"up","direction":"down"}',
    '{"distance":NaN}','{"distance":Infinity}',
    '{"direction":"down"}'+(' '*4001),
    '{"direction":'+('['*1200)+'"down"'+(']'*1200)+'}',
])
def test_scroll_json_representation_rejects_unsafe_or_invalid_contract(value):
    from task_server.services.ui_video_analysis import validate_analysis
    event={'action':'aiScroll','target':'页面列表','value':value,'start_ms':0,'end_ms':0,'frame_refs':['f0'],'observation':'页面列表显示底部入口','evidence_kind':'inferred','uncertainties':[]}
    with pytest.raises(ValueError,match='video_action_invalid'):
        validate_analysis({'events':[event],'uncertainties':[],'multiple_scenarios':False},[{'frame_id':'f0','timestamp_ms':0}],1000)


from task_server.services.ai_skill_service import run_ai_skill as REAL_VIDEO_RUN_AI_SKILL

@pytest.mark.parametrize('material,expected_status',[('passive_carousel','review_needed'),('touchless_navigation','ready')])
def test_full_worker_distinguishes_passive_only_contract_from_touchless_navigation(env,tmp_path,monkeypatch,material,expected_status):
    """Actual ffmpeg + skill prompt/schema; only provider response is simulated.

    Synthetic frames test transport/state handling, not real AI classification.
    """
    import json,subprocess
    from PIL import Image,ImageDraw
    from task_server.services import ai_skill_service as ai,ui_video_source_service as video
    from task_server.workers import ui_video_worker as worker
    frame_count=8 if material=='passive_carousel' else 4
    for i in range(frame_count):
        image=Image.new('RGB',(160,240),'white');draw=ImageDraw.Draw(image)
        draw.text((10,10),'Home' if material=='passive_carousel' or i<2 else 'Profile',fill='black')
        draw.rectangle((8,45,152,180),fill=['red','blue','green','yellow'][i%4])
        image.save(tmp_path/f'page-{i:03d}.png')
    movie=tmp_path/'synthetic.mp4'
    subprocess.run([video._tool('ffmpeg'),'-v','error','-framerate','1','-i',str(tmp_path/'page-%03d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
    store=video.VideoStore(tmp_path/'sources');body=movie.read_bytes()
    doc=store.upload(A,APP,'synthetic.mp4',io.BytesIO(body),len(body),actor=A)
    calls=[]
    def provider(skill_name,prompt,**kwargs):
        assert '自动轮播' in prompt and 'events=[]' in prompt and '跨页面导航' in prompt
        assert '不能仅凭轮播内容变化生成 aiScroll 或 aiWaitFor' in prompt
        calls.append(skill_name)
        frames=kwargs['payload']['frames']
        if material=='passive_carousel':result={'events':[],'uncertainties':['素材仅有自动轮播，没有可支持用户动作的证据，请提供实际操作片段'],'multiple_scenarios':False}
        else:
            event={'action':'aiTap','target':'首页个人中心入口','value':'','start_ms':frames[0]['timestamp_ms'],'end_ms':frames[-1]['timestamp_ms'],'frame_refs':[frames[0]['frame_id'],frames[-1]['frame_id']],'observation':'个人页面显示账号入口','evidence_kind':'inferred','uncertainties':[]}
            result={'events':[event],'uncertainties':[],'multiple_scenarios':False}
        return json.dumps(result,ensure_ascii=False)
    monkeypatch.setattr(ai,'run_ai_skill',REAL_VIDEO_RUN_AI_SKILL)
    monkeypatch.setattr(ai,'ai_gateway_skill_content',provider)
    monkeypatch.setattr(ai,'AI_SKILLS_STRICT_MODEL',True)
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6)
    monkeypatch.setenv('MIDSCENE_AI_SKILLS_USE_GATEWAY','1')
    worker.analyze_video_source(store,doc['source_id'])
    result=store.get(doc['source_id'],A['user_id'],APP)
    assert calls and all(name=='ui_video_steps' for name in calls)
    assert len(calls)==2  # passive batches or one action batch plus global consolidation
    assert result['status']==expected_status
    assert result['media']['coverage_end_ms']==frame_count*1000 and not result['media']['static']
    assert result['draft_id']==''
    if material=='passive_carousel':
        assert result['candidate_steps']==[] and result['events']==[]
        assert result['uncertainties'] and not result.get('multiple_scenarios')
    else:
        assert len(result['candidate_steps'])==1 and result['candidate_steps'][0]['evidence_kind']=='inferred'
        assert len(result['raw_events'])==1 and 'raw_uncertainties' in result
        from task_server.services.ui_video_analysis import draft_proposal
        content,snapshot=draft_proposal(result)
        assert all(step['review_state']=='needs_review' for step in content['steps'])


def _consolidation_fixture(tmp_path):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*1000} for i in range(12)]
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'mocked visual asset')
    def event(action,start,end,target):
        return {'action':action,'target':target,'value':'','start_ms':start*1000,'end_ms':end*1000,
                'frame_refs':[f'f{start}',f'f{end}'] if start!=end else [f'f{start}'],
                'observation':'页面显示相应状态','evidence_kind':'inferred','uncertainties':[]}
    return frames,event


def test_global_consolidation_preserves_audit_and_bounded_explicit_images(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    opening=event('aiTap',0,1,'桌面应用图标')
    delete=event('aiTap',5,6,'目标记录的删除')
    confirm=event('aiTap',7,8,'确认删除')
    result=event('aiAssert',9,10,'目标记录消失且邻居保留')
    calls=[]
    def model(name,payload,**kwargs):
        calls.append((payload,kwargs))
        if payload.get('phase')=='consolidate':
            assert payload['frames']==frames
            assert payload['candidate_events']==[opening,delete,confirm,result]
            assert payload['raw_uncertainties']==['局部静止，没有操作']
            assert len(payload['image_frames'])<=6
            assert [f['frame_id'] for f in payload['image_frames']]==[asset['name'] for asset in kwargs['image_assets']]
            assert {f'f{i}' for i in (5,6,7,8,9,10)}=={f['frame_id'] for f in payload['image_frames']}
            return {'events':[delete,confirm,result],'uncertainties':[],'multiple_scenarios':False}
        times=[f['timestamp_ms'] for f in payload['frames']]
        batch_events=[e for e in (opening,delete,confirm,result) if times[0]<=e['start_ms'] and e['end_ms']<=times[-1]]
        return {'events':batch_events,'uncertainties':['局部静止，没有操作'],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    output=analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    assert len(calls)==4 and calls[-1][0]['phase']=='consolidate'
    assert output['events']==[delete,confirm,result] and output['uncertainties']==[]
    assert output['raw_events']==[opening,delete,confirm,result]
    assert output['raw_uncertainties']==['局部静止，没有操作']


@pytest.mark.parametrize('kind',['new_action','outside_time','extends_time','unknown_frame','cancelled','multiple','evidence_upgrade'])
def test_consolidation_cannot_forge_evidence_or_clear_multiscene(monkeypatch,tmp_path,kind):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path);raw=event('aiTap',0,1,'入口')
    cancelled=[False]
    def model(name,payload,**kwargs):
        if payload.get('phase')=='consolidate':
            final=dict(raw)
            if kind=='new_action':final['action']='aiAssert'
            if kind=='outside_time':final.update(start_ms=9000,end_ms=10000,frame_refs=['f9','f10'])
            if kind=='extends_time':final.update(end_ms=2000)
            if kind=='evidence_upgrade':final['evidence_kind']='observed'
            if kind=='unknown_frame':final['frame_refs']=['forged']
            if kind=='cancelled':cancelled[0]=True
            return {'events':[final],'uncertainties':[],'multiple_scenarios':False}
        return {'events':[raw] if payload['frames'][0]['timestamp_ms']==0 else [],'uncertainties':[],'multiple_scenarios':kind=='multiple'}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    run=lambda:analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:cancelled[0])
    if kind=='multiple':assert run()['multiple_scenarios'] is True
    elif kind=='evidence_upgrade':assert run()['events'][0]['evidence_kind']=='inferred'
    else:
        with pytest.raises(ValueError):run()


def test_consolidation_audit_keeps_overlap_observations_and_redacts_secrets(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    calls=[]
    def model(name,payload,**kwargs):
        calls.append(payload)
        if payload['phase']=='consolidate':
            assert 'secret-value' not in str(payload)
            return {'events':payload['candidate_events'],'uncertainties':[],'multiple_scenarios':False}
        if payload['frames'][0]['timestamp_ms']>5000:
            return {'events':[],'uncertainties':[],'multiple_scenarios':False}
        raw=event('aiInput',5,5,'输入框')
        raw.update(value='secret-value',observation=f"页面显示secret-value，批次{payload['frames'][0]['frame_id']}")
        return {'events':[raw],'uncertainties':['secret-value 需要确认'],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    result=analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    assert len(result['events'])==1 and len(result['raw_events'])==2
    assert result['raw_events'][0]['observation']!=result['raw_events'][1]['observation']
    assert 'secret-value' not in str(result)
    assert all(event['value']=='' for event in result['raw_events'])


def test_global_prompt_requires_business_scope_and_scroll_content_direction():
    from pathlib import Path
    prompt=(Path(__file__).resolve().parents[1]/'ai_skills/prompts/ui_video_steps.v1.md').read_text()
    for requirement in ('phase=consolidate','image_frames','不是手指移动方向','不得填经验默认值','不能用按钮高亮当成业务预期','原始观察由平台独立保留审计'):
        assert requirement in prompt


def test_consolidation_cannot_bridge_unsupported_action_time_gaps(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    def model(name,payload,**kwargs):
        if payload['phase']=='consolidate':
            return {'events':[event('aiTap',0,10,'合并点击')],'uncertainties':[],'multiple_scenarios':False}
        raw=[]
        if payload['frames'][0]['timestamp_ms']==0:raw=[event('aiTap',0,1,'入口')]
        elif payload['frames'][0]['timestamp_ms']==5000:raw=[event('aiTap',9,10,'确认')]
        return {'events':raw,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_timeline'):
        analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)


def test_consolidation_still_redacts_original_input_secret_when_value_is_empty(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    def model(name,payload,**kwargs):
        raw=event('aiInput',0,1,'输入框')
        raw['observation']='页面显示输入secret-original'
        if payload['phase']=='consolidate':
            return {'events':[raw],'uncertainties':['secret-original需要核对'],'multiple_scenarios':False}
        raw['value']='secret-original'
        return {'events':[raw] if payload['frames'][0]['timestamp_ms']==0 else [],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    result=analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    assert 'secret-original' not in str(result)


def test_global_images_reserve_navigation_and_scroll_alongside_destructive_result(tmp_path):
    from task_server.services.ui_video_analysis import _consolidation_images
    frames,event=_consolidation_fixture(tmp_path)
    events=[event('aiTap',0,1,'个人入口'),event('aiScroll',2,3,'记录列表'),
            event('aiTap',5,6,'目标记录删除'),event('aiTap',7,8,'确认删除'),
            event('aiWaitFor',9,10,'目标消失且其他记录保留')]
    refs={frame['frame_id'] for frame in _consolidation_images(events,frames,6)}
    assert len(refs)<=6 and refs&{'f0','f1'}
    assert {'f2','f3'}<=refs
    assert refs&{'f5','f6'} and refs&{'f7','f8'} and refs&{'f9','f10'}


def test_global_result_image_stays_before_return_navigation(tmp_path):
    from task_server.services.ui_video_analysis import _consolidation_images
    frames,event=_consolidation_fixture(tmp_path)
    events=[event('aiTap',0,1,'个人入口'),event('aiScroll',2,3,'个人页面'),
            event('aiTap',5,6,'第二条记录删除'),event('aiTap',7,8,'弹窗确认'),
            event('aiWaitFor',8,10,'记录列表'),event('aiTap',10,11,'返回个人页面')]
    events[4]['frame_refs']=['f8','f9','f10']
    refs={frame['frame_id'] for frame in _consolidation_images(events,frames,6)}
    assert {'f1','f2','f3','f5','f7','f9'} == refs


def test_global_result_image_can_come_from_confirmation_outcome(tmp_path):
    from task_server.services.ui_video_analysis import _consolidation_images
    frames,event=_consolidation_fixture(tmp_path)
    events=[event('aiTap',0,1,'个人入口'),event('aiScroll',2,3,'个人页面'),
            event('aiTap',5,6,'第二条记录删除'),event('aiTap',7,8,'弹窗确认'),
            event('aiTap',10,11,'返回个人页面')]
    events[3]['outcome']={'observation':'目标消失，邻居保留','frame_refs':['f9']}
    refs={frame['frame_id'] for frame in _consolidation_images(events,frames,6)}
    assert {'f1','f2','f3','f5','f7','f9'} == refs


def test_batch_invalid_result_frame_gets_one_bounded_repair(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    calls=[]
    def model(name,payload,**kwargs):
        calls.append(payload)
        if len(calls)==1:
            raw=event('aiTap',0,1,'入口')
            raw['start_ms']=100;raw['end_ms']=800  # Real refs are 0 and 1000ms.
            raw['outcome']={'observation':'目标页面','frame_refs':['f0']}
            return {'events':[raw],'uncertainties':[],'multiple_scenarios':False}
        return {'events':[event('aiTap',0,1,'入口')] if len(calls)==2 or payload['phase']=='consolidate' else [],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    assert len(calls)==5
    assert 'validation_feedback' in calls[1]
    assert calls[0]['frames']==calls[1]['frames']
    assert calls[1]['candidate_events']==[event('aiTap',0,1,'入口')]
    assert calls[1]['required_candidate_indexes']==[0]


def test_outcome_repair_preserves_original_secrets_and_multiscene(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    calls=[]
    def model(name,payload,**kwargs):
        calls.append(payload)
        assert 'repair-private-input' not in str(payload)
        raw=event('aiInput',0,1,'测试输入')
        if len(calls)==1:
            raw.update(value='repair-private-input',outcome={'observation':'结果出现','frame_refs':['f0']})
            return {'events':[raw],'uncertainties':[],'multiple_scenarios':True}
        if len(calls)==2 or payload['phase']=='consolidate':
            raw['observation']='页面显示repair-private-input'
            return {'events':[raw],'uncertainties':['repair-private-input需要核对'],'multiple_scenarios':False}
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    result=analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    assert result['multiple_scenarios'] is True
    assert 'repair-private-input' not in str(result)
    assert len(calls)==5 and sum('validation_feedback' in payload for payload in calls)==1


@pytest.mark.parametrize('repair_mode',['invalid','missing','later_batch'])
def test_outcome_repair_cannot_loop_or_drop_interactions(monkeypatch,tmp_path,repair_mode):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path);calls=[]
    def model(name,payload,**kwargs):
        calls.append(payload)
        start=payload['frames'][0]['timestamp_ms']//1000
        raw=event('aiTap',start,start+1,'入口')
        if len(calls)==1 or repair_mode=='invalid' or (repair_mode=='later_batch' and len(calls)==3):
            raw['outcome']={'observation':'页面','frame_refs':[f'f{start}']}
        return {'events':[] if len(calls)==2 and repair_mode=='missing' else [raw],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError):
        analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    assert len(calls)==(3 if repair_mode=='later_batch' else 2)


@pytest.mark.parametrize('duplicate_wrong_time',[False,True])
def test_global_cannot_discard_required_navigation_even_with_equal_action_count(monkeypatch,tmp_path,duplicate_wrong_time):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    def model(name,payload,**kwargs):
        if payload['phase']=='consolidate':
            events=[event('aiTap',9,10,'后续确认')]
            if duplicate_wrong_time:events.insert(0,event('aiTap',9,9,'重复后续确认'))
            return {'events':events,'uncertainties':[],'multiple_scenarios':False}
        events=[]
        if payload['frames'][0]['timestamp_ms']==0:events=[event('aiTap',0,1,'个人入口')]
        elif payload['frames'][0]['timestamp_ms']==5000:events=[event('aiTap',9,10,'后续确认')]
        return {'events':events,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)


def test_outcome_refs_are_separate_from_interaction_time_and_supply_expected(tmp_path):
    from task_server.services.ui_video_analysis import validate_analysis,draft_proposal
    frames,event=_consolidation_fixture(tmp_path)
    confirm=event('aiTap',1,2,'确认删除')
    confirm['observation']='确认按钮出现高亮'
    confirm['outcome']={'observation':'目标记录消失，其他记录仍然可见','frame_refs':['f3','f4']}
    result=validate_analysis({'events':[confirm],'uncertainties':[],'multiple_scenarios':False},frames,12000)
    assert result['events'][0]['end_ms']==2000
    content,snapshot=draft_proposal({'source_id':'uvs_'+'a'*32,'analysis_version':1,'app_id':APP,'goal':'','media':{'frames':frames,'duration_ms':12000},**result})
    assert content['steps'][0]['expected']=='目标记录消失，其他记录仍然可见'
    assert snapshot['events'][0]['expected_candidate']['frame_refs']==['f3','f4']
    assert all(step['review_state']=='needs_review' for step in content['steps'])


@pytest.mark.parametrize('outcome',[
    {'observation':'目标记录消失','frame_refs':['unknown']},
    {'observation':'目标记录消失','frame_refs':['f0']},
    {'observation':'目标记录消失','frame_refs':['f4','f3']},
    {'observation':'目标记录消失','frame_refs':['f3'],'extra':True},
])
def test_outcome_rejects_unknown_early_or_invalid_evidence(tmp_path,outcome):
    from task_server.services.ui_video_analysis import validate_analysis
    frames,event=_consolidation_fixture(tmp_path)
    raw=event('aiTap',1,2,'确认删除');raw['outcome']=outcome
    with pytest.raises(ValueError):validate_analysis({'events':[raw],'uncertainties':[],'multiple_scenarios':False},frames,12000)


@pytest.mark.parametrize('result_time,allowed',[(3,True),(4,True),(5,False)])
def test_consolidated_outcome_cannot_borrow_result_from_next_interaction(monkeypatch,tmp_path,result_time,allowed):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    def model(name,payload,**kwargs):
        if payload['phase']=='consolidate':
            first=event('aiTap',0,1,'确认操作')
            first['outcome']={'observation':'目标记录消失且其他记录保留','frame_refs':[f'f{result_time}']}
            return {'events':[first,event('aiTap',4,5,'后续导航')],'uncertainties':[],'multiple_scenarios':False}
        events=[]
        if payload['frames'][0]['timestamp_ms']==0:
            events=[event('aiTap',0,1,'确认操作'),event('aiWaitFor',2,3,'目标状态'),event('aiTap',4,5,'后续导航')]
        if payload['frames'][0]['timestamp_ms']==5000:events=[event('aiWaitFor',5,6,'其他页面')]
        return {'events':events,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    run=lambda:analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)
    if allowed:assert run()['events'][0]['outcome']['frame_refs']==[f'f{result_time}']
    else:
        with pytest.raises(ValueError,match='video_reference_invalid'):run()


def test_outcome_is_schema_compatible_redacted_and_not_button_highlight_expected(tmp_path):
    import json,jsonschema
    from task_server.services.ui_video_analysis import validate_analysis,_expected_candidate
    frames,event=_consolidation_fixture(tmp_path)
    raw=event('aiInput',0,1,'测试输入')
    raw.update(value='secret-outcome',outcome={'observation':'页面显示secret-outcome','frame_refs':['f2']})
    payload={'events':[raw],'uncertainties':[],'multiple_scenarios':False}
    schema=json.loads((Path(__file__).resolve().parents[1]/'ai_skills/schemas/ui_video_steps.schema.json').read_text())
    jsonschema.validate(payload,schema)
    clean=validate_analysis(payload,frames,12000)
    assert 'secret-outcome' not in str(clean)
    raw['value']=''
    assert 'secret-outcome' not in str(validate_analysis(payload,frames,12000,['secret-outcome']))
    highlight=event('aiTap',0,1,'弹窗确认删除');highlight['observation']='确认按钮显示高亮'
    assert _expected_candidate(highlight,'')['text']==''
    highlight['outcome']={'observation':'确认按钮显示高亮','frame_refs':['f2']}
    assert _expected_candidate(highlight,'')['text']==''


def test_outcome_cannot_cross_next_final_interaction_with_same_start(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames,event=_consolidation_fixture(tmp_path)
    def model(name,payload,**kwargs):
        if payload['phase']=='consolidate':
            first=event('aiTap',2,2,'确认')
            first['outcome']={'observation':'目标状态显示完成','frame_refs':['f5']}
            return {'events':[first,event('aiTap',2,4,'下一交互')],'uncertainties':[],'multiple_scenarios':False}
        raw=[]
        if payload['frames'][0]['timestamp_ms']==0:raw=[event('aiTap',0,2,'确认'),event('aiTap',2,4,'下一交互')]
        elif payload['frames'][0]['timestamp_ms']==5000:raw=[event('aiWaitFor',5,6,'后续结果')]
        return {'events':raw,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6);monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_reference_invalid'):
        analysis.analyze_frames({'app_id':APP,'goal':'','media':{'duration_ms':12000,'frames':frames}},tmp_path,lambda:False)


@pytest.mark.parametrize('observation,expected',[
    ('页面向下滚动浏览，顶部昵称区域移出屏幕，底部服务模块及二维码完全显现','页面向下滚动浏览，顶部昵称区域移出屏幕，底部服务模块及二维码完全显现'),
    ('由打印记录列表返回个人中心，底部导航可见且我的标签高亮','由打印记录列表返回个人中心；底部导航可见'),
    ('画面显示删除成功提示浮层，列表中目标记录已消失其余保留','列表中目标记录已消失其余保留'),
    ('新页面并显示加载状态',''),
    ('确认按钮显示高亮',''),
    ('页面不显示报错，目标记录已消失','页面不显示报错，目标记录已消失'),
    ('如果提示浮层消失，才显示目标记录',''),
    ('不是目标记录消失，而是确认按钮高亮',''),
])
def test_visible_expected_keeps_stable_state_without_transient_feedback(observation,expected):
    from task_server.services.ui_video_analysis import _expected_candidate
    event={'observation':observation,'frame_refs':['action'],
           'outcome':{'observation':observation,'frame_refs':['result']}}
    candidate=_expected_candidate(event,'')
    assert candidate['text']==expected
    assert candidate['frame_refs']==['result'] and candidate['requires_confirmation']
    assert event['observation']==observation and event['outcome']['observation']==observation


def test_navigation_stable_result_creates_final_assert_without_requiring_feedback(tmp_path):
    from task_server.services.ui_video_analysis import draft_proposal
    frames,event=_consolidation_fixture(tmp_path)
    back=event('aiTap',1,2,'左上返回')
    back['observation']='由业务列表返回上级页面，底部导航可见且当前标签高亮'
    content,snapshot=draft_proposal({'source_id':'uvs_'+'a'*32,'analysis_version':1,'app_id':APP,'goal':'',
                                   'media':{'frames':frames,'duration_ms':12000},'events':[back],'uncertainties':[]})
    assert content['steps'][-1]['action']=='aiAssert'
    assert content['steps'][0]['expected']=='由业务列表返回上级页面；底部导航可见'
    assert '高亮' not in content['steps'][-1]['target']
    assert not content['questions']
    assert snapshot['events'][0]['observation']==back['observation']
    assert all(step['review_state']=='needs_review' for step in content['steps'])


def test_global_prompt_requires_reassessment_of_local_notes_and_stable_outcomes():
    prompt=(Path(__file__).resolve().parents[1]/'ai_skills/prompts/ui_video_steps.v1.md').read_text()
    for rule in ('全局 uncertainties 不是 raw_uncertainties 的复制或汇总','局部结论','uncertainties=[] 是正确输出',
                 '不能因为一句话含“无触点”就删除真正歧义','所有交互的 outcome 都优先表达',
                 '目标列表可见','不要求浮层可见','不要延长end_ms'):
        assert rule in prompt


@pytest.mark.parametrize('observation',[
    '未观察到以下状态：按钮高亮，目标记录消失',
    '没有证据表明按钮高亮且目标记录消失',
])
def test_stable_projection_never_loses_cognitive_negation_scope(observation):
    from task_server.services.ui_video_analysis import _expected_candidate
    assert _expected_candidate({'observation':observation,'frame_refs':['f']},'')['text']==''


def test_navigation_transition_keeps_named_destination_after_loading_clause_removed():
    from task_server.services.ui_video_analysis import _expected_candidate
    observation='画面由上级页面跳转至标题为“业务记录”的新页面，并显示加载状态。'
    candidate=_expected_candidate({'observation':observation,'frame_refs':['f']},'')
    assert candidate['text']=='画面由上级页面跳转至标题为“业务记录”的新页面'
    for vague in ('画面跳转至新页面，并显示加载状态','画面进入未知页面，按钮高亮'):
        assert _expected_candidate({'observation':vague,'frame_refs':['f']},'')['text']==''


def test_stable_projection_keeps_record_reordering_and_drops_temporarily_shown_notice():
    from task_server.services.ui_video_analysis import _expected_candidate
    observation='目标记录（2026-09-28 10:43:15）从列表中消失，原第三条记录（10:41:25）上移成为第二条，且列表上方短暂显示“删除成功”提示框。'
    event={'observation':'确认按钮按下','frame_refs':['action'],
           'outcome':{'observation':observation,'frame_refs':['result']}}
    candidate=_expected_candidate(event,'')
    assert candidate['text']=='目标记录（2026-09-28 10:43:15）从列表中消失；原第三条记录（10:41:25）上移成为第二条'
    assert candidate['frame_refs']==['result'] and candidate['requires_confirmation']
    assert event['outcome']['observation']==observation


@pytest.mark.parametrize('feedback',['短暂显示完成提示框','短暂出现完成提示','短暂弹出提示消息'])
def test_temporary_notice_projection_preserves_negation_scope(feedback):
    from task_server.services.ui_video_analysis import _expected_candidate
    assert _expected_candidate({'observation':f'没有证据表明{feedback}且目标记录消失','frame_refs':['f']},'')['text']==''


def test_batch_evidence_time_alignment_uses_real_frames_without_mutating_input():
    from task_server.services.ui_video_analysis import _align_batch_evidence_times
    frames=[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]
    raw={'events':[{'action':'aiTap','target':'入口','start_ms':100,'end_ms':800,'frame_refs':['f0','f1']} ]}
    aligned=_align_batch_evidence_times(raw,frames,2000)
    assert (aligned['events'][0]['start_ms'],aligned['events'][0]['end_ms'])==(0,1000)
    assert raw['events'][0]['start_ms']==100 and raw['events'][0]['end_ms']==800
    assert aligned['events'][0]['target']=='入口'


@pytest.mark.parametrize('override',[
    {'frame_refs':['unknown']},{'start_ms':-1},{'end_ms':3000},
    {'start_ms':'100'},{'frame_refs':['f1','f0']},
])
def test_batch_time_alignment_never_repairs_unknown_frames_or_invalid_timeline(override):
    from task_server.services.ui_video_analysis import _align_batch_evidence_times
    frames=[{'frame_id':'f0','timestamp_ms':0},{'frame_id':'f1','timestamp_ms':1000}]
    raw={'events':[dict(start_ms=100,end_ms=800,frame_refs=['f0','f1'])]}
    raw['events'][0].update(override)
    assert _align_batch_evidence_times(raw,frames,2000)==raw


def test_batch_time_alignment_does_not_hide_original_reverse_order():
    from task_server.services.ui_video_analysis import _align_batch_evidence_times
    frames=[{'frame_id':'f0','timestamp_ms':0}]
    raw={'events':[{'start_ms':900,'end_ms':1000,'frame_refs':['f0']},
                   {'start_ms':500,'end_ms':600,'frame_refs':['f0']}]}
    assert _align_batch_evidence_times(raw,frames,2000)==raw
