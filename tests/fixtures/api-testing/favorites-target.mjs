import { spawn, spawnSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../..')
const PYTHON = process.env.API_TESTING_PYTHON || path.join(ROOT, '.venv/bin/python')
const DATABASE_URL = process.env.TEST_DATABASE_URL
  || 'postgresql+psycopg://midscene:task5-test-postgres-only@127.0.0.1:5432/midscene_api_testing'
const REDIS_URL = process.env.TEST_REDIS_URL || 'redis://127.0.0.1:6379/15'
const SECRET = `e2e-${randomBytes(24).toString('hex')}`

export async function startFavoritesAcceptance() {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), 'midscene-api-e2e-'))
  let target
  let schema
  let server
  let worker
  try {
    target = await startTargetServer()
    const platformPort = await freePort()
    schema = createSchema()
    const queue = `api-testing-e2e-${Date.now().toString(36)}`
    const openApiPath = path.join(temporaryRoot, 'favorites-openapi.json')
    const source = JSON.parse(await readFile(path.join(ROOT, 'tests/api_testing/fixtures/my_favorites_openapi.json'), 'utf8'))
    source.servers = [{ url: `${target.baseUrl}/app`, description: '本地验收环境' }]
    await writeFile(openApiPath, JSON.stringify(source, null, 2), 'utf8')
    flushRedis()

    const runtimeDirectories = Object.fromEntries(
      ['TASK_DIR', 'REPORT_DIR', 'LEARNING_DIR', 'ASSET_DIR', 'CASE_DIR', 'GENERATE_JOB_DIR', 'KNOWLEDGE_DIR']
        .map(name => [name, path.join(temporaryRoot, name.toLowerCase())]),
    )
    const environment = {
    ...process.env,
    ...runtimeDirectories,
    PYTHONUNBUFFERED: '1',
    MIDSCENE_ENV_FILE: '/dev/null',
    PORT: String(platformPort),
    TASK_APP_ENV: 'test',
    TASK_ADMIN_USER: 'admin',
    TASK_ADMIN_PASSWORD: 'sonic2026',
    TASK_ADMIN_PASSWORD_HASH: '',
    TASK_SESSION_SECRET: 'e2e-session-42f6b0fc9a2d4f8fa19ad2a97acb792b',
    MIDSCENE_RUNNER_TOKEN: 'e2e-runner-5bdd155e65014f25a10e205f4c20cf36',
    SONIC_CALLBACK_TOKEN: 'e2e-sonic-callback-bf1e6d450f794501b29f19c6c1bcd760',
    API_TESTING_ENABLED: '1',
    API_TESTING_DATABASE_URL: schema.url,
    API_TESTING_REDIS_URL: REDIS_URL,
    API_TESTING_SECRET_KEY: 'e2e-encryption-e5418a9f3c31483ca8c42fedc8d27b86',
    API_TESTING_QUEUE: queue,
    API_TESTING_TEST_ALLOWED_HOSTS: '127.0.0.1,localhost',
    API_TESTING_AI_PROVIDER_ID: 'qwen_plus',
    API_TESTING_AI_MODEL: 'qwen3.7-plus',
    AI_GATEWAY_URL: target.baseUrl,
    MIDSCENE_REPORT_CLEANUP_ON_STARTUP: '0',
    }
    const captured = []
    server = startProcess([PYTHON, '-m', 'task_server'], environment, captured)
    await waitForHealth(`http://127.0.0.1:${platformPort}/api/health`, server, captured)
    worker = startProcess([
      PYTHON, '-m', 'celery', '-A', 'task_server.api_testing.tasks:celery_app',
      'worker', '--loglevel=WARNING', `--queues=${queue}`, '--concurrency=1', '--pool=solo',
    ], environment, captured)
    await waitForWorker(worker, captured)

    let closed = false
    return {
    platformUrl: `http://127.0.0.1:${platformPort}`,
    openApiPath,
    secret: SECRET,
    useRegressionResponses: target.useRegressionResponses,
    output: () => sanitize(captured.join('\n')),
    async readLatestExecution(page) {
      return page.evaluate(async () => {
        const token = sessionStorage.getItem('sessionToken') || ''
        const headers = { Authorization: `Bearer ${token}` }
        const workspaceResponse = await fetch('/api/api-testing/v1/workspace', { headers })
        const workspacePayload = await workspaceResponse.json()
        const projectId = workspacePayload.data.workspace.project_id
        const listResponse = await fetch(`/api/api-testing/v1/executions?project_id=${encodeURIComponent(projectId)}&limit=1`, { headers })
        const listPayload = await listResponse.json()
        const executionId = listPayload.data.executions[0].id
        const detailResponse = await fetch(`/api/api-testing/v1/executions/${executionId}`, { headers })
        return (await detailResponse.json()).data.execution
      })
    },
      async close() {
        if (closed) return
        closed = true
        await stopProcess(worker)
        await stopProcess(server)
        await target.close()
        dropSchema(schema.name)
        flushRedis()
        await rm(temporaryRoot, { recursive: true, force: true })
      },
    }
  } catch (error) {
    await stopProcess(worker)
    await stopProcess(server)
    if (target) await target.close()
    if (schema) dropSchema(schema.name)
    await rm(temporaryRoot, { recursive: true, force: true })
    throw error
  }
}

async function startTargetServer() {
  let regression = false
  const audit = []
  const server = http.createServer(async (request, response) => {
    const body = await readBody(request)
    const url = new URL(request.url || '/', 'http://127.0.0.1')
    audit.push(`${request.method} ${url.pathname}`)
    if (url.pathname === '/ai/api-case-generation') {
      const payload = parseJson(body)
      const messages = Array.isArray(payload.messages) ? payload.messages : []
      const system = String(messages[0]?.content || '')
      if (system.includes('失败分析器')) {
        return sendJson(response, 200, {
          success: true,
          providerId: 'qwen_plus',
          model: 'qwen3.7-plus',
          fallbackUsed: false,
          fallbackIndex: 0,
          fallbackReason: '',
          content: JSON.stringify({
            summary: '真实执行结果未满足预期',
            root_cause: '响应断言失败或网络连接中断',
            recommendations: ['核对业务响应与测试环境连通性'],
            evidence: ['来自本次脱敏请求、响应和断言结果'],
          }),
        })
      }
      const userPayload = parseJson(String(messages.at(-1)?.content || '{}'))
      const endpoints = Array.isArray(userPayload.endpoints) ? userPayload.endpoints : []
      return sendJson(response, 200, {
        success: true,
        providerId: 'qwen_plus',
        model: 'qwen3.7-plus',
        fallbackUsed: false,
        fallbackIndex: 0,
        fallbackReason: '',
        content: JSON.stringify({ candidates: endpoints.map(candidateForEndpoint) }),
      })
    }
    if (url.pathname.endsWith('/favorite/cancel') && regression) {
      request.socket.destroy()
      return
    }
    if (url.pathname.endsWith('/favorite/list')) {
      return sendJson(response, 200, { code: 0, data: [{ id: 'favorite-001' }] })
    }
    if (url.pathname.endsWith('/favorite/add')) {
      return sendJson(response, 200, regression ? { code: 4009, message: '业务拒绝收藏' } : { code: 0, message: 'ok' })
    }
    if (url.pathname.endsWith('/favorite/cancel')) {
      return sendJson(response, 200, { code: 0, message: 'ok' })
    }
    sendJson(response, 404, { code: 404, message: 'not found' })
  })
  await listen(server)
  const address = server.address()
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    useRegressionResponses() { regression = true },
    output() { return audit.join('\n') },
    close: () => closeServer(server),
  }
}

function candidateForEndpoint(endpoint) {
  const isList = endpoint.method === 'GET'
  return {
    endpoint_id: endpoint.endpoint_id,
    case: {
      name: `${endpoint.summary}-成功响应`,
      purpose: `验证${endpoint.summary}的状态码和业务码`,
      priority: 'P0',
      request: {
        method: endpoint.method,
        path: endpoint.path,
        service: 'default',
        path_params: {},
        query: isList ? { pageNum: 1 } : {},
        headers: { Biz: '{{Biz}}' },
        cookies: {},
        body: isList ? null : { targetId: 'synthetic-model-001', favoriteType: 'MODEL' },
      },
      data_rows: [],
      assertions: [
        { type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true },
        { type: 'json_path', operator: 'equals', path: '$.code', expected: 0, timeout_ms: 0, enabled: true },
      ],
      extractions: [],
      dependencies: [],
      processing: { pre: [], post: [] },
    },
  }
}

function createSchema() {
  const script = `
import json, sys, uuid
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from tests.api_testing.test_migrations import _alembic_config
base = sys.argv[1]
name = 'test_api_testing_' + uuid.uuid4().hex
engine = create_engine(base, isolation_level='AUTOCOMMIT')
with engine.connect() as connection:
    connection.execute(text('CREATE SCHEMA "' + name + '"'))
url = make_url(base).set(query={'options': '-csearch_path=' + name}).render_as_string(hide_password=False)
command.upgrade(_alembic_config(url), 'head')
print(json.dumps({'name': name, 'url': url}))
`
  const result = run(PYTHON, ['-c', script, DATABASE_URL])
  return JSON.parse(result.stdout.trim().split('\n').at(-1))
}

function dropSchema(name) {
  if (!/^test_api_testing_[0-9a-f]{32}$/.test(name)) throw new Error('refusing to drop non-test schema')
  const script = `
import sys
from sqlalchemy import create_engine, text
name = sys.argv[2]
if not name.startswith('test_api_testing_'):
    raise SystemExit('unsafe schema')
engine = create_engine(sys.argv[1], isolation_level='AUTOCOMMIT')
with engine.connect() as connection:
    connection.execute(text('DROP SCHEMA "' + name + '" CASCADE'))
`
  run(PYTHON, ['-c', script, DATABASE_URL, name])
}

function flushRedis() {
  const script = 'import redis,sys; redis.Redis.from_url(sys.argv[1]).flushdb()'
  run(PYTHON, ['-c', script, REDIS_URL])
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: ROOT, encoding: 'utf8', env: process.env })
  if (result.status !== 0) throw new Error(`${command} failed: ${sanitize(result.stderr || result.stdout)}`)
  return result
}

function startProcess(command, environment, captured) {
  const child = spawn(command[0], command.slice(1), { cwd: ROOT, env: environment, stdio: ['ignore', 'pipe', 'pipe'] })
  child.stdout.on('data', chunk => captured.push(sanitize(String(chunk))))
  child.stderr.on('data', chunk => captured.push(sanitize(String(chunk))))
  return child
}

async function waitForHealth(url, process, captured) {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`Task server exited early: ${sanitize(captured.join('\n'))}`)
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {}
    await delay(250)
  }
  throw new Error(`Task server health timeout: ${sanitize(captured.join('\n'))}`)
}

async function waitForWorker(process, captured) {
  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`API worker exited early: ${sanitize(captured.join('\n'))}`)
    if (captured.some(line => line.includes('ready') || line.includes('.> app:'))) {
      await delay(500)
      if (process.exitCode === null) return
    }
    await delay(200)
  }
  throw new Error(`API worker readiness timeout: ${sanitize(captured.join('\n'))}`)
}

async function stopProcess(process) {
  if (!process || process.exitCode !== null) return
  process.kill('SIGTERM')
  await Promise.race([
    new Promise(resolve => process.once('exit', resolve)),
    delay(5_000).then(() => { if (process.exitCode === null) process.kill('SIGKILL') }),
  ])
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port
      server.close(error => error ? reject(error) : resolve(port))
    })
  })
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
}

function closeServer(server) {
  return new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = []
    request.on('data', chunk => chunks.push(chunk))
    request.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    request.on('error', reject)
  })
}

function parseJson(value) {
  try { return JSON.parse(value || '{}') } catch { return {} }
}

function sendJson(response, status, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) })
  response.end(body)
}

function sanitize(value) {
  return String(value || '').split(SECRET).join('***')
}

function delay(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}
