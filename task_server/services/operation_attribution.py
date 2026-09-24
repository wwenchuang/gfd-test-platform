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
_SAFE_CHANGE_FIELD = frozenset({"name", "title", "status", "scope", "role_ids", "description", "module", "project", "environment", "version", "trigger", "schedule", "package", "display_name", "permissions"})
MAX_BATCH_ITEMS = 10000
MAX_SPOOL_BYTES = 4 * 1024 * 1024


def _short(value, limit=128):
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value[:limit * 4].strip() if ch.isprintable())
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
    detailed = event.get("item_details")
    items = detailed if isinstance(detailed, list) else event.get("item_outcomes") if isinstance(event.get("item_outcomes"), list) else []
    safe_items = []
    for item in itertools.islice(items, MAX_BATCH_ITEMS):
        item = item if isinstance(item, dict) else {}
        safe_items.append({"resource_id": _identifier(item.get("resource_id")),
                           "result": item.get("result") if item.get("result") in _RESULTS else "failed",
                           "reason_code": _identifier(item.get("reason_code"), 64)})
    declared_total = int(event.get("item_total") or len(items))
    item_total = max(len(items), declared_total)
    items_complete = (bool(event.get("items_complete")) if "items_complete" in event else not bool(event.get("truncated"))) and item_total == len(safe_items)
    capture_status = "complete" if items_complete else "limit_exceeded" if item_total > len(safe_items) else "legacy_incomplete"
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
        "route_key": _identifier(event.get("route_key")) or "unknown",
        "method": event.get("method") if event.get("method") in _METHODS else "",
        "resource_type": _identifier(event.get("resource_type")),
        "resource_id": _identifier(event.get("resource_id")),
        "scope_refs": safe_scope, "result": result,
        "status_code": max(0, min(599, int(event.get("status_code") or 0))),
        "timestamp": float(event.get("timestamp") or time.time()),
        "duration_ms": max(0, min(86400000, int(event.get("duration_ms") or 0))),
        "summary": safe_summary, "item_outcomes": safe_items[:25],
        "item_details": safe_items, "item_total": item_total, "item_captured": len(safe_items),
        "items_complete": items_complete, "capture_status": capture_status,
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
                CREATE TABLE IF NOT EXISTS event_items (
                    event_id TEXT NOT NULL, ordinal INTEGER NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(event_id, ordinal), FOREIGN KEY(event_id) REFERENCES events(event_id));
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
            public = {key: value for key, value in event.items() if key != "item_details"}
            cursor = db.execute("INSERT OR IGNORE INTO events(event_id,timestamp,actor_kind,actor_id,initiator_id,method,action,resource_type,resource_id,result,data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (event["event_id"], event["timestamp"], event["actor"]["kind"], event["actor"]["user_id"],
                 event["initiator_user_id"], event["method"], event["action"], event["resource_type"], event["resource_id"], event["result"],
                 json.dumps(public, ensure_ascii=False, separators=(",", ":"))))
            if cursor.rowcount:
                db.executemany("INSERT INTO event_items(event_id,ordinal,data) VALUES (?,?,?)",
                    ((event["event_id"], index, json.dumps(item, separators=(",", ":")))
                     for index, item in enumerate(event["item_details"])))
            return cursor.rowcount == 0

    def _spool(self, event):
        target = self.spool_dir / (event["event_id"] + ".json")
        temporary = self.spool_dir / (uuid.uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(event, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                if os.fstat(stream.fileno()).st_size > MAX_SPOOL_BYTES:
                    raise OSError("operation spool size limit")
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
        pending = False
        degraded_marker = self.spool_dir / ".recovery-degraded"
        replay_limit = max(1, min(int(limit), 1000))
        for index, path in enumerate(itertools.islice(self.spool_dir.glob("*.json"), replay_limit + 1)):
            if index == replay_limit:
                pending = True
                break
            try:
                with path.open("rb") as stream:
                    raw = stream.read(MAX_SPOOL_BYTES + 1)
                if len(raw) > MAX_SPOOL_BYTES:
                    raise ValueError("oversized spool event")
                event = json.loads(raw.decode("utf-8"))
                if not isinstance(event, dict) or path.stem != event.get("event_id"):
                    raise ValueError("invalid spool event")
                self._insert(_sanitize(event))
                path.unlink()
                replayed += 1
            except (ValueError, KeyError, TypeError, UnicodeError):
                # Preserve diagnostic evidence while removing permanently invalid input
                # from the replay queue so later valid files can progress.
                try:
                    path.rename(path.with_name(path.name + "." + uuid.uuid4().hex + ".invalid"))
                    _private_file(degraded_marker)
                    directory_fd = os.open(self.spool_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    remaining += 1
            except (OSError, sqlite3.Error):
                remaining += 1
        unresolved = int(degraded_marker.exists())
        return {"replayed": replayed, "remaining": remaining + unresolved + int(pending),
                "quarantined": bool(unresolved), "pending": pending}

    @staticmethod
    def _live_profile(profile):
        profile = profile or {}
        user_id = _identifier(profile.get("user_id"))
        if not user_id or profile.get("status") != "active" or profile.get("must_change_password"):
            raise PermissionError("active authenticated profile required")
        live = identity.get_access_profile(profile.get("username"))
        if not live or live["user_id"] != user_id or live["status"] != "active" or live["must_change_password"]:
            raise PermissionError("profile is no longer active")
        return live

    @staticmethod
    def _public_event(data):
        event = json.loads(data)
        event.pop("item_details", None)
        event.setdefault("item_total", len(event.get("item_outcomes", [])))
        event.setdefault("items_complete", not event.get("truncated", False))
        event.setdefault("item_captured", event["item_total"] if event["items_complete"] else len(event.get("item_outcomes", [])))
        event.setdefault("capture_status", "complete" if event["items_complete"] else "legacy_incomplete")
        return event

    def list_items(self, profile, event_id, *, limit=50, cursor=None, all_actors=False):
        """Return ordered sanitized details under the same live scope as history."""
        event_id = _identifier(event_id)
        if not event_id:
            return {"items": [], "total": 0, "next_cursor": None, "items_complete": False}
        live = self._live_profile(profile)
        unrestricted = bool(all_actors and live.get("is_superuser"))
        predicate = "" if unrestricted else " AND (actor_id=? OR initiator_id=?)"
        values = [event_id] if unrestricted else [event_id, live["user_id"], live["user_id"]]
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(cursor or 0))
        with self._connect() as db:
            row = db.execute("SELECT data FROM events WHERE event_id=?" + predicate, values).fetchone()
            if not row:
                return {"items": [], "total": 0, "next_cursor": None, "items_complete": False}
            event = self._public_event(row[0])
            total = int(event.get("item_total", len(event.get("item_outcomes", []))))
            captured = int(event.get("item_captured", len(event.get("item_outcomes", []))))
            complete = bool(event.get("items_complete", not event.get("truncated", False)))
            rows = db.execute("SELECT data FROM event_items WHERE event_id=? AND ordinal>=? ORDER BY ordinal LIMIT ?",
                              (event_id, offset, limit + 1)).fetchall()
        if rows:
            items = [json.loads(item[0]) for item in rows[:limit]]
        else:
            items = event.get("item_outcomes", [])[offset:offset + limit]
        next_cursor = offset + len(items) if offset + len(items) < captured else None
        return {"items": items, "total": total, "item_captured": captured,
                "next_cursor": next_cursor, "items_complete": complete,
                "capture_status": event["capture_status"]}

    def list_events(self, profile, *, limit=50, cursor=None, filters=None, all_actors=False):
        """Own events by default; only a live superuser can request all actors."""
        live = self._live_profile(profile)
        user_id = live["user_id"]
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
        return {"events": [self._public_event(row[1]) for row in rows], "total": total,
                "next_cursor": rows[-1][0] if more else None}
