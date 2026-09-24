// Isolated browser regression for the real recorder markup and stylesheet.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const ROOT = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const errors=[];
    for (const [width,height] of [[1366,768],[768,480],[390,600]]) {
      const page = await browser.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.setViewportSize({width,height});
      await page.setContent('<html><head></head><body><div id="editor-area" class="editor-area" style="width:100%;height:100vh"></div></body></html>');
      await page.addStyleTag({content:fs.readFileSync(process.env.RECORDER_LAYOUT_CSS || path.join(ROOT,'css/app.css'),'utf8')});
      await page.addScriptTag({content:`
        var taskApps=[{name:'智小白3D',package:'com.kfb.model',modules:['3D打印基线']},{name:'微信',package:'com.tencent.mm',modules:['登录']}];
        var runnerDevices=[];
        function escapeHtml(value) {const element=document.createElement('span');element.textContent=String(value??'');return element.innerHTML.replace(/"/g,'&quot;');}
        function showToast() {} function resetYamlToolbarForManager() {}
      `});
      await page.addScriptTag({path:path.join(ROOT,'js/device-recorder.js')});
      await page.evaluate(() => {
        deviceRecorderHistory=Array.from({length:42},(_,i)=>({id:'layout-'+i,status:i%3?'finished':'cancelled',app_package:i%2?'com.kfb.model':'com.tencent.mm',module_name:i%3?'3D打印基线':'',task_name:'响应式验证用例-'+i,step_count:4,updated_at:'2026-09-24 10:00:00'}));
        showDeviceRecordingHistory();
      });
      const bounds=await page.evaluate(() => {
        const panel=document.querySelector('.device-recorder-page');
        const head=panel.querySelector('.review-head').getBoundingClientRect();
        return {headBottom:head.bottom,buttons:[...panel.querySelectorAll('.review-head button')].map(button=>{const b=button.getBoundingClientRect();return {bottom:b.bottom,left:b.left,right:b.right};}),width:panel.clientWidth,scrollWidth:panel.scrollWidth};
      });
      assert.ok(bounds.buttons.every(b=>b.bottom<=bounds.headBottom && b.left>=0 && b.right<=width),`header clipped at ${width}x${height}: ${JSON.stringify(bounds)}`);
      assert.ok(bounds.scrollWidth<=bounds.width+1,`horizontal overflow at ${width}`);
      await page.selectOption('#recorder-history-app','com.kfb.model');
      await page.check('#recorder-history-all');
      assert.equal(await page.locator('[data-recording-select]:checked').count(),21);
      await page.selectOption('#recorder-history-module','');
      assert.equal(await page.locator('[data-recording-select]:checked').count(),0);
      assert.equal(await page.locator('.device-recorder-history-entry').count(),7);
      await page.locator('.device-recorder-history-entry').last().scrollIntoViewIfNeeded();
      assert.ok(await page.locator('.device-recorder-history-entry').last().isVisible());
      await page.evaluate(()=>document.querySelector('.device-recorder-page').scrollTop=0);
      if (process.env.RECORDER_LAYOUT_ARTIFACTS) {
        fs.mkdirSync(process.env.RECORDER_LAYOUT_ARTIFACTS,{recursive:true});
        await page.screenshot({path:path.join(process.env.RECORDER_LAYOUT_ARTIFACTS,`history-${width}x${height}.png`)});
      }
      await page.evaluate(()=>{deviceRecorderSession=null;renderDeviceRecorder()});
      const record=await page.evaluate(()=>{
        const panel=document.querySelector('.device-recorder-page'), head=panel.querySelector('.review-head').getBoundingClientRect();
        return {bottom:head.bottom,buttons:[...panel.querySelectorAll('.review-head button')].map(b=>b.getBoundingClientRect().bottom),width:panel.clientWidth,scrollWidth:panel.scrollWidth};
      });
      assert.ok(record.buttons.every(bottom=>bottom<=record.bottom),`record header clipped at ${width}`);
      assert.ok(record.scrollWidth<=record.width+1,`record horizontal overflow at ${width}`);
      await page.evaluate(() => {
        loadRecorderEvidence=async()=>{};
        deviceRecorderSession={id:'layout-session',status:'finished',app_package:'com.kfb.model',steps:Array.from({length:4},(_,i)=>({id:'step-'+i,sequence:i+1,type:'tap',semantic_description:['我的','打印记录','返回按钮','首页'][i],screenshot_path:'fixture.png',evidence_status:'ready'}))};
        renderDeviceRecorder();
        document.querySelector('[data-recorder-evidence]').src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="2412"><rect width="1080" height="2412" fill="#e0f2fe"/><text x="100" y="120" font-size="60">Screenshot top</text><text x="100" y="2300" font-size="60">Screenshot bottom</text></svg>');
      });
      await page.locator('[data-recorder-evidence]').evaluate(img=>img.decode());
      const timeline=await page.evaluate(()=>{
        const head=document.querySelector('.review-head.compact'), timeline=document.querySelector('.device-recorder-timeline'), img=document.querySelector('[data-recorder-evidence]');
        return {header:head.getBoundingClientRect().height,buttons:[...document.querySelectorAll('.device-recorder-track button')].map(b=>b.getBoundingClientRect().height),clipped:timeline.scrollHeight>timeline.clientHeight+1,imageWidth:img.getBoundingClientRect().width,frameWidth:img.parentElement.parentElement.clientWidth,panelWidth:document.querySelector('.device-recorder-page').clientWidth,scrollWidth:document.querySelector('.device-recorder-page').scrollWidth};
      });
      assert.ok(timeline.header<=90,`timeline header too tall: ${JSON.stringify(timeline)}`);
      assert.ok(timeline.buttons.every(h=>h<=80),`timeline cards stretched: ${JSON.stringify(timeline)}`);
      assert.equal(timeline.clipped,false,`screenshot has nested clipping at ${width}`);
      assert.ok(timeline.imageWidth<=timeline.frameWidth+1,`image overflows at ${width}`);
      assert.ok(timeline.scrollWidth<=timeline.panelWidth+1,`step layout overflows at ${width}`);
      await page.locator('.device-recorder-step-tools').scrollIntoViewIfNeeded();
      await page.locator('.device-recorder-step-tools summary').click();
      assert.ok(await page.locator('#recorder-edit-step-0').isVisible());
      if (process.env.RECORDER_LAYOUT_ARTIFACTS) await page.screenshot({path:path.join(process.env.RECORDER_LAYOUT_ARTIFACTS,`recording-${width}x${height}.png`)});
      console.log(`PASS history, live devices, compact timeline, full screenshot and editing ${width}x${height}`);
      await page.close();
    }
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
