# 知识评估接口负载复算

在仓库根目录运行：

```sh
python3 -m unittest discover -s tools/perf_report
python3 tools/perf_report/report.py /path/to/run-directory --output /path/to/summary.json
```

脚本只用 Python 标准库。输入根目录可以包含 `negative`、`smoke`、`baseline`、`medium`、`high`、`retest` 六个子目录。每个子目录使用执行器写出的 `requests.jtl`、`metadata.json` 和 `monitor.csv`。每档输出包含三份输入的 SHA-256。执行或恢复期间该档出现在 `pending_stages`，尚未创建的档出现在 `missing_stages`。

- `checks` 独立复核负控的一次 HTTP 200 且问题数断言失败，以及冒烟的两次业务成功。
- `runs[].warm` 和 `runs[].steady` 按 JTL 的请求开始时间落入左闭右开窗口分组。吞吐量为请求起始数量除以窗口墙钟秒数，包括思考与等待时间。每档另列稳态结束后发起的样本数。提前停止会缩短实际统计窗口，计划起止保存在档级字段中。完整稳态还要求最后 5 秒内有实发样本。复算核对 JTL 行数与执行器终态记录的样本数。
- HTTP 成功率统计 2xx。业务成功率要求 2xx 且 JMeter 业务断言通过。失败分为传输错误、HTTP 非 2xx、2xx 但断言失败三类。总错误率以三类失败之和除以总样本数。分位数用全部样本的 JTL `elapsed` 排序，取从 1 开始的 `ceil(分位 × 样本数)` 位，单位为毫秒。空窗口的分位数与成功率为 `null`。
- `monitor` 按预热、稳态和恢复窗汇总实采进程 CPU、RSS、SQLite 数据文件和 WAL 字节数，输出采样数、最小值、最大值和算术均值。指标点按其记录时间分组；CPU 是相邻采样间 CPU 时间的差值。独立 JMeter 进程退出后，恢复窗仅能继续观测服务和数据库。未采到的指标没有条目。
- `comparisons` 比较基线与复测的稳态指标。两档都需完成稳态、有样本，且用户数、思考时间及两段时长一致。差值均为复测减基线，错误率差值的单位为百分点。运行时版本、机器配置和数据快照可用本批次另存的环境清单核对。
