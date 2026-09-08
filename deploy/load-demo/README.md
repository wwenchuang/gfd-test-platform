# 独立压测演示服务 1.0.1

用途：验收压测平台报告和诊断流程，不代表真实业务或AI模型容量。服务不访问任何上游，不修改FRP，不含API模型调用。普通进程/Pod/GPU/数据库未部署，不能在报告中假装已监控。

## 在目标服务器 43.157.42.206（2核2GB）启动

以下命令在目标演示服务器执行，不是在平台服务器执行。将部署包放到 `/opt`，执行（无需拉整个仓库）：

```bash
mkdir -p /opt/midscene-load-demo
tar -xzf /opt/midscene-load-demo-1.0.1.tar.gz -C /opt/midscene-load-demo
cd /opt/midscene-load-demo
python3 prepare.py
docker compose -f compose.yml --profile monitoring up -d --build
python3 check.py
docker compose -f compose.yml --profile monitoring ps
```

`check.py` 最长等待20秒处理容器刚启动时的连接拒绝、连接重置或超时；HTTP错误仍立即失败，不会被当成“尚未就绪”。

默认演示接口127.0.0.1:18080，Prometheus127.0.0.1:19090。不会占80或7000端口。Python镜像3.12.5-slim-bookworm、Prometheus2.54.1为固定演示版本，升级须按组织安全要求复核。

服务最大0.5核/256MB/48PID；监控最大0.25核/384MB/64PID，数据保留1天或256MB。总限制不是实际消耗；资源不足或OOM应检查日志并停止演示，不能增加并发硬撑。可仅 `up -d --build demo` 将Prometheus放到别的机器，但必须有安全可达的采集地址，替换prometheus.yml的targets并安全复制metrics_token。

需要Linux cgroup v2私有命名空间；不满足时拒绝启动，不退回读取宿主机指标。macOS只能通过Docker Linux虚拟机运行。Docker socket、宿主机文件系统和特权模式均不需要。

`secrets`主机目录权限700；三个随机令牌各自独立，只挂载文件供非root容器读取。prepare.py可重复执行，不更换已有令牌。不要把令牌发到聊天、Git或截图。复制到平台的API令牌取自secrets/api_token；metrics_token仅供Prometheus，admin_token仅用于管理员故障演示。

## 联网前必须完成

默认仅本机可用，平台/专用节点尚不能访问。**不要直接改成0.0.0.0后对公网开放。**

- 有互通可信私网：通过.env设置DEMO_BIND_IP为该机器实际私网地址，并只允许平台、指定压力节点及采集机IP。演示API令牌仍应走加密可信通道；共享/公网网络必须HTTPS。
- 公网服务器：提供可用域名及TLS网关，网关将业务路径代理到127.0.0.1:18080，只允许指定调用方；管理路径不暴露。Prometheus仅向平台开放GET /api/v1/query_range，不开放管理写入或生命周期端点。复用已有企业网关，不调整FRP。
- 公网IP不能当作已开通的私网。CPU/内存数据源填写的是Prometheus查询网关地址，**不是/demo接口，也不是/metrics**。没有安全互通方式时先完成本机检查，不取消平台SSRF/TLS验证。

本包不自动修改防火墙、安全组、Nginx或FRP。平台连通性/预检及监控测试通过后才正式发压。

## 平台配置

新建单独应用/项目：`压测演示（非业务）`，不要并入3D固定基线，不开启定时任务。导入openapi.json（不含密钥）；创建环境时填写实际批准的演示网关地址，并在平台私密配置中设置请求头Authorization为Bearer加api_token。

服务监控选择“容器”：
- Prometheus地址：实际允许的只读查询网关地址；身份凭据在平台填写。
- instance：同机默认 `demo:8080`；远端采集必须填Prometheus实际target/instance值。
- id：`/load-demo`（本导出器标识当前演示容器的逻辑ID，不是宿主机根cgroup）。
- 勾选CPU核数、容器内存working set；5秒采样。服务名建议“演示服务自身容器（cgroup v2）”。
- 开始前至少有2分钟连续历史采样；报告CPU为2分钟rate，不等于单个5秒窗内的瞬时峰值。

本导出器读取当前容器的cpu.stat/memory.current/memory.stat/cpu.max/memory.max。working set=max(memory.current-inactive_file,0)，不是RSS。使用兼容指标名，数据源是演示服务自身，不冒充安装了cAdvisor。限制缺失不输出限制指标。

## 分轮验收（先固定吞吐1次/秒，勿一次全开）

| 独立场景 | 请求 | 断言与观察 |
| --- | --- | --- |
| 正常 | GET /demo/ok | HTTP200且code=0；确认节点/报告闭环 |
| 延迟 | GET /demo/slow | HTTP200且code=0；固定等待600ms，P95阈值设500ms可观察未通过 |
| HTTP错误 | GET /demo/http-error | HTTP应为200/code=0的正常业务断言会失败；接口实际固定500 |
| 业务失败 | GET /demo/business-error | HTTP200但code=1001，必须有code=0断言才能区分业务失败 |
| CPU变化 | GET /demo/cpu | 每次最多150ms墙钟CPU工作，不改变宿主机配额；看CPU曲线与响应时间 |
| 内存变化 | GET /demo/memory | 单容器共享64MiB/30秒TTL，重复请求不累加/不延长当前TTL；持续请求可开始下一轮分配 |

先正常1次/秒30秒验证，资源观察轮建议120秒并勾选监控。需要更大压力必须再检查并发槽和服务器状态，最多4个业务工作槽，超出返回429，不能把演示保护当成服务器最大容量。CPU/内存峰值由真实采样决定，不能要求图表出现固定数字。

固定错误接口用于检查预检失败展示，不绕过门禁。正式失败报告使用 GET /demo/switchable，默认HTTP200/code=0。先确保 `python3 admin.py reset`，让场景通过预检并开始120秒正式执行；运行20秒后，管理员本机执行 `python3 admin.py http-error` 或 `python3 admin.py business-error`。只影响switchable接口，最长120秒后自动恢复，也可 `python3 admin.py reset` 提前恢复。分别测试两种错误，不同时开启；保留code=0业务断言。已配置错误保护时可能提前停止，这是预期行为。管理员需要记录切换时刻，AI仅凭请求错误不能知道后台故障是人为制造的，应在测试条件中注明受控故障演示。

监控缺失：先正常场景通过预检并开始，管理员本机暂停采集120秒（API仍正常），报告应显示缺失而非0。以下命令在脚本内部读取令牌，不打印令牌，也不把令牌放进进程参数：

```bash
python3 admin.py pause-metrics
# 需要提前恢复时
python3 admin.py reset
```

自动到期只恢复指标出口，CPU历史与报告证据恢复仍要等采样。不要反复自动暂停，也不要把管理操作放进业务基线。

## 停止与回滚

```bash
cd /opt/midscene-load-demo
python3 admin.py reset
docker compose -f compose.yml --profile monitoring down
```

默认保留监控卷和令牌，不删除历史。此命令只操作midscene-load-demo项目，与FRP、平台和Agent无关。

参考：[Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)、[Prometheus配置](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)。
