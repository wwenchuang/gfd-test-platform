"""Tiny execution graph representation for optional DAG runs."""

from __future__ import annotations

from typing import Any, Dict, List


class ExecutionGraph:
    def __init__(self, nodes=None, edges=None):
        self.nodes = nodes if isinstance(nodes, list) else []
        self.edges = edges if isinstance(edges, list) else []

    @classmethod
    def from_plan(cls, plan: List[Dict[str, Any]]):
        nodes = [str(item.get("node")) for item in (plan or []) if item.get("enabled", True) and item.get("node")]
        edges = [{"from": nodes[i], "to": nodes[i + 1]} for i in range(max(len(nodes) - 1, 0))]
        return cls(nodes=nodes, edges=edges)

    def to_dict(self):
        return {"nodes": self.nodes, "edges": self.edges}
