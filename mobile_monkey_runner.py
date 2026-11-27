#!/usr/bin/env python3
"""Bounded native app stability run. One JSON request on stdin, JSONL on stdout."""
import json
import os
import plistlib
import random
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path


class Interrupted(Exception):
    """A controlled, safe-to-publish reason the run could not be verified."""


class AppIssue(Exception):
    """Allowlisted native diagnostic evidence from this observation window."""

    def __init__(self, kind, fields):
        self.kind = kind
        self.evidence = "\n".join([f"kind={kind}", *(f"{k}={v}" for k, v in fields)])[:4096]
        super().__init__(kind)


@lru_cache(maxsize=1)
def runner():
    return runpy.run_path(str(Path(__file__).with_name("mac-midscene-runner.py")))


class Run:
    def __init__(self, request, emit, stopped, clock=None):
        self.platform = request.get("platform")
        self.device = request.get("device_id")
        self.app = request.get("app_id")
        self.count = request.get("event_count")
        self.duration = request.get("duration_seconds")
        if ("event_count" in request) == ("duration_seconds" in request):
            raise ValueError
        if "event_count" in request:
            if type(self.count) is not int or not 1 <= self.count <= 10000:
                raise ValueError
            timeout = min(1800, 90 + self.count * 12)
        else:
            if type(self.duration) is not int or not 60 <= self.duration <= 86400:
                raise ValueError
            timeout = self.duration + 180
        seed = request.get("seed")
        self.mode = request.get("mode", "browse")
        if (self.platform not in ("android", "ios") or self.mode not in ("browse", "mixed")
                or not isinstance(self.device, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", self.device)
                or not isinstance(self.app, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*(\.[a-zA-Z0-9_-]+)+", self.app)
                or len(self.app) > 255
                or type(seed) is not int or not 0 <= seed <= 2147483647):
            raise ValueError
        self.rng = random.Random(seed)
        self.emit = emit
        self.stopped = stopped
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + timeout
        self.started = None
        self.finished = None
        self.executed = 0
        self.history = deque(maxlen=12)

    @property
    def elapsed_seconds(self):
        if self.started is None:
            return 0
        end = self.finished if self.finished is not None else self.clock()
        return int(end - self.started)

    def target_reached(self):
        if self.count is not None:
            return self.executed >= self.count
        return self.elapsed_seconds >= self.duration

    def check(self):
        if self.stopped():
            raise Interrupted("收到停止请求")
        if self.clock() >= self.deadline:
            raise Interrupted("已达到本轮超时上限")


def gesture(rng, width, height, mode):
    if not 64 <= width <= 16384 or not 64 <= height <= 16384:
        raise Interrupted("无法确认应用可视区域")
    x, y = rng.randint(width//6, width*5//6), rng.randint(height//6, height*5//6)
    if mode == "mixed" and rng.randrange(2) == 0:
        return "tap", [(x, y)]
    if rng.randrange(2):
        dx = rng.randint(width//6, width//3)
        end = (x + (dx if x < width//2 else -dx), y)
    else:
        dy = rng.randint(height//6, height//3)
        end = (x, y + (dy if y < height//2 else -dy))
    return "swipe", [(x, y), end]


def drive(run, viewport, send):
    try:
        while not run.target_reached():
            run.check()
            foreground, width, height = viewport()
            if not foreground:
                raise Interrupted("目标应用离开前台或焦点无法确认")
            action = gesture(run.rng, width, height, run.mode)
            run.check()
            # A focus check can outlast the remaining time. Do not dispatch a
            # fresh input after the time target. A dispatched event may finish.
            if run.target_reached():
                break
            if run.started is None:
                run.started = run.clock()
            send(action)
            run.executed += 1
            run.history.append(f"#{run.executed} {action[0]}")
            elapsed = run.elapsed_seconds
            detail = (f"已执行 {run.executed}/{run.count} 个事件" if run.count is not None
                      else f"已运行 {elapsed}/{run.duration} 秒，已执行 {run.executed} 个事件")
            run.emit(dict(type="progress", executed=run.executed, elapsed_seconds=elapsed, message=detail))
    finally:
        run.finished = run.clock()
    run.check()
    if not viewport()[0]:
        raise Interrupted("末次事件后目标应用离开前台")


def command(args, timeout=10):
    cp = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    if cp.returncode:
        raise Interrupted("设备命令失败，请检查 USB 连接、工具与设备状态")
    return cp.stdout


SYMBOL = r"[A-Za-z_$][A-Za-z0-9_.$:<>+~-]{0,159}"
LOG_LINE = re.compile(r"^(?P<time>(?:\d{4}-)?\d\d-\d\d \d\d:\d\d:\d\d\.\d{3,6})\s+"
                      r"(?P<pid>\d+)\s+\d+\s+[VDIWEFA]\s+(?P<tag>[\w]+)\s*:\s?(?P<body>.*)$")


class AndroidEvidence:
    def __init__(self, app):
        self.app = app
        self.java_pids = set()
        self.native_writers = {}
        self.frames = {}
        self.signals = {}
        self.record = None
        self.lock = threading.Lock()

    def feed(self, line):
        m = LOG_LINE.fullmatch(line)
        if not m:
            return
        pid, tag, body = m["pid"], m["tag"], m["body"].strip()
        with self.lock:
            if tag in ("am_crash", "am_anr"):
                self.event(m["time"], tag, body)
            elif tag == "AndroidRuntime":
                identity = re.fullmatch(r"Process: ([^, ]+), PID: (\d+)", body)
                if identity and identity[2] == pid:
                    self.java_pids.discard(pid)
                    if identity[1].split(":")[0] == self.app:
                        self.java_pids.add(pid)
                        self.frames[pid] = []
                if pid in self.java_pids:
                    frame = re.fullmatch(r"at (" + SYMBOL + r"\((?:[\w$-]{1,96}\.(?:java|kt):\d+|"
                                         r"Native Method|Unknown Source)\))", body)
                    if frame and len(self.frames[pid]) < 8:
                        self.frames[pid].append(frame[1])
            elif tag == "DEBUG":
                self.native(pid, m["time"], body)

    def native(self, writer, stamp, body):
        identity = re.search(r"pid: (\d+), tid: (\d+),.*?>>> ([^ ]+) <<<", body)
        if identity:
            self.native_writers.pop(writer, None)
            if identity[3].split(":")[0] == self.app:
                self.native_writers[writer] = (identity[1], identity[2])
                self.frames[identity[1]] = []
        if writer not in self.native_writers:
            return
        pid, tid = self.native_writers[writer]
        sig = re.search(r"^signal \d+ \((SIG[A-Z0-9]{1,12})\)", body)
        if sig:
            self.signals[pid] = sig[1]
            self.record = ("native_crash", stamp, pid, [("thread", tid)])
        frame = re.match(r"#\d+\s+pc\s+([a-fA-F0-9]{1,16})\s+(\S+)(?:\s+\(([^)]+)\))?", body)
        if frame and len(self.frames[pid]) < 8:
            name = frame[2].rsplit("/", 1)[-1]
            if re.fullmatch(r"[A-Za-z0-9._+-]{1,96}", name):
                symbol = frame[3] if frame[3] and re.fullmatch(SYMBOL, frame[3]) else ""
                self.frames[pid].append(f"{name}+0x{int(frame[1], 16):x}" + (f" {symbol}" if symbol else ""))

    def event(self, stamp, tag, body):
        match = re.match(r"^\[\d+,(\d+),([^,]+),[^,]*,(.*)\]$", body)
        if not match or match[2].split(":")[0] != self.app:
            return
        if self.record and self.record[0] != "am_anr":
            return
        fields = []
        if tag == "am_anr":
            reason = match[3].lower()
            for label in ("input dispatching timed out", "executing service", "broadcast of intent",
                          "content provider", "no focused window"):
                if label in reason:
                    fields.append(("reason", label))
                    break
        else:
            cls = match[3].split(",", 1)[0]
            if re.fullmatch(SYMBOL, cls):
                fields.append(("exception_type", cls))
            # Messages can contain commas and private app values. Keep only the
            # final Java/Kotlin source basename and numeric line when present.
            source = re.search(r",([\w$-]{1,96}\.(?:java|kt)),(\d{1,8})$", match[3])
            if source:
                fields.append(("frame", f"{source[1]}:{source[2]}"))
        self.record = (tag, stamp, match[1], fields)

    def issue(self):
        with self.lock:
            if not self.record:
                return None
            kind, stamp, pid, details = self.record
            fields = [("timestamp", stamp), ("pid", pid), *details]
            if pid in self.signals:
                fields.append(("signal", self.signals[pid]))
            fields.extend(("frame", f) for f in self.frames.get(pid, []))
            return AppIssue(kind, fields)


class AndroidLog:
    """Live events and crash monitor, bounded by unique device-side markers."""
    def __init__(self, adb, app):
        self.adb = adb
        self.token = uuid.uuid4().hex
        self.begin = threading.Event()
        self.end = threading.Event()
        self.evidence = AndroidEvidence(app)

    def read(self, stream):
        for raw in stream:
            line = raw.decode("utf-8", "replace").strip()
            parsed = LOG_LINE.fullmatch(line)
            if parsed and parsed["tag"] == "MonkeyQA" and parsed["body"] == self.token + "begin":
                self.begin.set()
            elif parsed and parsed["tag"] == "MonkeyQA" and parsed["body"] == self.token + "end":
                self.end.set()
                break
            elif self.begin.is_set():
                self.evidence.feed(line)

    def marker(self, name):
        command(self.adb + ["shell", "log", "-p", "i", "-t", "MonkeyQA", self.token + name])
        if not getattr(self, name).wait(4):
            raise Interrupted("未收到 logcat 采集边界，日志覆盖不完整")

    def verify(self):
        issue = self.evidence.issue()
        if issue:
            raise issue
        if self.process.poll() is not None:
            raise Interrupted("logcat 在测试中断开")

    @contextmanager
    def collect(self):
        self.process = subprocess.Popen(self.adb + ["logcat", "-b", "events", "-b", "main", "-b", "crash",
                                                   "-v", "threadtime", "-v", "year",
                                                   "-T", "1", "MonkeyQA:I", "am_crash:I", "am_anr:I",
                                                   "AndroidRuntime:E", "DEBUG:F", "*:S"],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        thread = threading.Thread(target=self.read, args=(self.process.stdout,), daemon=True)
        thread.start()
        try:
            self.marker("begin")
            try:
                yield
            finally:
                # The diagnostic thread may still be writing stack frames after
                # the app loses focus. Drain through a confirmed end marker.
                time.sleep(1)
                try:
                    self.marker("end")
                finally:
                    self.verify()
        finally:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            thread.join(timeout=2)
            self.process.stdout.close()


def android_window(dump, app):
    focus = re.search(r"mCurrentFocus=Window\{([^}\n]+)}", dump)
    if not focus or not re.search(r"\s" + re.escape(app) + r"/", focus[1]):
        return None
    # Use the frame of this very window; fullscreen display dimensions would
    # allow touches in the adjacent application in split-screen mode.
    window = re.search(r"(?ms)^\s*Window #\d+ Window\{" + re.escape(focus[1])
                       + r"}:.*?(?=^\s*Window #\d+ Window\{|\Z)", dump)
    if not window:
        raise Interrupted("无法确认焦点窗口的边界")
    frame = re.search(r"\b(?:frame|mFrame)=\[(-?\d+),(-?\d+)]\[(-?\d+),(-?\d+)]", window[0])
    if not frame:
        raise Interrupted("无法读取目标应用的可视区域")
    left, top, right, bottom = map(int, frame.groups())
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise Interrupted("目标应用窗口区域异常")
    return left, top, right-left, bottom-top


def android(run):
    adb = [runner()["recording_adb_path"](), "-s", run.device]
    if command(adb + ["get-state"]).strip() != b"device":
        raise Interrupted("所选 Android 手机不可用")
    shell = adb + ["shell"]
    component = command(shell + ["cmd", "package", "resolve-activity", "--brief", "-a",
                                 "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER",
                                 "-p", run.app]).decode().splitlines()
    if not component or not re.fullmatch(re.escape(run.app) + r"/[.a-zA-Z0-9_$]+", component[-1]):
        raise Interrupted("目标包没有可核对的启动 Activity")
    log = AndroidLog(adb, run.app)

    origin = (0, 0)

    def viewport():
        nonlocal origin
        log.verify()
        data = command(shell + ["dumpsys", "window", "-a"]).decode("utf-8", "replace")
        bounds = android_window(data, run.app)
        if bounds is None:
            return False, 0, 0
        left, top, width, height = bounds
        origin = (left, top)
        return True, width, height

    def send(action):
        kind, points = action
        args = [str(n+offset) for point in points for n, offset in zip(point, origin)]
        command(shell + ["input", kind] + args + (["360"] if kind == "swipe" else []))

    with log.collect():
        run.check()
        # Component class names may contain '$'. ADB shell performs a remote
        # shell parse, so quote this already validated component as one token.
        started = command(shell + ["am", "start", "-W", "-n", "'" + component[-1] + "'"], timeout=20)
        if not re.search(rb"(?m)^Status:\s*ok\s*$", started):
            raise Interrupted("目标包未返回启动成功回执")
        drive(run, viewport, send)
        time.sleep(1)
        if not viewport()[0]:
            raise Interrupted("收尾时目标应用已离开前台")
    return "Android logcat events 采集起止标记完整，未观察到目标包 am_crash/am_anr"


def ios_crash(path, app, start, end):
    # Only the report header is required; stack traces and user paths stay on the device.
    with path.open("rb") as f:
        first = f.readline(64 * 1024)
    try:
        header = json.loads(first)
        if header.get("bug_type") != "309":
            return None
        if not header.get("bundleID"):
            raise ValueError
        if header["bundleID"] != app:
            return None
        stamp = datetime.strptime(header["timestamp"], "%Y-%m-%d %H:%M:%S.%f %z")
        if start <= stamp.timestamp() <= end:
            fields = [("timestamp", stamp.isoformat())]
            fields.extend(ios_stack(path))
            return AppIssue("ios_crash", fields)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise Interrupted("新增的 iOS 诊断报告无法归属或读取")


def ios_stack(path):
    # Accept bounded, typed diagnostic fields only, never raw messages, register
    # data, image paths or exception reasons from a third-party app.
    fields = []
    try:
        with path.open("rb") as f:
            f.readline(65536)
            raw = f.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return [("symbolication", "unavailable")]
        body = json.loads(raw)
        if type(body.get("pid")) is int:
            fields.append(("pid", body["pid"]))
        for key, name in (("type", "exception_type"), ("signal", "signal")):
            value = (body.get("exception") or {}).get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z_0-9]{1,63}", value):
                fields.append((name, value))
        threads = body.get("threads", [])
        index = body.get("faultingThread")
        if type(index) is not int:
            index = next((i for i, thread in enumerate(threads) if thread.get("triggered")), -1)
        images = body.get("usedImages", [])
        if not 0 <= index < len(threads):
            return fields + [("symbolication", "unavailable")]
        fields.append(("thread", index))
        has_symbol = False
        for f in threads[index].get("frames", [])[:8]:
            i = f.get("imageIndex")
            if type(i) is not int or not 0 <= i < len(images):
                continue
            name = images[i].get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[\w.$+-]{1,96}", name):
                continue
            offset = f.get("imageOffset")
            frame = name
            if type(offset) is int and 0 <= offset < 2**64:
                frame += f"+0x{offset:x}"
            symbol = f.get("symbol")
            if isinstance(symbol, str) and (re.fullmatch(SYMBOL, symbol) or
                    re.fullmatch(r"[-+]\[[\w.$]+ [\w:]+\]", symbol)) and len(symbol) <= 160:
                frame += " " + symbol
                has_symbol = True
            fields.append(("frame", frame))
        fields.append(("symbolication", "partial" if has_symbol else "unavailable"))
        return fields
    except (ValueError, KeyError, IndexError, AttributeError, TypeError, OSError):
        return fields + [("symbolication", "unavailable")]


def ios_reports(run, name, directory):
    cp = subprocess.run(["idevicecrashreport", "-u", run.device, "--keep", "--filter", name, str(directory)],
                        stdin=subprocess.DEVNULL, capture_output=True, timeout=35)
    if cp.returncode or re.search(rb"(?im)\b(error|failed|could not|warning)\b", cp.stdout + cp.stderr):
        raise Interrupted("指定 iPhone 的诊断报告采集不完整")
    return {path.relative_to(directory) for path in directory.rglob("*") if path.is_file() and not path.is_symlink()}


def ios_time(run):
    raw = command(["ideviceinfo", "-u", run.device, "-k", "TimeIntervalSince1970"]).strip()
    return int(float(raw))


def ios(run):
    runner_api = runner()
    plist = command(["ideviceinstaller", "-u", run.device, "list", "-b", run.app, "-a", "CFBundleIdentifier",
                     "-a", "CFBundleExecutable", "--xml"])
    name = next((a.get("CFBundleExecutable") for a in plistlib.loads(plist)
                 if a.get("CFBundleIdentifier") == run.app), "")
    if not isinstance(name, str) or not name or len(name) > 180 or "/" in name:
        raise Interrupted("未能从所选 iPhone 确认目标包及其诊断名称")
    with tempfile.TemporaryDirectory(prefix="monkey-ios-") as directory:
        before, after = Path(directory)/"before", Path(directory)/"after"
        before.mkdir()
        after.mkdir()
        previous = ios_reports(run, name, before)
        start = ios_time(run)
        try:
            with runner_api["ios_usb_forward"](run.device) as port:
                ios_session(run, port)
        finally:
            time.sleep(2)
            current = ios_reports(run, name, after)
            end = ios_time(run) + 1
            unreadable = False
            for rel in sorted(current - previous):
                if rel.suffix.lower() != ".ips":
                    unreadable = True
                    continue
                try:
                    issue = ios_crash(after / rel, run.app, start, end)
                except Interrupted:
                    unreadable = True
                    continue
                if issue:
                    raise issue
            if unreadable:
                raise Interrupted("新增的 iOS 诊断报告无法完整核对")
    return "已按设备时间、Bundle ID 和新增 .ips 核对诊断目录，未观察到本轮目标应用崩溃"


def ios_session(run, port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method=method)
        with opener.open(req, timeout=12) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise Interrupted("WDA 响应超过上限")
        result = json.loads(raw)
        if isinstance(result.get("value"), dict) and result["value"].get("error"):
            raise Interrupted("WDA 未确认设备操作")
        return result

    if request("GET", "/status")["value"].get("ready") is not True:
        raise Interrupted("所选 iPhone 的 WDA 尚未就绪")
    run.check()
    result = request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
    sid = result.get("sessionId") or (result.get("value") or {}).get("sessionId")
    if not sid:
        raise Interrupted("WDA 未创建执行会话")
    base = "/session/" + urllib.parse.quote(str(sid), safe="")

    def viewport():
        size = request("GET", base + "/window/size")["value"]
        info = request("GET", "/wda/activeAppInfo")["value"]
        return info.get("bundleId") == run.app, int(size["width"]), int(size["height"])

    def send(action):
        kind, points = action
        x, y = points[0]
        moves = [{"type": "pointerMove", "duration": 0, "x": x, "y": y},
                 {"type": "pointerDown", "button": 0}]
        if kind == "swipe":
            x, y = points[1]
            moves.append({"type": "pointerMove", "duration": 360, "x": x, "y": y})
        else:
            moves.append({"type": "pause", "duration": 70})
        moves.append({"type": "pointerUp", "button": 0})
        request("POST", base + "/actions", {"actions": [{"type": "pointer", "id": "finger",
                "parameters": {"pointerType": "touch"}, "actions": moves}]})

    try:
        run.check()
        request("POST", base + "/wda/apps/launch", {"bundleId": run.app})
        drive(run, viewport, send)
    finally:
        request("DELETE", base)


def execute(request, emit, stopped=lambda: False):
    try:
        if not isinstance(request, dict):
            raise ValueError
        run = Run(request, emit, stopped)
    except (TypeError, ValueError):
        return dict(type="result", status="failed", executed=0, elapsed_seconds=0, log="", crash_log="", message="无效的设备、应用或执行参数")
    crash_log = ""
    try:
        run.check()
        evidence = (ios if run.platform == "ios" else android)(run)
        run.check()
        status, message = "passed", "已达到设定的测试目标并完成崩溃日志核对"
    except AppIssue as exc:
        evidence = exc.kind
        crash_log = exc.evidence
        status = "failed" if exc.kind == "am_anr" else "crashed"
        message = "发现目标应用 ANR" if status == "failed" else "发现本轮目标应用崩溃记录"
    except Interrupted as exc:
        status, message, evidence = "inconclusive", str(exc), ""
    except Exception as exc:
        status, message = "inconclusive", "Runner 或设备采集异常"
        evidence = "错误类型 " + type(exc).__name__
    return dict(type="result", status=status, executed=run.executed, elapsed_seconds=run.elapsed_seconds,
                log="\n".join([*run.history, evidence]).strip()[:2048], crash_log=crash_log, message=message)


def watch_parent(fd, stopped):
    def read_pipe():
        try:
            with os.fdopen(fd, "rb", buffering=0) as pipe:
                while pipe.read(1):
                    pass
        except OSError:
            pass
        finally:
            stopped.set()
    if fd < 3:
        stopped.set()
        return
    threading.Thread(target=read_pipe, name="monkey-parent-liveness", daemon=True).start()


def main():
    stopped = threading.Event()
    parent_fd = os.getenv("MIDSCENE_MONKEY_PARENT_PIPE_FD")
    if parent_fd is not None:
        try:
            watch_parent(int(parent_fd), stopped)
        except ValueError:
            stopped.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    def emit(row):
        print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        raw = sys.stdin.buffer.read(8193)
        request = json.loads(raw) if len(raw) <= 8192 else None
    except (ValueError, UnicodeError):
        request = None
    emit(execute(request, emit, stopped.is_set))


if __name__ == "__main__":
    main()
