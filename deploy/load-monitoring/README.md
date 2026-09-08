# 标准主机监控采集包

该包面向管理员在被测 **Linux 主机**安装 node_exporter 与 Prometheus。网页服务配置由平台后台只读查询。CPU、内存、网络/磁盘属于整机、具体设备与文件系统，不代表某一业务进程。无 Docker socket、无 SSH 密码、无自动远程执行；本目录不会自动下载安装或修改现有监控。

## 部署

1. 从官方发行页选定与本机架构匹配、组织允许的 Prometheus 和 node_exporter 版本，验证该版本公开校验和，将 `prometheus`、`promtool`、`node_exporter` 安装到 `/usr/local/bin`。不要把下载脚本直接传给 shell。
2. 将本目录 `prometheus.yml` 复制到 `/etc/load-monitoring/prometheus.yml`（目录 0755、文件 0644），将两个 `.service` 安装到 `/etc/systemd/system/`。先执行 `/usr/local/bin/promtool check config /etc/load-monitoring/prometheus.yml`，然后执行 `sudo systemctl daemon-reload` 和 `sudo systemctl enable --now node-exporter.service prometheus.service`。每台被测主机各有独立源，或由已有中央 Prometheus 采集，不把多台主机写成同一个 instance。
3. 两个进程只监听 **127.0.0.1**，不会直接暴露网络端口。默认保留 7 天 / 2 GB，先到上限者生效；Prometheus 内存上限 512 MB、CPU 50%，exporter 内存 256 MB、CPU 25%。根据实际规模调整并观察服务日志及资源限制，避免采集包本身造成明显负担。
4. 平台不访问回环地址。需要由内部网关提供可达的、管理员授权的地址。本包可生成仅绑定指定 RFC1918 地址且仅允许平台后台 IP 的 Nginx **只读查询**配置：

   ```bash
   python3 deploy/load-monitoring/render_gateway.py --listen-ip 10.20.0.10 --backend-ip 10.20.0.20 > /tmp/load-monitoring-gateway.conf
   ```

   示例 IP 必须替换为真实地址，校对后由管理员安装到 Nginx 的 http 配置目录，执行 `sudo nginx -t` 再重载。防火墙同时只允许后台 IP 到 TCP 9091。默认仅适用于隔离、可信内网；HTTP 不提供加密与身份保护，跨共享网络必须使用组织 TLS 网关和只读访问认证，禁止暴露公网。带 bearer 凭据的平台地址必须是 HTTPS，令牌在网页密码字段配置，不写入此仓库。网关只允许 GET `/api/v1/query_range`，其他路径 404；Prometheus 管理、写入与生命周期端点均不开放。
5. 等待至少 2 分钟历史采样，再在环境“服务与监控”选择整机，填写 `http://实际私网IP:9091`（或组织 HTTPS 网关）及精确 `instance=127.0.0.1:9100`。只有一个本地主机目标时该 instance 明确对应此数据源；不要把它当平台需要直接请求的地址。选择需要的 CPU、内存、网络/磁盘指标（空间与平均延迟按需选择；空闲磁盘没有可定义的延迟，不会填零），管理员授权地址后保存并测试连接。

## 验收与故障边界

在被测主机执行 `systemctl status node-exporter prometheus`、`ss -lnt`，确认 9100/9090 只绑定回环，网关 9091 只绑定选定私网 IP。确认没有 Docker socket 挂载、没有 root 常驻采集进程。源侧可通过 `curl --fail http://127.0.0.1:9100/metrics` 检查指标；平台后台允许访问网关，其他客户端必须 403，任意非查询路径必须 404。用同一时间点检查原始 PromQL 与网页采样值，设备 ID 必须一致。单元与 promtool 检查不代替这些部署验收。

停止 exporter 后，等待新鲜度窗口（默认 60 秒）过去，网页监控应标记缺失而非 0；重启后 CPU / 各速率需重新形成两分钟历史。检查 Prometheus `up` 和 target scrape 错误定位采集失败。回滚可停用网关配置并 `sudo systemctl disable --now node-exporter prometheus`；默认保留 TSDB，删除历史数据须按环境保留策略另行确认。

## 容器 / Kubernetes 接入

本包不安装特权 cAdvisor 或 Kubernetes 集群组件。已有 cAdvisor / kubelet 监控可复用同一 Prometheus 只读接入：Docker 使用实际 `instance` + 非根 `id`；Kubernetes 使用精确 `instance` + `namespace` + `pod`，采集该 Pod 内真实容器，排除 `container=""` 和 `container="POD"`。需确保指标标签未在现有源中丢弃；缺失不能回退为主机指标。

支持 `container_cpu_usage_seconds_total` 两分钟 rate 与 `container_memory_working_set_bytes`。CPU 配额由同一容器、同一时间的新鲜 `container_spec_cpu_quota / container_spec_cpu_period` 正值计算；无限额或未暴露配额时保留实际核数，占比未知。内存 working set 不是 RSS；本批不推测 cAdvisor limit 是否为真正硬限制，因此不计算容器内存百分比。

主机已支持文件系统 size-free 已用 bytes / size 百分比；保留挂载点、设备、类型，不把多个挂载汇总。磁盘平均读取/写入延迟使用两分钟 rate(累计耗时)/rate(操作数)，不是 P95；零 I/O 时为未知。为保持文件系统真实视图，node_exporter 单元保留可读主机挂载，使用 ProtectHome=read-only、PrivateTmp=no，并由 ProtectSystem=strict 禁止写入。

当前未实现：工作负载跨 Pod 聚合、Pod 就绪/重启计数、PostgreSQL 以外数据库专用指标、容器网络/磁盘模板。主机吞吐与 IOPS 按设备分列，不能把物理盘/分区或虚拟网卡重复求和。非支持选项不在网页伪装可选。

官方资料：[node_exporter 指南](https://prometheus.io/docs/guides/node-exporter/)、[Prometheus 配置](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)、[cAdvisor 指标](https://github.com/google/cadvisor/blob/master/docs/storage/prometheus.md)。


## PostgreSQL 指定数据库

复用已有 prometheus-community/postgres_exporter 的 `pg_stat_database` collector，网页选 PostgreSQL 并填写精确 `instance`、`datname`。只读采集 `pg_stat_database_numbackends`（含空闲连接）、`pg_stat_database_xact_commit` 两分钟提交事务速率、`pg_stat_database_xact_rollback` 两分钟回滚事务速率。事务数不是平台业务链路数；回滚不能直接判成业务失败。此包不保存数据库密码，也不自动安装 exporter 或授予数据库权限。

未接入的最大连接额度、活动事务、慢查询、锁等待不会出现在可选指标中；其他数据库类型不冒用这些模板。连接检查必须从已授权源取得该数据库的实际指标，空匹配或过期结果不可用。官方指标定义：[postgres_exporter pg_stat_database collector](https://github.com/prometheus-community/postgres_exporter/blob/master/collector/pg_stat_database.go)。
