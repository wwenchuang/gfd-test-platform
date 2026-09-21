import importlib.util
from pathlib import Path

from task_server.services.yaml_static_validator import validate_yaml_static_executable


ROOT = Path(__file__).resolve().parents[1]


def _yaml(step: str) -> str:
    return f"""android: {{}}
tasks:
  - name: Midscene 1.13 contract
    flow:
{step}
      - aiWaitFor: 页面稳定
      - aiAssert: 页面可见
"""


def test_midscene_113_accepts_all_official_scroll_types():
    for scroll_type in (
        "singleAction",
        "scrollToBottom",
        "scrollToTop",
        "scrollToRight",
        "scrollToLeft",
    ):
        result = validate_yaml_static_executable(
            _yaml(f"      - aiScroll: 当前内容区域\n        scrollType: {scroll_type}")
        )
        assert result["ok"], (scroll_type, result["errors"])


def test_midscene_113_accepts_keyboard_scalar_and_located_forms():
    scalar = validate_yaml_static_executable(_yaml("      - aiKeyboardPress: Enter"))
    located = validate_yaml_static_executable(
        _yaml("      - aiKeyboardPress: 搜索输入框\n        keyName: Enter")
    )
    assert scalar["ok"], scalar["errors"]
    assert located["ok"], located["errors"]


def test_midscene_113_accepts_global_scroll_without_prompt():
    result = validate_yaml_static_executable(
        _yaml("      - aiScroll:\n        scrollType: scrollToBottom")
    )
    assert result["ok"], result["errors"]


def test_midscene_113_rejects_nested_or_prefixed_adb_shell():
    nested = validate_yaml_static_executable(
        _yaml("      - runAdbShell:\n          command: dumpsys battery")
    )
    prefixed = validate_yaml_static_executable(
        _yaml("      - runAdbShell: adb shell dumpsys battery")
    )
    assert not nested["ok"]
    assert not prefixed["ok"]
    assert any("标量" in item for item in nested["errors"])
    assert any("adb shell" in item for item in prefixed["errors"])


def test_midscene_113_runner_node_version_contract():
    spec = importlib.util.spec_from_file_location(
        "windows_midscene_runner", ROOT / "windows-midscene-runner.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    assert module.node_version_supported("v20.19.0")
    assert module.node_version_supported("v22.12.0")
    assert module.node_version_supported("v24.0.0")
    assert not module.node_version_supported("v20.18.1")
    assert not module.node_version_supported("v22.11.0")
