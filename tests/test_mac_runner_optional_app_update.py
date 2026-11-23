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
function scene(body=xml, behind=home) {
  let activity=home;
  const commands=[], taps=[];
  const device={
    screenshotBase64: async () => 'image:'+activity,
    tapPoint: async point => taps.push(point),
    getAdb: async () => ({shell: async command=>{
      commands.push(command);
      if (command.startsWith('dumpsys activity')) return 'topResumedActivity=ActivityRecord{x u0 '+activity+' t15}\nmLastPausedActivity: ActivityRecord{y u0 '+behind+' t15}';
      if (command.startsWith('file=')) return body;
      if (command==='input tap 28 63') {activity=home;return '';}
      throw Error('Unexpected adb action: '+command);
    }})
  };
  installOptionalAppUpdateGuard({page:device});
  return {device, commands, taps, show:()=>{activity=update;}};
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
