"""Original PTS revisit acquisition is bounded, private and read only."""
from pathlib import Path
import pytest
from task_server.services import ui_video_revisit as rv


def setup_loader(tmp_path,monkeypatch,pts=(1.1,1.2,1.3),cancelled=lambda:False):
    source=tmp_path/'source.mp4';source.write_bytes(b'original source')
    root=tmp_path/'output';root.mkdir();(root/'frames').mkdir()
    (root/'frames'/'frame-002.jpg').write_bytes(b'existing')
    calls=[]
    def decode(command,cancelled,timeout=150):
        calls.append(command)
        output=Path(command[-1])
        for i,t in enumerate(pts,1):Path(str(output).replace('%03d',f'{i:03d}')).write_bytes(b'jpeg')
        return b'',b'\n'.join(f'[Parsed_showinfo] n: {i} pts: {i} pts_time:{t}'.encode() for i,t in enumerate(pts))
    monkeypatch.setattr(rv,'_process',decode)
    monkeypatch.setattr(rv,'_tool',lambda n:n)
    monkeypatch.setattr(rv,'_sync_args',lambda c:['-fps_mode','vfr'])
    frames=[dict(frame_id='frame-001',timestamp_ms=1000),dict(frame_id='frame-002',timestamp_ms=1400)]
    return rv.make_revisit_loader(source,root,frames,cancelled),root,source,calls


def request():return dict(first_frame='frame-001',last_frame='frame-002',question='检查是否有遗漏触点')


def test_actual_pts_metadata_and_private_files_without_source_mutation(tmp_path,monkeypatch):
    loader,root,source,calls=setup_loader(tmp_path,monkeypatch)
    result=loader(request(),2)
    assert result==[dict(frame_id='frame-003',timestamp_ms=1100),dict(frame_id='frame-004',timestamp_ms=1300)]
    assert source.read_bytes()==b'original source'
    assert (root/'frames'/'frame-002.jpg').read_bytes()==b'existing'
    assert all((root/'frames'/(f['frame_id']+'.jpg')).stat().st_mode&0o777==0o600 for f in result)
    assert not list(root.glob('revisit-*'))
    command=calls[0];vf=command[command.index('-vf')+1]
    assert 'showinfo' in vf and 'select=' in vf and 'fps=' not in vf and 'setpts' not in vf
    assert '-copyts' not in command and '-protocol_whitelist' in command and '-frames:v' in command


def test_repeated_timestamp_is_never_reused(tmp_path,monkeypatch):
    loader,root,_,_=setup_loader(tmp_path,monkeypatch,pts=(1,1.1,1.1,1.4))
    assert [f['timestamp_ms'] for f in loader(request(),20)]==[1100]
    assert loader(request(),20)==[]


@pytest.mark.parametrize('change',[{'first_frame':'other'},{'last_frame':'frame-001'},{'question':''},{'path':'/tmp/arbitrary'},{'question':'x'*2001}])
def test_request_rejects_untrusted_or_invalid_fields(tmp_path,monkeypatch,change):
    loader,_,_,calls=setup_loader(tmp_path,monkeypatch)
    with pytest.raises(ValueError):loader({**request(),**change},2)
    assert calls==[]


@pytest.mark.parametrize('capacity',[1,21,True,'2'])
def test_capacity_bounds(tmp_path,monkeypatch,capacity):
    loader,_,_,calls=setup_loader(tmp_path,monkeypatch)
    with pytest.raises(ValueError):loader(request(),capacity)
    assert calls==[]


def test_overdense_decode_is_rejected_and_cleaned(tmp_path,monkeypatch):
    loader,root,_,_=setup_loader(tmp_path,monkeypatch,pts=[1+i/1000 for i in range(121)])
    with pytest.raises(ValueError):loader(request(),20)
    assert sorted(p.name for p in (root/'frames').iterdir())==['frame-002.jpg']
    assert not list(root.glob('revisit-*'))


def test_cancelled_before_decode(tmp_path,monkeypatch):
    loader,_,_,calls=setup_loader(tmp_path,monkeypatch,cancelled=lambda:True)
    with pytest.raises(ValueError,match='video_cancelled'):loader(request(),2)
    assert calls==[]


def test_global_new_frame_budget_is_64(tmp_path,monkeypatch):
    loader,root,_,_=setup_loader(tmp_path,monkeypatch,pts=[1+i/1000 for i in range(1,101)])
    results=[loader(request(),20) for _ in range(4)]
    assert [len(x) for x in results]==[20,20,20,4]
    assert len({x['timestamp_ms'] for batch in results for x in batch})==64
    with pytest.raises(ValueError,match='video_revisit_limit'):loader(request(),2)


def test_existing_output_collision_is_never_overwritten(tmp_path,monkeypatch):
    loader,root,_,_=setup_loader(tmp_path,monkeypatch)
    target=root/'frames'/'frame-003.jpg';target.write_bytes(b'user data')
    with pytest.raises(ValueError,match='video_revisit_collision'):loader(request(),2)
    assert target.read_bytes()==b'user data'


def test_cancellation_before_write_leaves_no_new_images(tmp_path,monkeypatch):
    count=0
    def cancelled():
        nonlocal count
        count+=1
        return count==3
    loader,root,_,_=setup_loader(tmp_path,monkeypatch,cancelled=cancelled)
    with pytest.raises(ValueError,match='video_cancelled'):loader(request(),2)
    assert [p.name for p in (root/'frames').iterdir()]==['frame-002.jpg']
    assert not list(root.glob('revisit-*'))


def test_decoder_pts_outside_requested_interval_is_rejected(tmp_path,monkeypatch):
    loader,_,_,_=setup_loader(tmp_path,monkeypatch,pts=(0.1,0.2))
    with pytest.raises(ValueError,match='video_frames_invalid'):loader(request(),2)


def test_gap_and_identifier_budget_reject_before_decode(tmp_path,monkeypatch):
    loader,root,source,calls=setup_loader(tmp_path,monkeypatch)
    for fs in [[dict(frame_id='frame-001',timestamp_ms=0),dict(frame_id='frame-002',timestamp_ms=3001)],
               [dict(frame_id='frame-998',timestamp_ms=1000),dict(frame_id='frame-999',timestamp_ms=1400)]]:
        load=rv.make_revisit_loader(source,root,fs,lambda:False)
        with pytest.raises(ValueError):load(dict(first_frame=fs[0]['frame_id'],last_frame=fs[-1]['frame_id'],question='check'),2)
    assert calls==[]


def test_successful_new_frames_can_bound_a_later_refinement(tmp_path,monkeypatch):
    loader,_,_,_=setup_loader(tmp_path,monkeypatch)
    first=loader(request(),2)
    # The second decode in this mock includes only the newly bounded interval.
    result=loader(dict(first_frame=first[0]['frame_id'],last_frame=first[-1]['frame_id'],question='细查区间'),2)
    assert result==[dict(frame_id='frame-005',timestamp_ms=1200)]


def test_real_offset_source_uses_canonical_decoder_time_origin(tmp_path):
    """A nonzero source start must select the same pixels as the existing decoder."""
    import shutil
    import subprocess
    from PIL import Image,ImageChops,ImageStat
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    source=tmp_path/'offset.mp4'
    subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=96x96:rate=10:duration=8',
                    '-output_ts_offset','5','-c:v','mpeg4',str(source)],check=True)
    root=tmp_path/'revisit';root.mkdir();(root/'frames').mkdir()
    loader=rv.make_revisit_loader(source,root,[dict(frame_id='frame-001',timestamp_ms=5000),dict(frame_id='frame-002',timestamp_ms=6000)],lambda:False)
    found=loader(dict(first_frame='frame-001',last_frame='frame-002',question='check actual pixels'),2)
    assert found[0]['timestamp_ms']==5100
    expected=tmp_path/'expected.jpg'
    subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(source),'-vf',"select='eq(n,51)'",'-frames:v','1','-q:v','4',str(expected)],check=True)
    with Image.open(expected) as a, Image.open(root/'frames'/(found[0]['frame_id']+'.jpg')) as b:
        assert max(ImageStat.Stat(ImageChops.difference(a,b)).mean)<1


def test_bounded_locator_clips_decoder_before_write_and_keeps_request(tmp_path,monkeypatch):
    import copy
    loader,root,_,calls=setup_loader(tmp_path,monkeypatch,pts=(1.2,))
    q=request();before=copy.deepcopy(q)
    result=loader(q,2,owned_time_range_ms=(1150,1250))
    assert [f['timestamp_ms'] for f in result]==[1200] and q==before
    vf=calls[0][calls[0].index('-vf')+1]
    assert 'between(t,1.149,1.251)'in vf and 'floor('not in vf


@pytest.mark.parametrize('pts',[(1.149,), (1.25,), (1.251,)])
def test_bounded_decoder_filters_collar_and_exclusive_end_without_new_files(tmp_path,monkeypatch,pts):
    loader,root,_,_=setup_loader(tmp_path,monkeypatch,pts=pts)
    assert loader(request(),2,owned_time_range_ms=(1150,1250))==[]
    assert [p.name for p in (root/'frames').iterdir()]==['frame-002.jpg']


@pytest.mark.parametrize('bounds',[(1400,1500),(0,1000),(1250,1150),(True,1200),(1000.0,1200),(1000,),None])
def test_invalid_bounded_ranges_reject_before_decoder(tmp_path,monkeypatch,bounds):
    loader,_,_,calls=setup_loader(tmp_path,monkeypatch)
    if bounds is None:
        # None is explicitly the legacy inclusive decoder mode.
        assert loader(request(),2)
    else:
        with pytest.raises(ValueError):loader(request(),2,owned_time_range_ms=bounds)
        assert calls==[]


@pytest.mark.parametrize('rate,duration,first,last,cut,expected,timescale',[(30,0.2,33,100,67,67,None),(2000,0.003,0,2,1,1,None),(2000,0.51,500,502,501,501,1000000)])
def test_real_fractional_pts_midpoint_belongs_to_exactly_one_canonical_ms_owner(tmp_path,rate,duration,first,last,cut,expected,timescale):
    import shutil
    import subprocess
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    source=tmp_path/'fractional.mp4'
    command=[ffmpeg,'-nostdin','-v','error','-f','lavfi','-i',f'testsrc2=size=96x96:rate={rate}:duration={duration}','-c:v','mpeg4']
    if timescale is not None:command+=['-video_track_timescale',str(timescale)]
    subprocess.run(command+[str(source)],check=True)
    root=tmp_path/'output';root.mkdir();(root/'frames').mkdir()
    loader=rv.make_revisit_loader(source,root,[dict(frame_id='frame-001',timestamp_ms=first),dict(frame_id='frame-002',timestamp_ms=last)],lambda:False)
    q=dict(first_frame='frame-001',last_frame='frame-002',question='fractional source PTS at frozen canonical midpoint')
    assert loader(q,2,owned_time_range_ms=(first,cut))==[]
    result=loader(q,2,owned_time_range_ms=(cut,last+1))
    assert result==[dict(frame_id='frame-003',timestamp_ms=expected)]


def test_bounded_filter_uses_decimal_half_up_at_both_canonical_edges(tmp_path,monkeypatch):
    loader,_,_,_=setup_loader(tmp_path,monkeypatch,pts=(1.149499,1.1495,1.149501,1.249499,1.2495,1.249501))
    found=loader(request(),20,owned_time_range_ms=(1150,1250))
    assert [f['timestamp_ms']for f in found]==[1150,1249]


def test_bounded_raw_padding_still_rejects121_frames_before_canonical_filter(tmp_path,monkeypatch):
    loader,root,_,_=setup_loader(tmp_path,monkeypatch,pts=[1.149+i/100000 for i in range(121)])
    with pytest.raises(ValueError,match='video_frames_invalid'):loader(request(),20,owned_time_range_ms=(1150,1151))
    assert [p.name for p in (root/'frames').iterdir()]==['frame-002.jpg']
