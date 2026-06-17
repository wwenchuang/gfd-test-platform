// empty-states.js
// 空状态引导配置与渲染组件

/**
 * 空状态引导配置
 */
const EMPTY_STATES = {
  reports: {
    icon: '📈', title: '还没有执行报告',
    desc: '执行测试后，报告会自动出现在这里。',
    actions: [{ label: '去执行用例', fn: "activateWorkflow('execute')" }],
    tips: ['先在「用例资产」或「AI 生成」准备好测试脚本', '然后在「调试执行」中运行', '完成后自动生成报告']
  },
  bug_drafts: {
    icon: '🐛', title: '还没有缺陷草稿',
    desc: '测试失败时，AI 会自动生成缺陷报告草稿，你可以编辑后提交。',
    actions: [{ label: '执行测试看看', fn: "activateWorkflow('execute')" }],
    tips: ['缺陷草稿来自失败的测试用例', 'AI 会自动收集日志和截图', '确认后可提交到飞书群']
  },
  agent_history: {
    icon: '🕐', title: '还没有运行记录',
    desc: '启动 Agent 后，所有的规划、生成、执行步骤都会记录在这里。',
    actions: [{ label: '启动 Agent', fn: "activateWorkflow('dashboard')" }],
    tips: ['在「Agent 工作台」输入测试目标并启动', 'Agent 会自动完成 规划→生成→执行→分析', '每一步都可以在这里查看详情']
  },
  agent_confirm: {
    icon: '✋', title: '暂无待确认项',
    desc: '当 Agent 遇到高风险操作时，会在这里等待你的确认。',
    actions: [],
    tips: ['删除、修改基线、提交缺陷等操作需要确认', '可在「系统设置」调整风险策略']
  },
  knowledge_empty: {
    icon: '📸', title: '该应用还没有页面知识',
    desc: '保存页面截图和关键元素信息，让 AI 生成时有可靠的上下文。',
    actions: [{ label: '创建页面知识', fn: "showKnowledgeManager && showKnowledgeManager()" }],
    tips: ['可以上传截图或从 Figma 导入', 'AI 生成用例时会参考这些页面信息']
  },
  search_no_result: {
    icon: '🔍', title: '没有找到匹配结果',
    desc: '根据当前条件没有找到符合的用例。',
    actions: [{ label: '清空搜索', fn: "clearSearch && clearSearch()" }, { label: '新建用例', fn: "activateWorkflow('generate')" }],
    tips: ['检查应用筛选是否正确', '尝试其他关键词']
  },
  failure_analysis: {
    icon: '🔬', title: '暂无失败分析',
    desc: '执行测试后，如果有失败用例，AI 会在这里给出结构化分析。',
    actions: [{ label: '去执行用例', fn: "activateWorkflow('execute')" }],
    tips: ['先运行测试，有失败时自动生成分析', '分析包含失败原因、修复建议']
  },
  modules_empty: {
    icon: '🗂️', title: '还没有用例模块',
    desc: '模块用来组织测试用例，比如「首页」「打印」等。',
    actions: [{ label: '创建模块', fn: "showAddModule && showAddModule()" }],
    tips: ['每个模块可以包含多个测试脚本', '模块按应用动态分类']
  }
};

/**
 * 渲染空状态 HTML
 * @param {string} key - EMPTY_STATES 中的 key
 * @param {string} [customDesc] - 覆盖默认描述
 * @returns {string} HTML 字符串
 */
function renderEmptyState(key, customDesc) {
  const cfg = EMPTY_STATES[key];
  if (!cfg) return '<div class="empty-state-simple">暂无内容</div>';
  const actionsHtml = (cfg.actions || []).map(a =>
    `<button class="btn-sm btn-primary" onclick="${a.fn}">${a.label}</button>`
  ).join(' ');
  const tipsHtml = (cfg.tips || []).map(t => `<li>${t}</li>`).join('');
  return `
    <div class="empty-state">
      <div class="empty-state-icon">${cfg.icon}</div>
      <h3 class="empty-state-title">${cfg.title}</h3>
      <p class="empty-state-desc">${customDesc || cfg.desc}</p>
      ${actionsHtml ? `<div class="empty-state-actions">${actionsHtml}</div>` : ''}
      ${tipsHtml ? `<div class="empty-state-tips"><p class="empty-state-tips-label">💡 快速提示：</p><ul>${tipsHtml}</ul></div>` : ''}
    </div>`;
}
