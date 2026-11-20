# 智小白 3D 每日 Android 基线

北京时间每天 08:30 在服务器登记当天任务。周一至周五 08:55 使用手机，周六、周日 08:30 使用手机。手机固定为 OPPO Reno9（ecbfd645），当天记录包含归属账号、预计操作时间、工作台批次 ID 和各用例结果。

先跑我的收藏、打印记录和模型生成记录。三条全部结束且至少两条通过，才提交其他 12 条。最后单独提交关节龙。每阶段仅接受清单里的发布版本；工作台记录正式报告，关闭群通知。打印机固定为 9888E0094F2A。

## 安装和查看下次触发

先按原用例 ID 发布本次的 16 条 YAML，再将本目录的 run.py、baselines.json、install.sh 和两个 systemd unit 放到服务器同一目录。在该目录执行安装，参数是获授权的既有工作台登录名。

```sh
sudo bash install.sh "$WORKBENCH_USERNAME"
systemctl is-enabled midscene-3d-daily.timer
systemctl is-active midscene-3d-daily.timer
systemctl list-timers --all midscene-3d-daily.timer
```

安装脚本用 midscene 系统账号读取主平台的配置和身份库，绑定工作台账号的持久 ID。工作台账号需要启用、具有 ui.view、ui.execute 及 com.kfb.model 的访问范围。脚本实际检查全部 16 个发布版本的一致性，检查通过后启用定时器。主服务运行目录为 /opt/midscene-task-platform，Python 为该目录 .venv/bin/python。

生产打印门禁沿用服务端受保护的 /opt/midscene-auth/print-patrol-prod.cnf。midscene 需要能够执行原平台的生产只读查询，读到设备最近 60 秒的 StandingBy，且该上报以来没有新的 print 下行；查询失败、上报过期、占用或状态不明时，单独记录该日关节龙跳过。下发后，YAML 在手机上复核设备、空闲和新任务回执。

用安装时绑定的账号检查当前数据库门禁可执行以下只读命令。

```sh
sudo -u midscene sh -c 'set -a; . /opt/midscene-3d-daily/current/owner.env; cd /opt/midscene-task-platform && PYTHONPATH=. .venv/bin/python /opt/midscene-3d-daily/current/run.py --printer-status'
```

## 核验当天结果

```sh
sudo cat "/var/lib/midscene-3d-daily/$(TZ=Asia/Shanghai date +%F).json"
journalctl -u midscene-3d-daily.service --since today --no-pager
```

关节龙在 Runner 断言型号和设备通过后，调度按本次执行起止时间核对生产库中该设备唯一新增的打印任务。编号记录在 dragon_receipt.print_sn。缺失、多笔或查询失败记为 dragon_receipt_unconfirmed，保留批次且不重复提交。

按记录中的 batches.smoke、batches.remaining、batches.dragon 到工作台查看对应批次的 Runner 实际结果、截图和报告。临界时发生重启或重复调用时，同一天同一阶段沿用已提交的批次。设备忙时保留跳过原因。中止定时计划可执行 `sudo systemctl disable --now midscene-3d-daily.timer`；已提交批次的状态仍可在工作台查看。
