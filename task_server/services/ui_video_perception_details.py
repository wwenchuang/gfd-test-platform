"""Opt-in deterministic JPEG thirds. Derived pixels have no action authority."""
import base64
import binascii
import hashlib
import io
from PIL import Image


def validate_detail_frames(frames, details):
    """Closed complete thirds in original-parent order; originals stay authoritative."""
    def fail():
        raise ValueError('video_detail_frames_invalid')
    if not isinstance(details, list) or len(details) not in (0, 3, 6):
        fail()
    previous = -1
    for offset in range(0, len(details), 3):
        first = details[offset]
        if not isinstance(first, dict):
            fail()
        parent_index = next((i for i, f in enumerate(frames) if f['frame_id'] == first.get('source_frame_id')), -1)
        if parent_index <= previous:
            fail()
        previous = parent_index
        parent = frames[parent_index]
        size = first.get('source_size')
        if (not isinstance(size, list) or len(size) != 2 or
                any(type(v) is not int or not 100 <= v <= 4096 for v in size) or size[0]*size[1] > 16777216):
            fail()
        w, h = size
        for i, detail in enumerate(details[offset:offset+3]):
            if (not isinstance(detail, dict) or set(detail) != {'frame_id','source_frame_id','timestamp_ms','source_size','box'} or
                    detail.get('frame_id') != parent['frame_id']+f'-detail-{i}' or
                    detail.get('source_frame_id') != parent['frame_id'] or
                    type(detail.get('timestamp_ms')) is not int or detail['timestamp_ms'] != parent['timestamp_ms'] or
                    detail.get('source_size') != size or
                    not isinstance(detail.get('source_size'), list) or any(type(v) is not int for v in detail['source_size']) or
                    not isinstance(detail.get('box'), list) or any(type(v) is not int for v in detail['box']) or
                    detail['box'] != [0, h*i//3, w, h*(i+1)//3]):
                fail()
    return details


def build_detail_views(frames, assets, selected_parent_ids, cancelled):
    """Return metadata, derived assets and source/view SHA manifest, never mutate.

    Original JPEG bytes are decoded without resizing; thirds are JPEG re-encoded
    at quality90. Callers retain original assets and the returned derived manifest.
    """
    from .ui_video_stage_policy import _metadata
    _metadata(frames)
    if (not isinstance(selected_parent_ids, list) or len(selected_parent_ids) > 2 or
            any(type(v) is not str for v in selected_parent_ids) or len(set(selected_parent_ids)) != len(selected_parent_ids) or
            not set(selected_parent_ids) <= {f['frame_id'] for f in frames}):
        raise ValueError('video_detail_frames_invalid')
    if not isinstance(assets, list) or len(assets) != len(frames) or len(assets)+3*len(selected_parent_ids) > 12:
        raise ValueError('video_vision_limit')
    total = 0
    for f, a in zip(frames, assets):
        if (not isinstance(a, dict) or a.get('name') != f['frame_id'] or a.get('timestamp_ms') != f['timestamp_ms'] or
                a.get('mime') != 'image/jpeg' or not isinstance(a.get('base64'), str) or not a['base64'] or a.get('dataUrl')):
            raise ValueError('video_frames_invalid')
        total += len(a['base64'])
    if total > 10*1024*1024:
        raise ValueError('video_vision_limit')
    details, views, manifest = [], [], []
    for f, a in zip(frames, assets):
        if cancelled():
            raise ValueError('video_cancelled')
        if f['frame_id'] not in selected_parent_ids:
            continue
        try:
            raw = base64.b64decode(a['base64'], validate=True)
            with Image.open(io.BytesIO(raw)) as source:
                w, h = source.size
                if source.format != 'JPEG' or not 100 <= w <= 4096 or not 100 <= h <= 4096 or w*h > 16777216:
                    raise ValueError('video_detail_frames_invalid')
                source.load()
                for i in range(3):
                    if cancelled():
                        raise ValueError('video_cancelled')
                    box = [0, h*i//3, w, h*(i+1)//3]
                    out = io.BytesIO()
                    source.crop(box).convert('RGB').save(out, format='JPEG', quality=90)
                    view = out.getvalue(); encoded = base64.b64encode(view).decode('ascii')
                    total += len(encoded)
                    if total > 10*1024*1024:
                        raise ValueError('video_vision_limit')
                    d = dict(frame_id=f['frame_id']+f'-detail-{i}', source_frame_id=f['frame_id'], timestamp_ms=f['timestamp_ms'], source_size=[w,h], box=box)
                    details.append(d)
                    views.append(dict(name=d['frame_id'],timestamp_ms=d['timestamp_ms'],mime='image/jpeg',base64=encoded))
                    manifest.append(dict(metadata=dict(d), source_sha256=hashlib.sha256(raw).hexdigest(), view_sha256=hashlib.sha256(view).hexdigest()))
        except (OSError, binascii.Error) as exc:
            raise ValueError('video_detail_frames_invalid') from exc
    if cancelled():
        raise ValueError('video_cancelled')
    return details, views, manifest
