"""Strict environment configuration for the outbound-only load Agent."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import os


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class AgentConfigError(ValueError):
    pass


def _positive_int(values, name, default, minimum, maximum):
    try:
        value = int(str(values.get(name, default)))
    except ValueError as error:
        raise AgentConfigError(f"{name} 必须是整数") from error
    if not minimum <= value <= maximum:
        raise AgentConfigError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


@dataclass(frozen=True)
class AgentConfig:
    platform_url: str
    data_dir: Path
    enroll_token: str
    credential_file: Path
    calibration_file: Path
    k6_binary: str
    allow_insecure_private_transport: bool
    request_timeout_seconds: int
    poll_interval_seconds: int
    heartbeat_interval_seconds: int
    stop_grace_seconds: int
    max_processes: int
    max_vus: int
    max_iterations_per_second: int
    max_duration_seconds: int

    @classmethod
    def from_env(cls):
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values):
        platform_url = str(values.get("PLATFORM_URL") or "").strip().rstrip("/")
        if not platform_url:
            raise AgentConfigError("PLATFORM_URL 不能为空")
        parsed = urlsplit(platform_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AgentConfigError("PLATFORM_URL 必须是完整的 HTTP(S) 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AgentConfigError("PLATFORM_URL 不能包含账号密码、查询参数或片段")
        insecure = str(values.get("ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT") or "").lower() in TRUE_VALUES
        if parsed.scheme != "https" and not insecure:
            raise AgentConfigError(
                "Agent凭据和环境密钥必须通过HTTPS传输；仅受控私网/VPN可显式设置"
                " ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT=1"
            )
        raw_data_dir = str(values.get("AGENT_DATA_DIR") or "").strip()
        if not raw_data_dir:
            raise AgentConfigError("AGENT_DATA_DIR 不能为空")
        data_dir = Path(raw_data_dir).expanduser()
        if not data_dir.is_absolute():
            raise AgentConfigError("AGENT_DATA_DIR 必须是绝对路径")
        credential_file = data_dir / "credential.json"
        enroll_token = str(values.get("ENROLL_TOKEN") or "").strip()
        if not enroll_token and not credential_file.is_file():
            raise AgentConfigError("首次启动必须提供 ENROLL_TOKEN，后续启动使用已保存凭据")
        return cls(
            platform_url=platform_url,
            data_dir=data_dir,
            enroll_token=enroll_token,
            credential_file=credential_file,
            calibration_file=data_dir / "calibration.json",
            k6_binary=str(values.get("K6_BIN") or "k6").strip() or "k6",
            allow_insecure_private_transport=insecure,
            request_timeout_seconds=_positive_int(values, "AGENT_REQUEST_TIMEOUT_SECONDS", 30, 1, 60),
            poll_interval_seconds=_positive_int(values, "AGENT_POLL_INTERVAL_SECONDS", 2, 1, 60),
            heartbeat_interval_seconds=_positive_int(values, "AGENT_HEARTBEAT_INTERVAL_SECONDS", 10, 5, 300),
            stop_grace_seconds=_positive_int(values, "K6_STOP_GRACE_SECONDS", 10, 1, 60),
            max_processes=_positive_int(values, "AGENT_MAX_PROCESSES", 1, 1, 16),
            max_vus=_positive_int(values, "AGENT_MAX_VUS", 500, 1, 1_000_000),
            max_iterations_per_second=_positive_int(values, "AGENT_MAX_ITERATIONS_PER_SECOND", 2000, 1, 10_000_000),
            max_duration_seconds=_positive_int(values, "AGENT_MAX_DURATION_SECONDS", 1800, 1, 86_400),
        )
