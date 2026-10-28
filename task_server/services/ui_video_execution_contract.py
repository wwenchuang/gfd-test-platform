"""Trusted, opt-in video semantics; public proposals cannot select an executor."""
import copy
import hashlib
import json
import re

VIDEO_EXPLICIT_KEYBOARD_V1 = 'video-explicit-keyboard-v1'


def validate_execution_contract(value):
    if value is not None and (type(value) is not str or value != VIDEO_EXPLICIT_KEYBOARD_V1):
        raise ValueError('video_execution_contract_invalid')
    return value


def source_execution_contract(document):
    """Only the server trajectory result can install a contract in an origin."""
    mode = validate_execution_contract(document.get('execution_contract'))
    if mode is not None:
        audit = document.get('trajectory_audit')
        if (not isinstance(audit, dict) or audit.get('version') != 'trajectory-v2' or
                audit.get('execution_contract') != mode):
            raise ValueError('video_execution_contract_invalid')
    return mode


def compiler_options(snapshot, app_id, steps):
    """Derive options from immutable private origin and exact step provenance."""
    mode = validate_execution_contract(snapshot.get('execution_contract'))
    if mode is None:
        return {}
    source = snapshot.get('source_id'); version = snapshot.get('analysis_version')
    if (snapshot.get('kind') != 'video' or snapshot.get('app_id') != app_id or
            not isinstance(source, str) or not re.fullmatch(r'uvs_[0-9a-f]{32}', source) or
            type(version) is not int or version < 1 or
            snapshot.get('snapshot_id') != hashlib.sha256(json.dumps([source, version]).encode()).hexdigest() or
            not isinstance(snapshot.get('events'), list)):
        raise ValueError('video_execution_contract_invalid')
    provenance = {}
    for index, event in enumerate(snapshot['events']):
        if not isinstance(event, dict):
            raise ValueError('video_execution_contract_invalid')
        for key in ('draft_step_id', 'expected_check_step_id', 'selection_step_id'):
            if event.get(key):
                provenance[event[key]] = f'video:{source}:{version}:{index}'
    for step in steps:
        ref = step.get('source_ref', 'text')
        if (ref.startswith('video:') or step.get('step_id') in provenance) and provenance.get(step.get('step_id')) != ref:
            raise ValueError('video_execution_contract_invalid')
    return {'execution_contract': mode}


# Explicit positive projection: observer narrative, questions and hypothetical
# extensions are never audit evidence. Outcome/state predicates remain claims
# for the critic to contradict, including predicates that may be false.
_CLAIM_KEYS = frozenset({'track', 'event', 'fragments', 'event_id', 'canonical_event_id', 'packet_id',
    'action', 'target', 'value', 'frame_refs', 'trigger_frame', 'trigger_kind', 'page_before', 'page_after',
    'continuation_of', 'edit_boundary', 'outcome', 'start_ms', 'end_ms', 'evidence_kind',
    'required_frame_refs', 'allowed_frame_refs', 'reference_compilation', 'raw_frame_refs', 'original_frame_refs',
    'selected_frame_refs', 'required_anchors', 'method', 'version', 'exact_outcome', 'state_claim',
    'predicate', 'predicates', 'object_id', 'return_id', 'continuation_id'})


def project_audit_claims(value, *, outcome=False):
    if isinstance(value, list):
        return [project_audit_claims(item, outcome=outcome) for item in value]
    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if key not in _CLAIM_KEYS and not (outcome and key == 'observation'):
                continue
            if key == 'value' and value.get('action') == 'aiScroll' and isinstance(item, dict):
                # Canonical scroll values have already passed action validation.
                # Preserve their typed parameters, rather than treating them as
                # claim metadata or admitting arbitrary nested narrative fields.
                projected[key] = {name: copy.deepcopy(parameter) for name, parameter in item.items()
                                  if name in {'direction', 'distance', 'scrollType'}}
            else:
                projected[key] = project_audit_claims(item, outcome=key in {'outcome', 'exact_outcome'})
        return projected
    return copy.deepcopy(value)
