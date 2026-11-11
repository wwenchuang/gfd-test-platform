#!/usr/bin/env node
import {McpServer} from '@modelcontextprotocol/sdk/server/mcp.js';
import {StdioServerTransport} from '@modelcontextprotocol/sdk/server/stdio.js';
import {z} from 'zod';

const token = process.env.PLATFORM_SESSION_TOKEN;
if (!token || /[\r\n]/.test(token)) {
  process.stderr.write('platform_session_required\n');
  process.exit(1);
}
const server = new McpServer({name:'midscene-practice-week10', version:'1.0.0'});
const uuid = z.string().regex(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/);
const definitions = [
  ['list_suites','List available business checks and their environment.',z.strictObject({})],
  ['run_suite','Start the selected QA suite. Reuse the request ID for every retry.',z.strictObject({
    suite_id:z.enum(['published-catalog','feedback-categories']),environment:z.literal('platform'),request_id:uuid,
  })],
  ['get_run','Read status and evidence of your QA run.',z.strictObject({run_id:uuid})],
];

const platformCodes = new Set(['invalid_arguments','unknown_tool','unauthorized','forbidden','run_not_found',
  'check_busy','execution_disabled','receipt_unavailable']);

function failure(code) {
  return {isError:true,content:[{type:'text',text:code}]};
}
for (const [name,description,inputSchema] of definitions) {
  server.registerTool(name,{description,inputSchema},async args => {
    try {
      const reply = await fetch('http://127.0.0.1:8088/api/platform/mcp/call',{
        method:'POST',redirect:'error',signal:AbortSignal.timeout(30000),
        headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
        body:JSON.stringify({name,arguments:args}),
      });
      if (!reply.ok) {
        const refusal = await reply.json().catch(()=>null);
        const code = platformCodes.has(refusal?.code) ? '_'+refusal.code : '';
        return failure('platform_http_'+reply.status+code);
      }
      const body = await reply.json();
      if (body?.ok !== true || !body.result || typeof body.result !== 'object' || Array.isArray(body.result))
        return failure('platform_result_invalid');
      const result = name === 'list_suites' && Array.isArray(body.result.suites)
        ? {...body.result,suites:body.result.suites.filter(s => ['published-catalog','feedback-categories'].includes(s.suite_id))}
        : body.result;
      return {content:[{type:'text',text:JSON.stringify(result)}],structuredContent:result};
    } catch {
      return failure('platform_transport_failed');
    }
  });
}
await server.connect(new StdioServerTransport());
