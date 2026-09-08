import copy
import importlib
import importlib.util

import pytest


def _validator():
    module = "task_server.api_testing.services.load_resource_service"
    assert importlib.util.find_spec(module), "server runtime resource validation missing"
    return importlib.import_module(module).validate_generator_resources


def _payload():
    return {"role": "load_generator", "interval_seconds": 5, "sample_count": 1, "dropped_samples": 0,
            "samples": [{"sampled_at": "2026-09-08T10:00:00+00:00", "cpu_scope": "cgroup_v2",
                         "cpu_used_cores": .5, "cpu_limit_cores": 2, "cpu_percent": 25,
                         "cpu_limit_source": "cgroup_quota", "memory_scope": "cgroup_v2",
                         "memory_used_bytes": 256, "memory_limit_bytes": 1024, "memory_percent": 25}]}


def test_runtime_resources_validate_and_preserve_nulls():
    payload = _payload()
    payload["samples"][0].update(cpu_used_cores=None, cpu_percent=None,
                                 memory_used_bytes=None, memory_percent=None, memory_scope="unavailable")
    result = _validator()(payload)
    assert result == payload
    assert result is not payload


@pytest.mark.parametrize("field,value", [("cpu_used_cores", float("nan")), ("cpu_limit_cores", 0),
    ("cpu_percent", 50), ("memory_used_bytes", -1), ("memory_used_bytes", True),
    ("memory_limit_bytes", 0), ("memory_percent", 0), ("cpu_scope", "target"),
    ("sampled_at", "2026-09-08T10:00:00"), ("unknown", "secret")])
def test_invalid_resource_samples_are_rejected(field, value):
    validator = _validator()
    payload = _payload()
    payload["samples"][0][field] = value
    with pytest.raises(ValueError):
        validator(payload)


def test_resource_bounds_count_order_and_role_are_validated():
    validator = _validator()
    for change in [{"role": "target"}, {"interval_seconds": 0}, {"sample_count": 3},
                   {"dropped_samples": -1}, {"samples": [_payload()["samples"][0]] * 3001}]:
        with pytest.raises(ValueError):
            validator({**_payload(), **change})


def test_live_resource_tail_merges_without_losing_previous_evidence():
    module = importlib.import_module("task_server.api_testing.services.load_resource_service")
    assert hasattr(module, "merge_generator_resources")
    previous = _payload()
    incoming = copy.deepcopy(previous)
    incoming.update(sample_count=2, dropped_samples=1)
    incoming["samples"][0]["sampled_at"] = "2026-09-08T10:00:05+00:00"
    merged = module.merge_generator_resources(previous, incoming)
    assert merged["sample_count"] == 2
    assert merged["dropped_samples"] == 0
    assert len(merged["samples"]) == 2
    assert module.merge_generator_resources(merged, incoming) == merged
    with pytest.raises(ValueError):
        module.merge_generator_resources(merged, previous)
