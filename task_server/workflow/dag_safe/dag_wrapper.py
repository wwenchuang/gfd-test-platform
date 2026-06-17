"""Public wrapper for optional DAG execution."""

from __future__ import annotations

from typing import Any, Callable, Dict

from .execution_plan import ExecutionPlan
from .simple_dag import SimpleDAG


class DAGWrapper:
    """Build and run a safe, sequential execution graph."""

    def __init__(self, adapters: Dict[str, Callable] | None = None):
        self.plan_builder = ExecutionPlan()
        self.dag = SimpleDAG(adapters or {})

    def run(self, request: Dict[str, Any], ctx: Dict[str, Any] | None = None) -> Dict[str, Any]:
        request = request if isinstance(request, dict) else {}
        ctx = ctx if isinstance(ctx, dict) else dict(request)
        plan = self.plan_builder.build(request)
        result = self.dag.run(plan, ctx)
        result["plan"] = plan
        return result
