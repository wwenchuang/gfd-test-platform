/* Run with Node and a local browser; TRACKING_SOURCE_DOCX also validates the product's actual plan. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
test('reads merged requirements, unlabelled events, references and Word exposure rows', async () => {
  const browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);
    await page.addScriptTag({path:path.join(root, 'js/vendor/tracking/jszip.min.js')});
    await page.addScriptTag({path:path.join(root, 'js/mobile-tracking-source.js')});
    const checks = await page.evaluate(async () => {
      const s = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
      const r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
      const header = ['模块','节点','需要统计的行为','使用事件','页面标识','重点记录的信息'];
      const row = (n, values) => '<row r="'+n+'">'+values.map((v,i)=>v ? '<c r="'+String.fromCharCode(65+i)+n+'" t="inlineStr"><is><t>'+v+'</t></is></c>' : '').join('')+'</row>';
      const book = new JSZip();
      book.file('xl/workbook.xml','<workbook xmlns="'+s+'" xmlns:r="'+r+'"><sheets><sheet name="流程" r:id="rA"/><sheet name="指标" r:id="rB"/></sheets></workbook>');
      book.file('xl/_rels/workbook.xml.rels','<Relationships>'+['A','B'].map(k=>'<Relationship Id="r'+k+'" Type="'+r+'/worksheet" Target="worksheets/'+k+'.xml"/>').join('')+'</Relationships>');
      book.file('xl/worksheets/A.xml','<worksheet xmlns="'+s+'"><sheetData>'+row(1,['说明'])+row(3, header)+
        row(4,['','详情','点击挂件','AEC','page=detail','traceId'])+row(5,['','','点击书签'])+
        row(7,['','打印','打印成功'])+'</sheetData><mergeCells><mergeCell ref="B4:B5"/><mergeCell ref="D4:D5"/><mergeCell ref="E4:E5"/></mergeCells></worksheet>');
      book.file('xl/worksheets/B.xml','<worksheet xmlns="'+s+'"><sheetData>'+row(1,['指标','统计方式'])+row(2,['DAU','按用户去重'])+'</sheetData></worksheet>');
      const xlsx=await book.generateAsync({type:'uint8array'});
      const doc=new JSZip();
      doc.file('word/embeddings/source.xlsx',xlsx);
      const para=v=>'<w:p><w:r><w:t>'+v+'</w:t></w:r></w:p>';
      const tr=items=>'<w:tr>'+items.map(v=>'<w:tc>'+para(v)+'</w:tc>').join('')+'</w:tr>';
      doc.file('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+para('五、内容曝光埋点')+'<w:tbl>'+tr(['模块','第一展示位'])+tr(['首页','firstExposurePosition=home'])+'</w:tbl></w:body></w:document>');
      const sheets=await MobileTrackingSource.extract(new File([await doc.generateAsync({type:'uint8array'})],'方案.docx'));
      return {kinds:sheets.map(s=>s.kind), rules:MobileTrackingSource.checks(sheets),xlsx:MobileTrackingSource.checks(await MobileTrackingSource.extract(new File([xlsx],'方案.xlsx')))};
    });
    assert.deepEqual(checks.kinds,['business','reference','exposure']);
    assert.equal(checks.rules.length,4);
    assert.equal(checks.xlsx.length,3);
    assert.deepEqual(checks.rules[1].slice(0,5),['点击书签','AEC',true,'page=detail','详情']);
    assert.match(checks.rules[1][5],/第 5 行$/);
    assert.equal(checks.rules[0][6],'traceId');
    assert.equal(checks.rules[2][1],'');
    assert.equal(checks.rules[3][5],'方案.docx / 五、内容曝光埋点 第 2 行');
    if (process.env.TRACKING_SOURCE_DOCX) {
      await page.evaluate(() => {const input=document.createElement('input');input.type='file';document.body.append(input);});
      const planPath = process.env.TRACKING_SOURCE_DOCX;
      const body = require('node:fs').readFileSync(planPath);
      await page.locator('input').setInputFiles({name:path.basename(planPath),mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:body});
      const scopes=await page.evaluate(async()=>{
        const parsed=await MobileTrackingSource.extract(document.querySelector('input').files[0]);
        const rules=MobileTrackingSource.checks(parsed);
        return {business:rules.filter(r=>r[7]==='business'), exposure:rules.filter(r=>r[7]==='exposure'), refs:parsed.filter(s=>s.kind==='reference').length};
      });
      assert.equal(scopes.business.length,81);
      assert.equal(scopes.exposure.length,13);
      assert.equal(scopes.refs,4);
      assert.equal(scopes.business.find(r=>r[0]==='点击书签')[1],'AEC');
      assert.equal(scopes.business.find(r=>r[5].includes('Worksheet7.xlsx') && r[0]==='模型打印成功')[1],'');
      assert.match(scopes.exposure.find(r=>r[4]==='印章')[3],/firstExposurePosition=sealDetailModel\s+pagePre=/);
      assert.equal(new Set([...scopes.business,...scopes.exposure].map(r=>r[5])).size,94);
    }
  } finally { await browser.close(); }
});
