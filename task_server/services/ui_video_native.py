"""Closed original-MP4 transport; no decoding, transcoding or provider access."""
import base64
import binascii
import hashlib
import re
from pathlib import Path

MAX_NATIVE_BYTES = 12 * 1024 * 1024
MAX_NATIVE_ENCODED = 10_000_000
NATIVE_FILE_TRANSPORT = 'dashscope-temporary-original-v1'
NATIVE_OPTIONS = {'fps': 4, 'max_pixels': 655360, 'total_pixels': 268435456}


def _validate_native_asset(payload, assets, *, transport=True):
    def fail():
        raise ValueError('video_native_asset_invalid')
    if (type(payload) is not dict or set(payload) != {'phase', 'video'} or
            payload['phase'] != 'whole-timeline'):
        fail()
    meta = payload['video']
    if (type(meta) is not dict or set(meta) not in ({'source_sha256', 'source_bytes', 'duration_ms', *NATIVE_OPTIONS},
                {'source_sha256', 'source_bytes', 'duration_ms', 'transport', *NATIVE_OPTIONS}) or
            type(meta.get('source_sha256')) is not str or not re.fullmatch('[0-9a-f]{64}', meta['source_sha256']) or
            type(meta.get('source_bytes')) is not int or not 12 <= meta['source_bytes'] <= MAX_NATIVE_BYTES or
            type(meta.get('duration_ms')) is not int or not 0 < meta['duration_ms'] <= 600000 or
            any(type(meta[k]) is not int or meta[k] != v for k, v in NATIVE_OPTIONS.items())):
        fail()
    if type(assets) is not list or len(assets) != 1 or type(assets[0]) is not dict:
        fail()
    asset = assets[0]
    if (set(asset) - {'name', 'mime', 'timestamp_ms', 'base64', 'dataUrl'} or
            asset.get('name') != 'source-video' or asset.get('mime') != 'video/mp4' or
            type(asset.get('timestamp_ms')) is not int or asset['timestamp_ms'] != 0 or
            ('dataUrl' in asset and asset['dataUrl'] != '') or
            type(asset.get('base64')) is not str or not 0 < len(asset['base64']) <= 16 * 1024 * 1024):
        fail()
    mode = meta.get('transport')
    if ('transport' in meta and mode != NATIVE_FILE_TRANSPORT) or (
            mode == NATIVE_FILE_TRANSPORT and len(asset['base64']) < MAX_NATIVE_ENCODED):
        fail()
    if transport and len(asset['base64']) >= MAX_NATIVE_ENCODED and mode != NATIVE_FILE_TRANSPORT:
        fail()
    try:
        raw = base64.b64decode(asset['base64'], validate=True)
    except (ValueError, binascii.Error):
        fail()
    if (base64.b64encode(raw).decode('ascii') != asset['base64'] or
            len(raw) != meta['source_bytes'] or raw[4:8] != b'ftyp' or
            hashlib.sha256(raw).hexdigest() != meta['source_sha256']):
        fail()


def validate_native_asset(payload, assets):
    return _validate_native_asset(payload, assets)


def _load_native_video(source_path, duration_ms):
    with Path(source_path).open('rb') as source:
        raw = source.read(MAX_NATIVE_BYTES + 1)
    if len(raw) > MAX_NATIVE_BYTES:
        raise ValueError('video_native_asset_invalid')
    metadata = {'source_sha256': hashlib.sha256(raw).hexdigest(), 'source_bytes': len(raw),
                'duration_ms': duration_ms, **NATIVE_OPTIONS}
    assets = [{'name': 'source-video', 'mime': 'video/mp4', 'base64': base64.b64encode(raw).decode('ascii'),
               'timestamp_ms': 0}]
    _validate_native_asset({'phase': 'whole-timeline', 'video': metadata}, assets, transport=False)
    return metadata, assets


def load_native_video(source_path, duration_ms):
    metadata, assets = _load_native_video(source_path, duration_ms)
    validate_native_asset({'phase':'whole-timeline', 'video':metadata}, assets)
    return metadata, assets


def prepare_optional_native_video(source_path, duration_ms):
    """Select the closed temporary-file capability for large validated originals."""
    metadata, assets = _load_native_video(source_path, duration_ms)
    if len(assets[0]['base64']) >= MAX_NATIVE_ENCODED:
        metadata['transport'] = NATIVE_FILE_TRANSPORT
    validate_native_asset({'phase': 'whole-timeline', 'video': metadata}, assets)
    return metadata, assets, None


def validate_timeline_output(value, duration_ms):
    """Bind only scene time bounds; timeline narrative never gains execution authority."""
    def fail():
        raise ValueError('video_timeline_output_invalid')
    if (type(duration_ms) is not int or not 0 < duration_ms <= 600000 or
            type(value) is not dict or set(value) != {'scenes', 'questions'} or
            type(value['scenes']) is not list or len(value['scenes']) > 60 or
            type(value['questions']) is not list or len(value['questions']) > 40 or
            any(type(q) is not str or len(q) > 2000 for q in value['questions'])):
        fail()
    for scene in value['scenes']:
        if (type(scene) is not dict or set(scene) != {'kind', 'start_ms', 'end_ms', 'description'} or
                type(scene['kind']) is not str or scene['kind'] not in {'input', 'navigation', 'interaction', 'wait'} or
                type(scene['start_ms']) is not int or type(scene['end_ms']) is not int or
                not 0 <= scene['start_ms'] <= scene['end_ms'] <= duration_ms or
                type(scene['description']) is not str or len(scene['description']) > 2000):
            fail()
    return value
