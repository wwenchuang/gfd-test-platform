"""Future job audit only. These events are never an authorization source.

No job JSON fields are promoted to identity. Unknown asynchronous callers and
jobs without a unique creation event remain unattributed rather than guessed.
"""

import hashlib
import logging
import sqlite3
from functools import lru_cache

from .operation_attribution import OperationStore, default_db_path

_LOG = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _store(path):
    return OperationStore(path)


def _append(job_id, action, *, yaml_content=None):
    try:
        store = _store(str(default_db_path()))
        event = {"action": action, "resource_type": "job", "resource_id": job_id,
                 "result": "success" if yaml_content is None else "accepted"}
        if yaml_content is not None:
            event["content_sha256"] = hashlib.sha256(yaml_content.encode("utf-8")).hexdigest()
            try:
                event["initiator_user_id"] = store.job_creation_actor_id(job_id)
            except (sqlite3.Error, OSError):
                # Still spool the exact dispatch hash during a database outage.
                event["summary"] = {"reason_code": "initiator_lookup_unavailable"}
        if not store.append(event)["stored"]:
            _LOG.error("job audit unavailable action=%s", action)
    except Exception:
        # Audit storage already falls back to a durable private spool. A total
        # audit outage must not turn a persisted job into an apparent create
        # failure (and cause a retry to enqueue the same business work twice).
        # Do not log job payloads, YAML, credentials, or exception strings.
        _LOG.error("job audit unavailable action=%s", action)


def record_created(job_id):
    """Call after persistence under JOB_LOCK, before Runner can claim the job."""
    _append(job_id, "job.created")


def record_dispatch(job_id, yaml_content):
    """Hash final dispatch text; this does not claim delivery or execution."""
    _append(job_id, "job.dispatch_prepared", yaml_content=yaml_content)
