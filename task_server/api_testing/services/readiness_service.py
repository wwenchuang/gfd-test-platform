"""Bounded dependency diagnostics for the API testing subsystem."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from ..db import engine_for_url


_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
_DEFAULT_WORKER_HEARTBEAT_KEY = "midscene:api-testing:worker-heartbeat"


class ReadinessService:
    def __init__(
        self,
        settings,
        *,
        database_probe=None,
        redis_probe=None,
        worker_probe=None,
        gateway_probe=None,
        migration_probe=None,
    ):
        self.settings = settings
        self.database_probe = database_probe or self._database_probe
        self.redis_probe = redis_probe or self._redis_probe
        self.worker_probe = worker_probe or self._worker_probe
        self.gateway_probe = gateway_probe or self._gateway_probe
        self.migration_probe = migration_probe or self._migration_probe

    def check(self):
        if not self.settings.enabled:
            return {
                "ready": False,
                "database": {"connected": False, "error_code": "api_testing_disabled"},
                "redis": {"connected": False, "error_code": "api_testing_disabled"},
                "worker": {"available": False, "error_code": "api_testing_disabled"},
                "ai_gateway": {"connected": False, "error_code": "api_testing_disabled"},
                "api_testing": {"enabled": False},
            }

        database = self._check_database()
        redis_state = self._safe_boolean(
            self.redis_probe,
            state_key="connected",
            error_code="redis_unavailable",
        )
        worker = self._safe_boolean(
            self.worker_probe,
            state_key="available",
            error_code="worker_unavailable",
        )
        ai_gateway = self._safe_boolean(
            self.gateway_probe,
            state_key="connected",
            error_code="ai_gateway_unavailable",
        )
        ready = all(
            (
                database.get("connected") and database.get("migration_ready"),
                redis_state.get("connected"),
                worker.get("available"),
                ai_gateway.get("connected"),
            )
        )
        return {
            "ready": bool(ready),
            "database": database,
            "redis": redis_state,
            "worker": worker,
            "ai_gateway": ai_gateway,
            "api_testing": {"enabled": True},
        }

    def _check_database(self):
        try:
            if self.database_probe() is not True:
                raise RuntimeError("database probe failed")
        except Exception:
            return {"connected": False, "error_code": "database_unavailable"}

        try:
            current, expected = self.migration_probe()
        except Exception:
            return {"connected": False, "error_code": "database_unavailable"}

        ready = bool(current) and current == expected
        result = {
            "connected": True,
            "migration_current": current,
            "migration_expected": expected,
            "migration_ready": ready,
        }
        if not ready:
            result["error_code"] = "migration_required"
        return result

    @staticmethod
    def _safe_boolean(probe, *, state_key, error_code):
        try:
            ready = probe() is True
        except Exception:
            ready = False
        result = {state_key: ready}
        if not ready:
            result["error_code"] = error_code
        return result

    def _database_probe(self):
        with engine_for_url(self.settings.database_url).connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1

    def _redis_client(self):
        return redis.Redis.from_url(self.settings.redis_url, decode_responses=True)

    def _redis_probe(self):
        return bool(self._redis_client().ping())

    def _worker_probe(self):
        heartbeat_key = getattr(
            self.settings,
            "worker_heartbeat_key",
            _DEFAULT_WORKER_HEARTBEAT_KEY,
        )
        return self._redis_client().get(heartbeat_key) == "1"

    def _gateway_probe(self):
        base_url = os.getenv("AI_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/")
        request = Request(base_url + "/health", headers={"Accept": "application/json"})
        with urlopen(request, timeout=2) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("ok") is True

    def _migration_probe(self):
        with engine_for_url(self.settings.database_url).connect() as connection:
            current = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

        config = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(_MIGRATIONS_DIR))
        expected = ScriptDirectory.from_config(config).get_current_head()
        return current, expected
