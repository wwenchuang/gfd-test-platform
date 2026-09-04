"""Security and lifecycle contracts for distributed load Agents."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from task_server.api_testing import access
from task_server.api_testing.models.load_testing import ApiLoadAgent, ApiLoadAgentEnrollment
from task_server.api_testing.services.load_agent_service import LoadAgentError, LoadAgentService
from tests.api_testing.test_load_testing_repository import load_factory


FIXED_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
HARD_LIMITS = {
    "max_processes": 2,
    "max_vus": 500,
    "max_iterations_per_second": 2000,
    "max_duration_seconds": 1800,
    "cpu_cores": 4,
    "memory_mb": 4096,
}
CAPABILITIES = {
    "agent_version": "1.0.0",
    "k6_version": "0.52.0",
    "hard_limits": HARD_LIMITS,
    "labels": {"region": "tencent-shanghai"},
}


@pytest.fixture()
def agent_permissions(monkeypatch):
    profiles = {
        "admin": {
            "status": "active",
            "must_change_password": False,
            "is_superuser": False,
            "permissions": ["api.view", "api.loadtest.view", "api.loadtest.execute", "api.loadtest.manage_agents"],
        },
        "viewer": {
            "status": "active",
            "must_change_password": False,
            "is_superuser": False,
            "permissions": ["api.view", "api.loadtest.view"],
        },
    }
    monkeypatch.setattr(access, "get_access_profile", lambda actor: profiles.get(actor))
    return profiles


def _service(load_factory, now=FIXED_NOW):
    return LoadAgentService(load_factory, now=lambda: now)


def test_enrollment_is_one_time_and_secrets_are_only_stored_as_hashes(
    load_factory, agent_permissions
):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {
            "name": "专用压测节点一",
            "node_group": "腾讯云",
            "scheduling_tier": "preferred",
            "expires_in_seconds": 600,
        },
        "admin",
    )
    registration = service.register(enrollment.token, CAPABILITIES)

    assert registration.agent.name == "专用压测节点一"
    assert registration.agent.scheduling_tier == "preferred"
    assert len(registration.secret) >= 43
    with pytest.raises(LoadAgentError) as replay:
        service.register(enrollment.token, CAPABILITIES)
    assert replay.value.code == "enrollment_used"

    with load_factory() as session:
        stored_enrollment = session.get(ApiLoadAgentEnrollment, enrollment.id)
        stored_agent = session.get(ApiLoadAgent, registration.agent.id)
        assert stored_enrollment.token_hash == hashlib.sha256(enrollment.token.encode()).hexdigest()
        assert stored_agent.credential_hash == hashlib.sha256(registration.secret.encode()).hexdigest()
        serialized = repr(stored_enrollment.__dict__) + repr(stored_agent.__dict__)
        assert enrollment.token not in serialized
        assert registration.secret not in serialized


def test_expired_enrollment_cannot_register(load_factory, agent_permissions):
    enrollment = _service(load_factory).create_enrollment(
        {"name": "过期节点", "scheduling_tier": "normal", "expires_in_seconds": 60},
        "admin",
    )
    with pytest.raises(LoadAgentError) as expired:
        _service(load_factory, FIXED_NOW + timedelta(seconds=61)).register(
            enrollment.token, CAPABILITIES
        )
    assert expired.value.code == "enrollment_expired"


def test_manage_permission_is_required_before_creating_enrollment(
    load_factory, agent_permissions
):
    with pytest.raises(access.AccessDeniedError) as denied:
        _service(load_factory).create_enrollment(
            {"name": "越权节点", "scheduling_tier": "normal"}, "viewer"
        )
    assert denied.value.permission == "api.loadtest.manage_agents"


def test_heartbeat_authenticates_secret_and_records_valid_capacity(
    load_factory, agent_permissions
):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "心跳节点", "scheduling_tier": "normal"}, "admin"
    )
    registration = service.register(enrollment.token, CAPABILITIES)
    updated = service.heartbeat(
        registration.secret,
        {
            "agent_version": "1.0.1",
            "k6_version": "0.52.0",
            "hard_limits": HARD_LIMITS,
            "current_usage": {"processes": 1, "vus": 20},
            "health": {"cpu_percent": 12.5, "memory_available_mb": 3020},
            "egress_ip": "203.0.113.10",
        },
    )

    assert updated.status == "online"
    assert updated.agent_version == "1.0.1"
    assert updated.current_usage == {"processes": 1, "vus": 20}
    assert service.authenticate(registration.secret).id == registration.agent.id
    with pytest.raises(LoadAgentError) as invalid:
        service.authenticate("not-the-agent-secret")
    assert invalid.value.code == "agent_unauthorized"


def test_soft_limits_never_exceed_agent_hard_limits(load_factory, agent_permissions):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "容量节点", "scheduling_tier": "normal"}, "admin"
    )
    registration = service.register(enrollment.token, CAPABILITIES)

    accepted = service.update_agent(
        registration.agent.id,
        {"soft_limits": {**HARD_LIMITS, "max_vus": 300}, "scheduling_tier": "fallback"},
        "admin",
    )
    assert accepted.soft_limits["max_vus"] == 300
    assert accepted.scheduling_tier == "fallback"
    with pytest.raises(LoadAgentError) as exceeded:
        service.update_agent(
            registration.agent.id,
            {"soft_limits": {**HARD_LIMITS, "max_vus": 501}},
            "admin",
        )
    assert exceeded.value.code == "soft_limit_exceeds_hard_limit"


@pytest.mark.parametrize("tier", ["fast", "primary", "", None])
def test_unknown_scheduling_tier_is_rejected(load_factory, agent_permissions, tier):
    with pytest.raises(LoadAgentError) as invalid:
        _service(load_factory).create_enrollment(
            {"name": "错误级别", "scheduling_tier": tier}, "admin"
        )
    assert invalid.value.code == "invalid_scheduling_tier"


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_processes", 0),
        ("max_vus", -1),
        ("max_iterations_per_second", 0),
        ("max_duration_seconds", 0),
        ("cpu_cores", 0),
        ("memory_mb", -20),
    ],
)
def test_registration_rejects_non_positive_hard_capacity(
    load_factory, agent_permissions, field, value
):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "错误容量-" + field, "scheduling_tier": "normal"}, "admin"
    )
    with pytest.raises(LoadAgentError) as invalid:
        service.register(
            enrollment.token,
            {**CAPABILITIES, "hard_limits": {**HARD_LIMITS, field: value}},
        )
    assert invalid.value.code == "invalid_hard_limits"


def test_disabled_agent_credentials_are_revoked(load_factory, agent_permissions):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "停用节点", "scheduling_tier": "normal"}, "admin"
    )
    registration = service.register(enrollment.token, CAPABILITIES)
    service.update_agent(
        registration.agent.id, {"scheduling_tier": "disabled"}, "admin"
    )

    with pytest.raises(LoadAgentError) as revoked:
        service.authenticate(registration.secret)
    assert revoked.value.code == "agent_disabled"


def test_online_agent_receives_one_durable_calibration_command_until_result_is_reported(
    load_factory, agent_permissions
):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "校准节点", "scheduling_tier": "normal"}, "admin"
    )
    registration = service.register(enrollment.token, CAPABILITIES)
    heartbeat = {
        "agent_version": "1.0.0",
        "k6_version": "0.52.0",
        "hard_limits": HARD_LIMITS,
        "current_usage": {"processes": 0, "vus": 0},
        "health": {"schedulable": False, "calibration": {"state": "missing"}},
        "egress_ip": "203.0.113.10",
    }
    service.heartbeat(registration.secret, heartbeat)

    requested = service.request_calibration(registration.agent.id, "admin")
    repeated = service.request_calibration(registration.agent.id, "admin")
    command = requested.health["pending_command"]

    assert command["type"] == "calibrate"
    assert repeated.health["pending_command"]["id"] == command["id"]
    waiting = service.heartbeat(registration.secret, heartbeat)
    assert waiting.health["pending_command"]["id"] == command["id"]
    assert waiting.health["calibration"]["state"] == "calibrating"

    completed = service.heartbeat(registration.secret, {
        **heartbeat,
        "health": {
            "schedulable": True,
            "calibration": {
                "state": "valid",
                "command_id": command["id"],
                "calibrated_at": "2026-09-03T12:00:20+00:00",
                "valid_until": "2026-09-10T12:00:20+00:00",
                "agent_version": "1.0.0",
                "k6_version": "0.52.0",
                "hardware_signature": "machine",
                "max_vus": 320,
                "max_iterations_per_second": 1200,
            },
        },
    })
    assert "pending_command" not in completed.health
    assert completed.health["calibration"]["state"] == "valid"


def test_offline_agent_calibration_request_explains_how_to_recover(load_factory, agent_permissions):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "离线节点", "scheduling_tier": "normal"}, "admin"
    )
    registration = service.register(enrollment.token, CAPABILITIES)

    with pytest.raises(LoadAgentError) as offline:
        service.request_calibration(registration.agent.id, "admin")

    assert offline.value.code == "agent_offline"
    assert "启动Agent" in str(offline.value)


def test_stale_heartbeat_cannot_accept_a_calibration_command(load_factory, agent_permissions):
    service = _service(load_factory)
    enrollment = service.create_enrollment(
        {"name": "心跳超时节点", "scheduling_tier": "preferred"}, "admin"
    )
    registration = service.register(enrollment.token, CAPABILITIES)
    service.heartbeat(registration.secret, {
        "agent_version": "1.0.0", "k6_version": "0.52.0", "hard_limits": HARD_LIMITS,
        "current_usage": {"processes": 0, "vus": 0},
        "health": {"schedulable": False, "calibration": {"state": "missing"}}, "egress_ip": "",
    })

    with pytest.raises(LoadAgentError) as stale:
        _service(load_factory, FIXED_NOW + timedelta(seconds=46)).request_calibration(
            registration.agent.id, "admin"
        )

    assert stale.value.code == "agent_offline"
    assert "心跳超时" in str(stale.value)


def test_target_connectivity_command_survives_heartbeat_until_matching_result(load_factory, agent_permissions):
    service = _service(load_factory)
    enrollment = service.create_enrollment({"name": "连通性节点", "scheduling_tier": "normal"}, "admin")
    registration = service.register(enrollment.token, CAPABILITIES)
    heartbeat = {
        "agent_version": "1.0.0", "k6_version": "0.52.0", "hard_limits": HARD_LIMITS,
        "current_usage": {"processes": 0, "vus": 0},
        "health": {"schedulable": True, "calibration": {"state": "valid"}}, "egress_ip": "",
    }
    service.heartbeat(registration.secret, heartbeat)
    requested = service.request_target_connectivity(
        registration.agent.id, "environment-revision-1",
        [{"name": "default", "host": "api.example.test", "port": 443, "tls": True}], "admin",
    )
    command = requested.health["pending_command"]
    with pytest.raises(LoadAgentError) as conflicting:
        service.request_target_connectivity(
            registration.agent.id, "environment-revision-2",
            [{"name": "other", "host": "other.example.test", "port": 443, "tls": True}], "admin",
        )
    assert conflicting.value.code == "agent_command_pending"
    waiting = service.heartbeat(registration.secret, heartbeat)
    assert waiting.health["pending_command"]["id"] == command["id"]

    completed = service.heartbeat(registration.secret, {
        **heartbeat,
        "health": {**heartbeat["health"], "target_connectivity": {
            "environment-revision-1": {
                "command_id": command["id"], "reachable": True, "stage": "complete",
                "dns_ms": 2.1, "connect_ms": 6.2, "tls_ms": 12.5,
            },
        }},
    })
    assert "pending_command" not in completed.health
    assert completed.health["target_connectivity"]["environment-revision-1"]["reachable"] is True
