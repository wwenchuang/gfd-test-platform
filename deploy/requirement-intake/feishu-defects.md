# 需求冒烟自动登记飞书项目缺陷

运行需求冒烟的工作台服务需要读取以下环境变量。需求同步和缺陷登记可以使用同一飞书项目插件身份。插件代表的用户需要目标空间读取工作项和创建缺陷的权限，发起测试的工作台账户需要应用范围内的 `ui.view`、`ui.execute` 和 `platform.notify`。

| 环境变量 | 填写内容 |
| --- | --- |
| `FEISHU_PROJECT_PLUGIN_ID` | 飞书项目插件 ID |
| `FEISHU_PROJECT_PLUGIN_SECRET` | 插件密钥，放入服务的受保护环境文件 |
| `FEISHU_PROJECT_USER_KEY` | 飞书项目执行身份的 user key |
| `MIDSCENE_FEISHU_DEFECT_CONFIG` | 应用和缺陷规则 JSON 文件的绝对路径 |

复制 [配置示例](./feishu-defects.example.json)，把目标空间、缺陷类型和用于保存证据的可写文本字段换成项目实际值。若项目模板有其他必填字段，可在同一应用配置对象填写数值 `template_id` 和 `field_value_pairs` 数组。数组元素使用飞书项目的 `field_key` 和 `field_value`，选项字段填写空间中的选项 ID。

每条规则从已发布用例和一次可核对的 Runner 失败记录取得 `case_id`、`case_revision`、`step_id` 以及 stderr 的完整诊断行。选择能单独表明验收偏差的诊断，例如同一笔计算的预期金额与实际金额。诊断应适合发送到该缺陷空间。规则里的标题和预期会随证据一起提交。发布新用例版本时更新规则的修订和步骤编号。工作台收到正常结束信号，核对同一失败步骤和精确诊断后才提交。脚本失效、模型服务超时、设备离线或证据不足的记录留在报告中。

需求收件回执的每个批次中可查看 `defect_review`，其中 `investigation` 关联原有只读失败调查。`submitted` 携带飞书返回的 `issue_id`。`needs_evidence` 表示需要核对报告，`configuration_required` 表示需要补配置。`rejected` 记录上游错误码，修正必填项或权限后后台重试。`receipt_unknown` 表示建单结果暂时未知，后台按完整跟踪标题查询原单。查询到唯一匹配的原单后会自动回填 ID。飞书中找不到原单时保留该状态，避免重复创建。密钥和原始日志不进入缺陷内容。

接口和字段以飞书项目[创建工作项文档](https://project.feishu.cn/b/helpcenter/1p8d7djs/6c8et3fg)及[官方 SDK](https://github.com/larksuite/project-oapi-sdk-golang)为准。
