"""Unused, opt-in source metadata binding for parsed perception inventories.

This validates transport shapes, never pixels or actions. Hashes describe parsed
JSON, not original transport bytes. No caller is wired into the default worker.
"""
import copy
import hashlib
import json
import re


_ROW_FIELDS = {
    'frame_id', 'role', 'transient_marks', 'persistent_ui', 'field_state',
    'keyboard_state', 'changes', 'context_refs', 'uncertainties',
}
_MARK_FIELDS = {'shape', 'color', 'position', 'nearest_visible_control', 'uncertainty'}
_PERSISTENT_UI_FIELDS = {'shape', 'color', 'position', 'nearest_visible_control'}
_HASH_DEFINITION = (
    'sha256(UTF-8 JSON; sorted keys; compact separators; '
    'ensure_ascii=false; allow_nan=false)'
)


def project_inventory_format(raw):
    """Losslessly wrap only three known string/list paths, never validate or repair.

    The explicit perception caller must still validate the full canonical schema
    and exact source binding. Metadata contains paths/types only, no fact copies.
    Bound traversal by the explicit stage 12-image limit; over-limit rows remain
    unchanged and fail exact source binding, never silently lose trailing rows.
    """
    result = copy.deepcopy(raw)
    audit = []
    def wrap(container, key, path):
        if type(container.get(key)) is str:
            container[key] = [container[key]]
            audit.append({'path': path, 'reason': 'singleton_string_array',
                          'from_type': 'string', 'to_type': 'array'})
    if isinstance(result, dict):
        wrap(result, 'uncertainties', '$.uncertainties')
        rows = result.get('frames')
        if isinstance(rows, list) and len(rows) <= 12:
            for i, row in enumerate(rows):
                if isinstance(row, dict):
                    for key in ('persistent_ui', 'uncertainties'):
                        wrap(row, key, f'$.frames[{i}].{key}')
    return result, audit


def _keys(value, expected, path):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f'{path}: invalid fields')


def _text(value, path, maximum=8192):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f'{path}: expected bounded string')


def _list(value, path, maximum=128):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{path}: expected bounded list')


def _texts(value, path):
    _list(value, path)
    for item in value:
        _text(item, path)


def _persistent_ui(value, path):
    _list(value, path)
    for i, item in enumerate(value):
        item_path = f'{path}[{i}]'
        if isinstance(item, dict):
            _keys(item, _PERSISTENT_UI_FIELDS, item_path)
            for key in _PERSISTENT_UI_FIELDS:
                _text(item[key], f'{item_path}.{key}')
        else:
            _text(item, item_path)


def _role(value, path):
    _text(value, path)
    if value not in {'owned', 'context'}:
        raise ValueError(f'{path}: invalid role')


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')).hexdigest()


def _source_index(packet):
    if not isinstance(packet, dict):
        raise ValueError('packet: expected object')
    packet_id = packet.get('packet_id')
    _text(packet_id, 'packet.packet_id', 128)
    if not packet_id:
        raise ValueError('packet.packet_id: empty')
    frames = packet.get('frames')
    _list(frames, 'packet.frames', 1000)
    if not frames:
        raise ValueError('packet.frames: empty')
    positions = {}
    previous_time = -1
    for i, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError('packet.frames: expected frame object')
        frame_id = frame.get('frame_id')
        if not isinstance(frame_id, str) or not re.fullmatch(r'frame-(?:[0-9]{3}|end)', frame_id):
            raise ValueError('packet.frames: invalid canonical frame ID')
        timestamp = frame.get('timestamp_ms')
        if type(timestamp) is not int or timestamp <= previous_time:
            raise ValueError('packet.frames: timestamps must be nonnegative increasing integers')
        if frame_id in positions:
            raise ValueError('packet.frames: duplicate frame ID')
        positions[frame_id] = i
        previous_time = timestamp
    partitions = {}
    for role in ('owned', 'context'):
        ids = packet.get(f'{role}_frame_ids')
        _list(ids, f'packet.{role}_frame_ids', 1000)
        previous_position = -1
        for frame_id in ids:
            if not isinstance(frame_id, str) or frame_id not in positions:
                raise ValueError(f'packet.{role}_frame_ids: unknown frame ID')
            if positions[frame_id] <= previous_position or frame_id in partitions:
                raise ValueError('packet: duplicate, overlapping or unordered partition')
            partitions[frame_id] = role
            previous_position = positions[frame_id]
    if set(partitions) != set(positions):
        raise ValueError('packet: incomplete ownership partition')
    # Only authoritative identity, chronology and partition enter this proof.
    index = {'packet_id': packet_id, 'frames': [
        {'frame_id': f['frame_id'], 'timestamp_ms': f['timestamp_ms'],
         'role': partitions[f['frame_id']]} for f in frames
    ]}
    return index, partitions


def bind_inventory(raw, packet, *, allow_numeric_suffix_aliases=False):
    """Return a new fact bank; exact IDs by default, proven suffix aliases opt-in.

    Text fields are bounded to 8192 characters; visual/question lists to 128.
    Persistent UI entries are strings or exact four-field visual descriptions;
    their original structure and values are copied without interpretation.
    The source index and reference lists allow at most 1000 frames (the complete
    three-digit namespace). Roles always come from the disjoint source partition.
    ``normalized_bank_sha256`` excludes binding_audit to avoid a recursive hash.
    Warnings describe projections only and never indicate visual qualification.
    """
    if type(allow_numeric_suffix_aliases) is not bool:
        raise ValueError('allow_numeric_suffix_aliases: expected boolean')
    index, roles = _source_index(packet)
    _keys(raw, {'packet_id', 'frames', 'uncertainties'}, 'inventory')
    if raw['packet_id'] != index['packet_id']:
        raise ValueError('inventory.packet_id: mismatch')
    _texts(raw['uncertainties'], 'inventory.uncertainties')
    _list(raw['frames'], 'inventory.frames', 1000)
    owned_ids = packet['owned_frame_ids']
    if len(raw['frames']) != len(owned_ids):
        raise ValueError('inventory.frames: incomplete or extra owned rows')
    aliases = {}
    for frame_id in roles:
        # The decoder's final anchor is exact-only, never an "end" alias.
        if frame_id == 'frame-end':
            continue
        suffix = frame_id[-3:]
        if suffix in aliases or suffix in roles:
            raise ValueError('packet: ambiguous suffix alias')
        aliases[suffix] = frame_id
    warnings = []

    def project(original, canonical, path, reason):
        if original != canonical:
            warnings.append({'path': path, 'original': original,
                             'canonical': canonical, 'reason': reason})
        return canonical

    def resolve(label, path):
        if not isinstance(label, str):
            raise ValueError(f'{path}: expected frame ID string')
        if label in roles:
            return label
        if allow_numeric_suffix_aliases and label in aliases:
            return project(label, aliases[label], path, 'numeric_suffix_alias')
        raise ValueError(f'{path}: unknown frame ID')

    rows = []
    for i, (row, expected_id) in enumerate(zip(raw['frames'], owned_ids)):
        path = f'frames[{i}]'
        _keys(row, _ROW_FIELDS, path)
        frame_id = resolve(row['frame_id'], f'{path}.frame_id')
        if frame_id != expected_id:
            raise ValueError(f'{path}.frame_id: wrong owned row or order')
        _role(row['role'], f'{path}.role')
        role = project(row['role'], roles[frame_id], f'{path}.role', 'source_partition_role')
        _list(row['transient_marks'], f'{path}.transient_marks')
        for mark in row['transient_marks']:
            _keys(mark, _MARK_FIELDS, f'{path}.transient_marks')
            for key in _MARK_FIELDS:
                _text(mark[key], f'{path}.transient_marks.{key}')
        _persistent_ui(row['persistent_ui'], f'{path}.persistent_ui')
        _texts(row['uncertainties'], f'{path}.uncertainties')
        for key in ('field_state', 'keyboard_state', 'changes'):
            _text(row[key], f'{path}.{key}')
        _list(row['context_refs'], f'{path}.context_refs', 1000)
        refs, seen = [], set()
        for j, ref in enumerate(row['context_refs']):
            ref_path = f'{path}.context_refs[{j}]'
            _keys(ref, {'frame_id', 'role'}, ref_path)
            ref_id = resolve(ref['frame_id'], f'{ref_path}.frame_id')
            if ref_id in seen:
                raise ValueError(f'{ref_path}: duplicate canonical reference')
            seen.add(ref_id)
            _role(ref['role'], f'{ref_path}.role')
            ref_role = project(ref['role'], roles[ref_id], f'{ref_path}.role',
                               'source_partition_role')
            refs.append({'frame_id': ref_id, 'role': ref_role})
        bound = copy.deepcopy(row)
        del bound['context_refs']
        bound.update(frame_id=frame_id, role=role, evidence_refs=refs)
        rows.append(bound)
    bank = {'packet_id': index['packet_id'], 'frames': rows,
            'uncertainties': copy.deepcopy(raw['uncertainties'])}
    bank['binding_audit'] = {
        'hash_definition': _HASH_DEFINITION,
        'raw_inventory_sha256': _hash(raw),
        'authoritative_index_sha256': _hash(index),
        'normalized_bank_sha256': _hash(bank),
        'warnings': warnings, 'pixel_verified': False, 'qualified': False,
    }
    return bank
