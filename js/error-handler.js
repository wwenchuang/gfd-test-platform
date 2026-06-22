// error-handler.js
// 统一错误拦截器 — 将技术性报错翻译为用户能理解的中文

/**
 * 错误分类与友好文案
 * match 签名：(status: number, message: string) => boolean
 */
const ERROR_FRIENDLY_MAP = [
  // HTTP 状态码映射
  { match: (s, m) => s === 401, icon: '🔐', msg: '登录已过期，请重新登录' },
  { match: (s, m) => s === 403, icon: '🚫', msg: '无权限执行此操作' },
  { match: (s, m) => s === 404, icon: '❌', msg: '请求的资源不存在' },
  { match: (s, m) => s === 413, icon: '📦', msg: '上传内容过大，当前建议控制在 300M 以内' },
  { match: (s, m) => s === 502 || s === 504, icon: '⚠️', msg: '服务暂时不可用，请稍后重试' },
  // 关键词匹配（后端 Python 异常）
  { match: (s, m) => /is not defined|NameError/.test(m), icon: '⚠️', msg: '服务配置异常，请联系管理员或稍后重试' },
  { match: (s, m) => /ImportError|ModuleNotFound/.test(m), icon: '⚠️', msg: '服务组件加载失败，请稍后重试' },
  { match: (s, m) => /FileNotFoundError/.test(m), icon: '❌', msg: '文件不存在或已被删除' },
  { match: (s, m) => /ValueError|invalid/.test(m), icon: '✏️', msg: '输入格式有误，请检查后重试' },
  { match: (s, m) => /timeout|timed? ?out/i.test(m), icon: '⏱️', msg: 'AI 处理超时，请稍后重试' },
  { match: (s, m) => /yaml|YAML/.test(m), icon: '✏️', msg: 'YAML 格式有误，请检查缩进和语法' },
  { match: (s, m) => /sonic/i.test(m), icon: '🔗', msg: '测试设备连接异常，请检查设备配置' },
  { match: (s, m) => /Permission|权限/.test(m), icon: '🚫', msg: '权限不足，请联系管理员' },
  { match: (s, m) => /disk|space|磁盘/.test(m), icon: '💾', msg: '存储空间不足，请清理后重试' },
];

/**
 * 将原始错误翻译为用户友好的文案
 * @param {number} status - HTTP 状态码
 * @param {string} rawMessage - 原始错误信息
 * @returns {{ icon: string, msg: string }}
 */
function friendlyError(status, rawMessage) {
  const raw = String(rawMessage || '');
  for (const rule of ERROR_FRIENDLY_MAP) {
    if (rule.match(status, raw)) {
      return { icon: rule.icon, msg: rule.msg };
    }
  }
  // 兜底：隐藏技术细节
  return { icon: '❌', msg: '操作失败，请刷新后重试' };
}

/**
 * 显示友好错误（集成 showToast）
 * @param {number} status - HTTP 状态码
 * @param {string} rawMessage - 原始错误信息
 */
function showFriendlyError(status, rawMessage) {
  const { icon, msg } = friendlyError(status, rawMessage);
  showToast(`${icon} ${msg}`, 'error');
}
