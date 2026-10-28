"""Explicit keyboard semantics are server selected and survive private draft edits."""
import copy
import io
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from task_server.services import ui_case_draft_contract as compiler, ui_video_analysis as analysis
from task_server.services import ui_case_draft_service as drafts, ui_video_source_service as video
from task_server.services import ui_video_trajectory as vt, ui_video_windows, ai_skill_service as ai
from task_server.services.operation_attribution import actor_context
from task_server.services.yaml_static_validator import validate_yaml_static_executable
from test_ui_case_draft_service import env, A, APP
from test_ui_video_trajectory import frames, event

CONTRACT = 'video-explicit-keyboard-v1'
SOURCE = 'uvs_' + 'a'*32


def source_document():
    observed = []
    for i, (action, target, value, predicate) in enumerate([
        ('aiInput', '搜索文字输入区域', '测试内容', '输入区域显示测试内容，键盘隐藏'),
        ('aiTap', '键盘收起控件', '', '页面键盘隐藏'),
        ('aiTap', '搜索提交控件', '', '搜索结果列表出现'),
    ]):
        observed.append(dict(action=action, target=target, value=value, start_ms=i*200, end_ms=i*200+100,
                             frame_refs=[f'frame-{i*2+1:03d}', f'frame-{i*2+2:03d}'],
                             observation=predicate, outcome={'observation':predicate,'frame_refs':[f'frame-{i*2+2:03d}']},
                             evidence_kind='inferred', uncertainties=['原始疑问'] if i==0 else []))
    return dict(source_id=SOURCE, app_id=APP, analysis_version=1, goal='', title='录屏输入和独立控制',
                media={'frames':frames(6), 'duration_ms':1000}, events=observed, uncertainties=['源问题'],
                execution_contract=CONTRACT, trajectory_audit={'version':'trajectory-v2','execution_contract':CONTRACT})


def inputs(compiled):
    return [x for task in yaml.safe_load(compiled['yaml_content'])['tasks'] for x in task['flow'] if 'aiInput' in x]


def test_opt_in_compiler_sets_official_boolean_only_on_video_inputs_and_preserves_mapping():
    proposal, snapshot = analysis.draft_proposal(source_document())
    proposal['steps'].append(dict(step_id=str(uuid4()), action='aiInput', target='人工补充的备注框', value='备注',
                                  expected='备注显示', review_state='needs_review', source_ref='text'))
    before=copy.deepcopy(proposal)
    legacy=compiler.build_draft_yaml(proposal, APP)
    new=compiler.build_draft_yaml(proposal, APP, execution_contract=CONTRACT)
    assert inputs(new)[0]['autoDismissKeyboard'] is False
    assert 'autoDismissKeyboard' not in inputs(new)[1]
    parsed=yaml.safe_load(new['yaml_content'])
    del next(x for x in parsed['tasks'][0]['flow'] if 'aiInput' in x)['autoDismissKeyboard']
    assert parsed==yaml.safe_load(legacy['yaml_content'])
    assert new['step_ids']==legacy['step_ids'] and new['compiler_roles']==legacy['compiler_roles']
    assert new['questions']==legacy['questions'] and new['ready']==legacy['ready'] is False
    assert proposal==before and proposal['steps'][0]['expected']=='输入区域显示测试内容，键盘隐藏'
    assert snapshot['events'][0]['uncertainties']==['原始疑问']
    assert validate_yaml_static_executable(new['yaml_content'], strict=True)['ok']
    assert [s['target'] for s in proposal['steps'][:3]]==['搜索文字输入区域','键盘收起控件','搜索提交控件']


@pytest.mark.parametrize('ref',['text','manual_yaml','recording:'+'b'*64+':'+'c'*32])
def test_nonvideo_sources_keep_exact_output_even_with_trusted_option(ref):
    proposal,_=analysis.draft_proposal(source_document())
    for step in proposal['steps']:step['source_ref']=ref
    assert compiler.build_draft_yaml(proposal,APP,execution_contract=CONTRACT)==compiler.build_draft_yaml(proposal,APP)


@pytest.mark.parametrize('mode',['unknown', True, {}, []])
def test_unknown_compiler_mode_rejected(mode):
    proposal,_=analysis.draft_proposal(source_document())
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):
        compiler.build_draft_yaml(proposal,APP,execution_contract=mode)


def test_new_snapshot_import_edit_and_validation_keep_keyboard_contract(env,tmp_path,monkeypatch):
    store=video.VideoStore(tmp_path/'videos');monkeypatch.setattr(video,'service_store',lambda:store)
    uploaded=store.upload(A,APP,'clip.mp4',io.BytesIO(b'mp4'),3,actor=A)
    document=source_document();document.pop('source_id');document.pop('app_id');document.pop('analysis_version')
    store.transition(uploaded['source_id'],{'queued'},'ready',**document)
    with actor_context(A):
        draft=video.import_video_draft(A,uploaded['source_id'],APP)
        snapshot=copy.deepcopy(draft['source_snapshot'])
        assert snapshot['execution_contract']==CONTRACT and inputs(draft)[0]['autoDismissKeyboard'] is False
        body={k:copy.deepcopy(draft[k]) for k in ('title','preconditions','steps','questions')}
        body.update(revision=1,title='人工审核标题')
        edited=drafts.update_draft(A,draft['draft_id'],body)
        assert inputs(edited)[0]['autoDismissKeyboard'] is False
        candidates=[]
        actual_static=drafts.validate_yaml_static_executable
        def capture_static(content,**kw):
            candidates.append(content)
            return actual_static(content,**kw)
        monkeypatch.setattr(drafts,'validate_yaml_static_executable',capture_static)
        gate=drafts.validate_draft(A,draft['draft_id'],{'revision':2,'target':{'runner_id':'missing','device_id':'none','platform':'android'}})
        assert inputs(gate['draft'])[0]['autoDismissKeyboard'] is False
        assert candidates and 'autoDismissKeyboard: false' in candidates[0]
        assert gate['static_validation']['ok'] and gate['ready'] is False
        assert video.import_video_draft(A,uploaded['source_id'],APP)['revision']==2
        assert edited['source_snapshot']==snapshot
        for key in ('execution_contract','source_snapshot'):
            with pytest.raises(ValueError,match='draft_request_invalid'):
                drafts.update_draft(A,draft['draft_id'],{**body,'revision':2,key:CONTRACT})


@pytest.mark.parametrize('change',[{'kind':'text'},{'app_id':'com.other.app'},{'source_id':'bad'},{'analysis_version':True},{'execution_contract':'unknown'}])
def test_protected_snapshot_binding_rejects_wrong_origin(change):
    from task_server.services.ui_video_execution_contract import compiler_options
    proposal,snapshot=analysis.draft_proposal(source_document())
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):
        compiler_options({**snapshot,**change},APP,proposal['steps'])


def test_protected_snapshot_rejects_unbound_video_step_and_old_snapshots_are_legacy():
    from task_server.services.ui_video_execution_contract import compiler_options
    proposal,snapshot=analysis.draft_proposal(source_document())
    bad=copy.deepcopy(proposal['steps']);bad[0]['source_ref']=f'video:{SOURCE}:2:0'
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):compiler_options(snapshot,APP,bad)
    bad=copy.deepcopy(proposal['steps']);bad[0]['step_id']=str(uuid4())
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):compiler_options(snapshot,APP,bad)
    old={k:v for k,v in snapshot.items() if k!='execution_contract'}
    assert compiler_options(old,APP,proposal['steps'])=={}


def test_untrusted_document_metadata_cannot_install_contract():
    document=source_document();document['trajectory_audit']={}
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):analysis.draft_proposal(document)
    document.pop('execution_contract');document['model_metadata']={'execution_contract':CONTRACT}
    proposal,snapshot=analysis.draft_proposal(document)
    assert 'execution_contract' not in snapshot and 'autoDismissKeyboard' not in inputs(compiler.build_draft_yaml(proposal,APP))[0]


def test_installed_legacy_yaml_factory_forwards_boolean_without_cli_or_device():
    path=Path('/Users/adouceshi/Applications/MidsceneIOS/node_modules/@midscene/core/dist/lib/yaml/test-runner-compat.js')
    if not path.exists():pytest.skip('installed SDK offline compatibility fixture unavailable')
    proposal,_=analysis.draft_proposal(source_document())
    item=inputs(compiler.build_draft_yaml(proposal,APP,execution_contract=CONTRACT))[0]
    device=path.parents[2]/'es/device/index.mjs'
    script="""(async()=>{
      const c=require(process.argv[1]);
      const d=await import(process.argv[2]);
      const compiled=c.compileLegacyFlowItem(JSON.parse(process.argv[3]));
      let received;
      const input=d.defineActionInput({typeText:async(value,options)=>{received={value,options}}});
      const parsed=input.paramSchema.parse(compiled.input.params);
      await input.call(parsed);
      const bad=d.actionInputParamSchema.safeParse({...compiled.input.params,autoDismissKeyboard:'false'});
      process.stdout.write(JSON.stringify({compiled,received,rejectsString:!bad.success}));
    })().catch(error=>{console.error(error);process.exit(1)});"""
    result=subprocess.run(['node','-e',script,str(path),str(device),json.dumps(item)],capture_output=True,text=True,check=True)
    output=json.loads(result.stdout);compiled=output['compiled']
    assert compiled['input']['params']['autoDismissKeyboard'] is False and compiled['input']['params']['value']=='测试内容'
    assert output['received']['value']=='测试内容' and output['received']['options']['autoDismissKeyboard'] is False
    assert output['rejectsString'] is True


def test_bound_video_identity_cannot_silently_become_text_provenance():
    from task_server.services.ui_video_execution_contract import compiler_options
    proposal,snapshot=analysis.draft_proposal(source_document())
    proposal['steps'][0]['source_ref']='text'
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):
        compiler_options(snapshot,APP,proposal['steps'])


def test_old_snapshot_and_new_source_never_enroll_from_public_edit_or_model_result(env):
    with actor_context(A):
        for key in ('execution_contract','source_snapshot'):
            with pytest.raises(ValueError,match='draft_request_invalid'):
                drafts.create_draft(A,{'app_id':APP,'text':'输入测试文字',key:CONTRACT})
    with pytest.raises(ValueError,match='draft_model_structure_invalid'):
        drafts._model_proposal({'title':'伪造','preconditions':[],'steps':[],'questions':[],
                                'execution_contract':CONTRACT},APP)
    document=source_document();document.pop('execution_contract');document.pop('trajectory_audit')
    proposal,snapshot=analysis.draft_proposal(document)
    assert 'execution_contract' not in snapshot
    assert compiler.build_draft_yaml(proposal,APP)==compiler.build_draft_yaml(proposal,APP,execution_contract=None)


def test_manual_yaml_rebuild_does_not_inherit_source_video_semantics(env,tmp_path,monkeypatch):
    store=video.VideoStore(tmp_path/'videos');monkeypatch.setattr(video,'service_store',lambda:store)
    uploaded=store.upload(A,APP,'clip.mp4',io.BytesIO(b'mp4'),3,actor=A)
    document=source_document();document.pop('source_id');document.pop('app_id');document.pop('analysis_version')
    store.transition(uploaded['source_id'],{'queued'},'ready',**document)
    proposal,_=analysis.draft_proposal(source_document())
    for step in proposal['steps']:step['source_ref']='manual_yaml'
    manual=compiler.build_draft_yaml(proposal,APP)
    with actor_context(A):
        draft=video.import_video_draft(A,uploaded['source_id'],APP)
        edited=drafts.apply_yaml_candidate(A,draft['draft_id'],{'revision':1,'yaml_content':manual['yaml_content']})
        assert edited['yaml_candidate']['status']=='converted'
        assert all(s['source_ref']=='manual_yaml' for s in edited['steps'])
        assert 'autoDismissKeyboard' not in edited['yaml_content']
        assert edited['source_snapshot']==draft['source_snapshot']
        restored=drafts.restore_draft(A,draft['draft_id'],{'revision':2})
        assert inputs(restored)[0]['autoDismissKeyboard'] is False
