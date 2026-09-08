import os
import time

import pytest

from load_agent import runtime


def _reader(files):
    def read(path):
        if str(path) not in files:
            raise FileNotFoundError(path)
        return files[str(path)]
    return read


def _sampler(files, **kwargs):
    assert hasattr(runtime, "RuntimeResourceSampler"), "runtime resource sampler is missing"
    return runtime.RuntimeResourceSampler(123, read_text=_reader(files), host_cpu_count=lambda: 8,
                                          affinity_count=lambda: 4, **kwargs)


def test_cgroup_v2_uses_quota_cpu_delta_and_bytes_not_host_percent():
    files = {"/proc/123/cgroup": "0::/\n", "/sys/fs/cgroup/cpu.stat": "usage_usec 1000000\n",
             "/sys/fs/cgroup/cpu.max": "200000 100000", "/sys/fs/cgroup/memory.current": "268435456",
             "/sys/fs/cgroup/memory.max": "1073741824"}
    sampler = _sampler(files)
    sampler.sample(now=10)
    files["/sys/fs/cgroup/cpu.stat"] = "usage_usec 3500000\n"
    sampler.sample(now=15)
    result = sampler.snapshot()
    sample = result["samples"][-1]
    assert result["role"] == "load_generator"
    assert sample["cpu_scope"] == "cgroup_v2"
    assert sample["cpu_used_cores"] == .5
    assert sample["cpu_limit_cores"] == 2
    assert sample["cpu_percent"] == 25
    assert sample["memory_used_bytes"] == 268435456
    assert sample["memory_limit_bytes"] == 1073741824
    assert sample["memory_percent"] == 25
    assert result["samples"][0]["cpu_used_cores"] is None


def test_cgroup_v1_unlimited_memory_has_no_fabricated_denominator():
    files = {"/proc/123/cgroup": "2:cpu,cpuacct:/docker/abc\n3:memory:/docker/abc\n",
             "/sys/fs/cgroup/cpu,cpuacct/docker/abc/cpuacct.usage": "1000000000",
             "/sys/fs/cgroup/cpu,cpuacct/docker/abc/cpu.cfs_quota_us": "50000",
             "/sys/fs/cgroup/cpu,cpuacct/docker/abc/cpu.cfs_period_us": "100000",
             "/sys/fs/cgroup/memory/docker/abc/memory.usage_in_bytes": "4096",
             "/sys/fs/cgroup/memory/docker/abc/memory.limit_in_bytes": "9223372036854771712"}
    sampler = _sampler(files)
    sampler.sample(now=1)
    sample = sampler.snapshot()["samples"][0]
    assert sample["cpu_scope"] == "cgroup_v1"
    assert sample["cpu_limit_cores"] == .5
    assert sample["memory_scope"] == "cgroup_v1"
    assert sample["memory_used_bytes"] == 4096
    assert sample["memory_limit_bytes"] is None
    assert sample["memory_percent"] is None


def test_missing_and_invalid_counters_are_null_and_reset_cpu_baseline():
    files = {"/proc/123/cgroup": "0::/", "/sys/fs/cgroup/cpu.stat": "usage_usec 1000000",
             "/sys/fs/cgroup/cpu.max": "200000 100000"}
    sampler = _sampler(files, process_reader=lambda: (None, None))
    sampler.sample(now=1)
    files["/sys/fs/cgroup/cpu.stat"] = "usage_usec nan"
    sampler.sample(now=6)
    sample = sampler.snapshot()["samples"][-1]
    assert sample["cpu_used_cores"] is None
    assert sample["memory_used_bytes"] is None
    assert sample["memory_percent"] is None
    files["/sys/fs/cgroup/cpu.stat"] = "usage_usec 2000000"
    sampler.sample(now=11)
    assert sampler.snapshot()["samples"][-1]["cpu_percent"] is None


def test_bounded_series_counts_omitted_samples_and_preserves_last():
    sampler = _sampler({}, process_reader=lambda: (1, 1024), max_samples=2)
    for index in range(4):
        sampler.sample(now=index * 5)
    result = sampler.snapshot()
    assert len(result["samples"]) == 2
    assert result["sample_count"] == 4
    assert result["dropped_samples"] == 2
    assert result["samples"][-1]["memory_scope"] == "k6_process"
    assert result["samples"][-1]["memory_limit_bytes"] is None


def test_actual_own_process_sampling_is_finite_and_memory_is_positive():
    assert hasattr(runtime, "RuntimeResourceSampler"), "runtime resource sampler is missing"
    sampler = runtime.RuntimeResourceSampler(os.getpid())
    sampler.sample()
    until = time.monotonic() + .05
    while time.monotonic() < until:
        sum(range(1000))
    sampler.sample(force=True)
    sample = sampler.snapshot()["samples"][-1]
    assert sample["cpu_used_cores"] is not None
    assert sample["cpu_used_cores"] >= 0
    assert sample["memory_used_bytes"] > 0


def test_runtime_returns_resources_even_on_metric_failure_and_uploads_live(tmp_path):
    from tests.load_agent.test_runtime import _Process, _Sink, _shard
    process = _Process(['{"metric":broken}\n'])
    sampler = _sampler({}, process_reader=lambda: (1, 4096))
    times = iter([0, 31])
    sink = _Sink()
    assert "resource_sampler_factory" in __import__("inspect").signature(runtime.K6Runtime).parameters
    runner = runtime.K6Runtime(tmp_path, popen=lambda *_a, **_kw: process, poll_interval=0,
                               resource_sampler_factory=lambda *_a, **_kw: sampler,
                               monotonic=lambda: next(times))
    result = runner.run(_shard(), lambda: [], sink)
    assert result.state == "failed"
    assert result.load_generator_resources["samples"][0]["memory_used_bytes"] == 4096
    assert sink.metrics[0][1]["buckets"] == []
    assert sink.metrics[0][1]["load_generator_resources"]["role"] == "load_generator"


def test_nested_cgroup_honors_parent_quota_and_memory_limit():
    files = {"/proc/123/cgroup": "0::/jobs/123", "/sys/fs/cgroup/jobs/123/cpu.stat": "usage_usec 1",
             "/sys/fs/cgroup/jobs/123/cpu.max": "max 100000", "/sys/fs/cgroup/jobs/cpu.max": "75000 100000",
             "/sys/fs/cgroup/jobs/123/memory.current": "64", "/sys/fs/cgroup/jobs/123/memory.max": "max",
             "/sys/fs/cgroup/jobs/memory.max": "1024"}
    sampler = _sampler(files)
    sampler.sample(now=1)
    sample = sampler.snapshot()["samples"][0]
    assert sample["cpu_limit_cores"] == .75
    assert sample["memory_limit_bytes"] == 1024


def test_long_run_resource_interval_fits_whole_day_within_sample_bound():
    from load_agent.runtime_resources import resource_interval
    assert resource_interval({"run": {"configuration": {"workload": {"duration_seconds": 86400}}}}) >= 29
    assert resource_interval({"run": {"configuration": {"workload": {"stages": [{"duration_seconds": 43200}] * 2}}}}) >= 29


def test_cgroup_namespace_mount_root_maps_to_the_visible_mount():
    files = {"/proc/123/cgroup": "0::/docker/abc/job",
             "/proc/123/mountinfo": "31 23 0:28 /docker/abc /sys/fs/cgroup rw - cgroup2 cgroup rw",
             "/sys/fs/cgroup/job/cpu.stat": "usage_usec 1000000",
             "/sys/fs/cgroup/job/cpu.max": "100000 100000",
             "/sys/fs/cgroup/job/memory.current": "256", "/sys/fs/cgroup/job/memory.max": "1024"}
    sampler = _sampler(files)
    sampler.sample(now=1)
    sample = sampler.snapshot()["samples"][0]
    assert sample["cpu_scope"] == "cgroup_v2"
    assert sample["cpu_limit_cores"] == 1
    assert sample["memory_used_bytes"] == 256


def test_full_resource_snapshot_fits_existing_http_body_limit():
    import json
    from load_agent.runtime_resources import MAX_RESOURCE_SAMPLES
    sampler = _sampler({}, process_reader=lambda: (12345.123456789, 922337203685477), max_samples=MAX_RESOURCE_SAMPLES)
    for index in range(MAX_RESOURCE_SAMPLES):
        sampler.sample(now=index * 5)
    assert len(json.dumps({"state": "finished", "summary": {"load_generator_resources": sampler.snapshot()}}).encode()) < 900000
