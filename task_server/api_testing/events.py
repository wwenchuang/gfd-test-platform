"""Durable execution events with a bounded Redis wake-up stream."""

import copy
from dataclasses import dataclass
import json
import time

from sqlalchemy import select

from .executor import redact
from .models.load_testing import ApiLoadEvent
from .repositories.execution_repository import ExecutionRepository
from .repositories.load_testing_repository import LoadTestingRepository


@dataclass(frozen=True)
class ExecutionEvent:
    sequence: int
    type: str
    payload: dict
    created_at: object


@dataclass(frozen=True)
class LoadEvent:
    sequence: int
    type: str
    payload: dict
    created_at: object


class EventStream:
    MAX_LENGTH = 2000
    TTL_SECONDS = 24 * 60 * 60
    MAX_PAYLOAD_BYTES = 64 * 1024
    POSTGRES_POLL_SECONDS = 0.05

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
        sanitized = self._bounded_payload(redact(copy.deepcopy(payload)))
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
        deadline = time.monotonic() + (block_ms / 1000)
        use_redis = self.redis is not None
        while True:
            events = self._read_database(execution_id, after_id)
            if events or block_ms == 0:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ()
            if use_redis:
                try:
                    self.redis.xread(
                        {self._key(execution_id): f"{after_id}-0"},
                        count=1,
                        block=max(1, int(remaining * 1000)),
                    )
                except Exception:
                    use_redis = False
                continue
            time.sleep(min(self.POSTGRES_POLL_SECONDS, remaining))

    def _bounded_payload(self, payload):
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) <= self.MAX_PAYLOAD_BYTES:
            return payload
        preview = encoded[: self.MAX_PAYLOAD_BYTES // 2].decode(
            "utf-8", errors="ignore"
        )
        return {
            "truncated": True,
            "original_bytes": len(encoded),
            "preview": preview,
        }

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


class LoadEventStream:
    """Durable load-run events with Redis used only as a bounded wake-up channel."""

    MAX_LENGTH = 2000
    TTL_SECONDS = 24 * 60 * 60
    MAX_PAYLOAD_BYTES = 64 * 1024
    POSTGRES_POLL_SECONDS = 0.05
    INTERNAL_EVENT_TYPES = frozenset({"metric_batch_ingested"})

    def __init__(self, session_factory, redis_client=None):
        self.session_factory = session_factory
        self.redis = redis_client

    @staticmethod
    def _key(run_id):
        return f"api-testing:load-run:{run_id}:events"

    def append(self, run_id, event_type, payload):
        sanitized = EventStream._bounded_payload(self, redact(copy.deepcopy(payload)))
        record = LoadTestingRepository.from_factory(self.session_factory).append_event(
            run_id, event_type, sanitized
        )
        if self.redis is not None:
            try:
                key = self._key(run_id)
                self.redis.xadd(
                    key,
                    {"type": event_type, "payload": json.dumps(sanitized, ensure_ascii=False)},
                    id=f"{record.sequence}-0",
                    maxlen=self.MAX_LENGTH,
                    approximate=False,
                )
                self.redis.expire(key, self.TTL_SECONDS)
            except Exception:
                pass
        return record.sequence

    def read(self, run_id, after_id, block_ms):
        if not isinstance(after_id, int) or after_id < 0:
            raise ValueError("after_id must be a non-negative integer")
        if not isinstance(block_ms, int) or not 0 <= block_ms <= 30_000:
            raise ValueError("block_ms must be between 0 and 30000")
        deadline = time.monotonic() + block_ms / 1000
        use_redis = self.redis is not None
        while True:
            events = self._read_database(run_id, after_id)
            if events or block_ms == 0:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ()
            if use_redis:
                try:
                    self.redis.xread(
                        {self._key(run_id): f"{after_id}-0"},
                        count=1,
                        block=max(1, int(remaining * 1000)),
                    )
                except Exception:
                    use_redis = False
                continue
            time.sleep(min(self.POSTGRES_POLL_SECONDS, remaining))

    def _read_database(self, run_id, after_id):
        with self.session_factory() as session:
            records = tuple(
                session.scalars(
                    select(ApiLoadEvent)
                    .where(
                        ApiLoadEvent.run_id == run_id,
                        ApiLoadEvent.sequence > after_id,
                        ApiLoadEvent.event_type.not_in(self.INTERNAL_EVENT_TYPES),
                    )
                    .order_by(ApiLoadEvent.sequence)
                )
            )
        return tuple(
            LoadEvent(item.sequence, item.event_type, copy.deepcopy(item.payload), item.created_at)
            for item in records
        )
