import unittest
from task_server.services import ui_keyboard_dismiss as k

class KeyboardContract(unittest.TestCase):
    def test_finite_marker(self):
        self.assertEqual(k.encode('dismissKeyboard', k.TARGET, ''), k.MARKER)
        for target,value in [('其他',''),(k.TARGET,{}),(k.TARGET,'back')]:
            with self.assertRaises(ValueError): k.encode('dismissKeyboard',target,value)
    def test_reserved_rejected(self):
        for step in [{'aiQuery':k.MARKER+'x'},{'aiQuery':k.MARKER,'timeout':1}]:
            with self.assertRaises(ValueError):k.validate_flow({'tasks':[{'flow':[step]}]})

    def test_adapter_runtime(self):
        import ast, pathlib, subprocess
        tree=ast.parse(pathlib.Path('mac-midscene-runner.py').read_text())
        adapter=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RUNNER_KEYBOARD_DISMISS_ADAPTER' for t in n.targets))
        script=adapter+'''\nprocess.env.MIDSCENE_PLATFORM='android';process.env.MIDSCENE_NATIVE_TARGET_PACKAGE='app';
(async()=>{for(const mode of ['hidden','shown','ambiguous','failed','navigated']){
 let visible=mode!=='hidden', calls=0, reads=0;
 const agent={aiQuery:async()=>{throw Error('model called')},page:{getAdb:async()=>({isSoftKeyboardPresent:async()=>mode==='ambiguous'?{}:visible,shell:async()=>`mResumedActivity: ActivityRecord{ x ${mode==='navigated'&&calls?'other/.Main':'app/.Main'} t1}`}),hideKeyboard:async()=>{calls++;visible=false;return mode!=='failed'}}};
 installKeyboardDismiss(agent);let failed=false;
 try{await agent.aiQuery('平台软键盘收起v1：dismissKeyboard')}catch(e){failed=true}
 if(failed!==['ambiguous','failed','navigated'].includes(mode))throw Error(mode+' verdict');
 if(mode==='hidden'&&calls)throw Error('hidden action');
} })().catch(e=>{console.error(e);process.exitCode=1});'''
        subprocess.run(['node','-e',script],check=True)

    def test_compiler_capability_and_platform(self):
        import yaml
        from task_server.services.ui_case_compiler import compile_ui_case
        source=yaml.safe_dump({'android':{},'tasks':[{'name':'键盘','flow':[{'aiQuery':k.MARKER}]}]},allow_unicode=True)
        for platform,enabled,ok in [('android',True,True),('android',False,False),('ios',True,False)]:
            target={'ok':True,'platform':platform,'source_package':'app','target_package':'app','platform_mappings':{'android':'app'},'capabilities':{'android_baseline':True,'ui_actions':['aiQuery'],k.CAPABILITY:enabled}}
            result=compile_ui_case({'yaml_content':source},target)
            self.assertEqual(result['ok'],ok,result)

    def test_draft_preserves_expected_and_provenance(self):
        import yaml
        from task_server.services.ui_case_draft_contract import build_draft_yaml
        result=build_draft_yaml({'title':'键盘','steps':[{'action':'dismissKeyboard','target':k.TARGET,'value':'','expected':'键盘已收起','review_state':'confirmed'}]},'com.kfb.model')
        flow=yaml.safe_load(result['yaml_content'])['tasks'][0]['flow']
        self.assertEqual(flow[1:], [{'aiQuery':k.MARKER},{'aiWaitFor':'键盘已收起','timeout':12000},{'aiAssert':'键盘已收起'}])

    def test_abort_both_signal_names_before_hidden_noop(self):
        import ast,pathlib,subprocess
        tree=ast.parse(pathlib.Path('mac-midscene-runner.py').read_text())
        adapter=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RUNNER_KEYBOARD_DISMISS_ADAPTER' for t in n.targets))
        script=adapter+'''\nprocess.env.MIDSCENE_PLATFORM='android';process.env.MIDSCENE_NATIVE_TARGET_PACKAGE='app';
(async()=>{for(const name of ['signal','abortSignal']){let calls=0;const agent={aiQuery:async()=>{},page:{getAdb:async()=>{calls++;return {isSoftKeyboardPresent:async()=>false,shell:async()=> 'topResumedActivity=ActivityRecord{x app/.Main t1}'}} ,hideKeyboard:async()=>true}};installKeyboardDismiss(agent);const c=new AbortController();c.abort();let failed=false;try{await agent.aiQuery('平台软键盘收起v1：dismissKeyboard',{[name]:c.signal})}catch(e){failed=true}if(!failed||calls)throw Error(name)}})().catch(e=>{console.error(e);process.exitCode=1});'''
        subprocess.run(['node','-e',script],check=True)
