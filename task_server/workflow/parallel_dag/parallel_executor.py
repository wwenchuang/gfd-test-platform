"""Parallel executor with trace support.

It is intentionally conservative: callers decide which nodes may be submitted
together by passing precomputed batches.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

from task_server.core.observability.tracer import Tracer


class ParallelExecutor:
    def __init__(self, adapters: Dict[str, Callable], max_workers: int = 4):
        self.adapters = adapters if isinstance(adapters, dict) else {}
        self.max_workers = max(1, int(max_workers or 4))
        self.tracer = Tracer()

    def run_batch(self, nodes: List[str], ctx: Dict[str, Any]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(len(nodes), 1))) as pool:
            futures = {}
            for node in nodes:
                fn = self.adapters.get(node)
                span = self.tracer.start_span(node, ctx)

                def wrapped(node=node, fn=fn, span=span):
                    try:
                        if not fn:
                            output = {"ok": False, "status": "missing_adapter", "node": node}
                        else:
                            output = fn(ctx) or {}
                        self.tracer.finish_span(span, result=output)
                        return node, output
                    except Exception as exc:
                        output = {"ok": False, "error": str(exc), "node": node}
                        self.tracer.finish_span(span, error=str(exc), result=output)
                        return node, output

                futures[pool.submit(wrapped)] = node
            for future in as_completed(futures):
                node, output = future.result()
                results[node] = output
        return results
