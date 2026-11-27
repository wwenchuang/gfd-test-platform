# 飞书项目需求接入

同步任务每五分钟读取指定空间已提测的工作项。插件需要该空间的工作项读权限，平台服务账号需要目标应用的 `ui.view` 和 `ui.execute`。同步按工作项类型和空间再次核对每一条返回记录。飞书未返回可读验收条件时，该条保留为同步错误。工作台首页展示已接入需求。批次提交、Runner 执行和缺陷创建分别显示各自结果。[飞书项目工作项筛选接口](https://project.feishu.cn/b/helpcenter/1p8d7djs/19pti4e2)

在服务端现有凭据管理中配置 `FEISHU_PROJECT_PLUGIN_ID`、`FEISHU_PROJECT_PLUGIN_SECRET` 和 `FEISHU_PROJECT_USER_KEY`。在 `/etc/midscene-feishu-requirements.env` 设置 `MIDSCENE_FEISHU_REQUIREMENT_CONFIG` 和 `MIDSCENE_REQUIREMENT_BINDINGS` 为两个服务端 JSON 文件的绝对路径。工作台进程需读取同一个 `MIDSCENE_REQUIREMENT_BINDINGS` 和同一套身份、批次数据库、任务目录及 `UI_SIMPLE_FLOW_ENABLED=1` 环境。用实际部署值替换以下示例。

源配置中的 `project_key` 使用飞书项目空间 ID。`search_group` 使用该空间标记「进入测试」的条件。`acceptance_field` 支持文本或字符串列表。只有租户启用且字段确实存在时才填写可选的 `scenario_field`、`revision_field`。省略 `revision_field` 时使用标题、验收条件和业务场景内容的摘要作为版本，重复轮询和无关字段变更共用同一批次。

```json
{
  "project_key": "example_project",
  "work_item_type_key": "story",
  "app_id": "com.kfb.model",
  "username": "qa-service",
  "user_id": "platform-account-id",
  "acceptance_field": "field_acceptance",
  "scenario_field": "field_scenario",
  "search_group": {
    "conjunction": "AND",
    "search_params": [{"param_key": "work_item_id", "operator": "HAS ANY OF", "value": [123456]}],
    "search_groups": []
  }
}
```

示例条件适合用一个已授权工作项试运行。正式启用时换成实际提测筛选条件。工作台管理的映射可以按场景复用，也可以把 `scenario` 替换为 `requirement_id` 和 `revision` 绑定单个版本。后者优先。通过首页手工录入的需求使用来源 `workbench` 及精确的需求版本映射。缺少映射的需求可在工作台查看，补齐后重新接收同一条需求即可继续。有现有需求设计权限的账号可将原需求标题、版本和验收条件保存为本人的设计材料，在「需求生成测试设计」中继续。保存材料不启动模型或 Runner。

```json
[
  {
    "source": "feishu-project:example_project",
    "app_id": "com.kfb.model",
    "scenario": "navigation",
    "runs": [{
      "execution_policy": "reviewed_non_printing",
      "cases": [{"case_id": "published-case-id", "revision": "sha256-of-reviewed-published-yaml"}],
      "target": {"platform":"ios","runner_id":"registered-runner-id","device_id":"registered-phone-id"}
    }]
  }
]
```

由维护者逐条审阅已发布用例的固定内容，确认不会下发真实打印任务后填写其 SHA-256。每个平台最多三条冒烟。Android 和 iOS 分别填写已验证的版本与手机。同步仅下发映射中列出的这些用例。设备忙、Runner 不支持或修订变化会记录阻断原因。已绑定的请求保存原版本和设备。新的基线快照使用新的需求版本。测试状态可在工作台对应批次查看。

按实际运行目录安装随附的 service 和 timer。配置部署本身不代表接入已验证。

```sh
sudo install -m 0644 deploy/requirement-intake/midscene-feishu-requirements.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start midscene-feishu-requirements.service
sudo journalctl -u midscene-feishu-requirements.service -n 20 --no-pager
sudo systemctl enable --now midscene-feishu-requirements.timer
```

单次启动会按上述范围入库并下发通过预检的冒烟。先用已授权的一个需求核对需求编号、原条款、手机、Runner 报告和飞书项目缺陷，再扩展筛选范围。服务日志只写状态数量及无法接入的工作项编号、原因码。

## 服务器填写入口

工作台服务与需求同步服务可以共用 root 所有、权限 0600 的 `/etc/midscene-feishu-requirements.env`。样例见 [环境文件](feishu-project.env.example)。使用服务管理器的 `EnvironmentFile` 指向此文件，保留工作台原有环境及批次存储配置。实际配置文件需允许服务用户读取。更新凭据后在空闲窗口重启工作台，先对指定的一条需求运行同步服务并核对结果，再启用定时器。
