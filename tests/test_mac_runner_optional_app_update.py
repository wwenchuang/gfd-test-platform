import json
import os
import runpy
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
runner = runpy.run_path(str(ROOT / 'mac-midscene-runner.py'))


def test_guard_only_opts_in_published_android_regressions():
    rows = json.loads((ROOT / 'deploy/3d-daily/baselines.json').read_text())
    enabled = runner['should_defer_zxb_update']
    for row in rows:
        source = (ROOT / 'server-tasks' / row['module'] / row['filename']).read_text()
        task, = yaml.safe_load(source)['tasks']
        job = dict(platform='android', ui_batch_id='example', ui_execution_contract='ui-v1',
                   app_id='com.kfb.model', target_package='com.kfb.model', yaml_content=source,
                   module=row['module'], file=row['filename'], target_task_name=task['name'])
        assert enabled(job), row
    for change in (dict(platform='ios'), dict(ui_batch_id=''), dict(ui_execution_contract=''),
                   dict(target_package='other.app'), dict(app_id='other.app'), dict(module='草稿'),
                   dict(file='同名但来自其他用例.yaml'), dict(target_task_name='升级检查'),
                   dict(yaml_content='- aiTap: 弹窗的「立即更新」按钮')):
        assert not enabled({**job, **change}), change


def test_android_update_dialog_guard_uses_only_the_optional_later_control(tmp_path):
    harness = r"""
const assert = require('node:assert/strict');
globalThis.setTimeout = fn => queueMicrotask(fn);
const home = 'com.kfb.model/.MainActivity';
const update = 'com.kfb.model/com.mango.bridge.ui.update.AppUpdateTransAct';
function node(id, text, bounds, clickable) {
  return '<node package="com.kfb.model" resource-id="com.kfb.model:id/base_u3d_dlg_'+id+
    '" text="'+text+'" bounds="'+bounds+'" enabled="true" clickable="'+clickable+'"/>';
}
const title=node('title','发布新版本啦','[5,5][100,25]',false);
const later=node('btn_later','下次再说','[8,55][48,71]',true);
const upgrade=node('btn_update','立即更新','[54,55][105,71]',true);
const xml = '<hierarchy>'+title+later+upgrade+'</hierarchy>';
// The selected rows retain the tokens and spacing of the OPPO task 4997 capture.
const taskHeader='  * Task{c436927 #4997 type=standard A=11039:com.kfb.model U=0 visible=true}';
const paused='    mLastPausedActivity: ActivityRecord{232ecfb u0 '+update+' t-1 f}}';
const foreground=(id,name)=>'    topResumedActivity=ActivityRecord{'+id+' u0 '+name+' t4997}';
const topHistory='    * Hist  #1: ActivityRecord{128d54a u0 '+update+' t4997}';
const homeHistory='    * Hist  #0: ActivityRecord{71cbc93 u0 '+home+' t4997}';
const stack=[taskHeader,paused,foreground('128d54a',update),topHistory,homeHistory].join('\n');
function scene(body=xml, behind=home, mutate=s=>s, after=s=>s) {
  let reads=0, homeReads=0, shots=0;
  let activity=home;
  const commands=[], taps=[];
  const device={
    screenshotBase64: async () => {shots++;return 'image:'+activity;},
    tapPoint: async point => taps.push(point),
    getAdb: async () => ({shell: async command=>{
      commands.push(command);
      if (command.startsWith('dumpsys activity')) return activity===update ?
        mutate(stack.replace(homeHistory,homeHistory.replace(home,behind)), ++reads) :
        after([taskHeader, foreground('71cbc93',home),homeHistory].join('\n'), ++homeReads);
      if (command.startsWith('file=')) return body;
      if (command==='input tap 28 63') {activity=home;return '';}
      throw Error('Unexpected adb action: '+command);
    }})
  };
  installOptionalAppUpdateGuard({page:device});
  return {device, commands, taps, shots:()=>shots, show:()=>{activity=update;}};
}
(async()=>{
  const s=scene();
  assert.equal(await s.device.screenshotBase64(),'image:'+home);
  s.show();
  assert.equal(await s.device.screenshotBase64(),'image:'+home);
  assert.deepEqual(s.commands.filter(s=>s.startsWith('input')),['input tap 28 63']);
  s.show();
  await s.device.tapPoint({x:1,y:3});
  assert.deepEqual(s.taps,[{x:1,y:3}]);
  assert.equal(s.commands.filter(s=>s==='input tap 28 63').length,2);
  for (const [dialog, parent] of [
    [title+upgrade,home], [xml.replace('发布新版本啦','其他提示'),home], [xml.replace('下次再说','立即更新'),home],
    [xml.replace('enabled="true" clickable="true"','enabled="false" clickable="true"'),home],
    [xml+later,home], [xml.replace('[8,55][48,71]','[48,55][48,71]'),home], [xml,'other.app/.MainActivity']
  ]) {
    const blocked=scene(dialog,parent);blocked.show();
    await assert.rejects(()=>blocked.device.screenshotBase64());
    await assert.rejects(()=>blocked.device.tapPoint({x:50,y:60}));
    assert.deepEqual(blocked.commands.filter(s=>s.startsWith('input')),[]);
    assert.deepEqual(blocked.taps,[]);
  }
  for (const change of [
    s=>s.replace(homeHistory,homeHistory.replace('t4997','t20')),
    s=>s.replace(homeHistory,homeHistory.replace('u0','u10')),
    s=>s.replace(homeHistory,homeHistory.replace('#0','#3')),
    s=>s.replace(homeHistory,'    * Hist  #0: ActivityRecord{17ac u0 other.app/.Gate t4997}\n'+homeHistory),
    s=>s.replace(homeHistory,'  * Task{efbb #20 type=standard}\n'+homeHistory),
    s=>s.replace(homeHistory,'    * Hist  #0: <unknown>\n'+homeHistory),
    s=>s.replace(topHistory,topHistory.replace('128d54a','333aaaa')),
    s=>s.replace(topHistory,topHistory+'\n'+topHistory),
    s=>s.replace(homeHistory,''),
    s=>s.replace(paused,'    mLastPausedActivity: ActivityRecord{71cbc93 u0 '+home+' t4997}')
      .replace(homeHistory,homeHistory.replace(home,'com.kfb.model/.Unknown')),
    s=>s.replace(paused,foreground('128d54a',update)),
    (s,n)=>n===1?s:s.replaceAll('128d54a','333aaaa'),
    (s,n)=>n===1?s:s.replaceAll('4997','4998'),
    (s,n)=>n===1?s:s.replace('71cbc93','333aaaa')
  ]) {
    const blocked=scene(xml,home,change);blocked.show();
    await assert.rejects(()=>blocked.device.screenshotBase64());
    assert.deepEqual(blocked.commands.filter(s=>s.startsWith('input')),[]);
  }
  const noFocus=s=>s.replace(/topResumedActivity=[^\n]+/,'');
  const multiFocus=s=>s+'\n'+foreground('666aaaa','com.android.launcher/.Launcher');
  const nullFocus=s=>s.replace(/topResumedActivity=[^\n]+/,'topResumedActivity=null');
  for (const transient of [noFocus,multiFocus,nullFocus]) {
    const launch=scene(xml,home,s=>s,(s,n)=>n<=2?transient(s):s);
    assert.equal(await launch.device.screenshotBase64(),'image:'+home);
    assert.equal(launch.shots(),2);
    assert.deepEqual(launch.commands.filter(s=>s.startsWith('input')),[]);
    const staleTap=scene(xml,home,s=>s,(s,n)=>n===1?transient(s):s);
    await assert.rejects(()=>staleTap.device.tapPoint({x:50,y:60}),/fresh|changed/i);
    assert.deepEqual(staleTap.taps,[]);
    const stuck=scene(xml,home,s=>s,transient);
    await assert.rejects(()=>stuck.device.tapPoint({x:50,y:60}),/foreground/i);
    assert.ok(stuck.commands.length>=2 && stuck.commands.length<=12);
    assert.deepEqual(stuck.taps,[]);
  }
  const resumedDialog=scene(xml,home,(s,n)=>n<3?noFocus(s):s);resumedDialog.show();
  assert.equal(await resumedDialog.device.screenshotBase64(),'image:'+home);
  assert.deepEqual(resumedDialog.commands.filter(s=>s.startsWith('input')),['input tap 28 63']);
  const lostBeforeTap=scene(xml,home,(s,n)=>n===1?s:multiFocus(s));lostBeforeTap.show();
  await assert.rejects(()=>lostBeforeTap.device.screenshotBase64());
  assert.deepEqual(lostBeforeTap.commands.filter(s=>s.startsWith('input')),[]);
  const adbError=scene(xml,home,s=>s,()=>{throw Error('ADB unavailable');});
  await assert.rejects(()=>adbError.device.screenshotBase64(), /ADB unavailable/);
  assert.equal(adbError.commands.length,1);
  const wrongResume=scene(xml,home,s=>s,s=>s.replaceAll('4997','4998')); wrongResume.show();
  await assert.rejects(()=>wrongResume.device.tapPoint({x:50,y:60}),/Home did not resume/);
  assert.deepEqual(wrongResume.commands.filter(s=>s.startsWith('input')),['input tap 28 63']);
  assert.deepEqual(wrongResume.taps,[]);
  const splash='com.kfb.model/.splash.SplashActivity';
  const cold=scene(xml,home,s=>s,(s,n)=>n<=35?s.replaceAll(home,splash):s);
  await cold.device.screenshotBase64();
  assert.equal(cold.shots(),2);
  assert.deepEqual(cold.commands.filter(s=>s.startsWith('input')),[]);
  const stalled=scene(xml,home,s=>s,s=>s.replaceAll(home,splash));
  await assert.rejects(()=>stalled.device.screenshotBase64(),/SplashActivity/);
  const changedTarget=scene(xml,home,s=>s,(s,n)=>n===1?s.replaceAll(home,splash):s);
  await assert.rejects(()=>changedTarget.device.tapPoint({x:1,y:2}),/fresh target/);
  assert.deepEqual(changedTarget.taps,[]);
  const foreign=scene(xml,home,s=>s,(s,n)=>s.replaceAll(home,n===1?splash:'other.app/.Main'));
  await assert.rejects(()=>foreign.device.screenshotBase64(),/launch.*task/);
  const popup='com.kfb.model/com.mango.flutter.act.PopupFlutterBoostAct';
  const popupStack=stack.replaceAll(update,popup);
  const handoff=scene(xml,home,s=>s,()=>popupStack);handoff.show();
  await handoff.device.screenshotBase64();
  assert.equal(handoff.shots(),2);
  assert.deepEqual(handoff.commands.filter(s=>s.startsWith('input')),['input tap 28 63']);
  const stalePopupTap=scene(xml,home,s=>s,()=>popupStack);stalePopupTap.show();
  await assert.rejects(()=>stalePopupTap.device.tapPoint({x:1,y:2}),/fresh target/);
  assert.deepEqual(stalePopupTap.taps,[]);
  const otherHome=scene(xml,home,s=>s,()=>popupStack.replaceAll('71cbc93','99caa'));otherHome.show();
  await assert.rejects(()=>otherHome.device.screenshotBase64(),/Home did not resume/);
  process.env.MIDSCENE_ZXB_OPTIONAL_UPDATE='0';
  const disabled=scene(); disabled.show();
  assert.equal(await disabled.device.screenshotBase64(),'image:'+update);
  assert.deepEqual(disabled.commands,[]);
})().catch(err=>{console.error(err);process.exitCode=1;});
"""
    fixture = tmp_path / 'update-fixture.cjs'
    fixture.write_text(runner['RUNNER_ZXB_UPDATE_ADAPTER'] + harness)
    subprocess.run(['node', str(fixture)], env={**os.environ, 'MIDSCENE_ZXB_OPTIONAL_UPDATE': '1',
                   'MIDSCENE_NATIVE_TARGET_PACKAGE': 'com.kfb.model', 'MIDSCENE_PLATFORM': 'android'},
                   check=True, timeout=15)
