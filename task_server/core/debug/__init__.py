"""Unified debug facade exports.

Keep router free of debugger/replay implementation details.  Existing modules
stay in their original packages; this package is the stable import surface for
execution/debug orchestration.
"""

from task_server.core.debugger.debug_view import DebugView
from task_server.core.debugger.trace_exporter import TraceExporter
from task_server.core.debugger.trace_formatter import TraceFormatter
from task_server.core.replay.diff_engine import DiffEngine
from task_server.core.replay.replay_engine import ReplayEngine
from task_server.core.replay.snapshot_store import SnapshotStore

__all__ = [
    "DebugView",
    "DiffEngine",
    "ReplayEngine",
    "SnapshotStore",
    "TraceExporter",
    "TraceFormatter",
]
