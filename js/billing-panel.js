// Read the exported bill locally so account details stay in the viewer's browser.
function dailyBill(csv) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const c = csv[i];
    if (c === '"') {
      if (quoted && csv[i + 1] === '"') { field += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && c === ',') {
      row.push(field.trim()); field = '';
    } else if (!quoted && c === '\n') {
      row.push(field.trim()); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (quoted) throw Error('账单 CSV 引号不完整');
  row.push(field.trim()); rows.push(row);
  const header = rows.shift();
  const date = header.findIndex(x => ['时间','账单日期','账单时间','日期'].includes(x));
  const amount = header.findIndex(x => /^应付金额(?:[（(]元[)）])?$/.test(x));
  if (date < 0 || amount < 0) throw Error('账单需要包含时间和应付金额两列');
  const days = new Map();
  for (const [i, fields] of rows.entries()) {
    if (fields.every(x => !x)) continue;
    const day = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:$|[ T])/.exec(fields[date] || '');
    const value = (fields[amount] || '').replace(/,/g,'');
    if (!day || !/^-?\d+(?:\.\d+)?$/.test(value)) throw Error('请检查第 ' + (i+2) + ' 行日期和金额');
    const key = [day[1],day[2].padStart(2,'0'),day[3].padStart(2,'0')].join('-');
    const item = days.get(key) || { count: 0, sum: 0 };
    item.count++;
    item.sum += Number(value);
    days.set(key,item);
  }
  return [...days].sort(([a],[b]) => b.localeCompare(a));
}
if (typeof module !== 'undefined') module.exports = dailyBill;
if (typeof document !== 'undefined') {
  document.querySelector('#ai-bill-file').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    const status = document.querySelector('#ai-bill-status');
    try {
      const bytes = await file.arrayBuffer();
      let csv;
      try { csv = new TextDecoder('utf-8',{fatal:true}).decode(bytes); }
      catch { csv = new TextDecoder('gb18030').decode(bytes); }
      const days = dailyBill(csv);
      const body = document.querySelector('#ai-bill-days');
      body.replaceChildren();
      for (const [date,item] of days) {
        const row = body.insertRow();
        for (const value of [date,item.count,item.sum.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:8})]) row.insertCell().textContent = value;
      }
      status.textContent = file.name + ' · ' + days.length + ' 天';
    } catch (error) { status.textContent = '导入失败：' + error.message + '。保留上次结果。'; }
  });
}

async function openAiBilling() {
  const dialog = document.querySelector('#ai-billing');
  const status = dialog.querySelector('#ai-bill-sync');
  const cloud = dialog.querySelector('#ai-bill-cloud');
  cloud.replaceChildren();
  dialog.showModal();
  status.textContent = '正在读取最近七天的云账单…';
  try {
    const response = await fetch('/api/platform/ai-billing', {
      headers: {Authorization: 'Bearer ' + (sessionStorage.getItem('sessionToken') || '')},
      cache: 'no-store'
    });
    if (!response.ok) throw Error(response.status === 403 ? '同步账单仅管理员可查看' : '读取云账单失败');
    const {bill} = await response.json();
    if (!bill) { status.textContent = '等待首次账单同步'; return; }
    status.textContent = '最近七天 · 应付金额 · 上次同步 ' + bill.updated;
    const list = document.createElement('ul');
    for (const day of bill.days) {
      const line = document.createElement('li');
      line.textContent = day.date + '　' + (day.amount === null ? '暂无账单明细' : day.amount + ' 元（' + day.count + ' 条）');
      list.append(line);
    }
    cloud.append(list);
  } catch (e) { status.textContent = e.message; }
}
