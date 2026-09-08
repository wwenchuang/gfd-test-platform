import json
from pathlib import Path
import signal

import pytest

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


def test_runtime_tolerates_non_utf8_k6_console_bytes_and_keeps_valid_metrics(tmp_path):
    fake_k6 = tmp_path / "fake-k6-non-utf8"
    fake_k6.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "os.write(1, b'\\xff\\xfeprogress\\n')\n"
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
    assert result.exit_code == 0
    assert sink.metrics[0][1]["buckets"][0]["metrics"]["requests"] == 1
    assert not list(tmp_path.glob("run-*"))


def test_excessively_late_metrics_fail_shard_without_overwriting_sent_bucket(tmp_path):
    from tests.load_agent.test_k6_metrics import _point
    process = _Process([json.dumps(_point("http_reqs", 1, second)) + "\n" for second in (0, 10, 0)])
    runtime = K6Runtime(tmp_path, popen=lambda *_args, **_kwargs: process, poll_interval=0)
    sink = _Sink()
    result = runtime.run(_shard(), lambda: [], sink)
    assert result.state == "failed"
    assert "乱序" in result.error_message
    assert signal.SIGKILL in process.signals
    assert len(sink.metrics) == 1
    assert sink.metrics[0][1]["buckets"][0]["metrics"]["requests"] == 1
    assert not list(tmp_path.glob("run-*"))


def test_metric_reader_drains_buffered_lines_before_waiting_for_more_pipe_data():
    import os
    from load_agent.runtime import _MetricLineReader
    descriptor, writer = os.pipe()
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        try:
            reader = _MetricLineReader(stream)
            os.write(writer, b"first\nsecond\n")
            assert reader.readline(0) == "first\n"
            assert reader.readline(0) == "second\n"
            assert reader.readline(0) == ""
            assert not reader.eof
        finally:
            os.close(writer)
        assert reader.readline(0) == ""
        assert reader.eof


def test_runtime_drains_all_metric_lines_even_after_process_exits(tmp_path):
    fake_k6 = tmp_path / "fake-k6-burst"
    fake_k6.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os\n"
        "rows=[]\n"
        "for i in range(20):\n"
        " for metric in ('http_reqs','iterations','workflow_iteration_success','http_req_duration'):\n"
        "  rows.append(json.dumps({'type':'Point','metric':metric,'data':{'time':'2026-09-03T08:00:00Z','value':1}}))\n"
        "os.write(1, ('\\n'.join(rows)+'\\n').encode())\n",
        encoding="utf-8",
    )
    fake_k6.chmod(0o700)
    sink = _Sink()
    result = K6Runtime(tmp_path, k6_binary=str(fake_k6), poll_interval=0.01).run(_shard(), lambda: [], sink)
    assert result.state == "finished"
    metrics = [b['metrics'] for _, p in sink.metrics for b in p['buckets']]
    for name in ('requests', 'iterations', 'workflow_iterations'):
        assert sum(m[name] for m in metrics) == 20
    assert sum(m['latency_histogram']['count'] for m in metrics) == 20


def test_metric_reader_preserves_split_utf8_and_final_line_without_newline():
    import os
    from load_agent.runtime import _MetricLineReader
    descriptor, writer = os.pipe()
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        reader = _MetricLineReader(stream)
        encoded = "中文指标".encode("utf-8")
        os.write(writer, encoded[:2])
        assert reader.readline(0) == ""
        os.write(writer, encoded[2:])
        os.close(writer)
        assert reader.readline(0) == ""
        assert reader.readline(0) == "中文指标"
        assert reader.readline(0) == ""
        assert reader.eof


@pytest.mark.parametrize("quiet", [True, False])
def test_real_k6_json_metrics_match_local_http_server(tmp_path, quiet):
    """Optional real-k6 acceptance; never accesses a business environment."""
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import subprocess

    binary = os.environ.get("K6_TEST_BINARY")
    if not binary:
        pytest.skip("set K6_TEST_BINARY to run local k6 metric integrity acceptance")

    class Handler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    shard = _shard()
    shard['script'] = """
import http from 'k6/http';
import {check} from 'k6';
import {Rate} from 'k6/metrics';
const workflow = new Rate('workflow_iteration_success');
export const options = {scenarios:{main:{executor:'constant-arrival-rate',rate:5,
 timeUnit:'1s',duration:'3s',preAllocatedVUs:2,maxVUs:2}}};
export default function(){
 const r = http.get('http://127.0.0.1:PORT');
 check(r, {'status': r=>r.status===200, 'body': r=>r.body==='{}'});
 workflow.add(true);
}
""".replace('PORT', str(server.server_port))
    sink = _Sink()
    try:
        def popen(argv, **kwargs):
            if not quiet:
                argv = [arg for arg in argv if arg not in {"--quiet", "--no-summary"}]
            return subprocess.Popen(argv, **kwargs)
        result = K6Runtime(tmp_path, k6_binary=binary, popen=popen, poll_interval=0.01).run(shard, lambda: [], sink)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    if not quiet:
        assert result.state == "failed"
        assert "指标输出损坏" in result.error_message
        return
    assert result.state == 'finished', result.error_message
    metrics = [b['metrics'] for _, p in sink.metrics for b in p['buckets']]
    assert Handler.requests >= 15
    # These equalities apply to this controlled single-request fixture only.
    for name in ('requests', 'iterations', 'workflow_iterations'):
        assert sum(m[name] for m in metrics) == Handler.requests, name
    assert sum(m['latency_histogram']['count'] for m in metrics) == Handler.requests
    assert sum(m['business_assertions'] for m in metrics) == 2 * Handler.requests


def test_runtime_reserves_stdout_for_json_metrics(tmp_path):
    observed = []
    def popen(argv, **_kwargs):
        observed.extend(argv)
        return _Process()
    result = K6Runtime(tmp_path, popen=popen, poll_interval=0).run(_shard(), lambda: [], _Sink())
    assert result.state == "finished"
    assert "--quiet" in observed
    assert "--no-summary" in observed


def test_corrupted_metric_json_fails_without_disclosing_metric_contents(tmp_path):
    process = _Process(['{"metric":"http_reqs","type":"Point",progress business-secret\n'])
    result = K6Runtime(tmp_path, popen=lambda *a, **k: process, poll_interval=0).run(_shard(), lambda: [], _Sink())
    assert result.state == "failed"
    assert "指标输出损坏" in result.error_message
    assert "business-secret" not in result.error_message
