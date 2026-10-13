# P0 初始核对与页面缺陷修复

日期：2026-10-13。新方案尚未发布。

## 实时只读核对

- origin/main = bb7bc93；线上 health = d31cdf19dcd76f961ac551037d7a807817b0dc3a；最新 WDA 修复仅需本机 Runner 更新，二者差异符合此前发布边界。
- Windows win-runner-01 / PHM110 ecbfd645：在线、idle、preflight ready，CLI 1.13.1。
- Mac mac-ios-adouceshi / iPhone13 00008110-000150CC21C2801E：在线、idle、preflight ready，版本 wda-recovery-v1。第二台 iPhone 本次未见在线，不计通过。
- Chrome CUA 两次入口均 30 秒超时；没有据此声称页面/真机三轮验收完成。

## 确定性缺陷

1. renderDeviceOptions 每次用 innerHTML 重建所有 option；已选设备离线会丢掉原选择；无空闲手机时自动选机选项仍可选。新增四项回归先全失败，改为按设备键复用 option、更新状态、保留离线选择并禁用后通过。
2. jobErrorText 会把运行中 stdout/stderr 提取为失败原因，包含 false 等正常过程输出。新增运行中回归先失败，修复为 pending/running 不提前给失败归因，原始输出仍保留。真实失败仍显示错误。

## 检查

- pytest tests/test_ios_case_reuse.py：22 通过；unittest discover 原先收集 0 项，不计验证。
- Node 设备选项、设备卡片、结果显示：21 通过。
- 前端静态 84、JavaScript 语法、diff 检查通过。缓存键随修复升级。
- 未重跑真实手机业务；首次触控偶发未跳转根因仍未知，按原缺口保留。
