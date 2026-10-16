import ast
import json
from pathlib import Path
import subprocess


def adapter():
    tree=ast.parse(Path('mac-midscene-runner.py').read_text())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RUNNER_DYNAMIC_OBJECT_ADAPTER' for t in n.targets))


def test_runtime_binding_is_exact_fresh_and_fail_closed(tmp_path):
    script=tmp_path/'adapter.cjs';script.write_text(adapter())
    test=tmp_path/'test.cjs'
    test.write_text(r'''
const assert=require('node:assert/strict');
const {installDynamicObject}=require(process.argv[2]);
async function main(){
 let reads=0, checks=[], taps=[];
 const row={status:'matched',target_id:'1554081049356550144',target_label:'测试记录',retained_id:'1554081728676478976',retained_label:'另一条测试记录'};
 const agent={aiQuery:async()=>{reads++;return {...row};},aiAssert:async p=>checks.push(p),aiTap:async p=>taps.push(p),aiWaitFor:async()=>{},aiScroll:async()=>{},aiInput:async()=>{}};
 let task=0;
 installDynamicObject(agent,()=>task);
 await assert.rejects(()=>agent.aiTap('{{本次对象}}删除按钮'),/未选取/);
 await agent.aiQuery('平台运行时对象选择v1：当前列表第一条可删除测试记录');
 await agent.aiTap('{{本次对象}}删除按钮');
 await agent.aiAssert('{{本次对象}}消失，{{保留对象}}仍保留');
 assert.equal(reads,1);assert(taps[0].includes(row.target_id));assert(checks.at(-1).includes(row.retained_id));
 await assert.rejects(()=>agent.aiQuery('平台运行时对象选择v1：再选一条'),/重复/);
 task=1;
 await assert.rejects(()=>agent.aiTap('{{本次对象}}删除按钮'),/未选取/);
 row.target_id=1554081049356550144;
 await assert.rejects(()=>agent.aiQuery('平台运行时对象选择v1：第一条'),/测试数据/);
 assert.equal(taps.length,1);
 task=2;row.target_id='1554081049356550144';row.retained_id=row.target_id;
 await assert.rejects(()=>agent.aiQuery('平台运行时对象选择v1：第一条'),/测试数据/);
 task=3;row.status='no_match';
 await assert.rejects(()=>agent.aiQuery('平台运行时对象选择v1：第一条'),/测试数据/);
 task=4;row.status='matched';row.retained_id='1554081728676478976';
 const aborter=new AbortController();aborter.abort();const count=reads;
 await assert.rejects(()=>agent.aiQuery('平台运行时对象选择v1：第一条',{abortSignal:aborter.signal}));
 assert.equal(reads,count);
 task=5;row.target_id='1554081049356550199';
 await agent.aiQuery('平台运行时对象选择v1：第一条');
 await agent.aiTap('{{本次对象}}删除按钮');assert(taps.at(-1).includes(row.target_id));
}
main().catch(e=>{console.error(e);process.exit(1)});
''')
    subprocess.run(['node',str(test),str(script)],check=True,capture_output=True,text=True)
