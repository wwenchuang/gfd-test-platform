"""Same-input context may overlap without reversing accepted edit evidence."""
import copy

import pytest

from task_server.services import ui_video_trajectory as vt
from test_ui_video_trajectory import event, frames


def input_fragment(refs, trigger, continuation='', outcome=None):
    raw=event([f'frame-{i:03d}' for i in refs],'aiInput')
    raw.update(trigger_frame=f'frame-{trigger:03d}',continuation_of=continuation)
    if outcome is not None:raw['outcome']['frame_refs']=[f'frame-{outcome:03d}']
    return raw


@pytest.mark.parametrize('trigger',[36,40])
def test_context_overlap_continues_before_or_after_prior_action_terminal(trigger):
    fs=frames();packets=vt.make_packets(fs,[])
    first=input_fragment([26,30,38],30)
    first['uncertainties']=['Prior edit result remains uncertain']
    old,tracks=vt.bind_packet_events([first],packets[0],fs,[],[])
    second=input_fragment([35,trigger,50],trigger,tracks[0]['event_id'])
    second['uncertainties']=['Current candidate selection remains uncertain']
    before=copy.deepcopy((first,second,old,tracks))
    joined,history=vt.bind_packet_events([second],packets[1],fs,old,tracks)
    assert len(joined)==1
    assert (joined[0]['start_ms'],joined[0]['end_ms'])==(2600,5000)
    assert joined[0]['uncertainties']==old[0]['uncertainties']+second['uncertainties']
    assert joined[0]['outcome']['frame_refs']==['frame-050']
    assert history[0]['fragments']==[first,second]
    assert history[0]['frame_refs']==sorted(set(first['frame_refs']+second['frame_refs']))
    assert set(history[0]['frame_refs'])==set(joined[0]['frame_refs'])
    assert len(joined[0]['frame_refs'])<=8
    assert (first,second,old,tracks)==before


def accepted_input(outcome=45):
    fs=frames();packet=vt.make_packets(fs,[])[1]
    first=input_fragment([30,34,45],34,outcome=outcome)
    old,tracks=vt.bind_packet_events([first],packet,fs,[],[])
    return fs,packet,old,tracks


def test_continuation_can_retain_equal_action_and_outcome_endpoints():
    fs,packet,old,tracks=accepted_input(outcome=60)
    second=input_fragment([35,40,45],40,tracks[0]['event_id'],outcome=60)
    joined,history=vt.bind_packet_events([second],packet,fs,old,tracks)
    assert (joined[0]['start_ms'],joined[0]['end_ms'])==(3000,4500)
    assert joined[0]['outcome']['frame_refs']==['frame-060']
    assert len(history[0]['fragments'])==2


@pytest.mark.parametrize('refs,trigger,outcome',[
    ([35,40,50],40,50),  # The action endpoint would move backwards.
    ([35,40,55],40,55),  # Only the separate outcome endpoint moves backwards.
    ([29,40,65],40,65),  # Context reaches before canonical input start.
    ([35,40,65],34,65),  # Repeated trigger, still owned and present below.
    ([33,40,65],33,65),  # Backward trigger, still owned.
])
def test_continuation_cannot_regress_accepted_time_boundaries(refs,trigger,outcome):
    fs,packet,old,tracks=accepted_input(outcome=60)
    # Isolate action regression from outcome regression.
    if refs[-1]==50:
        first=input_fragment([30,34,55],34,outcome=60)
        old,tracks=vt.bind_packet_events([first],packet,fs,[],[])
        outcome=65
    if trigger not in refs:refs=sorted(refs+[trigger])
    second=input_fragment(refs,trigger,tracks[0]['event_id'],outcome=outcome)
    with pytest.raises(ValueError) as exc:
        vt.bind_packet_events([second],packet,fs,old,tracks)
    assert exc.value.trajectory_reason=='input_continuation'


@pytest.mark.parametrize('trigger',[39,45])
def test_third_fragment_compares_trigger_with_last_fragment_not_track_origin(trigger):
    fs,packet,old,tracks=accepted_input()
    second=input_fragment([45,50],45,tracks[0]['event_id'])
    old,tracks=vt.bind_packet_events([second],packet,fs,old,tracks)
    # Both are later than the original 34 but do not advance beyond the last 45.
    third=input_fragment([trigger,50,55],trigger,tracks[0]['event_id'])
    with pytest.raises(ValueError) as exc:
        vt.bind_packet_events([third],packet,fs,old,tracks)
    assert exc.value.trajectory_reason=='input_continuation'


def test_repeated_trigger_at_action_terminal_cannot_be_a_new_fragment():
    fs,packet,_,_=accepted_input()
    first=input_fragment([30,34],34)
    old,tracks=vt.bind_packet_events([first],packet,fs,[],[])
    second=input_fragment([34,40,50],34,tracks[0]['event_id'])
    with pytest.raises(ValueError) as exc:
        vt.bind_packet_events([second],packet,fs,old,tracks)
    assert exc.value.trajectory_reason=='input_continuation'


@pytest.mark.parametrize('gap,accepted',[(5000,True),(5001,False)])
def test_forward_trigger_gap_is_bounded_even_with_old_context(gap,accepted):
    fs,packet,old,tracks=accepted_input()
    # Sparse future sampling leaves overlapping context in the same local packet.
    for f in fs:
        if f['timestamp_ms']>=5000:f['timestamp_ms']+=gap-500
    second=input_fragment([45,50,55],50,tracks[0]['event_id'])
    if accepted:
        joined,_=vt.bind_packet_events([second],packet,fs,old,tracks)
        assert joined[0]['start_ms']==3000 and joined[0]['end_ms']==gap+5000
    else:
        with pytest.raises(ValueError) as exc:
            vt.bind_packet_events([second],packet,fs,old,tracks)
        assert exc.value.trajectory_reason=='input_continuation'


def test_forward_third_fragment_preserves_prior_anchors_and_raw_outcomes():
    fs,packet,old,tracks=accepted_input(outcome=50)
    second=input_fragment([40,45,50],45,tracks[0]['event_id'],outcome=55)
    old,tracks=vt.bind_packet_events([second],packet,fs,old,tracks)
    third=input_fragment([45,50,55],50,tracks[0]['event_id'],outcome=60)
    joined,history=vt.bind_packet_events([third],packet,fs,old,tracks)
    assert (joined[0]['start_ms'],joined[0]['end_ms'])==(3000,5500)
    assert joined[0]['outcome']['frame_refs']==['frame-060']
    assert len(history[0]['fragments'])==3
    assert [f['trigger_frame'] for f in history[0]['fragments']]==['frame-034','frame-045','frame-050']
    assert [f['outcome']['frame_refs'] for f in history[0]['fragments']]==[['frame-050'],['frame-055'],['frame-060']]
    assert set(joined[0]['frame_refs'])=={'frame-030','frame-034','frame-040','frame-045','frame-050','frame-055'}
