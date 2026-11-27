# 工作台服务日志巡检

在测试平台服务器上，每五分钟读一个已结束的五分钟窗口。来源为本机 systemd journal 中的 midscene-task、midscene-api-worker、midscene-api-thinking-worker 和 midscene-api-scheduler 四个服务。统计 HTTP 5xx、Python traceback、journal 严重级别 0 至 3 或含 ERROR、CRITICAL、FATAL 标记的日志行。每行只记一类，同一异常可能产生多行。HTTP 5xx 仅统计 journal 中实际存在的 HTTP 访问日志行。工作台 TaskHTTPHandler 默认关闭逐请求访问日志，因此这里的计数不能作为完整的接口 5xx 统计。状态和通知仅含固定类别、计数、服务名、时间窗及只读复查命令。

## 只读预演

从部署目录运行。可重复指定白名单中的 `--unit`，省略时检查全部四个服务。预演仅读日志并输出 JSON，无需飞书配置，也不创建状态文件。

```sh
python3 deploy/log-monitor/monitor.py --dry-run --unit midscene-task.service
```

`coverage=complete` 表示可以读取该服务的 journal，且该窗口的日志读取完整。日志源不存在、没有历史可见记录、命令缺失或权限不足时返回 `source_unavailable`。每个服务每窗最多分析最新 500 行，超限或字段无法解析时标为 `partial`。非完整窗口的退出码是 2，只有完整观察到零命中才会结束该源的错误告警。恢复消息仅描述这一窗口的日志观测。

## 持续运行

将仓库中的服务和定时器安装到已有工作台主机。部署账户需能读取四个服务的 journal，服务使用专属的 0700 状态目录。以下命令启用日志采集并写入本机状态。

```sh
sudo install -m 0644 deploy/log-monitor/midscene-log-monitor.service deploy/log-monitor/midscene-log-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now midscene-log-monitor.timer
systemctl list-timers midscene-log-monitor.timer
sudo journalctl -u midscene-log-monitor.service -n 3 --no-pager
```

飞书投递默认为关闭。需要通知时，在 `/etc/midscene-log-monitor.env` 配置 `MIDSCENE_LOG_MONITOR_NOTIFY=1`。投递复用工作台 `/opt/midscene.env` 已配置的 `FEISHU_WEBHOOK_DEFAULT`。下一次巡检即读取配置，外部投递会发生在发现错误、采集缺失、监控中断或恢复时。不要在命令行粘贴机器人地址。

状态位于 `/var/lib/midscene-log-monitor/state.json`，工作台 Web 服务与定时器共用 midscene 系统账号。具有平台配置权限及平台共用数据范围的登录用户可通过工作台或 `GET /api/platform/log-monitor/status` 查看白名单服务的错误类别、条数、覆盖和投递状态。接口只读取现有状态。首次定时采集前显示 unavailable，所有来源的窗口结束时间都在 360 秒内显示 current，超过该时间或有来源仍未采集显示 stale。新鲜度与来源本身是否可读分别展示。状态文件中每个服务保留最近窗口和最近一次告警。同目录的 history.sqlite3 按服务与窗口去重，仅保存最近 48 小时的采集覆盖、时间和三种固定错误计数。失败调查按本次 API 或 UI 的实际起止时间读重叠窗口，优先有错误的窗口，最多展示 8 个，并标明完整覆盖数量。未运行巡检期间无法补回历史窗口。持续异常只发送一次，同一窗口重跑会沿用已存状态。明确拒绝或渠道缺失会在后续窗口重试最近状态，更新的状态变更取代旧通知。接收端业务码 0 记录为 `sent`；无有效回执、网络超时或发送期间进程退出记录为 `unknown`，需人工核对群内记录。恢复后跨过的窗口以 `gap_seconds` 记录；计数只覆盖实际观测窗口。

## 智小白业务日志接入范围

上述四个来源只覆盖工作台服务器。智小白 App 后台、其他主机、容器或测试数据库尚未在该巡检器接入。接入前需要服务负责人提供实际 QA 环境标识、业务服务名、日志平台与只读数据范围，以及代表性的错误和正常样例，再把对应只读来源和识别规则加入白名单。跨环境同名服务应使用不同的来源标识与告警渠道。平台原有按执行和用例绑定的设备日志继续用于单次失败取证。
