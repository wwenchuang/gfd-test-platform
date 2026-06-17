"""Observability primitives."""

from .span import Span
from .tracer import Tracer, global_tracer

__all__ = ["Span", "Tracer", "global_tracer"]
