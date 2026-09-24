"""Trusted operation attribution and durable, access-scoped business history.

Callers must supply resource IDs and allowlisted summaries from trusted service
state, after the business outcome is known. This module never grants resource
access; the existing route policy remains authoritative.
"""

import json
import itertools
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import MappingProxyType

from task_server import auth, identity


_ACTOR = ContextVar("operation_actor", default=None)
_REQUEST_ID = ContextVar("operation_request_id", default=None)
_RESULTS = frozenset({"success", "failed", "denied", "partial", "accepted", "interrupted"})
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"})
_KINDS = frozenset({"user", "runner", "sonic", "system", "unknown", "anonymous"})
_SAFE_FIELD = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_CHANGE_FIELD = frozenset({"name", "title", "status", "scope", "role_ids", "description", "module", "project", "environment", "version", "trigger", "schedule"})


def _short(value, limit=128):
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value.strip() if ch.isprintable())
    return value[:limit]


def _identifier(value, limit=128):
    value = _short(value, limit)
    return value if _SAFE_FIELD.fullmatch(value) else ""


def _actor(kind, user_id="", username="", display_name=""):
    if kind not in _KINDS:
        kind = "unknown"
    return MappingProxyType({"kind": kind, "user_id": _identifier(user_id),
                             "username": _short(username, 64), "display_name": _short(display_name, 96)})


def actor_from_trusted_username(username):
    """Resolve a persisted internal initiator; never call with request data."""
    profile = identity.get_access_profile(username) if isinstance(username, str) and username else None
    if not profile:
        return _actor("unknown")
    return _actor("user", profile["user_id"], profile["username"], profile["display_name"])


def resolve_request_actor(headers, body=None):
    """Resolve only verified headers. `body` is accepted for adapter convenience and ignored."""
    headers = headers or {}
    if auth.is_runner_authorized(headers):
        return _actor("runner", username="runner", display_name="Runner")
    if auth.is_sonic_callback_authorized(headers):
        return _actor("sonic", username="sonic", display_name="Sonic")
    session = auth.verify_session_token(auth.bearer_token(headers))
    if session:
        profile = identity.get_access_profile(session["user"])
        if profile and profile["user_id"] == session["user_id"] and profile["status"] == "active":
            return _actor("user", profile["user_id"], profile["username"], profile["display_name"])
    return _actor("anonymous")


@contextmanager
def actor_context(actor, request_id=None):
    actor = _actor(**dict(actor))
    actor_token = _ACTOR.set(actor)
    request_token = _REQUEST_ID.set(_identifier(request_id))
    try:
        yield
    finally:
        _REQUEST_ID.reset(request_token)
        _ACTOR.reset(actor_token)


def current_actor():
    return _ACTOR.get() or _actor("unknown")


def current_request_id():
    return _REQUEST_ID.get() or None


def default_db_path():
    configured = os.getenv("TASK_OPERATION_DB")
    if configured:
        return Path(configured).expanduser().absolute()
    return identity.default_db_path().with_name("operations.sqlite3")


def _private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.lstat()
    if path.is_symlink() or (hasattr(os, "geteuid") and stat.st_uid != os.geteuid()):
        raise PermissionError("operation directory must be owned by service account")
    os.chmod(path, 0o700)


def _private_file(path):
    if path.is_symlink():
        raise PermissionError("operation file cannot be a symlink")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if hasattr(os, "geteuid") and os.fstat(fd).st_uid != os.geteuid():
            raise PermissionError("operation file must be owned by service account")
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _sanitize(event):
    actor = event.get("actor") or current_actor()
    actor = dict(actor) if isinstance(actor, (dict, MappingProxyType)) else {}
    kind = actor.get("kind") if actor.get("kind") in _KINDS else "unknown"
    user_id = _identifier(actor.get("user_id")) if kind == "user" else ""
    if kind == "user" and not user_id:
        kind = "unknown"
    summary = event.get("summary") if isinstance(event.get("summary"), dict) else {}
    original_changed = summary.get("changed_fields") if isinstance(summary.get("changed_fields"), list) else []
    changed = [_identifier(item, 64) for item in original_changed if item in _SAFE_CHANGE_FIELD][:25]
    safe_summary = {"changed_fields": changed} if changed else {}
    for key in ("reason_code", "trigger", "version", "content_hash"):
        value = _identifier(summary.get(key), 64)
        if value:
            safe_summary[key] = value
    items = event.get("item_outcomes") if isinstance(event.get("item_outcomes"), list) else []
    safe_items = []
    for item in items[:25]:
        if not isinstance(item, dict):
            continue
        safe_items.append({"resource_id": _identifier(item.get("resource_id")),
                           "result": item.get("result") if item.get("result") in _RESULTS else "failed",
                           "reason_code": _identifier(item.get("reason_code"), 64)})
    scope = event.get("scope_refs") if isinstance(event.get("scope_refs"), dict) else {}
    safe_scope = {key: _identifier(scope.get(key)) for key in ("ui_app", "api_project", "api_environment") if _identifier(scope.get(key))}
    result = event.get("result")
    if result not in _RESULTS:
        raise ValueError("invalid operation result")
    return {
        "event_id": _identifier(event.get("event_id")) or uuid.uuid4().hex,
        "request_id": _identifier(event.get("request_id") or current_request_id()),
        "batch_id": _identifier(event.get("batch_id")),
        "actor": {"kind": kind, "user_id": user_id, "username": _short(actor.get("username"), 64),
                  "display_name": _short(actor.get("display_name"), 96)},
        "action": _identifier(event.get("action")),
        "method": event.get("method") if event.get("method") in _METHODS else "",
        "resource_type": _identifier(event.get("resource_type")),
        "resource_id": _identifier(event.get("resource_id")),
        "scope_refs": safe_scope, "result": result,
        "status_code": max(0, min(599, int(event.get("status_code") or 0))),
        "timestamp": float(event.get("timestamp") or time.time()),
        "duration_ms": max(0, min(86400000, int(event.get("duration_ms") or 0))),
        "summary": safe_summary, "item_outcomes": safe_items,
        "truncated": bool(event.get("truncated")) or len(items) > 25 or len(original_changed) > 25,
        "initiator_user_id": _identifier(event.get("initiator_user_id")),
        "source_job_id": _identifier(event.get("source_job_id")),
        "source_run_id": _identifier(event.get("source_run_id")),
        "source_recording_id": _identifier(event.get("source_recording_id")),
        "version_id": _identifier(event.get("version_id")),
        "content_sha256": value if re.fullmatch(r"[0-9a-f]{64}", value := _short(event.get("content_sha256"), 64)) else "",
    }


class OperationStore:
    def __init__(self, path=None, spool_dir=None):
        self.path = Path(path).expanduser().absolute() if path is not None else default_db_path()
        self.spool_dir = Path(spool_dir).expanduser().absolute() if spool_dir else self.path.parent / "operation-spool"
        _private_dir(self.path.parent)
        _private_dir(self.spool_dir)
        _private_file(self.path)
        self._schema_ready = False
        try:
            self._ensure_schema()
        except sqlite3.Error:
            # A temporary primary outage must not prevent durable spool writes.
            pass

    def _ensure_schema(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                    timestamp REAL NOT NULL, actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL,
                    initiator_id TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
                    result TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_actor_seq ON events(actor_id, seq DESC);
                CREATE INDEX IF NOT EXISTS events_action_seq ON events(action, seq DESC);
                CREATE INDEX IF NOT EXISTS events_resource_seq ON events(resource_type, resource_id, seq DESC);
                CREATE INDEX IF NOT EXISTS events_result_seq ON events(result, seq DESC);
                CREATE INDEX IF NOT EXISTS events_timestamp_seq ON events(timestamp, seq DESC);
            """)
            if "initiator_id" not in {row[1] for row in db.execute("PRAGMA table_info(events)")}:
                db.execute("ALTER TABLE events ADD COLUMN initiator_id TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE events SET initiator_id=COALESCE(json_extract(data, '$.initiator_user_id'),'')")
            if "method" not in {row[1] for row in db.execute("PRAGMA table_info(events)")}:
                db.execute("ALTER TABLE events ADD COLUMN method TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE events SET method=COALESCE(json_extract(data, '$.method'),'')")
            db.execute("CREATE INDEX IF NOT EXISTS events_initiator_seq ON events(initiator_id, seq DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS events_method_seq ON events(method, seq DESC)")
        self._schema_ready = True

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=0.15)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _insert(self, event):
        if not self._schema_ready:
            self._ensure_schema()
        with self._connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO events(event_id,timestamp,actor_kind,actor_id,initiator_id,method,action,resource_type,resource_id,result,data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (event["event_id"], event["timestamp"], event["actor"]["kind"], event["actor"]["user_id"],
                 event["initiator_user_id"], event["method"], event["action"], event["resource_type"], event["resource_id"], event["result"],
                 json.dumps(event, ensure_ascii=False, separators=(",", ":"))))
            return cursor.rowcount == 0

    def _spool(self, event):
        target = self.spool_dir / (event["event_id"] + ".json")
        temporary = self.spool_dir / (uuid.uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(event, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                return True
            directory_fd = os.open(self.spool_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def append(self, event):
        event = _sanitize(event)
        try:
            duplicate = self._insert(event)
            return {"stored": True, "storage": "sqlite", "duplicate": duplicate}
        except sqlite3.Error:
            try:
                duplicate = self._spool(event)
                return {"stored": True, "storage": "spool", "duplicate": bool(duplicate)}
            except OSError:
                return {"stored": False, "storage": "failed", "duplicate": False}

    def replay_spool(self, limit=100):
        replayed = 0
        remaining = 0
        for path in itertools.islice(self.spool_dir.glob("*.json"), max(1, min(int(limit), 1000))):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
                if path.stem != event.get("event_id"):
                    continue
                self._insert(_sanitize(event))
                path.unlink()
                replayed += 1
            except (OSError, sqlite3.Error, ValueError, KeyError):
                remaining += 1
        return {"replayed": replayed, "remaining": remaining}

    def list_events(self, profile, *, limit=50, cursor=None, filters=None, all_actors=False):
        """Own events by default; only a live superuser can request all actors."""
        profile = profile or {}
        user_id = _identifier(profile.get("user_id"))
        if not user_id or profile.get("status") != "active" or profile.get("must_change_password"):
            raise PermissionError("active authenticated profile required")
        # Recheck current identity; stale caller snapshots cannot widen access.
        live = identity.get_access_profile(profile.get("username"))
        if not live or live["user_id"] != user_id or live["status"] != "active" or live["must_change_password"]:
            raise PermissionError("profile is no longer active")
        unrestricted = bool(all_actors and live.get("is_superuser"))
        filters = filters or {}
        predicates = [] if unrestricted else ["(actor_id=? OR initiator_id=?)"]
        values = [] if unrestricted else [user_id, user_id]
        for name, column in (("actor_id", "actor_id"), ("method", "method"), ("action", "action"),
                             ("resource_type", "resource_type"), ("resource_id", "resource_id"), ("result", "result")):
            value = _identifier(filters.get(name))
            if value:
                predicates.append(f"{column}=?")
                values.append(value)
        for name, operator in (("from_ts", ">="), ("to_ts", "<=")):
            if filters.get(name) is not None:
                predicates.append("timestamp" + operator + "?")
                values.append(float(filters[name]))
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            db.execute("BEGIN")
            total = db.execute("SELECT COUNT(*) FROM events" + where, values).fetchone()[0]
            page_where = where + (" AND " if where else " WHERE ") + "seq<?" if cursor else where
            page_values = values + [int(cursor)] if cursor else values
            rows = db.execute("SELECT seq,data FROM events" + page_where + " ORDER BY seq DESC LIMIT ?", page_values + [limit + 1]).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"events": [json.loads(row[1]) for row in rows], "total": total,
                "next_cursor": rows[-1][0] if more else None}
