"""Private work-directory and k6 subprocess lifecycle."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import select
import subprocess
import tempfile
import time
import uuid

from .k6_metrics import MetricAggregator


@dataclass(frozen=True, repr=False)
class ShardResult:
    state: str
    exit_code: int
    stop_reason: str
    error_message: str
    metric_bucket_count: int

    def __repr__(self):
        return (
            f"ShardResult(state={self.state!r}, exit_code={self.exit_code!r}, "
            f"stop_reason={self.stop_reason!r}, error_message={self.error_message!r}, "
            f"metric_bucket_count={self.metric_bucket_count!r})"
        )


class K6Runtime:
    def __init__(
        self,
        data_dir,
        *,
        k6_binary="k6",
        popen=None,
        stop_grace_seconds=10,
        poll_interval=0.2,
        sleeper=None,
    ):
        self.data_dir = Path(data_dir)
        self.k6_binary = k6_binary
        self.popen = popen or subprocess.Popen
        self.stop_grace_seconds = max(0, float(stop_grace_seconds))
        self.poll_interval = max(0, float(poll_interval))
        self.sleeper = sleeper or time.sleep

    def run(self, shard, command_source, metric_sink):
        shard_id = str(shard.get("id") or "")
        script_text = shard.get("script")
        if not shard_id or not isinstance(script_text, str) or not script_text:
            raise ValueError("分片缺少 ID 或已编译脚本")
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        work = Path(tempfile.mkdtemp(prefix="run-", dir=self.data_dir))
        os.chmod(work, 0o700)
        script = work / "scenario.js"
        dataset = work / "dataset.json"
        script.write_text(script_text, encoding="utf-8")
        dataset_rows = shard.get("dataset_rows") or []
        dataset.write_text(json.dumps(dataset_rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.chmod(script, 0o600)
        os.chmod(dataset, 0o600)
        environment = {
            str(key): str(value)
            for key, value in (shard.get("environment") or {}).items()
            if value is not None
        }
        secret_values = tuple(value for value in environment.values() if value)
        process_env = {"PATH": os.environ.get("PATH", ""), **environment}
        process_env["LOAD_DATASET_FILE"] = str(dataset)
        aggregator = MetricAggregator()
        bucket_count = 0
        stopped = False
        stop_reason = ""
        process = None
        error_message = ""
        stderr_path = work / "k6-stderr.log"
        stderr_path.touch(mode=0o600)
        stderr_stream = stderr_path.open("w", encoding="utf-8")
        try:
            process = self.popen(
                [self.k6_binary, "run", "--out", "json=-", str(script)],
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=process_env,
                cwd=work,
            )
            while True:
                commands = command_source() or []
                stop = next((item for item in commands if item.get("type") == "stop"), None)
                if stop and not stopped:
                    stopped = True
                    stop_reason = str(stop.get("reason") or "平台停止")[:1000]
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=self.stop_grace_seconds)
                    except (subprocess.TimeoutExpired, TimeoutError):
                        process.kill()
                        process.wait()
                line = self._readline(process.stdout, self.poll_interval)
                if line:
                    try:
                        ready = aggregator.accept(json.loads(line))
                    except json.JSONDecodeError:
                        ready = ()
                    if ready:
                        bucket_count += self._send_buckets(metric_sink, ready)
                exit_code = process.poll()
                if exit_code is not None and not line:
                    break
                if not line:
                    try:
                        exit_code = process.wait(timeout=0)
                        break
                    except (subprocess.TimeoutExpired, TimeoutError):
                        pass
                if self.poll_interval and not line:
                    self.sleeper(self.poll_interval)
            final_buckets = aggregator.flush_all()
            if final_buckets:
                bucket_count += self._send_buckets(metric_sink, final_buckets)
            stderr_stream.flush()
            stderr = (
                self._read_stderr(process.stderr)
                if process.stderr is not None
                else stderr_path.read_text(encoding="utf-8", errors="replace")[:4000]
            )
            error_message = self._redact(stderr, secret_values)[:2000]
            state = "cancelled" if stopped else "finished" if exit_code == 0 else "failed"
            return ShardResult(state, int(exit_code or 0), stop_reason, error_message, bucket_count)
        except Exception as error:
            if process is not None and process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
            error_message = self._redact(str(error), secret_values)[:2000]
            return ShardResult("cancelled" if stopped else "failed", -1, stop_reason, error_message, bucket_count)
        finally:
            stderr_stream.close()
            self._secure_remove(work)

    @staticmethod
    def _readline(stream, timeout):
        if stream is None:
            return ""
        try:
            descriptor = stream.fileno()
        except (AttributeError, OSError):
            return stream.readline()
        readable, _, _ = select.select([descriptor], [], [], max(0.0, timeout))
        return stream.readline() if readable else ""

    @staticmethod
    def _send_buckets(sink, buckets):
        metric_rows = [{key: value for key, value in item.items() if key != "samples"} for item in buckets]
        sample_rows = [sample for item in buckets for sample in item.get("samples", [])]
        batch_id = str(uuid.uuid4())
        sink.post_metrics({"buckets": metric_rows}, batch_id=batch_id)
        if sample_rows:
            sink.post_samples({"samples": sample_rows}, batch_id=batch_id + "-samples")
        return len(metric_rows)

    @staticmethod
    def _read_stderr(stream):
        if stream is None:
            return ""
        rows = []
        while sum(len(item) for item in rows) < 4000:
            line = stream.readline()
            if not line:
                break
            rows.append(line)
        return "".join(rows)

    @staticmethod
    def _redact(value, secrets):
        result = str(value or "")
        for secret in sorted(secrets, key=len, reverse=True):
            result = result.replace(secret, "***")
        return result

    @staticmethod
    def _secure_remove(work):
        if not work.exists():
            return
        for item in work.rglob("*"):
            if not item.is_file():
                continue
            try:
                with item.open("r+b") as stream:
                    stream.write(b"\x00" * item.stat().st_size)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                pass
        shutil.rmtree(work, ignore_errors=True)
