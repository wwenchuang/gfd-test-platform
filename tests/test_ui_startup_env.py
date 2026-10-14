"""UI release flags load from a protected startup file without widening its allowlist."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FLAGS = ("UI_SIMPLE_FLOW_ENABLED", "UI_BATCH_NOTIFICATIONS_ENABLED")


def startup(tmp_path, contents, process_flags=None):
    env_path = tmp_path / "midscene.env"
    env_path.write_text(contents, encoding="utf-8")
    env_path.chmod(0o600)
    # Fresh import exercises real startup loading, never the host's /opt/midscene.env.
    result = subprocess.run(
        [sys.executable, "-c", """
import json
import os
from task_server import config
from task_server.services.ui_batch_dispatch import enabled
from task_server.services.ui_batch_notification_service import notifications_enabled
print(json.dumps({
    'status': config.ENV_FILE_LOAD_STATUS,
    'values': {key: os.getenv(key) for key in (
        'UI_SIMPLE_FLOW_ENABLED', 'UI_BATCH_NOTIFICATIONS_ENABLED', 'UI_UNRELATED_SETTING')},
    'admission': enabled(),
    'notifications': notifications_enabled(),
}))
"""],
        cwd=ROOT,
        env={"MIDSCENE_ENV_FILE": str(env_path), "TASK_APP_ENV": "test", **(process_flags or {})},
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("admission", ["true", "false"])
@pytest.mark.parametrize("notifications", ["true", "false"])
def test_startup_file_loads_both_ui_flags(tmp_path, admission, notifications):
    result = startup(tmp_path, f"export {FLAGS[0]}='{admission}'\n{FLAGS[1]}={notifications}\n")
    assert result["status"]["valid"] is True
    assert set(result["status"]["loaded_keys"]) == set(FLAGS)
    assert result["values"][FLAGS[0]] == admission
    assert result["values"][FLAGS[1]] == notifications
    assert result["admission"] is (admission == "true")
    assert result["notifications"] is (notifications == "true")


@pytest.mark.parametrize("process_value,file_value", [("true", "false"), ("false", "true")])
def test_process_ui_flags_take_precedence_over_startup_file(tmp_path, process_value, file_value):
    result = startup(
        tmp_path,
        "".join(f"{key}={file_value}\n" for key in FLAGS),
        {key: process_value for key in FLAGS},
    )
    assert result["status"]["valid"] is True
    assert result["status"]["loaded_keys"] == []
    assert all(result["values"][key] == process_value for key in FLAGS)
    assert result["admission"] is (process_value == "true")
    assert result["notifications"] is (process_value == "true")


def test_absent_ui_flags_default_to_disabled(tmp_path):
    result = startup(tmp_path, "# No UI release flags configured\n")
    assert result["status"]["valid"] is True
    assert all(result["values"][key] is None for key in FLAGS)
    assert result["admission"] is False
    assert result["notifications"] is False


def test_startup_file_rejects_unrelated_ui_keys(tmp_path):
    result = startup(tmp_path, "UI_UNRELATED_SETTING=true\n")
    assert result["status"]["loaded"] is True
    assert result["status"]["valid"] is False
    assert result["status"]["loaded_keys"] == []
    assert result["status"]["issues"] == [
        {"key": "UI_UNRELATED_SETTING", "message": "忽略非平台配置变量"}
    ]
    assert result["values"]["UI_UNRELATED_SETTING"] is None
