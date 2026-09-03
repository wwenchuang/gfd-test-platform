import json
from pathlib import Path
import signal

from load_agent.runtime import K6Runtime


class _Stdout:
    def __init__(self, lines):
        self.lines = list(lines)

    def readline(self):
        return self.lines.pop(0) if self.lines else ""


class _Process:
    def __init__(self, lines=(), *, returncode=0, hangs_after_interrupt=False):
        self.stdout = _Stdout(lines)
        self.stderr = _Stdout([])
        self.returncode = None
        self.final_returncode = returncode
        self.signals = []
        self.hangs_after_interrupt = hangs_after_interrupt

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        if not self.hangs_after_interrupt:
            self.returncode = 0

    def wait(self, timeout=None):
        if self.hangs_after_interrupt and self.returncode is None:
            raise TimeoutError("still running")
        if self.returncode is None:
            self.returncode = self.final_returncode
        return self.returncode

    def kill(self):
        self.signals.append(signal.SIGKILL)
        self.returncode = -9


class _Sink:
    def __init__(self):
        self.metrics = []
        self.samples = []

    def post_metrics(self, payload, batch_id):
        self.metrics.append((batch_id, payload))

    def post_samples(self, payload, batch_id):
        self.samples.append((batch_id, payload))


def _shard(secret="business-secret"):
    return {
        "id": "shard-1",
        "script": "export default function() {}",
        "environment": {"SECRET_TOKEN": secret, "BASE_URL_DEFAULT": "https://api.example.com"},
        "dataset_rows": [{"keyword": "收纳盒"}],
    }


def test_runtime_streams_metrics_and_removes_private_work_directory(tmp_path):
    line = json.dumps({
        "type": "Point",
        "metric": "http_reqs",
        "data": {"time": "2026-09-03T08:00:00+00:00", "value": 1, "tags": {"step_id": "search"}},
    }) + "\n"
    process = _Process([line])
    commands = []

    runtime = K6Runtime(tmp_path, popen=lambda *_args, **_kwargs: process, poll_interval=0)
    sink = _Sink()
    result = runtime.run(_shard(), lambda: commands, sink)

    assert result.state == "finished"
    assert sink.metrics
    assert not list(tmp_path.glob("run-*"))
    assert "business-secret" not in repr(result)


def test_stop_sends_sigint_then_sigkill_after_grace_and_still_cleans_files(tmp_path):
    process = _Process(hangs_after_interrupt=True)
    runtime = K6Runtime(
        tmp_path,
        popen=lambda *_args, **_kwargs: process,
        stop_grace_seconds=0,
        poll_interval=0,
    )

    result = runtime.run(_shard(), lambda: [{"type": "stop", "reason": "用户停止"}], _Sink())

    assert process.signals == [signal.SIGINT, signal.SIGKILL]
    assert result.state == "cancelled"
    assert result.stop_reason == "用户停止"
    assert not list(tmp_path.glob("run-*"))


def test_nonzero_k6_exit_returns_bounded_secret_free_crash_summary(tmp_path):
    process = _Process(returncode=23)
    process.stderr = _Stdout(["failed token=business-secret " + "x" * 10000])
    runtime = K6Runtime(tmp_path, popen=lambda *_args, **_kwargs: process, poll_interval=0)

    result = runtime.run(_shard(), lambda: [], _Sink())

    assert result.state == "failed"
    assert result.exit_code == 23
    assert "business-secret" not in result.error_message
    assert len(result.error_message) <= 2000


def test_runtime_executes_a_real_fake_k6_binary(tmp_path):
    fake_k6 = tmp_path / "fake-k6"
    fake_k6.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'type':'Point','metric':'http_reqs','data':"
        "{'time':'2026-09-03T08:00:00+00:00','value':1,'tags':{'step_id':'real'}}}), flush=True)\n",
        encoding="utf-8",
    )
    fake_k6.chmod(0o700)
    sink = _Sink()

    result = K6Runtime(tmp_path, k6_binary=str(fake_k6), poll_interval=0.01).run(
        _shard(), lambda: [], sink
    )

    assert result.state == "finished"
    assert sink.metrics[0][1]["buckets"][0]["metrics"]["requests"] == 1
    assert not list(tmp_path.glob("run-*"))
