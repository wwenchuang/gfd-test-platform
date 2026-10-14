"""Private, durable UI batch snapshots and dispatch intents.

This store records intent only. Job creation and device leases belong to the
dispatcher; a dispatch_key can be used as its deterministic job_id on retry.
"""

import json
import os
import sqlite3
import time
import threading
import uuid
from pathlib import Path

from task_server import identity
from .ui_execution_lifecycle import UiExecutionLifecycle, manual_stop_eligible, manual_stop_version


class BatchConflict(ValueError):
    pass


def default_db_path():
    configured = os.getenv("TASK_UI_EXECUTION_DB")
    return Path(configured).expanduser().absolute() if configured else identity.default_db_path().with_name("ui-executions.sqlite3")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decoded(value):
    return json.loads(value) if value else {}


_SCHEMA_LOCK = threading.Lock()


class UiExecutionStore(UiExecutionLifecycle):
    def __init__(self, path=None):
        self.path = Path(path).expanduser().absolute() if path else default_db_path()

    def _connect(self):
        with _SCHEMA_LOCK:
            return self._open_connection()

    def _open_connection(self):
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if directory.is_symlink() or (hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
            raise PermissionError("UI execution directory must be owned by service account")
        os.chmod(directory, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if hasattr(os, "geteuid") and os.fstat(fd).st_uid != os.geteuid():
                raise PermissionError("UI execution database must be owned by service account")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5):
            db.close()
            raise RuntimeError("Unsupported UI execution schema version")
        if version == 0:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS batches (
                ui_batch_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
                owner_username TEXT NOT NULL, owner_display_name TEXT NOT NULL,
                app_id TEXT NOT NULL, suite_id TEXT, suite_revision TEXT,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                selection_json TEXT NOT NULL, status TEXT NOT NULL,
                report_status TEXT NOT NULL DEFAULT 'not_requested',
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                created_ns INTEGER NOT NULL,
                UNIQUE(owner_user_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS batches_actor_time ON batches(owner_user_id, created_ns DESC, ui_batch_id DESC);
            CREATE INDEX IF NOT EXISTS batches_actor_app_time ON batches(owner_user_id, app_id, created_ns DESC, ui_batch_id DESC);
            CREATE INDEX IF NOT EXISTS batches_app_time ON batches(app_id, created_ns DESC, ui_batch_id DESC);
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY, ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id),
                case_id TEXT NOT NULL, case_revision TEXT NOT NULL,
                source_hash TEXT NOT NULL, source_yaml TEXT NOT NULL, metadata_json TEXT NOT NULL,
                UNIQUE(ui_batch_id, case_id, case_revision)
            );
            CREATE TABLE IF NOT EXISTS case_attempts (
                attempt_id TEXT PRIMARY KEY, ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id),
                snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                case_id TEXT NOT NULL, case_revision TEXT NOT NULL,
                runner_id TEXT NOT NULL, device_id TEXT NOT NULL, platform TEXT NOT NULL,
                attempt_no INTEGER NOT NULL, dispatch_key TEXT NOT NULL UNIQUE,
                target_resolution_json TEXT NOT NULL, compiled_case_json TEXT NOT NULL,
                job_id TEXT UNIQUE, agent_run_id TEXT, sonic_result_id TEXT,
                status TEXT NOT NULL,
                UNIQUE(ui_batch_id, case_id, runner_id, device_id, attempt_no)
            );
            CREATE INDEX IF NOT EXISTS attempts_batch_case_device ON case_attempts(ui_batch_id, case_id, device_id, attempt_no);
            CREATE INDEX IF NOT EXISTS attempts_job_id ON case_attempts(job_id);
            CREATE TABLE IF NOT EXISTS device_leases (
                platform TEXT NOT NULL, runner_id TEXT NOT NULL, device_id TEXT NOT NULL,
                ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id),
                attempt_id TEXT NOT NULL REFERENCES case_attempts(attempt_id),
                state TEXT NOT NULL, acquired_ns INTEGER NOT NULL,
                PRIMARY KEY(platform, runner_id, device_id)
            );
            CREATE TABLE IF NOT EXISTS delivery_outbox (
                delivery_key TEXT PRIMARY KEY, ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id),
                channel_id TEXT NOT NULL, report_revision TEXT NOT NULL,
                status TEXT NOT NULL, due_ns INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS outbox_status_due ON delivery_outbox(status, due_ns);
            """)
        if version < 2:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                    for name, spec in (
                        ("block_code", "TEXT NOT NULL DEFAULT ''"), ("block_message", "TEXT NOT NULL DEFAULT ''"),
                        ("claim_token", "TEXT NOT NULL DEFAULT ''"), ("event_sequence", "INTEGER NOT NULL DEFAULT 0"),
                        ("started_ns", "INTEGER"), ("finished_ns", "INTEGER"), ("last_event_ns", "INTEGER"),
                        ("stop_confirmed", "INTEGER NOT NULL DEFAULT 0"), ("job_sync_pending", "INTEGER NOT NULL DEFAULT 0"),
                        ("execution_progress_json", "TEXT"), ("result_json", "TEXT")
                    ):
                        db.execute(f"ALTER TABLE case_attempts ADD COLUMN {name} {spec}")
                    db.execute("CREATE UNIQUE INDEX lease_physical_device ON device_leases(platform,device_id)")
                    db.execute("ALTER TABLE batches ADD COLUMN last_reconcile_ns INTEGER NOT NULL DEFAULT 0")
                    db.execute("ALTER TABLE batches ADD COLUMN execution_settled INTEGER NOT NULL DEFAULT 0")
                    db.execute("ALTER TABLE batches ADD COLUMN needs_job_sync INTEGER NOT NULL DEFAULT 0")
                    db.execute("CREATE INDEX batches_recovery ON batches(last_reconcile_ns,created_ns) WHERE execution_settled=0 OR needs_job_sync=1")
                    db.execute("CREATE INDEX attempts_pending ON case_attempts(status,job_id)")
                    db.execute("PRAGMA user_version=2")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                db.close()
                raise
        if version < 3:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] < 3:
                    for name, spec in (
                        ("claim_token", "TEXT NOT NULL DEFAULT ''"), ("claimed_ns", "INTEGER"),
                        ("error_code", "TEXT NOT NULL DEFAULT ''"), ("finished_ns", "INTEGER"),
                        ("manual_retries", "INTEGER NOT NULL DEFAULT 0"),
                        ("cycle_attempts", "INTEGER NOT NULL DEFAULT 0"),
                        ("retry_actor_id", "TEXT NOT NULL DEFAULT ''")
                    ):
                        db.execute(f"ALTER TABLE delivery_outbox ADD COLUMN {name} {spec}")
                    db.execute("CREATE UNIQUE INDEX delivery_identity ON delivery_outbox(ui_batch_id,report_revision,channel_id)")
                    db.execute("CREATE INDEX deliveries_batch ON delivery_outbox(ui_batch_id,report_revision)")
                    # Previous schema had no claim timestamp. Never blindly resend an in-flight request.
                    db.execute("UPDATE batches SET delivery_status='unknown' WHERE ui_batch_id IN (SELECT ui_batch_id FROM delivery_outbox WHERE status='sending')")
                    db.execute("UPDATE delivery_outbox SET status='unknown',error_code='worker_interrupted' WHERE status='sending'")
                    db.execute("UPDATE batches SET delivery_status='queued' WHERE ui_batch_id IN (SELECT ui_batch_id FROM delivery_outbox WHERE status='pending')")
                    db.execute("CREATE INDEX IF NOT EXISTS batches_notify_ready ON batches(created_ns,ui_batch_id) WHERE execution_settled=1 AND report_status='ready' AND delivery_status='pending'")
                    db.execute("PRAGMA user_version=3")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                db.close()
                raise
        if version < 4:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""CREATE TABLE IF NOT EXISTS ui_manual_stop_recoveries (
                    attempt_id TEXT PRIMARY KEY REFERENCES case_attempts(attempt_id),
                    ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id), job_id TEXT NOT NULL,
                    runner_id TEXT NOT NULL, device_id TEXT NOT NULL, platform TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL, actor_username TEXT NOT NULL, actor_display_name TEXT NOT NULL,
                    confirmed_ns INTEGER NOT NULL, reason TEXT NOT NULL,
                    expected_status TEXT NOT NULL, expected_event_sequence INTEGER NOT NULL,
                    expected_last_event_ns INTEGER NOT NULL, lease_acquired_ns INTEGER NOT NULL, expected_recovery_version TEXT NOT NULL)""")
                db.execute("CREATE INDEX IF NOT EXISTS ui_leases_batch ON device_leases(ui_batch_id)")
                db.execute("CREATE INDEX IF NOT EXISTS ui_recoveries_batch ON ui_manual_stop_recoveries(ui_batch_id)")
                db.execute("PRAGMA user_version=4")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                db.close()
                raise
        if version < 5:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] < 5:
                    db.execute("ALTER TABLE batches ADD COLUMN report_requested_ns INTEGER")
                    db.execute("ALTER TABLE batches ADD COLUMN report_request_json TEXT NOT NULL DEFAULT '{}' ")
                    # Earlier versions queued every execution. No historical row is
                    # evidence of an explicit release-report request. Keep saved
                    # revisions and existing deliveries, but stop implicit builds.
                    db.execute("UPDATE batches SET report_status='not_requested' WHERE report_status IN ('pending','generating')")
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_report_heads'").fetchone():
                        db.execute("""UPDATE batches SET report_status='ready' WHERE report_status='not_requested'
                            AND EXISTS (SELECT 1 FROM ui_report_heads h JOIN ui_report_snapshots r
                              ON r.ui_batch_id=h.ui_batch_id AND r.report_revision=h.report_revision
                              WHERE h.ui_batch_id=batches.ui_batch_id AND r.status='ready')""")
                    db.execute("""UPDATE batches SET delivery_status='not_requested'
                        WHERE NOT EXISTS (SELECT 1 FROM delivery_outbox d WHERE d.ui_batch_id=batches.ui_batch_id)""")
                    db.execute("DROP INDEX IF EXISTS ui_reports_pending")
                    db.execute("""CREATE INDEX ui_reports_pending ON batches(created_ns)
                        WHERE execution_settled=1 AND report_status='pending' AND report_requested_ns IS NOT NULL""")
                    db.execute("PRAGMA user_version=5")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                db.close()
                raise
        return db

    def create_batch(self, prepared):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT ui_batch_id, request_hash FROM batches WHERE owner_user_id=? AND idempotency_key=?",
                                  (prepared["owner_user_id"], prepared["idempotency_key"])).fetchone()
            if existing:
                if existing["request_hash"] != prepared["request_hash"]:
                    raise BatchConflict("idempotency_conflict")
                batch_id = existing["ui_batch_id"]
            else:
                batch_id = "uib_" + uuid.uuid4().hex
                created_ns = time.time_ns()
                release = (prepared['selection'].get('release_report') or {}).get('enabled') is True
                report_request = {'requested_ns':created_ns,
                    'actor':{key:prepared['owner_'+key] for key in ('user_id','username','display_name')},
                    'notifications':prepared['selection'].get('notifications') or {'enabled':False}} if release else {}
                db.execute("""INSERT INTO batches(ui_batch_id, owner_user_id, owner_username, owner_display_name,
                           app_id, suite_id, suite_revision, idempotency_key, request_hash, selection_json,
                           status, report_status, delivery_status, created_ns, report_requested_ns, report_request_json)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (batch_id, prepared["owner_user_id"], prepared["owner_username"], prepared["owner_display_name"],
                            prepared["app_id"], prepared.get("suite_id"), prepared.get("suite_revision"),
                            prepared["idempotency_key"], prepared["request_hash"], _json(prepared["selection"]),
                            "queued", "pending" if release else "not_requested",
                            "pending" if release and report_request['notifications'].get('enabled') else "not_requested",
                            created_ns, created_ns if release else None, _json(report_request)))
                snapshots = {}
                for snapshot in prepared["snapshots"]:
                    snapshot_id = uuid.uuid4().hex
                    snapshots[(snapshot["case_id"], snapshot["case_revision"])] = snapshot_id
                    db.execute("""INSERT INTO snapshots(snapshot_id, ui_batch_id, case_id, case_revision,
                               source_hash, source_yaml, metadata_json) VALUES(?,?,?,?,?,?,?)""",
                               (snapshot_id, batch_id, snapshot["case_id"], snapshot["case_revision"],
                                snapshot["source_hash"], snapshot["source_yaml"], _json(snapshot["metadata"])))
                for index, attempt in enumerate(prepared["attempts"], 1):
                    target = attempt["target"]
                    dispatch_key = f"{batch_id}_{index}_1"
                    db.execute("""INSERT INTO case_attempts(attempt_id, ui_batch_id, snapshot_id, case_id,
                               case_revision, runner_id, device_id, platform, attempt_no, dispatch_key,
                               target_resolution_json, compiled_case_json, status)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (uuid.uuid4().hex, batch_id, snapshots[(attempt["case_id"], attempt["case_revision"])],
                                attempt["case_id"], attempt["case_revision"], target["runner_id"], target["device_id"],
                                target["platform"], 1, dispatch_key, _json(attempt["target_resolution"]),
                                _json(attempt["compiled_case"]), "queued"))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        return self.get_batch(batch_id)

    def find_idempotent(self, owner_user_id, key):
        db = self._connect()
        try:
            row = db.execute("SELECT ui_batch_id, request_hash FROM batches WHERE owner_user_id=? AND idempotency_key=?",
                             (owner_user_id, key)).fetchone()
            return dict(row) if row else None
        finally:
            db.close()

    def get_batch(self, batch_id):
        db = self._connect()
        try:
            row = db.execute("SELECT * FROM batches WHERE ui_batch_id=?", (batch_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["selection"] = _decoded(result.pop("selection_json"))
            result["report_request"] = _decoded(result.pop("report_request_json"))
            result["snapshots"] = []
            for item in db.execute("SELECT * FROM snapshots WHERE ui_batch_id=? ORDER BY rowid", (batch_id,)):
                snapshot = dict(item)
                snapshot["metadata"] = _decoded(snapshot.pop("metadata_json"))
                result["snapshots"].append(snapshot)
            attempt_rows = list(db.execute("SELECT * FROM case_attempts WHERE ui_batch_id=? ORDER BY rowid", (batch_id,)))
            result["case_attempts"] = [self._attempt(item) for item in attempt_rows]
            recoveries = {r['attempt_id']:dict(r) for r in db.execute(
                'SELECT * FROM ui_manual_stop_recoveries WHERE ui_batch_id=?', (batch_id,))}
            leases = {r['attempt_id']:dict(r) for r in db.execute(
                'SELECT * FROM device_leases WHERE ui_batch_id=?', (batch_id,))}
            now = time.time_ns()
            for raw, attempt in zip(attempt_rows, result['case_attempts']):
                attempt['manual_stop_recovery_version'] = manual_stop_version(raw, leases.get(attempt['attempt_id']))
                attempt['manual_stop_recovery'] = recoveries.get(attempt['attempt_id'])
                attempt['manual_stop_recovery_eligible'] = manual_stop_eligible(attempt, leases.get(attempt['attempt_id']), now)
            from collections import Counter
            result["execution_settled"] = bool(result["execution_settled"])
            result["stop_pending"] = any(a["stop_pending"] for a in result["case_attempts"])
            result["counts"] = dict(Counter(a["status"] for a in result["case_attempts"]))
            result["started_ns"] = min((a["started_ns"] for a in result["case_attempts"] if a["started_ns"]), default=None)
            result["finished_ns"] = max((a["finished_ns"] for a in result["case_attempts"] if a["finished_ns"]), default=None) if result["execution_settled"] else None
            return result
        finally:
            db.close()

    @staticmethod
    def _attempt(row):
        result = dict(row)
        result["target_resolution"] = _decoded(result.pop("target_resolution_json"))
        result["compiled_case"] = _decoded(result.pop("compiled_case_json"))
        result["stop_pending"] = bool(result.pop("claim_token", None) and not result.get("stop_confirmed"))  # Never expose the delivery credential.
        result["execution_settled"] = result["status"] in {"passed","failed","cancelled"} or (result["status"] == "interrupted" and bool(result.get("stop_confirmed")))
        result["execution_progress"] = _decoded(result.pop("execution_progress_json", None))
        result["result"] = _decoded(result.pop("result_json", None))
        from .ui_attempt_progress import describe_steps
        result.update(describe_steps(result))
        return result

    def list_batches(self, owner_user_id=None, app_id=None, cursor="", limit=20, app_ids=None):
        limit = max(1, min(int(limit), 100))
        if app_ids is not None and not app_ids:
            return {"items": [], "next_cursor": ""}
        where, params = [], []
        if owner_user_id is not None:
            where.append("owner_user_id=?")
            params.append(owner_user_id)
        if app_id:
            where.append("app_id=?")
            params.append(app_id)
        if app_ids is not None:
            where.append("app_id IN (" + ",".join("?" for _ in app_ids) + ")")
            params.extend(app_ids)
        if cursor:
            try:
                timestamp, batch_id = cursor.split(":", 1)
                timestamp = int(timestamp)
                if not batch_id.startswith("uib_"):
                    raise ValueError
            except (ValueError, AttributeError):
                raise ValueError("invalid_cursor") from None
            where.append("(created_ns, ui_batch_id) < (?, ?)")
            params.extend([timestamp, batch_id])
        query = """SELECT ui_batch_id, owner_user_id, owner_display_name, app_id,
                   (SELECT json_extract(s.metadata_json, '$.app_name') FROM snapshots s
                    WHERE s.ui_batch_id=batches.ui_batch_id ORDER BY s.rowid LIMIT 1) AS app_name,
                   status, report_status, delivery_status, created_ns FROM batches"""
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_ns DESC, ui_batch_id DESC LIMIT ?"
        db = self._connect()
        try:
            rows = [dict(row) for row in db.execute(query, (*params, limit + 1))]
        finally:
            db.close()
        items = rows[:limit]
        next_cursor = f"{items[-1]['created_ns']}:{items[-1]['ui_batch_id']}" if len(rows) > limit else ""
        return {"items": items, "next_cursor": next_cursor}

    def pending_dispatches(self, limit=100):
        db = self._connect()
        try:
            return [self._attempt(row) for row in db.execute(
                "SELECT * FROM case_attempts WHERE job_id IS NULL AND status='queued' ORDER BY rowid LIMIT ?",
                (max(1, min(int(limit), 100)),))]
        finally:
            db.close()

    def link_job(self, dispatch_key, job_id):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM case_attempts WHERE dispatch_key=?", (dispatch_key,)).fetchone()
            if not row:
                raise KeyError("dispatch_not_found")
            if job_id != dispatch_key or row["job_id"] not in (None, job_id):
                raise BatchConflict("dispatch_job_conflict")
            db.execute("UPDATE case_attempts SET job_id=? WHERE dispatch_key=? AND job_id IS NULL", (job_id, dispatch_key))
            result = self._attempt(db.execute("SELECT * FROM case_attempts WHERE dispatch_key=?", (dispatch_key,)).fetchone())
            db.execute("COMMIT")
            return result
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def append_case_attempt(self, batch_id, case_id, case_revision, target, compiled_case):
        """Append a later attempt for an already frozen case/target pair."""
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT a.* FROM case_attempts a
                                WHERE a.ui_batch_id=? AND a.case_id=? AND a.case_revision=?
                                AND a.runner_id=? AND a.device_id=? AND a.platform=?
                                ORDER BY a.attempt_no DESC LIMIT 1""",
                             (batch_id, case_id, case_revision, target["runner_id"], target["device_id"], target["platform"])).fetchone()
            if not row or _decoded(row["compiled_case_json"]) != compiled_case:
                raise BatchConflict("frozen_attempt_mismatch")
            attempt_no = row["attempt_no"] + 1
            dispatch_key = row["dispatch_key"].rsplit("_", 1)[0] + f"_{attempt_no}"
            db.execute("""INSERT INTO case_attempts(attempt_id, ui_batch_id, snapshot_id, case_id, case_revision,
                       runner_id, device_id, platform, attempt_no, dispatch_key, target_resolution_json,
                       compiled_case_json, status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (uuid.uuid4().hex, batch_id, row["snapshot_id"], row["case_id"], case_revision,
                        row["runner_id"], row["device_id"], row["platform"], attempt_no, dispatch_key,
                        row["target_resolution_json"], row["compiled_case_json"], "queued"))
            self._rollup(db, batch_id)
            db.execute("UPDATE batches SET report_status='pending' WHERE ui_batch_id=? AND report_requested_ns IS NOT NULL", (batch_id,))
            result = self._attempt(db.execute("SELECT * FROM case_attempts WHERE dispatch_key=?", (dispatch_key,)).fetchone())
            db.execute("COMMIT")
            return result
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()
