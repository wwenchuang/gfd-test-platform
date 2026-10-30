"""Pure ownership validation and unverified forward-question projection."""
import copy


def _fail(reason):
    error=ValueError('video_model_invalid');error.trajectory_reason=reason
    raise error


def audit_domains(payload):
    """Derive actual attached roles and half-open cuts, ignoring model allowlists."""
    frames=payload.get('frames');owned=payload.get('owned_frame_ids');context=payload.get('context_frame_ids')
    cut=payload.get('owned_time_range_ms',[payload.get('owned_start_ms'),payload.get('owned_end_ms')])
    if (not isinstance(frames,list) or not frames or not isinstance(owned,list) or not owned or
        not isinstance(context,list) or not isinstance(cut,list) or len(cut)!=2 or
        any(type(t) is not int for t in cut) or not 0<=cut[0]<cut[1]):_fail('audit_owner_domain')
    if (('owned_start_ms' in payload and payload['owned_start_ms']!=cut[0]) or
        ('owned_end_ms' in payload and payload['owned_end_ms']!=cut[1])):_fail('audit_owner_domain')
    times={}
    for frame in frames:
        if (not isinstance(frame,dict) or not isinstance(frame.get('frame_id'),str) or not frame['frame_id'] or
            type(frame.get('timestamp_ms')) is not int or frame['timestamp_ms']<0 or
            frame['frame_id'] in times):_fail('audit_owner_domain')
        times[frame['frame_id']]=frame['timestamp_ms']
    if list(times.values())!=sorted(set(times.values())):_fail('audit_owner_domain')
    if (any(not isinstance(r,str) for r in owned+context) or len(set(owned+context))!=len(owned+context) or
        set(owned+context)!=set(times) or owned!=[r for r in times if cut[0]<=times[r]<cut[1]] or
        context!=[r for r in times if r not in owned]):_fail('audit_owner_domain')
    return list(owned),[r for r in context if times[r]>=cut[1]]


def freeze_ownership(frames,packets):
    """Tuple snapshots cannot be rewritten when loader appends current new frames."""
    rows=[];seen=[]
    if (not isinstance(frames,list) or not frames or
        any(not isinstance(f,dict) or not isinstance(f.get('frame_id'),str) or not f['frame_id'] or
            type(f.get('timestamp_ms')) is not int or f['timestamp_ms']<0 for f in frames)):
        _fail('frame_ownership_incomplete')
    times={f['frame_id']:f['timestamp_ms'] for f in frames}
    if len(times)!=len(frames) or list(times.values())!=sorted(set(times.values())):
        _fail('frame_ownership_incomplete')
    for p in packets:
        owned,_=audit_domains(p)
        if any(f['frame_id'] not in times or times[f['frame_id']]!=f['timestamp_ms'] for f in p['frames']):
            _fail('frame_ownership_incomplete')
        rows.append((p['packet_id'],p['owned_start_ms'],p['owned_end_ms'],
                     tuple((r,times[r]) for r in owned)))
        seen.extend(owned)
    if (seen!=list(times) or len(set(seen))!=len(seen) or len({r[0] for r in rows})!=len(rows) or
        any(a[2]!=b[1] for a,b in zip(rows,rows[1:]))):_fail('frame_ownership_incomplete')
    return tuple(rows)


def partition_issues(issues,packet,ownership):
    """Validate full ordinary shape first, then route only genuine later owners."""
    from .ui_video_trajectory import validate_audit_issues
    owned,future=audit_domains(packet)
    positions={row[0]:i for i,row in enumerate(ownership)}
    if packet['packet_id'] not in positions:_fail('audit_owner_domain')
    index=positions[packet['packet_id']];snapshot=ownership[index]
    if (packet['owned_start_ms'],packet['owned_end_ms'])!=snapshot[1:3]:_fail('audit_owner_domain')
    original={r:(i,t) for i,row in enumerate(ownership) for r,t in row[3]}
    for frame in packet['frames']:
        r=frame['frame_id']
        if r not in original and r not in owned:_fail('audit_owner_domain')
        if r in original and (original[r][1]!=frame['timestamp_ms'] or (r in owned)!=(original[r][0]==index)):
            _fail('audit_owner_domain')
    if not {r for r,_ in snapshot[3]}<=set(owned):_fail('audit_owner_domain')
    eligible=[r for r in future if r in original and original[r][0]>index]
    # Existing validator remains the sole issue shape/question/ref authority.
    validate_audit_issues(issues,{**packet,'owned_frame_ids':owned+eligible})
    current=[];forward=[]
    for i,item in enumerate(issues):
        if item['owner_frame'] in owned:current.append(copy.deepcopy(item))
        else:forward.append({'issue_index':i,'target_packet_id':ownership[original[item['owner_frame']][0]][0],
                             'issue':copy.deepcopy(item)})
    return current,forward
