"""Optional parallel DAG runner."""

from __future__ import annotations

from typing import Any, Callable, Dict

from .execution_graph import ExecutionGraph
from .node_scheduler import NodeScheduler
from .parallel_executor import ParallelExecutor


class ParallelDAGRunner:
    def __init__(self, adapters: Dict[str, Callable], max_workers: int = 4):
        self.adapters = adapters if isinstance(adapters, dict) else {}
        self.max_workers = max_workers

    def run(self, graph: ExecutionGraph, ctx: Dict[str, Any]) -> Dict[str, Any]:
        graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph()
        ctx = ctx if isinstance(ctx, dict) else {}
        scheduler = NodeScheduler()
        executor = ParallelExecutor(self.adapters, max_workers=self.max_workers)
        results: Dict[str, Any] = {}
        for batch in scheduler.batches(graph.nodes):
            batch_result = executor.run_batch(batch, ctx)
            for node, output in batch_result.items():
                if isinstance(output, dict):
                    ctx.update({k: v for k, v in output.items() if k not in {"ctx", "context"}})
            results.update(batch_result)
        return {"ok": True, "graph": graph.to_dict(), "results": results, "trace": executor.tracer.get_trace()}


# Keep the typo alias because some planning notes used it.
ParallelDAGRuuner = ParallelDAGRunner
