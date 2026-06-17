"""Sequential DAG runner used as a safe wrapper."""

from __future__ import annotations

from typing import Any, Callable, Dict, List


class SimpleDAG:
    """Run enabled nodes in order and merge dict outputs into context."""

    def __init__(self, adapters: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]):
        self.adapters = adapters if isinstance(adapters, dict) else {}

    def run(self, plan: List[Dict[str, Any]], ctx: Dict[str, Any]) -> Dict[str, Any]:
        ctx = ctx if isinstance(ctx, dict) else {}
        result: Dict[str, Any] = {"nodes": {}, "order": []}
        for step in plan or []:
            if not step.get("enabled", True):
                continue
            node = str(step.get("node") or "").strip()
            if not node:
                continue
            handler = self.adapters.get(node)
            if not handler:
                output = {"ok": False, "status": "missing_adapter", "node": node}
            else:
                output = handler(ctx) or {}
            if isinstance(output, dict):
                ctx.update({k: v for k, v in output.items() if k not in {"ctx", "context"}})
            result["nodes"][node] = output
            result["order"].append(node)
        result["context"] = ctx
        return result
