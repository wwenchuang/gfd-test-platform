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
      res.writeHead(404, {'content-type': 'text/plain; charset=utf-8'});
      res.end('file missing in smoke test');
      return;
    }
    if (url.pathname === '/api/jobs') {
      json(res, {jobs: [], background_jobs: []});
      return;
    }
    if (url.pathname === '/api/runners') {
      json(res, {devices: []});
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
    page.on('pageerror', err => errors.push(err.message));
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
    await page.screenshot({path: path.join(ARTIFACTS, 'assets.png'), fullPage: true});
    const assetsBox = await page.locator('.assets-browser').boundingBox();
    if (!assetsBox || assetsBox.width < 900) throw new Error(`assets workspace is too narrow: ${assetsBox && assetsBox.width}`);

    await page.click('.workflow-step[data-workflow="execute"]');
    await page.waitForSelector('text=调试执行');
    await page.waitForSelector('text=Runner 进度');
    if (!await page.locator('.jobs-panel').isVisible()) throw new Error('execution page should show Runner progress panel');
    if (await page.locator('text=Agent 状态').isVisible()) throw new Error('execution page should not show Agent status title');

    await page.click('.workflow-step:has-text("Agent 工作台")');
    await page.waitForSelector('#agent-goal');
    await page.waitForFunction(() => {
      const select = document.querySelector('#agent-model');
      return select && select.innerText.includes('Highway GPT-5 Mini') && select.innerText.includes('千问 Qwen Plus');
    });
    const agentModelOptions = await page.locator('#agent-model').innerText();
    if (!/自动（按模型策略：千问 Qwen Plus）/.test(agentModelOptions)) throw new Error(`Agent model auto option did not use AI Gateway router: ${agentModelOptions}`);
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
    if (errors.length) throw new Error(`page errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({
      ok: true,
      url,
      screenshots: [
        path.join(ARTIFACTS, 'login.png'),
        path.join(ARTIFACTS, 'dashboard.png'),
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
