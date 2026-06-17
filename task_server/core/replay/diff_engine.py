"""Compare two execution snapshots."""

from __future__ import annotations

from typing import Any, Dict, List


class DiffEngine:
    def diff(self, snap_a: Dict[str, Any], snap_b: Dict[str, Any]) -> Dict[str, Any]:
        snap_a = snap_a if isinstance(snap_a, dict) else {}
        snap_b = snap_b if isinstance(snap_b, dict) else {}
        nodes_a = self._nodes(snap_a)
        nodes_b = self._nodes(snap_b)
        by_key_a = {self._node_key(n, i): n for i, n in enumerate(nodes_a)}
        by_key_b = {self._node_key(n, i): n for i, n in enumerate(nodes_b)}
        added = [by_key_b[k] for k in by_key_b.keys() - by_key_a.keys()]
        removed = [by_key_a[k] for k in by_key_a.keys() - by_key_b.keys()]
        changed: List[Dict[str, Any]] = []
        for key in sorted(by_key_a.keys() & by_key_b.keys()):
            a = by_key_a[key]
            b = by_key_b[key]
            diffs = {}
            for field in ("status", "error", "durationMs", "title"):
                if a.get(field) != b.get(field):
                    diffs[field] = {"before": a.get(field), "after": b.get(field)}
            if diffs:
                changed.append({"node": key, "diff": diffs})
        return {
            "ok": True,
            "a": snap_a.get("id") or snap_a.get("snapshotId"),
            "b": snap_b.get("id") or snap_b.get("snapshotId"),
            "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)},
            "added": added,
            "removed": removed,
            "changed": changed,
        }

    def _nodes(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        trace = snapshot.get("trace") if isinstance(snapshot.get("trace"), dict) else {}
        nodes = trace.get("nodes") if isinstance(trace.get("nodes"), list) else []
        return [node for node in nodes if isinstance(node, dict)]

    def _node_key(self, node: Dict[str, Any], index: int) -> str:
        return str(node.get("node") or node.get("id") or f"node-{index}")
