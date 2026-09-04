from load_agent.resource_limits import cpu_limit_cores


def test_cpu_limit_uses_cgroup_v2_quota_instead_of_host_cpu_count():
    values = {"/sys/fs/cgroup/cpu.max": "150000 100000"}

    assert cpu_limit_cores(
        read_text=lambda path: values[path],
        host_cpu_count=lambda: 4,
    ) == 1.5


def test_cpu_limit_uses_cgroup_v1_quota_when_v2_is_unlimited():
    values = {
        "/sys/fs/cgroup/cpu.max": "max 100000",
        "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "200000",
        "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
    }

    assert cpu_limit_cores(
        read_text=lambda path: values[path],
        host_cpu_count=lambda: 8,
    ) == 2


def test_cpu_limit_falls_back_to_host_and_never_exceeds_it():
    def missing(_path):
        raise OSError("missing")

    assert cpu_limit_cores(read_text=missing, host_cpu_count=lambda: 4) == 4

    values = {"/sys/fs/cgroup/cpu.max": "800000 100000"}
    assert cpu_limit_cores(
        read_text=lambda path: values[path],
        host_cpu_count=lambda: 4,
    ) == 4
