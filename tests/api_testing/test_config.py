import os
from pathlib import Path
import subprocess

import pytest


from task_server.api_testing.config import ApiTestingSettings


ROOT = Path(__file__).resolve().parents[2]


def test_settings_require_secret_when_enabled(monkeypatch):
    monkeypatch.setenv("API_TESTING_ENABLED", "1")
    monkeypatch.delenv("API_TESTING_SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="API_TESTING_SECRET_KEY"):
        ApiTestingSettings.from_env()


def test_settings_are_disabled_without_infrastructure(monkeypatch):
    monkeypatch.setenv("API_TESTING_ENABLED", "0")

    settings = ApiTestingSettings.from_env()

    assert settings.enabled is False


def test_disabled_migration_does_not_require_infrastructure(tmp_path):
    env = dict(os.environ)
    env.update({
        "API_TESTING_ENABLED": "0",
        "APP_DIR": str(tmp_path),
        "VENV_DIR": str(tmp_path / ".venv"),
    })

    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "api-testing-migrate.sh")],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "disabled" in result.stdout
