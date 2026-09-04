from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "load-agent"


def _run(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=script.parent,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    script = bin_dir / "docker"
    script.write_text(
        """#!/usr/bin/env bash
set -eu
printf 'K6=%s PYTHON=%s ARGS=%s\\n' "${K6_IMAGE:-}" "${PYTHON_IMAGE:-}" "$*" >> "${FAKE_DOCKER_LOG}"
if [ "${1:-}" = "compose" ] && [ "${2:-}" = "version" ]; then exit 0; fi
if [ "${1:-}" = "compose" ] && printf '%s\\n' "$*" | grep -q 'exec.*credential.json'; then exit 0; fi
if printf '%s\\n' "$*" | grep -q ' build ' && [ -n "${FAKE_FAIL_K6_IMAGE:-}" ] && [ "${K6_IMAGE:-}" = "${FAKE_FAIL_K6_IMAGE}" ]; then exit 1; fi
exit 0
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return bin_dir, log


def _copy_scripts(tmp_path: Path) -> Path:
    target = tmp_path / "load-agent"
    shutil.copytree(DEPLOY, target)
    return target


def test_shell_scripts_have_valid_syntax_and_expected_files():
    expected = {
        "docker-compose.yml", ".env.example", "install.sh", "upgrade.sh", "uninstall.sh", "check.sh"
    }
    assert expected <= {item.name for item in DEPLOY.iterdir()}
    for name in ("install.sh", "upgrade.sh", "uninstall.sh", "check.sh"):
        subprocess.run(["bash", "-n", str(DEPLOY / name)], check=True)
    installer = (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8")
    assert 'load_agent_env_backup="$(mktemp)"' in installer
    assert 'cp "${APP_DIR}/deploy/load-agent/.env" "${load_agent_env_backup}"' in installer
    assert 'install -m 0600 "${load_agent_env_backup}" "${APP_DIR}/deploy/load-agent/.env"' in installer
    assert 'rm -f "${APP_DIR}/deploy/load-agent/.env"' in installer
    assert "midscene-load-agent:0.1.2" in (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "midscene-load-agent:0.1.2" in (DEPLOY / ".env.example").read_text(encoding="utf-8")
    check = (DEPLOY / "check.sh").read_text(encoding="utf-8")
    assert "当前运行版本" in check
    assert "历史排障信息" in check


def test_compose_bounds_the_agent_without_privileged_mounts():
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "restart: unless-stopped" in compose
    assert "read_only: true" in compose
    assert "tmpfs:" in compose
    assert "healthcheck:" in compose
    assert "mem_limit:" in compose
    assert "cpus:" in compose
    assert "pids_limit:" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "/var/run/docker.sock" not in compose
    assert "load-agent-data:/var/lib/midscene-load-agent" in compose


def test_install_is_idempotent_private_and_removes_enrollment_token(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    bin_dir, log = _fake_docker(tmp_path)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "PLATFORM_URL": "https://platform.example.test",
        "ENROLL_TOKEN": "single-use-secret",
        "AGENT_MAX_VUS": "320",
        "AGENT_MAX_ITERATIONS_PER_SECOND": "900",
        "AGENT_MAX_DURATION_SECONDS": "600",
        "LOAD_AGENT_CPU_LIMIT": "2.5",
        "LOAD_AGENT_MEMORY_LIMIT": "3g",
    }
    first = _run(scripts / "install.sh", env=env)
    assert first.returncode == 0, first.stderr + first.stdout

    env_file = scripts / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    saved = env_file.read_text(encoding="utf-8")
    assert "PLATFORM_URL=https://platform.example.test" in saved
    assert "AGENT_MAX_VUS=320" in saved
    assert "LOAD_AGENT_CPU_LIMIT=2.5" in saved
    assert "ENROLL_TOKEN=" in saved
    assert "single-use-secret" not in saved
    assert "single-use-secret" not in log.read_text(encoding="utf-8")

    second = _run(
        scripts / "install.sh",
        env={
            "PATH": env["PATH"],
            "FAKE_DOCKER_LOG": str(log),
        },
    )
    assert second.returncode == 0, second.stderr + second.stdout
    assert "single-use-secret" not in env_file.read_text(encoding="utf-8")


def test_install_requires_first_token_and_rejects_unprotected_http(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    bin_dir, log = _fake_docker(tmp_path)
    common = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(log)}
    missing = _run(scripts / "install.sh", env={**common, "PLATFORM_URL": "https://platform.example.test"})
    assert missing.returncode != 0
    assert "ENROLL_TOKEN" in missing.stderr + missing.stdout

    insecure = _run(
        scripts / "install.sh",
        env={**common, "PLATFORM_URL": "http://203.0.113.9", "ENROLL_TOKEN": "token"},
    )
    assert insecure.returncode != 0
    assert "HTTPS" in insecure.stderr + insecure.stdout


def test_install_tries_the_next_configured_image_source(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    bin_dir, log = _fake_docker(tmp_path)
    result = _run(
        scripts / "install.sh",
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_FAIL_K6_IMAGE": "mirror.invalid/k6:0.52.0",
            "PLATFORM_URL": "https://platform.example.test",
            "ENROLL_TOKEN": "token",
            "K6_IMAGE_CANDIDATES": "mirror.invalid/k6:0.52.0,grafana/k6:0.52.0",
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    calls = log.read_text(encoding="utf-8")
    assert "K6=mirror.invalid/k6:0.52.0" in calls
    assert "K6=grafana/k6:0.52.0" in calls


def test_uninstall_preserves_credentials_unless_purge_is_explicit(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    bin_dir, log = _fake_docker(tmp_path)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(log)}
    (scripts / ".env").write_text("PLATFORM_URL=https://example.test\nENROLL_TOKEN=\n", encoding="utf-8")

    stopped = _run(scripts / "uninstall.sh", env=env)
    assert stopped.returncode == 0
    assert (scripts / ".env").exists()
    assert "down --remove-orphans" in log.read_text(encoding="utf-8")
    assert "down --volumes" not in log.read_text(encoding="utf-8")

    purged = _run(scripts / "uninstall.sh", "--purge", env=env)
    assert purged.returncode == 0
    assert not (scripts / ".env").exists()
    assert "down --volumes --remove-orphans" in log.read_text(encoding="utf-8")


def test_package_contains_agent_context_and_excludes_private_runtime_files(tmp_path: Path):
    private_env = DEPLOY / ".env"
    private_data = ROOT / "load_agent" / "credential.json"
    try:
        private_env.write_text("ENROLL_TOKEN=must-not-package\n", encoding="utf-8")
        private_data.write_text('{"secret":"must-not-package"}', encoding="utf-8")
        result = subprocess.run(
            ["bash", str(ROOT / "deploy" / "package-server.sh")],
            cwd=ROOT,
            env={**os.environ, "OUT_DIR": str(tmp_path / "dist"), "VERSION": "load-agent-test", "KEEP_PACKAGES": "0"},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        archive = Path(result.stdout.strip().splitlines()[-1])
        with tarfile.open(archive) as stream:
            names = set(stream.getnames())
            assert "midscene-task-platform/load_agent/Dockerfile" in names
            assert "midscene-task-platform/deploy/load-agent/docker-compose.yml" in names
            assert "midscene-task-platform/deploy/load-agent/install.sh" in names
            assert "midscene-task-platform/docs/api-load-agent-operations.md" in names
            assert not any(name.endswith("deploy/load-agent/.env") for name in names)
            assert not any(name.endswith("credential.json") for name in names)
            assert not any(name.endswith("identity.sqlite3") for name in names)
            assert not any("/datasets/" in name for name in names)
    finally:
        private_env.unlink(missing_ok=True)
        private_data.unlink(missing_ok=True)
