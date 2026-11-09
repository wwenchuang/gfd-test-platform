import importlib.util
import json
from pathlib import Path

import pytest

from task_server.services import agent_service, runner_service


@pytest.mark.parametrize(
    "check_name",
    [
        "check_agent_risk_detail_explains_source",
        "check_agent_requirement_background_delete_is_not_high_risk",
    ],
)
def test_agent_risk_static_checks_do_not_write_external_history_or_audit(tmp_path, monkeypatch, check_name):
    """Calling an individual static check must leave the caller's stores untouched."""
    history = tmp_path / "external-runs.json"
    original = {"runs": [{"runId": "existing", "status": "DONE"}]}
    history.write_text(json.dumps(original), encoding="utf-8")
    audit = tmp_path / "external-audit.jsonl"
    audit.write_text("existing audit\n", encoding="utf-8")

    def external_audit(*_args, **_kwargs):
        with audit.open("a", encoding="utf-8") as stream:
            stream.write("test unexpectedly wrote audit\n")

    monkeypatch.setattr(agent_service, "AGENT_RUNS_FILE", str(history))
    monkeypatch.setattr(agent_service, "_log_tool_call", external_audit)
    monkeypatch.setattr(agent_service, "AGENT_RUNNER_PRECHECK_RETRY_SECONDS", 0)
    monkeypatch.setattr(runner_service, "list_runners", lambda: {})
    monkeypatch.setattr(runner_service, "all_online_devices", lambda: [])
    module_path = Path(__file__).with_name("backend_static_checks.py")
    spec = importlib.util.spec_from_file_location("backend_static_checks_for_isolation", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    getattr(module, check_name)()

    assert json.loads(history.read_text(encoding="utf-8")) == original
    assert audit.read_text(encoding="utf-8") == "existing audit\n"
    assert agent_service.AGENT_RUNS_FILE == str(history)
    assert agent_service._log_tool_call is external_audit


def test_agent_risk_check_restores_stores_after_error(tmp_path, monkeypatch):
    history = str(tmp_path / "external-runs.json")
    audit = lambda *_args, **_kwargs: None
    monkeypatch.setattr(agent_service, "AGENT_RUNS_FILE", history)
    monkeypatch.setattr(agent_service, "_log_tool_call", audit)
    spec = importlib.util.spec_from_file_location(
        "backend_static_checks_for_failure", Path(__file__).with_name("backend_static_checks.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    @module.isolated_agent_risk_check
    def failing_check():
        assert agent_service.AGENT_RUNS_FILE != history
        assert agent_service._log_tool_call is not audit
        raise RuntimeError("test failure")

    with pytest.raises(RuntimeError, match="test failure"):
        failing_check()

    assert agent_service.AGENT_RUNS_FILE == history
    assert agent_service._log_tool_call is audit
