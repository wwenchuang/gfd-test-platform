"""A stale/nonexistent confirmation must never convert failure into success."""
import copy

import pytest

from task_server.services import agent_service


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "DONE", "RUNNING", "WAIT_CONFIRM"])
@pytest.mark.parametrize("action", ["CONTINUE", "APPLY_REPAIR_AND_RERUN"])
def test_missing_confirmation_never_changes_run(monkeypatch, status, action):
    run = {
        "runId": "audit-confirmation-integrity", "status": status,
        "currentStep": "PLAN", "error": "没有可转换为自动化 YAML 的用例",
        "steps": [{"step": "PLAN", "status": "FAILED"},
                  {"step": "GENERATE_YAML", "status": "SKIPPED"}],
        "pendingConfirmations": [], "artifacts": {},
    }
    original = copy.deepcopy(run)
    writes = []
    monkeypatch.setattr(agent_service, "load_agent_runs", lambda: [run])
    monkeypatch.setattr(agent_service, "save_agent_runs", lambda value: writes.append(copy.deepcopy(value)))
    result = agent_service.confirm_agent_step(run["runId"], "", action)
    assert result.get("error"), "no actual confirmation exists; this is not an approval"
    assert result.get("run") == original
    assert run == original
    assert not writes


def test_unknown_confirmation_id_cannot_approve_a_different_yaml_draft(monkeypatch):
    run = {
        "runId": "audit-stale-confirmation", "status": "WAIT_CONFIRM",
        "currentStep": "WAIT_CONFIRM", "steps": [],
        "artifacts": {"draftPath": "/never-write-this-draft.yaml"},
        "pendingConfirmations": [{"id": "actual-confirmation", "type": "generated_yaml_draft"}],
    }
    original = copy.deepcopy(run)
    monkeypatch.setattr(agent_service, "load_agent_runs", lambda: [run])
    monkeypatch.setattr(agent_service, "save_agent_runs", lambda value: pytest.fail("must not save"))
    monkeypatch.setattr(agent_service, "_confirm_agent_yaml_files", lambda *a: pytest.fail("must not apply draft"))
    result = agent_service.confirm_agent_step(run["runId"], "stale-confirmation", "approve")
    assert result.get("error")
    assert run == original


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "DONE"])
def test_terminal_run_cannot_resume_a_stale_confirmation(monkeypatch, status):
    run = {
        "runId": "audit-terminal-confirmation", "status": status,
        "currentStep": "PLAN", "artifacts": {},
        "pendingConfirmations": [{"id": "stale", "type": "high_risk_action"}],
    }
    original = copy.deepcopy(run)
    monkeypatch.setattr(agent_service, "load_agent_runs", lambda: [run])
    monkeypatch.setattr(agent_service, "save_agent_runs", lambda value: pytest.fail("must not save"))
    result = agent_service.confirm_agent_step(run["runId"], "stale", "approve")
    assert result.get("error")
    assert run == original
