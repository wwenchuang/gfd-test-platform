// loading-manager.js
// 统一加载状态管理器 — 为耗时操作（AI 调用、Agent 启动等）提供视觉反馈

const LoadingManager = {
  _overlayEl: null,

  /**
   * 显示全局加载遮罩
   * @param {string} message - 提示文案
   */
  showOverlay(message = '处理中...') {
    this.hideOverlay(); // 防重复
    const el = document.createElement('div');
    el.className = 'loading-overlay';
    el.innerHTML = `
      <div class="loading-overlay-content">
        <div class="loading-spinner"></div>
        <p class="loading-message">${typeof escapeHtml === 'function' ? escapeHtml(message) : message}</p>
      </div>`;
    document.body.appendChild(el);
    this._overlayEl = el;
  },

  /**
   * 隐藏全局加载遮罩
   */
  hideOverlay() {
    if (this._overlayEl) {
      this._overlayEl.classList.add('fade-out');
      const el = this._overlayEl;
      setTimeout(() => el.remove(), 300);
      this._overlayEl = null;
    }
  },

  /**
   * 设置按钮为加载态
   * @param {HTMLElement|string} btn - 按钮元素或选择器
   * @param {string} label - 加载中显示的文字
   * @returns {Function} restore - 调用此函数恢复按钮原状
   */
  setButtonLoading(btn, label = '处理中...') {
    const el = typeof btn === 'string' ? document.querySelector(btn) : btn;
    if (!el) return () => {};
    const origHTML = el.innerHTML;
    const origDisabled = el.disabled;
    el.disabled = true;
    el.classList.add('btn-loading');
    el.innerHTML = `<span class="btn-spinner"></span> ${label}`;
    return function restore() {
      el.innerHTML = origHTML;
      el.disabled = origDisabled;
      el.classList.remove('btn-loading');
    };
  },

  /**
   * 便捷包装：执行异步操作并自动管理加载状态
   * @param {Function} asyncFn - 异步函数
   * @param {Object} opts - 选项
   * @param {HTMLElement|string} [opts.btn] - 按钮
   * @param {string} [opts.btnLabel] - 按钮加载文字
   * @param {string} [opts.overlay] - 遮罩文字（传入则显示遮罩）
   */
  async withLoading(asyncFn, opts = {}) {
    let restoreBtn = null;
    try {
      if (opts.btn) restoreBtn = this.setButtonLoading(opts.btn, opts.btnLabel || '处理中...');
      if (opts.overlay) this.showOverlay(opts.overlay);
      return await asyncFn();
    } finally {
      if (restoreBtn) restoreBtn();
      if (opts.overlay) this.hideOverlay();
    }
  }
};
