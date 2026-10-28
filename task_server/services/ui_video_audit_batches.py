"""Pure claim partitions for the existing twelve-frame fine-audit capacity."""
import copy


def partition_audit_claims(claims):
    """Greedily keep original claim order, counting shared required anchors once."""
    if not isinstance(claims, list):
        raise ValueError('video_audit_batches_invalid')
    batches = []; current = []; anchors = set(); identities = set()
    for claim in claims:
        track = claim.get('track') if isinstance(claim, dict) else None
        identity = track.get('event_id') if isinstance(track, dict) else None
        refs = track.get('required_frame_refs') if isinstance(track, dict) else None
        if (not isinstance(identity, str) or not identity or identity in identities or
                not isinstance(refs, list) or not refs or
                any(not isinstance(ref, str) or not ref for ref in refs) or
                len(set(refs)) != len(refs) or len(refs) > 12):
            raise ValueError('video_audit_batches_invalid')
        identities.add(identity)
        if len(anchors | set(refs)) > 12:
            batches.append(current); current = []; anchors = set()
        current.append(copy.deepcopy(claim)); anchors.update(refs)
    return batches + [current]  # Even no claims must audit the complete owned timeline.
