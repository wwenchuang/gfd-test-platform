"""Normal opt-in runtime: injected model jobs only, no provider/device calls."""
import copy
import importlib.util
import inspect
import io
import json
from pathlib import Path

import pytest

from task_server.services import ui_video_analysis as analysis, ui_video_business_inputs as inputs
from task_server.services import ui_video_outcomes as outcomes, ui_video_source_service as source
from task_server.services import ui_case_draft_execution as execution
from task_server.services.ui_video_execution_contract import compiler_options
from task_server.services.ui_case_draft_contract import build_draft_yaml
from test_ui_video_executor_contract import source_document, APP


def runtime():
    assert importlib.util.find_spec('task_server.services.ui_video_runtime'), 'normal runtime is missing'
    from task_server.services import ui_video_runtime
    return ui_video_runtime


def frames(n=299):
    return [{'frame_id':f'frame-{i:03d}', 'timestamp_ms':i*100} for i in range(n)]


def decoder_contract_frames(n):
    # FFmpeg frame-%03d sequence plus the separate unchanged duration-80 anchor.
    samples=[{'frame_id':f'frame-{i+1:03d}', 'timestamp_ms':i*100} for i in range(n-1)]
    return samples+[{'frame_id':'frame-end', 'timestamp_ms':n*100-80}]


def inventory(window):
    return dict(packet_id=window['packet_id'], frames=[dict(frame_id=fid, role='owned', transient_marks=[],
        persistent_ui=['完整原文字'], field_state='', keyboard_state='未知', changes='与前张相同',
        context_refs=[{'frame_id':window['frames'][0]['frame_id'], 'role':'owned' if window['frames'][0]['frame_id'] in window['owned_frame_ids'] else 'context'}],
        uncertainties=['原行问题']) for fid in window['owned_frame_ids']], uncertainties=['窗口问题'])


def fake_receipted_job(records,stage,index):
    """Bounded fake transport admission; the loopback test covers actual IPC."""
    from task_server.services.ui_video_stage_policy import STAGES
    proof=records.append('proof',{'stage':stage,'skill':STAGES[stage][0],'skill_version':STAGES[stage][1],
        'correlation':f'{index:032x}','gateway_request_sha256':'a'*64,'timeout_ms':120000})
    receipt=records.append('receipt',{'content':'{}','gateway_request_sha256':'a'*64})
    return {'index':index,'reserved':True,'proof_persisted':True,'dispatch_intent':True,'raw_received':True,
            'receipt_persisted':True,'admitted':True,'terminal':True,'cleanup':True,
            'events':['reserved','proof_persisted','dispatch_intent','raw_received','receipt_persisted','admitted','cleanup'],
            'transport_status':'receipt_received','proof_ref':proof,'receipt_ref':receipt}


def test_original_bank_299_windows_complete_and_temporal_neighbors():
    m=runtime(); original=frames(); before=copy.deepcopy(original)
    windows=m.producer_windows(original, original)
    assert len(windows)==38
    assert [fid for w in windows for fid in w['owned_frame_ids']]==[f['frame_id'] for f in original]
    for i,w in enumerate(windows):
        assert w['frames']==original[max(0,i*8-2):min(299,i*8+len(w['owned_frame_ids'])+2)]
        assert len(w['frames'])<=12 and len(w['owned_frame_ids'])<=8
    assert original==before


@pytest.mark.parametrize('n', [9, 299, 300])
def test_decoder_final_frame_bank_windows_and_projection_preserve_exact_source(n):
    original = decoder_contract_frames(n)
    before = copy.deepcopy(original)
    bank = runtime().PerceptionBank(original)
    assert bank.original_count == (len(original) + 7) // 8
    assert [fid for w in bank.windows for fid in w['owned_frame_ids']] == [f['frame_id'] for f in original]
    for w in bank.windows:
        assert len(w['frames']) <= 12 and len(w['owned_frame_ids']) <= 8
        raw = inventory(w)
        if 'frame-end' in [f['frame_id'] for f in w['frames']]:
            role = 'owned' if 'frame-end' in w['owned_frame_ids'] else 'context'
            for row in raw['frames']:row['context_refs'].append({'frame_id':'frame-end', 'role':role})
        bank.admit(w, raw, {'proof_ref':{'entry':1,'sha256':'a'*64}, 'receipt_ref':{'entry':2,'sha256':'b'*64}})
    for role in ['owned', 'context']:
        packet = {'frames':original[-2:],
                  'owned_frame_ids':['frame-end'] if role == 'owned' else [original[-2]['frame_id']],
                  'context_frame_ids':[original[-2]['frame_id']] if role == 'owned' else ['frame-end']}
        projection = bank.project(packet)
        assert projection['packet_roles']['frame-end'] == role
        window, row = next((w, r) for w in projection['windows'] for r in w['frames'] if r['frame_id'] == 'frame-end')
        assert row['role'] == 'owned' and row['uncertainties'] == ['原行问题']
        assert row['changes'] == '与前张相同' and row['persistent_ui'] == ['完整原文字']
        assert {'frame_id':'frame-end', 'role':'owned'} in row['evidence_refs']
        assert window['uncertainties'] == ['窗口问题'] and window['binding_audit']['warnings'] == []
        assert window['source_index']['frames'][-1] == {**original[-1], 'role':'owned'}
        assert window['proof_ref'] == {'entry':1,'sha256':'a'*64}
        assert window['receipt_ref'] == {'entry':2,'sha256':'b'*64}
    assert original == before


def test_projection_preserves_neighbor_scope_roles_questions_and_predecessors():
    m=runtime(); original=frames(20); bank=m.PerceptionBank(original)
    windows=bank.windows
    for w in windows:bank.admit(w, inventory(w), {'proof_ref':{'entry':1,'sha256':'a'*64}, 'receipt_ref':{'entry':2,'sha256':'b'*64}})
    packet={'frames':original[7:10], 'owned_frame_ids':['frame-008'], 'context_frame_ids':['frame-007','frame-009']}
    projection=bank.project(packet)
    assert len(projection['windows'])==2
    assert projection['packet_roles']=={'frame-007':'context','frame-008':'owned','frame-009':'context'}
    for w in projection['windows']:
        assert w['uncertainties']==['窗口问题'] and w['source_index']['frames']
        assert w['proof_ref']['entry']==1 and w['receipt_ref']['entry']==2
        assert all(row['role']=='owned' and row['changes']=='与前张相同' and row['uncertainties']==['原行问题'] for row in w['frames'])
    assert projection['windows'][1]['predecessor_frame_id']=='frame-005'
    assert bank.project({'frames':original[16:], 'owned_frame_ids':[f['frame_id'] for f in original[16:]],'context_frame_ids':[]})['windows'][0]['window_id']==windows[2]['packet_id']


def test_overlay_only_owns_new_frames_and_original_partitions_stay_frozen():
    m=runtime(); original=frames(20); bank=m.PerceptionBank(original); frozen=copy.deepcopy(bank.windows)
    extra=[{'frame_id':'frame-020','timestamp_ms':850},{'frame_id':'frame-021','timestamp_ms':950}]
    windows=bank.add_frames(extra)
    assert bank.windows==frozen and bank.original_count==3
    assert [fid for w in windows for fid in w['owned_frame_ids']]==['frame-020','frame-021']
    assert bank.frames==sorted(original+extra,key=lambda f:f['timestamp_ms'])
    assert [f['frame_id'] for f in windows[0]['frames']]==['frame-007','frame-008','frame-020','frame-021','frame-010','frame-011']


def test_inventory_source_role_projection_is_admitted_with_warning():
    bank=runtime().PerceptionBank(frames(4)); raw=inventory(bank.windows[0]);raw['frames'][0]['role']='context'
    bank.admit(bank.windows[0],raw,{})
    assert raw['frames'][0]['role']=='context'
    assert bank.entries[0]['frames'][0]['role']=='owned'
    assert bank.entries[0]['binding_audit']['warnings']==[{'path':'frames[0].role',
        'original':'context','canonical':'owned','reason':'source_partition_role'}]
    assert bank.entries[0]['binding_audit']['qualified'] is False


def test_shared_cap_lowers_once_before_semantics_never_refunds():
    now=[0.0];b=runtime().RuntimeBudget(38,clock=lambda:now[0])
    for _ in range(39):b.reserve()
    b.freeze_packets(1)
    assert b.limit==52 and b.reserved==39
    b.freeze_packets(30);assert b.limit==52
    for _ in range(13):b.reserve()
    with pytest.raises(ValueError,match='video_vision_limit'):b.reserve()
    assert b.reserved==52
    now[0]=1800
    with pytest.raises(TimeoutError):b.check()


def test_engine_time_is_shared_with_outer_and_not_reset_by_overlay():
    now=[1.0];b=runtime().RuntimeBudget(1,clock=lambda:now[0]);now[0]=501;b.start_engine()
    assert b.engine_deadline()==1701
    now[0]=1701
    with pytest.raises(TimeoutError):b.check()
    b.end_engine();b.check();now[0]=1801
    with pytest.raises(TimeoutError):b.check()


def test_tail_accepts_local_keyword_only_runner():
    for fun in (analysis.consolidate_observed_frames,outcomes.repair_outcomes,inputs.recover_business_inputs):
        parameter=inspect.signature(fun).parameters.get('skill_runner')
        assert parameter is not None and parameter.kind==inspect.Parameter.KEYWORD_ONLY


def test_trial_source_derives_private_keyboard_option_and_rejects_tamper():
    doc=source_document();doc['events']=doc['events'][:1];doc['events'][0]['uncertainties']=[];doc['uncertainties']=[]
    proposal,snapshot=analysis.draft_proposal(doc)
    for step in proposal['steps']:step['review_state']='confirmed'
    draft={**proposal,'app_id':APP,'source_snapshot':snapshot}
    expected=build_draft_yaml(proposal,APP,**compiler_options(snapshot,APP,proposal['steps']))
    assert execution._source(draft)['yaml_content']==expected['yaml_content']
    draft['source_snapshot']['app_id']='other'
    with pytest.raises(ValueError,match='video_execution_contract_invalid'):execution._source(draft)


def test_owner_and_app_cas_rejects_without_mutating_source(tmp_path):
    store=source.VideoStore(tmp_path/'videos');doc=store.upload({'user_id':'u','username':'u'},APP,'a.mp4',io.BytesIO(b'a'),1,actor={})
    before=store.get(doc['source_id'],'u',APP)
    assert store.transition(doc['source_id'],{'queued'},'decoding',expected_version=1,expected_owner='other',expected_app=APP) is None
    assert store.transition(doc['source_id'],{'queued'},'decoding',expected_version=1,expected_owner='u',expected_app='other') is None
    assert store.get(doc['source_id'],'u',APP)==before


def test_retry_clears_contract_runtime_and_public_projection(tmp_path):
    from task_server.services.ui_video_workspace import PRIVATE_FIELDS
    expected={'execution_contract','video_runtime_selection','video_runtime_private','video_runtime_summary'}
    assert expected <= PRIVATE_FIELDS
    store=source.VideoStore(tmp_path/'videos');doc=store.upload({'user_id':'u','username':'u'},APP,'a.mp4',io.BytesIO(b'a'),1,actor={})
    fields={k:('private' if k!='execution_contract' else 'video-explicit-keyboard-v1') for k in expected}
    doc=store.transition(doc['source_id'],{'queued'},'failed',**fields)
    assert not (expected-{'video_runtime_summary'}) & source.public_source(doc).keys()
    retried=store.retry(doc['source_id'],'u',APP)
    assert not expected & retried.keys()


def test_qa_selection_is_exact_server_env_only(monkeypatch):
    m=runtime();identifier='uvs_'+'a'*32
    monkeypatch.delenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',raising=False)
    assert not m.selected(identifier)
    for value in ('*',identifier+',uvs_'+'b'*32,' '+identifier,'UVS_'+'a'*32):
        monkeypatch.setenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',value);assert not m.selected(identifier)
    monkeypatch.setenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',identifier)
    assert m.selected(identifier) and not m.selected('uvs_'+'b'*32)


def test_qa_env_autoload_exact_key_only(tmp_path,monkeypatch):
    from task_server import config
    monkeypatch.delenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',raising=False)
    path=tmp_path/'env';path.write_text('UI_VIDEO_RUNTIME_QA_SOURCE_ID=uvs_'+'a'*32+'\nUI_OTHER_UNTRUSTED=x\n');path.chmod(0o600)
    result=config.load_startup_env(str(path))
    assert 'UI_VIDEO_RUNTIME_QA_SOURCE_ID' in result['loaded_keys']
    assert 'UI_OTHER_UNTRUSTED' not in result['loaded_keys']


def test_runtime_wires_original_engine_tail_sorted_media_once_and_exact_stages(tmp_path,monkeypatch):
    m=runtime();assert hasattr(m,'run_runtime'), 'normal orchestration is missing'
    from task_server.services import ui_video_trajectory as trajectory, ui_video_revisit as revisit
    from task_server.services import ui_video_model_jobs as jobs_module, ui_video_windows as windows
    calls=[];published=[];tails=[];original=frames(10)
    doc={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'',
         'baseline_examples':[{'title':'real','yaml_content':'trusted'}], 'media':{'frames':original,'duration_ms':1000}}
    class Jobs:
        def __init__(self,records,**kw):self.accounting=[];self.kw=kw;self.records=records
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def run(self,skill,payload,**kw):
            self.kw['check']();self.kw['reserve']();self.accounting.append(fake_receipted_job(self.records,kw['video_stage'],len(self.accounting)));calls.append((skill,copy.deepcopy(payload),copy.deepcopy(kw)))
            return inventory(payload) if kw['video_stage']=='perception' else {}
        def map(self,builders):
            for i,build in enumerate(builders):
                skill,payload,kw=build();yield i,self.run(skill,payload,**kw)
    monkeypatch.setattr(jobs_module,'ModelJobs',Jobs)
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id']} for f in fs])
    extra=[{'frame_id':'frame-010','timestamp_ms':450}]
    monkeypatch.setattr(revisit,'make_revisit_loader',lambda *a:lambda *a,**k:extra)
    def engine(document,root,cancelled,progress,**kw):
        assert kw['execution_contract']=='video-explicit-keyboard-v1'
        run=kw['skill_runner'];base={'phase':'whole-trajectory','frames':original,'image_frames':original,'reasoning_mode':'bounded'}
        run('ui_video_trajectory',base,version='overview',image_assets=[1],max_tokens=10000,timeout=120,retry_count=0,repair_invalid_json=False)
        progress('observing',0,1,[])
        packet={**base,'packet_id':'packet-001','owned_frame_ids':[f['frame_id'] for f in original], 'context_frame_ids':[], 'prior_events':[]}
        run('ui_video_trajectory',packet,version='observe',image_assets=[1],max_tokens=10000,timeout=120,retry_count=0,repair_invalid_json=False)
        added=kw['revisit_bounded']({'first_frame':'frame-004','last_frame':'frame-005','question':'原问题'},2,owned_time_range_ms=(0,1001))
        augmented=sorted(original+added,key=lambda f:f['timestamp_ms'])
        run('ui_video_trajectory',{**packet,'frames':augmented,'image_frames':augmented,'trajectory':[]},version='audit',image_assets=[1],max_tokens=10000,timeout=120,retry_count=0,repair_invalid_json=False)
        progress('planning',1,1,[{'frame_refs':['frame-010']}])
        return {'media':{**doc['media'],'frames':augmented},'events':[], 'uncertainties':['原问题'],'multiple_scenarios':False,
                'execution_contract':kw['execution_contract'],'trajectory_audit':{'version':'trajectory-v2','execution_contract':kw['execution_contract']}}
    monkeypatch.setattr(trajectory,'observe_trajectory',engine)
    def conso(document,root,cancelled,observed,first_pass,secrets,progress,*,skill_runner):
        tails.append('conso');assert first_pass==observed['events'];assert document['media']['frames'][5]==extra[0]
        assert document['baseline_examples']==doc['baseline_examples'] and document['trajectory_audit']==observed['trajectory_audit']
        skill_runner('ui_video_steps',{'phase':'whole-consolidate'},version='consolidate',max_tokens=10000)
        skill_runner('ui_video_observe',{'phase':'whole-observe'},version='outcome',max_tokens=4000)
        return {'events':[],'uncertainties':['原问题'],'multiple_scenarios':False}
    monkeypatch.setattr(analysis,'consolidate_observed_frames',conso)
    def recover(result,document,root,cancelled,*,skill_runner):
        tails.append('inputs');assert result['media']==document['media'];assert result['execution_contract']
        skill_runner('ui_video_observe',{'phase':'whole-observe'},version='inputs',max_tokens=3000)
        return {**result,'input_recovery':[]}
    monkeypatch.setattr(inputs,'recover_business_inputs',recover)
    result=m.run_runtime(doc,tmp_path,tmp_path/'source.mp4',lambda:None,lambda **kw:published.append(copy.deepcopy(kw)))
    assert tails==['conso','inputs'] and result['uncertainties']==['原问题']
    assert result['media']['frames'][5]==extra[0]
    from task_server.services.ui_video_stage_policy import STAGES
    assert all(kw.get('model_config')=={'providerId':'qwen_plus','model':STAGES[kw['video_stage']][3]} for _,p,kw in calls)
    stages=[kw['video_stage'] for _,p,kw in calls]
    assert stages==['perception','perception','overview','semantic','perception','audit','consolidate','outcome','inputs']
    semantic=next((p,k) for _,p,k in calls if k['video_stage']=='semantic')
    assert 'image_frames' not in semantic[0] and semantic[1]['image_assets']==[] and semantic[1]['output_schema']
    assert semantic[0]['perception_bank']['windows'] and 'baseline_examples' not in semantic[0]
    audit=next(p for _,p,k in calls if k['video_stage']=='audit')
    assert audit['perception_mode']=='fine-detail' and 1<=len(audit['perception_frame_ids'])<=12
    for publication in published:
        if publication.get('raw_events'):assert extra[0] in publication['media']['frames']
    assert result['video_runtime_summary']['reserved']==9 and result['video_runtime_summary']['limit']==16


def test_worker_enrolls_only_selected_source_and_publishes_version_workspace(tmp_path,monkeypatch):
    m=runtime();from task_server.workers import ui_video_worker as worker
    from task_server.services import ui_case_draft_service as drafts
    store=source.VideoStore(tmp_path/'videos');profile={'kind':'user','user_id':'u','username':'u','display_name':'U'}
    doc=store.upload(profile,APP,'a.mp4',io.BytesIO(b'a'),1,actor=profile);identifier=doc['source_id']
    monkeypatch.setenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',identifier)
    monkeypatch.setattr(worker,'require_ui_actor',lambda *a,**k:profile)
    monkeypatch.setattr(drafts,'_application',lambda *a:None)
    baselines=[];monkeypatch.setattr(drafts,'_top3_examples',lambda *a:baselines.append(a) or [])
    def decode(path,dest,cancelled):
        latest=store.get(identifier,'u',APP)
        assert latest['analysis_workspace_version']==1 and latest['video_runtime_selection']['source_id']==identifier
        assert dest==store.root/identifier/'analysis-v1'/'frames' and not cancelled()
        return {'frames':frames(4),'duration_ms':500,'black':False,'static':False}
    monkeypatch.setattr(worker,'decode_video',decode)
    def run(document,workspace,path,check,publish,**kw):
        check();assert workspace==store.root/identifier/'analysis-v1'
        publish(media=document['media'],analysis_progress={'phase':'planning','completed':1,'total':1},raw_events=[])
        return {'events':[],'multiple_scenarios':False,'uncertainties':[],'media':document['media']}
    monkeypatch.setattr(m,'run_runtime',run,raising=False)
    monkeypatch.setattr(analysis,'analyze_whole_frames',lambda *a:pytest.fail('legacy selected'))
    monkeypatch.setattr(inputs,'recover_business_inputs',lambda *a:pytest.fail('duplicate input recovery'))
    worker.analyze_video_source(store,identifier)
    assert store.get(identifier,'u',APP)['status']=='review_needed' and len(baselines)==1


@pytest.fixture
def worker_env(tmp_path,monkeypatch):
    from task_server.workers import ui_video_worker as worker
    from task_server.services import ui_case_draft_service as drafts
    from task_server import identity
    actor={'kind':'user','user_id':'u','username':'u','display_name':'U'}
    profile={**actor,'status':'active','permissions':['ui.edit','ui.view'],'scope':{'ui_apps':[APP]}}
    store=source.VideoStore(tmp_path/'videos');doc=store.upload(profile,APP,'a.mp4',io.BytesIO(b'a'),1,actor=actor)
    monkeypatch.setenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID',doc['source_id'])
    monkeypatch.setattr(identity,'get_access_profile',lambda _:copy.deepcopy(profile))
    monkeypatch.setattr(drafts,'_application',lambda *a:None)
    monkeypatch.setattr(drafts,'_top3_examples',lambda *a:[])
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'frames':frames(4),'duration_ms':500,'black':False,'static':False})
    return worker,store,doc,profile


@pytest.mark.parametrize('boundary',['dispatch','progress','tail','final'])
@pytest.mark.parametrize('drift',['owner','app','version','cancel','permission','app_disabled'])
def test_current_binding_and_auth_at_runtime_boundaries(worker_env,monkeypatch,boundary,drift):
    worker,store,doc,profile=worker_env;m=runtime();identifier=doc['source_id'];sentinel=[]
    def mutate():
        if drift=='permission':profile['permissions']=[]
        elif drift=='app_disabled':
            from task_server.services import ui_case_draft_service as drafts
            def disabled(*a):raise ValueError('draft_app_unavailable')
            # Worker captures this function at entry; mutate through shared flag below.
            disabled_app[0]=True
        else:
            fields={'owner':{'owner_user_id':'foreign'},'app':{'app_id':'foreign'},'version':{'analysis_version':2},'cancel':{}}[drift]
            store.transition(identifier,{'analyzing'},'cancelled' if drift=='cancel' else 'analyzing',**fields)
    disabled_app=[False]
    from task_server.services import ui_case_draft_service as drafts
    def application(*a):
        if disabled_app[0]:raise ValueError('draft_app_unavailable')
    monkeypatch.setattr(drafts,'_application',application)
    def run(document,root,path,check,publish,**kw):
        if boundary=='dispatch':mutate();check();sentinel.append('dispatched')
        elif boundary=='progress':mutate();publish(raw_events=[{'private':'new'}]);sentinel.append('published')
        elif boundary=='tail':mutate();check();sentinel.append('tail')
        else:mutate()
        return {'events':[],'multiple_scenarios':False,'uncertainties':[]}
    monkeypatch.setattr(m,'run_runtime',run)
    worker.analyze_video_source(store,identifier)
    with store.db() as db:latest=json.loads(db.execute('SELECT document FROM sources WHERE id=?',(identifier,)).fetchone()[0])
    assert not sentinel and not latest.get('raw_events')
    if drift in {'owner','app','version'}:assert latest['status']=='analyzing' and 'error_code' not in latest
    elif drift=='cancel':assert latest['status']=='cancelled' and latest.get('error_code','')==''
    else:assert latest['status']=='failed' and latest['error_code']=='video_access_changed'


def test_tail_failure_retains_augmented_media_and_current_frame_directory(worker_env,monkeypatch):
    worker,store,doc,_=worker_env;m=runtime();extra={'frame_id':'frame-004','timestamp_ms':150}
    def run(document,root,path,check,publish,**kw):
        (root/'frames'/'frame-004.jpg').write_bytes(b'original')
        publish(media={**document['media'],'frames':sorted(document['media']['frames']+[extra],key=lambda f:f['timestamp_ms'])},raw_events=[{'frame_refs':['frame-004']}])
        raise ValueError('video_expected_missing')
    monkeypatch.setattr(m,'run_runtime',run)
    worker.analyze_video_source(store,doc['source_id'])
    latest=store.get(doc['source_id'],'u',APP)
    from task_server.services.ui_video_workspace import resolve_frame_directory
    assert latest['status']=='failed' and extra in latest['media']['frames']
    assert (resolve_frame_directory(store.root,latest)/'frame-004.jpg').read_bytes()==b'original'


def test_failed_progress_cas_stops_before_more_work(worker_env,monkeypatch):
    worker,store,doc,_=worker_env;m=runtime();actual=store.transition;continued=[]
    def transition(*args,**kw):
        if 'analysis_progress' in kw:return None
        return actual(*args,**kw)
    monkeypatch.setattr(store,'transition',transition)
    def run(document,root,path,check,publish,**kw):
        publish(analysis_progress={'phase':'observing','completed':0,'total':1});continued.append(True)
    monkeypatch.setattr(m,'run_runtime',run)
    worker.analyze_video_source(store,doc['source_id'])
    assert not continued and store.get(doc['source_id'],'u',APP)['status']=='cancelled'


def test_unselected_legacy_calls_are_unchanged_despite_public_flags(worker_env,monkeypatch):
    worker,store,doc,_=worker_env;monkeypatch.delenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID');calls=[]
    store.transition(doc['source_id'],{'queued'},'queued',video_runtime_selection={'mode':'video-perception-bank-v1'},goal='video-perception-bank-v1')
    def analyze(document,root,cancelled,progress):
        calls.append(('analyze',root));return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    def recover(result,document,root,cancelled):calls.append(('inputs',root));return result
    monkeypatch.setattr(analysis,'analyze_whole_frames',analyze);monkeypatch.setattr(inputs,'recover_business_inputs',recover)
    monkeypatch.setattr(runtime(),'run_runtime',lambda *a,**kw:pytest.fail('untrusted enrollment'))
    worker.analyze_video_source(store,doc['source_id'])
    assert calls==[('analyze',store.root/doc['source_id']),('inputs',store.root/doc['source_id'])]
    assert 'analysis_workspace_version' not in store.get(doc['source_id'],'u',APP)


def test_summary_public_but_records_identity_and_contract_private():
    summary={'mode':'video-perception-bank-v1','original_windows':38,'reserved':61,'limit':70,'receipts':60}
    public=source.public_source({'video_runtime_summary':summary,'video_runtime_private':{'secret':'raw'},'video_runtime_selection':{'owner':'u'},'execution_contract':'private'})
    assert public=={'video_runtime_summary':summary}


def test_original_tail_local_runner_covers_outcome_and_safe_input(monkeypatch,tmp_path):
    from test_ui_video_outcomes import fixture as outcome_fixture, result as outcome_result
    from test_ui_video_business_inputs import fixture as input_fixture, model as input_result
    from task_server.services import ai_skill_service, ui_video_windows
    monkeypatch.setattr(ai_skill_service,'run_ai_skill',lambda *a,**kw:pytest.fail('global escape'))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    calls=[]
    def run(skill,payload,**kw):
        calls.append((skill,payload,kw));return outcome_result() if kw['version']=='outcome' else input_result()
    fs,candidates,plan=outcome_fixture(tmp_path)
    outcomes.repair_outcomes(plan,candidates,fs,800,tmp_path,[0],lambda:False,[],skill_runner=run)
    doc,result=input_fixture()
    recovered=inputs.recover_business_inputs(result,doc,tmp_path,lambda:False,skill_runner=run)
    assert recovered['events'][0]['value']=='自定义文字'
    assert [(skill,k['version'],k['max_tokens']) for skill,p,k in calls]==[('ui_video_observe','outcome',4000),('ui_video_observe','inputs',3000)]
    assert all(k['retry_count']==0 and k['timeout']==120 and k['repair_invalid_json'] is False for _,p,k in calls)


@pytest.mark.parametrize('n,corrections,with_end',[(10,0,False),(299,0,False),(299,2,False),(299,0,True)])
def test_actual_engine_consolidation_input_and_draft_with_injected_jobs(tmp_path,monkeypatch,n,corrections,with_end):
    m=runtime()
    from task_server.services import ui_video_model_jobs as jobs_module, ui_video_windows, ui_video_revisit
    from task_server.services import ai_skill_service as ai
    from test_ui_video_trajectory import event
    fs=frames(n);calls=[];publications=[]
    if with_end:fs=decoder_contract_frames(n)
    original=copy.deepcopy(fs)
    doc={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'',
         'baseline_examples':[{'title':'真实成功基线','yaml_content':'tasks: []'}], 'media':{'frames':fs,'duration_ms':n*100}}
    class Jobs:
        def __init__(self,records,**kw):self.accounting=[];self.kw=kw;self.records=records
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def run(self,skill,payload,**kw):
            self.kw['check']();self.kw['reserve']();self.accounting.append(fake_receipted_job(self.records,kw['video_stage'],len(self.accounting)));calls.append((skill,copy.deepcopy(payload),kw))
            stage=kw['video_stage']
            if stage=='perception':return inventory(payload)
            raw={'states':[],'events':[],'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
            if stage=='overview':return raw
            if stage=='semantic':
                if sum(k['video_stage']=='semantic' for s,p,k in calls)<=corrections:
                    raw['checks']=[{}];return raw
                if payload['packet_id']=='packet-001':raw['events']=[event(['frame-001','frame-002'],'aiInput')]
                return raw
            if stage=='audit':return {'checks':[{'event_id':c['track']['event_id'],'trigger_verdict':'supported','outcome_verdict':'supported','question':''} for c in payload['trajectory']], 'issues':[],'multiple_scenarios':False,'revisit':[]}
            if stage=='consolidate':
                assert payload['baseline_examples']==doc['baseline_examples']
                return {'title':'输入备注','events':[{'candidate_index':0,'action':'aiInput','target':'备注输入框','value':'','observation':'目标页面标题可见','uncertainties':[]}],'multiple_scenarios':False,'uncertainties':[]}
            if stage=='inputs':return {'events':[{'action':'aiInput','target':'candidate:0','value':'测试备注','frame_refs':payload['checks'][0]['final_input_frame_ids'][-1:],'observation':'普通业务输入','uncertainties':[]}],'uncertainties':[],'multiple_scenarios':False}
            pytest.fail(stage)
        def map(self,builders):
            for i,build in enumerate(builders):
                skill,payload,kw=build();yield i,self.run(skill,payload,**kw)
    monkeypatch.setattr(jobs_module,'ModelJobs',Jobs)
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:pytest.fail('global escape'))
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(ui_video_revisit,'make_revisit_loader',lambda *a:lambda *a,**kw:[])
    result=m.run_runtime(doc,tmp_path,tmp_path/'source.mp4',lambda:None,lambda **kw:publications.append(kw))
    assert result['events'][0]['value']=='测试备注'
    assert result['input_recovery'][0]['status']=='recovered'
    assert len([1 for s,p,k in calls if k['video_stage']=='perception'])==(n+7)//8
    assert len([1 for s,p,k in calls if k['video_stage']=='inputs'])==1
    assert len(result['trajectory_audit']['calls'])==1+2*((n+31)//32)+corrections
    proposal,snapshot=analysis.draft_proposal({**doc,**result})
    assert snapshot['execution_contract']=='video-explicit-keyboard-v1' and all(s['review_state']=='needs_review' for s in proposal['steps'])
    assert snapshot['frames']==original
    assert all('baseline_examples' not in p for s,p,k in calls if k['video_stage']!='consolidate')
    assert result['video_runtime_summary']['reserved']==len(calls)
    assert result['media']['frames']==original and doc['media']['frames']==original
    assert result['video_runtime_summary']['original_windows']==(len(original)+7)//8
    if with_end:
        semantic=next(p for s,p,k in reversed(calls) if k['video_stage']=='semantic')
        assert semantic['perception_bank']['packet_roles']['frame-end']=='owned'
        assert any(row['frame_id']=='frame-end' for w in semantic['perception_bank']['windows'] for row in w['frames'])
        audit=next(p for s,p,k in reversed(calls) if k['video_stage']=='audit')
        assert audit['frames'][-1]==original[-1] and 'frame-end' in audit['owned_frame_ids']
        assert all(len(p.get('perception_frame_ids',[]))<=12 for s,p,k in calls)


from test_ui_case_draft_execution import draft_env
from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment


def test_normal_validate_trial_and_snapshot_keep_same_protected_options(draft_env,monkeypatch):
    from task_server.services.ui_case_draft_store import DraftStore
    from task_server.services import ui_case_draft_service as drafts, sonic_service
    from task_server.services.operation_attribution import actor_context
    from test_ui_execution_service import ACTORS
    _,(runners,sonic)=draft_env
    runners['mac']['capabilities'].update(ui_platforms=['android'],android_baseline=True,ui_actions=['launch','aiInput','aiTap','aiWaitFor','aiAssert'])
    runners['mac']['devices'][0].update(platform='android',installed_apps=[{'package':APP,'installed':True,'version_name':'1','version_code':'2'}])
    monkeypatch.setattr(sonic_service,'sonic_notify_known_apps',lambda:[{'package':APP,'name':'测试应用'}])
    doc=source_document();doc['events']=doc['events'][:1];doc['events'][0]['uncertainties']=[];doc['uncertainties']=[]
    proposal,snapshot=analysis.draft_proposal(doc)
    for step in proposal['steps']:step['review_state']='confirmed'
    store=DraftStore();draft=store.create({'user_id':'u-a','username':'a','display_name':'作者A'},APP,'测试来源',recording_snapshot=snapshot,recording_content=proposal)
    target={'runner_id':'mac','device_id':'iphone','platform':'android'}
    with actor_context(ACTORS['a']):
        gate=drafts.validate_draft(ACTORS['a'],draft['draft_id'],{'revision':draft['revision'],'target':target})
        assert gate['ready'],gate['blockers']
        batch=execution.create_draft_trial(ACTORS['a'],draft['draft_id'],{'revision':draft['revision'],'target':target},'keyboard-consistency')
    attempt=batch['case_attempts'][0]
    assert attempt['compiled_case']==gate['compiled_case']
    assert 'autoDismissKeyboard: false' in batch['snapshots'][0]['source_yaml']
    assert execution.validate_trial_snapshot(batch,attempt)
    changed=copy.deepcopy(batch);changed['snapshots'][0]['source_yaml']=changed['snapshots'][0]['source_yaml'].replace('autoDismissKeyboard: false','autoDismissKeyboard: true')
    assert not execution.validate_trial_snapshot(changed,attempt)


@pytest.mark.parametrize('with_end', [False, True])
def test_normal_runtime_owned_jobs_real_loopback_transport_records_and_cleanup(tmp_path,monkeypatch,with_end):
    from test_ui_video_model_jobs import Gateway
    from test_ui_video_trajectory import event
    from task_server import config
    from task_server.services.ui_video_records import RecordStore
    fs=frames(17);doc={'source_id':'uvs_'+'a'*32,'app_id':APP,'analysis_version':1,'goal':'',
                       'media':{'frames':fs,'duration_ms':1700},'baseline_examples':[]}
    if with_end:fs[:]=decoder_contract_frames(17)
    original=copy.deepcopy(fs)
    (tmp_path/'frames').mkdir();(tmp_path/'source.mp4').write_bytes(b'synthetic test source')
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'test-original')
    def response(body):
        stage=body['videoStage'];p=body['payload']
        if stage=='perception':value=inventory(p)
        elif stage in {'overview','semantic'}:
            value={'states':[],'events':[event(['frame-001','frame-002'],'aiInput')] if stage=='semantic' else [],
                   'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]}
        elif stage=='audit':value={'checks':[{'event_id':c['track']['event_id'],'trigger_verdict':'supported','outcome_verdict':'supported','question':''} for c in p['trajectory']], 'issues':[],'multiple_scenarios':False,'revisit':[]}
        elif stage=='consolidate':value={'title':'输入备注','events':[{'candidate_index':0,'action':'aiInput','target':'备注输入框','value':'','observation':'目标页面标题可见','uncertainties':[]}],'multiple_scenarios':False,'uncertainties':[]}
        else:
            assert stage=='inputs'
            value={'events':[{'action':'aiInput','target':'candidate:0','value':'测试备注','frame_refs':p['checks'][0]['final_input_frame_ids'][-1:],'observation':'普通业务输入','uncertainties':[]}],'uncertainties':[],'multiple_scenarios':False}
        return json.dumps(value,ensure_ascii=False)
    gateway=Gateway(response)
    try:
        monkeypatch.setattr(config,'AI_GATEWAY_URL',gateway.url)
        result=runtime().run_runtime(doc,tmp_path,tmp_path/'source.mp4',lambda:None,lambda **kw:None)
        assert len(gateway.calls)==8 and gateway.peak==2
        summary=result['video_runtime_summary'];assert summary['reserved']==summary['receipts']==8
        private=result['video_runtime_private']
        with RecordStore.open_snapshot(tmp_path/'model-records',private['records']) as records:
            aggregate=records.read(private['accounting_ref'])
            assert len(aggregate['refs'])==8
            rows=[records.read(ref) for ref in aggregate['refs']]
            assert all(row['cleanup'] and row['admitted'] and row['receipt_persisted'] for row in rows)
        assert len(private['windows'])==3 and all('proof_ref' in w and 'receipt_ref' in w for w in private['windows'])
        semantic=next(call for call in gateway.calls if call['videoStage']=='semantic')
        assert semantic['imageAssets']==[] and semantic['outputSchema']
        assert result['media']['frames']==original
        if with_end:
            producers=sorted((call for call in gateway.calls if call['videoStage']=='perception'),key=lambda call:call['payload']['packet_id'])
            assert [fid for call in producers for fid in call['payload']['owned_frame_ids']]==[f['frame_id'] for f in original]
            for role in ['owned','context']:
                call=next(call for call in producers if 'frame-end' in call['payload'][role+'_frame_ids'])
                assert call['payload']['frames'][-1]==original[-1]
                assert call['imageAssets'][-1]=={'name':'frame-end','timestamp_ms':1620,'mime':'image/jpeg','base64':'dGVzdC1vcmlnaW5hbA==','dataUrl':''}
            assert (tmp_path/'frames'/'frame-end.jpg').read_bytes()==b'test-original'
            assert private['windows'][-1]['source_index']['frames'][-1]=={**original[-1],'role':'owned'}
    finally:gateway.close()


def test_projection_order_is_chronological_despite_parallel_completion():
    bank=runtime().PerceptionBank(frames(20))
    for window in reversed(bank.windows):bank.admit(window,inventory(window),{})
    projection=bank.project({'frames':frames(20),'owned_frame_ids':[f['frame_id'] for f in frames(20)],'context_frame_ids':[]})
    assert [window['window_id'] for window in projection['windows']]==[w['packet_id'] for w in bank.windows]


def test_runtime_rejects_perception_without_owned_receipt_provenance(tmp_path,monkeypatch):
    from task_server.services import ui_video_model_jobs as module, ui_video_windows
    class Unproven:
        def __init__(self,*a,**kw):self.accounting=[]
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def map(self,builders):
            for i,build in enumerate(builders):
                _,payload,_=build();yield i,inventory(payload)
    monkeypatch.setattr(module,'ModelJobs',Unproven)
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    doc={'media':{'frames':frames(4),'duration_ms':400}}
    with pytest.raises(ValueError,match='video_perception_receipt_missing'):
        runtime().run_runtime(doc,tmp_path,tmp_path/'missing.mp4',lambda:None,lambda **kw:None)


def test_engine_budget_cannot_restart_after_tail_and_lowering_overdraw_stops():
    m=runtime();budget=m.RuntimeBudget(1);budget.start_engine();budget.end_engine()
    with pytest.raises(ValueError,match='video_runtime_engine_started'):budget.start_engine()
    budget=m.RuntimeBudget(1)
    for _ in range(16):budget.reserve()
    with pytest.raises(ValueError,match='video_vision_limit'):budget.freeze_packets(1)
    with pytest.raises(ValueError,match='video_vision_limit'):budget.reserve()


def test_native_frame_route_reads_augmented_worker_failure_and_rejects_stale_version(worker_env,monkeypatch):
    import re
    from task_server import ui_video_http
    from task_server.services.operation_attribution import actor_context
    worker,store,doc,_=worker_env
    def run(document,root,path,check,publish,**kw):
        (root/'frames'/'frame-004.jpg').write_bytes(b'original revisit')
        publish(media={**document['media'],'frames':sorted(document['media']['frames']+[{'frame_id':'frame-004','timestamp_ms':150}],key=lambda f:f['timestamp_ms'])})
        raise ValueError('video_expected_missing')
    monkeypatch.setattr(runtime(),'run_runtime',run)
    worker.analyze_video_source(store,doc['source_id'])
    monkeypatch.setattr(source,'service_store',lambda:store)
    handlers=[]
    def regex(pattern):
        def bind(f):handlers.append((pattern,f));return f
        return bind
    def unused(*a):return lambda f:f
    ui_video_http.register_routes(unused,unused,regex,unused)
    monkeypatch.setattr(ui_video_http,'_mark',lambda *a:None)
    class Handler:
        def __init__(self):self.wfile=io.BytesIO();self.status=None
        def _json(self,value,status=200):self.status=status
        def send_response(self,status):self.status=status
        def send_header(self,*a):pass
        def end_headers(self):pass
    pattern,get=handlers[0]
    with actor_context(doc['actor']):
        for version,expected in [('1',200),('2',404)]:
            handler=Handler();get(handler,{'app_id':APP,'analysis_version':version},re.match(pattern,'/api/ui-video-sources/'+doc['source_id']+'/frames/frame-004'))
            assert handler.status==expected
            assert handler.wfile.getvalue()==(b'original revisit' if expected==200 else b'')
