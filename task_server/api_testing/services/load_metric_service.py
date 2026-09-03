"""Validate Agent metric evidence before durable, idempotent ingestion."""

import copy
from datetime import datetime, timezone
import logging
import math
import re

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..events import LoadEventStream
from ..models.load_testing import (
    ApiLoadEvent,
    ApiLoadMetricBucket,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadScenarioVersion,
)


logger = logging.getLogger(__name__)
BUCKET_SECONDS = 5
MAX_BUCKETS = 200
LATENCY_BOUNDS_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
COUNTER_FIELDS = (
    "requests",
    "iterations",
    "dropped_iterations",
    "bytes_sent",
    "bytes_received",
    "http_failures",
    "business_assertions",
    "business_failures",
    "workflow_iterations",
    "workflow_failures",
)
_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


class LoadMetricError(ValueError):
    def __init__(self, message, *, status=422, code="invalid_metric_batch"):
        self.status = status
        self.code = code
        super().__init__(message)


def _timestamp(value):
    if not isinstance(value, str) or not value:
        raise LoadMetricError("指标窗口开始时间必须是带时区的ISO时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LoadMetricError("指标窗口开始时间格式无效") from error
    if parsed.tzinfo is None:
        raise LoadMetricError("指标窗口开始时间必须包含时区")
    parsed = parsed.astimezone(timezone.utc)
    if int(parsed.timestamp()) % BUCKET_SECONDS:
        raise LoadMetricError("指标窗口必须按5秒边界对齐")
    return parsed


def _number(value, field, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LoadMetricError(f"指标 {field} 必须是数字")
    if not math.isfinite(float(value)):
        raise LoadMetricError(f"指标 {field} 必须是有限数字")
    if value < 0:
        raise LoadMetricError(f"指标 {field} 不能为负数")
    if integer and int(value) != value:
        raise LoadMetricError(f"指标 {field} 必须是整数")
    return int(value) if integer else float(value)


class LoadMetricService:
    def __init__(self, session_factory, *, publisher=None):
        self.session_factory = session_factory
        self.publisher = publisher or LoadEventStream(session_factory).append

    def ingest(self, agent_id, shard_id, payload):
        if not isinstance(payload, dict):
            raise LoadMetricError("指标批次必须是对象")
        batch_id = payload.get("batch_id")
        if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
            raise LoadMetricError("指标批次ID格式无效")
        buckets = payload.get("buckets")
        if not isinstance(buckets, list) or not 1 <= len(buckets) <= MAX_BUCKETS:
            raise LoadMetricError(f"每批指标必须包含1到{MAX_BUCKETS}个窗口")

        with self.session_factory.begin() as session:
            shard = session.scalar(
                select(ApiLoadRunShard)
                .where(ApiLoadRunShard.id == shard_id)
                .with_for_update()
            )
            if shard is None:
                raise LoadMetricError("压测分片不存在", status=404, code="shard_not_found")
            if shard.agent_id != agent_id:
                raise LoadMetricError("该分片不属于当前压测节点", status=403, code="shard_not_owned")
            if shard.state in {"finished", "failed", "cancelled"}:
                raise LoadMetricError("压测分片已经结束，不能继续上报指标", status=409, code="shard_finished")
            if shard.state != "running":
                raise LoadMetricError("压测分片尚未开始，不能上报指标", status=409, code="shard_not_running")
            run = session.scalar(
                select(ApiLoadRun).where(ApiLoadRun.id == shard.run_id).with_for_update()
            )
            if run is None or run.state not in {"running", "stopping"}:
                raise LoadMetricError("压测任务当前状态不接受指标", status=409, code="run_not_running")
            duplicate = session.scalar(
                select(ApiLoadEvent.id).where(
                    ApiLoadEvent.run_id == run.id,
                    ApiLoadEvent.event_type == "metric_batch_ingested",
                    ApiLoadEvent.payload.contains({"shard_id": shard.id, "batch_id": batch_id}),
                ).limit(1)
            )
            if duplicate is not None:
                return {"accepted": len(buckets), "duplicate": True}

            version = session.get(ApiLoadScenarioVersion, run.scenario_version_id)
            if version is None:
                raise LoadMetricError("场景版本快照不存在", status=409, code="scenario_missing")
            known_steps = {
                str(item.get("id"))
                for item in (version.definition.get("steps") or [])
                if isinstance(item, dict) and item.get("id")
            }
            known_steps.add("all")
            normalized = [self._bucket(item, known_steps) for item in buckets]
            keys = [(item["step_id"], item["started_at"]) for item in normalized]
            if len(keys) != len(set(keys)):
                raise LoadMetricError("同一批指标不能重复包含相同步骤和时间窗口")
            timestamps = [item["started_at"] for item in normalized]
            if timestamps != sorted(timestamps):
                raise LoadMetricError("指标窗口必须按时间顺序上报")
            for item in normalized:
                values = {
                    "run_id": run.id,
                    "shard_id": shard.id,
                    "scenario_step_id": item["step_id"],
                    "bucket_started_at": item["started_at"],
                    "bucket_seconds": BUCKET_SECONDS,
                    "metrics": item["metrics"],
                    "owner_id": run.owner_id,
                    "created_by": run.owner_id,
                    "updated_by": run.owner_id,
                }
                statement = insert(ApiLoadMetricBucket).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=(
                        ApiLoadMetricBucket.run_id,
                        ApiLoadMetricBucket.shard_id,
                        ApiLoadMetricBucket.scenario_step_id,
                        ApiLoadMetricBucket.bucket_started_at,
                    ),
                    set_={
                        "bucket_seconds": BUCKET_SECONDS,
                        "metrics": copy.deepcopy(item["metrics"]),
                        "updated_by": run.owner_id,
                        "updated_at": func.now(),
                    },
                )
                session.execute(statement)
            sequence = (
                session.scalar(
                    select(func.max(ApiLoadEvent.sequence)).where(ApiLoadEvent.run_id == run.id)
                )
                or 0
            ) + 1
            session.add(
                ApiLoadEvent(
                    run_id=run.id,
                    sequence=sequence,
                    event_type="metric_batch_ingested",
                    payload={"shard_id": shard.id, "batch_id": batch_id, "bucket_count": len(normalized)},
                    owner_id=run.owner_id,
                    created_by=run.owner_id,
                    updated_by=run.owner_id,
                )
            )
            shard.last_heartbeat_at = datetime.now(timezone.utc)
            run_id = run.id

        if self.publisher is not None:
            try:
                self.publisher(
                    run_id,
                    "metrics",
                    {"shard_id": shard_id, "bucket_count": len(buckets)},
                )
            except Exception:
                logger.warning("Unable to publish load metric wake-up run_id=%s", run_id, exc_info=True)
        return {"accepted": len(buckets), "duplicate": False}

    @classmethod
    def _bucket(cls, value, known_steps):
        if not isinstance(value, dict):
            raise LoadMetricError("指标窗口必须是对象")
        step_id = value.get("step_id")
        if not isinstance(step_id, str) or step_id not in known_steps:
            raise LoadMetricError(f"指标包含未知步骤：{step_id or '-'}")
        if value.get("bucket_seconds") != BUCKET_SECONDS:
            raise LoadMetricError("指标窗口必须固定为5秒")
        metrics = value.get("metrics")
        if not isinstance(metrics, dict):
            raise LoadMetricError("指标内容必须是对象")
        unknown = set(metrics) - set(COUNTER_FIELDS) - {"latency_ms", "latency_histogram"}
        if unknown:
            raise LoadMetricError("指标包含不支持字段：" + "、".join(sorted(unknown)))
        normalized = {
            field: _number(metrics.get(field, 0), field, integer=True)
            for field in COUNTER_FIELDS
        }
        normalized["latency_histogram"] = cls._histogram(metrics.get("latency_histogram"))
        if "latency_ms" in metrics:
            latency = metrics["latency_ms"]
            if not isinstance(latency, dict):
                raise LoadMetricError("延迟摘要必须是对象")
            normalized["latency_ms"] = {
                key: _number(latency.get(key, 0), f"latency_ms.{key}", integer=(key == "count"))
                for key in ("count", "p50", "p90", "p95", "p99", "max")
            }
        return {"step_id": step_id, "started_at": _timestamp(value.get("started_at")), "metrics": normalized}

    @staticmethod
    def _histogram(value):
        if not isinstance(value, dict):
            raise LoadMetricError("延迟直方图不能为空")
        bounds = value.get("bounds_ms")
        counts = value.get("counts")
        if bounds != list(LATENCY_BOUNDS_MS):
            raise LoadMetricError("延迟直方图边界与平台版本不兼容")
        if not isinstance(counts, list) or len(counts) != len(LATENCY_BOUNDS_MS) + 1:
            raise LoadMetricError("延迟直方图计数长度无效")
        normalized_counts = [_number(item, "latency_histogram.counts", integer=True) for item in counts]
        count = _number(value.get("count"), "latency_histogram.count", integer=True)
        if sum(normalized_counts) != count:
            raise LoadMetricError("延迟直方图计数合计不一致")
        return {
            "bounds_ms": list(LATENCY_BOUNDS_MS),
            "counts": normalized_counts,
            "count": count,
            "sum_ms": _number(value.get("sum_ms"), "latency_histogram.sum_ms"),
            "max_ms": _number(value.get("max_ms"), "latency_histogram.max_ms"),
        }
