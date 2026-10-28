"""Opt-in fine-image allocation from supplied original evidence; no event inference."""


def select_audit_fine_frames(frames, claims, facts, owned_frame_ids):
    """Keep required refs, owned-anchor neighbors, then exact mark representatives.

    Returned IDs are chronological. Neighbors are existing rows, never ID arithmetic.
    Prototype equality only proposes image sampling, not touch/episode/action counts.
    The caller retains all images, role metadata and claims, and partitions claims
    before calling when required evidence exceeds the unchanged twelve-image cap.
    """
    def fail():
        raise ValueError('video_audit_sampling_invalid')

    if any(not isinstance(value, list) for value in (frames, claims, facts, owned_frame_ids)):
        fail()
    positions = {}; times = {}; previous = -1
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            fail()
        fid, stamp = frame.get('frame_id'), frame.get('timestamp_ms')
        if (not isinstance(fid, str) or not fid or fid in positions or
                type(stamp) is not int or stamp <= previous):
            fail()
        positions[fid] = index; times[fid] = stamp; previous = stamp
    if (any(not isinstance(fid, str) or fid not in positions for fid in owned_frame_ids) or
            len(set(owned_frame_ids)) != len(owned_frame_ids) or
            owned_frame_ids != sorted(owned_frame_ids, key=positions.get)):
        fail()
    owned = set(owned_frame_ids)

    def refs(value, nonempty=True):
        if (not isinstance(value, list) or (nonempty and not value) or
                any(not isinstance(fid, str) or fid not in positions for fid in value) or
                len(set(value)) != len(value)):
            fail()
        return value

    required = set(); anchors = set()
    for claim in claims:
        track = claim.get('track') if isinstance(claim, dict) else None
        if not isinstance(track, dict):
            fail()
        mandatory = refs(track.get('required_frame_refs'))
        trigger = track.get('trigger_frame')
        outcome = track.get('outcome')
        if not isinstance(trigger, str) or trigger not in positions or not isinstance(outcome, dict):
            fail()
        end = refs(outcome.get('frame_refs'))[-1]
        if not {trigger, end} <= set(mandatory):
            fail()
        for key in ('frame_refs', 'allowed_frame_refs'):
            if key in track:
                refs(track[key])
        required.update(mandatory)
        anchors.update({trigger, end} & owned)
    if len(required) > 12:
        fail()

    rows = {}; prototypes = {}
    for row in facts:
        if not isinstance(row, dict):
            fail()
        fid = row.get('frame_id')
        if (not isinstance(fid, str) or fid not in positions or fid in rows or
                row.get('role') != ('owned' if fid in owned else 'context') or
                ('timestamp_ms' in row and (type(row['timestamp_ms']) is not int or row['timestamp_ms'] != times[fid]))):
            fail()
        marks = row.get('transient_marks')
        if not isinstance(marks, list):
            fail()
        keys = set()
        for mark in marks:
            if not isinstance(mark, dict):
                fail()
            key = tuple(mark.get(field, '') for field in ('shape', 'color', 'nearest_visible_control'))
            if any(not isinstance(value, str) for value in key):
                fail()
            keys.add(key)
        rows[fid] = row; prototypes[fid] = keys

    chosen = set(required)
    def add(candidates):
        for fid in candidates:
            if len(chosen) < 12:
                chosen.add(fid)

    neighbors = set()
    for fid in anchors:
        at = positions[fid]
        for index in (at - 1, at + 1):
            if 0 <= index < len(frames):
                neighbors.add(frames[index]['frame_id'])
    add(sorted(neighbors & owned, key=positions.get))
    add(sorted(neighbors - owned, key=positions.get))
    # One representative per exact prototype in each role. Ignore free descriptions.
    for role_ids in (owned, set(positions) - owned):
        seen = set()
        representatives = []
        for fid in sorted(role_ids & set(rows), key=positions.get):
            keys = prototypes[fid]
            if keys - seen:
                representatives.append(fid)
            seen.update(keys)
        add(representatives)
    return sorted(chosen, key=positions.get)
