"""Strict, backward-compatible generator runtime evidence contract."""

import copy
from datetime import datetime
import math


SCOPES = {"cgroup_v1", "cgroup_v2", "k6_process", "unavailable"}
FIELDS = {"sampled_at", "cpu_scope", "cpu_used_cores", "cpu_limit_cores", "cpu_percent",
          "cpu_limit_source", "memory_scope", "memory_used_bytes", "memory_limit_bytes", "memory_percent"}


def _number(value, *, positive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("压力机资源指标必须是有限数字")
    if value < 0 or positive and value <= 0 or integer and int(value) != value:
        raise ValueError("压力机资源数值范围无效")
    return value


def validate_generator_resources(value):
    if not isinstance(value, dict) or set(value) != {"role", "interval_seconds", "sample_count", "dropped_samples", "samples"}:
        raise ValueError("压力机资源快照字段无效")
    if value["role"] != "load_generator":
        raise ValueError("Agent资源只能标记为压力机")
    interval = _number(value["interval_seconds"], positive=True)
    count = _number(value["sample_count"], integer=True)
    dropped = _number(value["dropped_samples"], integer=True)
    rows = value["samples"]
    if interval < 5 or interval > 3600 or not isinstance(rows, list) or len(rows) > 3000 or count != dropped + len(rows):
        raise ValueError("压力机采样数量或间隔无效")
    previous_time = None
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("压力机资源样本字段无效")
        try:
            timestamp = datetime.fromisoformat(row["sampled_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("压力机资源采样时间无效") from None
        if timestamp.tzinfo is None or previous_time is not None and timestamp < previous_time:
            raise ValueError("压力机资源采样时间必须带时区且有序")
        previous_time = timestamp
        if row["cpu_scope"] not in SCOPES or row["memory_scope"] not in SCOPES:
            raise ValueError("压力机资源采样范围无效")
        if row["cpu_limit_source"] not in {"cgroup_quota", "visible_cpus", "unavailable"}:
            raise ValueError("压力机CPU分母来源无效")
        for name in FIELDS - {"sampled_at", "cpu_scope", "memory_scope", "cpu_limit_source"}:
            if row[name] is not None:
                _number(row[name], positive=name in {"cpu_limit_cores", "memory_limit_bytes"}, integer=name.endswith("bytes"))
        if (row["cpu_limit_cores"] is None) != (row["cpu_limit_source"] == "unavailable"):
            raise ValueError("压力机CPU分母与来源不一致")
        if row["memory_scope"] == "k6_process" and row["memory_limit_bytes"] is not None:
            raise ValueError("单进程RSS不能冒用容器内存限制")
        for prefix, used_field, limit_field in (("cpu", "cpu_used_cores", "cpu_limit_cores"),
                                                ("memory", "memory_used_bytes", "memory_limit_bytes")):
            used, limit, percent = row[used_field], row[limit_field], row[prefix + "_percent"]
            if row[prefix + "_scope"] == "unavailable" and used is not None:
                raise ValueError("资源范围缺失时不能填充使用量")
            expected = used / limit * 100 if used is not None and limit is not None else None
            if expected is None and percent is not None or expected is not None and (percent is None or not math.isclose(percent, expected, rel_tol=1e-6, abs_tol=1e-6)):
                raise ValueError("压力机资源百分比与原始分子分母不一致")
    return copy.deepcopy(value)


def merge_generator_resources(previous, incoming):
    """Merge a small live tail using implicit sample ordinals, with a hard cap."""
    incoming = validate_generator_resources(incoming)
    if not previous:
        return incoming
    previous = validate_generator_resources(previous)
    if incoming["sample_count"] < previous["sample_count"] or incoming["interval_seconds"] != previous["interval_seconds"]:
        raise ValueError("压力机资源快照不能倒退或改变采样间隔")
    indexed = dict(enumerate(previous["samples"], previous["dropped_samples"]))
    for index, row in enumerate(incoming["samples"], incoming["dropped_samples"]):
        if index in indexed and indexed[index] != row:
            raise ValueError("压力机资源已收到的样本不能被覆盖")
        indexed[index] = row
    # Retain only the contiguous suffix: gaps cannot be disguised as complete
    # evidence by packing disjoint samples into implicit consecutive ordinals.
    rows = []
    for index in range(incoming["sample_count"] - 1, -1, -1):
        if index not in indexed or len(rows) == 1500:
            break
        rows.append(indexed[index])
    rows.reverse()
    return validate_generator_resources({**incoming, "samples": rows,
                                         "dropped_samples": incoming["sample_count"] - len(rows)})
