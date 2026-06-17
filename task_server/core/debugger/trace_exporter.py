"""Export real Agent/Runner traces from platform storage."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from task_server.core.observability.tracer import global_tracer
from task_server.services.agent_service import load_agent_runs
from task_server.services.job_service import load_jobs

from .trace_formatter import TraceFormatter


class TraceExporter:
    def __init__(self):
        self.formatter = TraceFormatter()

    def list_traces(self, limit: int = 50) -> Dict[str, Any]:
        traces: List[Dict[str, Any]] = []
        for run in load_agent_runs()[: max(limit, 1)]:
            traces.append(self._agent_trace(run))
        for job in load_jobs(limit=max(limit, 1)):
            traces.append(self._job_trace(job))
        memory_nodes = global_tracer.get_trace()
        if memory_nodes:
            traces.append(self.formatter.format_trace(
                "memory:global",
                "memory",
                {"runId": "memory:global", "status": "running", "target": "内存 DAG Trace"},
                memory_nodes[-100:],
            ))
        traces.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
        traces = traces[: max(limit, 1)]
        return {
            "ok": True,
            "traces": traces,
            "summary": {
                "total": len(traces),
                "agent": len([t for t in traces if t.get("sourceType") == "agent"]),
                "job": len([t for t in traces if t.get("sourceType") == "job"]),
                "memory": len([t for t in traces if t.get("sourceType") == "memory"]),
            },
        }

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        trace_id = str(trace_id or "").strip()
        if not trace_id:
            return None
        if trace_id.startswith("agent:"):
            run_id = trace_id.split(":", 1)[1]
            for run in load_agent_runs():
                if str(run.get("runId") or "") == run_id:
                    return self._agent_trace(run)
        if trace_id.startswith("job:"):
            job_id = trace_id.split(":", 1)[1]
            for job in load_jobs(limit=None):
                if str(job.get("job_id") or job.get("jobId") or "") == job_id:
                    return self._job_trace(job)
        if trace_id == "memory:global":
            return self.formatter.format_trace(
                "memory:global",
                "memory",
                {"runId": "memory:global", "status": "running", "target": "内存 DAG Trace"},
                global_tracer.get_trace()[-100:],
            )
        return None

    def _agent_trace(self, run: Dict[str, Any]) -> Dict[str, Any]:
        nodes: List[Dict[str, Any]] = []
        for step in run.get("steps") or []:
            node = dict(step)
            node["node"] = step.get("step")
            node["events"] = step.get("liveTrace") or step.get("trace") or []
            node["result"] = {
                "summary": step.get("summary") or "",
                "artifactRefs": step.get("artifactRefs") or [],
                "toolCalls": step.get("toolCalls") or [],
            }
            nodes.append(node)
            for call in step.get("toolCalls") or []:
                call_node = dict(call)
                call_node["node"] = call.get("toolName")
                nodes.append(call_node)
        run_id = str(run.get("runId") or "")
        return self.formatter.format_trace(f"agent:{run_id}", "agent", run, nodes)

    def _job_trace(self, job: Dict[str, Any]) -> Dict[str, Any]:
        events = job.get("events") if isinstance(job.get("events"), list) else []
        nodes: List[Dict[str, Any]] = []
        if events:
            for index, event in enumerate(events):
                node = dict(event)
                node["id"] = event.get("id") or f"event-{index}"
                node["node"] = event.get("type") or event.get("event") or "job_event"
                node["status"] = event.get("status") or job.get("status")
                nodes.append(node)
        else:
            nodes.append({
                "node": "runner_job",
                "status": job.get("status"),
                "startedAt": job.get("created_at") or job.get("createdAt") or "",
                "endedAt": job.get("finished_at") or job.get("updated_at") or "",
                "summary": job.get("message") or job.get("stderr_tail") or "",
                "result": {
                    "module": job.get("module") or "",
                    "file": job.get("file") or "",
                    "targetTaskName": job.get("target_task_name") or "",
                    "reportUrl": job.get("report_url") or "",
                },
                "error": job.get("error") or "",
            })
        job_id = str(job.get("job_id") or job.get("jobId") or "")
        return self.formatter.format_trace(f"job:{job_id}", "job", job, nodes)
