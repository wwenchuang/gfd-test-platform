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

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (error) {
        reject(error);
      }
    });
    req.on('error', reject);
  });
}

function serve() {
  let fileReadCount = 0;
  let apiAssetSyncPollCount = 0;
  let apiPlanGenerationPollCount = 0;
  let apiPlanGenerationRetried = false;
  let meterSelection = {project_id: 'project-interface', environment_id: 'env-qa'};
  const bindingRequestBodies = [];
  const authRequestBodies = [];
  const reportSourceQueries = [];
  let businessAuth = {
    configured: true,
    auth_type: 'bearer',
    header_name: 'Authorization',
    variable_name: 'API_BEARER_TOKEN',
    environment_id: 'env-qa',
    environment_name: 'QA 环境',
    scope: 'environment',
    reused: true,
    usage_count: 2,
    updated_at: '2026-07-22 09:38:00',
  };
  const apiAssetSync = () => ({
    sync_id: 'api-sync-visual-001',
    source_id: 'api-source-visual-001',
    trigger: 'manual',
    status: 'running',
    phase: apiAssetSyncPollCount ? 'analyze_impact' : 'diff_revision',
    poll_after_ms: 3000,
    created_at: '2026-07-22 09:20:00',
    started_at: '2026-07-22 09:20:01',
    finished_at: '',
    previous_revision_id: 'api-revision-visual-000',
    asset_id: 'api-asset-visual-001',
    revision_id: 'api-revision-visual-001',
    summary: {added: 3, changed: 2, removed: 1, unchanged: 586, affected_plans: 2},
    error: '',
    events: Array.from({length: 28}, (_, index) => ({
      at: `2026-07-22 09:${String(20 + Math.floor(index / 6)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}`,
      phase: index < 6 ? 'fetch_source' : index < 12 ? 'parse_document' : index < 20 ? 'diff_revision' : 'analyze_impact',
      message: `真实同步事件 ${index + 1}：已完成服务端脱敏并持久化状态`,
    })),
  });
  const meterExecution = () => ({
    execution_id: 'ms-execution-visual-001',
    plan_id: 'api-plan-visual-001',
    plan_name: '账号接口日常回归',
    status: 'running',
    current_phase: 'metersphere_run',
    created_at: '2026-07-22 09:40:00',
    started_at: '2026-07-22 09:40:02',
    updated_at: '2026-07-22 09:42:18',
    run_id: 'ms-run-visual-001',
    remote_status: 'running',
    report_status: 'waiting',
    duration_seconds: 136,
    poll_after_ms: 3000,
    stats: {total: 24, passed: 8, failed: 0},
    phases: [
      {id: 'push_cases', title: '推送用例', state: 'succeeded', summary: '24 条确认用例已推送', started_at: '2026-07-22 09:40:02', updated_at: '2026-07-22 09:40:08', duration_seconds: 6},
      {id: 'trigger_plan', title: '触发计划', state: 'succeeded', summary: 'MeterSphere 计划已触发', started_at: '2026-07-22 09:40:08', updated_at: '2026-07-22 09:40:12', duration_seconds: 4},
      {id: 'metersphere_run', title: 'MeterSphere 执行', state: 'running', summary: '8 / 24 条已完成', started_at: '2026-07-22 09:40:12', updated_at: '2026-07-22 09:42:18', duration_seconds: 126},
      {id: 'sync_report', title: '同步报告', state: 'waiting', summary: '等待远端执行终态', started_at: '', updated_at: '', duration_seconds: 0},
    ],
    events: [
      {
        event_id: 'event-push-001',
        timestamp: '2026-07-22 09:40:08',
        phase_id: 'push_cases',
        state: 'succeeded',
        summary: '确认用例推送完成',
        detail: {
          push_id: 'push-visual-001',
          counts: {total: 24, succeeded: 24, failed: 0},
          response_summary: Array.from({length: 28}, (_, index) => `批次 ${index + 1}：已接收并完成服务端脱敏`).join('\n'),
        },
      },
      {
        event_id: 'event-run-001',
        timestamp: '2026-07-22 09:42:18',
        phase_id: 'metersphere_run',
        state: 'running',
        summary: 'MeterSphere 正在执行',
        detail: {run_id: 'ms-run-visual-001', status: 'RUNNING', completed: 8, total: 24},
      },
    ],
  });
  const apiPlan = () => ({
    plan_id: 'api-plan-visual-ready',
    name: '积分兑换接口回归',
    status: 'draft',
    source: 'ai',
    source_id: 'api-source-visual-001',
    source_name: '3D 接口',
    revision_id: 'api-revision-visual-001',
    module_paths: ['家用业务/app接口/我的'],
    generation_id: 'api-generation-visual-001',
    ai_trace: {model: 'qwen3.8-plus', provider: 'qwen', batches: 1, completed_at: '2026-07-22 09:30:00'},
    execution_binding: {project_id: 'project-interface', project_name: '接口业务', environment_id: 'env-qa', environment_name: 'QA 环境', binding_fingerprint: 'binding-visual-001'},
    auth_binding: {...businessAuth},
    binding_state: {state: 'matched', current_fingerprint: 'binding-visual-001', planned_fingerprint: 'binding-visual-001'},
    endpoint_count: 2,
    case_count: 2,
    executable_case_count: 1,
    needs_review_case_count: 1,
    revision_state: {state: 'fresh', planned_revision_id: 'api-revision-visual-001', active_revision_id: 'api-revision-visual-001'},
    execution_readiness: {
      state: 'partial',
      executable_case_count: 1,
      needs_review_case_count: 1,
      can_confirm: true,
      can_execute: false,
      missing: ['request.body.productId'],
    },
    cases: [{
      contract_version: 'api_case_contract/v1',
      case_id: 'API-001-P',
      name: '查询积分成功',
      type: 'positive',
      priority: 'P0',
      request: {method: 'GET', path: '/points', path_params: {}, query: {}, headers: {}, body: {}, auth_ref: 'environment_default'},
      assertions: [{type: 'status', operator: 'in', expected: [200]}, {type: 'schema', schema_ref: 'response:2xx'}],
      readiness: {state: 'executable', missing: [], issues: []},
    }, {
      contract_version: 'api_case_contract/v1',
      case_id: 'API-002-P',
      name: '确认兑换',
      type: 'positive',
      priority: 'P0',
      request: {method: 'POST', path: '/exchange', path_params: {}, query: {}, headers: {}, body: {}, auth_ref: 'environment_default'},
      assertions: [{type: 'status', operator: 'in', expected: [200]}],
      readiness: {state: 'needs_review', missing: ['request.body.productId'], issues: []},
    }],
  });
  const apiPlanGeneration = (generationId = 'api-generation-visual-001') => {
    const late = generationId === 'api-generation-late';
    const completed = !apiPlanGenerationRetried && apiPlanGenerationPollCount >= 3;
    const afterRetry = apiPlanGenerationRetried && apiPlanGenerationPollCount >= 1;
    const status = late ? 'succeeded' : (afterRetry ? 'succeeded' : (completed ? 'partial' : (apiPlanGenerationPollCount ? 'running' : 'queued')));
    const batchStates = apiPlanGenerationRetried && !afterRetry
      ? ['succeeded', 'succeeded', 'queued']
      : status === 'queued'
      ? ['queued', 'queued', 'queued']
      : status === 'running' && apiPlanGenerationPollCount === 1
        ? ['running', 'queued', 'queued']
        : status === 'running'
          ? ['succeeded', 'running', 'queued']
          : status === 'partial'
            ? ['succeeded', 'succeeded', 'failed']
            : ['succeeded', 'succeeded', 'succeeded'];
    return {
      generation_id: generationId,
      source_id: late ? 'api-source-visual-001' : 'api-source-visual-001',
      asset_revision_id: 'api-revision-visual-001',
      module_paths: ['家用业务'],
      selected_endpoint_keys: Array.from({length: 25}, (_, index) => `route:GET /visual/${index + 1}`),
      scope_fingerprint: 'scope-visual-001',
      execution_binding_id: 'source-binding-visual-001',
      binding_fingerprint: 'binding-visual-001',
      auth_binding: {...businessAuth},
      status,
      batch_size: 12,
      batch_count: 3,
      completed_batches: status === 'succeeded' ? 3 : (status === 'partial' || apiPlanGenerationRetried ? 2 : Math.min(apiPlanGenerationPollCount, 1)),
      failed_batches: status === 'partial' ? 1 : 0,
      retry_count: apiPlanGenerationRetried ? 1 : 0,
      poll_after_ms: 20,
      created_at: '2026-07-22 09:28:00',
      started_at: '2026-07-22 09:28:01',
      updated_at: '2026-07-22 09:30:00',
      finished_at: ['partial', 'succeeded'].includes(status) ? '2026-07-22 09:30:00' : '',
      error: status === 'partial' ? '第 3 批模型响应超时' : '',
      batches: [12, 12, 1].map((endpointCount, index) => ({
        batch_index: index + 1,
        status: batchStates[index],
        endpoint_count: endpointCount,
        endpoint_ids: Array.from({length: endpointCount}, (_, endpointIndex) => `api-${index * 12 + endpointIndex + 1}`),
        plan_id: batchStates[index] === 'succeeded' ? `api-plan-real-00${index + 1}` : '',
        attempts: index === 2 && apiPlanGenerationRetried ? 2 : 1,
        started_at: batchStates[index] === 'queued' ? '' : `2026-07-22 09:${28 + index}:00`,
        finished_at: ['succeeded', 'failed'].includes(batchStates[index]) ? `2026-07-22 09:${28 + index}:20` : '',
        error: batchStates[index] === 'failed' ? '模型响应超时' : '',
      })),
      events: Array.from({length: 28}, (_, index) => ({
        event_id: `generation-event-${index + 1}`,
        timestamp: `2026-07-22 09:${String(28 + Math.floor(index / 10)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}`,
        phase: index < 8 ? 'prepare' : index < 22 ? 'generate_batch' : 'validate_plan',
        summary: late ? 'LATE RESPONSE MUST NOT RENDER' : `AI 批次真实事件 ${index + 1}`,
        detail: {generation_id: generationId, batch_index: Math.min(2, Math.floor(index / 10)), model: 'qwen3.8-plus'},
      })),
    };
  };
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
    if (url.pathname === '/api/cases/mindmaps') {
      json(res, {ok: true, mindmaps: [
        {
          case_set_id: 'agent-newest',
          title: '最新 AI 建模脑图',
          module: 'AI_Agent_草稿',
          yaml_file: 'AI建模.yaml',
          generated_at: '2026-06-24 10:00:00',
          scenario_count: 18,
          automation_case_count: 12,
          manual_case_count: 6,
          smoke_count: 2,
          priority_counts: {P0: 2, P1: 6, P2: 10},
          mindmap_exists: true,
          mindmap_downloadable: true,
          mindmap_size: 39120,
          mindmap_updated_at: '2026-06-25 15:29:48',
          mindmap_sort_ts: 1782372588,
        },
        {
          case_set_id: 'agent-old',
          title: '较早模型众测方案',
          module: 'AI_Agent_草稿',
          yaml_file: '模型众测.yaml',
          generated_at: '2026-06-23 14:40:05',
          scenario_count: 47,
          automation_case_count: 27,
          manual_case_count: 10,
          smoke_count: 1,
          priority_counts: {P0: 1, P1: 18, P2: 28},
          mindmap_exists: true,
          mindmap_downloadable: true,
          mindmap_size: 30210,
          mindmap_updated_at: '2026-06-23 15:16:03',
          mindmap_sort_ts: 1782198963,
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
    if (url.pathname === '/api/api-testing/sources' && req.method === 'GET') {
      json(res, {
        ok: true,
        sources: [{
          source_id: 'api-source-visual-001',
          source_type: 'apifox',
          name: '3D 接口',
          project_id: '5904970',
          branch_id: '',
          credential_configured: true,
          configured: true,
          sync_enabled: true,
          sync_interval_minutes: 60,
          last_success_at: '2026-07-22 08:20:00',
          last_sync_status: 'running',
          last_error: '',
          sync_scope: {mode: 'all', module_paths: []},
          module_catalog: [
            {path: '家用业务/app接口/我的', parent: '家用业务/app接口', depth: 3, endpoint_count: 12},
            {path: '家用业务/app接口/我的/收藏', parent: '家用业务/app接口/我的', depth: 4, endpoint_count: 6},
            {path: '家用业务/app接口/我的下载', parent: '家用业务/app接口', depth: 3, endpoint_count: 7},
            {path: '家用业务/app接口/我的下载备份', parent: '家用业务/app接口', depth: 3, endpoint_count: 3},
          ],
        }, {
          source_id: 'api-source-visual-002',
          source_type: 'apifox',
          name: '账户中心',
          project_id: '5904971',
          branch_id: '',
          credential_configured: true,
          configured: true,
          sync_enabled: true,
          sync_interval_minutes: 60,
          last_success_at: '2026-07-22 08:20:00',
          last_sync_status: 'no_change',
          last_error: '',
          sync_scope: {mode: 'selected', module_paths: ['账户/登录']},
          module_catalog: [{path: '账户/登录', parent: '账户', depth: 2, endpoint_count: 5}],
        }],
        syncs: [apiAssetSync()],
      });
      return;
    }
    if (url.pathname === '/api/api-testing/assets' && req.method === 'GET') {
      const sourceId = url.searchParams.get('source_id') || 'api-source-visual-001';
      const modulePaths = sourceId === 'api-source-visual-002'
        ? ['账户/登录']
        : ['家用业务/app接口/我的', '家用业务/app接口/我的/收藏', '家用业务/app接口/我的下载', '家用业务/app接口/我的下载备份'];
      const endpoints = Array.from({length: 25}, (_, index) => {
        const modulePath = modulePaths[index % modulePaths.length];
        return {
          endpoint_id: `${sourceId}-api-${index + 1}`,
          endpoint_key: `route:${index % 3 ? 'GET' : 'POST'} /visual/${index + 1}`,
          method: index % 3 ? 'GET' : 'POST',
          path: `/visual/${index + 1}`,
          module: modulePath,
          module_path: modulePath,
          name: `${modulePath} 接口 ${index + 1}`,
          required_fields: index % 2 ? [] : ['page'],
          schema_hash: `schema-${index + 1}`,
        };
      });
      const defaultRevisionId = sourceId === 'api-source-visual-002' ? 'api-revision-visual-002' : 'api-revision-visual-001';
      const revisionId = url.searchParams.get('snapshot_id') || defaultRevisionId;
      json(res, {
        ok: true,
        source_id: sourceId,
        assets: [{asset_id: `api-asset-${sourceId}`, name: sourceId === 'api-source-visual-002' ? '账户中心' : '3D 接口', active_revision_id: revisionId, endpoint_count: endpoints.length}],
        asset: {asset_id: `api-asset-${sourceId}`, name: sourceId === 'api-source-visual-002' ? '账户中心' : '3D 接口', active_revision_id: revisionId, schema_version: '3.0.1', endpoint_count: endpoints.length},
        revisions: sourceId === 'api-source-visual-001'
          ? [
            {revision_id: 'api-revision-visual-001', endpoint_count: endpoints.length, created_at: '2026-07-22 09:20:20'},
            {revision_id: 'api-revision-visual-000', endpoint_count: endpoints.length, created_at: '2026-07-22 08:20:00'},
          ]
          : [{revision_id: revisionId, endpoint_count: endpoints.length, created_at: '2026-07-22 09:20:20'}],
        snapshots: [{snapshot_id: revisionId, title: sourceId === 'api-source-visual-002' ? '账户中心' : '3D', version: '1.0.0', endpoint_count: endpoints.length}],
        snapshot: {snapshot_id: revisionId, revision_id: revisionId, title: sourceId === 'api-source-visual-002' ? '账户中心' : '3D', version: '1.0.0', openapi_version: '3.0.1', endpoints},
        module_summary: {total_modules: modulePaths.length, total_endpoints: endpoints.length, roots: []},
        endpoints,
      });
      return;
    }
    if (url.pathname === '/api/api-testing/plan-generations' && req.method === 'POST') {
      readJsonBody(req).then(body => {
        const endpointIds = body.endpoint_ids || [];
        if (body.source_id !== 'api-source-visual-001' || body.revision_id !== 'api-revision-visual-001' || endpointIds.length !== 25) {
          res.writeHead(400, {'content-type': 'application/json; charset=utf-8'});
          res.end(JSON.stringify({ok: false, error: `unexpected generation scope: ${JSON.stringify(body)}`}));
          return;
        }
        apiPlanGenerationPollCount = 0;
        apiPlanGenerationRetried = false;
        json(res, {ok: true, generation: apiPlanGeneration()});
      });
      return;
    }
    const planGenerationRetryMatch = url.pathname.match(/^\/api\/api-testing\/plan-generations\/([^/]+)\/retry$/);
    if (planGenerationRetryMatch && req.method === 'POST') {
      readJsonBody(req).then(body => {
        if (Object.prototype.hasOwnProperty.call(body, 'endpoint_ids') || Object.prototype.hasOwnProperty.call(body, 'module_paths')) {
          res.writeHead(400, {'content-type': 'application/json; charset=utf-8'});
          res.end(JSON.stringify({ok: false, error: 'retry must not resubmit successful batches'}));
          return;
        }
        apiPlanGenerationRetried = true;
        apiPlanGenerationPollCount = 0;
        json(res, {ok: true, generation: apiPlanGeneration(planGenerationRetryMatch[1])});
      });
      return;
    }
    const planGenerationMatch = url.pathname.match(/^\/api\/api-testing\/plan-generations\/([^/]+)$/);
    if (planGenerationMatch && req.method === 'GET') {
      const generationId = planGenerationMatch[1];
      const respond = () => {
        apiPlanGenerationPollCount += 1;
        json(res, {ok: true, generation: apiPlanGeneration(generationId)});
      };
      if (generationId === 'api-generation-late') setTimeout(respond, 180);
      else respond();
      return;
    }
    if (url.pathname === '/api/api-testing/plans' && req.method === 'GET') {
      const plan = apiPlan();
      const stalePlan = {
        ...plan,
        plan_id: 'api-plan-visual-stale',
        name: '旧版本登录接口回归',
        revision_state: {
          state: 'stale',
          planned_revision_id: 'api-revision-visual-000',
          active_revision_id: 'api-revision-visual-001',
          reason: '接口版本已变化，请重新生成计划',
          affected_case_ids: ['API-001-P'],
        },
        execution_readiness: {...plan.execution_readiness, can_confirm: false, can_execute: false},
        binding_state: {state: 'mismatched', current_fingerprint: 'binding-visual-002', planned_fingerprint: 'binding-visual-001'},
      };
      json(res, {ok: true, plans: [plan, stalePlan].map(item => ({
        plan_id: item.plan_id,
        name: item.name,
        status: item.status,
        source_id: item.source_id,
        source_name: item.source_name,
        revision_id: item.revision_id,
        module_paths: item.module_paths,
        generation_id: item.generation_id,
        ai_trace: item.ai_trace,
        execution_binding: item.execution_binding,
        auth_binding: item.auth_binding,
        binding_state: item.binding_state,
        case_count: item.case_count,
        executable_case_count: item.executable_case_count,
        needs_review_case_count: item.needs_review_case_count,
        execution_readiness: item.execution_readiness,
        revision_state: item.revision_state,
        created_at: '2026-07-22 09:30:00',
      }))});
      return;
    }
    if (url.pathname === '/api/api-testing/plans/api-plan-visual-ready' && req.method === 'GET') {
      json(res, {ok: true, plan: apiPlan()});
      return;
    }
    if (url.pathname === '/api/api-testing/plans/api-plan-visual-stale' && req.method === 'GET') {
      const plan = apiPlan();
      json(res, {
        ok: true,
        plan: {
          ...plan,
          plan_id: 'api-plan-visual-stale',
          name: '旧版本登录接口回归',
          revision_state: {
            state: 'stale',
            planned_revision_id: 'api-revision-visual-000',
            active_revision_id: 'api-revision-visual-001',
            reason: '接口版本已变化，请重新生成计划',
            affected_case_ids: ['API-001-P'],
          },
          execution_readiness: {...plan.execution_readiness, can_confirm: false, can_execute: false},
          binding_state: {state: 'mismatched', current_fingerprint: 'binding-visual-002', planned_fingerprint: 'binding-visual-001'},
        },
      });
      return;
    }
    if (url.pathname === '/api/api-testing/syncs/api-sync-visual-001' && req.method === 'GET') {
      apiAssetSyncPollCount += 1;
      json(res, {ok: true, sync: apiAssetSync()});
      return;
    }
    if (url.pathname === '/api/api-testing/sources/api-source-visual-001/sync' && req.method === 'POST') {
      json(res, {ok: true, sync: {...apiAssetSync(), created: true, conflict: false}});
      return;
    }
    if (url.pathname === '/api/api-testing/sources/api-source-visual-001/execution-binding' && req.method === 'GET') {
      const requestedProjectId = url.searchParams.get('project_id') || '';
      if (requestedProjectId) {
        json(res, {
          ok: true,
          source_id: 'api-source-visual-001',
          selected_project_id: requestedProjectId,
          projects: [
            {id: 'project-interface', name: '接口业务', enabled: true},
            {id: 'project-3d', name: '3D 业务', enabled: true},
          ],
          environments: requestedProjectId === 'project-3d'
            ? [{id: 'env-staging', name: '预发环境', project_id: 'project-3d', enabled: true}]
            : [{id: 'env-qa', name: 'QA 环境', project_id: 'project-interface', enabled: true}],
          version: 'v3.6.5-lts',
        });
        return;
      }
      json(res, {
        ok: true,
        source_id: 'api-source-visual-001',
        binding: {
          binding_id: 'source-binding-visual-001',
          source_id: 'api-source-visual-001',
          provider: 'metersphere',
          project_id: meterSelection.project_id,
          project_name: meterSelection.project_id === 'project-3d' ? '3D 业务' : '接口业务',
          environment_id: meterSelection.environment_id,
          environment_name: meterSelection.environment_id === 'env-staging' ? '预发环境' : 'QA 环境',
          config_fingerprint: 'binding-visual-001',
          auth_binding: {...businessAuth},
          updated_at: '2026-07-22 09:42:18',
        },
      });
      return;
    }
    if (url.pathname === '/api/api-testing/sources/api-source-visual-001/execution-binding' && req.method === 'POST') {
      readJsonBody(req).then(body => {
        bindingRequestBodies.push({...body});
        const requestedSelection = {project_id: body.project_id || '', environment_id: body.environment_id || ''};
        meterSelection = requestedSelection;
        if (businessAuth.environment_id !== requestedSelection.environment_id) {
          businessAuth = {
            configured: false,
            auth_type: 'bearer',
            header_name: 'Authorization',
            variable_name: '',
            environment_id: requestedSelection.environment_id,
            environment_name: requestedSelection.environment_id === 'env-staging' ? '预发环境' : 'QA 环境',
            scope: 'environment',
            reused: false,
            usage_count: 0,
            updated_at: '2026-07-22 09:44:00',
          };
        }
        const delay = requestedSelection.project_id === 'project-interface' ? 180 : 20;
        setTimeout(() => {
          json(res, {
            ok: true,
            binding: {
              binding_id: 'source-binding-visual-001',
              source_id: 'api-source-visual-001',
              provider: 'metersphere',
              project_id: requestedSelection.project_id,
              project_name: requestedSelection.project_id === 'project-3d' ? '3D 业务' : '接口业务',
              environment_id: requestedSelection.environment_id,
              environment_name: requestedSelection.environment_id === 'env-staging' ? '预发环境' : 'QA 环境',
              config_fingerprint: 'binding-visual-002',
              auth_binding: {...businessAuth},
              updated_at: '2026-07-22 09:44:00',
            },
          });
        }, delay);
      });
      return;
    }
    if (url.pathname === '/api/api-testing/sources/api-source-visual-001/auth-binding' && req.method === 'POST') {
      readJsonBody(req).then(body => {
        authRequestBodies.push({...body});
        businessAuth = {
          configured: true,
          auth_type: body.auth_type,
          header_name: body.auth_type === 'api_key' ? body.header_name : 'Authorization',
          variable_name: body.auth_type === 'api_key' ? 'API_KEY' : 'API_BEARER_TOKEN',
          environment_id: meterSelection.environment_id,
          environment_name: meterSelection.environment_id === 'env-staging' ? '预发环境' : 'QA 环境',
          scope: 'environment',
          reused: true,
          usage_count: 2,
          updated_at: '2026-07-22 09:45:00',
        };
        json(res, {ok: true, binding: {...businessAuth}});
      });
      return;
    }
    if (url.pathname === '/api/api-testing/sources/api-source-visual-001/auth-binding' && req.method === 'DELETE') {
      readJsonBody(req).then(body => {
        authRequestBodies.push({...body});
        businessAuth = {
          configured: false,
          auth_type: businessAuth.auth_type,
          header_name: businessAuth.header_name,
          variable_name: '',
          environment_id: meterSelection.environment_id,
          environment_name: meterSelection.environment_id === 'env-staging' ? '预发环境' : 'QA 环境',
          updated_at: '2026-07-22 09:46:00',
        };
        json(res, {ok: true, binding: {...businessAuth}});
      });
      return;
    }
    if (url.pathname === '/api/api-testing/metersphere/execution-context') {
      const execution = meterExecution();
      json(res, {
        ok: true,
        source_id: url.searchParams.get('source_id') || 'api-source-visual-001',
        connection: {
          state: 'connected',
          base_url: 'http://metersphere.example.test',
          auth_mode: 'access_key',
          latency_ms: 82,
          checked_at: '2026-07-22 09:42:18',
        },
        selection: {...meterSelection},
        binding: {
          binding_id: 'source-binding-visual-001',
          source_id: 'api-source-visual-001',
          provider: 'metersphere',
          project_id: meterSelection.project_id,
          project_name: meterSelection.project_id === 'project-3d' ? '3D 业务' : '接口业务',
          environment_id: meterSelection.environment_id,
          environment_name: meterSelection.environment_id === 'env-staging' ? '预发环境' : 'QA 环境',
          config_fingerprint: 'binding-visual-001',
          auth_binding: {...businessAuth},
          updated_at: '2026-07-22 09:42:18',
        },
        auth_binding: {...businessAuth},
        businesses: [
          {id: 'project-interface', name: '接口业务', enabled: true},
          {id: 'project-3d', name: '3D 业务', enabled: true},
        ],
        environments: [
          meterSelection.project_id === 'project-3d'
            ? {id: 'env-staging', name: '预发环境', project_id: 'project-3d', enabled: true}
            : {id: 'env-qa', name: 'QA 环境', project_id: 'project-interface', enabled: true},
        ],
        metadata: {source: 'live', stale: false, fetched_at: '2026-07-22 09:42:18', errors: []},
        config: {
          base_url: 'http://metersphere.example.test',
          auth_mode: 'access_key',
          access_key_configured: true,
          secret_key_configured: true,
          token_configured: false,
          workspace_id: 'workspace-visual',
          project_id: 'project-interface',
          environment_id: 'env-qa',
          health_path: '/api/health',
          project_list_path: '/projects',
          environment_list_path: '/environments/{project_id}',
          case_push_path: '/cases/push',
          plan_run_path: '/plans/run',
          run_status_path: '/runs/{run_id}',
          report_path: '/reports/{run_id}',
        },
        capabilities: {can_push: true, can_run: true, can_query_run: true, can_pull_report: true, missing: [], ready: true},
        readiness: {state: 'running', can_execute: true, missing: [], primary_action: '查看实时进度'},
        plans: [{
          plan_id: 'api-plan-visual-001',
          name: '账号接口日常回归',
          status: 'confirmed',
          endpoint_count: 12,
          case_count: 24,
          executable_case_count: 24,
          needs_review_case_count: 0,
          execution_readiness: {state: 'ready', executable_case_count: 24, needs_review_case_count: 0, can_execute: true, missing: []},
          revision_state: {state: 'fresh'},
          confirmed_at: '2026-07-22 09:30:00',
          test_plan_name: 'QA 每日主链路',
          can_execute: false,
          active_run: execution,
          latest_run: execution,
        }],
        active_runs: [execution],
        recent_runs: [],
        empty_reason: '',
      });
      return;
    }
    if (url.pathname === '/api/api-testing/metersphere/executions/ms-execution-visual-001') {
      json(res, {ok: true, execution: meterExecution()});
      return;
    }
    if (url.pathname === '/api/api-testing/metersphere/executions/ms-execution-late') {
      setTimeout(() => {
        json(res, {
          ok: true,
          execution: {
            ...meterExecution(),
            execution_id: 'ms-execution-late',
            plan_name: 'LATE EXECUTION MUST NOT RENDER',
          },
        });
      }, 180);
      return;
    }
    if (url.pathname === '/api/api-testing/reports' && req.method === 'GET') {
      const sourceId = url.searchParams.get('source_id') || '';
      reportSourceQueries.push({
        sourceId,
        businessLine: url.searchParams.get('business_line') || '',
      });
      const reportId = sourceId === 'api-source-visual-002'
        ? 'SOURCE 2 REPORT'
        : (sourceId === 'api-source-visual-001' ? 'LATE SOURCE 1 REPORT' : 'UNSCOPED REPORT');
      setTimeout(() => {
        json(res, {
          ok: true,
          reports: [{
            report_id: reportId,
            source_id: sourceId,
            status: 'passed',
            total: 1,
            passed: 1,
            failed: 0,
            created_at: '2026-07-22 09:50:00',
          }],
        });
      }, sourceId === 'api-source-visual-001' ? 180 : 20);
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
              plan: {
                aiGenerated: true,
                model: 'qwen-plus',
                objective: '验证关节龙打印主流程可达且关键文案可见',
                businessFlows: [{name: '关节龙打印', steps: ['进入首页', '打开模型详情', '进入打印流程'], checks: ['打印入口可见', '确认页可达']}],
                platformLifecycle: ['生成并校验 YAML', '固定 Runner 设备执行'],
                visualReference: {figmaPageCount: 1, figmaImageCount: 1, sentToAiForJudgement: true, aiJudgementCompleted: true},
              },
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
            plan: {
              aiGenerated: true,
              model: 'qwen-plus',
              objective: '验证关节龙打印主流程可达且关键文案可见',
              businessFlows: [{name: '关节龙打印', steps: ['进入首页', '打开模型详情', '进入打印流程'], checks: ['打印入口可见', '确认页可达']}],
              platformLifecycle: ['生成并校验 YAML', '固定 Runner 设备执行'],
              visualReference: {figmaPageCount: 1, figmaImageCount: 1, sentToAiForJudgement: true, aiJudgementCompleted: true},
            },
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
      resolve({
        server,
        url: `http://127.0.0.1:${address.port}/task-manager.html`,
        getFileReadCount: () => fileReadCount,
        getBindingRequestBodies: () => bindingRequestBodies.map(item => ({...item})),
        getAuthRequestBodies: () => authRequestBodies.map(item => ({...item})),
        getReportSourceQueries: () => [...reportSourceQueries],
      });
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
  const {server, url, getFileReadCount, getBindingRequestBodies, getAuthRequestBodies, getReportSourceQueries} = await serve();
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
    await page.fill('#password', 'visual-smoke-password');
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

    await page.evaluate(() => showMindmapCenter());
    await page.waitForSelector('text=脑图中心');
    await page.waitForSelector('.mindmap-compact-list .mindmap-row.file');
    const firstMindmapTitle = await page.locator('.mindmap-row.file .mindmap-row-title strong').first().innerText();
    if (!/最新 AI 建模脑图/.test(firstMindmapTitle)) throw new Error(`mindmap list is not latest-first: ${firstMindmapTitle}`);
    const mindmapRows = await page.locator('.mindmap-row.file').count();
    if (mindmapRows < 2) throw new Error(`mindmap compact rows missing, rows=${mindmapRows}`);
    const firstMindmapHeight = await page.locator('.mindmap-row.file').first().boundingBox();
    if (!firstMindmapHeight || firstMindmapHeight.height > 150) throw new Error(`mindmap row is too tall: ${firstMindmapHeight && firstMindmapHeight.height}`);
    await page.screenshot({path: path.join(ARTIFACTS, 'mindmap.png'), fullPage: true});

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

    await page.click('.workflow-step[data-workflow="api_assets"]');
    await page.waitForSelector('.api-asset-console');
    await page.waitForFunction(() => document.querySelector('.api-project-select')?.value === 'api-source-visual-001');
    if (await page.locator('.api-endpoint-check:checked').count()) throw new Error('Module workspace must start with zero selected endpoints');
    await page.locator('.api-project-select').selectOption('api-source-visual-002');
    await page.waitForFunction(() => document.querySelector('.api-project-select')?.value === 'api-source-visual-002');
    if (await page.locator('.api-endpoint-check:checked').count()) throw new Error('Switching source must reset endpoint selection');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-project-switch.png'), fullPage: true});
    await page.locator('.api-project-select').selectOption('api-source-visual-001');
    await page.waitForFunction(() => document.querySelector('.api-project-select')?.value === 'api-source-visual-001');
    await page.locator('.api-asset-revision-picker select').selectOption('api-revision-visual-000');
    await page.waitForFunction(() => apiTestingProjectScope.revisionId === 'api-revision-visual-000');
    if (await page.locator('.api-endpoint-check:checked').count()) throw new Error('Switching revision must reset endpoint selection');
    await page.locator('.api-asset-revision-picker select').selectOption('api-revision-visual-001');
    await page.waitForFunction(() => apiTestingProjectScope.revisionId === 'api-revision-visual-001');
    await page.locator('.api-module-tree input[data-module-path="家用业务/app接口/我的"]').check();
    const siblingSelected = await page.locator('.api-module-tree input[data-module-path="家用业务/app接口/我的下载"]').isChecked();
    if (siblingSelected) throw new Error('Selecting a parent-like module must not select a same-prefix sibling');
    await page.locator('.api-module-tree button[data-module-path="家用业务/app接口/我的"]').click();
    const activeEndpointText = await visibleText(page, '.api-module-endpoints');
    if (!/家用业务\/app接口\/我的/.test(activeEndpointText) || /我的下载备份/.test(activeEndpointText)) throw new Error('Endpoint table must show only the active module path boundary');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-module-tree.png'), fullPage: true});
    await page.locator('button[aria-label="新增 Apifox 项目"]').click();
    if (!await page.locator('#api-source-settings-panel:not([hidden])').isVisible()) throw new Error('Add project action must open an empty source draft');
    if (await page.locator('#api-source-project-id').inputValue()) throw new Error('New project draft must not inherit an existing Apifox project id');
    await page.locator('button[aria-label="取消新增 Apifox 项目"]').click();
    if (!await page.locator('.api-source-actions .btn-sm.primary').isVisible()) throw new Error('Apifox sync must remain the primary asset action while an automatic sync is running');
    for (const [workflow, icon] of Object.entries({api_dashboard: '🧭', api_assets: '🔗', api_plan: '🧠', api_execution: '▶️', api_reports: '📊'})) {
      const iconText = await page.locator(`.workflow-step[data-workflow="${workflow}"] .workflow-index`).textContent();
      if ((iconText || '').trim() !== icon) throw new Error(`API sidebar icon mismatch for ${workflow}: ${iconText}`);
    }
    await page.locator('button[aria-label="Apifox 来源设置"]').click();
    await page.waitForSelector('#api-source-settings-panel:not([hidden])');
    if (!await page.locator('#api-source-credential-saved').isVisible()) throw new Error('Configured Apifox token must show a saved credential state');
    if (await page.locator('#api-source-token-editor').isVisible()) throw new Error('Configured Apifox token editor must stay hidden until replacement is requested');
    if (await page.locator('#api-source-token').inputValue()) throw new Error('Saved Apifox token must never be refilled into the browser');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-source-settings.png'), fullPage: true});
    await page.locator('button[aria-label="更换 Apifox 访问令牌"]').click();
    if (!await page.locator('#api-source-token-editor').isVisible()) throw new Error('Apifox replacement token editor did not open on demand');
    if (await page.locator('#api-source-credential-saved').isVisible()) throw new Error('Saved credential state must hide while replacement editor is open');
    if (await page.locator('#api-source-token').inputValue()) throw new Error('Apifox replacement token editor must open empty');
    await page.locator('button[aria-label="取消更换 Apifox 访问令牌"]').click();
    if (!await page.locator('#api-source-credential-saved').isVisible()) throw new Error('Cancelling token replacement must restore saved credential state');
    await page.locator('button[aria-label="关闭设置"]').click();
    await page.locator('.api-sync-log-detail > summary').click();
    const apiAssetLogScrollBefore = await page.locator('.api-asset-sync-log').evaluate(el => {
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTop = Math.min(160, max);
      return {top: el.scrollTop, max};
    });
    if (apiAssetLogScrollBefore.max < 50 || apiAssetLogScrollBefore.top <= 0) throw new Error(`Apifox sync log fixture is not independently scrollable: ${JSON.stringify(apiAssetLogScrollBefore)}`);
    await page.evaluate(() => pollApiAssetSync(apiAssetActiveSyncId));
    await page.waitForTimeout(100);
    if (!await page.locator('.api-sync-log-detail').evaluate(el => el.open)) throw new Error('Apifox sync polling collapsed an expanded technical log');
    const apiAssetLogScrollAfter = await page.locator('.api-asset-sync-log').evaluate(el => el.scrollTop);
    if (Math.abs(apiAssetLogScrollAfter - apiAssetLogScrollBefore.top) > 2) throw new Error(`Apifox sync polling reset technical log scroll: before=${apiAssetLogScrollBefore.top}, after=${apiAssetLogScrollAfter}`);
    const apiAssetDesktopOverflow = await page.locator('.api-asset-console').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (apiAssetDesktopOverflow) throw new Error('API asset sync console overflows horizontally on desktop');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-assets-sync.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    await page.screenshot({path: path.join(ARTIFACTS, 'api-project-switch-mobile.png'), fullPage: true});
    if (await page.locator('.api-module-workspace').evaluate(el => el.scrollWidth > el.clientWidth + 1)) throw new Error('Module workspace overflows horizontally on mobile');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-module-tree-mobile.png'), fullPage: true});
    const apiAssetMobileOverflow = await page.locator('.api-asset-console').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (apiAssetMobileOverflow) {
      const overflowDetails = await page.locator('.api-asset-console *').evaluateAll(elements => elements.map(el => ({
        tag: el.tagName,
        cls: el.className,
        width: el.getBoundingClientRect().width,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      })).filter(item => item.scrollWidth > item.clientWidth + 1).slice(0, 12));
      throw new Error(`API asset sync console overflows horizontally on mobile: ${JSON.stringify(overflowDetails)}`);
    }
    if (!await page.locator('.api-source-actions .btn-sm.primary').isVisible()) throw new Error('Apifox sync action is not visible on mobile');
    await page.locator('button[aria-label="Apifox 来源设置"]').click();
    if (!await page.locator('#api-source-credential-saved').isVisible()) throw new Error('Configured Apifox token state is not visible on mobile');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-source-settings-mobile.png'), fullPage: true});
    await page.locator('button[aria-label="关闭设置"]').click();
    await page.screenshot({path: path.join(ARTIFACTS, 'api-assets-sync-mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1440, height: 900});
    await page.locator('.api-module-tree button[data-module-path="家用业务"]').click();
    await page.locator('.api-module-select-current').click();
    if (await page.locator('.api-endpoint-check:checked').count() !== 25) throw new Error('Plan generation fixture must explicitly select all 25 endpoints');

    await page.click('.workflow-step[data-workflow="api_plan"]');
    await page.waitForSelector('#api-plan-result');
    await page.evaluate(async () => {
      apiTestingCurrentSnapshotId = '';
      apiTestingProjectScope = {sourceId: 'api-source-visual-001', revisionId: ''};
      await showApiPlanPage();
    });
    await page.waitForSelector('.api-plan-workspace');
    const directPlanScope = await page.evaluate(() => ({...apiTestingProjectScope}));
    if (directPlanScope.revisionId !== 'api-revision-visual-001') throw new Error(`Direct plan navigation did not resolve the active revision: ${JSON.stringify(directPlanScope)}`);
    if (!await page.locator('.api-plan-generate-action').isVisible()) throw new Error('Asynchronous plan generation action is missing');
    await page.locator('.api-plan-generate-action').click();
    await page.waitForSelector('.api-plan-generation[data-status="running"]');
    if (await page.locator('.api-plan-batch-row.status-running').count() !== 1) throw new Error('Plan generation UI must show only the server-reported sequential active batch');
    await page.waitForSelector('.api-plan-generation[data-status="partial"]');
    const generationCounts = await page.locator('.api-plan-batch-row .api-plan-batch-count').allTextContents();
    if (generationCounts.map(value => Number(value.trim())).join(',') !== '12,12,1') throw new Error(`25 endpoint generation must render stable 12/12/1 batches: ${generationCounts}`);
    const partialGenerationText = await visibleText(page, '.api-plan-generation');
    if (!/校验接口范围/.test(partialGenerationText) || !/AI 分批设计/.test(partialGenerationText) || /api-generation-visual-001/.test(partialGenerationText)) throw new Error('Plan generation must show human stages and keep technical ids collapsed by default');
    if (await page.locator('.api-plan-generation button:has-text("重试失败批次")').count() !== 1) throw new Error('Partial generation must offer exactly one failed-batch retry action');
    const planCardsText = await visibleText(page, '.api-plan-card-list');
    if (!/可执行/.test(planCardsText) || !/待补/.test(planCardsText) || /api-revision-visual-001|API_BEARER_TOKEN/.test(planCardsText)) throw new Error('Plan cards must prioritize review counts and keep technical facts collapsed');
    await page.locator('.api-plan-card .api-plan-tech-detail > summary').first().click();
    const planCardTechnicalText = await visibleText(page, '.api-plan-card-list');
    if (!/3D 接口/.test(planCardTechnicalText) || !/api-revision-visual-001/.test(planCardTechnicalText) || !/qwen3\.8-plus/.test(planCardTechnicalText) || !/接口业务/.test(planCardTechnicalText) || !/API_BEARER_TOKEN/.test(planCardTechnicalText)) throw new Error('Expanded plan technical details must retain backend source, revision, AI, binding, and auth facts');
    await page.locator('.api-generation-log-detail > summary').click();
    const generationTechnicalText = await visibleText(page, '.api-plan-generation');
    if (!/api-generation-visual-001/.test(generationTechnicalText) || !/api-plan-real-001/.test(generationTechnicalText) || !/api-plan-real-002/.test(generationTechnicalText)) throw new Error('Expanded generation technical details must expose real server ids');
    const generationLogScrollBefore = await page.locator('.api-generation-log-content').evaluate(el => {
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTop = Math.min(170, max);
      return {top: el.scrollTop, max};
    });
    if (generationLogScrollBefore.max < 50 || generationLogScrollBefore.top <= 0) throw new Error(`Generation technical log fixture is not independently scrollable: ${JSON.stringify(generationLogScrollBefore)}`);
    await page.evaluate(() => pollApiPlanGeneration(apiPlanGenerationCurrent.generation_id));
    await page.waitForTimeout(100);
    if (!await page.locator('.api-generation-log-detail').evaluate(el => el.open)) throw new Error('Generation polling collapsed an expanded technical log');
    const generationLogScrollAfter = await page.locator('.api-generation-log-content').evaluate(el => el.scrollTop);
    if (Math.abs(generationLogScrollAfter - generationLogScrollBefore.top) > 2) throw new Error(`Generation polling reset technical log scroll: before=${generationLogScrollBefore.top}, after=${generationLogScrollAfter}`);
    await page.screenshot({path: path.join(ARTIFACTS, 'api-batch-review.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    if (await page.locator('.api-plan-workspace').evaluate(el => el.scrollWidth > el.clientWidth + 1)) throw new Error('API batch review overflows horizontally on mobile');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-batch-review-mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1440, height: 900});
    await page.locator('.api-plan-generation button:has-text("重试失败批次")').click();
    await page.waitForSelector('.api-plan-generation[data-status="succeeded"]');
    if (!await page.locator('.api-generation-log-detail').evaluate(el => el.open)) {
      await page.locator('.api-generation-log-detail > summary').click();
    }
    const completedGenerationText = await visibleText(page, '.api-plan-generation');
    if (!/api-plan-real-003/.test(completedGenerationText)) throw new Error('Retry must preserve successful plans and expose the recovered batch plan id');
    await page.evaluate(() => {
      window.__lateGenerationPoll = pollApiPlanGeneration('api-generation-late');
      apiTestingProjectScope = {sourceId: 'api-source-visual-002', revisionId: 'api-revision-visual-002'};
    });
    await page.waitForTimeout(260);
    if (/LATE RESPONSE MUST NOT RENDER/.test(await visibleText(page, '.api-plan-generation'))) throw new Error('A late generation response redrew the newly selected source');
    await page.evaluate(() => {
      apiTestingProjectScope = {sourceId: 'api-source-visual-001', revisionId: 'api-revision-visual-001'};
    });
    await page.locator('.api-plan-list-button[data-plan-id="api-plan-visual-stale"]').click();
    await page.waitForSelector('#api-plan-result .api-plan-readiness');
    const stalePlanText = await visibleText(page, '#api-plan-result');
    if (!/接口已变化/.test(stalePlanText) || /api-revision-visual-001|API_BEARER_TOKEN/.test(stalePlanText)) throw new Error('Stale plan must explain the user action while technical facts remain collapsed');
    if (!/本版变更 1/.test(stalePlanText)) throw new Error('Changed-case ids from revision state must map back to the affected endpoint group');
    await page.locator('#api-plan-result .api-plan-facts-detail > summary').click();
    const stalePlanTechnicalText = await visibleText(page, '#api-plan-result');
    if (!/3D 接口/.test(stalePlanTechnicalText) || !/api-revision-visual-001/.test(stalePlanTechnicalText) || !/家用业务/.test(stalePlanTechnicalText) || !/qwen3\.8-plus/.test(stalePlanTechnicalText)) throw new Error('Expanded plan details must show source, revision, modules, and AI trace from backend facts');
    if (!/接口业务/.test(stalePlanTechnicalText) || !/API_BEARER_TOKEN/.test(stalePlanTechnicalText)) throw new Error('Expanded plan details must show execution binding and public auth metadata');
    if (await page.locator('#api-plan-result button:has-text("确认可执行用例")').count()) throw new Error('A stale plan must not be confirmable');
    if (!await page.locator('#api-plan-result button:has-text("按最新接口重新生成")').isVisible()) throw new Error('A stale plan must offer regeneration');
    await page.locator('.api-plan-list-button[data-plan-id="api-plan-visual-ready"]').click();
    await page.waitForSelector('#api-plan-result .api-plan-readiness');
    let planText = await visibleText(page, '#api-plan-result');
    if (!/可执行/.test(planText) || !/待补数据/.test(planText) || !/request\.body\.productId/.test(planText)) throw new Error('API plan detail must expose readiness counts and missing data');
    if (!await page.locator('#api-plan-result button:has-text("确认可执行用例")').isVisible()) throw new Error('A partial plan with executable cases must remain confirmable');
    if (await page.locator('#api-plan-result button:has-text("进入执行")').count()) throw new Error('A draft plan must not expose an execution action');
    const pointsGroup = page.locator('#api-plan-result .api-case-group[data-endpoint-key="GET /points"]');
    if (!await pointsGroup.evaluate(el => el.open)) await pointsGroup.locator('summary').click();
    planText = await visibleText(page, '#api-plan-result');
    if (!/状态码 in 200/.test(planText) || !/GET \/points/.test(planText)) throw new Error('API plan detail must render structured requests and assertions');
    if (await page.locator('#api-plan-result .api-case-group').count() !== 2) throw new Error('API cases must be grouped by endpoint instead of flattened into one dense table');
    const missingCategoryButton = page.locator('#api-plan-result .api-plan-missing-summary button', {hasText: '请求体数据'});
    if (!await missingCategoryButton.isVisible()) throw new Error('Missing-data summary categories must be actionable filters');
    await missingCategoryButton.click();
    if (await page.locator('#api-plan-result .api-case-group').count() !== 1) throw new Error('Missing-data category did not filter endpoint groups');
    await missingCategoryButton.click();
    const reviewSearch = page.locator('#api-plan-result input[aria-label="搜索接口或用例"]');
    await reviewSearch.fill('points');
    const filteredPoints = page.locator('#api-plan-result .api-case-group[data-endpoint-key="GET /points"]');
    if (!await filteredPoints.evaluate(el => el.open)) await filteredPoints.locator('summary').click();
    await filteredPoints.locator('summary').click();
    await page.evaluate(() => rerenderApiPlanReview());
    if (await page.locator('#api-plan-result .api-case-group[data-endpoint-key="GET /points"]').evaluate(el => el.open)) throw new Error('A manually collapsed endpoint group reopened after rerender');
    await page.locator('.api-plan-list-button[data-plan-id="api-plan-visual-stale"]').click();
    await page.waitForSelector('#api-plan-result .api-plan-readiness');
    if (await page.locator('#api-plan-result input[aria-label="搜索接口或用例"]').inputValue()) throw new Error('Review search leaked into another plan');
    await page.locator('.api-plan-list-button[data-plan-id="api-plan-visual-ready"]').click();
    await page.waitForSelector('#api-plan-result .api-plan-readiness');
    if (await page.locator('#api-plan-result input[aria-label="搜索接口或用例"]').inputValue() !== 'points') throw new Error('Plan-scoped review search was not restored');
    await page.locator('#api-plan-result input[aria-label="搜索接口或用例"]').fill('');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-plan-readiness.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    const apiPlanMobileOverflow = await page.locator('.api-testing-page').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (apiPlanMobileOverflow) {
      const overflowDetails = await page.locator('.api-testing-page *').evaluateAll(elements => elements.map(el => ({
        tag: el.tagName,
        cls: el.className,
        width: el.getBoundingClientRect().width,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      })).filter(item => item.scrollWidth > item.clientWidth + 1).slice(0, 12));
      throw new Error(`API plan readiness page overflows horizontally on mobile: ${JSON.stringify(overflowDetails)}`);
    }
    if (await page.locator('.api-workflow-desktop-steps').isVisible()) throw new Error('Desktop workflow steps must collapse on mobile');
    const mobileWorkflow = page.locator('.api-workflow-mobile-steps');
    if (!await mobileWorkflow.isVisible() || !/审阅确认/.test(await visibleText(page, '.api-workflow-mobile-steps > summary'))) throw new Error('Mobile workflow must summarize the current review step');
    await mobileWorkflow.locator('summary').click();
    if (await mobileWorkflow.locator('ol > li').count() !== 5) throw new Error('Expanded mobile workflow must expose all five steps');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-plan-readiness-mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1440, height: 900});

    await page.click('.workflow-step[data-workflow="api_execution"]');
    await page.waitForSelector('.api-execution-console');
    await page.waitForSelector('text=账号接口日常回归');
    if (!/接口业务/.test(await visibleText(page, '#api-execution-header'))) throw new Error('MeterSphere business must render from execution-context data');
    if (!/QA 环境/.test(await visibleText(page, '#api-execution-header'))) throw new Error('MeterSphere environment must render from execution-context data');
    if (!await page.locator('.api-business-auth-panel').isVisible()) throw new Error('Environment-shared business authentication panel is missing');
    if (!/环境公共鉴权/.test(await visibleText(page, '.api-business-auth-panel')) || !/覆盖 2 个业务来源/.test(await visibleText(page, '.api-business-auth-panel')) || /API_BEARER_TOKEN/.test(await visibleText(page, '.api-business-auth-panel'))) throw new Error('Public auth summary must show reusable environment status without technical variables');
    await page.locator('.api-business-auth-panel .api-auth-detail > summary').click();
    if (!/API_BEARER_TOKEN/.test(await visibleText(page, '.api-business-auth-panel'))) throw new Error('Expanded auth management must show only public variable metadata');
    await page.locator('.api-execution-project-select').selectOption('project-3d');
    await page.waitForFunction(() => document.querySelector('.api-execution-project-select')?.value === 'project-3d' && document.querySelector('.api-execution-environment-select')?.value === 'env-staging');
    if (!/3D 业务/.test(await visibleText(page, '#api-execution-header')) || !/预发环境/.test(await visibleText(page, '#api-execution-header'))) throw new Error('Project and environment changes must persist on the selected source binding');
    await page.evaluate(() => {
      window.__oldBindingSave = updateApiMeterSphereSelection({project_id: 'project-interface', environment_id: 'env-qa'});
    });
    await page.waitForTimeout(15);
    await page.evaluate(() => {
      window.__latestBindingSave = updateApiMeterSphereSelection({project_id: 'project-3d', environment_id: 'env-staging'});
      window.__bindingRaceMutations = [];
      window.__bindingRaceObserver = new MutationObserver(() => {
        window.__bindingRaceMutations.push({
          projectId: document.querySelector('.api-execution-project-select')?.value || '',
          environmentId: document.querySelector('.api-execution-environment-select')?.value || '',
        });
      });
      window.__bindingRaceObserver.observe(document.querySelector('#api-execution-header'), {childList: true, subtree: true});
    });
    await page.waitForTimeout(260);
    const bindingRace = await page.evaluate(() => {
      window.__bindingRaceObserver.disconnect();
      return {
        mutations: window.__bindingRaceMutations,
        selection: {...(apiExecutionContext?.selection || {})},
      };
    });
    if (bindingRace.mutations.some(item => item.projectId === 'project-interface' || item.environmentId === 'env-qa')) throw new Error(`A delayed old binding response overwrote the latest UI selection: ${JSON.stringify(bindingRace)}`);
    if (bindingRace.selection.project_id !== 'project-3d' || bindingRace.selection.environment_id !== 'env-staging') throw new Error(`Latest binding intent was not retained: ${JSON.stringify(bindingRace.selection)}`);
    const bindingBodies = getBindingRequestBodies();
    if (
      !bindingBodies.length
      || bindingBodies.some(body => !Object.prototype.hasOwnProperty.call(body, 'expected_binding_fingerprint'))
      || bindingBodies.some(body => !body.client_session_id || !Number.isInteger(body.client_intent_id))
    ) throw new Error(`Binding save did not send optimistic concurrency identity: ${JSON.stringify(bindingBodies)}`);
    await page.screenshot({path: path.join(ARTIFACTS, 'metersphere-project-binding.png'), fullPage: true});
    await page.locator('button[aria-label="配置业务鉴权"]').click();
    if (await page.locator('#api-business-auth-secret').inputValue()) throw new Error('Business auth replacement must always open with an empty secret');
    await page.locator('button[data-auth-type="api_key"]').click();
    if (!await page.locator('#api-business-auth-header').isVisible()) throw new Error('API Key mode must expose a conditional header field');
    await page.locator('#api-business-auth-header').fill('X-API-Key');
    await page.locator('#api-business-auth-secret').fill('visual-secret-must-not-render');
    await page.locator('button:has-text("保存公共鉴权")').click();
    await page.waitForFunction(() => document.querySelector('.api-business-auth-panel')?.innerText.includes('API Key 已配置'));
    const authBodies = getAuthRequestBodies();
    if (
      authBodies.length !== 1
      || authBodies[0].expected_project_id !== 'project-3d'
      || authBodies[0].expected_environment_id !== 'env-staging'
      || authBodies[0].expected_binding_version !== 'binding-visual-002'
      || !Object.prototype.hasOwnProperty.call(authBodies[0], 'expected_profile_version')
    ) throw new Error(`Public auth save did not carry the visible binding/profile CAS state: ${JSON.stringify(authBodies)}`);
    if (/visual-secret-must-not-render/.test(await visibleText(page, '.api-business-auth-panel'))) throw new Error('Saved business secret leaked into rendered metadata');
    await page.locator('.api-business-auth-panel .api-auth-detail > summary').click();
    if (!/API_KEY/.test(await visibleText(page, '.api-business-auth-panel'))) throw new Error('Saved public auth metadata must remain available in management details');
    await page.locator('button[aria-label="更换业务鉴权"]').click();
    if (await page.locator('#api-business-auth-secret').inputValue()) throw new Error('Saved business auth secret was rehydrated into the replacement field');
    await page.locator('button[aria-label="取消更换业务鉴权"]').click();
    await page.screenshot({path: path.join(ARTIFACTS, 'api-business-auth.png'), fullPage: true});
    if (await page.locator('.api-execution-plan-row').count() !== 1) throw new Error('Daily execution console must render confirmed plans as a compact list');
    if (await page.locator('.api-run-phases > li').count() !== 4) throw new Error('Active MeterSphere run must render four stable phases');
    await page.locator('.api-log-detail').first().locator('summary').click();
    const executionLogKey = await page.locator('.api-log-detail').first().getAttribute('data-api-log-key');
    if (!executionLogKey || !executionLogKey.startsWith('api-source-visual-001:api-revision-visual-001::')) throw new Error(`Execution log key must include the selected source scope: ${executionLogKey}`);
    const meterLogScrollBefore = await page.locator('.api-log-detail').first().locator('.api-log-content').evaluate(el => {
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTop = Math.min(180, max);
      return {top: el.scrollTop, max};
    });
    if (meterLogScrollBefore.max < 50 || meterLogScrollBefore.top <= 0) throw new Error(`MeterSphere technical log fixture is not independently scrollable: ${JSON.stringify(meterLogScrollBefore)}`);
    await page.evaluate(() => pollApiMeterSphereExecution(apiExecutionActiveId));
    await page.waitForTimeout(100);
    if (!await page.locator('.api-log-detail').first().evaluate(el => el.open)) throw new Error('MeterSphere status polling collapsed an expanded technical log');
    const meterLogScrollAfter = await page.locator('.api-log-detail').first().locator('.api-log-content').evaluate(el => el.scrollTop);
    if (Math.abs(meterLogScrollAfter - meterLogScrollBefore.top) > 2) throw new Error(`MeterSphere polling reset technical log scroll: before=${meterLogScrollBefore.top}, after=${meterLogScrollAfter}`);
    await page.evaluate(() => {
      apiExecutionActiveId = 'ms-execution-late';
      window.__sameScopeLateExecutionPoll = pollApiMeterSphereExecution(
        'ms-execution-late',
        apiExecutionPollRequestId,
        apiProjectScopeKey(),
      );
    });
    await page.waitForTimeout(20);
    await page.evaluate(() => {
      apiExecutionActiveId = 'ms-execution-visual-001';
    });
    await page.waitForTimeout(220);
    if (/LATE EXECUTION MUST NOT RENDER/.test(await visibleText(page, '#api-active-run'))) throw new Error('A late execution response replaced a newer execution in the same source scope');
    await page.evaluate(() => {
      window.__previousApiExecutionId = apiExecutionActiveId;
      apiExecutionActiveId = 'ms-execution-late';
      window.__lateExecutionPoll = pollApiMeterSphereExecution('ms-execution-late');
      apiTestingProjectScope = {sourceId: 'api-source-visual-002', revisionId: 'api-revision-visual-002'};
    });
    await page.waitForTimeout(260);
    if (/LATE EXECUTION MUST NOT RENDER/.test(await visibleText(page, '#api-active-run'))) throw new Error('A late execution poll response redrew the newly selected source');
    await page.evaluate(() => {
      apiTestingProjectScope = {sourceId: 'api-source-visual-001', revisionId: 'api-revision-visual-001'};
      apiExecutionActiveId = window.__previousApiExecutionId;
    });
    const meterDesktopOverflow = await page.locator('.api-execution-console').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (meterDesktopOverflow) throw new Error('MeterSphere daily execution console overflows horizontally on desktop');
    await page.screenshot({path: path.join(ARTIFACTS, 'metersphere-execution.png'), fullPage: true});
    await page.locator('button[aria-label="MeterSphere 设置"]').click();
    await page.waitForSelector('.api-settings-drawer.open');
    await page.waitForTimeout(220);
    if (await page.locator('.api-settings-group').count() !== 4) throw new Error('MeterSphere settings must use four responsibility-based groups');
    if (!await page.locator('#api-ms-auth-access').isVisible() || await page.locator('#api-ms-auth-token').isVisible()) throw new Error('MeterSphere settings must show only the selected authentication fields');
    const meterSecretValues = await page.locator('#api-ms-access-key, #api-ms-secret-key, #api-ms-token').evaluateAll(inputs => inputs.map(input => input.value));
    if (meterSecretValues.some(Boolean)) throw new Error('MeterSphere saved credentials must never be refilled into browser inputs');
    const meterSettingsDesktopOverflow = await page.locator('.api-settings-drawer').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (meterSettingsDesktopOverflow) throw new Error('MeterSphere settings drawer overflows horizontally on desktop');
    await page.screenshot({path: path.join(ARTIFACTS, 'metersphere-settings.png'), fullPage: true});
    await page.locator('.api-settings-head button[aria-label="关闭设置"]').click();
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    const meterMobileOverflow = await page.locator('.api-execution-console').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (meterMobileOverflow) throw new Error('MeterSphere daily execution console overflows horizontally on mobile');
    if (!await page.locator('.api-execution-plan-row .btn-sm.primary').isVisible()) throw new Error('MeterSphere primary plan action is not visible on mobile');
    await page.screenshot({path: path.join(ARTIFACTS, 'metersphere-project-binding-mobile.png'), fullPage: true});
    if (await page.locator('.api-business-auth-panel').evaluate(el => el.scrollWidth > el.clientWidth + 1)) throw new Error('Business authentication panel overflows horizontally on mobile');
    await page.screenshot({path: path.join(ARTIFACTS, 'api-business-auth-mobile.png'), fullPage: true});
    await page.screenshot({path: path.join(ARTIFACTS, 'metersphere-execution-mobile.png'), fullPage: true});
    await page.locator('button[aria-label="MeterSphere 设置"]').click();
    await page.waitForSelector('.api-settings-drawer.open');
    await page.waitForTimeout(220);
    const meterSettingsMobileOverflow = await page.locator('.api-settings-drawer').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (meterSettingsMobileOverflow) throw new Error('MeterSphere settings drawer overflows horizontally on mobile');
    const meterSettingsMobileBox = await page.locator('.api-settings-drawer').boundingBox();
    if (!meterSettingsMobileBox || meterSettingsMobileBox.x > 1 || Math.abs(meterSettingsMobileBox.width - 390) > 1) throw new Error(`MeterSphere mobile settings drawer must fill the viewport after opening: ${JSON.stringify(meterSettingsMobileBox)}`);
    await page.screenshot({path: path.join(ARTIFACTS, 'metersphere-settings-mobile.png'), fullPage: true});
    await page.locator('.api-settings-head button[aria-label="关闭设置"]').click();
    await page.setViewportSize({width: 1440, height: 900});

    await page.evaluate(() => {
      apiTestingProjectScope = {sourceId: 'api-source-visual-001', revisionId: 'api-revision-visual-001'};
      window.__oldReportLoad = showApiReportsPage();
    });
    await page.waitForTimeout(15);
    await page.evaluate(() => {
      apiTestingProjectScope = {sourceId: 'api-source-visual-002', revisionId: 'api-revision-visual-002'};
      window.__latestReportLoad = showApiReportsPage();
    });
    await page.waitForTimeout(260);
    const reportText = await visibleText(page, '.api-testing-page');
    if (!/SOURCE 2 REPORT/.test(reportText) || /LATE SOURCE 1 REPORT|UNSCOPED REPORT/.test(reportText)) throw new Error(`API reports rendered a stale or unscoped source response: ${reportText}`);
    const reportQueries = getReportSourceQueries();
    if (!reportQueries.some(item => item.sourceId === 'api-source-visual-001' && item.businessLine === '家用业务') || !reportQueries.some(item => item.sourceId === 'api-source-visual-002') || reportQueries.some(item => !item.sourceId)) throw new Error(`API report requests were not source/business-line scoped: ${JSON.stringify(reportQueries)}`);

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
    if (!/Agent 启动前预览/.test(previewDialogText) || !/执行设备/.test(previewDialogText) || !/需求显式候选（非业务路径）/.test(previewDialogText) || !/AI 业务计划：尚未执行/.test(previewDialogText)) throw new Error(`Agent preview button did not keep candidates separate from the later AI plan: ${previewDialogText}`);
    await page.click('button:has-text("安装/更新 App")');
    await page.waitForSelector('text=安装包更新');
    await page.waitForSelector('#apk-install-device');
    if (!await page.locator('text=执行前设备检查').isVisible()) throw new Error('Agent install shortcut did not open the install preflight panel');
    await page.click('.workflow-step:has-text("Agent 工作台")');
    await page.waitForSelector('#agent-goal');
    const agentModelOptions = await page.locator('#agent-model').innerText();
    if (!/自动（按模型策略：千问 Qwen Plus）/.test(agentModelOptions)) throw new Error(`Agent model auto option did not use AI Gateway router: ${agentModelOptions}`);
    if (!await page.locator('text=还没有选择运行记录').isVisible()) throw new Error('Agent workbench should open in new-run mode');
    if (await page.locator('text=Agent 执行阶段').isVisible()) throw new Error('Agent workbench should not show the previous run phases by default');
    await page.fill('#agent-goal', '关节龙打印流程回归');
    await page.click('#agent-start-btn');
    await page.waitForSelector('text=Agent 执行阶段');
    await page.waitForSelector('.agent-phase-list');
    if (await page.locator('.agent-phase-step').count() !== 5) throw new Error('The normal Agent path should show five phases; failure recovery must remain conditional');
    if (await page.locator('.agent-checkpoint-trace').evaluate(el => el.open)) throw new Error('Internal Agent checkpoints should be collapsed by default');
    await page.locator('.agent-checkpoint-trace > summary').click();
    await page.waitForTimeout(50);
    if (!await page.locator('.agent-checkpoint-trace').evaluate(el => el.open)) throw new Error('Internal Agent checkpoints did not open after user interaction');
    await page.evaluate(() => updateAgentWorkbenchDynamic());
    if (!await page.locator('.agent-checkpoint-trace').evaluate(el => el.open)) throw new Error('Internal Agent checkpoints collapsed after a polling-style render');
    await page.locator('.agent-checkpoint-trace > summary').click();
    await page.waitForTimeout(50);
    await page.evaluate(() => updateAgentWorkbenchDynamic());
    if (await page.locator('.agent-checkpoint-trace').evaluate(el => el.open)) throw new Error('Internal Agent checkpoints reopened after the user collapsed them');
    await page.waitForSelector('text=人工复核');
    await page.waitForSelector('.agent-artifact-layout');
    if (await page.locator('.agent-artifact-nav-group').count() !== 5) throw new Error('Agent artifacts must be grouped into five readable sections');
    if (await page.locator('.agent-artifact-nav-item').count() !== 11) throw new Error('Grouped Agent navigation must retain all eleven auditable artifacts');
    if (!await page.locator('.agent-artifact-nav-item[data-tab="plan"]').evaluate(el => el.classList.contains('active'))) throw new Error('AI plan must be the default Agent artifact');
    if (!/平台 MM AI/.test(await visibleText(page, '#agent-artifact-box'))) throw new Error('AI plan artifact is not rendered as readable content');
    const artifactDesktop = await page.locator('.agent-artifact-layout').boundingBox();
    const artifactNavDesktop = await page.locator('.agent-artifact-nav').boundingBox();
    if (!artifactDesktop || !artifactNavDesktop || artifactNavDesktop.width < 150 || artifactNavDesktop.width > 210) throw new Error(`Agent artifact desktop navigation has suspicious dimensions: ${JSON.stringify({artifactDesktop, artifactNavDesktop})}`);
    const artifactOverflow = await page.locator('#agent-artifacts-card').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (artifactOverflow) throw new Error('Agent artifact card overflows horizontally on desktop');
    await page.screenshot({path: path.join(ARTIFACTS, 'agent.png'), fullPage: true});
    if (!await page.locator('text=Agent 状态').isVisible()) throw new Error('Agent status center is missing');
    if (!await anyVisible(page.locator('text=确认执行'))) throw new Error('Agent wait-confirm action is missing');
    if (!await page.locator('button:has-text("下载 YAML")').isVisible()) throw new Error('Agent YAML download button is missing');
    await page.locator('.agent-artifact-nav-item[data-tab="failure"]').click();
    if (!/当前无需失败分析/.test(await visibleText(page, '#agent-artifact-box'))) throw new Error('Conditional failure artifact must explain why no content exists');
    await page.locator('.agent-artifact-nav-item[data-tab="plan"]').click();
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    const artifactMobileOverflow = await page.locator('#agent-artifacts-card').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (artifactMobileOverflow) throw new Error('Agent artifact card overflows horizontally on mobile');
    const mobileNav = await page.locator('.agent-artifact-nav').boundingBox();
    const mobileView = await page.locator('.agent-artifact-view').boundingBox();
    if (!mobileNav || !mobileView || mobileView.y < mobileNav.y + mobileNav.height - 1) throw new Error('Agent artifact mobile navigation must sit above the detail view');
    await page.screenshot({path: path.join(ARTIFACTS, 'agent-mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1440, height: 900});
    await page.evaluate(async () => {
      const branchNames = ['文档打印入口文案校验', '照片打印入口可达性校验', '扫描复印入口同级关系校验'];
      const failedExecutionItems = Array.from({length: 12}, (_, index) => ({
        jobId: `job-failure-${String(index + 1).padStart(2, '0')}`,
        taskName: `${branchNames[index % branchNames.length]} ${index + 1}`,
        file: `midscene-tasks/AI_Agent_草稿/${String(index + 1).padStart(2, '0')}-${branchNames[index % branchNames.length]}.yaml`,
        status: 'failed',
        failureType: index % 4 === 3 ? 'ENV_ISSUE' : 'SCRIPT_ISSUE',
        failureReason: index === 0
          ? '脚本停留在照片打印父页面，仍等待内层规格页的目标文案；Runner 关键帧显示当前页面存在可见的“照片打印”入口，应先完成父子页面导航。'
          : '当前页面与脚本等待目标不一致，需要结合失败关键帧和同业务成功基线修正可见文字导航。',
        reportUrl: `/reports/job-failure-${String(index + 1).padStart(2, '0')}.html`,
      }));
      agentCurrentRun = normalizeAgentRun({
        runId: 'agent-failure-visual-001',
        target: '基础打印新增百度网盘入口',
        status: 'FAILED',
        currentStep: 'ANALYZE_FAILURE',
        updatedAt: '2026-07-15T14:05:14',
        steps: [{state: 'ANALYZE_FAILURE', status: 'SUCCESS', summary: 'AI 已完成失败归因'}],
        artifacts: {
          failureAnalysis: {
            failureType: 'SCRIPT_ISSUE',
            conclusion: '失败主要来自脚本导航层级不足：Runner 已到达业务父页面，但 YAML 直接等待叶子页面目标，导致可见文字定位超时。',
            recommendation: '优先参考同业务成功基线补齐父页面到叶子页的短链路，再使用当前关键帧中的真实可见文字完成稳定态断言。',
            canAutoRepair: true,
            summary: JSON.stringify({runnerReport: {failed: 12, logs: '完整 Runner 日志与内部字段仅在技术详情中展示。'.repeat(30)}}),
            aiEvidence: [
              '关键帧显示“照片打印”入口可见，但目标规格文案尚未出现。',
              '同业务成功基线包含父页面到规格页的可见文字点击路径。',
            ],
            evidence: {
              reportKeyframeCount: 3,
              reportKeyframes: [{name: 'before-failure.png'}, {name: 'failure.png'}, {name: 'after-cleanup.png'}],
              baselineExamples: [
                {id: 'baseline-photo-6inch', provenancePath: 'server-tasks-all/小白学习基线用例-基础打印/6寸照片打印.yaml', businessPath: '首页 > 照片打印 > 照片打印 > 6寸照片'},
                {id: 'baseline-scan', provenancePath: 'server-tasks-all/小白学习基线用例-基础打印/文件扫描.yaml', businessPath: '首页 > 扫描复印 > 文件扫描'},
              ],
              sources: ['runner_report', 'report_keyframes', 'successful_baselines'],
            },
          },
          diagnosis: {
            rootCause: 'YAML 缺少父页面到业务叶子页的可见文字导航。',
            impact: '12 个 Runner 失败任务需要逐条归因；此前真实通过的任务不受影响。',
            nextActions: ['生成有界修复草稿', '静态校验通过后在同一固定设备验证'],
          },
          failedExecutionItems,
        },
      });
      agentActiveTab = 'failure';
      await showAgentWorkbench();
    });
    await page.waitForSelector('.agent-failure-overview');
    if (await page.locator('.agent-failure-card').count() !== 3) throw new Error('Failure analysis must use three concise summary cards');
    if (await page.locator('.agent-failure-task').count() !== 12) throw new Error('Failure analysis lost task-level Runner outcomes');
    const failureText = await visibleText(page, '#agent-artifact-box');
    for (const label of ['根因判断', '影响范围', '建议动作', 'AI 判断依据']) {
      if (!failureText.includes(label)) throw new Error(`Structured failure analysis is missing: ${label}`);
    }
    const runErrorFallbackText = await page.evaluate(() => {
      const holder = document.createElement('div');
      holder.innerHTML = renderAgentArtifactContent('failure', {
        runId: 'agent-failure-before-analysis',
        status: 'FAILED',
        error: '最终覆盖门禁阻断：扫描复印入口尚未形成可执行 YAML',
        artifacts: {},
      }, 'ready');
      return holder.innerText;
    });
    if (!/扫描复印入口尚未形成可执行 YAML/.test(runErrorFallbackText)) throw new Error('Structured failure analysis hid a top-level Agent error before AI RCA completed');
    if (await page.locator('.agent-failure-technical > .agent-artifact-pre').isVisible()) throw new Error('Raw failure JSON must stay collapsed by default');
    const artifactScrollBefore = await page.locator('#agent-artifact-box').evaluate(el => {
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTop = Math.min(260, max);
      return {top: el.scrollTop, max};
    });
    if (artifactScrollBefore.max < 200 || artifactScrollBefore.top <= 0) throw new Error(`Failure fixture is not scrollable: ${JSON.stringify(artifactScrollBefore)}`);
    await page.evaluate(() => updateAgentWorkbenchDynamic());
    const artifactScrollAfter = await page.locator('#agent-artifact-box').evaluate(el => el.scrollTop);
    if (Math.abs(artifactScrollAfter - artifactScrollBefore.top) > 2) throw new Error(`Agent artifact polling reset scroll position: before=${artifactScrollBefore.top}, after=${artifactScrollAfter}`);
    await page.locator('.agent-failure-technical > summary').click();
    const openScrollBefore = await page.locator('#agent-artifact-box').evaluate(el => {
      el.scrollTop = Math.min(420, el.scrollHeight - el.clientHeight);
      return el.scrollTop;
    });
    await page.evaluate(() => updateAgentWorkbenchDynamic());
    if (!await page.locator('.agent-failure-technical').evaluate(el => el.open)) throw new Error('Polling collapsed the user-opened technical failure detail');
    const openScrollAfter = await page.locator('#agent-artifact-box').evaluate(el => el.scrollTop);
    if (Math.abs(openScrollAfter - openScrollBefore) > 2) throw new Error(`Polling reset scroll after opening technical detail: before=${openScrollBefore}, after=${openScrollAfter}`);
    await page.locator('.agent-failure-technical > summary').click();
    await page.locator('#agent-artifact-box').evaluate(el => { el.scrollTop = 0; });
    await page.locator('#agent-artifacts-card').screenshot({path: path.join(ARTIFACTS, 'agent-failure.png')});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    const failureMobileOverflow = await page.locator('#agent-artifacts-card').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (failureMobileOverflow) throw new Error('Structured failure analysis overflows horizontally on mobile');
    const failureTypeChipMobile = await page.locator('.agent-failure-overview .failure-type-chip').boundingBox();
    if (!failureTypeChipMobile || failureTypeChipMobile.width < 54 || failureTypeChipMobile.height > 30) throw new Error(`Failure type chip must remain horizontal on mobile: ${JSON.stringify(failureTypeChipMobile)}`);
    await page.locator('#agent-artifact-box').evaluate(el => { el.scrollTop = 0; });
    await page.evaluate(() => {
      const card = document.querySelector('#agent-artifacts-card');
      const shell = document.createElement('div');
      shell.id = 'agent-artifact-visual-shell';
      shell.style.cssText = 'position:fixed;inset:0;z-index:99999;overflow:auto;padding:8px;background:#02060e;';
      const clone = card.cloneNode(true);
      clone.style.width = '100%';
      clone.style.boxSizing = 'border-box';
      shell.appendChild(clone);
      document.body.appendChild(shell);
    });
    await page.locator('#agent-artifact-visual-shell > #agent-artifacts-card').screenshot({path: path.join(ARTIFACTS, 'agent-failure-mobile.png')});
    await page.locator('#agent-artifact-visual-shell').evaluate(el => el.remove());
    await page.setViewportSize({width: 1440, height: 900});
    await page.evaluate(async () => {
      agentCurrentRun = normalizeAgentRun({
        runId: 'agent-rerun-visual-001',
        target: '基础打印新增入口回归',
        status: 'FAILED',
        currentStep: 'RERUN',
        runnerId: 'win-runner-01',
        deviceId: 'ecbfd645',
        deviceStrategy: 'fixed',
        updatedAt: '2026-07-14T11:20:35',
        steps: [{
          state: 'RERUN',
          status: 'PARTIAL_FAILED',
          summary: '重跑执行完成：3 个修复任务，1 个成功，2 个失败',
          toolCalls: [{toolName: 'retry_failed_job', status: 'PARTIAL_FAILED', outputSummary: '固定 OPPO 串行重跑完成'}],
          liveTrace: [
            {time: '2026-07-14T11:18:05', status: 'RUNNING', message: '准备调用工具：_tool_rerun'},
            {time: '2026-07-14T11:20:35', status: 'FAILED', message: '调用工具：_tool_rerun'},
          ],
        }],
        artifacts: {
          rerunResult: {createdCount: 3, completedCount: 1, failedCount: 2, timeoutCount: 0},
          rerunProgress: {
            source: 'repair_draft',
            usesRepairDraft: true,
            sourceFailedCount: 3,
            total: 3,
            completedCount: 3,
            successCount: 1,
            failedCount: 2,
            timeoutCount: 0,
            runningCount: 0,
            pendingCount: 0,
            serialSameDevice: true,
            runnerId: 'win-runner-01',
            deviceId: 'ecbfd645',
            items: [
              {sourceJobId: 'job-source-document', newJobId: 'job-rerun-document', targetTaskName: '文档打印百度网盘入口', sourceFile: '01-document.yaml', repairFile: '01-document-repair.yaml', failureReason: '已经到达百度网盘文件列表，脚本仍等待模糊目标而超时', repairChanges: ['删除到达终态后的冗余等待，改为文件列表稳定态断言'], repairSource: 'ai_gateway', runnerId: 'win-runner-01', deviceId: 'ecbfd645', status: 'success', reportUrl: '/reports/job-rerun-document.html'},
              {sourceJobId: 'job-source-photo', newJobId: 'job-rerun-photo', targetTaskName: '照片打印 5寸照片入口', sourceFile: '03-photo.yaml', repairFile: '03-photo-repair.yaml', failureReason: '只停在照片打印父页面，未进入 5寸照片叶子页', repairChanges: ['参考成功照片基线补充父页面到 5寸照片的可见文字导航'], repairSource: 'ai_gateway', runnerId: 'win-runner-01', deviceId: 'ecbfd645', status: 'failed', resultReason: '目标页面不匹配'},
              {sourceJobId: 'job-source-chain', newJobId: 'job-rerun-chain', targetTaskName: '三业务入口跨页回归', sourceFile: '05-chain.yaml', repairFile: '05-chain-repair.yaml', failureReason: '长链路执行超时', repairChanges: ['拆分关键分叉点并修正滚动动作参数'], repairSource: 'ai_gateway', runnerId: 'win-runner-01', deviceId: 'ecbfd645', status: 'failed', resultReason: 'Midscene 动作参数校验失败'},
            ],
          },
          postRerunAutonomy: {analyzed: true, failureType: 'SCRIPT_ISSUE', repairGenerated: false, followupExecuted: false, reason: '最新失败已分析，动作参数门禁阻止无效二次下发'},
        },
      });
      expandedStepIndexes.clear();
      expandedStepIndexes.add(16);
      agentCheckpointTraceOpen = true;
      agentActiveTab = 'final';
      await showAgentWorkbench();
    });
    await page.waitForSelector('.agent-rerun-overview');
    if (await page.locator('.agent-rerun-item').count() !== 3) throw new Error('Rerun detail must retain all three serial task outcomes');
    if (!/成功 1/.test(await visibleText(page, '.agent-rerun-overview')) || !/失败 2/.test(await visibleText(page, '.agent-rerun-overview'))) throw new Error('Rerun aggregate must keep earlier successes visible');
    if (!await page.locator('.agent-rerun-item.status-success', {hasText: '文档打印百度网盘入口'}).isVisible()) throw new Error('Successful repair rerun is missing from the task-level result list');
    if (await page.locator('.agent-technical-trace').evaluate(el => el.open)) throw new Error('Technical rerun trace must be collapsed by default');
    if (await anyVisible(page.locator('text=_tool_rerun'))) throw new Error('Internal rerun function names must not appear in the primary result view');
    const rerunDesktopOverflow = await page.locator('.agent-rerun-overview').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (rerunDesktopOverflow) throw new Error('Rerun overview overflows horizontally on desktop');
    await page.screenshot({path: path.join(ARTIFACTS, 'agent-rerun.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(100);
    const rerunMobileOverflow = await page.locator('.agent-rerun-list').evaluate(el => el.scrollWidth > el.clientWidth + 1);
    if (rerunMobileOverflow) throw new Error('Task-level rerun evidence overflows horizontally on mobile');
    const mobileEvidenceColumns = await page.locator('.agent-rerun-evidence').first().evaluate(el => window.getComputedStyle(el).gridTemplateColumns.split(' ').length);
    if (mobileEvidenceColumns !== 1) throw new Error(`Rerun evidence must stack on mobile, columns=${mobileEvidenceColumns}`);
    await page.screenshot({path: path.join(ARTIFACTS, 'agent-rerun-mobile.png'), fullPage: true});
    const aggregateChecks = await page.evaluate(() => {
      const current = agentCurrentRun.artifacts.rerunProgress;
      const history = JSON.parse(JSON.stringify(current));
      history.source = 'original_yaml';
      history.usesRepairDraft = false;
      const followup = {
        ...current,
        total: 1,
        sourceFailedCount: 1,
        completedCount: 1,
        successCount: 1,
        failedCount: 0,
        items: [{
          sourceJobId: 'job-rerun-photo',
          newJobId: 'job-followup-photo',
          targetTaskName: '照片打印 5寸照片入口二次纠偏',
          repairSource: 'ai_gateway',
          runnerId: 'win-runner-01',
          deviceId: 'ecbfd645',
          status: 'success',
        }],
      };
      const holder = document.createElement('div');
      holder.innerHTML = renderRerunDetail({}, {
        ...agentCurrentRun.artifacts,
        rerunProgressHistory: [history],
        rerunProgress: followup,
      });
      const runnerHolder = document.createElement('div');
      runnerHolder.innerHTML = renderRunTaskDetail({}, {
        jobProgress: {phase: '扩展第1批', total: 1, completed: 0, failed: 1, running: 0, timeout: 1800, elapsed: 118, jobs: [{status: 'failed'}]},
        jobProgressByPhase: {
          '首批冒烟': {phase: '首批冒烟', total: 3, completed: 3, failed: 0, running: 0, timeout: 1800},
          '扩展第1批': {phase: '扩展第1批', total: 1, completed: 0, failed: 1, running: 0, timeout: 1800},
        },
      });
      const activeMetrics = agentRunnerProgressMetrics({total: 1, completed: 0, failed: 1, running: 0, timeout: 1800, jobs: [{status: 'failed'}]});
      const queuedMetrics = agentRunnerProgressMetrics({
        total: 2,
        running: 2,
        jobs: [{status: 'running'}, {status: 'pending'}],
      });
      const timeoutAndRunningMetrics = agentRunnerProgressMetrics({
        total: 2,
        timeoutCount: 1,
        jobs: [{status: 'timeout'}, {status: 'running'}],
      });
      const runningRerunHolder = document.createElement('div');
      runningRerunHolder.innerHTML = renderStepDetail(
        {step: 'RERUN', status: 'RUNNING', toolCalls: []},
        {artifacts: {rerunProgress: {
          source: 'original_yaml',
          usesRepairDraft: false,
          total: 2,
          completedCount: 0,
          runningCount: 2,
          pendingCount: 0,
          serialSameDevice: true,
          runnerId: 'win-runner-01',
          deviceId: 'ecbfd645',
          items: [
            {sourceJobId: 'job-running', targetTaskName: '照片入口证据重试', status: 'running', repairSource: 'original_yaml'},
            {sourceJobId: 'job-queued', targetTaskName: '扫描入口证据重试', status: 'pending', repairSource: 'original_yaml'},
          ],
        }}},
      );
      const summaryHolder = document.createElement('div');
      summaryHolder.innerHTML = renderAgentSummaryArtifact({
        status: 'FAILED',
        target: '基础打印新增百度网盘入口',
        steps: [{step: 'RUN_TASK', status: 'PARTIAL_FAILED', summary: '扩展任务失败'}],
        artifacts: {
          summary: {title: '基础打印新增百度网盘入口 - 执行总结', conclusion: '未通过', completed: 12, totalSteps: 20},
          report: {
            status: 'failed',
            successJobs: [{jobId: 'smoke-1'}, {jobId: 'smoke-2'}],
            failedJobs: [{jobId: 'expanded-1', status: 'failed'}],
            timeoutJobs: [],
            runningJobs: [],
            jobStatuses: [],
          },
        },
      });
      return {rerunText: holder.innerText, runnerText: runnerHolder.innerText, runningRerunText: runningRerunHolder.innerText, summaryText: summaryHolder.innerText, activeMetrics, queuedMetrics, timeoutAndRunningMetrics};
    });
    if (!/尝试 2 轮/.test(aggregateChecks.rerunText) || !/成功 2/.test(aggregateChecks.rerunText) || !/失败 2/.test(aggregateChecks.rerunText) || !/原脚本证据重试/.test(aggregateChecks.rerunText) || !/AI 修复脚本验证/.test(aggregateChecks.rerunText)) throw new Error('Bounded AI repair cycles must use a cumulative causal attempt chain');
    if (!/Runner 真实执行累计/.test(aggregateChecks.runnerText) || !/成功\s*3/.test(aggregateChecks.runnerText) || !/失败\s*1/.test(aggregateChecks.runnerText)) throw new Error('Runner phase history must keep earlier successful execution visible');
    if (aggregateChecks.activeMetrics.timeout !== 0 || aggregateChecks.activeMetrics.timeoutSeconds !== 1800) throw new Error('Runner timeout limit must not be rendered as 1800 timed-out jobs');
    if (aggregateChecks.queuedMetrics.running !== 1 || aggregateChecks.queuedMetrics.pending !== 1) throw new Error(`Runner progress must split executing and queued jobs: ${JSON.stringify(aggregateChecks.queuedMetrics)}`);
    if (aggregateChecks.timeoutAndRunningMetrics.running !== 1 || aggregateChecks.timeoutAndRunningMetrics.timeout !== 1) throw new Error(`A terminal timeout must not hide another executing job: ${JSON.stringify(aggregateChecks.timeoutAndRunningMetrics)}`);
    if (!/1 执行中/.test(aggregateChecks.runningRerunText) || !/1 排队中/.test(aggregateChecks.runningRerunText) || !/原脚本证据重试/.test(aggregateChecks.runningRerunText)) throw new Error('A running RERUN step must render causal task progress before toolCalls finish');
    if (!/部分通过/.test(aggregateChecks.summaryText) || !/编排阻断/.test(aggregateChecks.summaryText) || !/Runner 通过\s*2/.test(aggregateChecks.summaryText) || !/脚本 \/ 环境 \/ 待归因\s*1/.test(aggregateChecks.summaryText)) throw new Error('Final summary must preserve successful smoke outcomes and keep unclassified test failures separate from product failures');
    await page.setViewportSize({width: 1440, height: 900});
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
        path.join(ARTIFACTS, 'metersphere-execution.png'),
        path.join(ARTIFACTS, 'metersphere-execution-mobile.png'),
        path.join(ARTIFACTS, 'metersphere-settings.png'),
        path.join(ARTIFACTS, 'metersphere-settings-mobile.png'),
        path.join(ARTIFACTS, 'api-source-settings.png'),
        path.join(ARTIFACTS, 'api-source-settings-mobile.png'),
        path.join(ARTIFACTS, 'api-project-switch.png'),
        path.join(ARTIFACTS, 'api-project-switch-mobile.png'),
        path.join(ARTIFACTS, 'api-module-tree.png'),
        path.join(ARTIFACTS, 'api-module-tree-mobile.png'),
        path.join(ARTIFACTS, 'api-assets-sync.png'),
        path.join(ARTIFACTS, 'api-assets-sync-mobile.png'),
        path.join(ARTIFACTS, 'api-plan-readiness.png'),
        path.join(ARTIFACTS, 'api-plan-readiness-mobile.png'),
        path.join(ARTIFACTS, 'api-batch-review.png'),
        path.join(ARTIFACTS, 'api-batch-review-mobile.png'),
        path.join(ARTIFACTS, 'api-business-auth.png'),
        path.join(ARTIFACTS, 'api-business-auth-mobile.png'),
        path.join(ARTIFACTS, 'metersphere-project-binding.png'),
        path.join(ARTIFACTS, 'metersphere-project-binding-mobile.png'),
        path.join(ARTIFACTS, 'agent.png'),
        path.join(ARTIFACTS, 'agent-mobile.png'),
        path.join(ARTIFACTS, 'agent-failure.png'),
        path.join(ARTIFACTS, 'agent-failure-mobile.png'),
        path.join(ARTIFACTS, 'agent-rerun.png'),
        path.join(ARTIFACTS, 'agent-rerun-mobile.png'),
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
