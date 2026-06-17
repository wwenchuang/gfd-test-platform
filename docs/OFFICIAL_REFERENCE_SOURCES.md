# Official Reference Sources

涉及 Sonic、Midscene、Agent 执行闭环、模型调用和平台接口时，先读这里登记的来源，再做实现或修改。这里是项目固定参考入口，后续对话不需要再临时提醒。

## Sonic

- 官网文档：https://soniccloudorg.github.io/
  - 用途：部署、使用、开发文档入口。
  - 备注：Sonic 官网定位为开源云真机平台，覆盖 0 编码 UI 自动化、分布式设备集群、可视化报表等能力。
- Gitee 组织：https://gitee.com/sonic-cloud
  - 用途：源码仓库入口。
  - 重点仓库：
    - `sonic-server`
    - `sonic-agent`
    - `sonic-client-web`
    - `sonic-driver-core`
- TesterHome 项目页：https://www.testerhome.com/opensource_projects/sonic
  - 用途：社区经验、问题排查、部署实践、版本背景。
- TesterHome Sonic 搜索：https://www.testerhome.com/search?q=Sonic
  - 用途：查找具体问题和社区实践。
- 项目内核验记录：`docs/sonic-official-api-notes.md`
  - 用途：保存本平台对 Sonic 官方源码的接口核验结论。
  - 当前结论：Sonic 官方后端暴露测试套执行 `/testSuites/runSuite`，未暴露单个 testCase 的 run REST；Task 平台单条/多条调试统一走 Windows/Mac Runner，不再创建 Sonic 临时测试套。

## Midscene

- 官网：https://midscenejs.com/
  - 用途：总入口。
- YAML script runner：https://midscenejs.com/yaml-script-runner
  - 用途：YAML 脚本结构、CLI 执行、报告、`.env` 模型配置。
- YAML workflow：https://midscenejs.com/yaml-script
  - 用途：YAML flow item、脚本结构和工作流约束。
- Android introduction：https://midscenejs.com/android-introduction
  - 用途：Android 自动化能力边界。
- Android getting started：https://midscenejs.com/android-getting-started
  - 用途：ADB、设备连接、模型准备、Android 脚本入门。
- Android integration：https://midscenejs.com/integrate-with-android
  - 用途：Android SDK/ADB 集成方式。
- Model configuration：https://midscenejs.com/model-configuration
  - 用途：模型环境变量、兼容接口和多模型配置。

## 使用规则

1. 改 Sonic 桥接、Sonic 回调、Sonic 执行套件、Sonic 结果解析前，必须先查 Sonic 官网/Gitee/TesterHome。
2. 改 Midscene YAML、flowItem、Runner、报告、Android 执行前，必须先查 Midscene 官方文档。
3. 如果官方文档和本地经验冲突，先按官方文档确认，再把本地差异写入 `ai_skills/references/`。
4. 不要把社区文章当成唯一依据；社区经验只能作为补充。
5. 每次新增外部依赖或接口约定，补充到本文件。

## 本次确认记录

- 2026-06-05：确认 Sonic 官网/Gitee/TesterHome 入口，用于后续 Sonic 执行、回调、报告和 Agent 工具接入参考。
- 2026-06-05：确认 Midscene YAML runner、Android、模型配置入口，用于后续 YAML 生成、校验、Runner 和模型错误排查参考。
