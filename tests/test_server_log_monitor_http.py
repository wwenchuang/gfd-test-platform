import json
import threading
import time

from task_server import app, auth
from test_identity import activate, create_member, identity_db
from test_platform_mcp_http import request

URL = "/api/platform/log-monitor/status"
UNIT = "midscene-task.service"


def test_monitor_http_projects_only_fixed_fields_and_uses_management_permission(identity_db, monkeypatch, tmp_path):
    from task_server.services import server_log_monitor_status as view

    path = tmp_path / "state.json"
    monkeypatch.setattr(view, "STATE_PATH", path)
    _, store = identity_db
    create_member(store)
    activate(store)
    admin = auth.create_session_token("admin")
    member = auth.create_session_token("member")
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert request(server, "GET", URL)[0] == 401
        assert request(server, "GET", URL, member)[0] == 403
        status, data, headers = request(server, "GET", URL, admin)
        assert status == 200 and data["status"] == "unavailable"
        assert headers["Cache-Control"] == "private, no-store"
        assert len(data["sources"]) == 4 and all(item["coverage"] == "unobserved" for item in data["sources"])
        end = int(time.time()) // 300 * 300
        source = {"window_end": end, "coverage": "complete", "matches": {"traceback": 2, "raw": "PRIVATE"},
                  "error_active": True, "delivery": "sent", "last_alert": "PRIVATE"}
        records = {unit: source for unit in view.UNITS}
        path.write_text(json.dumps({**records, "PRIVATE": {}}))
        status, data, _ = request(server, "GET", URL, admin)
        assert status == 200 and data["status"] == "current"
        first = data["sources"][0]
        assert first["unit"] == UNIT and first["matches"] == {"traceback": 2} and first["fresh"]
        assert "PRIVATE" not in json.dumps(data)
        source.update(window_end=end - 600, reason="PRIVATE", delivery="PRIVATE")
        path.write_text(json.dumps(records))
        status, data, _ = request(server, "GET", URL, admin)
        assert status == 200 and data["status"] == "stale"
        assert not data["sources"][0]["fresh"] and "PRIVATE" not in json.dumps(data)
        assert request(server, "GET", URL+"?file=/etc/passwd", admin)[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_history_reads_yesterday_and_deduplicates_each_service_window(tmp_path, monkeypatch):
    import sqlite3
    from test_server_log_monitor import monitor
    from task_server.services import server_log_monitor_status as view
    now = int(time.time()) // 300 * 300
    yesterday = now - 86400
    root = tmp_path / 'state'
    monkeypatch.setattr(view, 'STATE_PATH', root / 'state.json')
    def observe(*args):
        return dict(coverage='complete', matches={'http_5xx': 2})
    def scan(end):
        return monitor.scan([UNIT], root, end, observe_unit=observe, deliver=lambda _: 'disabled')
    scan(now - 49 * 3600)
    scan(yesterday)
    scan(yesterday)
    scan(now)
    rows, total, expected = view.overlapping_windows(yesterday - 45, yesterday - 2)
    assert len(rows) == total == 1 and expected == 4
    assert rows[0]['window_end'] == yesterday and rows[0]['matches'] == {'http_5xx':2}
    with sqlite3.connect(root / 'history.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM windows').fetchone()[0] == 2
