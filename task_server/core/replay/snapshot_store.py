"""Persistent execution snapshot storage."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional

from task_server.config import LEARNING_DIR
from task_server.storage import read_json_file, write_json_file


SNAPSHOT_FILE = os.path.join(LEARNING_DIR, "execution-snapshots.json")


class SnapshotStore:
    def _load(self) -> Dict[str, Any]:
        data = read_json_file(SNAPSHOT_FILE, default={"snapshots": []})
        if isinstance(data, list):
            return {"snapshots": data}
        if isinstance(data, dict):
            data.setdefault("snapshots", [])
            return data
        return {"snapshots": []}

    def _save(self, data: Dict[str, Any]) -> None:
        snapshots = data.get("snapshots") if isinstance(data.get("snapshots"), list) else []
        write_json_file(SNAPSHOT_FILE, {"snapshots": snapshots[-500:]})

    def save(self, trace: Dict[str, Any], context: Optional[Dict[str, Any]] = None, source_id: str = "") -> Dict[str, Any]:
        data = self._load()
        snap_id = f"snap-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        snapshot = {
            "id": snap_id,
            "snapshotId": snap_id,
            "sourceId": source_id or str((trace or {}).get("traceId") or ""),
            "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trace": trace if isinstance(trace, dict) else {},
            "context": context if isinstance(context, dict) else {},
        }
        snapshots = data.get("snapshots") if isinstance(data.get("snapshots"), list) else []
        snapshots.insert(0, snapshot)
        data["snapshots"] = snapshots
        self._save(data)
        return snapshot

    def get(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        snapshot_id = str(snapshot_id or "").strip()
        for snapshot in self.list(limit=None):
            if str(snapshot.get("id") or snapshot.get("snapshotId") or "") == snapshot_id:
                return snapshot
        return None

    def list(self, limit: Optional[int] = 50) -> List[Dict[str, Any]]:
        snapshots = self._load().get("snapshots") or []
        snapshots = [item for item in snapshots if isinstance(item, dict)]
        if limit is not None and limit > 0:
            return snapshots[: int(limit)]
        return snapshots
