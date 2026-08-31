import {isIP} from 'node:net';

const GENERATION_ROUTES = new Set([
  '/ai/validate-yaml', '/ai/generate-yaml', '/ai/generate-case', '/ai/skill',
  '/ai/analyze-failure', '/ai/optimize-yaml', '/ai/chat', '/ai/api-case-generation',
  '/ai/generate-bug',
]);
const EXTERNAL_HEADERS = new Set([
  'authorization', 'origin', 'forwarded', 'x-real-ip', 'sec-fetch-site', 'referer', 'cookie',
]);

function isLoopback(address) {
  if (typeof address !== 'string') return false;
  const normalized = address.toLowerCase().replace(/^::ffff:/, '');
  return normalized === '::1' || (isIP(normalized) === 4 && normalized.startsWith('127.'));
}

function isTrustedInternal(req) {
  // Only the actual TCP peer is trusted, never Express req.ip or forwarded values.
  return isLoopback(req.socket?.remoteAddress) && !Object.keys(req.headers || {}).some((name) => (
    EXTERNAL_HEADERS.has(name.toLowerCase()) || name.toLowerCase().startsWith('x-forwarded-')
  ));
}

function authEndpoint(baseUrl) {
  try {
    const url = new URL(baseUrl);
    const hostname = url.hostname.replace(/^\[|\]$/g, '');
    if (!['http:', 'https:'].includes(url.protocol)
      || !(isLoopback(hostname) || hostname === 'localhost')
      || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
      throw new Error();
    }
    return new URL('/api/auth/me', url).href;
  } catch {
    throw new Error('AI_GATEWAY_AUTH_BASE_URL must be a loopback HTTP(S) origin without credentials, path, query or fragment');
  }
}

function requiredPermissions(method, route) {
  if (method === 'GET' || method === 'HEAD') {
    if (route === '/ai/providers' || route === '/ai/model-router') return ['ui.view', 'api.view'];
  }
  if (method === 'POST') {
    if (route === '/ai/model-router' || route === '/ai/providers/test') return ['platform.configure'];
    if (GENERATION_ROUTES.has(route)) return ['ui.edit', 'api.edit'];
  }
  return [];
}

function isAgentRoute(method, route) {
  return ((method === 'GET' || method === 'HEAD') && /^\/agent\/runs(?:\/[^/]+)?$/.test(route))
    || (method === 'POST' && (route === '/agent/run' || /^\/agent\/runs\/[^/]+\/(confirm|cancel)$/.test(route)));
}

function deny(res, status, code) {
  return res.status(status).json({success: false, code, error: code});
}

export function createGatewayAuth({
  baseUrl = process.env.AI_GATEWAY_AUTH_BASE_URL || 'http://127.0.0.1:8091',
  timeoutMs = process.env.AI_GATEWAY_AUTH_TIMEOUT_MS,
  fetchImpl = globalThis.fetch,
} = {}) {
  const endpoint = authEndpoint(baseUrl);
  const configuredTimeout = Number(timeoutMs);
  const timeout = Number.isFinite(configuredTimeout)
    ? Math.max(100, Math.min(5000, configuredTimeout)) : 3000;

  return async (req, res, next) => {
    if (isTrustedInternal(req)) return next();
    res.set('Cache-Control', 'no-store');
    // Match Express's default case-insensitive routing and optional trailing slash.
    const route = String(req.path || '').toLowerCase().replace(/\/$/, '');
    if ((req.method === 'GET' || req.method === 'HEAD') && route === '/health') {
      return res.json({ok: true, service: 'ai-gateway'});
    }

    const authorization = req.headers?.authorization;
    const match = typeof authorization === 'string'
      ? /^Bearer[ \t]+([A-Za-z0-9._~+/-]+=*)$/i.exec(authorization) : null;
    if (!match) return deny(res, 401, 'authentication_required');

    const controller = new AbortController();
    let timer;
    let result;
    try {
      // The deadline includes response body parsing; no token or profile is cached.
      result = await Promise.race([
        (async () => {
          const response = await fetchImpl(endpoint, {
            method: 'GET',
            headers: {Authorization: `Bearer ${match[1]}`, Accept: 'application/json'},
            signal: controller.signal,
            redirect: 'error',
            cache: 'no-store',
          });
          return {status: response.status, payload: response.status === 200 ? await response.json() : null};
        })(),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error('Auth lookup timed out'));
          }, timeout);
        }),
      ]);
    } catch {
      return deny(res, 503, 'authentication_unavailable');
    } finally {
      clearTimeout(timer);
      controller.abort();
    }

    if (result.status === 401) return deny(res, 401, 'authentication_required');
    if (result.status === 403) return deny(res, 403, 'permission_denied');
    const payload = result.payload;
    const profile = payload?.profile ?? payload;
    if (result.status !== 200 || payload?.ok !== true
      || typeof profile?.is_superuser !== 'boolean'
      || typeof profile?.must_change_password !== 'boolean'
      || !Array.isArray(profile?.permissions)
      || !profile.permissions.every((permission) => typeof permission === 'string')) {
      return deny(res, 503, 'authentication_unavailable');
    }
    if (profile.must_change_password) return deny(res, 403, 'password_change_required');
    if (profile.is_superuser) return next();
    // Agent tools can read global cases/reports; caller app IDs cannot prove scope.
    if (isAgentRoute(req.method, route)) {
      const allowed = profile.scope?.ui_apps === '*'
        && ['platform.configure', 'ui.execute'].every((permission) => profile.permissions.includes(permission));
      return allowed ? next() : deny(res, 403, 'permission_denied');
    }
    if (requiredPermissions(req.method, route).some((permission) => profile.permissions.includes(permission))) return next();
    return deny(res, 403, 'permission_denied');
  };
}
