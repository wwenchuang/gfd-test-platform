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


def test_settings_reject_short_secret_when_enabled(monkeypatch):
    monkeypatch.setenv("API_TESTING_ENABLED", "1")
    monkeypatch.setenv("API_TESTING_SECRET_KEY", "x" * 31)
    monkeypatch.setenv("API_TESTING_DATABASE_URL", "postgresql+psycopg://test@127.0.0.1/test")
    monkeypatch.setenv("API_TESTING_REDIS_URL", "redis://127.0.0.1:6379/0")

    with pytest.raises(ValueError, match="at least 32 characters"):
        ApiTestingSettings.from_env()


def test_settings_accept_32_character_secret_when_enabled(monkeypatch):
    monkeypatch.setenv("API_TESTING_ENABLED", "1")
    monkeypatch.setenv("API_TESTING_SECRET_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("API_TESTING_DATABASE_URL", "postgresql+psycopg://test@127.0.0.1/test")
    monkeypatch.setenv("API_TESTING_REDIS_URL", "redis://127.0.0.1:6379/0")

    settings = ApiTestingSettings.from_env()

    assert settings.enabled is True


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


def test_enabled_migration_uses_planned_alembic_path(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_python = venv_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$MIGRATION_ARGS\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    migrations_dir = tmp_path / "task_server" / "api_testing" / "migrations"
    migrations_dir.mkdir(parents=True)
    alembic_config = migrations_dir / "alembic.ini"
    alembic_config.write_text("[alembic]\n", encoding="utf-8")
    migration_args = tmp_path / "migration-args.txt"
    env = dict(os.environ)
    env.update({
        "API_TESTING_ENABLED": "1",
        "APP_DIR": str(tmp_path),
        "VENV_DIR": str(tmp_path / ".venv"),
        "MIGRATION_ARGS": str(migration_args),
    })

    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "api-testing-migrate.sh")],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert migration_args.read_text(encoding="utf-8").splitlines() == [
        "-m",
        "alembic",
        "-c",
        str(alembic_config),
        "upgrade",
        "head",
    ]


def test_worker_unit_render_uses_custom_app_and_venv_paths(tmp_path):
    app_dir = tmp_path / "custom-app"
    venv_dir = tmp_path / "custom-venv"
    env = dict(os.environ)
    env.update({"APP_DIR": str(app_dir), "VENV_DIR": str(venv_dir)})

    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "install-server.sh"), "--render-api-worker-unit"],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"WorkingDirectory={app_dir}" in result.stdout
    assert f"ExecStart={venv_dir}/bin/celery" in result.stdout
    assert "/opt/midscene-task-platform/.venv/bin/celery" not in result.stdout
