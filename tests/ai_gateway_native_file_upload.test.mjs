import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

const digest = (kind, bytes) => createHash(kind).update(bytes).digest('hex');
const source = Buffer.concat([Buffer.from([0,0,0,24]), Buffer.from('ftypisom'), Buffer.alloc(20, 7)]);
const correlation = '1234567890abcdef1234567890abcdef';
const secret = 'never-echo-account-secret';
const args = () => ({ source, sourceSha256: digest('sha256', source), sourceBytes: source.length, model: 'qwen3.8-max', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', apiKey: secret, correlation, deadlineMs: Date.now() + 1000 });
const policy = () => ({ data: { upload_host: 'https://dashscope-file.oss-cn-beijing.aliyuncs.com', upload_dir: 'dashscope-instant/account/model', oss_access_key_id: 'signed-access', signature: 'signed-secret', policy: Buffer.from(JSON.stringify({ expiration: new Date(Date.now()+60000).toISOString(), conditions: [['content-length-range',1,1024], ['starts-with','$key','dashscope-instant/account/model/'], {'x-oss-object-acl':'private'}, {'x-oss-forbid-overwrite':'true'}] })).toString('base64'), x_oss_object_acl: 'private', x_oss_forbid_overwrite: 'true', expire_in_seconds: 60, max_file_size_mb: 1 } });
const mod = () => import('../ai-gateway/native-video-upload.js');
const safeFailure = async (promise, code) => assert.rejects(promise, error => { assert.match(error.code, code); assert.equal(error.message, error.code); assert.doesNotMatch(JSON.stringify(error), /never-echo|signed-secret|oss:\/\//); return true; });
const successfulFetch = (calls, p=policy()) => async (url, init) => { calls.push({url, init}); return calls.length % 2 ? new Response(JSON.stringify(p)) : new Response(null, {status:200, headers:{etag:`"${digest('md5',source)}"`}}); };

test('original bytes, official account policy, private file-last form and exact ephemeral envelope', async () => {
 const {uploadNativeVideo} = await mod(); const calls=[]; const input=args(); const result=await uploadNativeVideo({...input,fetchImpl:successfulFetch(calls)});
 assert.equal(calls.length,2); assert.equal(calls[0].url,'https://dashscope.aliyuncs.com/api/v1/uploads?action=getPolicy&model=qwen3.8-max');
 assert.equal(calls[0].init.headers.Authorization,`Bearer ${secret}`);
 assert.equal(calls[1].url,policy().data.upload_host); assert.equal(calls[1].init.method,'POST'); assert.equal(calls[1].init.headers,undefined);
 assert.equal(calls[0].init.signal,calls[1].init.signal); assert.ok(calls[0].init.signal instanceof AbortSignal);
 for(const call of calls) assert.equal(call.init.redirect,'error');
 const entries=[...calls[1].init.body.entries()]; assert.equal(entries.at(-1)[0],'file'); const file=entries.at(-1)[1];
 assert.deepEqual(Buffer.from(await file.arrayBuffer()),source); assert.equal(file.name,`${correlation}.mp4`); assert.equal(file.type,'video/mp4');
 const form=Object.fromEntries(entries); assert.equal(form.key,`dashscope-instant/account/model/${correlation}.mp4`); assert.equal(form['x-oss-object-acl'],'private'); assert.equal(form['x-oss-forbid-overwrite'],'true'); assert.equal(form.success_action_status,'200');
 assert.deepEqual(Object.keys(result).sort(),['version','uri','source_sha256','source_bytes','source_md5','model','correlation','resource_reference_sha256','uploaded_at_ms','documented_max_lifetime_seconds','private_acl','forbid_overwrite','resolve_header'].sort());
 assert.equal(result.uri,`oss://${form.key}`); assert.equal(result.version,'dashscope-temporary-original-v1'); assert.equal(result.resource_reference_sha256,digest('sha256',result.uri)); assert.equal(result.source_sha256,input.sourceSha256); assert.equal(result.source_md5,digest('md5',source)); assert.equal(result.source_bytes,source.length); assert.equal(result.model,input.model); assert.equal(result.correlation,correlation); assert.equal(result.private_acl,true); assert.equal(result.forbid_overwrite,true); assert.equal(result.resolve_header,'enable'); assert.equal(result.documented_max_lifetime_seconds,172800); assert.ok(result.uploaded_at_ms>=input.deadlineMs-1000 && result.uploaded_at_ms<=Date.now());
});

for(const [name, change] of Object.entries({hash:a=>a.sourceSha256='0'.repeat(64),size:a=>a.sourceBytes++,buffer:a=>a.source=new Uint8Array(source),ftyp:a=>{a.source=Buffer.alloc(32);a.sourceSha256=digest('sha256',a.source)},oversize:a=>{a.source=Buffer.alloc(12*1024*1024+1);a.sourceBytes=a.source.length;a.sourceSha256=digest('sha256',a.source)},model:a=>a.model='qwen3-max',region:a=>a.baseUrl='https://evil.invalid/compatible-mode/v1',query:a=>a.baseUrl+='?key=foo',credentials:a=>a.baseUrl='https://x:y@dashscope.aliyuncs.com/compatible-mode/v1',path:a=>a.baseUrl='https://dashscope.aliyuncs.com/anything',correlation:a=>a.correlation='../bad',deadline:a=>a.deadlineMs=Date.now()+121000,key:a=>a.apiKey=''})) {
 test(`rejects invalid input ${name} before any fetch`,async()=>{const {uploadNativeVideo}=await mod();const a=args();change(a);let calls=0;await safeFailure(uploadNativeVideo({...a,fetchImpl:async()=>{calls++;throw Error(secret)}}),/^video_native_/);assert.equal(calls,0)});
}
for(const origin of ['dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com']) test(`policy uses configured official region ${origin}`,async()=>{const {uploadNativeVideo}=await mod();const calls=[];await uploadNativeVideo({...args(),baseUrl:`https://${origin}/compatible-mode/v1`,fetchImpl:successfulFetch(calls)});assert.equal(new URL(calls[0].url).hostname,origin)});
for(const [name, change] of Object.entries({host:p=>p.data.upload_host='https://dashscope-file.oss-cn-beijing.aliyuncs.com.evil.invalid',redirect:p=>p.data.upload_host+='/?redirect=bad',acl:p=>p.data.x_oss_object_acl='public-read',overwrite:p=>p.data.x_oss_forbid_overwrite='false',size:p=>p.data.max_file_size_mb=0,expiry:p=>p.data.expire_in_seconds=0,directory:p=>p.data.upload_dir='../escape',expiredSigned:p=>p.data.policy=Buffer.from(JSON.stringify({expiration:'2000-01-01T00:00:00Z'})).toString('base64'),signedSize:p=>p.data.policy=Buffer.from(JSON.stringify({expiration:new Date(Date.now()+10000).toISOString(),conditions:[['content-length-range',1,2]]})).toString('base64'),signedAcl:p=>p.data.policy=Buffer.from(JSON.stringify({expiration:new Date(Date.now()+10000).toISOString(),conditions:[{'x-oss-object-acl':'public-read'}]})).toString('base64')})) {
 test(`rejects policy ${name} without uploading`,async()=>{const {uploadNativeVideo}=await mod();const p=policy();change(p);const calls=[];await safeFailure(uploadNativeVideo({...args(),fetchImpl:successfulFetch(calls,p)}),/^video_native_policy_invalid$/);assert.equal(calls.length,1)});
}
for(const phase of [0,1]) test(`cancellation closes phase ${phase}`,async()=>{const {uploadNativeVideo}=await mod();const controller=new AbortController();let calls=0;await safeFailure(uploadNativeVideo({...args(),signal:controller.signal,fetchImpl:async(url,init)=>{if(calls++===phase){controller.abort(Error(secret));throw Error(secret)}return new Response(JSON.stringify(policy()))}}),/^video_native_cancelled$/);assert.equal(calls,phase+1)});
for(const phase of [0,1]) test(`deadline bounds an uncooperative phase ${phase}`,async()=>{const {uploadNativeVideo}=await mod();let calls=0;await safeFailure(uploadNativeVideo({...args(),deadlineMs:Date.now()+25,fetchImpl:async()=>{if(calls++===phase)return new Promise(()=>{});return new Response(JSON.stringify(policy()))}}),/^video_native_deadline$/);assert.equal(calls,phase+1)});
test('expired deadline and already cancelled never dispatch',async()=>{const {uploadNativeVideo}=await mod();let count=0;const fetchImpl=async()=>count++;await safeFailure(uploadNativeVideo({...args(),deadlineMs:Date.now()-1,fetchImpl}),/^video_native_deadline$/);await safeFailure(uploadNativeVideo({...args(),signal:AbortSignal.abort(),fetchImpl}),/^video_native_cancelled$/);assert.equal(count,0)});
for(const phase of [0,1]) for(const status of [302,403,500]) test(`HTTP ${status} phase ${phase} is closed, without retry/body read`,async()=>{const {uploadNativeVideo}=await mod();let count=0;await safeFailure(uploadNativeVideo({...args(),fetchImpl:async()=>{if(count++===phase)return new Response(secret,{status});return new Response(JSON.stringify(policy()))}}),(status >= 500 ? /^video_stage_provider_failed$/ : /^video_native_(policy|upload)_failed$/));assert.equal(count,phase+1)});
test('wrong ETag rejects uploaded object',async()=>{const {uploadNativeVideo}=await mod();let count=0;await safeFailure(uploadNativeVideo({...args(),fetchImpl:async()=>++count===1?new Response(JSON.stringify(policy())):new Response(null,{headers:{etag:'"00000000000000000000000000000000"'}})}),/^video_native_integrity$/);assert.equal(count,2)});
test('oversized policy response rejects with bounded reading',async()=>{const {uploadNativeVideo}=await mod();let reads=0,cancelled=false;const stream=new ReadableStream({pull(c){reads++;c.enqueue(new Uint8Array(32769))},cancel(){cancelled=true}});await safeFailure(uploadNativeVideo({...args(),fetchImpl:async()=>new Response(stream)}),/^video_native_policy_invalid$/);assert.ok(reads<=4);assert.equal(cancelled,true)});
test('network errors expose neither secrets nor provider body',async()=>{const {uploadNativeVideo}=await mod();await safeFailure(uploadNativeVideo({...args(),fetchImpl:async()=>{throw Error(secret)}}),/^video_stage_provider_failed$/)});
test('does not cache a successful upload',async()=>{const {uploadNativeVideo}=await mod();const calls=[];const fetchImpl=successfulFetch(calls);await uploadNativeVideo({...args(),fetchImpl});await uploadNativeVideo({...args(),fetchImpl});assert.equal(calls.length,4)});
test('takes an original-byte snapshot before asynchronous policy fetch',async()=>{const {uploadNativeVideo}=await mod();const a=args();a.source=Buffer.from(source);const calls=[];const dispatch=successfulFetch(calls);const result=await uploadNativeVideo({...a,fetchImpl:async(url,init)=>{if(calls.length===0)a.source.fill(0);return dispatch(url,init)}});const uploaded=calls[1].init.body.get('file');assert.deepEqual(Buffer.from(await uploaded.arrayBuffer()),source);assert.equal(result.source_md5,digest('md5',source))});
test('rejects policy body stalled beyond deadline',async()=>{const {uploadNativeVideo}=await mod();let cancelled=false;await safeFailure(uploadNativeVideo({...args(),deadlineMs:Date.now()+25,fetchImpl:async()=>new Response(new ReadableStream({pull(){return new Promise(()=>{})},cancel(){cancelled=true}}))}),/^video_native_deadline$/);assert.equal(cancelled,true)});
test('rejects signed key restriction that cannot authorize selected object',async()=>{const {uploadNativeVideo}=await mod();const p=policy();p.data.policy=Buffer.from(JSON.stringify({expiration:new Date(Date.now()+10000).toISOString(),conditions:[['starts-with','$key','another/account/']]})).toString('base64');const calls=[];await safeFailure(uploadNativeVideo({...args(),fetchImpl:successfulFetch(calls,p)}),/^video_native_policy_invalid$/);assert.equal(calls.length,1)});

test('rejects an unexpected official-host resource namespace before uploading',async()=>{const {uploadNativeVideo}=await mod();const p=policy();p.data.upload_dir='temporary/account/model';p.data.policy=Buffer.from(Buffer.from(p.data.policy,'base64').toString('utf8').replaceAll('dashscope-instant/account/model','temporary/account/model')).toString('base64');const calls=[];await safeFailure(uploadNativeVideo({...args(),fetchImpl:successfulFetch(calls,p)}),/^video_native_policy_invalid$/);assert.equal(calls.length,1)});


for (const outcome of ['success', 'policy-http', 'upload-http', 'integrity']) {
 test(`cleans ignored stalled response body on ${outcome}`, { timeout: 2000 }, async () => {
  const { uploadNativeVideo } = await mod();
  const caller = new AbortController();
  const calls = [];
  let cancelled = false;
  const response = new Response(new ReadableStream({
   pull() { return new Promise(() => {}); },
   cancel() { cancelled = true; },
  }), { status: outcome.endsWith('http') ? 403 : 200,
   headers: { etag: outcome === 'integrity' ? 'wrong' : `"${digest('md5', source)}"` } });
  const promise = uploadNativeVideo({ ...args(), signal: caller.signal, fetchImpl: async (url, init) => {
   calls.push({ url, init });
   return outcome === 'policy-http' || calls.length === 2 ? response : new Response(JSON.stringify(policy()));
  }});
  if (outcome === 'success') assert.equal((await promise).source_md5, digest('md5', source));
  else await safeFailure(promise, new RegExp(`^video_native_${outcome === 'integrity' ? 'integrity' : outcome === 'policy-http' ? 'policy_failed' : 'upload_failed'}$`));
  assert.equal(cancelled, true, 'ignored response body must be cancelled before returning');
  assert.equal(calls[0].init.signal.aborted, true, 'helper-owned network signal must be closed');
  assert.equal(caller.signal.aborted, false, 'caller signal remains caller-owned');
 });
}

for (const cancellation of ['pending', 'rejecting']) {
 test(`cleanup does not wait for ${cancellation} cancellation`, { timeout: 2000 }, async () => {
  const { uploadNativeVideo } = await mod();
  let cancelled = false;
  let count = 0;
  const response = new Response(new ReadableStream({
   cancel() { cancelled = true; return cancellation === 'pending' ? new Promise(() => {}) : Promise.reject(Error(secret)); },
  }), { headers: { etag: digest('md5', source) } });
  const result = await uploadNativeVideo({ ...args(), fetchImpl: async () => ++count === 1 ? new Response(JSON.stringify(policy())) : response });
  assert.equal(result.source_md5, digest('md5', source));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancelled, true);
 });
}

for (const phase of [0, 1]) for (const reason of ['deadline', 'cancelled']) {
 test(`cleans late response after ${reason} in phase ${phase}`, { timeout: 2000 }, async () => {
  const { uploadNativeVideo } = await mod();
  const caller = new AbortController();
  let release;
  let ownedSignal;
  let count = 0;
  let cancelled = false;
  const late = new Promise(resolve => { release = resolve; });
  await safeFailure(uploadNativeVideo({ ...args(), deadlineMs: Date.now() + 100, signal: caller.signal, fetchImpl: async (url, init) => {
   ownedSignal = init.signal;
   if (count++ === phase) {
    if (reason === 'cancelled') caller.abort();
    return late;
   }
   return new Response(JSON.stringify(policy()));
  }}), new RegExp(`^video_native_${reason}$`));
  assert.equal(ownedSignal.aborted, true);
  release(new Response(new ReadableStream({ cancel() { cancelled = true; return Promise.reject(Error(secret)); } })));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cancelled, true, 'late ignored response must also be closed');
  if (reason === 'deadline') assert.equal(caller.signal.aborted, false);
 });
}


for (const phase of [0, 1]) for (const fault of ['network', 429, 503, 599, 403, 'redirect']) {
 test(`classifies native transport ${fault} in phase ${phase} without retry`, async () => {
  const { uploadNativeVideo } = await mod();
  let calls = 0;
  let ownedSignal;
  const transient = fault === 'network' || typeof fault === 'number' && (fault === 429 || fault >= 500);
  await safeFailure(uploadNativeVideo({ ...args(), fetchImpl: async (url, init) => {
   ownedSignal = init.signal;
   if (calls++ !== phase) return new Response(JSON.stringify(policy()));
   if (fault === 'network') throw new TypeError(secret);
   if (fault === 'redirect') throw new TypeError('fetch failed', { cause: Error('unexpected redirect') });
   return new Response(secret, { status: fault });
  }}), transient ? /^video_stage_provider_failed$/ : new RegExp(`^video_native_${phase === 0 ? 'policy' : 'upload'}_failed$`));
  assert.equal(calls, phase + 1);
  assert.equal(ownedSignal.aborted, true);
 });
}

test('policy body transport failure is optional but malformed JSON stays hard', async () => {
 const { uploadNativeVideo } = await mod();
 for (const fault of ['network', 'malformed']) {
  let calls = 0;
  await safeFailure(uploadNativeVideo({ ...args(), fetchImpl: async () => {
   calls++;
   return fault === 'network' ? new Response(new ReadableStream({ start(c) { c.error(new TypeError(secret)); } })) : new Response('{broken-json');
  }}), fault === 'network' ? /^video_stage_provider_failed$/ : /^video_native_policy_failed$/);
  assert.equal(calls, 1);
 }
});
