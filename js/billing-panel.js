async function showBillingPage() {
  const host = document.getElementById('editor-area');
  const page = document.createElement('section');
  page.className = 'billing-page';
  page.innerHTML = `<header><div><h1>Token 用量</h1><p>百炼北京工作空间 · 实时推理 · 最近 7 天</p></div><button type="button">刷新</button></header>
    <section class="billing-sheet"><div class="billing-sheet-head"><h2>每日 Token 用量</h2><small></small></div>
    <div class="billing-state" role="status">正在读取用量…</div></section>`;
  host.replaceChildren(page);
  const button = page.querySelector('button');
  button.onclick = showBillingPage;
  button.disabled = true;
  const state = page.querySelector('[role=status]');
  try {
    const response = await fetch('/api/platform/token-usage', {headers: authHeaders(), cache: 'no-store'});
    if (!response.ok) throw Error(response.status === 401 ? '登录已过期，请重新登录。' : response.status === 403 ? '当前账号没有用量查看权限。' : '用量暂时无法读取，请稍后刷新。');
    const { bill } = await response.json();
    if (!bill) {
      state.innerHTML = '<strong>等待首次同步</strong><p>首次取得模型用量后，这里会显示输入、输出和总 Token。</p>';
      return;
    }
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>日期</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th></tr></thead>';
    const body = table.createTBody();
    for (const day of bill.days) {
      const row = body.insertRow();
      for (const value of [day.date, ...['input_tokens','output_tokens','total_tokens'].map(key => day[key]?.toLocaleString('zh-CN') ?? '暂无数据')]) row.insertCell().textContent = value;
    }
    page.querySelector('small').textContent = '统计截至 ' + bill.through;
    state.replaceWith(table);
  } catch (error) {
    state.textContent = error.message;
  } finally { button.disabled = false; }
}
