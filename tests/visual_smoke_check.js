const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const HTML = path.join(ROOT, 'task-manager.html');
const ARTIFACTS = path.join(__dirname, 'artifacts');

function json(res, body) {
  const payload = JSON.stringify(body);
  res.writeHead(200, {
    'content-type': 'application/json; charset=utf-8',
    'access-control-allow-origin': '*',
  });
  res.end(payload);
}

function serve() {
  let fileReadCount = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/' || url.pathname === '/task-manager.html') {
      res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
      res.end(fs.readFileSync(HTML));
      return;
    }
    if (url.pathname.startsWith('/css/') || url.pathname.startsWith('/js/')) {
      const topDir = url.pathname.startsWith('/css/') ? 'css' : 'js';
      const rel = url.pathname.slice(`/${topDir}/`.length);
      const filePath = path.resolve(ROOT, topDir, rel);
      const fileRoot = path.resolve(ROOT, topDir);
      if (!filePath.startsWith(fileRoot) || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
        res.writeHead(404, {'content-type': 'text/plain; charset=utf-8'});
        res.end(`${topDir} file not found`);
        return;
      }
      res.writeHead(200, {
        'content-type': topDir === 'css' ? 'text/css; charset=utf-8' : 'application/javascript; charset=utf-8'
      });
      res.end(fs.readFileSync(filePath));
      return;
    }
    if (url.pathname.startsWith('/assets/')) {
      const rel = url.pathname.slice('/assets/'.length);
      const assetPath = path.resolve(ROOT, 'assets', rel);
      const assetRoot = path.resolve(ROOT, 'assets');
      if (!assetPath.startsWith(assetRoot) || !fs.existsSync(assetPath) || !fs.statSync(assetPath).isFile()) {
        res.writeHead(404, {'content-type': 'text/plain; charset=utf-8'});
        res.end('asset not found');
        return;
      }
      const contentType = assetPath.endsWith('.png') ? 'image/png' : assetPath.endsWith('.jpg') || assetPath.endsWith('.jpeg') ? 'image/jpeg' : 'application/octet-stream';
      res.writeHead(200, {'content-type': contentType});
      res.end(fs.readFileSync(assetPath));
      return;
    }
    if (url.pathname === '/api/modules') {
      json(res, {
        'AI测试': ['AI建模.yaml', '耗材确认弹窗.yaml'],
        '3D打印基线': ['十二生肖印章打印.yaml'],
      });
      return;
    }
    if (url.pathname === '/api/task-apps') {
      json(res, {apps: [{name: '智小白3D', package: 'com.kfb.model'}]});
      return;
    }
    if (url.pathname === '/api/apps') {
      json(res, {ok: true, apps: [{name: '智小白3D APP', package: 'com.kfb.model'}]});
      return;
    }
    if (url.pathname === '/api/models') {
      json(res, {ok: true, models: [
        {id: 'qwen3.6-plus', name: 'qwen3.6-plus', group: 'Qwen', default: true},
        {id: 'gpt-5-mini', name: 'gpt-5-mini', group: 'Highway'}
      ]});
      return;
    }
    if (url.pathname === '/api/task-meta') {
      json(res, {meta: {}});
      return;
    }
    if (url.pathname === '/api/sonic/cases') {
      json(res, {cases: []});
      return;
    }
    if (url.pathname === '/api/sonic/status') {
      json(res, {ok: true, synced: false, cases: [], summary: {synced: 0, total: 0}});
      return;
    }
    if (url.pathname === '/api/repair-drafts') {
      json(res, {ok: true, drafts: []});
      return;
    }
    if (url.pathname === '/api/baseline/page-refs') {
      json(res, {ok: true, refs: []});
      return;
    }
    if (url.pathname === '/api/yaml-stats') {
      json(res, {
        ok: true,
        stats: {
          'AI测试': {
            'AI建模.yaml': {loaded: true, total: 8, p0: 1, p1: 3, p2: 4, p3: 0, smoke: 1},
            '耗材确认弹窗.yaml': {loaded: true, total: 12, p0: 0, p1: 6, p2: 5, p3: 1, smoke: 2},
          },
          '3D打印基线': {
            '十二生肖印章打印.yaml': {loaded: true, total: 1, p0: 1, p1: 0, p2: 0, p3: 0, smoke: 1},
          },
        },
      });
      return;
    }
    if (url.pathname === '/api/file' && req.method === 'GET') {
      fileReadCount += 1;
      const file = url.searchParams.get('file') || '调试用例.yaml';
      const name = file.replace(/\.ya?ml$/i, '');
      const yaml = [
        'android:',
        '  tasks:',
        `    - name: "${name}主流程验证"`,
        '      flow:',
        '        - aiAssert: "页面加载正常，核心入口可见"',
        `    - name: "${name}异常提示验证"`,
        '      flow:',
        '        - aiAssert: "页面无空白、无网络错误、无异常弹窗"',
        '',
      ].join('\n');
      res.writeHead(200, {'content-type': 'text/plain; charset=utf-8'});
      res.end(yaml);
      return;
    }
    if (url.pathname === '/api/jobs') {
      json(res, {jobs: [
        {
          job_id: 'job-debug-running',
          module: 'AI测试',
          file: 'AI建模.yaml',
          status: 'running',
          run_mode: 'test',
          target_task_name: 'AI建模主流程验证',
          progress: 35,
          created_at: '2026-06-18 09:00:00',
          started_at: '2026-06-18 09:00:10',
          current_task_name: 'AI建模主流程验证',
          target_runner_id: 'win-runner-01',
          device_id: 'UQG0220513008845',
        },
        {
          job_id: 'job-baseline-failed',
          module: '3D打印基线',
          file: '十二生肖印章打印.yaml',
          status: 'failed',
          run_mode: 'baseline',
          target_task_name: '十二生肖印章打印主流程验证',
          progress: 100,
          created_at: '2026-06-18 08:10:00',
          finished_at: '2026-06-18 08:12:00',
          error: '断言失败',
          failure_review: {category: 'unknown', reason: '待复核', manual_confirmed: false},
        }
      ], background_jobs: [
        {
          job_id: 'gen_1781253319281_00004',
          status: 'failed',
          type: 'repair',
          title: 'AI修复',
          error: 'UNKNOWN',
          created_at: '2026-06-18 08:20:00',
        }
      ]});
      return;
    }
    if (url.pathname === '/api/runners') {
      json(res, {devices: [
        {
          runner_id: 'win-runner-01',
          runner_online: true,
          device_id: 'UQG0220513008845',
          status: 'online',
          label: 'OPPO PHM110',
          brand: 'OPPO',
          model: 'PHM110',
          android_version: '15',
          resolution: '1080×2412',
          installed_apps: [
            {package: 'com.kfb.model', installed: true, version_name: '1.16.0', version_code: 38}
          ],
        }
      ]});
      return;
    }
    if (url.pathname === '/api/health') {
      json(res, {ok: true});
      return;
    }
    if (url.pathname === '/api/auth/login' && req.method === 'POST') {
      json(res, {ok: true, user: 'admin', token: 'visual-smoke-token'});
      return;
    }
    if (url.pathname === '/api/auth/me') {
      if (req.headers.authorization !== 'Bearer visual-smoke-token') {
        res.writeHead(401, {'content-type': 'application/json; charset=utf-8'});
        res.end(JSON.stringify({ok: false, error: 'Unauthorized'}));
        return;
      }
      json(res, {ok: true, user: 'admin'});
      return;
    }
    if (url.pathname === '/api/auth/logout' && req.method === 'POST') {
      json(res, {ok: true});
      return;
    }
    if (url.pathname === '/ai-gateway/ai/providers/test' && req.method === 'POST') {
      json(res, {success: true, providerId: 'qwen_plus', provider: '千问 Qwen Plus', model: 'qwen-plus', output: 'gateway ok'});
      return;
    }
    if (url.pathname === '/ai-gateway/ai/providers') {
      json(res, {
        success: true,
        providers: [
          {id: 'highway_gpt5_mini', name: 'Highway GPT-5 Mini', type: 'openai_compatible', model: 'gpt-5-mini', configured: true, temperatureLocked: true, fixedTemperature: 1},
          {id: 'qwen_plus', name: '千问 Qwen Plus', type: 'openai_compatible', model: 'qwen-plus', configured: true, temperatureLocked: false}
        ]
      });
      return;
    }
    if (url.pathname === '/ai-gateway/ai/model-router' && req.method === 'GET') {
      json(res, {
        success: true,
        router: {
          generate_case: 'qwen_plus',
          generate_yaml: 'qwen_plus',
          analyze_failure: 'qwen_plus',
          optimize_yaml: 'qwen_plus',
          agent_plan: 'qwen_plus',
          generate_bug: 'qwen_plus'
        }
      });
      return;
    }
    if (url.pathname === '/ai-gateway/ai/model-router' && req.method === 'POST') {
      json(res, {success: true, router: {generate_case: 'qwen_plus', generate_yaml: 'qwen_plus', analyze_failure: 'qwen_plus', optimize_yaml: 'qwen_plus', agent_plan: 'qwen_plus', generate_bug: 'qwen_plus'}});
      return;
    }
    if (url.pathname === '/ai-gateway/ai/analyze-failure' && req.method === 'POST') {
      json(res, {success: true, analysis: {failureType: 'SCRIPT_ISSUE', conclusion: 'mock failure analysis'}});
      return;
    }
    if (url.pathname === '/ai-gateway/ai/optimize-yaml' && req.method === 'POST') {
      json(res, {success: true, yaml: 'android:\\n  tasks:\\n    - name: mock repair\\n      flow:\\n        - sleep: 1000', validation: {valid: true, errors: []}});
      return;
    }
    if (url.pathname === '/api/agent-runs') {
      json(res, {ok: true, runs: []});
      return;
    }
    if (url.pathname === '/api/agent-runs/preview' && req.method === 'POST') {
      json(res, {
        ok: true,
        plan: {
          mode: 'AUTO_SAFE',
          appName: '智小白3D APP',
          platform: 'android',
          scope: 'auto',
          steps: [
            '1. 理解目标和输入资料',
            '2. 整理 Figma、需求文档和截图',
            '3. 生成并校验 YAML',
            '4. 交给 Runner 执行并刷新状态'
          ],
        },
      });
      return;
    }
    if (url.pathname === '/api/agent-runs/start' && req.method === 'POST') {
      let body = '';
      req.on('data', chunk => { body += chunk; });
      req.on('end', () => {
        json(res, {
          ok: true,
          run: {
            runId: 'agent-test-001',
            status: 'WAIT_CONFIRM_RUN',
            currentStep: 'WAIT_CONFIRM_RUN',
            retryCount: 0,
            updatedAt: '2026-06-05T15:30:00.000Z',
            options: {
              goal: '关节龙打印流程回归',
              mode: 'SEMI_AUTO',
              effectiveMode: 'SEMI_AUTO',
            },
            steps: [
              {state: 'PLAN', success: true, tool: 'agentPlan', durationMs: 6},
              {state: 'PREPARE_SOURCE', success: true, tool: 'prepare_source', durationMs: 8, summary: '已整理 manual 输入来源'},
              {state: 'MATCH_CASES', success: true, tool: 'list_cases', durationMs: 15},
              {state: 'GENERATE_YAML', success: true, tool: 'generateYaml', durationMs: 20},
              {state: 'VALIDATE_YAML', success: true, tool: 'validateYaml', durationMs: 4},
            ],
            confirmations: [{type: 'confirm_before_run'}],
            artifacts: {
              caseDraft: '关节龙打印流程回归测试用例',
              yamlDraft: 'android:\\n  tasks:\\n    - name: 关节龙打印流程回归\\n      flow:\\n        - sleep: 1000',
              validation: {valid: true, errors: []},
            },
          },
        });
      });
      return;
    }
    const jobActionMatch = url.pathname.match(/^\/api\/jobs\/([^/]+)\/(cancel|retry|review)$/);
    if (jobActionMatch && req.method === 'POST') {
      const action = jobActionMatch[2];
      json(res, {
        ok: true,
        job: {
          job_id: jobActionMatch[1],
          status: action === 'cancel' ? 'cancelled' : 'pending',
          failure_review: action === 'review' ? {manual_confirmed: true, category: 'unknown'} : {},
        }
      });
      return;
    }
    if (url.pathname === '/api/agent-runs/agent-test-001') {
      json(res, {
        ok: true,
        run: {
          runId: 'agent-test-001',
          status: 'WAIT_CONFIRM_RUN',
          currentStep: 'WAIT_CONFIRM_RUN',
          retryCount: 0,
          updatedAt: '2026-06-05T15:30:00.000Z',
          options: {goal: '关节龙打印流程回归', mode: 'SEMI_AUTO', effectiveMode: 'SEMI_AUTO'},
          steps: [
            {state: 'PLAN', success: true, tool: 'agentPlan', durationMs: 6},
            {state: 'PREPARE_SOURCE', success: true, tool: 'prepare_source', durationMs: 8, summary: '已整理 manual 输入来源'},
            {state: 'MATCH_CASES', success: true, tool: 'list_cases', durationMs: 15},
            {state: 'GENERATE_YAML', success: true, tool: 'generateYaml', durationMs: 20},
            {state: 'VALIDATE_YAML', success: true, tool: 'validateYaml', durationMs: 4},
          ],
          confirmations: [{type: 'confirm_before_run'}],
          artifacts: {
            yamlDraft: 'android:\\n  tasks:\\n    - name: 关节龙打印流程回归\\n      flow:\\n        - sleep: 1000',
            validation: {valid: true, errors: []},
          },
        },
      });
      return;
    }
    res.writeHead(404, {'content-type': 'text/plain; charset=utf-8'});
    res.end('not found');
  });
  return new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolve({server, url: `http://127.0.0.1:${address.port}/task-manager.html`, getFileReadCount: () => fileReadCount});
    });
  });
}

async function visibleText(page, selector) {
  return (await page.locator(selector).innerText()).trim();
}

async function anyVisible(locator) {
  const count = await locator.count();
  for (let i = 0; i < count; i += 1) {
    if (await locator.nth(i).isVisible()) return true;
  }
  return false;
}

(async () => {
  fs.mkdirSync(ARTIFACTS, {recursive: true});
  const {server, url, getFileReadCount} = await serve();
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    const apiFailures = [];
    page.on('pageerror', err => errors.push(err.message));
    page.on('response', response => {
      const responseUrl = response.url();
      if (responseUrl.includes('/api/') && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${responseUrl}`);
      }
    });
    await page.route('https://fonts.googleapis.com/**', route => {
      route.fulfill({status: 200, contentType: 'text/css', body: ''});
    });
    await page.route('https://fonts.gstatic.com/**', route => route.abort());
    await page.goto(url, {waitUntil: 'domcontentloaded'});
    await page.screenshot({path: path.join(ARTIFACTS, 'login.png'), fullPage: true});

    const title = await page.title();
    if (title !== '功夫豆测试平台') throw new Error(`unexpected title: ${title}`);
    if (await page.locator('text=Midscene Task 管理平台').count()) throw new Error('old product title is visible');
    if (!await page.locator('.login-logo .brand-mark').isVisible()) throw new Error('login brand mark is not visible');
    const loginIconLoaded = await page.locator('.login-logo .brand-mark-img').evaluate(img => img.complete && img.naturalWidth > 0);
    if (!loginIconLoaded) throw new Error('login brand image did not load');
    if (!/功夫豆测试平台/.test(await visibleText(page, '.login-logo'))) throw new Error('login brand title is missing');

    await page.fill('#username', 'admin');
    await page.fill('#password', 'sonic2026');
    await page.click('button:has-text("登 录")');
    await page.waitForSelector('#app', {state: 'visible'});
    await page.waitForSelector('text=全自动 Agent 工作台');
    await page.screenshot({path: path.join(ARTIFACTS, 'dashboard.png'), fullPage: true});

    if (!await page.locator('.header-logo.brand-mark').isVisible()) throw new Error('header brand mark is not visible');
    const headerIconLoaded = await page.locator('.header-logo .brand-mark-img').evaluate(img => img.complete && img.naturalWidth > 0);
    if (!headerIconLoaded) throw new Error('header brand image did not load');
    if (!await page.locator('.nav-group-title', {hasText: 'Agent'}).isVisible()) throw new Error('sidebar Agent section title is missing');
    if (!await page.locator('.workflow-step', {hasText: 'Agent 工作台'}).isVisible()) throw new Error('Agent workbench sidebar entry is missing');
    if (!await page.locator('.workflow-step .workflow-index', {hasText: '🚀'}).isVisible()) throw new Error('sidebar semantic icons are missing');
    if (!await page.locator('text=Agent 状态').isVisible()) throw new Error('Agent status panel is missing');
    if (!await anyVisible(page.locator('text=启动 Agent'))) throw new Error('primary Agent action is missing');
    if (await page.locator('text=演示模式').count()) throw new Error('page incorrectly entered demo mode');
    if (getFileReadCount() !== 0) throw new Error(`dashboard should not read full YAML files during stats warmup, got ${getFileReadCount()}`);

    await page.click('.workflow-step:has-text("运行记录")');
    await page.waitForSelector('text=Agent 运行记录');
    if (await page.locator('#agent-goal').isVisible()) throw new Error('Agent history page should not leave the workbench form visible');
    await page.click('.workflow-step:has-text("待我确认")');
    await page.waitForSelector('text=人工确认中心');
    if (await page.locator('#agent-goal').isVisible()) throw new Error('Agent confirmation page should not leave the workbench form visible');
    await page.click('.workflow-step:has-text("Agent 工作台")');
    await page.waitForSelector('#agent-goal');

    const heroBox = await page.locator('.agent-hero').boundingBox();
    if (!heroBox || heroBox.height < 90 || heroBox.height > 260 || heroBox.width < 700) throw new Error(`dashboard hero layout is suspicious: ${JSON.stringify(heroBox)}`);
    const commandBox = await page.locator('.agent-primary-card').boundingBox();
    const jobsBox = await page.locator('.jobs-panel').boundingBox();
    if (!commandBox || commandBox.width < 700) throw new Error('dashboard command area is too narrow and may render vertical text');
    if (!jobsBox || jobsBox.width > 430) throw new Error(`jobs panel is too wide: ${jobsBox && jobsBox.width}`);

    await page.click('.workflow-step:has-text("用例资产")');
    await page.waitForSelector('text=YAML 文件');
    await page.waitForSelector('.assets-table');
    if (await page.locator('.jobs-panel').isVisible()) throw new Error('assets page should hide the right Agent/status panel');
    if (!await page.locator('.assets-table thead input[type="checkbox"]').isVisible()) throw new Error('assets table select-all checkbox is missing');
    for (const label of ['重命名', '移动', '删除']) {
      if (!await anyVisible(page.locator('.assets-table button', {hasText: label}))) throw new Error(`assets row action is missing: ${label}`);
    }
    await page.screenshot({path: path.join(ARTIFACTS, 'assets.png'), fullPage: true});
    const assetsBox = await page.locator('.assets-browser').boundingBox();
    if (!assetsBox || assetsBox.width < 900) throw new Error(`assets workspace is too narrow: ${assetsBox && assetsBox.width}`);

    await page.click('.workflow-step[data-workflow="execute"]');
    await page.waitForSelector('text=调试执行');
    await page.waitForSelector('text=选择要调试的 YAML');
    await page.waitForSelector('.execution-yaml-table');
    if (!await anyVisible(page.locator('.execution-yaml-table button', {hasText: '单条调试'}))) throw new Error('execution debug table is missing single-task action');
    if (!await anyVisible(page.locator('.execution-yaml-table button', {hasText: '整文件执行'}))) throw new Error('execution debug table is missing full-file action');
    await page.waitForSelector('text=Runner 进度');
    if (!await page.locator('.jobs-panel').isVisible()) throw new Error('execution page should show Runner progress panel');
    if (await page.locator('text=Agent 状态').isVisible()) throw new Error('execution page should not show Agent status title');
    if (!await page.locator('.jobs-panel', {hasText: '调试执行'}).isVisible()) throw new Error('execution panel must label debug-run jobs');
    if (!await page.locator('.jobs-panel', {hasText: '基线回归'}).isVisible()) throw new Error('execution panel must label baseline jobs');
    if (await page.locator('.jobs-panel', {hasText: 'gen_1781253319281_00004'}).count()) throw new Error('execution runner panel must not show generated background jobs as pending runner tasks');
    if (!await anyVisible(page.locator('.jobs-panel button', {hasText: '取消任务'}))) throw new Error('current runner task must expose cancel action');
    if (!await anyVisible(page.locator('.jobs-panel button', {hasText: '重跑'}))) throw new Error('pending failure card must expose retry action');
    if (!await anyVisible(page.locator('.jobs-panel button', {hasText: '已处理'}))) throw new Error('pending failure card must expose handled action');
    await page.screenshot({path: path.join(ARTIFACTS, 'execution.png'), fullPage: true});
    await page.locator('.execution-yaml-table button', {hasText: '单条调试'}).first().click();
    await page.waitForSelector('#modal-run-task.show');
    await page.waitForSelector('text=选择用例（可多选）');
    const runTaskOptions = await page.locator('#run-task-name option').count();
    if (runTaskOptions < 2) throw new Error(`single-task modal did not parse YAML tasks, options=${runTaskOptions}`);
    await page.click('#modal-run-task .btn-cancel');

    await page.click('.workflow-step:has-text("Agent 工作台")');
    await page.waitForSelector('#agent-goal');
    await page.waitForFunction(() => {
      const select = document.querySelector('#agent-model');
      return select && select.innerText.includes('Highway GPT-5 Mini') && select.innerText.includes('千问 Qwen Plus');
    });
    if (!await page.locator('.agent-start-layout').isVisible()) throw new Error('Agent grouped start layout is missing');
    if (await page.locator('.agent-form-section').count() < 2) throw new Error('Agent form sections are missing');
    if (!await page.locator('.agent-start-button').isVisible()) throw new Error('Agent start button is missing after layout change');
    await page.waitForFunction(() => {
      const hint = document.querySelector('#agent-runner-device-hint');
      return hint && hint.innerText.includes('win-runner-01') && hint.innerText.includes('com.kfb.model 1.16.0 (38)');
    });
    await page.selectOption('#agent-source-type', 'figma');
    await page.waitForSelector('text=Figma 链接在下方');
    await page.fill('#agent-source-figma-url', 'https://www.figma.com/design/mx4x043OQjy1IYB1OfUdxw/%E6%99%BA%E5%B0%8F%E7%99%BDAPP?node-id=4458-1905&p=f&t=visual-smoke-0');
    const figmaWrapOk = await page.locator('#agent-source-figma-url').evaluate(el => {
      const style = window.getComputedStyle(el);
      return style.overflowWrap === 'anywhere' || style.wordBreak === 'break-all';
    });
    if (!figmaWrapOk) throw new Error('Long Figma URL input must wrap instead of hiding information');
    let previewDialogText = '';
    page.once('dialog', async dialog => {
      previewDialogText = dialog.message();
      await dialog.accept();
    });
    await page.click('button:has-text("预览计划")');
    await page.waitForTimeout(300);
    if (!/全自动 Agent执行计划/.test(previewDialogText) || !/执行设备/.test(previewDialogText)) throw new Error(`Agent preview button did not return a readable plan: ${previewDialogText}`);
    await page.click('button:has-text("安装/更新 App")');
    await page.waitForSelector('text=安装包更新');
    await page.waitForSelector('#apk-install-device');
    if (!await page.locator('text=执行前设备检查').isVisible()) throw new Error('Agent install shortcut did not open the install preflight panel');
    await page.click('.workflow-step:has-text("Agent 工作台")');
    await page.waitForSelector('#agent-goal');
    const agentModelOptions = await page.locator('#agent-model').innerText();
    if (!/自动（按模型策略：千问 Qwen Plus）/.test(agentModelOptions)) throw new Error(`Agent model auto option did not use AI Gateway router: ${agentModelOptions}`);
    if (!await page.locator('text=还没有选择运行记录').isVisible()) throw new Error('Agent workbench should open in new-run mode');
    if (await page.locator('text=Agent 步骤时间线').isVisible()) throw new Error('Agent workbench should not show the previous run timeline by default');
    await page.fill('#agent-goal', '关节龙打印流程回归');
    await page.click('#agent-start-btn');
    await page.waitForSelector('text=Agent 步骤时间线');
    await page.waitForSelector('text=人工复核');
    await page.screenshot({path: path.join(ARTIFACTS, 'agent.png'), fullPage: true});
    if (!await page.locator('text=Agent 状态').isVisible()) throw new Error('Agent status center is missing');
    if (!await anyVisible(page.locator('text=确认执行'))) throw new Error('Agent wait-confirm action is missing');
    if (!await page.locator('button:has-text("下载 YAML")').isVisible()) throw new Error('Agent YAML download button is missing');
    let dialogText = '';
    page.once('dialog', async dialog => {
      dialogText = dialog.message();
      await dialog.accept();
    });
    await page.locator('details[data-nav-group="settings"]').evaluate(el => { el.open = true; });
    await page.click('.workflow-step[data-workflow="config"]');
    await page.waitForSelector('text=当前模型策略');
    await page.click('button:has-text("一键应用推荐策略")');
    await page.waitForSelector('text=当前模型策略');
    await page.click('summary:has-text("高级设置")');
    await page.waitForSelector('text=自定义路由');
    await page.click('button:has-text("保存模型策略")');
    await page.waitForSelector('text=当前模型策略');
    await page.click('button:has-text("测试当前策略")');
    await page.waitForTimeout(300);
    if (!/gateway ok/.test(dialogText)) throw new Error('AI Gateway test dialog did not include gateway ok');
    if (apiFailures.length) throw new Error(`api failures: ${apiFailures.join(' | ')}`);
    if (errors.length) throw new Error(`page errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({
      ok: true,
      url,
      screenshots: [
        path.join(ARTIFACTS, 'login.png'),
        path.join(ARTIFACTS, 'dashboard.png'),
        path.join(ARTIFACTS, 'execution.png'),
        path.join(ARTIFACTS, 'agent.png'),
      ],
    }, null, 2));
  } finally {
    await browser.close();
    server.close();
  }
})().catch(err => {
  console.error(err && err.stack || err);
  process.exit(1);
});
