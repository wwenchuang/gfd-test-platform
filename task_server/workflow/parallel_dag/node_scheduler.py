"""Conservative node scheduler."""

from __future__ import annotations

from typing import List


class NodeScheduler:
    """Group independent nodes.  The default keeps order stable."""

    def batches(self, nodes: List[str]):
        return [[node] for node in nodes or []]
