#!/usr/bin/env node
import {stdin,stdout,stderr} from 'node:process';
import {fileURLToPath} from 'node:url';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {runAcceptance} from './acceptance.mjs';

async function protectedInput() {
  if (stdin.isTTY) throw Error('protected_stdin_required');
  const chunks=[];
  let bytes=0;
  for await (const chunk of stdin) {
    bytes+=chunk.length;
    if (bytes>32768) throw Error('input_invalid');
    chunks.push(chunk);
  }
  try {return JSON.parse(Buffer.concat(chunks).toString('utf8'));} catch {throw Error('input_invalid');}
}
let client;
let started;
try {
  const mode=process.argv[2]?.replace(/^--/,'');
  if (process.argv.length!==3 || !['qwen','deterministic'].includes(mode)) throw Error('use_qwen_or_deterministic');
  const input=await protectedInput();
  if (typeof input?.session_token!=='string' || !input.session_token || /[\r\n]/.test(input.session_token))
    throw Error('platform_session_required');
  client=new Client({name:'midscene-practice-acceptance',version:'1.0.0'});
  const transport = new StdioClientTransport({command:process.execPath,
    args:[fileURLToPath(new URL('./server.mjs',import.meta.url))],
    env:{PLATFORM_SESSION_TOKEN:input.session_token},stderr:'ignore'});
  await client.connect(transport);
  const report = await runAcceptance(client,{mode,request:input.request,requestId:input.request_id,
    modelKey:input.model_key,model:input.model,baseUrl:input.base_url,onStarted:r=>{started=r;}});
  stdout.write(JSON.stringify(report,null,2)+'\n');
  if (report.status!=='passed') process.exitCode=1;
} catch (e) {
  const safe=/^(platform_http_\d{3}(?:_[a-z_]+)?|provider_http_\d{3}|[a-z]+(?:_[a-z]+)+)$/.test(e?.message) ? e.message : 'host_failed';
  stderr.write(JSON.stringify({error:safe,request_id:started?.request_id,run_id:started?.run_id})+'\n');
  process.exitCode=1;
} finally {await client?.close();}
