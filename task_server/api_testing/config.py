"""Environment-only settings for the optional API testing module."""

from dataclasses import dataclass
import os


_TRUE_VALUES = {"1", "true", "yes", "on"}
_MIN_SECRET_LENGTH = 32
_MIN_SECRET_UNIQUE_CHARACTERS = 8
_MAX_REPEATED_UNIT_LENGTH = 16
_DEFAULT_WORKER_HEARTBEAT_KEY = "midscene:api-testing:worker-heartbeat"
_DEFAULT_WORKER_HEARTBEAT_TTL_SECONDS = 45
_DEFAULT_LOAD_START_BARRIER_TIMEOUT_SECONDS = 60
_DEFAULT_LOAD_SHARD_STALE_SECONDS = 120
_DEFAULT_LOAD_PREFLIGHT_TIMEOUT_SECONDS = 30
_KNOWN_PLACEHOLDER_SECRETS = frozenset({
    "change-me",
    "change-this-long-random-secret",
    "replace-with-a-strong-random-secret",
    "replace-with-your-own-random-key",
    "your-api-testing-secret-key",
    "your-secret-key",
})


def _secret_is_strong(secret):
    if len(secret) < _MIN_SECRET_LENGTH:
        return False
    if secret.lower() in _KNOWN_PLACEHOLDER_SECRETS:
        return False
    if len(set(secret)) < _MIN_SECRET_UNIQUE_CHARACTERS:
        return False
    max_unit_length = min(_MAX_REPEATED_UNIT_LENGTH, len(secret) // 2)
    for unit_length in range(1, max_unit_length + 1):
        if len(secret) % unit_length == 0:
            unit = secret[:unit_length]
            if unit * (len(secret) // unit_length) == secret:
                return False
    return True


@dataclass(frozen=True)
class ApiTestingSettings:
    enabled: bool
    database_url: str
    redis_url: str
    secret_key: str
    queue: str
    worker_heartbeat_key: str
    worker_heartbeat_ttl_seconds: int
    load_start_barrier_timeout_seconds: int
    load_shard_stale_seconds: int
    load_preflight_timeout_seconds: int

    @classmethod
    def from_env(cls):
        enabled = os.getenv("API_TESTING_ENABLED", "0").strip().lower() in _TRUE_VALUES
        database_url = os.getenv("API_TESTING_DATABASE_URL", "").strip()
        redis_url = os.getenv("API_TESTING_REDIS_URL", "redis://127.0.0.1:6379/0").strip()
        secret_key = os.getenv("API_TESTING_SECRET_KEY", "").strip()
        queue = os.getenv("API_TESTING_QUEUE", "api-testing").strip() or "api-testing"
        worker_heartbeat_key = (
            os.getenv("API_TESTING_WORKER_HEARTBEAT_KEY", _DEFAULT_WORKER_HEARTBEAT_KEY).strip()
            or _DEFAULT_WORKER_HEARTBEAT_KEY
        )
        try:
            worker_heartbeat_ttl_seconds = int(
                os.getenv(
                    "API_TESTING_WORKER_HEARTBEAT_TTL_SECONDS",
                    str(_DEFAULT_WORKER_HEARTBEAT_TTL_SECONDS),
                )
            )
        except ValueError as error:
            raise ValueError("API_TESTING_WORKER_HEARTBEAT_TTL_SECONDS must be an integer") from error
        if not 15 <= worker_heartbeat_ttl_seconds <= 300:
            raise ValueError("API_TESTING_WORKER_HEARTBEAT_TTL_SECONDS must be between 15 and 300")

        def bounded_seconds(name, default, minimum, maximum):
            try:
                value = int(os.getenv(name, str(default)))
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        load_start_barrier_timeout_seconds = bounded_seconds(
            "API_LOAD_START_BARRIER_TIMEOUT_SECONDS",
            _DEFAULT_LOAD_START_BARRIER_TIMEOUT_SECONDS,
            15,
            600,
        )
        load_shard_stale_seconds = bounded_seconds(
            "API_LOAD_SHARD_STALE_SECONDS",
            _DEFAULT_LOAD_SHARD_STALE_SECONDS,
            15,
            3600,
        )
        load_preflight_timeout_seconds = bounded_seconds(
            "API_LOAD_PREFLIGHT_TIMEOUT_SECONDS",
            _DEFAULT_LOAD_PREFLIGHT_TIMEOUT_SECONDS,
            1,
            60,
        )

        if enabled and len(secret_key) < _MIN_SECRET_LENGTH:
            raise ValueError("API_TESTING_SECRET_KEY must be at least 32 characters when API testing is enabled")
        if enabled and not _secret_is_strong(secret_key):
            raise ValueError("API_TESTING_SECRET_KEY must be a strong random value when API testing is enabled")
        if enabled and not database_url:
            raise ValueError("API_TESTING_DATABASE_URL is required when API testing is enabled")
        if enabled and not redis_url:
            raise ValueError("API_TESTING_REDIS_URL is required when API testing is enabled")

        return cls(
            enabled=enabled,
            database_url=database_url,
            redis_url=redis_url,
            secret_key=secret_key,
            queue=queue,
            worker_heartbeat_key=worker_heartbeat_key,
            worker_heartbeat_ttl_seconds=worker_heartbeat_ttl_seconds,
            load_start_barrier_timeout_seconds=load_start_barrier_timeout_seconds,
            load_shard_stale_seconds=load_shard_stale_seconds,
            load_preflight_timeout_seconds=load_preflight_timeout_seconds,
        )
