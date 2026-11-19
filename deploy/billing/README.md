# 每日 AI 账单
在部署服务器上安装阿里云 CLI，配置具有 bss:DescribeInstanceBill 权限的只读身份。将本目录安装到 /opt/midscene-task-platform/deploy/billing，并在 /etc/midscene-billing.env 中填写两行配置：

    BILLING_CLI_PROFILE=账单只读配置名称
    BILLING_PIP_CODE=账单中已核实的百炼产品代码

单个产品口径覆盖该账号下该产品，不按模型 Key 过滤。服务保留北京日期最近七天的人民币应付金额，空记录标记为暂无明细。同步失败保留上次成功快照。平台管理员可查看，账单快照不要放进网页静态目录。

    install -m 644 /opt/midscene-task-platform/deploy/billing/midscene-billing.{service,timer} /etc/systemd/system/
    systemctl daemon-reload
    systemctl start midscene-billing.service
    systemctl enable --now midscene-billing.timer

管理员从平台侧栏「AI 费用账单」核对同步时间、日期和金额，并选同一产品同一天在百炼账单中对账。
