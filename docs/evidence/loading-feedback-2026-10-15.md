# 2026-10-15 统一等待反馈

## 已实现
- 录屏上传按钮立即忙碌；queued/decoding/analyzing 真实状态映射排队、读取视频、AI识别步骤，附待核对节点。没有虚构百分比。失败、取消、完成移除动画；自动导入和历史手动打开文案分开。
- 公共 LoadingManager 保留现有 AI 修复、执行、Agent 入口，安全显示文字、嵌套操作引用计数、恢复原禁用与aria状态。
- 中文草稿生成/保存/校验局部忙碌；基线搜索、首次设备/测试列表、截图刷新、报告下载与复制反馈。静默后台刷新保留已有内容。
- CSS-only动画，支持 reduced-motion，无新增轮询/AI调用/依赖。

## 盘点与边界
| 区域 | 本批处理 |
| --- | --- |
| 上传视频/中文草稿/开始测试/我的测试 | 新增实际请求状态反馈 |
| Agent、AI修复、旧执行入口 | 复用现有公共 LoadingManager，统一可访问性和恢复行为 |
| API资产读取、API报告刷新 | 已有 is-spinning 和操作状态，本批保持 |
| 模型配置、管理/历史 | 已有首次加载、错误与请求去重，本批保留，专项回归覆盖 |
| Runner执行步骤 | 保留原真实步骤进度，不用loading替代执行结果 |

## 验证
- 244项主链Node（loading、entry、draft、workflow、batch report/view）通过。另11项配置/管理loading回归通过；前端84/后端63静态通过。
- 独立审查两项minor已修复：历史任务自动打开文案、IME/离页主动取消后busy清理。新IME回归通过。
- Chrome隔离页面挂载真实视频组件、模拟后端状态，390x844和1440x900无横向溢出；取消后progress节点0；reduced-motion计算animationName=none。截图 /tmp/codex-loading-acceptance/。
- 未修改生产数据或操作手机；本批仍待部署线上验收。原视频Android/iOS Runner试跑缺口继续保留。
