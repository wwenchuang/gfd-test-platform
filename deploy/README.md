# Server Deployment

This deployment package publishes the complete Task platform:

- `midscene-upload.py`: Python Task service and API.
- `task-manager.html`: web console served by the Python service at `/` and `/task-manager.html`.
- `sonic-midscene-task-runner.groovy`: Sonic bridge script returned by `/api/sonic/bridge-groovy`.
- `/www/html/task-manager.html`: optional Nginx static web copy for port 80 access.
- `ai_skills/`: Qwen skill prompts, schemas, and eval fixtures.
- `/opt/midscene-*`: runtime data directories for tasks, reports, assets, generated cases, jobs, learning records, and page knowledge.

## Recommended Layout

```text
/opt/midscene-task-platform/
  midscene-upload.py
  task-manager.html
  sonic-midscene-task-runner.groovy
  ai_skills/
/www/html/
  task-manager.html
/opt/midscene.env
/opt/midscene-tasks/
/opt/midscene-reports/
/opt/midscene-learning/
/opt/midscene-assets/
/opt/midscene-cases/
/opt/midscene-generate-jobs/
/opt/midscene-knowledge/
```

## Existing `/opt` Flat Layout

If the server already has files directly under `/opt`, keep the runtime
directories and move only the application code into `/opt/midscene-task-platform`.

Existing runtime directories can stay where they are:

```text
/opt/midscene-assets/
/opt/midscene-cases/
/opt/midscene-generate-jobs/
/opt/midscene-knowledge/
/opt/midscene-learning/
/opt/midscene-reports/
/opt/midscene-tasks/
```

The server must also have these application files after deployment:

```text
/opt/midscene-task-platform/midscene-upload.py
/opt/midscene-task-platform/task-manager.html
/opt/midscene-task-platform/sonic-midscene-task-runner.groovy
/opt/sonic-midscene-task-runner.groovy
/opt/midscene-task-platform/ai_skills/
/www/html/task-manager.html
```

`midscene-upload.py` serves `task-manager.html` from the same directory as the
Python file. Skills are loaded from `AI_SKILLS_DIR`, which should point to
`/opt/midscene-task-platform/ai_skills`.

If your current web root is `/www/html`, keep using it for the visible page.
The install script copies `task-manager.html` there when the directory exists.
Because the page calls `API_BASE = '/api'`, the same host and port that serves
`task-manager.html` must also serve `/api/`. If Python serves the page directly
on `8088`, no Nginx proxy is needed. If Nginx serves `/www/html` on `8088`,
Nginx must proxy `/api/` to the Python service.

## Install

Run from the project root on the server:

```bash
sudo bash deploy/install-server.sh
sudo vim /opt/midscene.env
sudo systemctl restart midscene-task
```

If you are packaging from a local development machine:

```bash
bash deploy/package-server.sh
scp dist/midscene-task-platform-*.tar.gz <user>@<server>:/tmp/
ssh <user>@<server>
cd /tmp
tar -xzf midscene-task-platform-*.tar.gz
cd midscene-task-platform
sudo bash deploy/install-server.sh
sudo vim /opt/midscene.env
sudo systemctl restart midscene-task
```

For one-command release from a local development machine:

```bash
bash deploy/release-server.sh root@<server>
```

The release script runs local static and visual checks, builds a fresh package,
uploads it to `/tmp`, installs it on the server, restarts `midscene-task`, and
checks `/api/health`. Useful options:

```bash
SSH_PORT=2222 bash deploy/release-server.sh root@<server>
RUN_TESTS=0 bash deploy/release-server.sh root@<server>
HEALTH_URLS="http://127.0.0.1:8088/api/health http://127.0.0.1:8091/api/health" bash deploy/release-server.sh root@<server>
```

At minimum, configure:

- `MIDSCENE_RUNNER_TOKEN`
- `DASHSCOPE_API_KEY`
- Sonic connection variables if Sonic integration is required.
- `APP_PACKAGE` for the default tested app.

If AI Gateway is deployed separately on the same server, configure:

```bash
export HIGHWAY_API_KEY='your_highway_api_key'
export QWEN_API_KEY='your_dashscope_api_key'
```

AI Gateway models are configured in `/opt/ai-gateway/config/providers.json`;
capability routing is configured in `/opt/ai-gateway/config/model-router.json`.
API keys stay in `.env` or systemd environment only.

Report retention defaults are appended automatically during install when missing:

```bash
export MIDSCENE_AI_CHAT_TIMEOUT_SECONDS='480'
export MIDSCENE_AI_CHAT_RETRY_COUNT='1'
export MIDSCENE_COVERAGE_MODEL_WHEN_LOCAL_OK='0'
export MIDSCENE_REPORT_RETENTION_DAYS='14'
export MIDSCENE_REPORT_RETENTION_MIN_KEEP='200'
export MIDSCENE_REPORT_CLEANUP_INTERVAL_SECONDS='86400'
export MIDSCENE_REPORT_CLEANUP_ON_STARTUP='1'
export MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_SECONDS='1800'
export MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_PER_JOB_SECONDS='900'
export MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_MAX_SECONDS='7200'
export SONIC_TASK_CALLBACK_GRACE_SECONDS='180'
```

AI generation uses `MIDSCENE_AI_CHAT_TIMEOUT_SECONDS` for each Qwen/DashScope
request and retries `MIDSCENE_AI_CHAT_RETRY_COUNT` times when the model read
operation times out. Failed generation jobs keep a structured `error_detail`
with the failed stage and handling suggestion for the Task page.
`MIDSCENE_COVERAGE_MODEL_WHEN_LOCAL_OK=0` skips the extra coverage-auditor
model call when local coverage audit already passes and the generated case count
reaches the minimum target. Set it to `1` only when you want stricter but slower
model review every time.

The Task service uses the same cleanup policy for the page action
`配置 -> Midscene 报告清理` and for the background cleanup thread. The policy only
removes old local Midscene HTML reports and stale upload chunks; Sonic original
reports are not deleted.

Agent Runner 调试模式会先创建本地 Runner job，再等待报告回传。等待窗口默认
单条 1800 秒，多条按每条额外 900 秒递增，最多 7200 秒；可用
`MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_SECONDS`、
`MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_PER_JOB_SECONDS` 和
`MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_MAX_SECONDS` 调整。执行期间 Agent 时间线会持续写入
job 完成/失败/运行中数量，避免页面看起来卡在 RUN_SONIC。

Sonic 测试套完成后，如果 Sonic 原始结果已结束但 Task 平台仍缺少少量用例回调，
服务会在 `SONIC_TASK_CALLBACK_GRACE_SECONDS` 窗口内继续等待回调收齐后再发送唯一飞书汇总。
默认 180 秒，用来避免先收到一条“通过/告警”汇总、随后又因迟到回调再收到一条“失败”汇总。
如果等待窗口结束后 Sonic 原始报告明确为通过，且 Task 平台已收到的回调里没有失败或告警，
缺失的桥接回调只作为回传提示，不再把测试结论降级为告警。

## Verify

```bash
curl http://127.0.0.1:8088/api/health
curl "http://127.0.0.1:8088/api/reports/cleanup?dry_run=1"
curl -I http://127.0.0.1:8088/task-manager.html
sudo journalctl -u midscene-task -f
```

`/api/health` should include:

```json
{
  "paths": {
    "ai_skills": {
      "ready": true,
      "missing_prompts": [],
      "missing_schemas": []
    }
  }
}
```

## Nginx

Optional static web + API reverse proxy:

```bash
sudo cp deploy/nginx-midscene-task.conf /etc/nginx/conf.d/midscene-task.conf
sudo nginx -t
sudo systemctl reload nginx
```

Then open:

```text
http://<server-ip>:8088/task-manager.html
```

Check both static web and API proxy:

```bash
curl -I http://<server-ip>:8088/task-manager.html
curl http://<server-ip>:8088/api/health
curl http://127.0.0.1:8088/ai-gateway/health
curl http://127.0.0.1:8088/ai-gateway/ai/providers
curl http://127.0.0.1:8088/ai-gateway/ai/model-router
curl -X POST http://127.0.0.1:8088/ai-gateway/ai/providers/test \
  -H "Content-Type: application/json" \
  -d '{"providerId":"qwen_plus"}'
```

The browser page must call AI through the same-origin `/ai-gateway/` reverse
proxy. Do not put `http://127.0.0.1:8090` or the HighwayAPI URL into
`task-manager.html`; browser `127.0.0.1` is the user's computer, not the server.

If port `8088` is served by the Sonic reports Docker container, sync the page
inside the container after deployment. `install-server.sh` does this
automatically when it detects `sonic-server-272-midscene-reports-1`.

Manual fallback:

```bash
CONTAINER=sonic-server-272-midscene-reports-1 bash deploy/sync-docker-web.sh
curl -s http://127.0.0.1:8088/task-manager.html | wc -c
```

## Upgrade

After code, page, or skill changes:

```bash
sudo bash deploy/install-server.sh
sudo systemctl restart midscene-task
curl http://127.0.0.1:8088/api/health
```

The install script updates Python service files, `ai_skills`, the app copy of
`task-manager.html`, and the Sonic reports Docker web copy when that container
exists. Prompt/schema-only changes are read at runtime, but the install script
is still the safest way to keep the server copy consistent.

## Cleanup Old Packages

Deployment packages and temporary extraction directories can pile up on the
server. Keep only the newest three packages:

```bash
cd /tmp
KEEP=3 bash /opt/midscene-task-platform/deploy/cleanup-server-packages.sh /tmp
```

Preview without deleting:

```bash
DRY_RUN=1 KEEP=3 bash /opt/midscene-task-platform/deploy/cleanup-server-packages.sh /tmp
```

The cleanup script only removes:

- `midscene-task-platform-*.tar.gz`
- temporary `midscene-task-platform/` extraction directories outside `/opt`
- macOS resource files such as `._*` and `.DS_Store`

It does not remove `/opt/midscene-tasks`, `/opt/midscene-reports`, reports,
cases, learning data, or the running app directory.
