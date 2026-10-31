"""Reserve original changed pixels inside gaps; never infer an action."""
import pytest
from task_server.services import ui_video_source_service as video


def select(groups, times, base, limit, changes, cancelled=lambda: False):
    return video._select_micro_motion(groups, times, base, limit, cancelled, changes=changes)[0]


def test_saturated_budget_retains_temporary_low_area_state_and_control_context():
    # Redundant completed bursts consume the old budget before the temporary state.
    times=[0,100,200,300,400,500,600,1200,1300,1400,1500,2000]
    groups=[{0,1,2},{2,3,4},{4,5,6},{7,8,9,10}]
    old,_=video._select_micro_motion(groups,times,{0,2000},10)
    assert not old.intersection({1200,1300,1400,1500})
    result=select(groups,times,{0,2000},10,[0]*7+[.1]*4+[0])
    assert {600,1300,2000}<=result
    assert old<=result  # Complete short control evidence remains intact here.
    assert len(result)<=10


def test_isolated_faint_marker_with_delayed_response_needs_no_complete_group():
    times=[0,600,1200,1233,1800,2000]
    changes=[0,0,.05,0,2,0]
    assert video._micro_motion_groups(times,changes)==[]
    assert {0,1200,2000}<=select([],times,{0,2000},4,changes)


@pytest.mark.parametrize('end,witness,kept',[(1000,500,False),(1001,500,True),(1200,199,False),(1200,200,True),(1200,1000,True),(1200,1001,False)])
def test_one_second_gap_target_and_two_hundred_ms_context(end,witness,kept):
    times=[0,witness,end]
    result=select([],times,{0,end},3,[0,.04,0])
    assert (witness in result)==kept


def test_midpoint_ties_prefer_stronger_change_then_earlier_original_row():
    times=[0,800,1200,2000]
    assert 1200 in select([],times,{0,2000},3,[0,.04,.05,0])
    assert 800 in select([],times,{0,2000},3,[0,.05,.05,0])


def test_group_straddling_gap_is_still_raw_witness_eligible():
    times=[0,100,1000,2000,2100]
    groups=[{1,2,4}]
    result=select(groups,times,{0,2000},3,[0,0,.05,0,0])
    assert result=={0,1000,2000}


@pytest.mark.parametrize('changes',[[0],[0,float('nan'),0],[0,float('inf'),0]])
def test_invalid_raw_vector_refuses_safely(changes):
    with pytest.raises(ValueError,match='video_frames_invalid'):
        select([],[0,1000,2000],{0,2000},3,changes)


def test_no_candidates_and_omitted_changes_preserve_legacy_identity():
    args=([{0,1,2},{2,3,4}],[0,100,200,300,400],{0,400},4)
    old=video._select_micro_motion(*args)
    assert video._select_micro_motion(*args,changes=[0]*5)==old
    assert video._select_micro_motion([],[],{0,2000},3,changes=[])==({0,2000},0)


def test_protected_faint_triplet_and_gap_endpoints_fail_closed_when_full():
    times=[0,100,200,1000,2000]
    with pytest.raises(ValueError,match='video_split_required'):
        select([{0,1,2}],times,{0,2000},4,[0,0,0,.05,0])


def test_rerun_keeps_complete_groups_and_existing_faint_triplets():
    times=[0,100,200,300,400,500,600,1200,1300,1400,1500,2000]
    groups=[{0,1,2},{2,3,4},{4,5,6},{7,8,9,10}]
    result=select(groups,times,{0,2000},10,[0]*7+[.1]*4+[0])
    assert {0,100,200,300,400,500,600}<=result
    assert not (set(times[7:11])<=result)


def test_cancellation_during_added_raw_scan():
    calls=0
    def cancelled():
        nonlocal calls
        calls+=1
        return calls>=7
    with pytest.raises(ValueError,match='video_cancelled'):
        select([],[0,800,1200,2000],{0,2000},4,[0,.05,.05,0],cancelled)


def test_reservations_exceeding_selection_cap_refuse_without_dropping_base():
    with pytest.raises(ValueError,match='video_split_required'):
        select([],[0,1000,2000,3000,4000],{0,2000,4000},4,[0,.05,0,.05,0])


@pytest.mark.parametrize('times',[[0,float('nan'),2000],[0,float('inf'),2000],[0,1000,1000]])
def test_invalid_original_timestamps_refuse(times):
    with pytest.raises(ValueError,match='video_frames_invalid'):
        select([],times,{0,2000},3,[0,.05,0])


def test_static_raw_scan_does_not_fill_an_unchanged_wait():
    assert select([],[0,1000,2000,3000],{0,3000},5,[0,0,0,0])=={0,3000}


def test_reservation_is_not_recursively_expanded_into_more_gap_witnesses():
    times=[0,1500,3000,4500,6000]
    assert select([],times,{0,6000},10,[0,.1,.1,.1,0])=={0,3000,6000}


def test_subthreshold_changes_do_not_promote_clock_noise():
    assert select([],[0,1000,2000],{0,2000},3,[0,.039,0])=={0,2000}


def test_generated_original_video_preserves_marker_delayed_response_and_hud(tmp_path):
    import shutil
    import subprocess
    from PIL import Image
    movie=tmp_path/'transient.mp4'
    # The small marker precedes the page response by 1.5s; the second page has
    # a temporary low-area HUD. These are pixels, not asserted user actions.
    filters=("drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='between(t,3,5.99)',"
             "drawbox=x=30:y=300:w=16:h=16:color=0xe6e6e6:t=fill:enable='between(t,1.5,1.52)',"
             "drawbox=x=20:y=100:w=80:h=40:color=0x696969:t=fill:enable='between(t,4.5,4.9)'")
    subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i',
                    'color=white:s=256x512:r=30:d=7','-vf',filters,
                    '-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
    output=tmp_path/'frames'
    result=video.decode_video(movie,output,lambda:False)
    frames={f['timestamp_ms']:output/(f['frame_id']+'.jpg') for f in result['frames']}
    assert {0,1500,3000,4500,6000}<=frames.keys()
    with Image.open(frames[1500]) as image:
        assert 215<=image.convert('L').getpixel((38,308))<=240
    with Image.open(frames[4500]) as image:
        assert 85<=image.convert('L').getpixel((60,120))<=115
    assert max(frames)>6800
    assert len(frames)<=video.MAX_FRAMES
    assert sum(p.stat().st_size for p in output.glob('*.jpg'))<16*1024**2


def test_fully_consumed_budget_replaces_redundant_motion_with_gap_state():
    times=[0,100,200,300,400,500,600,700,800,1200,1400,1600,1800,2000]
    groups=[{0,1,2,3},{3,4,5,6},{2,3,7,8},{9,10,11,12}]
    base={0,100,200,300,400,500,2000}
    old,_=video._select_micro_motion(groups,times,base,10)
    assert len(old)==10
    assert not old.intersection({1200,1400,1600,1800})
    result=select(groups,times,base,10,[0]*9+[.1]*4+[0])
    assert base|{800,1400}<=result
    assert len(result)<=10
    assert old-result  # Redundant motion, not mandatory controls, is displaced.
