from pathlib import Path

import pytest

from load_agent.config import AgentConfig, AgentConfigError


def test_https_agent_config_requires_data_dir_and_enrollment_or_credential(tmp_path):
    data_dir = tmp_path / "agent-data"
    config = AgentConfig.from_mapping({
        "PLATFORM_URL": "https://platform.example.com",
        "AGENT_DATA_DIR": str(data_dir),
        "ENROLL_TOKEN": "one-time-token",
    })

    assert config.platform_url == "https://platform.example.com"
    assert config.data_dir == data_dir
    assert config.enroll_token == "one-time-token"
    assert config.k6_binary == "k6"


def test_public_http_is_rejected_without_explicit_private_transport_override(tmp_path):
    values = {
        "PLATFORM_URL": "http://101.34.197.12:8088",
        "AGENT_DATA_DIR": str(tmp_path / "data"),
        "ENROLL_TOKEN": "token",
    }
    with pytest.raises(AgentConfigError, match="HTTPS"):
        AgentConfig.from_mapping(values)

    values["ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT"] = "1"
    assert AgentConfig.from_mapping(values).allow_insecure_private_transport is True


def test_existing_private_credential_allows_restart_without_enroll_token(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    credential = data_dir / "credential.json"
    credential.write_text('{"agent_id":"a","secret":"s"}', encoding="utf-8")
    credential.chmod(0o600)

    config = AgentConfig.from_mapping({
        "PLATFORM_URL": "https://platform.example.com/",
        "AGENT_DATA_DIR": str(data_dir),
    })

    assert config.credential_file == credential
