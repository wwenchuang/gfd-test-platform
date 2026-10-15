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
    assert len(calls)==(2 if material=='passive_carousel' else 1)
    assert result['status']==expected_status
    assert result['media']['coverage_end_ms']==frame_count*1000 and not result['media']['static']
    assert result['draft_id']==''
    if material=='passive_carousel':
        assert result['candidate_steps']==[] and result['events']==[]
        assert result['uncertainties'] and not result.get('multiple_scenarios')
    else:
        assert len(result['candidate_steps'])==1 and result['candidate_steps'][0]['evidence_kind']=='inferred'
        from task_server.services.ui_video_analysis import draft_proposal
        content,snapshot=draft_proposal(result)
        assert all(step['review_state']=='needs_review' for step in content['steps'])
