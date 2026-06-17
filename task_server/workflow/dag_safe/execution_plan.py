"""Build a conservative execution plan from a request."""

from __future__ import annotations

from typing import Any, Dict, List


class ExecutionPlan:
    """Describe the current platform flow without replacing business logic."""

    DEFAULT_NODES = ("context", "agent", "case", "execution", "report")

    def build(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        request = request if isinstance(request, dict) else {}
        requested = request.get("nodes") or request.get("steps") or self.DEFAULT_NODES
        if not isinstance(requested, (list, tuple)):
            requested = self.DEFAULT_NODES
        enabled = set(str(item).strip() for item in requested if str(item or "").strip())
        if not enabled:
            enabled = set(self.DEFAULT_NODES)
        plan = []
        for node in self.DEFAULT_NODES:
            plan.append({
                "node": node,
                "enabled": node in enabled,
                "reason": "standardized-test-platform-flow",
            })
        return plan
