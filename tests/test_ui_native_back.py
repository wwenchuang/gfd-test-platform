import unittest
from task_server.services import ui_native_back as k

class NativeBackContract(unittest.TestCase):
    def test_finite_marker(self):
        self.assertEqual(k.encode('navigateBack', k.TARGET, ''), k.MARKER)
        for target,value in [('其他',''),(k.TARGET,{}),(k.TARGET,'back')]:
            with self.assertRaises(ValueError): k.encode('navigateBack',target,value)
    def test_reserved_rejected(self):
        for step in [{'aiQuery':k.MARKER+'x'},{'aiQuery':k.MARKER,'timeout':1}]:
            with self.assertRaises(ValueError):k.validate_flow({'tasks':[{'flow':[step]}]})


    def test_native_back_no_keyboard_and_single_call(self):
        import ast,pathlib,subprocess
        tree=ast.parse(pathlib.Path('mac-midscene-runner.py').read_text())
        adapter=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RUNNER_KEYBOARD_DISMISS_ADAPTER' for t in n.targets))
        script=adapter+'''\nprocess.env.MIDSCENE_PLATFORM='android';process.env.MIDSCENE_NATIVE_TARGET_PACKAGE='app';
(async()=>{for(const mode of ['hidden','shown','ambiguous','aborted','exit','restore_ime']){let calls=0;const agent={aiQuery:async()=>{throw Error('model')},page:{getAdb:async()=>({isSoftKeyboardPresent:async()=>mode==='ambiguous'?{}:mode==='shown'||mode==='restore_ime'&&calls>0,shell:async()=>`topResumedActivity=ActivityRecord{x ${mode==='exit'&&calls?'other':'app'}/.Main t1}`}),back:async()=>{calls++}}};installNativeBack(agent);let failed=false;try{await agent.aiQuery('平台原生返回v1：navigateBack',{signal:{aborted:mode==='aborted'}})}catch(e){failed=true}if(failed!==(!['hidden','restore_ime'].includes(mode))||calls!==(['hidden','exit','restore_ime'].includes(mode)?1:0))throw Error(mode)}})().catch(e=>{console.error(e);process.exitCode=1});'''
        subprocess.run(['node','-e',script],check=True)
