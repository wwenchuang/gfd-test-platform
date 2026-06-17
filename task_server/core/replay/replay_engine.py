"""Replay execution snapshots safely."""

from __future__ import annotations

from typing import Any, Dict


class ReplayEngine:
    def replay(self, snapshot: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        trace = snapshot.get("trace") if isinstance(snapshot.get("trace"), dict) else {}
        raw = trace.get("raw") if isinstance(trace.get("raw"), dict) else {}
        module = str(raw.get("module") or snapshot.get("context", {}).get("module") or "").strip()
        file_name = str(raw.get("file") or snapshot.get("context", {}).get("file") or "").strip()
        task_name = str(snapshot.get("context", {}).get("targetTaskName") or snapshot.get("context", {}).get("target_task_name") or "").strip()
        plan = {
            "sourceSnapshotId": snapshot.get("id") or snapshot.get("snapshotId") or "",
            "module": module,
            "file": file_name,
            "taskName": task_name,
            "dryRun": dry_run,
            "nodes": [node.get("node") for node in (trace.get("nodes") or []) if isinstance(node, dict)],
        }
        if dry_run:
            return {"ok": True, "mode": "dry_run", "plan": plan, "message": "已生成回放计划，未创建执行任务"}
        if not module or not file_name:
            return {"ok": False, "error": "快照缺少 module/file，不能创建回放任务", "plan": plan}
        from task_server.execution import ExecutionAdapter

        result = ExecutionAdapter().run({
            "module": module,
            "file": file_name,
            "taskName": task_name,
            "createJob": True,
            "mode": "local",
        })
        return {"ok": bool(result.get("ok")), "mode": "runner_job", "plan": plan, "result": result}
