"""Normalize execution traces for API and UI."""

from __future__ import annotations

import time
from typing import Any, Dict, List


def _parse_ts(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    text = text.replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except Exception:
            continue
    return 0.0


def _duration_ms(start, end) -> int:
    a = _parse_ts(start)
    b = _parse_ts(end)
    if not a or not b or b < a:
        return 0
    return int((b - a) * 1000)


class TraceFormatter:
    def format_node(self, node: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
        node = node if isinstance(node, dict) else {}
        status = str(node.get("status") or node.get("state") or "").upper()
        error = node.get("error") or node.get("message") if status in {"FAILED", "ERROR", "TIMEOUT"} else node.get("error")
        start = node.get("startedAt") or node.get("started_at") or node.get("ts") or node.get("time") or ""
        end = node.get("endedAt") or node.get("ended_at") or node.get("finished_at") or node.get("endTime") or ""
        duration = node.get("durationMs")
        if duration in (None, ""):
            duration = _duration_ms(start, end)
        return {
            "id": str(node.get("id") or node.get("callId") or node.get("step") or node.get("type") or f"node-{index}"),
            "node": str(node.get("node") or node.get("step") or node.get("toolName") or node.get("type") or f"node-{index}"),
            "title": str(node.get("title") or node.get("summary") or node.get("message") or node.get("outputSummary") or ""),
            "status": self.normalize_status(status or node.get("status")),
            "startedAt": start,
            "endedAt": end,
            "durationMs": int(duration or 0),
            "input": node.get("input") if isinstance(node.get("input"), dict) else {},
            "result": node.get("result") or node.get("output") or node.get("outputSummary") or {},
            "error": str(error or ""),
            "events": node.get("events") if isinstance(node.get("events"), list) else [],
            "diagnosis": node.get("diagnosis") if isinstance(node.get("diagnosis"), dict) else {},
        }

    def normalize_status(self, status: Any) -> str:
        text = str(status or "").strip().lower()
        if text in {"success", "done", "passed", "ok"}:
            return "success"
        if text in {"failed", "fail", "error", "timeout", "cancelled", "canceled"}:
            return "failed"
        if text in {"running", "in_progress"}:
            return "running"
        if text in {"pending", "waiting", "wait_confirm"}:
            return "waiting"
        if text in {"skipped", "skip"}:
            return "skipped"
        return text or "unknown"

    def format_trace(self, trace_id: str, source_type: str, source: Dict[str, Any], nodes: List[Dict[str, Any]]):
        formatted_nodes = [self.format_node(node, i) for i, node in enumerate(nodes or [])]
        failed = [n for n in formatted_nodes if n.get("status") == "failed"]
        running = [n for n in formatted_nodes if n.get("status") == "running"]
        total_duration = sum(int(n.get("durationMs") or 0) for n in formatted_nodes)
        return {
            "id": trace_id,
            "traceId": trace_id,
            "sourceType": source_type,
            "sourceId": str(source.get("runId") or source.get("job_id") or source.get("jobId") or trace_id),
            "title": str(source.get("target") or source.get("target_task_name") or source.get("file") or source.get("runId") or trace_id),
            "status": self.normalize_status(source.get("status") or source.get("state")),
            "updatedAt": str(source.get("updatedAt") or source.get("updated_at") or source.get("finished_at") or source.get("createdAt") or source.get("created_at") or ""),
            "summary": {
                "totalNodes": len(formatted_nodes),
                "failed": len(failed),
                "running": len(running),
                "durationMs": total_duration,
            },
            "nodes": formatted_nodes,
            "raw": {
                "module": source.get("module") or "",
                "file": source.get("file") or "",
                "currentStep": source.get("currentStep") or "",
                "progress": source.get("progress"),
            },
        }
