# 执行回放入口 Implementation Plan

**Goal:** 已结束试跑与我的测试直接查看已保存的 Midscene 时间轴回放。
**Architecture:** 私有接口先验证批次归属和应用权限，只解析报告中的 JSON 数据块与内嵌图片；用固定SDK模板重新构建，绝不执行上传HTML自带脚本。前端按需读取，在无同源权限的sandbox iframe弹窗播放，关闭/离页/身份改变后销毁。不增加轮询、不启动手机、不生成正式报告。
**Tech Stack:** Python HTMLParser / 固定 Midscene 1.13.0模板 / 原生JS dialog iframe。

- [x] 后端：新增 ui_report_replay.py 数据提取器与私有服务接口；测试恶意脚本/属性不进入重建文档、非图片URL拒绝、无数据明确报错、跨用户/应用权限拒绝。
- [x] 前端：共享 UiExecutionReplay 弹窗，在 UiBatchView 的已结束结果添加“查看执行回放”；草稿和我的测试都接入。多附件按执行记录选择；loading、错误重试、关闭与离页销毁。
- [x] 验证：Python相关回归、Node弹窗/入口回归、主链语法/后端/前端静态检查、独立审查。使用本次两端原报告验证时间轴/播放/截图解码，并验证sandbox隔离。
- [x] 部署：提交推送，恢复服务器终端后部署；精确health和线上两入口复验，更新CODEX_STATE。

## 约束
保留静态附件下载策略；历史数据不重写。固定模板来自本机已安装@midscene/core 1.13.0包，包含许可。只提取midscene_web_dump/midscene_test_run_dump JSON与midscene-image内嵌栅格图。所有数据块按JSON重新序列化，转义小于号防止script终止。不直接返回原始可执行HTML。
