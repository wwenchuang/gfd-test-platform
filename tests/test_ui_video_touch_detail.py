import base64
import io
import pytest
from PIL import Image,ImageDraw
from task_server.services import ui_video_touch_detail as detail


def scene():
    im=Image.new('RGB',(320,700),'#f4f8fa')
    ImageDraw.Draw(im).text((190,450),'records',fill='black')
    return im


def touched(im,x=250,y=460):
    out=im.copy();ImageDraw.Draw(out).ellipse((x-10,y-10,x+10,y+10),outline='#b0b0b0',width=2)
    return out


def test_new_ring_is_localized_and_original_not_modified():
    before=scene();after=touched(before);raw=after.tobytes()
    point=detail.locate_ring(before,after)
    assert point and abs(point[0]-250)<4 and abs(point[1]-460)<4
    assert after.tobytes()==raw


@pytest.mark.parametrize('kind',['static','disappeared','transition','resized','line'])
def test_no_detail_for_absent_or_unreliable_pointer(kind):
    a=scene();b=a.copy()
    if kind=='disappeared':a=touched(a)
    if kind=='transition':b=Image.new('RGB',a.size,'#999999')
    if kind=='resized':b=b.resize((160,350))
    if kind=='line':ImageDraw.Draw(b).line((0,460,319,460),fill='#b0b0b0',width=2)
    assert detail.locate_ring(a,b) is None


def test_detail_has_parent_evidence_and_respects_asset_budget(tmp_path):
    (tmp_path/'frames').mkdir();frames=[]
    for i,im in enumerate([scene(),touched(scene())],1):
        name=f'frame-{i:03d}';im.save(tmp_path/'frames'/f'{name}.jpg')
        frames.append({'frame_id':name,'timestamp_ms':i*100})
    assets=[{'name':f['frame_id'],'base64':'a'} for f in frames]
    extra=detail.append_details(frames,tmp_path,assets,lambda:False)
    assert len(extra)==1 and len(assets)==3
    assert extra[0]['source_frame_id']=='frame-002'
    assert assets[-1]['name']==extra[0]['frame_id']
    with Image.open(io.BytesIO(base64.b64decode(assets[-1]['base64']))) as crop:assert crop.width>=400
    full=[{'base64':'a'}]*60
    assert detail.append_details(frames,tmp_path,full,lambda:False)==[] and len(full)==60


def test_new_solid_patch_is_not_a_pointer_ring():
    a=scene();b=a.copy();ImageDraw.Draw(b).rectangle((220,430,280,490),fill='#b0b0b0')
    assert detail.locate_ring(a,b) is None


def test_cancellation_and_large_time_gap(tmp_path):
    (tmp_path/'frames').mkdir();frames=[]
    for i,im in enumerate([scene(),touched(scene())],1):
        name=f'frame-{i:03d}';im.save(tmp_path/'frames'/f'{name}.jpg');frames.append({'frame_id':name,'timestamp_ms':i*1000})
    assert detail.append_details(frames,tmp_path,[],lambda:False)==[]
    with pytest.raises(ValueError,match='video_cancelled'):detail.append_details(frames,tmp_path,[],lambda:True)


def test_observation_transports_details_without_new_event_refs(tmp_path,monkeypatch):
    from task_server.services import ui_video_windows as windows,ai_skill_service as ai
    (tmp_path/'frames').mkdir();frames=[]
    for i,im in enumerate([scene(),touched(scene())],1):
        name=f'frame-{i:03d}';im.save(tmp_path/'frames'/f'{name}.jpg');frames.append({'frame_id':name,'timestamp_ms':i*100})
    def call(skill,payload,**kw):
        assert payload['detail_frames'][0]['source_frame_id']=='frame-002'
        assert len(kw['image_assets'])==3
        assert payload['frames']==frames
        return {'events':[{'action':'aiTap','target':'查看记录','value':'','frame_refs':['frame-002'],'observation':'点击查看记录入口'}],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',call)
    result,_=windows.observe_windows(frames,300,tmp_path,lambda:False)
    assert result['events'][0]['frame_refs']==['frame-002']


def test_context_frame_cannot_consume_local_detail_budget(tmp_path):
    (tmp_path/'frames').mkdir();frames=[]
    for i,im in enumerate([scene(),touched(scene())],1):
        name=f'frame-{i:03d}';im.save(tmp_path/'frames'/f'{name}.jpg');frames.append({'frame_id':name,'timestamp_ms':i*100})
    assert detail.append_details(frames,tmp_path,[],lambda:False,{'frame-001'})==[]
