"""Sequential business-only owners; transient retry never reopens a failed owner."""
import copy

from .ui_video_model_jobs import LocalModelJobDeadline, ModelJobCancelled, ModelJobError, ModelJobs
from .ui_video_stage_policy import STAGES

RETRY_STAGES = frozenset({'overview', 'grounding', 'audit', 'consolidate', 'outcome', 'inputs'})


def _retry_reason(error, owner, offset, skill, stage, records):
    if (stage not in RETRY_STAGES or skill != STAGES[stage][0] or
            getattr(owner, '_closed', False) is not True or owner.active_pids or
            owner.cleanup_failures or owner.pending_receipts or len(owner.accounting) != offset + 1):
        return None
    row = owner.accounting[offset]
    if (any(row.get(k) is not True for k in ('reserved', 'proof_persisted', 'dispatch_intent', 'terminal', 'cleanup')) or
            row.get('admitted') is not False or not row.get('proof_ref')):
        return None
    proof = records.read(row['proof_ref'])
    if (proof.get('stage'), proof.get('skill'), proof.get('skill_version')) != (stage, skill, STAGES[stage][1]):
        return None
    if (type(error) is LocalModelJobDeadline and row.get('transport_status') == 'unknown' and
            row.get('raw_received') is False and row.get('receipt_persisted') is False and not row.get('receipt_ref')):
        return 'local_job_deadline'
    if (type(error) is ModelJobError and str(error) == 'video_job_failed' and
            row.get('raw_received') is True and row.get('receipt_persisted') is True and
            row.get('transport_status') == 'receipt_received' and row.get('receipt_ref')):
        receipt = records.read(row['receipt_ref'])
        status = receipt.get('http_status')
        if (receipt.get('gateway_request_sha256') == proof['gateway_request_sha256'] and
                receipt.get('rejection_code') == 'video_stage_provider_failed' and
                type(status) is int and (status == 429 or 500 <= status <= 599) and
                receipt.get('content') == '' and not receipt.get('usage') and
                all(receipt.get(k) is None for k in ('providerId', 'model', 'fallbackUsed', 'videoPolicy', 'finishReason'))):
            return 'temporary_provider_failure'
    return None


class BusinessJobs:
    def __init__(self, records, *, budget, check, owner_factory=ModelJobs):
        self.records, self.budget, self.check = records, budget, check
        self.owner_factory = owner_factory
        self.owners, self.retries = [], []
        self._retired, self._closed = set(), False
        self._new_owner()

    def _new_owner(self):
        self.current = self.owner_factory(self.records, deadline=self.budget.deadline,
            reserve=self.budget.reserve, check=self.check, engine_deadline=self.budget.engine_deadline)
        self.owners.append(self.current)
        self.current.__enter__()

    def __enter__(self):
        return self

    def _close_current(self, error=None):
        if id(self.current) not in self._retired:
            self._retired.add(id(self.current))
            self.current.__exit__(type(error) if error is not None else None, error, None)

    def __exit__(self, exc_type, error, traceback):
        self._closed = True
        self._close_current(error)

    @property
    def accounting(self):
        return [row for owner in self.owners for row in owner.accounting]

    def run(self, skill, payload=None, *, allow_transport_retry=True, **kwargs):
        if type(allow_transport_retry) is not bool:
            raise ValueError('video_job_options_invalid')
        if self._closed or id(self.current) in self._retired:
            raise ModelJobError('video_job_latched')
        # Each attempt gets detached immutable request data and a fresh trace.
        # Hooks have first-attempt semantics only; never replay external effects.
        trace = kwargs.pop('runtime_trace', {})
        if not isinstance(trace, dict):
            raise ValueError('video_job_options_invalid')
        hooks = {k: kwargs.pop(k) for k in ('request_proof_hook', 'raw_receipt') if k in kwargs}
        options, payload = copy.deepcopy(kwargs), copy.deepcopy(payload)
        stage = options.get('video_stage')
        for attempt in range(2):
            owner, offset, attempt_trace = self.current, len(self.current.accounting), {}
            try:
                return owner.run(skill, copy.deepcopy(payload), **copy.deepcopy(options),
                    **hooks, runtime_trace=attempt_trace)
            except Exception as error:
                if not (type(error) is LocalModelJobDeadline or
                        type(error) is ModelJobError and str(error) == 'video_job_failed'):
                    raise
                self._close_current(error)
                if self.check() is False:
                    raise ModelJobCancelled('video_job_cancelled')
                reason = _retry_reason(error, owner, offset, skill, stage, self.records)
                if not allow_transport_retry or attempt or len(self.retries) >= 2 or hooks or reason is None:
                    raise
                end = min(self.budget.deadline, self.budget.engine_deadline() or self.budget.deadline)
                if end - self.budget.clock() < 120.5 or self.budget.reserved >= self.budget.limit:
                    raise
                row = owner.accounting[offset]
                retry = {'stage':stage, 'reason':reason, 'attempt':2,
                         'failed_refs':{k:copy.deepcopy(row[k]) for k in ('proof_ref','receipt_ref') if k in row},
                         'failed_owner':len(self.owners)-1}
                self.retries.append(retry)
                self._new_owner()
                retry['replacement_owner'] = len(self.owners)-1
            finally:
                if len(owner.accounting) == offset + 1:
                    row = owner.accounting[offset]
                    refs = {k:copy.deepcopy(row[k]) for k in ('proof_ref','receipt_ref') if k in row}
                    attempt_trace['video_record_refs'] = refs
                    if attempt:
                        self.retries[-1]['replacement_refs'] = copy.deepcopy(refs)
                if isinstance(trace, dict):
                    trace.clear()
                    trace.update(attempt_trace)
