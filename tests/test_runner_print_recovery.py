import ast
import subprocess
import json
from pathlib import Path


def test_print_recovery_states_and_hard_bounds(tmp_path):
    tree=ast.parse(Path('mac-midscene-runner.py').read_text())
    code=next((ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RUNNER_PRINT_RECOVERY_ADAPTER' for t in n.targets)),None)
    assert code, 'missing deterministic print recovery adapter'
    p=tmp_path/'adapter.cjs';p.write_text(code)
    subprocess.run(['node','tests/runner_print_recovery_check.cjs',str(p)],check=True)


def test_observer_retains_recovery_for_all_tasks(tmp_path):
    tree=ast.parse(Path('mac-midscene-runner.py').read_text())
    constants={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('RUNNER_PRINT_RECOVERY_ADAPTER','RUNNER_DYNAMIC_OBJECT_ADAPTER','RUNNER_PROGRESS_OBSERVER')}
    observer=tmp_path/'observer.cjs';observer.write_text(''.join(constants[k] for k in ('RUNNER_PRINT_RECOVERY_ADAPTER','RUNNER_DYNAMIC_OBJECT_ADAPTER','RUNNER_PROGRESS_OBSERVER')))
    package=tmp_path/'node_modules/@midscene/cli';package.mkdir(parents=True);(package/'package.json').write_text('{"version":"1.13.0"}')
    core=tmp_path/'node_modules/@midscene/core';core.mkdir(parents=True)
    (core/'yaml.js').write_text('''
class ScriptPlayer {
 constructor(){this.currentTaskIndex=0;this.taskStatusList=[0,1].map(i=>({name:'任务'+i,status:'init',flow:[]}));}
 async setupAgent(){let states=['ready','active','active','confirm','done'];return {agent:{aiQuery:async()=>{const state=states.shift();return {state,unique:true,printer_id:'test',task_id:state==='ready'?'':'job'+this.currentTaskIndex};},aiAssert:async()=>{},aiTap:async()=>{}}};}
 async run(){for(let i=0;i<2;i++){this.currentTaskIndex=i;const {agent}=await this.setupAgent();for(const operation of ['submit','cancel'])await agent.aiQuery('平台打印取消恢复v1：'+JSON.stringify({operation,target:'测试控件',timeout_ms:180000,max_attempts:8,retry_delay_ms:10000}));this.taskStatusList[i].status='done';}}
}
module.exports={ScriptPlayer};
''')
    entry=tmp_path/'entry.cjs';entry.write_text("new (require('@midscene/core/yaml').ScriptPlayer)().run().catch(e=>{console.error(e);process.exit(1)})")
    run=subprocess.run(['node','--require',str(observer),str(entry)],capture_output=True,text=True,check=True)
    snapshots=[json.loads(line.split(' ',1)[1]) for line in run.stdout.splitlines() if line.startswith('MIDSCENE_PLATFORM_PROGRESS ')]
    assert len(snapshots[-1]['tasks'])==2
    assert all(t['print_recovery']['status']=='passed' and t['print_recovery']['attempts']==1 for t in snapshots[-1]['tasks'])
