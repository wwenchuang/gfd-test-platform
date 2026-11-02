import { createHash } from 'node:crypto';

const MAX_SOURCE_BYTES = 12 * 1024 * 1024;
const MAX_POLICY_BYTES = 64 * 1024;
const OFFICIAL_ORIGINS = new Set(['https://dashscope.aliyuncs.com', 'https://dashscope-intl.aliyuncs.com', 'https://dashscope-us.aliyuncs.com']);
const hash = (kind, value) => createHash(kind).update(value).digest('hex');
const failure = code => Object.assign(new Error(code), { code });
const invalidPolicy = () => { throw failure('video_native_policy_invalid'); };

async function boundedPolicy(response, bounded) {
  if (!response.body || Number(response.headers.get('content-length')) > MAX_POLICY_BYTES) invalidPolicy();
  const reader = response.body.getReader();
  let size = 0;
  const chunks = [];
  try {
    while (true) {
      let item;
      try { item = await bounded(reader.read()); }
      catch { throw failure('video_stage_provider_failed'); }
      const { done, value } = item;
      if (done) break;
      size += value.byteLength;
      if (size > MAX_POLICY_BYTES) invalidPolicy();
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } finally {
    // Do not await a remote body's cancellation; it must not extend the deadline.
    reader.cancel().catch(() => {});
  }
}

function validatePolicy(data, bytes, objectKey) {
  if (!data || typeof data !== 'object') invalidPolicy();
  let host;
  try { host = new URL(data.upload_host); } catch { invalidPolicy(); }
  if (host.protocol !== 'https:' || host.username || host.password || host.port || host.search || host.hash || host.pathname !== '/' ||
      !/^dashscope-file(?:-[a-z0-9]+)*\.oss-[a-z0-9-]+\.aliyuncs\.com$/.test(host.hostname)) invalidPolicy();
  if (typeof data.upload_dir !== 'string' || !/^dashscope-instant(?:\/[A-Za-z0-9_-]+)+$/.test(data.upload_dir)) invalidPolicy();
  if (data.x_oss_object_acl !== 'private' || !['true', true].includes(data.x_oss_forbid_overwrite)) invalidPolicy();
  if (!Number.isFinite(data.expire_in_seconds) || data.expire_in_seconds <= 0 || !Number.isFinite(data.max_file_size_mb) || bytes > data.max_file_size_mb * 1024 * 1024) invalidPolicy();
  for (const key of ['oss_access_key_id', 'signature', 'policy']) {
    if (typeof data[key] !== 'string' || !data[key] || data[key].length > MAX_POLICY_BYTES) invalidPolicy();
  }
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data.policy)) invalidPolicy();
  let signed;
  try { signed = JSON.parse(Buffer.from(data.policy, 'base64').toString('utf8')); } catch { invalidPolicy(); }
  if (!signed || typeof signed !== 'object' || !Number.isFinite(Date.parse(signed.expiration)) || Date.parse(signed.expiration) <= Date.now()) invalidPolicy();
  if (signed.conditions !== undefined && !Array.isArray(signed.conditions)) invalidPolicy();
  // OSS enforces the signature. Check recognizable constraints without depending on
  // undocumented policy condition order or requiring one particular encoding.
  const key = `${data.upload_dir}/${objectKey}`;
  const fields = { key, 'x-oss-object-acl': 'private', 'x-oss-forbid-overwrite': 'true', success_action_status: '200' };
  for (const condition of signed.conditions || []) {
    if (Array.isArray(condition)) {
      const [operation, field, value] = condition;
      if (operation === 'content-length-range' && (!Number.isFinite(field) || !Number.isFinite(value) || bytes < field || bytes > value)) invalidPolicy();
      if (['eq', 'starts-with'].includes(operation) && typeof field === 'string' && Object.hasOwn(fields, field.replace(/^\$/, ''))) {
        const actual = fields[field.replace(/^\$/, '')];
        if (typeof value !== 'string' || (operation === 'eq' ? actual !== value : !actual.startsWith(value))) invalidPolicy();
      }
    } else if (condition && typeof condition === 'object') {
      for (const [name, value] of Object.entries(condition)) {
        if (Object.hasOwn(fields, name) && fields[name] !== value) invalidPolicy();
      }
    }
  }
  return { host: host.origin, key };
}

/** Upload the exact validated original to the official private temporary resource. */
export async function uploadNativeVideo({ source, sourceSha256, sourceBytes, model, baseUrl, apiKey, correlation, deadlineMs, signal, fetchImpl = globalThis.fetch }) {
  const now = Date.now();
  if (!Buffer.isBuffer(source) || source.length < 12 || source.length > MAX_SOURCE_BYTES || source.toString('ascii', 4, 8) !== 'ftyp' ||
      sourceBytes !== source.length || !/^[a-f0-9]{64}$/.test(sourceSha256) || hash('sha256', source) !== sourceSha256 ||
      model !== 'qwen3.8-max' || !/^[a-f0-9]{32}$/.test(correlation) || typeof apiKey !== 'string' || !apiKey.trim() || /[\r\n]/.test(apiKey) ||
      !Number.isFinite(deadlineMs) || deadlineMs > now + 120000 || typeof fetchImpl !== 'function') throw failure('video_native_input_invalid');
  let base;
  try { base = new URL(baseUrl); } catch { throw failure('video_native_input_invalid'); }
  if (!OFFICIAL_ORIGINS.has(base.origin) || base.username || base.password || base.search || base.hash || base.pathname !== '/compatible-mode/v1') throw failure('video_native_input_invalid');
  if (signal?.aborted) throw failure('video_native_cancelled');
  if (deadlineMs <= now) throw failure('video_native_deadline');

  // Keep the validated bytes stable while policy retrieval awaits network I/O.
  source = Buffer.from(source);
  const controller = new AbortController();
  let abortCode;
  let rejectAbort;
  const aborted = new Promise((_, reject) => { rejectAbort = reject; });
  // The race below consumes this rejection even if the caller aborts synchronously.
  aborted.catch(() => {});
  const abort = code => {
    if (abortCode) return;
    abortCode = code;
    controller.abort();
    rejectAbort(failure(code));
  };
  const onCancel = () => abort('video_native_cancelled');
  const timer = setTimeout(() => abort('video_native_deadline'), Math.max(0, deadlineMs - Date.now()));
  signal?.addEventListener('abort', onCancel, { once: true });
  const check = () => {
    if (signal?.aborted) abort('video_native_cancelled');
    if (Date.now() >= deadlineMs) abort('video_native_deadline');
    if (abortCode) throw failure(abortCode);
  };
  const bounded = async promise => { const result = await Promise.race([promise, aborted]); check(); return result; };
  const responses = new Set();
  let closed = false;
  const cancelBody = response => {
    // A policy reader owns locked bodies and cancels them in boundedPolicy.
    // Cancellation itself may reject or stall; neither may escape this cleanup.
    try {
      if (response.body && !response.body.locked) Promise.resolve(response.body.cancel()).catch(() => {});
    } catch {}
  };
  const fetchResponse = (url, options) => bounded(Promise.resolve().then(() => fetchImpl(url, options)).then(response => {
    // An injected/uncooperative fetch can resolve after the deadline race ended.
    if (closed) cancelBody(response);
    else responses.add(response);
    return response;
  }, error => {
    // Native fetch rejects redirect:error before returning a Response. Keep that
    // security rejection hard instead of treating it as an optional outage.
    throw failure(error?.cause?.message === 'unexpected redirect' ? `video_native_${phase}_failed` : 'video_stage_provider_failed');
  }));
  let phase = 'policy';
  try {
    check();
    const response = await fetchResponse(`${base.origin}/api/v1/uploads?action=getPolicy&model=qwen3.8-max`, {
      method: 'GET', headers: { Authorization: `Bearer ${apiKey}` }, redirect: 'error', signal: controller.signal,
    });
    if (!response.redirected && (response.status === 429 || response.status >= 500 && response.status <= 599)) throw failure('video_stage_provider_failed');
    if (response.status !== 200 || response.redirected) throw failure('video_native_policy_failed');
    const policy = await boundedPolicy(response, bounded);
    const data = policy?.data;
    const filename = `${correlation}.mp4`;
    const { host, key } = validatePolicy(data, sourceBytes, filename);
    check();
    const form = new FormData();
    for (const [name, value] of Object.entries({ OSSAccessKeyId: data.oss_access_key_id, Signature: data.signature, policy: data.policy,
      'x-oss-object-acl': 'private', 'x-oss-forbid-overwrite': 'true', key, success_action_status: '200' })) form.append(name, value);
    form.append('file', new Blob([source], { type: 'video/mp4' }), filename);
    // No Bearer key is sent to OSS. Signed policy fields authorize this exact POST.
    phase = 'upload';
    check();
    const upload = await fetchResponse(host, { method: 'POST', body: form, redirect: 'error', signal: controller.signal });
    if (!upload.redirected && (upload.status === 429 || upload.status >= 500 && upload.status <= 599)) throw failure('video_stage_provider_failed');
    if (upload.status !== 200 || upload.redirected) throw failure('video_native_upload_failed');
    const sourceMd5 = hash('md5', source);
    const etag = upload.headers.get('etag');
    if (!etag || !new RegExp(`^(?:${sourceMd5}|"${sourceMd5}")$`, 'i').test(etag)) throw failure('video_native_integrity');
    check();
    const uri = `oss://${key}`;
    return { version: 'dashscope-temporary-original-v1', uri, source_sha256: sourceSha256, source_bytes: sourceBytes, source_md5: sourceMd5,
      model, correlation, resource_reference_sha256: hash('sha256', uri), uploaded_at_ms: Date.now(), documented_max_lifetime_seconds: 172800,
      private_acl: true, forbid_overwrite: true, resolve_header: 'enable' };
  } catch (error) {
    check();
    const allowed = new Set(['video_stage_provider_failed', 'video_native_policy_invalid', 'video_native_policy_failed', 'video_native_upload_failed', 'video_native_integrity']);
    if (allowed.has(error?.code)) throw failure(error.code);
    throw failure(`video_native_${phase}_failed`);
  } finally {
    closed = true;
    for (const response of responses) cancelBody(response);
    responses.clear();
    controller.abort();
    clearTimeout(timer);
    signal?.removeEventListener('abort', onCancel);
  }
}
