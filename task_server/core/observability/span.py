"""Trace span data model."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict


def _redact(value: Any):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in ("key", "token", "password", "secret")):
                redacted[key] = "***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value[:50]]
    return value


class Span:
    def __init__(self, node_name: str, ctx: Dict[str, Any] | None = None):
        self.id = str(uuid.uuid4())
        self.node = str(node_name or "unknown")
        self.start_time = time.time()
        self.end_time = None
        self.ctx_snapshot = _redact(dict(ctx or {}))
        self.result = None
        self.error = None

    def finish(self, result=None, error=None):
        self.end_time = time.time()
        self.result = _redact(result)
        self.error = str(error) if error else None

    def to_dict(self):
        duration = None
        if self.end_time:
            duration = round((self.end_time - self.start_time) * 1000, 2)
        return {
            "id": self.id,
            "node": self.node,
            "startedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_time)),
            "endedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.end_time)) if self.end_time else "",
            "durationMs": duration,
            "status": "error" if self.error else ("running" if not self.end_time else "success"),
            "input": self.ctx_snapshot,
            "result": self.result,
            "error": self.error,
        }
