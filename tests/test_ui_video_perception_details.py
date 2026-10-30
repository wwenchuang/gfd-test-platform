"""Generic derived thirds; no private source facts or action targets."""
import base64
import copy
import hashlib
import io
import pytest
from PIL import Image
from task_server.services import ui_video_stage_policy as policy
from task_server.services.ui_video_stage_receipts import expected_provider_options, make_expected_attestation, safe_attestation, validate_attestation
from tests.test_ui_video_stage_wire import evidence


def details(p, a, parents=(0, 2)):
    p['detail_frames'] = []
    for index in parents:
        f = p['frames'][index]
        for i in range(3):
            d = dict(frame_id=f['frame_id']+f'-detail-{i}', source_frame_id=f['frame_id'], timestamp_ms=f['timestamp_ms'], source_size=[120, 302], box=[0, 302*i//3, 120, 302*(i+1)//3])
            p['detail_frames'].append(d)
            a.append(dict(name=d['frame_id'], timestamp_ms=d['timestamp_ms'], mime='image/jpeg', base64='YQ=='))
    return p, a


def test_admit_owned_and_context_details_without_mutation():
    p,a=details(*evidence()); before=copy.deepcopy((p,a))
    assert policy.validate_video_stage('ui_video_perception',p,a,'perception')=='perception'
    assert (p,a)==before


@pytest.mark.parametrize('change', ['partial','order','parent','extra','bool','box','size','stamp','name','duplicate','image_order','extra_image','capacity','bytes'])
def test_closed_detail_contract(change):
    p,a=details(*evidence());d=p['detail_frames'][0]
    if change=='partial':p['detail_frames'].pop();a.pop()
    if change=='order':p['detail_frames'].reverse();a[3:]=reversed(a[3:])
    if change=='parent':d['source_frame_id']='frame-999'
    if change=='extra':d['target']='private'
    if change=='bool':d['box'][0]=False
    if change=='box':d['box'][3]+=1
    if change=='size':d['source_size'][0]=99
    if change=='stamp':d['timestamp_ms']+=1
    if change=='name':d['frame_id']='frame-000-detail'
    if change=='duplicate':p['detail_frames'][1]=copy.deepcopy(d)
    if change=='image_order':a[-1],a[-2]=a[-2],a[-1]
    if change=='extra_image':a.append(copy.deepcopy(a[-1]))
    if change=='capacity':p,a=details(*evidence(7),parents=(0,6))
    if change=='bytes':a[-1]['base64']='YQ=='*(10*1024*1024//4)
    with pytest.raises(ValueError,match='video_'):policy.validate_video_stage('ui_video_perception',p,a,'perception')


def test_old_absent_empty_wire_parity_and_attestation():
    p,a=evidence(); body=dict(videoStage='perception',skillName='ui_video_perception',skillVersion='v1',videoCorrelation='a'*32,payload=p,imageAssets=a,prompt='pixels')
    before=expected_provider_options(body);p['detail_frames']=[]
    policy.validate_video_stage('ui_video_perception',p,a,'perception')
    assert expected_provider_options(body)==before
    details(p,a); proof=make_expected_attestation(body)
    assert safe_attestation(proof)==proof
    validate_attestation(proof,body)
    label=expected_provider_options(body)['messages'][1]['content'][6]['text']
    assert label=='frame-000-detail-0 0ms [source_frame_id=frame-000 box=[0,0,120,100] role=owned; same-frame visual assistance; no separate time/event/action or ownership]'
    proof['effective_options']['fine_frame_ids'][-1]='frame-999-detail-2'
    with pytest.raises(RuntimeError):validate_attestation(proof,body)


def test_crop_real_pixels_manifest_capacity_cancellation(tmp_path):
    from task_server.services.ui_video_perception_details import build_detail_views
    p,a=evidence(); originals={}
    for f in p['frames']:
        im=Image.new('RGB',(120,302)); im.paste('red',(0,0,120,100));im.paste('green',(0,100,120,201));im.paste('blue',(0,201,120,302))
        out=io.BytesIO();im.save(out,format='JPEG',quality=95);raw=out.getvalue();originals[f['frame_id']]=raw
    for asset in a:asset['base64']=base64.b64encode(originals[asset['name']]).decode()
    before=copy.deepcopy((p,a,originals))
    ds,views,manifest=build_detail_views(p['frames'],a,['frame-002','frame-000'],lambda:False)
    assert [d['frame_id'] for d in ds]==[f'frame-{j:03d}-detail-{i}' for j in (0,2) for i in range(3)]
    assert (p,a,originals)==before
    for d,v,m in zip(ds,views,manifest):
        raw=base64.b64decode(v['base64']);im=Image.open(io.BytesIO(raw))
        assert im.size==(120,d['box'][3]-d['box'][1])
        channel=(int(d['frame_id'][-1]))
        pixel=im.getpixel((60,50));assert pixel[channel]>100 and pixel[channel]>max(pixel[:channel]+pixel[channel+1:])+60
        assert m['source_sha256']==hashlib.sha256(originals[d['source_frame_id']]).hexdigest()
        assert m['view_sha256']==hashlib.sha256(raw).hexdigest() and m['metadata']==d
    with pytest.raises(ValueError,match='video_cancelled'):build_detail_views(p['frames'],a,['frame-000'],lambda:True)
    for selected in (['frame-000']*2,['frame-999'],[f['frame_id'] for f in p['frames']]):
        with pytest.raises(ValueError):build_detail_views(p['frames'],a,selected,lambda:False)
    p7,a7=evidence(7)
    with pytest.raises(ValueError,match='video_vision_limit'):build_detail_views(p7['frames'],a7,['frame-000','frame-001'],lambda:False)


@pytest.mark.parametrize('n,parents', [(6,(0,5)),(9,(8,))])
def test_exact_twelve_images_retained(n,parents):
    p,a=details(*evidence(n),parents=parents)
    assert len(a)==12
    assert policy.validate_video_stage('ui_video_perception',p,a,'perception')=='perception'


def test_complete_gateway_transport_and_original_only_inventory(monkeypatch):
    from task_server.services import ai_skill_service as ai
    from tests.test_ui_video_stage_wire import capture
    from task_server.services.ui_video_perception_binding import bind_inventory
    p,a=details(*evidence()); calls=capture(monkeypatch)
    ai.ai_gateway_skill_content('ui_video_perception','pixels',payload=p,image_assets=a,video_stage='perception')
    assert [{k:asset[k] for k in a[0]} for asset in calls[0]['imageAssets']]==a and calls[0]['payload']==p
    raw={'packet_id':p['packet_id'],'frames':[dict(frame_id=ref,role='owned',transient_marks=[],persistent_ui=['unknown'],field_state='unknown',keyboard_state='unknown',changes='unknown',context_refs=[],uncertainties=['unresolved']) for ref in p['owned_frame_ids']], 'uncertainties':['root unresolved']}
    before=copy.deepcopy((raw,p))
    bind_inventory(raw,p)
    assert (raw,p)==before
    for ref in [p['detail_frames'][0]['frame_id'],p['context_frame_ids'][0]]:
        wrong=copy.deepcopy(raw);wrong['frames'][0]['frame_id']=ref
        with pytest.raises(ValueError):bind_inventory(wrong,p)


@pytest.mark.parametrize('stage', ['semantic','overview','audit','grounding','consolidate','outcome','inputs'])
def test_other_explicit_stages_still_forbid_derived_details(stage):
    p,a=details(*evidence());skill,version,phase,_,_=policy.STAGES[stage];p.update(phase=phase,reasoning_mode='bounded')
    with pytest.raises(ValueError):policy.validate_video_stage(skill,p,a,stage,version=version)


def test_safe_receipts_do_not_admit_derived_ids_for_other_stages():
    p,a=details(*evidence());body=dict(videoStage='perception',skillName='ui_video_perception',skillVersion='v1',videoCorrelation='a'*32,payload=p,imageAssets=a,prompt='pixels')
    proof=make_expected_attestation(body)
    for stage in ['audit','grounding','overview']:
        proof['stage']=stage
        assert 'fine_frame_ids' not in safe_attestation(proof)['effective_options']
    proof['stage']='perception'
    for name in ['frame-000-detail','frame-000-detail-3','frame-000-detail-00','secret']:
        proof['effective_options']['fine_frame_ids']=[name]
        assert 'fine_frame_ids' not in safe_attestation(proof)['effective_options']


@pytest.mark.parametrize('change', ['invalid_jpeg','bad_base64','tiny','oversize','missing','asset_name','bytes'])
def test_crop_rejects_invalid_source_and_capacity(change):
    from task_server.services.ui_video_perception_details import build_detail_views
    p,a=evidence(); im=Image.new('RGB',(99,120) if change=='tiny' else (4097,100) if change=='oversize' else (120,302));out=io.BytesIO();im.save(out,format='JPEG')
    for asset in a:asset['base64']=base64.b64encode(out.getvalue()).decode()
    if change=='invalid_jpeg':a[0]['base64']='YQ=='
    if change=='bad_base64':a[0]['base64']='@@'
    if change=='missing':a.pop()
    if change=='asset_name':a[0]['name']='frame-999'
    if change=='bytes':a[0]['base64']='YQ=='*(10*1024*1024//4)
    with pytest.raises(ValueError,match='video_'):build_detail_views(p['frames'],a,['frame-000'],lambda:False)


def test_crop_cancellation_during_encoding_returns_no_partial_result():
    from task_server.services.ui_video_perception_details import build_detail_views
    p,a=evidence();out=io.BytesIO();Image.new('RGB',(120,302)).save(out,format='JPEG')
    for asset in a:asset['base64']=base64.b64encode(out.getvalue()).decode()
    before=copy.deepcopy(a); checks=[]
    def cancelled():
        checks.append(True)
        return len(checks)==3
    with pytest.raises(ValueError,match='video_cancelled'):build_detail_views(p['frames'],a,['frame-000'],cancelled)
    assert a==before and len(checks)==3


def test_detail_asset_timestamp_cannot_be_boolean():
    p,a=details(*evidence());a[3]['timestamp_ms']=False
    with pytest.raises(ValueError,match='video_'):policy.validate_video_stage('ui_video_perception',p,a,'perception')


@pytest.mark.parametrize('assets', [None,[None,None,None]])
def test_crop_rejects_unavailable_assets(assets):
    from task_server.services.ui_video_perception_details import build_detail_views
    p,_=evidence()
    with pytest.raises(ValueError,match='video_'):build_detail_views(p['frames'],assets,['frame-000'],lambda:False)


def test_derived_encoding_crosses_byte_cap_without_mutating_originals():
    from task_server.services.ui_video_perception_details import build_detail_views
    p,a=evidence();out=io.BytesIO();Image.new('RGB',(120,302)).save(out,format='JPEG');jpeg=out.getvalue()
    for asset in a:asset['base64']=base64.b64encode(jpeg).decode()
    target=(10*1024*1024-sum(len(v['base64']) for v in a[1:])-100)//4*3
    a[0]['base64']=base64.b64encode(jpeg+b'\0'*(target-len(jpeg))).decode()
    before=copy.deepcopy(a)
    with pytest.raises(ValueError,match='video_vision_limit'):build_detail_views(p['frames'],a,['frame-000'],lambda:False)
    assert a==before
