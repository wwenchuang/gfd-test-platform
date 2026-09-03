"""Long-running Agent entry point."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from . import __version__
from .calibration import CalibrationRunner, calibration_state, hardware_signature
from .client import AgentClient
from .config import AgentConfig
from .connectivity import run_connectivity_command
from .runtime import K6Runtime


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _k6_version(binary):
    result = subprocess.run([binary, "version"], capture_output=True, check=True, text=True, timeout=10)
    return result.stdout.strip().splitlines()[0][:80]


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_private_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _memory_limit_mb():
    for candidate in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(candidate).read_text(encoding="utf-8").strip()
            if raw and raw != "max":
                return max(1, int(raw) // (1024 * 1024))
        except (OSError, ValueError):
            continue
    try:
        return max(1, os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        return 1024


class _ShardSink:
    def __init__(self, client, shard_id):
        self.client = client
        self.shard_id = shard_id

    def post_metrics(self, payload, batch_id):
        return self.client.post_metrics(self.shard_id, payload, batch_id=batch_id)

    def post_samples(self, payload, batch_id):
        return self.client.post_samples(self.shard_id, payload, batch_id=batch_id)


class _CommandSource:
    def __init__(
        self,
        client,
        shard,
        config,
        calibration,
        hard_limits,
        *,
        k6_version,
        monotonic=None,
    ):
        self.client = client
        self.shard = shard
        self.config = config
        self.calibration = calibration
        self.hard_limits = hard_limits
        self.k6_version = k6_version
        self.monotonic = monotonic or time.monotonic
        self.next_poll = 0.0
        self.next_heartbeat = 0.0
        self.cached_commands = []

    def __call__(self):
        now = self.monotonic()
        if now >= self.next_heartbeat:
            allocation = self.shard.get("allocation") or {}
            self.client.heartbeat(
                {
                    "agent_version": __version__,
                    "k6_version": self.k6_version,
                    "hard_limits": self.hard_limits,
                    "current_usage": {
                        "processes": 1,
                        "vus": int(allocation.get("vus") or 0),
                        "iterations_per_second": int(allocation.get("rate") or 0),
                    },
                    "health": {"schedulable": False, "calibration": self.calibration},
                    "egress_ip": "",
                }
            )
            self.next_heartbeat = now + self.config.heartbeat_interval_seconds
        if now >= self.next_poll:
            self.cached_commands = self.client.commands(self.shard["id"])
            self.next_poll = now + self.config.poll_interval_seconds
        return self.cached_commands


def _run_requested_calibration(commands, current, *, runner, save):
    """Execute an idle calibration command once and preserve a correlated result."""
    command = next((item for item in commands if item.get("type") == "calibrate"), None)
    if not command or not command.get("id") or current.get("command_id") == command.get("id"):
        return current
    try:
        result = {**runner.run(), "command_id": command["id"]}
    except Exception:
        logger.exception("节点本地k6校准失败")
        result = {
            "state": "failed",
            "command_id": command["id"],
            "message": "本地k6校准失败，请检查k6版本、CPU/内存限制和Agent日志",
        }
    save(result)
    return result


def _run_requested_connectivity(commands, current):
    command = next((item for item in commands if item.get("type") == "target_connectivity"), None)
    if not command:
        return current
    environment_revision_id = str(command.get("environment_revision_id") or "")
    previous = current.get(environment_revision_id) if isinstance(current, dict) else None
    if isinstance(previous, dict) and previous.get("command_id") == command.get("id"):
        return current
    return {**current, environment_revision_id: run_connectivity_command(command)}


def main():
    config = AgentConfig.from_env()
    config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config.data_dir, 0o700)
    k6_version = _k6_version(config.k6_binary)
    signature = hardware_signature()
    hard_limits = {
        "max_processes": config.max_processes,
        "max_vus": config.max_vus,
        "max_iterations_per_second": config.max_iterations_per_second,
        "max_duration_seconds": config.max_duration_seconds,
        "cpu_cores": max(1, os.cpu_count() or 1),
        "memory_mb": _memory_limit_mb(),
    }
    client = AgentClient(config)
    credential = client.ensure_registered(
        {"agent_version": __version__, "k6_version": k6_version, "hard_limits": hard_limits, "labels": {}}
    )
    calibration_runner = CalibrationRunner(
        k6_binary=config.k6_binary,
        data_dir=config.data_dir,
        agent_version=__version__,
        k6_version=k6_version,
        hard_max_vus=config.max_vus,
        hard_max_iterations_per_second=config.max_iterations_per_second,
    )
    calibration = _read_json(config.calibration_file)
    target_connectivity = {}
    now = datetime.now(timezone.utc)
    if calibration_state(calibration, now, __version__, k6_version, signature) != "valid":
        logger.info("节点开始本地k6校准，不访问业务环境 agent_id=%s", credential.agent_id)
        calibration = calibration_runner.run()
        _write_private_json(config.calibration_file, calibration)
    runtime = K6Runtime(
        config.data_dir,
        k6_binary=config.k6_binary,
        stop_grace_seconds=config.stop_grace_seconds,
    )
    while True:
        heartbeat_response = client.heartbeat(
            {
                "agent_version": __version__,
                "k6_version": k6_version,
                "hard_limits": hard_limits,
                "current_usage": {"processes": 0, "vus": 0, "iterations_per_second": 0},
                "health": {"schedulable": calibration.get("state") == "valid", "calibration": calibration, "target_connectivity": target_connectivity},
                "egress_ip": "",
            }
        )
        response_data = heartbeat_response.get("data") if isinstance(heartbeat_response, dict) else {}
        commands = response_data.get("commands") if isinstance(response_data, dict) else []
        calibration = _run_requested_calibration(
            commands if isinstance(commands, list) else [],
            calibration,
            runner=calibration_runner,
            save=lambda value: _write_private_json(config.calibration_file, value),
        )
        target_connectivity = _run_requested_connectivity(
            commands if isinstance(commands, list) else [], target_connectivity,
        )
        shard = client.claim()
        if shard:
            shard_id = shard["id"]
            commands = _CommandSource(
                client,
                shard,
                config,
                calibration,
                hard_limits,
                k6_version=k6_version,
            )
            pending_commands = commands()
            while not any(item.get("type") in {"start", "stop"} for item in pending_commands):
                time.sleep(config.poll_interval_seconds)
                pending_commands = commands()
            early_stop = next((item for item in pending_commands if item.get("type") == "stop"), None)
            if early_stop:
                client.finish(
                    shard_id,
                    "cancelled",
                    {"metric_bucket_count": 0, "exit_code": 0},
                    {"message": str(early_stop.get("reason") or "平台停止")[:1000]},
                )
                time.sleep(config.poll_interval_seconds)
                continue
            client.mark_started(shard_id, {"runtime": "k6", "agent_version": __version__})
            result = runtime.run(shard, commands, _ShardSink(client, shard_id))
            client.finish(
                shard_id,
                result.state,
                {"metric_bucket_count": result.metric_bucket_count, "exit_code": result.exit_code},
                {"message": result.error_message} if result.error_message else {},
            )
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
