# 正式报告仅在发版申请时生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 普通 UI 测试只保存真实执行结果和证据；开始测试时明确勾选“用于发版”，执行结束后才自动生成正式发版报告，并可选择发送飞书。

**Architecture:** 批次增加可信报告申请状态，正式报告 worker 只处理已申请批次。独立 evidence metadata API 保持截图可用；复用私有图片下载、预览、报告渲染与通知权限。开始页默认不用于发版，勾选后显示可选通知，完成后自动生成。

用户本轮已明确选择开始测试时勾选“用于发版”，取代最初的结束后人工申请方案。

**Tech Stack:** Python / SQLite / 原生 JavaScript / 现有 Node jsdom 与 pytest / Chrome Playwright CLI。

## Global Constraints

- 本次仅新 UI 简化入口批次链路，旧显式创建报告流程维持现状。
- 不改执行判定、Runner能力门禁、YAML、手机选择或实际测试版本；不把失败/取消转换为通过。
- 报告冻结执行人、测试版本、时间、用例和场景；生成申请者单独记录。
- 私有结果/证据/报告按本人或当前授权管理员校验；通知必须有 platform.notify 并用服务端渠道。
- 已生成历史报告、精确 revision 链接保持可读；无报告的历史 pending 不在迁移后自动生成。
- 普通测试不启动 DOCX 子进程，不写三格式报告，不新增截图采集或AI请求。
- 不改原用户工作区；仅现有隔离 worktree，小范围修改 router。

## Task 1: 基线搜索先完成

- [x] published 范围在计数/分页/分组前生效，用例库可显示未发布草稿。
- [x] 保留300ms筛选，按钮/Enter立即搜索，IME与旧请求/旧分页隔离。
- [x] 136 Node / 80 Python / 后端63 / 前端84通过；Chrome候选390px验证。
- [x] 提交并推送4dc3f81。
- [ ] 线上部署与真实目录复验。

## Task 2: 明确申请与后端生成门禁

Files: ui_execution_store.py, ui_execution_lifecycle.py, ui_batch_report_service.py, ui_batch_notification_service.py, ui_batch_dispatch.py, router.py 及对应 tests。

- [x] 用失败回归锁定：创建、结果回传、截图上传、迟到 report_ready、停止恢复、重跑均不自动申请报告。
- [x] 迁移显式申请字段；新批次 report_status / delivery_status 为 not_requested。
- [x] 所有 pending 重置仅限已申请批次；worker/_build双重检查，不让直接入口绕过。
- [x] GET /ui-executions/{id}/evidence 返回私有证据元数据，不读取BLOB或渲染。
- [x] create/preflight 接收 release_report:{enabled:boolean}，默认false；创建时冻结可信申请者/时间/渠道，release=false且notify=true拒绝。
- [x] 现有 POST /{id}/report 保留显式重试能力；仅settled且停止已确认，可信申请者/时间/渠道冻结。
- [x] 重复提交幂等，生成失败可重试，ready不重新发消息，不创建执行Job。
- [x] 历史快照与下载链接可读；普通结果摘要独立可用。
- [x] 后端专项回归、权限/迁移/晚到事件/通知回归与静态检查。

## Task 3: 结果与发版报告分开展示

Files: js/ui-test-home.js, js/ui-batch-view.js, js/ui-batch-report.js, js/ui-simple-entry.js, task-manager.html, css/ui-simple.css 及对应 Node tests。

- [x] 开始测试默认只执行，不自动通知；我的测试列出所有本人的执行。
- [x] not_requested在执行settled后停止执行明细轮询。
- [x] 普通结果可见实际状态/版本/证据，图片仍点击加载，复用预览弹窗。
- [x] 开始页增加默认不勾选的“用于发版”，勾选后提示完成自动生成报告；显示可选通知且默认关闭。
- [x] 去掉“用于发版”勾选时关闭通知；提交幂等，权限撤销/离页/换账号丢弃旧结果。
- [x] 已生成历史报告和精确版本通知链接保持可用；失败重试不执行测试。
- [x] Node回归与全站CSS Chrome桌面/390px验证。

## Task 4: 集成发布与验收

- [x] 独立审查两侧契约、迁移/回滚、晚到事件与通知幂等，修复实际问题。
- [x] 必跑Python编译/后端静态/前端静态/git diff --check；相关Node/Python全集。
- [x] 在隔离DB跑真实HTTP+worker：普通测试完结和晚到图片无正式文件，勾选发版后结束才生成，重复请求无新增Job/通知。
- [x] 复用真实JPG检查普通证据预览/下载、不勾选/勾选发版、历史报告，并恢复浏览器尺寸。
- [ ] 更新CODEX_STATE；提交/推送；堡垒机登录可用后标准部署。
- [ ] health精确SHA、页面资源与Chrome真实目录/历史报告核对；真实执行环境不可用时明确边界，不以本地fixture充当实机通过。
