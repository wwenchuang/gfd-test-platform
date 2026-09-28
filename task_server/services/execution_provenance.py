"""Trusted execution origins and private, immutable enqueue inputs.

Public jobs contain metadata only. Internal callers must authorize an asset
before enqueueing; human contexts are also rechecked here against current ACL.
"""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from task_server.storage import safe_join
from .operation_attribution import current_actor, current_request_id

_ORIGIN = ContextVar("execution_origin", default=None)
_RESERVED = frozenset({
    "created_by", "createdBy", "creator", "initiator", "initiator_user_id", "initiatorUserId",
    "source_request_id", "sourceRequestId", "input_artifact", "inputArtifact",
    "input_snapshot", "inputSnapshot", "snapshot_path", "snapshotPath",
    "yaml_content", "yamlContent", "execution_scope", "executionScope",
    "dispatch_sha256", "dispatchSha256", "dispatch_version", "dispatchVersion",
    "trigger_type", "executor", "input_sha256", "input_version", "asset_id", "version_id",
    "input_source", "inputSource", "provenance_version", "provenanceVersion",
    "actor", "actor_user_id", "creator_user_id", "resource_type", "resource_id",
    "content_sha256", "source_recording_id", "source_job_id", "source_run_id", "source_version_id",
    "scope_refs", "input_snapshot_path", "inputSnapshotPath",
})


class ExecutionJobRecord(dict):
    """Runtime marker from server job normalization; JSON cannot supply it."""


def reserved_field(name):
    return name in _RESERVED


@contextmanager
def trusted_job_context(job):
    """Internal validated worker/retry only; never call from a supplied parent ID.

    The caller authorizes the persisted source job before entering. A current
    authenticated human always remains the new initiator (manual retry).
    """
    # Legacy extra payload fields never become trusted merely by DB lookup.
    trusted = job if job.get("provenance_version") == 1 else {}
    supplied = trusted.get("initiator") or {}
    initiator = {key: supplied.get(key, "") for key in ("kind", "user_id", "username", "display_name")}
    token = _ORIGIN.set({"initiator": initiator,
                         "source_request_id": trusted.get("source_request_id") or ""})
    try:
        yield
    finally:
        _ORIGIN.reset(token)


def snapshot_directory(jobs_file):
    return Path(jobs_file).absolute().parent / ".job-inputs"


def _snapshot_path(job, jobs_file):
    identifier = str(job.get("job_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier):
        raise ValueError("invalid job snapshot identifier")
    directory = snapshot_directory(jobs_file)
    if directory.is_symlink():
        raise ValueError("symlink snapshot directory is not supported")
    return directory / (hashlib.sha256(identifier.encode()).hexdigest() + ".yaml")


def _attach_origin(job):
    actor = dict(current_actor())
    origin = _ORIGIN.get() or {}
    initiator = actor if actor.get("kind") == "user" else dict(origin.get("initiator") or {})
    if initiator.get("kind") != "user":
        initiator = {"kind": "unknown", "user_id": "", "username": "", "display_name": ""}
    job.update(provenance_version=1, initiator=initiator, initiator_user_id=initiator.get("user_id") or "",
               created_by=initiator.get("username") or "",
               source_request_id=current_request_id() or origin.get("source_request_id") or "",
               trigger_type="manual" if actor.get("kind") == "user" else "automatic")
    return initiator


@contextmanager
def prepare_job_input(job, *, task_dir, jobs_file):
    """Attach trusted metadata; yield pinned bytes; remove new input on failure.

    Call outside JOB_LOCK. No asset lock remains held during private snapshot
    writes or job persistence. Missing inputs are explicitly non-executable.
    """
    initiator = _attach_origin(job)
    kind = str(job.get("job_type") or job.get("type") or "").lower()
    filename = str(job.get("file") or "")
    if kind == "apk_install" or not filename.lower().endswith((".yaml", ".yml")):
        job["input_snapshot"] = "not_applicable"
        yield None
        return
    from task_server import access_control, identity
    from . import asset_lineage
    module = job.get("module") or ""
    path = safe_join(task_dir, module, filename)
    catalog = access_control.application_catalog()
    policy = access_control.MainAccess({"scope": {"ui_apps": "*"}}, catalog=catalog)
    apps = sorted(policy.module_apps.get(module, set()))
    if initiator.get("kind") == "user":
        profile = identity.get_access_profile(initiator.get("username"))
        if not profile or profile.get("user_id") != initiator.get("user_id") or profile.get("status") != "active" or profile.get("must_change_password"):
            raise access_control.AccessDenied("执行发起账号已不可用")
        access = access_control.MainAccess(profile, catalog=catalog)
        access.require("ui.execute")
        if not access.visible({"module": module}):
            raise access_control.AccessDenied("执行资产不在已授权范围")
        access._require_file_path(module, filename)
    job["execution_scope"] = {"ui_apps": apps}
    job.pop("appPackage", None)
    job["app_package"] = apps[0] if len(apps) == 1 else ""
    try:
        metadata, content = asset_lineage.snapshot(path)
    except FileNotFoundError:
        job["input_snapshot"] = "unavailable"
        yield None
        return
    artifact = {"asset_id": metadata["asset_id"], "version_id": metadata["version"]["version_id"],
                "content_sha256": metadata["version"]["content_sha256"], "size": len(content)}
    source = metadata.get("source_recording_id") or metadata["version"].get("source_recording_id")
    if source:
        artifact["source_recording_id"] = source
    private_path = _snapshot_path(job, jobs_file)
    private_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private_path.parent, 0o700)
    # O_EXCL forbids replacement of another job's input, including symlinks.
    descriptor = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        job.update(input_snapshot="pinned", input_artifact=artifact)
        yield content
    except BaseException:
        # A storage adapter can raise AFTER its atomic replace. Never remove an
        # input referenced by that committed job. This read is failure-only.
        try:
            persisted = json.loads(Path(jobs_file).read_text(encoding="utf-8"))
            committed = any(isinstance(row, dict) and row.get("job_id") == job.get("job_id")
                            and row.get("input_artifact") == artifact for row in persisted)
        except FileNotFoundError:
            committed = False
        except Exception:
            committed = True  # uncertain persistence: retain for maintenance
        if not committed:
            private_path.unlink(missing_ok=True)
        raise


def read_job_input(job, *, task_dir, jobs_file):
    """Machine/internal read: missing new snapshot never means mutable fallback."""
    state = job.get("input_snapshot")
    if state is None:
        # Explicit legacy compatibility; no new job may omit the marker.
        return Path(safe_join(task_dir, job["module"], job["file"])).read_bytes()
    if state != "pinned":
        raise FileNotFoundError("job has no executable frozen YAML input")
    path = _snapshot_path(job, jobs_file)
    if path.is_symlink():
        raise ValueError("symlink execution snapshot is not supported")
    try:
        content = path.read_bytes()
    except OSError:
        raise FileNotFoundError("frozen job input unavailable") from None
    expected = (job.get("input_artifact") or {}).get("content_sha256")
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("frozen job input hash mismatch")
    return content
