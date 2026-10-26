import copy
import pytest
from task_server.services import ai_skill_service as ai


def fixture():
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(6)]
    event={'action':'aiInput','target':'模型文字输入框','value':'','start_ms':0,'end_ms':200,
           'frame_refs':['f0','f2'],'observation':'文字修改后收起键盘','uncertainties':[
               '请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件']}
    return {'media':{'frames':frames,'duration_ms':600}}, {'events':[event],'uncertainties':[]}


def model(value='自定义文字',refs=None,note='普通业务输入'):
    return {'events':[{'action':'aiInput','target':'candidate:0','value':value,
                      'frame_refs':refs or ['f4'],'observation':note,'uncertainties':[]}],
            'uncertainties':[],'multiple_scenarios':False}


def test_recovers_business_text_from_final_pixels_with_provenance(monkeypatch,tmp_path):
    from task_server.services.ui_video_business_inputs import recover_business_inputs
    from task_server.services import ui_video_windows
    doc,result=fixture();before=copy.deepcopy(result);calls=[]
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    def run(skill,payload,**kwargs):
        calls.append(payload)
        assert kwargs['version']=='inputs'
        assert '自定义文字' not in str(payload)
        return model()
    monkeypatch.setattr(ai,'run_ai_skill',run)
    out=recover_business_inputs(result,doc,tmp_path,lambda:False)
    assert out['events'][0]['value']=='自定义文字'
    assert out['events'][0]['uncertainties']==[]
    assert out['input_recovery']==[{'candidate_index':0,'status':'recovered','frame_refs':['f4']}]
    assert result==before and len(calls)==1


@pytest.mark.parametrize('target',['密码输入框','短信验证码','搜索访问令牌','手机号搜索框','邮箱文本输入框','普通输入框'])
def test_sensitive_or_untyped_fields_never_request_transcription(monkeypatch,tmp_path,target):
    from task_server.services.ui_video_business_inputs import recover_business_inputs
    doc,result=fixture();result['events'][0]['target']=target
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:pytest.fail('must not request protected input'))
    out=recover_business_inputs(result,doc,tmp_path,lambda:False)
    assert out['events'][0]['value']==''
    assert out['input_recovery'][0]['status']=='manual'


@pytest.mark.parametrize('value',['123456','18951399531','a@b.com','sk-private-key','abcdefghijklmnopqrstuvwxyz123456','https://private.example/token'])
def test_credential_and_personal_data_shapes_are_not_saved(monkeypatch,tmp_path,value):
    from task_server.services.ui_video_business_inputs import recover_business_inputs
    from task_server.services import ui_video_windows
    doc,result=fixture();monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:model(value))
    out=recover_business_inputs(result,doc,tmp_path,lambda:False)
    assert out['events'][0]['value']=='' and value not in str(out)


@pytest.mark.parametrize('change',['outside','wrong_action','duplicate','uncertain','sensitive'])
def test_unverified_input_is_never_accepted(monkeypatch,tmp_path,change):
    from task_server.services.ui_video_business_inputs import recover_business_inputs
    from task_server.services import ui_video_windows
    doc,result=fixture();r=model()
    if change=='outside':r['events'][0]['frame_refs']=['not-sent']
    if change=='wrong_action':r['events'][0]['action']='aiTap'
    if change=='duplicate':r['events']*=2
    if change=='uncertain':r['events'][0]['uncertainties']=['看不清']
    if change=='sensitive':r['events'][0]['observation']='敏感输入'
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:r)
    if change in {'outside','wrong_action','duplicate'}:
        with pytest.raises(ValueError):recover_business_inputs(result,doc,tmp_path,lambda:False)
    else:
        assert recover_business_inputs(result,doc,tmp_path,lambda:False)['events'][0]['value']==''


def test_cancelled_recovery_never_calls_model(monkeypatch,tmp_path):
    from task_server.services.ui_video_business_inputs import recover_business_inputs
    doc,result=fixture()
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:pytest.fail('cancelled'))
    with pytest.raises(ValueError,match='video_cancelled'):
        recover_business_inputs(result,doc,tmp_path,lambda:True)


@pytest.mark.parametrize('target',['账户名称输入框','登录名称输入框','联系电话备注','收件人名称','username文本框','Account名称'])
def test_account_context_cannot_be_disguised_as_business_name(monkeypatch,tmp_path,target):
    from task_server.services.ui_video_business_inputs import recover_business_inputs
    doc,result=fixture();result['events'][0]['target']=target
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:pytest.fail('protected field'))
    assert recover_business_inputs(result,doc,tmp_path,lambda:False)['events'][0]['value']==''


@pytest.mark.parametrize('value',['138 1234 5678','138-1234-5678','4111 1111 1111 1111','(+86) 13812345678'])
def test_separated_phone_and_card_numbers_are_not_business_text(value):
    from task_server.services.ui_video_business_inputs import _safe_value
    assert not _safe_value(value)


from test_ui_case_draft_service import env,A,APP


def test_worker_publishes_and_imports_recovered_values_without_manual_edit(env,tmp_path,monkeypatch):
    import io
    from task_server.services import ui_video_source_service as video,ui_video_analysis as analysis,ui_video_windows
    from task_server.workers import ui_video_worker as worker
    from task_server.services.operation_attribution import actor_context
    from task_server.services.yaml_static_validator import validate_yaml_static_executable
    doc,result=fixture()
    for e in result['events']:
        e.update(evidence_kind='inferred',observation='文本输入框显示测试文字',
                 outcome={'observation':'文本输入框显示测试文字','frame_refs':['f4']})
    result['multiple_scenarios']=False
    store=video.VideoStore(tmp_path/'videos')
    source=store.upload(A,APP,'ordinary.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{**doc['media'],'black':False,'static':False})
    monkeypatch.setattr(analysis,'analyze_whole_frames',lambda *a:copy.deepcopy(result))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:model())
    worker.analyze_video_source(store,source['source_id'])
    saved=store.get(source['source_id'],A['user_id'],APP)
    assert saved['status']=='ready' and saved['candidate_steps'][0]['value']=='自定义文字'
    monkeypatch.setattr(video,'service_store',lambda:store)
    with actor_context(A):draft=video.import_video_draft(A,source['source_id'],APP)
    assert draft['steps'][0]['value']=='自定义文字'
    assert draft['source_snapshot']['input_recovery'][0]['status']=='recovered'
    assert validate_yaml_static_executable(draft['yaml_content'],strict=True)['ok']
