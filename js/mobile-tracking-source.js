// Keep every rule tied to its original sheet and row, including Word body tables.
window.MobileTrackingSource = (() => {
  const nodes = (root, tag) => Array.from(root.getElementsByTagNameNS('*', tag));
  const texts = node => nodes(node, 't').map(t => t.textContent).join('');

  async function xml(zip, path) {
    const member = zip.file(path);
    if (!member) throw Error('文件缺少 ' + path);
    const doc = new DOMParser().parseFromString(await member.async('string'), 'application/xml');
    if (doc.getElementsByTagName('parsererror').length) throw Error(path + ' 的 XML 不完整');
    return doc;
  }

  function position(ref) {
    const match = /^([A-Z]+)([1-9]\d*)$/.exec(ref || '');
    if (!match) return null;
    let col = 0;
    for (const char of match[1]) col = col * 26 + char.charCodeAt(0) - 64;
    const row = Number(match[2]);
    return col <= 16384 && row <= 1048576 ? {col:col - 1, row} : null;
  }

  function readRows(doc, strings) {
    const rows = [];
    for (const row of nodes(doc, 'row')) {
      const values = [];
      for (const cell of nodes(row, 'c')) {
        const pos = position(cell.getAttribute('r'));
        if (!pos) continue;
        const value = nodes(cell, 'v')[0]?.textContent || '';
        const type = cell.getAttribute('t');
        values[pos.col] = type === 's' ? strings[Number(value)] || '' : type === 'inlineStr' ? texts(cell) : value;
      }
      if (values.some(v => v.trim())) rows.push({n:Number(row.getAttribute('r')), values});
    }
    // Expand only into rows that already contain data and columns used in their header.
    // This preserves blank separators and does not allocate a row per cell of a large merge.
    const usedWidth = rows.reduce((width,row) => Math.max(width,row.values.length), 0);
    const byNumber = new Map(rows.map(row => [row.n, row]));
    for (const merge of nodes(doc, 'mergeCell')) {
      const [start, end] = (merge.getAttribute('ref') || '').split(':').map(position);
      const value = start && byNumber.get(start.row)?.values[start.col];
      if (!end || !value) continue;
      const lastCol = Math.min(end.col, usedWidth - 1);
      for (const row of rows) {
        if (row.n < start.row || row.n > end.row) continue;
        for (let c = start.col; c <= lastCol; c++) if (!row.values[c]) row.values[c] = value;
      }
    }
    return rows;
  }

  async function workbook(name, bytes) {
    const zip = await JSZip.loadAsync(bytes);
    const strings = zip.file('xl/sharedStrings.xml') ? nodes(await xml(zip, 'xl/sharedStrings.xml'), 'si').map(texts) : [];
    const rels = nodes(await xml(zip, 'xl/_rels/workbook.xml.rels'), 'Relationship');
    const sheets = [];
    for (const tab of nodes(await xml(zip, 'xl/workbook.xml'), 'sheet')) {
      const rel = rels.find(r => r.getAttribute('Id') === tab.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id'));
      if (!rel || rel.getAttribute('TargetMode') === 'External') throw Error('缺少内部工作表引用');
      if (!(rel.getAttribute('Type') || '').endsWith('/worksheet')) continue;
      const path = new URL(rel.getAttribute('Target'), 'https://local.invalid/xl/').pathname.slice(1);
      const rows = readRows(await xml(zip, path), strings);
      const header = rows.findIndex(row => row.values.includes('使用事件') && row.values.includes('需要统计的行为'));
      sheets.push({name, sheet:tab.getAttribute('name'), rows, header, kind:header >= 0 ? 'business' : 'reference'});
    }
    return sheets;
  }

  function wordTables(doc, name) {
    const result = [];
    const body = nodes(doc, 'body')[0];
    let heading = '', index = 0;
    for (const child of Array.from(body?.children || [])) {
      if (child.localName === 'p') {
        const title = texts(child).trim();
        if (/^([一二三四五六七八九十]+、|\d+[.、]\s*)/.test(title)) heading = title;
      }
      if (child.localName !== 'tbl') continue;
      index++;
      const rows = Array.from(child.children).filter(n => n.localName === 'tr').map((tr, i) => ({
        n:i + 1,
        values:Array.from(tr.children).filter(n => n.localName === 'tc').map(tc =>
          Array.from(tc.children).filter(n => n.localName === 'p')
            .map(p => Array.from(p.getElementsByTagNameNS('*', '*')).filter(e => ['t','br','cr'].includes(e.localName))
              .map(e => e.localName === 't' ? e.textContent : '\n').join('')).filter(Boolean).join('\n').trim())
      }));
      const header = rows.findIndex(r => r.values.includes('模块') && r.values.includes('第一展示位'));
      if (header >= 0) result.push({name, sheet:heading || '正文表 '+index, rows, header, kind:'exposure'});
    }
    return result;
  }

  async function extract(file) {
    const bytes = await file.arrayBuffer();
    if (!file.name.toLowerCase().endsWith('.docx')) return workbook(file.name, bytes);
    const doc = await JSZip.loadAsync(bytes);
    const entries = doc.file(/^word\/embeddings\/[^/]+\.xlsx$/i)
      .sort((a,b) => a.name.localeCompare(b.name, 'en', {numeric:true}));
    const sheets = [];
    for (const entry of entries) sheets.push(...await workbook(entry.name.split('/').pop(), await entry.async('uint8array')));
    sheets.push(...wordTables(await xml(doc, 'word/document.xml'), file.name));
    if (!sheets.length) throw Error('没有可解析的内嵌工作表或正文曝光表');
    return sheets;
  }

  function checks(sheets) {
    const results = [];
    for (const s of sheets) {
      if (s.kind === 'reference') continue;
      const header = s.rows[s.header]?.values || [];
      const idx = title => header.indexOf(title);
      const prop = header.findIndex(v => v?.includes('标识'));
      for (const {n,values} of s.rows.slice(s.header + 1)) {
        const origin = s.name + ' / ' + s.sheet + ' 第 ' + n + ' 行';
        if (s.kind === 'exposure') {
          const module = values[idx('模块')], rule = values[idx('第一展示位')];
          if (module && rule) results.push(['曝光 '+module,'CS',true,rule,module,origin,'',s.kind]);
        } else {
          const event = values[idx('使用事件')] || '';
          const action = values[idx('需要统计的行为')] || '';
          if (!action && !event) continue;
          const module = values[1] || s.sheet;
          results.push([action || module,event,true,values[prop] || '',module,origin,values[idx('重点记录的信息')] || '',s.kind]);
        }
      }
    }
    return results;
  }

  return {extract, checks};
})();
