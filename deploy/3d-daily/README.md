# 智小白 3D 每日 Android 基线

北京时间每天 05:00 在服务器登记当天任务，随即检查手机是否可用并开始回归。手机固定为 OPPO Reno9（ecbfd645），当天记录包含归属账号、预计操作时间、工作台批次 ID 和各用例结果。

每日执行 `baselines.json` 中的全部用例，包含发版核心和非核心场景。先跑我的收藏、打印记录和模型生成记录，三条全部结束且至少两条通过后，先提交浏览、编辑及文件导入等独立用例，每批最多 200 条。再检查指定打印机的空闲遥测，串行执行 7 条需要切片的预览用例。每条先创建自己的切片、核对预览，然后取消该切片并检查取消结果。打印机忙或状态不明时，在当天运行记录中保留跳过原因。普通用例失败会保留在当天记录中，后续批次照常执行。关节龙最后单独下发，打印机固定为 9888E0094F2A。每阶段核对发布版本，工作台生成报告并关闭群通知。

## 从上一版发布清单升级

将同版主服务部署后，在专项发布包解压目录执行以下命令。预检会检查当前清单、用例版本和发布身份；应用后会输出实际登记的基线数量以及定时器下次触发时间。

```sh
sudo bash publish-release.sh --check
sudo bash publish-release.sh --apply
```

## 安装和查看下次触发

先按清单中的用例 ID 发布对应 YAML，再将本目录的 run.py、baselines.json、install.sh 和两个 systemd unit 放到服务器同一目录。在该目录执行安装，参数是获授权的既有工作台登录名。

```sh
sudo bash install.sh "$WORKBENCH_USERNAME"
systemctl is-enabled midscene-3d-daily.timer
systemctl is-active midscene-3d-daily.timer
systemctl list-timers --all midscene-3d-daily.timer
```

安装脚本用 midscene 系统账号读取主平台的配置和身份库，绑定工作台账号的持久 ID。工作台账号需要启用、具有 ui.view、ui.execute 及 com.kfb.model 的访问范围。脚本逐项检查清单 ID、模块、文件名和发布内容 SHA-256，检查通过后启用定时器。主服务运行目录为 /opt/midscene-task-platform，Python 为该目录 .venv/bin/python。

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

关节龙在 Runner 断言当前任务和指定设备通过后，调度从该次执行的原始报告核对选中的切片号与实际提交的切片号。再按执行时间查指定设备唯一一次 print 下行，要求其请求号与切片号一致、下发成功，并有同设备同请求号的上行。打印单号、下行和上行记录号分别记在 dragon_receipt.print_sn、command_id、ack_id。缺失、多笔或查询失败记为 dragon_receipt_unconfirmed，保留批次且不重复提交。

按当天记录中 `batches` 的批次 ID 到工作台查看 Runner 结果、截图和报告。冒烟批次记为 `smoke`，普通批次依次记为 `remaining`、`remaining_2` 等，切片预览记为 `previews`，关节龙记为 `dragon`。各批次逐条结果保存在 `outcomes`。临界时发生重启或重复调用时，同一天同一阶段沿用已提交的批次。设备忙时保留跳过原因。中止定时计划可执行 `sudo systemctl disable --now midscene-3d-daily.timer`；已提交批次的状态仍可在工作台查看。
