"""Device-free tests for the native Monkey protocol."""
import json
import random
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_cli_rejects_unbounded_run_before_any_transport():
    request = dict(platform="android", device_id="serial", app_id="com.example.app", event_count=10001, seed=1)
    cp = subprocess.run([sys.executable, str(ROOT / "mobile_monkey_runner.py")],
                        input=json.dumps(request), text=True, capture_output=True)
    assert cp.stdout.strip(), "runner must return a structured terminal outcome"
    result = json.loads(cp.stdout)
    assert (result["type"], result["status"], result["executed"]) == ("result", "failed", 0)


def test_seeded_gestures_are_bounded_and_browse_never_taps():
    from mobile_monkey_runner import gesture
    def sample(mode):
        rng = random.Random(12)
        return [gesture(rng, 380, 840, mode) for _ in range(60)]
    assert sample("mixed") == sample("mixed")
    assert {g[0] for g in sample("mixed")} == {"tap", "swipe"}
    assert {g[0] for g in sample("browse")} == {"swipe"}
    for _, points in sample("mixed"):
        for x, y in points:
            assert 25 < x < 355
            assert 50 < y < 790
        if len(points) == 2:
            assert max(abs(points[0][i]-points[1][i]) for i in (0, 1)) > 30


def test_android_evidence_only_releases_target_classification():
    from mobile_monkey_runner import AndroidEvidence
    e = AndroidEvidence("com.example.app")
    e.feed("11-27 12:00:00.123 93 22 I am_crash: [0,278,com.example.app.other,0,secretToken]")
    assert e.issue() is None
    e.feed("11-27 12:00:00.124 93 22 I am_anr: [0,278,com.example.app,0,Input dispatching timed out (secret)]")
    assert e.issue().kind == "am_anr"
    assert "reason=input dispatching timed out" in e.issue().evidence
    e.feed("11-27 12:00:00.125 93 22 I am_crash: [0,278,com.example.app:worker,0,java.lang.Error,secretToken]")
    assert e.issue().kind == "am_crash"
    assert "secret" not in e.issue().evidence


def test_ios_crash_report_binds_bundle_and_time(tmp_path):
    from mobile_monkey_runner import ios_crash
    path = tmp_path / "app.ips"
    header = dict(bug_type="309", bundleID="com.example.app", timestamp="2026-11-27 12:00:00.00 +0000")
    path.write_text(json.dumps(header) + '\n' + '{"private":"mytoken"}')
    assert ios_crash(path, "com.example.app", 1795780799, 1795780801).kind == "ios_crash"
    assert ios_crash(path, "com.example.app.other", 1795780799, 1795780801) is None
    assert ios_crash(path, "com.example.app", 1795780801, 1795780802) is None


def test_stopped_and_foreground_loss_never_send_more_input():
    from mobile_monkey_runner import Run, Interrupted, drive
    request = dict(platform="ios", device_id="1234", app_id="com.example.app", event_count=3, seed=1)
    seen, calls = [], []
    run = Run(request, seen.append, lambda: False)
    with pytest.raises(Interrupted):
        drive(run, lambda: (len(calls) == 0, 375, 800), calls.append)
    assert run.executed == len(calls) == 1
    assert [r["executed"] for r in seen] == [1]
    run = Run(request, seen.append, lambda: True)
    with pytest.raises(Interrupted):
        drive(run, lambda: (True, 375, 800), calls.append)
    assert run.executed == 0


def test_driver_discards_raw_transport_errors():
    import mobile_monkey_runner as monkey
    req = dict(platform="android", device_id="serial", app_id="com.example.app", event_count=1, seed=1)
    with patch.object(monkey, "android", side_effect=OSError("secretToken1234")):
        result = monkey.execute(req, lambda _: None)
    assert result["status"] == "inconclusive"
    assert "secretToken" not in str(result)


def test_interleaved_java_and_native_frames_never_cross_processes():
    from mobile_monkey_runner import AndroidEvidence
    e = AndroidEvidence("com.example.app")
    def line(pid, tag, body):
        e.feed(f"2026-11-27 08:12:13.221  {pid}   12 E {tag}: {body}")
    line(201, "AndroidRuntime", "Process: com.example.app, PID: 201")
    line(203, "AndroidRuntime", "Process: com.private.other, PID: 203")
    line(203, "AndroidRuntime", "    at com.private.Secret.one(Secret.java:12)")
    line(201, "AndroidRuntime", "    at com.example.app.View.open(View.java:52)")
    line(14, "am_crash", "[0,201,com.example.app,0,java.lang.IllegalStateException,token=do-not-share,View.java,52]")
    evidence = e.issue().evidence
    assert "frame=com.example.app.View.open(View.java:52)" in evidence
    assert "exception_type=java.lang.IllegalStateException" in evidence
    assert "timestamp=2026-11-27 08:12:13.221" in evidence
    assert "Secret" not in evidence and "token" not in evidence
    e = AndroidEvidence("com.example.app")
    line(551, "DEBUG", "pid: 990, tid: 991, name: fake  >>> com.private.other <<<")
    line(552, "DEBUG", "pid: 201, tid: 202, name: fake  >>> com.example.app <<<")
    line(552, "DEBUG", "signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x1")
    line(551, "DEBUG", "    #00 pc 000000000034d194  /user/secret/libOther.so (other+4)")
    line(552, "DEBUG", "    #00 pc 000000000034d194  /user/secret/libApp.so (render+4)")
    evidence = e.issue().evidence
    assert "signal=SIGSEGV" in evidence and "frame=libApp.so+0x34d194" in evidence
    assert "Other" not in evidence and "secret" not in evidence


def test_ios_ips_faulting_thread_stack_and_no_other_threads(tmp_path):
    from mobile_monkey_runner import ios_crash
    path = tmp_path / "app.ips"
    header = dict(bug_type="309", bundleID="com.example.app", timestamp="2026-11-27 12:00:00.00 +0000")
    report = dict(pid=199, exception={"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV", "message": "secret"},
                  faultingThread=1, threads=[{"frames": [{"symbol": "Other", "imageIndex": 1}]},
                        {"frames": [{"symbol": "render", "imageIndex": 0, "imageOffset": 33},
                                    {"imageIndex": 1, "imageOffset": 80}]}],
                  usedImages=[{"name": "App", "path": "/user/private/app"}, {"name": "UIKitCore"}])
    path.write_text(json.dumps(header) + "\n" + json.dumps(report))
    evidence = ios_crash(path, "com.example.app", 1795780799, 1795780801).evidence
    for fragment in ("signal=SIGSEGV", "exception_type=EXC_BAD_ACCESS", "frame=App+0x21 render",
                     "frame=UIKitCore+0x50", "symbolication=partial", "thread=1"):
        assert fragment in evidence
    assert not any(v in evidence for v in ("Other", "secret", "private"))


def test_parent_pipe_eof_cancels_run():
    import os
    import threading
    from mobile_monkey_runner import watch_parent
    read_fd, write_fd = os.pipe()
    stopped = threading.Event()
    watch_parent(read_fd, stopped)
    assert not stopped.is_set()
    os.close(write_fd)
    assert stopped.wait(1)


def test_window_bounds_are_taken_from_exact_focused_window():
    from mobile_monkey_runner import android_window
    dump = """mCurrentFocus=Window{8311 u0 com.example.app/.Main}
    Window #2 Window{e333 u0 com.other.app/.A}:
      Frames: parent=[0,0][1080,1700] display=[0,0][1080,1700] frame=[0,50][500,1600]
    Window #1 Window{8311 u0 com.example.app/.Main}:
      Frames: parent=[0,0][1080,1700] display=[0,0][1080,1700] frame=[500,50][1080,1600]
"""
    assert android_window(dump, "com.example.app") == (500, 50, 580, 1550)
    assert android_window(dump, "com.other.app") is None


@pytest.mark.parametrize("lost_focus,crash,status,executed", [
    (False, False, "passed", 2), (True, False, "inconclusive", 1), (True, True, "crashed", 1)])
def test_ios_session_and_report_end_to_end(monkeypatch, lost_focus, crash, status, executed):
    """Simulate only external device transports, exercise the actual WDA JSON protocol."""
    import contextlib
    import io
    import plistlib
    import mobile_monkey_runner as m

    selected = "single-udid"
    app = "com.example.app"
    requests, moves, events = [], [], []
    def cmd(args, **kwargs):
        assert args[1:3] == ["-u", selected]
        if args[0] == "ideviceinstaller":
            return plistlib.dumps([{"CFBundleIdentifier": app, "CFBundleExecutable": "Sample"}])
        assert args[0] == "ideviceinfo"
        return b"1795780800"
    monkeypatch.setattr(m, "command", cmd)

    def pull(args, **kwargs):
        assert args[:6] == ["idevicecrashreport", "-u", selected, "--keep", "--filter", "Sample"]
        if crash and Path(args[-1]).name == "after":
            p = Path(args[-1]) / "Sample.ips"
            p.write_text(json.dumps(dict(bug_type="309", bundleID=app, timestamp="2026-11-27 12:00:00.00 +0000"))
                         + '\n{"exception":{"type":"EXC_CRASH","signal":"SIGABRT","details":"sensitive"}}')
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(m.subprocess, "run", pull)
    monkeypatch.setattr(m.time, "sleep", lambda n: None)
    @contextlib.contextmanager
    def forward(device):
        assert device == selected
        yield 9991
    monkeypatch.setattr(m, "runner", lambda: {"ios_usb_forward": forward})

    class WDA:
        def open(self, req, timeout):
            from urllib.parse import urlsplit
            assert req.full_url.startswith("http://127.0.0.1:9991/")
            path = urlsplit(req.full_url).path
            requests.append((req.get_method(), path))
            value = None
            if path == "/status":
                value = {"ready": True}
            elif path == "/session" and req.method == "POST":
                value = {"sessionId": "test-session"}
            elif path.endswith("/wda/apps/launch"):
                assert json.loads(req.data)["bundleId"] == app
            elif path.endswith("/window/size"):
                value = {"width": 375, "height": 844}
            elif path.endswith("/wda/activeAppInfo"):
                value = {"bundleId": "com.apple.springboard" if lost_focus and moves else app}
            elif path.endswith("/actions"):
                moves.append(json.loads(req.data)["actions"][0]["actions"])
            else:
                assert req.method == "DELETE" and path == "/session/test-session"
            return io.BytesIO(json.dumps({"value": value}).encode())

    monkeypatch.setattr(m.urllib.request, "build_opener", lambda *_: WDA())
    r = m.execute(dict(platform="ios", device_id=selected, app_id=app, event_count=2, seed=1), events.append)
    assert (r["status"], r["executed"]) == (status, executed)
    assert len(moves) == len(events) == executed
    assert requests[-1] == ("DELETE", "/session/test-session")
    if crash:
        assert "kind=ios_crash" in r["crash_log"] and "signal=SIGABRT" in r["crash_log"]
        assert "sensitive" not in str(r)
    else:
        assert not r["crash_log"]


def test_android_cli_with_exact_device_and_fake_live_logcat(tmp_path):
    import os
    adb = tmp_path / "adb"
    adb.write_text("""#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
assert sys.argv[1:3] == ['-s', 'chosen-serial']
cmd = sys.argv[3:]
marker = Path(__file__).with_name('markers')
if cmd[0] == 'get-state': print('device')
elif cmd[:3] == ['shell', 'cmd', 'package']: print('com.example.app/.Main')
elif cmd[:2] == ['shell', 'am']: print('Status: ok')
elif cmd[:2] == ['shell', 'log']:
    with marker.open('a') as f: print(cmd[-1], file=f)
elif cmd[0] == 'logcat':
    seen = set()
    while True:
        for row in marker.read_text().splitlines() if marker.exists() else []:
            if row not in seen:
                print('2026-11-27 12:00:00.001 123 456 I MonkeyQA: ' + row, flush=True)
                seen.add(row)
        time.sleep(.02)
elif cmd[:2] == ['shell', 'dumpsys']:
    print('mCurrentFocus=Window{003 u0 com.example.app/.Main}')
    print('  Window #2 Window{003 u0 com.example.app/.Main}:')
    print('    Frames: parent=[0,0][1080,1200] frame=[30,40][1050,1130]')
elif cmd[:3] == ['shell','input','swipe']:
    assert len(cmd) == 8
else: sys.exit(1)
""")
    adb.chmod(0o700)
    env = dict(os.environ, ADB_BIN=str(adb))
    cp = subprocess.run([sys.executable, str(ROOT / "mobile_monkey_runner.py")],
                        input=json.dumps(dict(platform="android", device_id="chosen-serial", app_id="com.example.app",
                                              event_count=2, seed=1)), text=True, capture_output=True, env=env, timeout=12)
    assert cp.returncode == 0
    progress, progress2, final = [json.loads(line) for line in cp.stdout.splitlines()]
    assert (progress["executed"], progress2["executed"]) == (1, 2)
    assert (final["status"], final["executed"], final["crash_log"]) == ("passed", 2, "")


@pytest.mark.parametrize("target", [{}, {"duration_seconds": 59}, {"duration_seconds": 86401},
                                   {"duration_seconds": True}, {"duration_seconds": 60, "event_count": 4}])
def test_duration_and_count_are_mutually_exclusive_and_bounded(target):
    from mobile_monkey_runner import execute
    result = execute(dict(platform="android", device_id="selected", app_id="com.example.app", seed=1,
                          **target), lambda event: None)
    assert result["status"] == "failed" and result["elapsed_seconds"] == 0


def test_duration_starts_at_first_gesture_not_setup_and_can_exceed_event_cap():
    from mobile_monkey_runner import Run, drive
    now = [100.0]
    def clock():
        return now[0]
    events = []
    req = dict(platform="ios", device_id="selected", app_id="com.example.app", seed=1, duration_seconds=60)
    run = Run(req, events.append, lambda: False, clock=clock)
    now[0] += 40
    def send(action):
        now[0] += 1 / 256
    drive(run, lambda: (True, 375, 800), send)
    assert run.executed == 60 * 256
    assert events[0]["elapsed_seconds"] == 0
    assert events[-1]["elapsed_seconds"] == 60
    assert run.elapsed_seconds == 60
    now[0] += 50  # final crash log retrieval
    assert run.elapsed_seconds == 60


def test_duration_stops_at_deadline_even_if_viewport_check_finishes_late():
    from mobile_monkey_runner import Run, drive
    now = [0]
    req = dict(platform="android", device_id="selected", app_id="com.example.app", seed=1, duration_seconds=60)
    run = Run(req, lambda row: None, lambda: False, clock=lambda: now[0])
    sends, probes = [], []
    def viewport():
        probes.append(True)
        now[0] += 2
        return True, 375, 800
    def send(action):
        sends.append(now[0])
        now[0] += 59
    drive(run, viewport, send)
    assert sends == [2] and len(probes) == 3
    assert run.elapsed_seconds == 61


def test_stop_freezes_duration_on_interruption_before_diagnostics():
    from mobile_monkey_runner import Run, drive, Interrupted
    now = [20]
    req = dict(platform="android", device_id="selected", app_id="com.example.app", seed=1, duration_seconds=60)
    events = []
    run = Run(req, events.append, lambda: bool(events), clock=lambda: now[0])
    def send(_):
        now[0] += 3
    with pytest.raises(Interrupted):
        drive(run, lambda: (True, 375, 800), send)
    now[0] += 40
    assert run.executed == 1 and run.elapsed_seconds == 3
