# Feishu Integration Plan

飞书后续应作为平台事件中心，而不是零散 webhook。

## 目标

让测试同事可以在飞书里收到清晰、可操作的通知：

- AI 生成完成：查看生成分析、下载脑图、打开 YAML。
- 资料不足：查看待确认项，一键采纳或补充。
- Sonic 套件完成：查看汇总报告、Sonic 报告、失败用例。
- Midscene 单条失败：查看失败原因、报告截图、修复建议、重试入口。
- 报告清理完成：查看释放空间和保留策略。

## 事件模型

建议后端统一抽象为事件：

```json
{
  "event": "ai_generation_completed",
  "title": "基础打印-耗材确认",
  "level": "success|warning|error",
  "module": "AI测试",
  "case_set_id": "cs_xxx",
  "links": [],
  "summary": {},
  "actions": []
}
```

## 配置建议

放在 `/opt/midscene.env`：

```bash
export FEISHU_NOTIFY_ENABLED='1'
export FEISHU_DEFAULT_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/...'
export FEISHU_AI_GENERATION_WEBHOOK=''
export FEISHU_SONIC_SUITE_WEBHOOK=''
export FEISHU_MIDSCENE_FAILURE_WEBHOOK=''
```

敏感配置只放服务器环境文件，不写入部署包。

## 后端实现建议

第一阶段可以继续放在 `midscene-upload.py`：

- `emit_platform_event(event)`
- `send_feishu_card(event)`
- `feishu_card_for_generation(event)`
- `feishu_card_for_sonic_suite(event)`
- `feishu_card_for_midscene_failure(event)`

等稳定后再拆：

```text
integrations/
  feishu.py
  events.py
```

## 页面入口

配置页建议新增：

- 飞书连接状态
- 测试发送
- 事件开关
- 默认机器人和分场景机器人
- 最近 20 条通知日志

## 风险点

- webhook 泄露风险：不在前端展示完整 token。
- 重复通知：事件必须有幂等 key，例如 `sonic_result_3_693`。
- 通知过载：失败单条可聚合，套件完成只发一条汇总。
- 外网失败：保留本地通知日志，允许手动重发。
- 权限边界：飞书只做通知和轻量确认，不直接执行高风险操作。

