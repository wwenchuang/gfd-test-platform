import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("filename", ["windows-midscene-runner.py", "mac-midscene-runner.py"])
@pytest.mark.parametrize("explicit", [None, "1", "0"])
def test_runner_does_not_disable_tls_verification_by_default(filename, explicit):
    """Execute the real env builder without reading credentials, network or ADB."""
    path = Path(__file__).resolve().parents[1] / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=filename)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "midscene_env")
    environment = {"NODE_EXTRA_CA_CERTS": "fixture-company-ca.pem"}
    if explicit is not None:
        environment["NODE_TLS_REJECT_UNAUTHORIZED"] = explicit
    scope = {
        "os": SimpleNamespace(environ=environment),
        "task_runtime_env": lambda: {},
        "infer_midscene_model_family": lambda *_args: "qwen3",
        "ensure_android_sdk_env": lambda _env: None,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), filename, "exec"), scope)
    result = scope["midscene_env"]("fixture-device")
    assert result.get("NODE_TLS_REJECT_UNAUTHORIZED") == explicit
    assert result["NODE_EXTRA_CA_CERTS"] == "fixture-company-ca.pem"
    assert result["ANDROID_SERIAL"] == "fixture-device"
