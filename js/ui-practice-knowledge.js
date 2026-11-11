// Scoped knowledge search within the existing test workbench.
window.UiPracticeKnowledge = (() => {
  const base = '/api/platform/requirement-design/knowledge';
  const methods = {keyword:'关键词检索', vector:'向量检索'};
  const metrics = {
    recall_at_5:'前五条召回', citation_support:'引用支持答案', unknown_refusal:'无答案时回答未知',
    access_refusal:'越权问题拒答', zero_leak:'无泄露'
  };
  function add(parent, tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    parent.append(node);
    return node;
  }
  function detail(parent, title) {
    const item = add(parent, 'details');
    add(item, 'summary', title);
    return item;
  }
  function sourceId(row) {
    return row.document_id + ' / ' + row.paragraph_id;
  }
  function showResult(body, result) {
    add(body, 'h3', result.status === 'cited' ? '检索回答' : '未知');
    add(body, 'p', result.answer);
    add(body, 'small', (methods[result.method] || '检索') + ' · ' + result.document_count + ' 份材料 · ' + result.paragraph_count + ' 个段落');
    for (const row of result.results) {
      const section = detail(body, row.title + ' · ' + sourceId(row));
      add(section, 'blockquote', row.excerpt);
      add(section, 'p', '原文位置 · ' + row.locator);
      add(section, 'small', '材料版本 · ' + row.version);
    }
  }
  function showReport(body, report) {
    if (report.created_at) {
      const date = new Date(report.created_at);
      if (Number.isFinite(date.getTime())) add(body, 'small', '评估时间 · ' + date.toLocaleString('zh-CN', {hour12:false}));
    }
    for (const [method, label] of Object.entries(methods)) {
      const run = report.methods[method];
      if (!run) continue;
      add(body, 'h3', label);
      if (run.engine) add(body, 'small', run.engine + (Number.isFinite(run.elapsed_ms) ? ' · 总耗时 ' + run.elapsed_ms.toFixed(1) + ' 毫秒' : ''));
      const list = add(body, 'ul');
      for (const [key, title] of Object.entries(metrics)) {
        const pair = run.metrics[key];
        if (pair) add(list, 'li', title + ' · ' + pair.count + ' / ' + pair.total);
      }
    }
    const review = report.human_review;
    add(body, 'p', '人工审查 · 已审 ' + review.reviewed + ' / ' + review.total + ' 题');
    if (review.reviewed < review.total) add(body, 'p', '待审查 ' + (review.total - review.reviewed) + ' 题，请逐题核对预期来源和返回段落。');
    for (const row of report.questions) {
      const section = detail(body, row.id + ' · ' + row.question);
      add(section, 'p', '预期来源 · ' + (row.gold.map(sourceId).join('，') || '应拒答'));
      for (const [method, label] of Object.entries(methods)) {
        const result = row.results[method];
        if (!result) continue;
        const status = {cited:'有来源', unknown:'未知', error:'检索错误'}[result.status] || '未记录';
        const assessment = result.status === 'cited' && typeof result.citation_supported === 'boolean'
          ? (result.citation_supported ? ' · 引用支持预期答案' : ' · 引用未支持预期答案') : '';
        add(section, 'p', label + ' · ' + status + assessment + (result.top5.length ? ' · ' + result.top5.map(sourceId).join('，') : ''));
      }
    }
  }
  function attach(page, permitted) {
    const panel = detail(page, '测试知识检索');
    panel.className = 'ui-case-source-picker ui-practice-panel ui-requirement-design';
    const notice = add(panel, 'p');
    notice.setAttribute('role', 'status');
    const content = add(panel, 'div');
    const active = new Set();
    let epoch = 0;
    function clear() {
      epoch++;
      for (const controller of active) controller.abort();
      content.replaceChildren();
      notice.textContent = '';
    }
    function current(version) {
      return permitted() && panel.open && epoch === version;
    }
    async function request(path, body, version) {
      const controller = new AbortController();
      active.add(controller);
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const options = {headers: authHeaders(body ? {'Content-Type':'application/json'} : {}), signal: controller.signal, cache:'no-store', redirect:'error'};
        if (body) {
          options.method = 'POST';
          options.body = JSON.stringify(body);
        }
        const response = await nativeFetch(base + path, options);
        if ([401, 403].includes(response.status) && current(version)) {
          clear();
          notice.textContent = '当前登录或应用权限无法读取测试知识，请重新打开或检查权限。';
        }
        if (response.status === 404) throw Error('暂时没有可查看的评估报告。');
        if (!response.ok) throw Error('读取失败，请重新尝试。');
        const data = await response.json();
        if (!data.ok) throw Error('读取失败，请重新尝试。');
        return data;
      } finally {
        clearTimeout(timeout);
        active.delete(controller);
      }
    }
    function render(apps, version) {
      const form = add(content, 'form');
      const actions = add(form, 'div');
      actions.className = 'ui-practice-ai-controls';
      const app = add(add(actions, 'label', '应用'), 'select');
      for (const item of apps) add(app, 'option', item.name).value = item.id;
      const method = add(add(actions, 'label', '检索方式'), 'select');
      for (const [key, value] of Object.entries(methods)) add(method, 'option', value).value = key;
      const question = add(add(form, 'label', '测试问题'), 'textarea');
      question.required = true;
      question.maxLength = 500;
      question.rows = 2;
      question.placeholder = '例如，EP7 打印中的实时画面怎样唤醒？';
      const submit = add(form, 'button', '检索');
      submit.type = 'submit';
      const answer = add(content, 'div');
      let searchId = 0;
      function resetAnswer() {
        searchId++;
        answer.replaceChildren();
        notice.textContent = '';
        submit.disabled = !question.value.trim();
      }
      form.oninput = resetAnswer;
      form.onchange = resetAnswer;
      resetAnswer();
      form.onsubmit = async event => {
        event.preventDefault();
        if (!current(version) || submit.disabled || !question.value.trim()) return;
        const id = ++searchId;
        submit.disabled = true;
        answer.replaceChildren();
        notice.textContent = '正在检索…';
        try {
          const data = await request('/search', {app_id:app.value, method:method.value, question:question.value.trim()}, version);
          if (current(version) && id === searchId) {
            showResult(answer, data.result);
            notice.textContent = '';
          }
        } catch (err) {
          if (current(version) && id === searchId) notice.textContent = err.name === 'AbortError' ? '请求超时，请重试。' : err.message;
        } finally {
          if (current(version) && id === searchId) submit.disabled = false;
        }
      };
      const evaluation = detail(content, '查看检索评估与人工审查');
      const report = add(evaluation, 'div');
      let reportId = 0;
      evaluation.ontoggle = async () => {
        const id = ++reportId;
        report.replaceChildren();
        if (!evaluation.open || !current(version)) return;
        report.textContent = '正在读取评估…';
        try {
          const data = await request('/evaluation', null, version);
          if (current(version) && evaluation.open && id === reportId) {
            report.replaceChildren();
            showReport(report, data.report);
          }
        } catch (err) {
          if (current(version) && evaluation.open && id === reportId) report.textContent = err.message;
        }
      };
    }
    panel.ontoggle = async () => {
      clear();
      const version = epoch;
      if (!current(version)) return;
      notice.textContent = '正在读取可用应用…';
      try {
        const data = await request('', null, version);
        if (!current(version)) return;
        notice.textContent = '';
        render(data.apps, version);
      } catch (err) {
        if (current(version)) notice.textContent = err.name === 'AbortError' ? '请求超时，请重试。' : err.message;
      }
    };
    return () => {clear(); panel.remove();};
  }
  return {attach};
})();
