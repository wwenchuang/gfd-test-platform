from types import SimpleNamespace


def _settings():
    return SimpleNamespace(
        enabled=True,
        database_url="postgresql+psycopg://midscene:secret@127.0.0.1/midscene",
        redis_url="redis://127.0.0.1:6379/0",
        worker_heartbeat_key="midscene:api-testing:worker-heartbeat",
    )


def _service(**overrides):
    from task_server.api_testing.services.readiness_service import ReadinessService

    probes = {
        "database_probe": lambda: True,
        "redis_probe": lambda: True,
        "worker_probe": lambda: True,
        "gateway_probe": lambda: True,
        "migration_probe": lambda: ("0003", "0003"),
    }
    probes.update(overrides)
    return ReadinessService(_settings(), **probes)


def test_readiness_is_false_when_database_authentication_fails():
    def fail_database():
        raise RuntimeError("auth failed with database password")

    result = _service(database_probe=fail_database).check()

    assert result["ready"] is False
    assert result["database"] == {
        "connected": False,
        "error_code": "database_unavailable",
    }
    assert "auth failed" not in str(result)
    assert "password" not in str(result)


def test_readiness_requires_current_migration():
    result = _service(migration_probe=lambda: ("0002", "0003")).check()

    assert result["ready"] is False
    assert result["database"] == {
        "connected": True,
        "migration_current": "0002",
        "migration_expected": "0003",
        "migration_ready": False,
        "error_code": "migration_required",
    }


def test_readiness_identifies_each_unavailable_dependency():
    expectations = {
        "redis_probe": ("redis", "redis_unavailable"),
        "worker_probe": ("worker", "worker_unavailable"),
        "gateway_probe": ("ai_gateway", "ai_gateway_unavailable"),
    }

    for probe_name, (component, error_code) in expectations.items():
        result = _service(**{probe_name: lambda: False}).check()
        assert result["ready"] is False
        assert result[component]["error_code"] == error_code


def test_readiness_is_true_only_when_all_components_are_ready():
    result = _service().check()

    assert result == {
        "ready": True,
        "database": {
            "connected": True,
            "migration_current": "0003",
            "migration_expected": "0003",
            "migration_ready": True,
        },
        "redis": {"connected": True},
        "worker": {"available": True},
        "ai_gateway": {"connected": True},
        "api_testing": {"enabled": True},
    }
