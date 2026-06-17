/**
 * 表单分步向导控制器
 */
const FormSteps = {
  currentStep: 0,
  totalSteps: 0,
  containerSelector: '',

  /**
   * 初始化分步表单
   * @param {string} containerSelector - 弹窗容器选择器
   */
  init(containerSelector) {
    this.containerSelector = containerSelector;
    const container = document.querySelector(containerSelector);
    if (!container) return;
    const steps = container.querySelectorAll('.form-step-content');
    this.totalSteps = steps.length;
    this.currentStep = 0;
    this.updateUI();
  },

  /**
   * 跳转到指定步骤
   */
  goTo(step) {
    if (step < 0 || step >= this.totalSteps) return;
    this.currentStep = step;
    this.updateUI();
  },

  next() { this.goTo(this.currentStep + 1); },
  prev() { this.goTo(this.currentStep - 1); },

  /**
   * 更新步骤指示器和内容区的显示/隐藏
   */
  updateUI() {
    const container = document.querySelector(this.containerSelector);
    if (!container) return;
    // 更新步骤指示器
    container.querySelectorAll('.step-indicator').forEach((el, i) => {
      el.classList.toggle('active', i === this.currentStep);
      el.classList.toggle('done', i < this.currentStep);
    });
    // 更新内容区
    container.querySelectorAll('.form-step-content').forEach((el, i) => {
      el.style.display = i === this.currentStep ? 'block' : 'none';
    });
    // 更新按钮
    const prevBtn = container.querySelector('.step-prev-btn');
    const nextBtn = container.querySelector('.step-next-btn');
    const submitBtn = container.querySelector('.step-submit-btn');
    if (prevBtn) prevBtn.style.display = this.currentStep === 0 ? 'none' : 'inline-block';
    if (nextBtn) nextBtn.style.display = this.currentStep < this.totalSteps - 1 ? 'inline-block' : 'none';
    if (submitBtn) submitBtn.style.display = this.currentStep === this.totalSteps - 1 ? 'inline-block' : 'none';
  }
};
