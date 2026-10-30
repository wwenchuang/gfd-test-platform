"""Server-selected video runtimes sharing the original trajectory and tail.

This module never writes a source. Its parent supplies fresh authorization and
version/owner/app CAS publication. Model jobs alone own bounded transport; the
bank and native timeline contain unverified proposals, never accepted actions or input values.
"""
import copy
import json
import os
from pathlib import Path
import re
import time

from .ui_video_perception_binding import bind_inventory, project_inventory_format, _source_index

MODE = 'video-perception-bank-v1'
BUSINESS_MODE = 'video-business-first-v1'


def runtime_mode():
    return BUSINESS_MODE if os.getenv('UI_VIDEO_RUNTIME_BUSINESS_FIRST') == '1' else MODE


def selected(identifier):
    value = os.getenv('UI_VIDEO_RUNTIME_QA_SOURCE_ID', '')
    return bool(re.fullmatch(r'uvs_[0-9a-f]{32}', value) and value == identifier)


def producer_windows(owned, universe, *, prefix='original'):
    """Eight actual owned originals and nearest two timeline neighbors each side."""
    positions = {f['frame_id']: i for i, f in enumerate(universe)}
    windows = []
    for start in range(0, len(owned), 8):
        group = owned[start:start + 8]
        left, right = positions[group[0]['frame_id']], positions[group[-1]['frame_id']]
        ids = {f['frame_id'] for f in group}
        # Overlay windows must not absorb intervening originals as owned rows.
        context = universe[max(0, left - 2):left] + universe[right + 1:right + 3]
        images = sorted(group + context, key=lambda f: f['timestamp_ms'])
        windows.append({'packet_id': f'producer-{prefix}-{len(windows) + 1:03d}',
            'frames': copy.deepcopy(images), 'owned_frame_ids': [f['frame_id'] for f in group],
            'context_frame_ids': [f['frame_id'] for f in images if f['frame_id'] not in ids]})
    return windows


class PerceptionBank:
    def __init__(self, frames):
        self.frames = copy.deepcopy(frames)
        # Reuse the exact authoritative ID/time/partition validator before any job.
        _source_index({'packet_id': 'original', 'frames': self.frames,
                       'owned_frame_ids': [f['frame_id'] for f in frames], 'context_frame_ids': []})
        self.windows = producer_windows(self.frames, self.frames)
        self.original_count = len(self.windows)
        self.entries = []
        self.overlays = 0

    def add_frames(self, extra):
        if not extra:
            return []
        universe = sorted(self.frames + copy.deepcopy(extra), key=lambda f: f['timestamp_ms'])
        _source_index({'packet_id': 'augmented', 'frames': universe,
                       'owned_frame_ids': [f['frame_id'] for f in universe], 'context_frame_ids': []})
        self.overlays += 1
        windows = producer_windows(extra, universe, prefix=f'revisit-{self.overlays}')
        self.frames = universe
        return windows

    def admit(self, window, raw, accounting):
        bound = bind_inventory(raw, window)
        if any(w['reason'] != 'source_partition_role' for w in bound['binding_audit']['warnings']):
            raise ValueError('video_perception_binding_invalid')
        index, _ = _source_index(window)
        first = next(i for i, f in enumerate(self.frames) if f['frame_id'] == window['frames'][0]['frame_id'])
        entry = {'window_id': window['packet_id'], 'source_index': index,
            'predecessor_frame_id': self.frames[first - 1]['frame_id'] if first else None,
            'frames': bound['frames'], 'uncertainties': bound['uncertainties'],
            'binding_audit': bound['binding_audit'],
            **{k: copy.deepcopy(accounting[k]) for k in ('proof_ref', 'receipt_ref') if k in accounting}}
        self.entries.append(entry)
        times={f['frame_id']:f['timestamp_ms'] for f in self.frames}
        self.entries.sort(key=lambda item:times[item['frames'][0]['frame_id']])

    def project(self, packet):
        refs = {f['frame_id'] for f in packet['frames']}
        owned = set(packet['owned_frame_ids'])
        windows = []
        for entry in self.entries:
            rows = [row for row in entry['frames'] if row['frame_id'] in refs]
            if rows:
                windows.append({**copy.deepcopy(entry), 'frames': copy.deepcopy(rows)})
        return {'classification': 'unverified_pixel_proposals', 'windows': windows,
                'packet_roles': {f['frame_id']: 'owned' if f['frame_id'] in owned else 'context' for f in packet['frames']}}

    def index(self):
        # Raw content is stored once by the jobs receipt hook. Private provenance
        # references it instead of repeating all inventory content in source state.
        return [{k: copy.deepcopy(v) for k, v in entry.items() if k not in {'frames', 'uncertainties'}}
                for entry in self.entries]


class RuntimeBudget:
    def __init__(self, original_count, *, clock=time.monotonic, business=False):
        self.clock = clock
        self.deadline = clock() + (4500 if business else 1800)
        self.original_count = original_count
        self.limit = 80
        self.reserved = 0
        self.frozen = False
        self._engine_deadline = None
        self._engine_started = False

    def check(self):
        # ModelJobs reserves its own 0.5s cleanup inside this same deadline.
        if self.clock() >= min(self.deadline - .5, self._engine_deadline or self.deadline):
            raise TimeoutError('video_runtime_deadline')

    def reserve(self):
        self.check()
        if self.reserved >= self.limit:
            raise ValueError('video_vision_limit')
        self.reserved += 1

    def freeze_packets(self, count, *, business=False):
        if not self.frozen:
            if type(count) is not int or count < 1:
                raise ValueError('video_vision_limit')
            self.limit = min(80, self.original_count + 2 * count + (20 if business else 12))
            self.frozen = True
            if self.reserved > self.limit:
                raise ValueError('video_vision_limit')

    def start_engine(self, *, business=False):
        self.check()
        if self._engine_started:
            raise ValueError('video_runtime_engine_started')
        self._engine_started = True
        # Business retains four bounded120s tail calls plus cleanup on the same
        # original source clock; injected smaller budgets are never extended.
        self._engine_deadline = self.deadline - 480.5 if business else self.clock() + 1200
        self.check()

    def end_engine(self):
        self._engine_deadline = None

    def engine_deadline(self):
        return self._engine_deadline


def _semantic_schema(payload):
    from .ai_skill_service import load_ai_skill_schema
    from .ui_video_stage_policy import _provider_projection, validate_output_schema
    schema = _provider_projection(load_ai_skill_schema('ui_video_trajectory')['oneOf'][0])
    refs = [f['frame_id'] for f in payload['frames']]
    owned = payload['owned_frame_ids']
    prior = ['']
    history = payload.get('prior_events', [])
    if history and history[-1].get('event', {}).get('action') == 'aiInput' and history[-1].get('edit_boundary') == 'none':
        prior.append(history[-1]['event_id'])
    def narrow(node):
        for key, child in node.get('properties', {}).items():
            if key in {'first_frame', 'last_frame'}:
                child['enum'] = refs
            elif key == 'trigger_frame':
                child['enum'] = owned
            elif key == 'frame_refs':
                child['items']['enum'] = refs
            elif key == 'continuation_of':
                child['enum'] = list(dict.fromkeys(prior))
            narrow(child)
        if 'items' in node:
            narrow(node['items'])
        for child in node.get('anyOf', []):
            narrow(child)
    narrow(schema)
    return validate_output_schema(schema)


def _audit_schema(payload):
    """Narrow canonical audit leaves to this dispatch's images, owners and shard."""
    from .ai_skill_service import load_ai_skill_schema
    from .ui_video_stage_policy import _provider_projection, validate_output_schema
    schema = _provider_projection(load_ai_skill_schema('ui_video_trajectory')['oneOf'][1])
    refs = [f['frame_id'] for f in payload['frames']]
    owned = payload['owned_frame_ids']
    ids = [claim['track']['event_id'] for claim in payload['trajectory']]
    props = schema['properties']
    props['issues']['items']['properties']['owner_frame']['enum'] = list(owned)
    props['issues']['items']['properties']['frame_refs']['items']['enum'] = refs
    for name in ('first_frame', 'last_frame'):
        props['revisit']['items']['properties'][name]['enum'] = refs
    if ids:
        props['checks']['items']['properties']['event_id']['enum'] = ids
    return validate_output_schema(schema, stage='audit')


def run_runtime(document, workspace, source_path, check, publish, *, budget=None, mode=None):
    # The worker freezes selection under source/version CAS. Default callers keep
    # the bank path even if the process environment later changes.
    if mode == BUSINESS_MODE:
        from .ui_video_business_first import run_business_runtime
        return run_business_runtime(document, workspace, source_path, check, publish,
                                    budget=budget or RuntimeBudget(0, business=True))
    if mode not in (None, MODE):
        raise ValueError('video_runtime_mode_invalid')
    return _run_runtime(document, workspace, source_path, check, publish, budget=budget)


def _native_transport_unavailable(error, owner, records):
    """Only terminal, durable timeline transport failures qualify; content is ignored."""
    from .ui_video_model_jobs import LocalModelJobDeadline, ModelJobError
    if (owner.active_pids or owner.cleanup_failures or len(owner.accounting) != 1):
        return None
    row = owner.accounting[0]
    if (any(row.get(k) is not True for k in ('reserved', 'proof_persisted', 'dispatch_intent', 'terminal', 'cleanup')) or
            row.get('admitted') is not False or not row.get('proof_ref')):
        return None
    proof = records.read(row['proof_ref'])
    if (proof.get('stage'), proof.get('skill'), proof.get('skill_version')) != ('timeline', 'ui_video_timeline', 'v1'):
        return None
    refs = {'proof_ref': copy.deepcopy(row['proof_ref'])}
    if isinstance(error, LocalModelJobDeadline) and not row.get('raw_received') and not row.get('receipt_persisted') and not row.get('receipt_ref'):
        reason = 'local_job_deadline'
    elif type(error) is ModelJobError and str(error) == 'video_job_failed' and row.get('raw_received') and row.get('receipt_persisted') and row.get('receipt_ref'):
        receipt = records.read(row['receipt_ref'])
        status = receipt.get('http_status')
        if (receipt.get('gateway_request_sha256') != proof['gateway_request_sha256'] or
                receipt.get('rejection_code') != 'video_stage_provider_failed' or
                type(status) is not int or not (status == 429 or 500 <= status <= 599) or
                any(receipt.get(k) is not None for k in ('providerId', 'model', 'fallbackUsed', 'videoPolicy'))):
            return None
        reason = 'temporary_provider_failure'
        refs['receipt_ref'] = copy.deepcopy(row['receipt_ref'])
    else:
        return None
    return {'classification': 'native_hypotheses_unavailable', 'instruction_authority': False,
            'reason': reason, **refs,
            'feedback': '可选视频传输不可用，没有可用原生假设。必须完整观察原始帧并独立审核，不得推断或补造动作。'}


def _run_runtime(document, workspace, source_path, check, publish, *, budget=None, business=False):
    """Return original analysis shape; parent remains the sole source publisher.

    check reauthorizes source binding and may raise; publish performs the same
    check and owner/app/version/status CAS. Only injected tests replace normal
    helpers. Production always uses owned jobs, bounded records and original
    trajectory, consolidation, outcome review and final input recovery.
    """
    from .ui_video_records import RecordStore
    from .ui_video_model_jobs import ModelJobs
    from .ui_video_windows import load_assets
    from .ui_video_trajectory import observe_trajectory
    from .ui_video_audit_batches import partition_audit_claims
    from .ui_video_audit_sampling import select_audit_fine_frames
    from .ui_video_execution_contract import VIDEO_EXPLICIT_KEYBOARD_V1
    from .ui_video_revisit import make_revisit_loader
    from .ui_video_analysis import consolidate_observed_frames
    from .ui_video_business_inputs import recover_business_inputs
    from .ui_video_stage_policy import STAGES

    bank = PerceptionBank(document['media']['frames'])
    budget = budget or RuntimeBudget(0 if business else bank.original_count, business=business)
    if business and budget.original_count != 0:
        raise ValueError('video_runtime_budget_invalid')
    mode = BUSINESS_MODE if business else MODE
    business_timeline = None
    media = copy.deepcopy(document['media'])
    perception_format_projections = []
    trajectory_diagnostics = []
    def boundary():
        check()
        budget.check()
    def cancelled():
        boundary()
        return False
    def progress(phase, completed, total, events):
        boundary()
        if phase == 'observing':
            budget.freeze_packets(total,business=business)
        publish(media=copy.deepcopy(media), raw_events=events,
                analysis_progress={'phase': phase, 'completed': completed, 'total': total})
        boundary()

    boundary()
    with RecordStore(Path(workspace) / 'model-records') as records:
        jobs = ModelJobs(records, deadline=budget.deadline, reserve=budget.reserve,
                         check=boundary, engine_deadline=budget.engine_deadline)
        native_jobs = None
        try:
            with jobs:
                def produce(windows, initial=False):
                    offset = len(jobs.accounting)
                    def builders():
                        for window_index, window in enumerate(windows):
                            def receipt(value, window=window, window_index=window_index):
                                # ModelJobs invokes this only after durable raw storage.
                                # Parse/projection never changes that original receipt.
                                from .ai_skill_service import normalize_model_json
                                try:
                                    parsed = normalize_model_json(value['content'])
                                except json.JSONDecodeError:
                                    return  # Child retains the original invalid-JSON rejection.
                                _, audit = project_inventory_format(parsed)
                                if audit:
                                    perception_format_projections.append({
                                        'window_id': window['packet_id'], 'projections': audit,
                                        'receipt_ref': copy.deepcopy(jobs.accounting[offset + window_index]['receipt_ref'])})
                            def build(window=window, receipt=receipt):
                                boundary()
                                payload = {'phase': 'whole-perception', **copy.deepcopy(window),
                                    'image_frames': copy.deepcopy(window['frames'])}
                                assets = load_assets(window['frames'], workspace, cancelled)
                                boundary()
                                return 'ui_video_perception', payload, dict(version='v1', video_stage='perception',
                                    model_config={'providerId':'qwen_plus','model':STAGES['perception'][3]},
                                    image_assets=assets, timeout=120, retry_count=0, max_tokens=STAGES['perception'][4],
                                    repair_invalid_json=False, raw_receipt=receipt)
                            yield build
                    iterator = jobs.map(builders(), concurrency=4)
                    try:
                        for index, raw in iterator:
                            boundary()
                            if len(jobs.accounting)<=offset+index:
                                raise ValueError('video_perception_receipt_missing')
                            row = jobs.accounting[offset + index]
                            if not (row.get('admitted') and row.get('receipt_persisted') and
                                    row.get('proof_ref') and row.get('receipt_ref')):
                                raise ValueError('video_perception_receipt_missing')
                            bank.admit(windows[index], raw, row)
                            if initial:
                                progress('perceiving', len(bank.entries), bank.original_count, [])
                    finally:
                        iterator.close()

                def runner(skill, payload, **kwargs):
                    boundary()
                    payload = copy.deepcopy(payload)
                    version = kwargs.get('version', 'v1')
                    stage = next((name for name, rule in STAGES.items() if (skill, version) == rule[:2]), None)
                    if stage is None:
                        raise ValueError('video_stage_scope_invalid')
                    if business and stage == 'semantic':
                        from .ui_video_trajectory import action_contract
                        payload['action_contract'] = action_contract()
                        stage = 'grounding'
                        if not budget.frozen:
                            raise ValueError('video_runtime_packets_missing')
                        kwargs['output_schema'] = _semantic_schema(payload)
                    elif stage == 'semantic':
                        if not budget.frozen:
                            raise ValueError('video_runtime_packets_missing')
                        payload.pop('image_frames', None)
                        payload['perception_bank'] = bank.project(payload)
                        kwargs.update(image_assets=[], output_schema=_semantic_schema(payload))
                    elif stage == 'audit' and business:
                        kwargs['output_schema'] = _audit_schema(payload)
                        # Multimodal providers may ignore json_schema; repeat trusted
                        # domains in prompt input while retaining local validation.
                        payload['reference_contract'] = {
                            'version': 'packet-audit-reference-v1',
                            'check_event_ids': [c['track']['event_id'] for c in payload['trajectory']],
                            'issue_owner_frame_ids': list(payload['owned_frame_ids']),
                            'image_frame_ids': [f['frame_id'] for f in payload['frames']]}
                    elif stage == 'audit' and not business:
                        projection = bank.project(payload)
                        # Sampler takes current roles; immutable producer roles in
                        # the bank remain untouched and are not sent as new claims.
                        facts = [{**copy.deepcopy(row), 'role': projection['packet_roles'][row['frame_id']]}
                                 for window in projection['windows'] for row in window['frames']]
                        fine = select_audit_fine_frames(payload['frames'], payload['trajectory'], facts, payload['owned_frame_ids'])
                        payload.update(perception_mode='fine-detail',
                                       perception_frame_ids=fine or [payload['owned_frame_ids'][0]])
                    kwargs['model_config']={'providerId':'qwen_plus','model':STAGES[stage][3]}
                    offset=len(jobs.accounting)
                    try:
                        return jobs.run(skill, payload, **kwargs, video_stage=stage)
                    finally:
                        trace=kwargs.get('runtime_trace')
                        if isinstance(trace,dict) and len(jobs.accounting)>offset:
                            trace['video_record_refs']={k:copy.deepcopy(jobs.accounting[offset][k])
                                for k in ('proof_ref','receipt_ref') if k in jobs.accounting[offset]}

                if business:
                    from .ui_video_native import prepare_optional_native_video
                    from .ui_video_business_first import validate_business_timeline
                    boundary()
                    metadata, assets, business_timeline = prepare_optional_native_video(source_path, media['duration_ms'])
                    boundary()
                    progress('understanding', 0, 1, [])
                    if business_timeline is None:
                        native_jobs = ModelJobs(records, deadline=budget.deadline, reserve=budget.reserve,
                            check=boundary, engine_deadline=budget.engine_deadline)
                        native_error = None
                        try:
                            with native_jobs:
                                native = native_jobs.run('ui_video_timeline',
                                    {'phase':'whole-timeline', 'video':metadata},
                                    version='v1', image_assets=assets, timeout=120, retry_count=0,
                                    max_tokens=10000, repair_invalid_json=False, video_stage='timeline',
                                    model_config={'providerId':'qwen_plus','model':STAGES['timeline'][3]})
                        except Exception as error:
                            native_error = error
                        boundary()
                        if native_error is not None:
                            business_timeline = _native_transport_unavailable(native_error, native_jobs, records)
                            if business_timeline is None:
                                raise native_error
                        # Keep durable provenance even when returned scene bounds fail.
                        row = native_jobs.accounting[0]
                        if native_error is None:
                            business_timeline = {'classification':'unverified_business_hypotheses',
                                'instruction_authority':False,
                                **{k:copy.deepcopy(row[k]) for k in ('proof_ref','receipt_ref') if k in row}}
                            boundary()
                            if not (row.get('admitted') and row.get('receipt_persisted') and
                                    row.get('proof_ref') and row.get('receipt_ref')):
                                raise ValueError('video_timeline_receipt_missing')
                            validate_business_timeline(native, media['duration_ms'])
                            business_timeline['timeline'] = copy.deepcopy(native)
                    progress('understanding', 1, 1, [])
                else:
                    produce(bank.windows, initial=True)
                boundary()
                loader = make_revisit_loader(source_path, workspace, bank.frames, cancelled)
                def revisit(request, capacity, *, owned_time_range_ms):
                    boundary()
                    extra = loader(request, capacity, owned_time_range_ms=owned_time_range_ms)
                    boundary()
                    windows = bank.add_frames(extra)
                    media['frames'] = copy.deepcopy(bank.frames)
                    # Make matching media available before any progress/event refs,
                    # even if overlay perception or a later original tail fails.
                    publish(media=copy.deepcopy(media))
                    if not business:
                        produce(windows)
                    return extra
                budget.start_engine(business=business)
                observed = observe_trajectory(document, workspace, cancelled, progress,
                    revisit_bounded=revisit, skill_runner=runner, audit_batcher=partition_audit_claims,
                    execution_contract=VIDEO_EXPLICIT_KEYBOARD_V1,diagnostics=trajectory_diagnostics,
                    **({'business_timeline':business_timeline,'engine_deadline':budget.engine_deadline()} if business else {}))
                budget.end_engine()
                boundary()
                media = copy.deepcopy(observed['media'])
                trusted = {'media': media, 'execution_contract': VIDEO_EXPLICIT_KEYBOARD_V1,
                           'trajectory_audit': copy.deepcopy(observed['trajectory_audit'])}
                augmented = {**document, **trusted}
                publish(**trusted)
                first_pass = copy.deepcopy(observed['events'])
                result = consolidate_observed_frames(augmented, workspace, cancelled, observed,
                    first_pass, [], progress, skill_runner=runner)
                result.update(trusted)
                progress('input_recovery', 0, 1, result['events'])
                result = recover_business_inputs(result, augmented, workspace, cancelled, skill_runner=runner)
                boundary()
            boundary()
        except Exception as error:
            # Preserve the original exception and exact local capacity cause.
            # Private metadata links back to durable replies; no model text is
            # copied into an error code or used to authorize a new action.
            previous=trajectory_diagnostics[-1] if trajectory_diagnostics else {}
            reason=(previous.get('reason') if previous.get('kind') in {'capacity_refusal','acquisition_failure'} else None)
            # A callback also runs overlay jobs: its generic acquisition label
            # must not mask the actual deadline or exhausted provider allowance.
            if reason in {None,'acquisition_failed'}:
                reason=('runtime_deadline' if isinstance(error,TimeoutError) and str(error)=='video_runtime_deadline' else
                        'model_job_deadline' if isinstance(error,TimeoutError) and str(error)=='video_job_deadline' else
                        'provider_allowance' if str(error)=='video_vision_limit' and budget.reserved>=budget.limit else
                        reason or 'runtime_failed')
            if len(trajectory_diagnostics)<64:
                trajectory_diagnostics.append({'kind':'runtime_failure','reason':reason,
                    **{k:copy.deepcopy(previous[k]) for k in ('stage','packet_id','call','proof_ref','receipt_ref') if k in previous},
                    'reserved':budget.reserved,'limit':budget.limit,
                    'acquired_frames':len(bank.frames)-len(document['media']['frames'])})
            raise
        finally:
            # All owned jobs have closed before the manifest is frozen. A failed
            # store latches and raises here; never claim an unpersisted raw receipt.
            accounting = (native_jobs.accounting if native_jobs is not None else []) + jobs.accounting
            refs = [records.append('status', {**row, 'index': index}) for index, row in enumerate(accounting)]
            aggregate = records.append('aggregate', {'refs': refs})
            private = {'mode': mode, 'windows': bank.index(), 'records': records.snapshot(),
                       'perception_format_projections': copy.deepcopy(perception_format_projections),
                       'accounting_ref': aggregate, 'trajectory_diagnostics': copy.deepcopy(trajectory_diagnostics)}
            if business:
                private['business_timeline'] = copy.deepcopy(business_timeline)
                private['job_owners'] = [
                    {'scope': scope, 'status_refs': copy.deepcopy(refs[start:end]),
                     'cleanup_failures': copy.deepcopy(getattr(owner, 'cleanup_failures', []))}
                    for scope, owner, start, end in (
                        ('native', native_jobs, 0, len(native_jobs.accounting) if native_jobs else 0),
                        ('authoritative', jobs, len(native_jobs.accounting) if native_jobs else 0, len(refs)))
                    if owner is not None]
            summary = {'mode': mode, 'original_windows': 0 if business else bank.original_count,
                       'reserved': budget.reserved, 'limit': budget.limit,
                       'receipts': sum(row.get('receipt_persisted', False) for row in accounting)}
            # Parent checks current access independently; terminal revocation can
            # settle without publishing this content or mutating a foreign source.
            check()
            publish(media=copy.deepcopy(media), video_runtime_private=private, video_runtime_summary=summary)
        result.update(video_runtime_private=private, video_runtime_summary=summary)
    boundary()
    return result
