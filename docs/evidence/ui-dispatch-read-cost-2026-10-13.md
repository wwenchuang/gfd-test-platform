# UI 调度读取成本（T4b3，2026-10-13，本地）

本次在 `codex/ui-platform-simple-workflows`、任务基点 `e035eb7` 上减少调度器对历史 Job 的重复排序和标准化。未部署，没有操作浏览器、设备、凭据或外部通知；本地合成成本不是生产 CPU、HTTP 或 Runner 心跳验收。

## 实现与一致性

- `job_service.load_ui_dispatch_jobs` 仅返回指定派发键的独立深复制 Job，以及运行中手机与 Job ID 的小投影。历史记录不做日期解析、排序或完整标准化；每个源代数只建立一次 ID/占用索引，源来自现有 JSON TTL 缓存，索引保留源对象引用。
- 路径、文件设备号/inode/大小/纳秒 mtime 和源对象变化使投影失效。两处实际写入入口 `job_service.save_jobs`、`runner_service._save_jobs` 都经过现有 `write_json_file` 失效逻辑。删除、重建、路径切换与缓存全清均覆盖。同 inode/大小/mtime 的外部编辑继续遵守既有 3 秒 TTL。
- 签名在缓存读取前后比较。读取中替换文件先失效再重试；连续两次变化抛出 `jobs_storage_changed`，不预约、不终止批次，后台调度下轮重试。
- `DISPATCH_LOCK → JOB_LOCK` 内重新读取占用并进行 SQLite 预约；非重入 `JOB_LOCK` 在 Job 创建之前释放。已有 Runner 领取在 `JOB_LOCK` 内检查预约，阻止预约与创建间的旧任务抢占。SQLite 继续校验当前尝试及设备租约；创建/关联崩溃及同 ID 竞争通过精确查找恢复。
- 当前权限、精确手机、版本、能力、停止证明、私有报告和租约校验保持原流程。Sonic 在锁外读取；结果同步及领取仍用既有权威状态。未增加查询、轮询、睡眠或设备 I/O。

自检复现了现有存储缓存竞争：一个旧读者已解析快照但尚未填缓存，写入/失效先完成，旧读者随后发布过期缓存。`storage.read_json_cached` 使用现有缓存锁内的失效代数阻止该发布，旧读者仍返回自己的快照，公共 TTL/返回/异常语义不变。代数是全局的：其他路径在读取期间失效时，该次读也不发布缓存，可能使后续调用多读取一次文件；既有有效缓存命中不受影响。分别有按路径失效和全清的确定性竞争测试。

## 红绿证据

- 初始 `tests/test_ui_dispatch_read_cost.py`：10 失败、4 已有安全行为通过。50 个真实本地排队批次、2,000 条已完成 Job 与 1 条运行中 Job，旧代码触发 **100,050 次日期解析**；目标检查期间的 Job 状态变化被旧批次快照漏掉。
- 加入投影/锁内预约后最初 14 项通过。补充锁跨度、精确重复创建回退后 UI 专项共 15 项通过。
- 存储缓存竞争先得到 2 失败、1 已有行为通过；失效代数修复后 3 项通过。
- 最终定向组 **181 项通过**：UI 调度/恢复/批次服务/事件、iOS 与普通 Runner 领取、Job 生命周期/时间/audit、设备快照，以及新增投影和缓存测试。
- 主链及改动模块 Python 编译、后端静态 **63 项**、差异检查通过。没有前端、AI Gateway 或 Runner 脚本改动。

新增专项覆盖两个写入入口、状态在批次间变化、重复确定性创建、取消/迟到结果与后续租约、嵌套可变字段隔离、路径/删除/重建/空数据/重启、TTL、读取期间替换文件、连续替换失败后重试、预约时的锁状态及预约到创建窗口的旧任务领取。

## 同条件本地成本对照

每组 50 次查询，7 轮测量，取中位数；每轮清空缓存与投影，包含首次 JSON 读取及索引建立。输入为指定数量的已完成历史 Job，加 1 条运行中和 1 条选定待执行 Job。旧路径复刻全量 `load_jobs(limit=None)`、ID 表和运行占用过滤；新路径使用本次投影。时延测量不安装计数 mock；计数另跑同一输入和 50 次查询。

| 已完成历史条数 | 旧 50 次中位耗时 | 新 50 次中位耗时 | 旧完整标准化/日期解析 | 新完整标准化/日期解析 | 新状态轻量分类次数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 21.311 ms | 0.589 ms | 5,100 / 5,100 | 50 / 0 | 152 |
| 2,000 | 452.399 ms | 1.738 ms | 100,100 / 100,100 | 50 / 0 | 2,052 |

两种路径每轮均解析 JSON **1 次**，分别加载 **102/2,002 行**。新路径每次交付 1 条精确 Job 和 1 条运行占用；额外 50 次标准化只处理指定 Job。另一项真实批次专项没有已存在的精确 Job，因此 50 个被占用阻塞的批次完整标准化/日期解析均为 **0**，JSON 只读 1 次。

新路径首次读取、Job 文件写入、外部替换和 TTL 刷新仍需解析 JSON 并对当前存储代数做一次轻量索引扫描，成本与总存储大小相关。旧 Job 创建/状态更新仍保存整个 JSON 文件，本次没有改持久化机制。成本证据限于避免**未变化历史在每个批次上的重复完整标准化/排序**，不能宣称不同写入代数的开销恒定或生产性能达标。

原始可重跑脚本及数值保存在本次任务 `/tmp/codex-ui-simple-20261013/t4b3-benchmark.py`、`t4b3-benchmark.json`；确定性计数回归保存在仓库测试中。

## 验证命令

在本隔离仓库执行：

```bash
TASK_AUTH_DB=/tmp/codex-ui-simple-20261013/t6-auth-fixture/identity.sqlite3 PYTHONPATH=/tmp/codex-ui-simple-20261013/report-test-deps python3 -m pytest -q tests/test_ui_dispatch_read_cost.py tests/test_storage_cache_coherence.py tests/test_ui_batch_dispatch.py tests/test_ui_batch_recovery.py tests/test_ui_execution_service.py tests/test_ui_runner_events.py tests/test_ios_case_reuse.py tests/test_job_time_parsing.py tests/test_job_lifecycle_regressions.py tests/test_job_audit.py tests/test_job_audit_lookup_cost.py tests/test_device_snapshot_sync.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/job_service.py task_server/services/ui_batch_dispatch.py task_server/storage.py
python3 tests/backend_static_checks.py
git diff --check
PYTHONPATH=. python3 /tmp/codex-ui-simple-20261013/t4b3-benchmark.py
```

## 扩大测试中的任务基点失败

最初扩大组加入 `test_sonic_integration.py` 后为 220 通过/71 失败，失败属于旧入口导出要求（如 `load_startup_env`）。从任务基点 `e035eb7` 单独 archive 入口、模块与测试，在 `/tmp/codex-ui-simple-20261013/t4b3-base-env` 重现代表用例：1 失败/109 未选。这仅证明相对本子任务基点既有，不代表原始主分支情况。

第二次扩大组加入 `test_operation_http.py`、`test_case_business.py` 后为 236 通过/2 失败；路由模式用例只替换 `([^/]+)`，未给私有附件路由的 hash 正则构造真实参数；spool 用例期望 4 个事件但得到 3 个。相同两项在独立 `e035eb7` 模块/测试环境重现为 **2 失败/32 未选**，主任务已接收整体集成审查。初次隔离复制少了 `test_identity.py` 引起收集错误，补齐基点测试 fixture 后才取得上述复现。没有回退共享 HEAD 或改兼容代码。

最终定向 181 项绿色验收和上述扩大组失败分别报告，不能称全部仓库测试通过。完整命令与结果、限制、文件清单见 `/tmp/codex-ui-simple-20261013/task-4b3-report.md`。

## 发布与剩余验收

本项是服务端变更，后续随主任务授权部署主服务器并重启服务，无独立 Runner 升级或新依赖。部署命令由主任务统一给出。仍须独立验证上线后的 CPU/HTTP/心跳与新批次整链路，Windows 原生进程及实机验收不属于本次合成测试结论。
