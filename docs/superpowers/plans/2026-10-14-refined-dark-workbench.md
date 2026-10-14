# Refined Dark Workbench Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan in the current isolated worktree.

**Goal:** 将开始测试改为精致深色工作台，保持真实数据、操作语义和性能边界。
**Architecture:** 模板仅增加语义容器/装饰图标；独立CSS限定在简化工作流内，现有事件与数据属性保持。原CSS作基础，新文件负责主题及布局。
**Tech Stack:** 原生JavaScript、CSS、SVG；既有Node/JSDOM和Chrome工具。

## Global Constraints

不新增网络请求/轮询、依赖、虚假数据或派发行为。保留原品牌及真实截图；小屏完整显示图片，控件支持键盘和减少动态效果。

### Task 1: 结构与样式
- [x] 修改 `js/ui-test-home.js`：增加模式属性、标题装饰、静态流程提示、用例标题与图标；原data属性/事件不变，新增字段只从当前已有数据读取。
- [x] 新建 `css/ui-workbench.css`：石墨/薄荷青配色、清单细线分隔、宽屏并排设备、底部执行栏、<=760px单列、自适应图片；所有选择器限定简化入口。
- [x] 修改 `task-manager.html`：加载版本化CSS并更新home脚本缓存。

### Task 2: 功能与视觉验收
- [x] `node --check js/ui-test-home.js`；`node --test tests/ui_simple_workflow_check.js tests/ui_simple_entry_check.js tests/ui_batch_view_check.js tests/ui_batch_report_check.js`，要求全部通过。
- [x] `python3 tests/frontend_static_checks.py`；`python3 tests/backend_static_checks.py`；`git diff --check`。
- [x] Chrome在真实页面临时加载候选模板/样式验证1634、1366、390布局。测试搜索、选择、发版控件和旧报告；不提交执行任务。结束重新加载恢复线上版本。
- [ ] 提交、部署、确认版本和资源一致，在Chrome重新验证真实页面；记录证据与未完成项。
