"""Actual offline Node upload failures through Python durable receipt disposition."""
import io
import json
from pathlib import Path
import subprocess
import urllib.error

import pytest
from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_runtime as runtime
from task_server.services.ui_video_model_jobs import ModelJobError
from task_server.services.ui_video_records import RecordStore
from test_ui_video_native_file_transport import body_for, source

ROOT = Path(__file__).resolve().parents[1]
# The subprocess emits only the closed helper error code, never provider data.
NODE_FAILURE = r'''
import { uploadNativeVideo } from './ai-gateway/native-video-upload.js';
import { createHash } from 'node:crypto';
const [phase, fault] = JSON.parse(process.argv[1]);
const source = Buffer.from('000000186674797069736f6d787878787878787878787878787878787878787878','hex');
const policy = {data:{upload_host:'https://dashscope-file.oss-cn-beijing.aliyuncs.com',
 upload_dir:'dashscope-instant/test/account',oss_access_key_id:'test',signature:'private-sentinel',
 policy:Buffer.from(JSON.stringify({expiration:new Date(Date.now()+60000).toISOString()})).toString('base64'),
 x_oss_object_acl:'private',x_oss_forbid_overwrite:'true',expire_in_seconds:60,max_file_size_mb:1}};
let count = 0;
try {
 await uploadNativeVideo({source,sourceBytes:source.length,sourceSha256:createHash('sha256').update(source).digest('hex'),
 model:'qwen3.8-max',baseUrl:'https://dashscope.aliyuncs.com/compatible-mode/v1',apiKey:'private-sentinel',
 correlation:'a'.repeat(32),deadlineMs:Date.now()+1000,fetchImpl:async()=>{
  if(count++ !== phase) return new Response(JSON.stringify(policy));
  if(fault === 'network') throw new TypeError('private-sentinel');
  if(fault === 'malformed') return new Response('{broken-json');
  if(fault === 'integrity') return new Response(null,{headers:{etag:'wrong'}});
  return new Response('private-sentinel',{status:Number(fault)});
 }});
 throw Error('unexpected-success');
} catch(error) { process.stdout.write(JSON.stringify({code:error.code,calls:count})); }
'''


@pytest.mark.parametrize('phase,fault,optional', [
    (phase, fault, optional)
    for phase in (0, 1)
    for fault, optional in [('network', True), ('429', True), ('503', True), ('403', False)]
] + [(0, 'malformed', False), (1, 'integrity', False)])
def test_actual_upload_error_through_gateway_receipt_and_native_disposition(tmp_path, monkeypatch, phase, fault, optional):
    result = subprocess.run(['node', '--input-type=module', '-e', NODE_FAILURE, json.dumps([phase, fault])],
                            cwd=ROOT, text=True, capture_output=True, timeout=10, check=True)
    outcome = json.loads(result.stdout)
    assert outcome['calls'] == phase + 1
    if optional:
        assert outcome['code'] == 'video_stage_provider_failed'
    else:
        assert outcome['code'].startswith('video_native_')

    proofs, receipts = [], []
    def http(req, timeout):
        # Actual Gateway uses HTTP500 for its closed thrown upload errors.
        raise urllib.error.HTTPError(req.full_url, 500, 'closed', {},
                                     io.BytesIO(json.dumps({'error': outcome['code']}).encode()))
    monkeypatch.setattr(ai.urllib.request, 'urlopen', http)
    body = body_for(source(32), file=False)
    with pytest.raises(RuntimeError, match='^video_stage_gateway_rejected$'):
        ai.ai_gateway_skill_content('ui_video_timeline', body['prompt'], payload=body['payload'],
                                    image_assets=body['imageAssets'], video_stage='timeline',
                                    raw_receipt=receipts.append, request_proof_hook=proofs.append)
    assert len(proofs) == len(receipts) == 1
    assert receipts[0]['rejection_code'] == ('video_stage_provider_failed' if optional else 'video_stage_gateway_rejected')
    assert 'private-sentinel' not in json.dumps(receipts)
    original = json.loads((ROOT/'tests/fixtures/video_optional_native_transport.json').read_text())['22']
    with RecordStore(tmp_path/'records') as records:
        row = {**original[2]['value'], 'proof_ref': records.append('proof', proofs[0]),
               'receipt_ref': records.append('receipt', receipts[0])}
        class Owner:
            accounting = [row]
            active_pids = []
            cleanup_failures = []
        disposition = runtime._native_transport_unavailable(ModelJobError('video_job_failed'), Owner(), records)
        if optional:
            assert disposition['reason'] == 'temporary_provider_failure'
            # Existing durable cleanup and request-binding guards still apply.
            row['cleanup'] = False
            assert runtime._native_transport_unavailable(ModelJobError('video_job_failed'), Owner(), records) is None
        else:
            assert disposition is None
