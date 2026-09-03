from load_agent.connectivity import run_connectivity_command


def test_connectivity_command_aggregates_dns_connect_and_tls_evidence():
    calls = []
    def probe(target):
        calls.append(target["name"])
        return {"reachable": True, "dns_ms": 1.2, "connect_ms": 3.4, "tls_ms": 5.6}

    result = run_connectivity_command({
        "type": "target_connectivity", "id": "command-1", "environment_revision_id": "env-1",
        "targets": [
            {"name": "default", "host": "api.example.test", "port": 443, "tls": True},
            {"name": "files", "host": "files.example.test", "port": 80, "tls": False},
        ],
    }, probe=probe)

    assert calls == ["default", "files"]
    assert result["reachable"] is True
    assert result["stage"] == "complete"
    assert result["command_id"] == "command-1"
    assert result["dns_ms"] == 2.4
    assert result["connect_ms"] == 6.8
    assert result["tls_ms"] == 11.2


def test_connectivity_command_reports_the_failed_target_and_remedy_without_secrets():
    result = run_connectivity_command({
        "type": "target_connectivity", "id": "command-2", "environment_revision_id": "env-1",
        "targets": [{"name": "private-api", "host": "private.example.test", "port": 443, "tls": True}],
    }, probe=lambda _target: {"reachable": False, "stage": "dns", "message": "secret-token failed"})

    assert result["reachable"] is False
    assert result["stage"] == "dns"
    assert result["failed_target"] == "private-api"
    assert result["message"] == "DNS解析失败，请检查节点DNS、目标域名和网络策略"
    assert "secret-token" not in str(result)
