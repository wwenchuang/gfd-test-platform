"""Structured trace logger."""

from __future__ import annotations

from typing import Any, Dict

from .tracer import global_tracer


def log_event(node: str, message: str, **payload: Dict[str, Any]):
    return global_tracer.record(
        node,
        result={"message": message, "payload": payload},
        ctx={"node": node},
    )
