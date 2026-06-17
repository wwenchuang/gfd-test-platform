# 存储设计文档

## 目录

- [1. 概述](#1-概述)
- [2. 存储目录结构](#2-存储目录结构)
- [3. JSON 状态文件清单](#3-json-状态文件清单)
- [4. 缓存策略](#4-缓存策略)
- [5. 原子写入](#5-原子写入)
- [6. 线程安全与锁](#6-线程安全与锁)
- [7. 路径安全](#7-路径安全)
- [8. SQLite 迁移预留](#8-sqlite-迁移预留)

---

## 1. 概述

当前 Midscene Task Platform 采用 **JSON 文件** 作为持久化存储方案。所有业务数据（Job 记录、Agent 运行历史、Runner 注册表等）均以 JSON 文件形式存储在 `/opt/midscene-learning/` 目录下。

为提升读取性能，采用进程内 **TTL 内存缓存**；为保障写入一致性，采用 **原子写入**（先写临时文件再 rename）。

---

## 2. 存储目录结构

```
/opt/midscene-learning/           # LEARNING_DIR — 核心状态数据
├── jobs.json                     # Job 执行记录
├── agent-runs.json               # Agent 运行历史
├── agent-tool-calls.json         # Agent 工具调用记录
├── repair-drafts.json            # 修复草稿
├── runners.json                  # Runner 注册表
├── task-apps.json                # 应用配置
├── task-meta.json                # 用例元信息
├── baseline-page-refs.json       # 基线页面引用
├── sonic-sync.json               # Sonic 同步状态
├── sonic-suite-results.json      # Sonic 测试套结果
├── sonic-token-cache.json        # Sonic Token 缓存
├── feishu-drafts.json            # 飞书缺陷草稿
├── versions/                     # YAML 版本历史备份
└── runs/                         # 执行日志目录
    └── {job_id}/
        ├── stdout.log
        ├── stderr.log
        └── screenshots/

/opt/midscene-tasks/              # TASK_DIR — YAML 用例文件
├── 3D打印基线/
│   ├── OBJ保龄球打印.yaml
│   ├── 关节龙打印.yaml
│   └── ...
└── AI测试/
    └── ...

/opt/midscene-reports/            # REPORT_DIR — HTML 测试报告
├── report-xxx.html
└── .chunks/                      # 分片上传临时目录

/opt/midscene-assets/             # ASSET_DIR — 上传的资产文件

/opt/midscene-knowledge/          # KNOWLEDGE_DIR — 知识库
└── {app_package}/
    └── pages/
        └── {page_id}/
            ├── meta.json
            └── screenshot.png

/opt/midscene-generate-jobs/      # GENERATE_JOB_DIR — AI 生成任务
```

---

## 3. JSON 状态文件清单

### 3.1 核心业务文件

| 文件 | 常量 | 默认路径 | 数据结构 | 说明 |
|------|------|----------|----------|------|
| `jobs.json` | `JOBS_FILE` | `/opt/midscene-learning/jobs.json` | `Array<Job>` | Job 执行记录，按创建时间倒序 |
| `agent-runs.json` | `AGENT_RUNS_FILE` | `/opt/midscene-learning/agent-runs.json` | `{runs: Array<AgentRun>}` | Agent 运行历史 |
| `agent-tool-calls.json` | `AGENT_TOOL_CALLS_FILE` | `/opt/midscene-learning/agent-tool-calls.json` | `{calls: Array<ToolCall>}` | Agent 工具调用记录 |
| `repair-drafts.json` | `REPAIR_DRAFTS_FILE` | `/opt/midscene-learning/repair-drafts.json` | `{drafts: Array<Draft>}` | 修复草稿，最多保留 500 条 |
| `runners.json` | `RUNNERS_FILE` | `/opt/midscene-learning/runners.json` | `{runner_id: RunnerInfo}` | Runner 注册表（以 runner_id 为键） |

### 3.2 配置与元数据文件

| 文件 | 常量 | 默认路径 | 数据结构 | 说明 |
|------|------|----------|----------|------|
| `task-apps.json` | `TASK_APPS_FILE` | `/opt/midscene-learning/task-apps.json` | `Array<AppConfig>` | 应用配置（含飞书 Webhook） |
| `task-meta.json` | `TASK_META_FILE` | `/opt/midscene-learning/task-meta.json` | `{module: {file: Meta}}` | 用例元信息（最后执行状态等） |
| `baseline-page-refs.json` | `BASELINE_REFS_FILE` | `/opt/midscene-learning/baseline-page-refs.json` | `{ref_key: {page_ids}}` | 基线页面引用关系 |

### 3.3 Sonic 集成文件

| 文件 | 常量 | 默认路径 | 数据结构 | 说明 |
|------|------|----------|----------|------|
| `sonic-sync.json` | `SONIC_SYNC_FILE` | `/opt/midscene-learning/sonic-sync.json` | `{cases: {case_key: SyncState}}` | Sonic 同步状态 |
| `sonic-suite-results.json` | `SONIC_SUITE_RESULTS_FILE` | `/opt/midscene-learning/sonic-suite-results.json` | `{suites: {key: SuiteResult}}` | Sonic 测试套结果 |
| `sonic-token-cache.json` | `SONIC_TOKEN_CACHE_FILE` | `/opt/midscene-learning/sonic-token-cache.json` | `{token, expires_at}` | Sonic Token 缓存 |

### 3.4 AI Gateway 日志文件

| 文件 | 路径 | 说明 |
|------|------|------|
| `ai-calls.jsonl` | `ai-gateway/logs/ai-calls.jsonl` | AI 调用日志（JSONL 格式） |
| `agent-runs.jsonl` | `ai-gateway/logs/agent-runs.jsonl` | Agent 运行日志（JSONL 格式） |

---

## 4. 缓存策略

### 4.1 TTL 内存缓存

`task_server/storage.py` 提供 `read_json_cached(path, ttl_seconds, default)` 函数，实现进程内 TTL 缓存：

```python
# 缓存结构
_JSON_CACHE: dict = {}  # {path: (timestamp, data)}
_CACHE_LOCK = threading.Lock()
```

### 4.2 各业务模块的 TTL 配置

| 模块 | 缓存对象 | TTL | 来源 |
|------|----------|-----|------|
| 通用 | JSON 文件读取 | 3s（默认） | `storage.py` |
| 用例模块 | `list_modules` 结果 | 3s | `yaml_service.py` |
| Job 列表 | `jobs.json` | 3s（默认） | `job_service.py` |
| Agent 运行 | `agent-runs.json` | 2s | `agent_service.py` |
| Agent 工具调用 | `agent-tool-calls.json` | 2s | `agent_service.py` |
| 修复草稿 | `repair-drafts.json` | 2s | `repair_service.py` |
| Sonic 同步状态 | `sonic-sync.json` | 3s | `sonic_service.py` |
| Sonic 项目列表 | Sonic API 响应 | 60s | `sonic_service.py` |
| Sonic 测试套列表 | Sonic API 响应 | 30s | `sonic_service.py` |
| Sonic 执行结果 | Sonic API 响应 | 5s | `sonic_service.py` |

### 4.3 缓存失效

写操作完成后自动调用 `invalidate_json_cache(path)` 失效对应缓存：

```python
def write_json_file(path, data):
    # ... 原子写入 ...
    invalidate_json_cache(str(target))  # 写入后失效
```

支持全局清除：`invalidate_json_cache(path=None)` 清空所有缓存。

---

## 5. 原子写入

### 5.1 JSON 原子写入

`write_json_file(path, data)` 实现：

```
1. 写入临时文件 {path}.tmp
2. flush + fsync 确保数据落盘
3. os.replace(tmp, target) 原子重命名
4. 失效缓存
```

异常处理：如果写入失败，临时文件被重命名为 `{path}.bad` 保留现场。

```python
def write_json_file(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    bad = target.with_suffix(target.suffix + ".bad")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)          # 原子操作
    except Exception:
        if tmp.exists():
            os.replace(tmp, bad)         # 保留现场
        raise
    invalidate_json_cache(str(target))   # 失效缓存
```

### 5.2 文本原子写入

`write_text_file(path, text)` 实现类似，临时文件命名为：

```
.{basename}.tmp.{pid}.{thread_id}
```

### 5.3 损坏文件备份

`read_json_file` 在解析失败时自动备份损坏文件：

```python
def read_json_file(path, default=None):
    try:
        return json.load(f)
    except Exception as e:
        # 将损坏文件备份为 {path}.bad.{timestamp}
        bad = f"{path}.bad.{int(time.time())}"
        # 复制损坏文件 → bad
        return default
```

---

## 6. 线程安全与锁

所有共享数据的写操作使用 `threading.Lock` 保护：

| 锁 | 常量 | 保护对象 |
|----|------|----------|
| `JOB_LOCK` | `config.JOB_LOCK` | `jobs.json` 读写 |
| `RUNNER_LOCK` | `config.RUNNER_LOCK` | `runners.json` 读写 |
| `AGENT_RUN_LOCK` | `config.AGENT_RUN_LOCK` | `agent-runs.json` 读写 |
| `SONIC_LOCK` | `config.SONIC_LOCK` | Sonic API 调用 |
| `SONIC_SUITE_LOCK` | `config.SONIC_SUITE_LOCK` | Sonic 测试套结果 |
| `GENERATE_LOCK` | `config.GENERATE_LOCK` | AI 生成任务 |
| `ID_LOCK` | `config.ID_LOCK` | 全局 ID 计数器 |
| `_CACHE_LOCK` | `storage._CACHE_LOCK` | TTL 缓存读写 |
| `_MEM_CACHE_LOCK` | `sonic_service._MEM_CACHE_LOCK` | Sonic 内存缓存 |

---

## 7. 路径安全

`safe_join(root, *parts)` 防止路径遍历攻击：

```python
def safe_join(root, *parts):
    root_abs = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root_abs, *parts))
    if path != root_abs and not path.startswith(root_abs + os.sep):
        raise ValueError("非法路径")
    return path
```

所有用户输入的文件路径（`module`、`file` 参数）均经过 `safe_join` 校验和 `clean_filename` 清洗。

---

## 8. SQLite 迁移预留

### 8.1 当前限制

JSON 文件存储存在以下瓶颈：

1. **并发写入**：全文件重写，锁粒度为整个文件
2. **查询效率**：无法按字段索引查询，必须全量加载
3. **数据量**：Job/Agent Run 记录持续增长，文件膨胀影响性能
4. **原子性**：单文件级别，无事务支持

### 8.2 迁移计划

未来可考虑迁移到 SQLite，关键约束：

- **零侵入**：不改变外部接口路径、请求/响应格式
- **渐进迁移**：优先迁移高频读写的 `jobs.json` 和 `agent-runs.json`
- **抽象层**：通过 `storage.py` 统一接口，上层业务代码无需变更
- **兼容期**：JSON → SQLite 双写过渡，确保回退安全

### 8.3 预期表结构

```sql
-- 仅供参考，非当前实现
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    module TEXT,
    file TEXT,
    status TEXT,
    created_at TEXT,
    finished_at TEXT,
    runner_id TEXT,
    device_id TEXT
);

CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT,
    status TEXT,
    current_step TEXT,
    options TEXT,  -- JSON
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE repair_drafts (
    draft_id TEXT PRIMARY KEY,
    job_id TEXT,
    module TEXT,
    file TEXT,
    status TEXT,
    created_at TEXT,
    applied_at TEXT
);
```

迁移时只需修改 `storage.py` 中的读写函数实现，上层 `service` 模块无需改动。
