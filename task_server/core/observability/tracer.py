"""In-process trace recorder."""

from __future__ import annotations

import threading
from typing import Any, Dict, List

from .span import Span


class Tracer:
    def __init__(self):
        self._spans: List[Span] = []
        self._lock = threading.Lock()

    def start_span(self, node, ctx=None):
        span = Span(node, ctx or {})
        with self._lock:
            self._spans.append(span)
            self._spans = self._spans[-500:]
        return span

    def finish_span(self, span, result=None, error=None):
        if span:
            span.finish(result=result, error=error)
        return span

    def record(self, node, result=None, error=None, ctx=None):
        span = self.start_span(node, ctx or {})
        self.finish_span(span, result=result, error=error)
        return span.to_dict()

    def get_trace(self):
        with self._lock:
            return [span.to_dict() for span in self._spans]

    def clear(self):
        with self._lock:
            self._spans.clear()


global_tracer = Tracer()
