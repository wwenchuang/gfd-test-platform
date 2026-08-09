"""Durable execution events with a bounded Redis wake-up stream."""

import copy
from dataclasses import dataclass
import json
import time

from .executor import redact
from .repositories.execution_repository import ExecutionRepository


@dataclass(frozen=True)
class ExecutionEvent:
    sequence: int
    type: str
    payload: dict
    created_at: object


class EventStream:
    MAX_LENGTH = 2000
    TTL_SECONDS = 24 * 60 * 60

    def __init__(self, session_factory, redis_client=None):
        self.session_factory = session_factory
        self.redis = redis_client

    @staticmethod
    def _key(execution_id):
        return f"api-testing:execution:{execution_id}:events"

    @staticmethod
    def _cancel_key(execution_id):
        return f"api-testing:execution:{execution_id}:cancel"

    def append(self, execution_id, event_type, payload):
        sanitized = redact(copy.deepcopy(payload))
        with self.session_factory.begin() as session:
            record = ExecutionRepository(session).append_event(
                execution_id, event_type, sanitized
            )
            sequence = record.sequence
        if self.redis is not None:
            try:
                key = self._key(execution_id)
                self.redis.xadd(
                    key,
                    {"type": event_type, "payload": json.dumps(sanitized, ensure_ascii=False)},
                    id=f"{sequence}-0",
                    maxlen=self.MAX_LENGTH,
                    approximate=False,
                )
                self.redis.expire(key, self.TTL_SECONDS)
            except Exception:
                pass
        return sequence

    def read(self, execution_id, after_id, block_ms):
        if not isinstance(after_id, int) or after_id < 0:
            raise ValueError("after_id must be a non-negative integer")
        if not isinstance(block_ms, int) or not 0 <= block_ms <= 30_000:
            raise ValueError("block_ms must be between 0 and 30000")
        events = self._read_database(execution_id, after_id)
        if events or block_ms == 0:
            return events
        if self.redis is not None:
            try:
                self.redis.xread(
                    {self._key(execution_id): f"{after_id}-0"},
                    count=1,
                    block=block_ms,
                )
            except Exception:
                time.sleep(min(block_ms, 50) / 1000)
        else:
            time.sleep(min(block_ms, 50) / 1000)
        return self._read_database(execution_id, after_id)

    def _read_database(self, execution_id, after_id):
        with self.session_factory() as session:
            records = ExecutionRepository(session).read_events(execution_id, after_id)
            return tuple(
                ExecutionEvent(
                    sequence=item.sequence,
                    type=item.event_type,
                    payload=copy.deepcopy(item.payload),
                    created_at=item.created_at,
                )
                for item in records
            )

    def signal_cancel(self, execution_id):
        if self.redis is None:
            return
        try:
            self.redis.set(self._cancel_key(execution_id), "1", ex=self.TTL_SECONDS)
        except Exception:
            pass
