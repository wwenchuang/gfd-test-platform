"""Pure ordinary-result domains and diagnostics for validated canonical evidence."""
INTERACTIONS = {'aiTap', 'aiInput', 'aiScroll'}


def ordinary_outcome_domain(events, index, duration):
    """Keep the planner's inclusive next evidence-start and exclusive shell bound."""
    from .ui_video_analysis import _system_boundary
    lower = events[index]['end_ms']
    upper = next((e['start_ms'] for e in events[index+1:] if e['action'] in INTERACTIONS), duration)
    system = min((e['start_ms'] for e in events[index+1:] if _system_boundary(e, duration)), default=duration+1)
    return {'lower_ms_inclusive': lower, 'upper_ms_inclusive': upper,
            'system_start_ms_exclusive': system, 'empty': lower > upper or lower >= system}


def ordinary_outcome_frames(events, index, frames, duration):
    domain = ordinary_outcome_domain(events, index, duration)
    return [f['frame_id'] for f in frames if domain['lower_ms_inclusive'] <= f['timestamp_ms'] <= domain['upper_ms_inclusive']
            and f['timestamp_ms'] < domain['system_start_ms_exclusive']]


def ordinary_export_conflicts(events, tracks, frames, duration, packets):
    """No prose or inferred causality: callers must first validate and fold fragments."""
    from .ui_video_analysis import _required_interactions
    required = set(_required_interactions(events, duration))
    times = {f['frame_id']: f['timestamp_ms'] for f in frames}
    owners = {ref: p['packet_id'] for p in packets for ref in p['owned_frame_ids']}
    def anchor(ref):
        return {'frame_id': ref, 'timestamp_ms': times[ref]}
    def identity(index):
        event, track = events[index], tracks[index]
        return {'candidate_index': index, 'event_id': track['event_id'], 'packet_id': track['packet_id'],
                'fragment_owner_ids': [owners[f['trigger_frame']] for f in track['fragments']],
                'fragments': [{'fragment_id': f"{track['event_id']}:fragment-{number}",
                               'packet_id': owners[fragment['trigger_frame']],
                               'first': anchor(fragment['frame_refs'][0]), 'last': anchor(fragment['frame_refs'][-1]),
                               'trigger': anchor(fragment['trigger_frame'])}
                              for number, fragment in enumerate(track['fragments'], 1)],
                'action': event['action'], 'first': anchor(event['frame_refs'][0]),
                'last': anchor(event['frame_refs'][-1]), 'trigger': anchor(track['trigger_frame']),
                'terminal': anchor(event['outcome']['frame_refs'][-1])}
    conflicts = []
    for index, event in enumerate(events):
        domain = ordinary_outcome_domain(events, index, duration)
        terminal = times[event['outcome']['frame_refs'][-1]]
        outside = not (domain['lower_ms_inclusive'] <= terminal <= domain['upper_ms_inclusive']
                       and terminal < domain['system_start_ms_exclusive'])
        if not domain['empty'] and not outside: continue
        next_index = next((i for i in range(index+1, len(events)) if events[i]['action'] in INTERACTIONS), None)
        conflicts.append({'classification': 'empty_ordinary_interval' if domain['empty'] else 'terminal_outside_ordinary_interval',
                          'required': index in required, 'event': identity(index),
                          'next_interaction': identity(next_index) if next_index is not None else None,
                          'interval': domain})
    return conflicts


def unsafe_current_correction_conflicts(conflicts, accepted_events, accepted_tracks, frames, packet_id):
    """Classify all required conflicts against the immutable pre-packet snapshot.

    Trigger PTS is eligibility only. Callers still rerun the original next.start
    domain after a whole-current-packet response; this function edits no evidence.
    """
    snapshots={t['event_id']:e for t,e in zip(accepted_tracks,accepted_events)}
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    unsafe=[]
    for conflict in conflicts:
        if not conflict['required']:continue
        left=conflict['event'];nxt=conflict['next_interaction'];domain=conflict['interval']
        proposed_floor=max(left['last']['timestamp_ms'],left['terminal']['timestamp_ms'])
        if proposed_floor>=domain['system_start_ms_exclusive'] or nxt is None:
            unsafe.append(conflict);continue
        if (nxt['event_id'] in snapshots or nxt['packet_id']!=packet_id or
            any(owner!=packet_id for owner in nxt['fragment_owner_ids'])):
            unsafe.append(conflict);continue
        snapshot=snapshots.get(left['event_id'])
        if snapshot is None:
            if any(owner!=packet_id for owner in left['fragment_owner_ids']):unsafe.append(conflict)
            continue
        # A current continuation never makes its accepted prefix mutable.
        floor=max(snapshot['end_ms'],times[snapshot['outcome']['frame_refs'][-1]])
        if floor>nxt['trigger']['timestamp_ms'] or floor>=domain['system_start_ms_exclusive']:
            unsafe.append(conflict)
    return unsafe
