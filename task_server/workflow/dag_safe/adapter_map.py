"""Safe adapters that expose existing services to the optional DAG wrapper."""

from __future__ import annotations

from typing import Any, Dict


def _context_adapter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    from task_server.services.agent_service import normalize_agent_input

    return {"normalizedInput": normalize_agent_input(ctx)}


def _agent_adapter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = str(ctx.get("target") or ctx.get("goal") or ctx.get("text") or "").strip()
    return {"agent": {"status": "ready", "target": target, "mode": ctx.get("mode") or "AUTO_SAFE"}}


def _case_adapter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    from task_server.services.case_service import build_suite

    matched = ctx.get("matchedCases") if isinstance(ctx.get("matchedCases"), list) else []
    return {"suite": build_suite(matched)}


def _execution_adapter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    from task_server.execution import ExecutionAdapter

    case = {
        "module": ctx.get("module") or ctx.get("currentModule") or "",
        "file": ctx.get("file") or ctx.get("currentFile") or "",
        "taskName": ctx.get("taskName") or ctx.get("targetTaskName") or "",
        "mode": ctx.get("executionMode") or "local",
        "createJob": bool(ctx.get("createExecutionJob", False)),
    }
    return {"execution": ExecutionAdapter().run(case)}


def _report_adapter(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {"report": {"status": "ready", "traceable": True}}


def build_adapters():
    return {
        "context": _context_adapter,
        "agent": _agent_adapter,
        "case": _case_adapter,
        "execution": _execution_adapter,
        "report": _report_adapter,
    }
