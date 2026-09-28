# Runner 实时步骤进度 Implementation Plan

**Goal:** 运行任务置顶，用例和当前步骤状态实时可见，不制造进度。
**Architecture:** 原 Midscene CLI/YAML 执行不变；Runner 的只读观察器采集 ScriptPlayer 公共 taskStatusList，按状态变化生成快照，通过现有 2 秒节流上报。服务端有界保存快照，前端复用现有刷新，不增加轮询端点。
**Tech Stack:** Python Runner、Node preload、原生 JS/CSS。

## 设计与约束

用户授权自行设计，采用“总体用例 + 当前步骤”两层展示，优于只有百分比（看不到位置）或默认展开所有步骤（侧栏过长）。运行中优先于排队，之后历史和待处理；当前步骤蓝色，完成绿色对勾，失败红叉，未执行灰色。展开列表保留刷新前状态。

- 不修改业务 YAML、不拆分执行、不新增 Midscene action、不增加 AI 调用。
- 观察器错误只降低进度能力；原进程、返回值和异常保持。未知/旧 Runner 明示缺少步骤，不用日志关键词猜成功。
- 中途失败、取消、超时后的剩余步骤保持未执行。终态拒绝迟到进度回包覆盖。
- Windows 独立 Python 部署方式保持；观察脚本由 Python 写入任务私有目录，Node 仅为本次子进程加载。
- 最多 100 个用例、每用例 200 步和总计 1000 步，超出明确提示，文本长度有界；不上传输入内容、shell 命令全文。

## 实施任务

- [x] 1. 先写排序、节点状态、步骤快照验证与终态防覆盖失败测试。
- [x] 2. js/app.js/css/app.css：运行任务置顶；紧凑总体条、步骤节点与当前步骤文本；保留展开状态、长文本换行、窄栏不溢出。
- [x] 3. windows-midscene-runner.py：只读观察 ScriptPlayer，版本/能力不匹配降级；消费结构化帧、末尾队列不漏帧、节流进度、不阻塞执行。正常启动/完成/失败不伪造全部通过。
- [x] 4. services/runner_progress.py 与 router.py：快照白名单、尺寸边界和终态防迟到覆盖；沿用已有鉴权和锁。
- [x] 5. 跑 Node 观察器模拟真实 API 行为、Python 回传和前端交互、必跑静态/编译；本地真实浏览器核对宽/窄侧栏和实时状态变化。
- [x] 6. 独立复审、更新 CODEX_STATE 与部署说明，提交。生产真机验收仅在 Windows Runner 已更新后进行，不能用模拟结果替代。

## 验证命令

`node --test tests/runner_live_progress_check.js tests/runner_progress_observer_check.js tests/runner_result_display_check.js`
`python -m pytest -q tests/test_runner_live_progress.py`
`python3 -m py_compile windows-midscene-runner.py task_server/router.py task_server/services/runner_progress.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py`
`python3 tests/backend_static_checks.py && python3 tests/frontend_static_checks.py && git diff --check`

源码参考：@midscene/cli@1.13.0 printer.ts/yaml-batch-executor.ts、@midscene/core@1.13.0 yaml/player.ts；官方 https://midscenejs.com/yaml-script-runner 。实际 npm 包已下载只读核查。非 TTY 默认仅开始/结束输出，不可依赖 console 持续步骤日志。

真实内核三轮补充：`python tests/runner_real_core_progress_check.py <安装了 @midscene/core 和 @midscene/cli 1.13.x 的 npm 目录>`。不连接手机或模型，不能替代实机验收。
