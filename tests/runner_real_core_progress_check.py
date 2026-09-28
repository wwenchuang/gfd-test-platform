"""Opt-in integration: python tests/runner_real_core_progress_check.py <npm runtime dir>.

Runtime must contain @midscene/core and @midscene/cli 1.13.x from npm.
Uses real ScriptPlayer + Runner subprocess/progress delivery; device adapter is local.
No phone, model, platform writes, or global npm installation.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest import mock

spec = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'windows-midscene-runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runtime = Path(sys.argv[1]).resolve()
node = shutil.which('node')
assert node and (runtime / 'node_modules/@midscene/cli/package.json').exists()

scenarios = [
    ('success', [{'name': '完整三步', 'flow': [{'sleep': 900}] * 3}], ['passed']),
    ('failure', [{'name': '中途失败', 'flow': [{'sleep': 900}, {'sleep': 666}, {'sleep': 900}]}], ['failed']),
    ('continue', [{'name': '失败后继续', 'continueOnError': True, 'flow': [{'sleep': 666}]},
                  {'name': '后续用例', 'flow': [{'sleep': 900}] * 3}], ['failed', 'passed']),
]
with tempfile.TemporaryDirectory(prefix='runner-core-') as temp:
    for name, tasks, expected in scenarios:
        directory = Path(temp) / ('中文 ' + name)
        directory.mkdir()
        (directory / 'node_modules').symlink_to(runtime / 'node_modules', target_is_directory=True)
        script = directory / 'real-player.cjs'
        script.write_text('''
const {ScriptPlayer} = require('@midscene/core/yaml');
const agent = {getActionSpace:async()=>[], destroy:async()=>{}, sleep:async ms=>{
  await new Promise(r=>setTimeout(r,ms));
  if(ms===666) throw new Error('expected local failure');
}};
const player = new ScriptPlayer(CONFIG, async()=>({agent,freeFn:[]}));
player.run().then(()=>{if(player.taskStatusList.some(t=>t.status==='error'))process.exitCode=1;})
.catch(e=>{console.error(e.message);process.exitCode=1;});
'''.replace('CONFIG', json.dumps({'android': {}, 'tasks': tasks}, ensure_ascii=False)), encoding='utf-8')
        delivered = []
        with mock.patch.object(runner, 'resolve_command', return_value=node), \
             mock.patch.object(runner, 'midscene_env', return_value=dict(os.environ)), \
             mock.patch.object(runner, 'post_job_progress', side_effect=lambda _, p: delivered.append(p)):
            result = runner.execute_midscene(name, directory, script, [t['name'] for t in tasks], '')
        snapshots = [p['execution_progress'] for p in delivered if 'execution_progress' in p]
        assert snapshots, (name, result)
        final = snapshots[-1]['tasks']
        assert [t['status'] for t in final] == expected, (name, final)
        assert result['status'] == ('passed' if name == 'success' else 'failed'), result
        assert final[0]['current_step'] == (2 if name == 'success' else 1 if name == 'failure' else 0)
        if name != 'failure':  # A short failure may finish within the existing 2-second upload interval.
            assert any(t['status'] == 'running' for s in snapshots[:-1] for t in s['tasks']), name
        if name == 'continue':
            assert any(p.get('current_task_name') == '后续用例' for p in delivered), delivered
        assert 'MIDSCENE_PLATFORM_PROGRESS' not in result['stdout']
        print(f'{name}: PASS; terminal={expected}; delivered snapshots={len(snapshots)}')
print('3 real-core subprocess rounds passed (local device adapter, not real-device acceptance).')
