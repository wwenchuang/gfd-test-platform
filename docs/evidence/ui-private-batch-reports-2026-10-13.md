# UI 批次私有报告底座（2026-10-13，未部署）

本次交付是 T5 后端/Runner 上传与权限底座。报告中文业务内容和版式仍由主任务按用户最新反馈完善；不以当前技术明细样本作为报告可用性验收通过。

## 存储与访问

- 报告与证据保存在 `TASK_UI_EXECUTION_DB` 指定的私有 SQLite。默认与身份库同目录，名为 `ui-executions.sqlite3`；不写 `REPORT_DIR`、任务根目录或公共 `/reports`。新增表保存报告快照、当前版本指针、原始证据字节、尝试-证据清单和临时分片。
- 来源只包含批次冻结用例、明确尝试、设备/版本快照与该任务认证证据，不查询同名文件的最近历史结果。稳定结果摘要决定 `report_revision`，同一摘要复用版本；当前指针与历史版本独立，清除上传错误时可重新指向已有正确版本。
- 所有报告 JSON、摘要、下载和图片/HTML 附件通过当前登录主体、批次归属和当前应用范围授权。管理员仍须有应用范围；新的 UI job 不进入旧任务列表或旧任务操作路径。历史报告访问策略保持原状，不补造归属。
- 原始 HTML 私有保留，对外只提供停用脚本/外链/跳转的静态安全预览并明确提示局限。导出 HTML 有内嵌 CSP，Blob/离线打开仍生效；主页面应继续使用无脚本 sandbox。原始 Midscene 交互页面不作为可用预览承诺。
- Markdown/HTML/DOCX 从同一快照生成。Word 复用现有 `render_report_docx`，真实 ZIP/Word XML。作者缺少可信冻结来源时显示“未记录”，不把执行者冒充作者；累计历史 Bug 未记录，不作为本批阻断结论。

## HTTP 接口

- `GET /api/ui-executions/{batch}/report[?revision=...]` → `{ok, report:{ui_batch_id, report_revision, status, error?, snapshot}}`。状态为 pending/ready/failed；pending 可以保留上版 snapshot，界面须按状态提示未完成。
- `POST /api/ui-executions/{batch}/report` → 202、同一 report 包装；只请求补生成，不派发设备、不重跑。执行未结算返回 409。
- `GET /api/ui-executions/{batch}/report/download?format=html|md|docx[&revision=...]` → 二进制，正确 MIME、RFC 5987 中文文件名、扩展名与 private/no-store。
- `GET /api/ui-executions/{batch}/summary` → `{ok,status,report_revision,summary}`；未就绪摘要为空。
- `GET /api/ui-executions/{batch}/artifacts/{artifact_id}` → PNG/JPEG 或静态安全 HTML；同样鉴权，不能直接以无认证链接加载。前端须使用当前会话 fetch 后生成 Blob。
- `snapshot` 包括 app_id/app_name、可信执行者、创建/开始/结束时间、selection、counts、summary、attempts（用例版本/元数据/步骤/结果/设备版本）、artifacts 清单。

## Runner 与后台行为

- 直接 `/report` 上传增加 `x-ui-job-id`、`x-ui-claim-token`、`x-ui-runner-id`、`x-ui-device-id`；分片/finish JSON 携带 job_id/claim/runner/device。服务验证已领取任务绑定，私有路径返回 `/api/ui-executions/.../artifacts/...`。
- 两种 Runner 的立即上传、分片 fallback、后台上传和重启 outbox 均携带明确任务身份。新批次不预留 `/reports/...`；缺失绑定的新批次文件名拒绝上传。inline report_html、截图、Runner 和 Sonic 旧结果回调均拦入私有链路；客户端任意 report_url 不会成为证据链接。
- Runner 必须上报 `ui_private_reports=1`，否则新批次预检/派发阻断并提示升级。旧任务继续原协议。
- 复用已有 5 秒执行 reconciler，每次最多启动一个后台报告构建；DOCX 子进程 30 秒超时后终止，失败需补生成。未确认停止不能生成终态报告。
- 同任务新分片首片替换被中断的暂存；成功 HTML 上传清除该任务分片。按 created_ns 索引每 tick 最多清理 16 个超过 24 小时的分片，保留活跃上传，不执行 VACUUM。

## 本地验证与发布边界

专项测试用系统 Python。该解释器缺少仓库已经声明的 python-docx，故仅在 `/tmp/codex-ui-simple-20261013/report-test-deps` 安装已有依赖范围并以 PYTHONPATH 加载；没有改生产依赖或读取凭据。

```bash
PYTHONPATH=/tmp/codex-ui-simple-20261013/report-test-deps python3 -m pytest -q tests/test_ui_batch_report_service.py tests/test_ui_batch_report_access.py tests/test_ui_report_runner_upload.py
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py mac-midscene-runner.py windows-midscene-runner.py
python3 tests/backend_static_checks.py
git diff --check
```

专项 35 项通过；独立审查复核上传重试回收、过期清理上限/活跃保留、错误消除后版本指针、当前 owner/app 权限。宽范围回归和实际命令输出见本次任务 `/tmp/codex-ui-simple-20261013/task-5-report.md`。

只检查了仓库 `deploy/nginx-midscene-task.conf` 与 `deploy/nginx-static-cache.conf`：`/api/` 已反代到后端；静态目录仍可能服务历史 `/reports`。没有访问实际服务器配置，没有修改 Nginx，因此没有 Nginx 回滚操作。上线前仍需主任务核对运行中反代和挂载目录，确认私有 SQLite 目录不在任何静态 alias/root 内。

发布须保持新入口关闭，主服务器升级后确认能力与私有路径，再更新并重启目标 Runner。主服务器常规命令：`cd /opt/midscene-task-platform-src && bash deploy/update-main-server.sh`。Mac 等待任务结束、停止旧进程后，使用既有 `~/Applications/MidsceneIOS/start-preview.command`；Windows 同样等待任务终态后重启现有 Runner 启动方式（脚本为 `windows-midscene-runner.py`），保留既有配置。不要同时启动第二个 Runner，不涉及设备重选。

回退优先关闭 `UI_SIMPLE_FLOW_ENABLED` 并保留 T5 后端接收未完成报告；不要将仍可能上传的私有批次 Runner 对接回旧公开上传服务。必须回退代码时先确认所有领取任务停止、回传/分片队列已清空，并对私有 SQLite 做一致备份；不可把该库或证据迁回公共 reports。此轮没有执行发布、回退、设备操作或飞书发送。
