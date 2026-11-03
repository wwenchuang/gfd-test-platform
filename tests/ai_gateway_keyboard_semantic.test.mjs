import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const source=fs.readFileSync(new URL('../ai-gateway/server.js',import.meta.url),'utf8');
const ctx=vm.createContext({Buffer,createHash});
vm.runInContext(source.slice(source.indexOf('function requestedCompletionTokens('),source.indexOf('async function appendAiLog(')),ctx);
test('Gateway admits actual Python canonical keyboard action schema, rejects unknown action',()=>{
 const py=spawnSync('python3',['-c',"import json;from task_server.services.ai_skill_service import load_ai_skill_schema;from task_server.services.ui_video_stage_policy import _provider_projection;print(json.dumps(_provider_projection(load_ai_skill_schema('ui_video_trajectory')['oneOf'][0])))"],{encoding:'utf8'});
 assert.equal(py.status,0,py.stderr);const schema=JSON.parse(py.stdout);
 assert.ok(schema.properties.events.items.properties.action.enum.includes('dismissKeyboard'));
 ctx.schema=schema;assert.doesNotThrow(()=>vm.runInContext("validateVideoOutputSchema(schema,'grounding')",ctx));
 schema.properties.events.items.properties.action.enum.push('arbitraryShell');
 assert.throws(()=>vm.runInContext("validateVideoOutputSchema(schema,'grounding')",ctx),/video_output_schema_invalid/);
});
