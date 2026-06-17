"""Production facade for execution, debug, replay, and shadow workflows.

Router code should keep HTTP/auth concerns only.  This facade is the single
entry for execution-shaped operations and debug execution helpers.
"""

from __future__ import annotations

from typing import Any, Dict

from task_server.core.debug import DebugView, DiffEngine, ReplayEngine, SnapshotStore, TraceExporter

from .execution_adapter import ExecutionAdapter


class ExecutionFacade:
    """Small orchestration facade over the stable execution adapter."""

    def __init__(self, adapter: ExecutionAdapter | None = None):
        self.adapter = adapter or ExecutionAdapter()

    def run(self, ctx: Dict[str, Any], mode: str | None = None) -> Dict[str, Any]:
        return self.adapter.run(ctx if isinstance(ctx, dict) else {}, mode=mode)

    def available_modes(self) -> Dict[str, Any]:
        return self.adapter.available_modes()

    def shadow_compare(
        self,
        ctx: Dict[str, Any],
        shadow_modes: list[str] | None = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        return self.adapter.shadow_compare(
            ctx if isinstance(ctx, dict) else {},
            shadow_modes=shadow_modes,
            dry_run=dry_run,
        )

    def run_dag(self, request: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        return self.run({"request": request or {}, "context": ctx or {}}, mode="dag")

    def run_parallel(self, request: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        return self.run({"request": request or {}, "context": ctx or {}}, mode="parallel")

    def list_traces(self, limit: int = 50) -> Dict[str, Any]:
        return TraceExporter().list_traces(limit=limit)

    def get_trace(self, trace_id: str) -> Dict[str, Any] | None:
        return TraceExporter().get_trace(trace_id)

    def render_trace_view(self, trace_id: str = "") -> str:
        exporter = TraceExporter()
        trace = exporter.get_trace(trace_id) if trace_id else None
        if not trace:
            traces = exporter.list_traces(limit=1).get("traces") or []
            trace = traces[0] if traces else {"traceId": "", "title": "暂无 Trace", "nodes": []}
        return DebugView().render(trace)

    def list_snapshots(self, limit: int = 50) -> Dict[str, Any]:
        snapshots = SnapshotStore().list(limit=limit)
        return {"ok": True, "snapshots": snapshots, "total": len(snapshots)}

    def get_snapshot(self, snapshot_id: str) -> Dict[str, Any] | None:
        return SnapshotStore().get(str(snapshot_id or "").strip())

    def save_snapshot_from_trace(
        self,
        trace_id: str,
        trace: Dict[str, Any] | None = None,
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        trace_id = str(trace_id or "").strip()
        resolved_trace = trace if isinstance(trace, dict) else None
        if not resolved_trace and trace_id:
            resolved_trace = TraceExporter().get_trace(trace_id)
        if not resolved_trace:
            return {"ok": False, "error": "Trace 不存在，不能保存快照", "status": 404}
        snapshot = SnapshotStore().save(
            trace=resolved_trace,
            context=context if isinstance(context, dict) else {},
            source_id=trace_id,
        )
        return {"ok": True, "snapshot": snapshot}

    def replay_snapshot(self, snapshot_id: str, dry_run: bool = True) -> Dict[str, Any]:
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            return {"ok": False, "error": "快照不存在", "status": 404}
        return ReplayEngine().replay(snapshot, dry_run=dry_run)

    def diff_snapshots(self, a_id: str, b_id: str) -> Dict[str, Any]:
        store = SnapshotStore()
        snap_a = store.get(str(a_id or "").strip())
        snap_b = store.get(str(b_id or "").strip())
        if not snap_a or not snap_b:
            return {"ok": False, "error": "需要两个有效快照 ID", "status": 400}
        return DiffEngine().diff(snap_a, snap_b)
