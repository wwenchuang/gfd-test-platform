# API 性能测试 Agent 部署与运维

压测 Agent 是只向平台发起请求的独立 k6 执行节点。它不连接 PostgreSQL、Redis，也不挂载 Docker Socket。平台创建场景、选择节点并汇总报告；Agent 在自身 Docker CPU、内存和进程上限内产生负载。

## 部署前检查

每台 Agent 服务器需要 Linux、Docker Engine 24+、Docker Compose v2，并且能够访问平台和待压测目标。起步配置建议至少为 2 CPU、2 GiB 可分配内存和 10 GiB 可用磁盘，正式容量以节点校准结果为准。

公网连接必须使用 HTTPS。当前平台若仍使用 HTTP，只能在受控私网或 VPN 中显式允许，不应跨公网传输 Agent 凭据和环境密钥。

调度级别在平台生成一次性注册令牌时确定：

| 中文名称 | 英文值 | 用途 |
|---|---|---|
| 首选节点 | `preferred` | 专用压测服务器，优先分配 |
| 普通节点 | `normal` | 稳定的共享服务器，首选容量不足时参与 |
| 备用节点 | `fallback` | 只有任务明确允许时参与，适合平台本机 |
| 停用节点 | `disabled` | 不再接收任务，需要重新注册才能恢复 |

本机硬上限、平台软上限和校准容量三者取最小值。提高页面中的软上限不会突破 Docker 或 Agent 的本机硬上限。

## 第一台服务器

专用节点不需要安装 Git，也不需要拉取主平台、前端、数据库或文档。发布时只上传 `load_agent/` 与 `deploy/load-agent/` 组成的最小 Agent 包；解压后目录结构应为：

```text
midscene-load-agent/
├── load_agent/
└── deploy/load-agent/
```

在 API 平台打开“性能测试 → 压测节点 → 注册节点”，填写容易识别的中文名称，专用服务器选择“首选节点”，并在准备执行安装时再生成有效期 15 分钟的一次性令牌。将最小 Agent 包解压到服务器并进入 `midscene-load-agent` 目录，然后执行页面提供的完整命令：

```bash
PLATFORM_URL='https://你的平台地址' ENROLL_TOKEN='页面的一次性令牌' bash deploy/load-agent/install.sh
```

受控私网或 VPN 内暂时使用 HTTP 时，必须显式包含：

```bash
PLATFORM_URL='http://私网平台地址' ENROLL_TOKEN='页面的一次性令牌' ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT=1 bash deploy/load-agent/install.sh
```

安装脚本构建固定版本镜像、启动容器、等待凭据写入命名卷，然后清空 `.env` 中的令牌并重建容器。一次性令牌不会继续保留在运行容器环境中。

Agent 通过出站 HTTP(S) 长轮询领取任务和上报心跳，不监听对外业务端口，因此不需要为 Agent 新开放入站端口。服务器必须能够出站访问平台地址、被测 API 地址，以及首次构建需要使用的容器镜像源。

## 扩展到第二台及更多服务器

新增节点不需要修改主平台配置，也没有两台上限。每增加一台服务器，就回到节点页生成一个只供该机器使用的新令牌，在新服务器解压相同发布包并执行命令：

```bash
PLATFORM_URL='https://你的平台地址' ENROLL_TOKEN='这台节点的新令牌' bash deploy/load-agent/install.sh
```

任意数量节点在线且校准有效后，平台按调度级别、软硬上限、校准容量和当前占用动态生成 N 个分片。每台机器只执行自己的 VU、吞吐和数据范围，各分片之和保持为任务设置的总目标。继续增加同级节点时只需重复注册和安装；调整优先顺序或是否参与时在“压测节点”页修改级别或在创建任务时选择节点，无需修改或重新部署平台代码。

不要在机器之间复制 `.env`、命名卷或凭据；每个 Agent 必须有独立身份。若首选节点已经覆盖目标容量，普通和备用节点不会为了凑数量而承接压力；需要同层均衡时，把参与节点设为同一调度级别并在任务中选中它们。

## 容量配置示例

```bash
AGENT_MAX_PROCESSES=1 \
AGENT_MAX_VUS=500 \
AGENT_MAX_ITERATIONS_PER_SECOND=2000 \
AGENT_MAX_DURATION_SECONDS=1800 \
LOAD_AGENT_CPU_LIMIT=2.0 \
LOAD_AGENT_MEMORY_LIMIT=2g \
PLATFORM_URL='https://你的平台地址' \
ENROLL_TOKEN='页面的一次性令牌' \
bash deploy/load-agent/install.sh
```

这些值分别表示：最多同时 1 个 k6 进程、最多 500 个虚拟用户（VU）、最多每秒 2000 次迭代、单次最长 30 分钟、容器最多 2 核 CPU 和 2 GiB 内存。不要按服务器总内存直接填满，应给操作系统和监控保留资源。修改本机硬上限后重新执行 `install.sh`，再回到页面重新校准。

## 多镜像源

本机镜像源不稳定时可提供多个候选，脚本按顺序尝试，全部失败才退出：

```bash
K6_IMAGE_CANDIDATES='你的镜像仓库/grafana/k6:0.52.0,grafana/k6:0.52.0' \
PYTHON_IMAGE_CANDIDATES='你的镜像仓库/python:3.12.5-slim-bookworm,python:3.12.5-slim-bookworm' \
bash deploy/load-agent/upgrade.sh
```

镜像标签应固定到明确版本，不要在生产节点使用 `latest`。

## 检查与日志

```bash
bash deploy/load-agent/check.sh
```

该命令检查 `.env` 权限、容器状态和最近 80 行日志，不打印配置文件或注册令牌。随后在平台节点页依次确认节点在线、硬上限正确、校准有效，以及创建运行前的目标连通性预检通过。

持续看日志：

```bash
cd deploy/load-agent
docker compose --env-file .env -f docker-compose.yml logs -f --tail 100 load-agent
```

## 升级

```bash
LOAD_AGENT_IMAGE='midscene-load-agent:明确的新版本' bash deploy/load-agent/upgrade.sh
```

升级只更新固定镜像值并重建容器，保留 `.env`、节点凭据、校准记录和数据卷。Agent 或 k6 版本变化会使旧校准证据失效，应回到平台重新校准后再压测。

## 停止、恢复和卸载

普通停止会保留凭据：

```bash
bash deploy/load-agent/uninstall.sh
```

恢复直接执行 `bash deploy/load-agent/install.sh`。永久清除本机凭据和命名卷必须显式执行：

```bash
bash deploy/load-agent/uninstall.sh --purge
```

永久清除后，还要在平台节点页把旧节点设为停用；再次使用必须生成新令牌。执行 `--purge` 前先确认没有正在运行的分片。

安装成功只代表 Agent 注册和存活。正式启用前还要完成固定 VU、固定到达率、停止下发、指标总数、报告阈值、AI 诊断，以及多节点分片总和和数据范围不重叠验证。受控目标的结果不能当成生产系统容量结论。
