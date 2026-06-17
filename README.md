# Midscene Task Platform

这是 Sonic + Task + 千问 + Midscene 的独立工程目录。

以后默认只修改这个目录：

```bash
/Users/wenchuang/Documents/Codex/midscene-task-platform
```

旧的对话目录只作为历史备份，不再作为日常开发入口。

## 目录结构

```text
midscene-task-platform/
  task_server/                       # 后端服务：任务、AI 生成、Sonic、报告、Figma、脑图
  midscene-upload.py                 # 兼容启动入口，实际服务入口为 python -m task_server
  task-manager.html                  # 前端页面：Task 管理平台
  ai_skills/                         # AI skills：需求解析、场景设计、自动化筛选、视觉校准、覆盖审查
  deploy/                            # 服务器部署、systemd、Docker 页面同步、打包脚本
  docs/                              # 平台设计、维护说明、后续飞书扩展规划
  tests/                             # 本地测试
  server-tasks/                      # 少量本地 YAML 样例
  server-tasks-all/                  # 现有 YAML 基线参考样例
  sonic-midscene-task-runner.groovy  # Sonic 桥接脚本
  windows-midscene-runner.py         # Windows Midscene runner
  mac-midscene-runner.py             # Mac Midscene runner
  dist/                              # 当前工程打出的部署包，只保留必要版本
```

## 日常开发规则

1. 后续所有改动都在本目录进行。
2. AI 生成策略优先沉淀到 `ai_skills/`，不要只写死在后端 prompt 里。
3. 平台通用设计、部署说明、飞书接入方案放到 `docs/`。
4. 服务器部署包从本目录打，不再从对话目录打。
5. 修改后至少跑：

```bash
cd /Users/wenchuang/Documents/Codex/midscene-task-platform
npm run test:static
npm run test:visual
```

## 打部署包

```bash
cd /Users/wenchuang/Documents/Codex/midscene-task-platform
bash deploy/package-server.sh
```

输出在：

```text
dist/midscene-task-platform-YYYYMMDD-HHMMSS.tar.gz
```

本地 `dist/` 默认只保留最近 5 个部署包，可以临时调整：

```bash
KEEP_PACKAGES=3 bash deploy/package-server.sh
```

## 服务器部署

上传部署包到服务器后：

```bash
cd /tmp
tar -xzf midscene-task-platform-YYYYMMDD-HHMMSS.tar.gz
cd midscene-task-platform
find . -name '._*' -delete
bash deploy/install-server.sh
chmod 600 /opt/midscene.env
systemctl restart midscene-task
systemctl status midscene-task --no-pager -l
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

部署成功后建议清理服务器旧包：

```bash
cd /tmp
KEEP=3 bash /opt/midscene-task-platform/deploy/cleanup-server-packages.sh /tmp
```

如果 8088 页面走 Docker 容器，`install-server.sh` 会自动同步
`task-manager.html` 到 `sonic-server-272-midscene-reports-1` 容器里。

## 平台边界

- Task 平台：资产、任务、生成记录、YAML、脑图、报告、页面知识库。
- 千问：需求解析、场景设计、自动化筛选、视觉校准、覆盖审查、修复建议。
- Midscene：真实 UI 自动化执行和报告。
- Sonic：稳定测试套、基线回归、设备调度和结果入口。
- 飞书：后续作为通知、审批/确认、任务入口和报告分发平台接入。

## 后续接飞书原则

飞书能力不要散落在页面和 Sonic 脚本里，建议后续统一收敛为：

- 后端：统一收敛到 `task_server/services/feishu_service.py` 与 `task_server/router.py` 的接口层。
- 配置：`/opt/midscene.env` 中只保存 webhook、app_id、secret、审批模板等配置。
- 页面：配置页只做连接状态、测试发送、通知策略开关。
- 流程：AI 生成完成、人工确认待处理、Sonic 套件完成、Midscene 失败、报告清理结果都走统一事件。
