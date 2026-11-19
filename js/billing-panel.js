async function showBillingPage() {
  const host = document.getElementById('editor-area');
  const page = document.createElement('section');
  page.className = 'billing-page';
  page.innerHTML = `<header><div><h1>AI 费用账单</h1><p>阿里云百炼 · 最近 7 天 · 人民币</p></div><button type="button">刷新</button></header>
    <section class="billing-sheet"><div class="billing-sheet-head"><h2>每日应付金额</h2><small></small></div>
    <div class="billing-state" role="status">正在读取账单…</div></section>`;
  host.replaceChildren(page);
  const button = page.querySelector('button');
  button.onclick = showBillingPage;
  button.disabled = true;
  const state = page.querySelector('[role=status]');
  try {
    const response = await fetch('/api/platform/ai-billing', {headers: authHeaders(), cache: 'no-store'});
    if (!response.ok) throw Error(response.status === 401 ? '登录已过期，请重新登录。' : response.status === 403 ? '当前账号没有账单查看权限。' : '账单暂时无法读取，请稍后刷新。');
    const { bill } = await response.json();
    if (!bill) {
      state.innerHTML = '<strong>等待首次同步</strong><p>服务器取得百炼账单后，每日费用会显示在这里。</p>';
      return;
    }
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>日期</th><th>明细数</th><th>应付金额（元）</th></tr></thead>';
    const body = table.createTBody();
    for (const day of bill.days) {
      const row = body.insertRow();
      for (const value of [day.date, day.count, day.amount ?? '暂无明细']) row.insertCell().textContent = value;
    }
    page.querySelector('small').textContent = '上次同步 ' + bill.updated;
    state.replaceWith(table);
  } catch (error) {
    state.textContent = error.message;
  } finally { button.disabled = false; }
}
