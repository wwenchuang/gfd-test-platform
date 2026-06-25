# Midscene YAML Style Guide

This guide is distilled from the existing Task YAML library under
`server-tasks` and `server-tasks-all`. It is a local platform convention, not a
generic Midscene tutorial.

## Observed Sample

- 76 YAML files were reviewed.
- All sampled files keep `# baseline.*` metadata comments.
- Common flow items: `runAdbShell`, `launch`, `sleep`, `ai`, `aiTap`,
  `aiInput`, `aiKeyboardPress`, `aiScroll`, `aiWaitFor`, `aiAssert`.
- Common timeouts: `30000`, `45000`, `60000`, `120000`, `240000`.

## Stable Structure

Each generated baseline task should follow this shape:

1. Clean app state.
2. Launch the target app.
3. Wait for a stable home or entry page.
4. Handle popups, permission dialogs, ads, guide overlays, and leftover tasks.
5. Navigate by visible business text or stable icon description.
6. Wait for the next target page, button, list, empty state, or result signal.
7. Assert a business-visible result.
8. Cancel or exit destructive/long-running flows.
9. Force-stop the app at the end.

## Local 3D Printing Conventions

For `com.kfb.model` 3D printing flows:

- Start from App 首页 unless the case explicitly requires another page.
- After launch, handle leftover slicing/printing tasks:
  - If the home page shows `模型处理完成` or `去打印`, enter print preview.
  - Tap `取消打印`.
  - Confirm `确定` if a cancel dialog appears.
  - Return to a stable home/detail page.
- Long model generation/slicing waits may use `aiWaitFor` with `240000`.
- Print flows should stop before real printing when possible:
  - Wait for `下一步`, `取消打印`, progress completion, or preview state.
  - Cancel and confirm instead of leaving a queued print task behind.

## Local Xiaobai Learning Conventions

For `com.xbxxhz.box` flows:

- Popups are common. Use conditional `ai` instructions for login prompts,
  ads, permission dialogs, guide overlays, satisfaction surveys, and import
  dialogs.
- External import paths, WeChat file pickers, albums, and system permission
  dialogs are unstable; generate them only when the requirement explicitly asks
  for that path and mark data requirements clearly.
- Prefer `aiWaitFor` target UI conditions over repeated long sleeps.

## Step Wording

Good step wording:

- `点击底部 Tab「我的」`
- `点击「打印记录」入口`
- `点击右上角放大镜搜索图标或搜索入口`
- `在当前页面的搜索输入框输入“保龄球”，并按 Enter 搜索`
- `点击列表第一条 .stl 文件的文件名文字区域，不点击右侧更多图标`

Avoid:

- Coordinates, XPath, CSS selector, view hierarchy.
- “点击左上角第三个按钮” when visible text/icon meaning is available.
- Fixed long sleeps as the primary synchronization method.
- Assertions such as “页面正常”“跳转成功”“结果符合预期”.

## Assertion Style

Prefer assertions that tolerate real data differences:

- Page title is visible.
- Target entry/button/tab is visible or selected.
- List area is shown, or an empty-state prompt is shown.
- No loading failure, network error, blank page, or blocking popup is shown.
- For dynamic lists, assert list region or empty state, not a fixed item count.

## Automation vs Manual

Move scenarios to `manual_cases` or mark as待准备 when they require:

- Real payment.
- Real deletion or irreversible state change.
- Switching account/login state.
- Backend data seeding.
- Specific coupons, addresses, documents, albums, or external app state.
- Real printer/device-side completion that cannot be safely cancelled.
- Search no-result fallback, account-specific work lists, empty states,
  pagination bottoms, first-time permission dialogs, model evaluation scores,
  generation interruption, duplicate-click protection, or old-entry absence
  checks unless the test data/app version has been explicitly prepared.

## Search and File Picker

- Use `aiInput` plus `aiKeyboardPress: Enter` for search flows.
- In file pickers, describe the file type and safe tap region.
- Do not click more-menu icons when selecting a file.

## Executability Guardrails

- 新生成 YAML 必须优先满足“能独立执行”：前置启动、稳定起点、弹窗处理、
  清晰导航、可见断言、收尾清理都要齐。
- 普通页面切换只允许短 `sleep` 稳定；接口、加载、生成、切片、列表刷新等
  异步状态必须用 `aiWaitFor` + `timeout` 等待真实 UI 信号。
- `aiTap`、`aiInput`、`aiWaitFor`、`aiAssert` 的提示词必须包含页面/区域/控件
  语义，不能只写“确认”“下一步”“返回”“页面正常”。
- 每条自动化 task 至少要有一个 `aiAssert` 或业务目标型 `aiWaitFor`，否则只能
  作为草稿待人工补充。
- 如果 Figma/截图没有覆盖关键入口或结果页，不能编造控件；把缺口写入
  `manual_cases`、`data_requirements` 或人工确认建议。
- 自动执行 YAML 优先做稳定冒烟：入口可达、页面核心模块、按钮/Tab 状态、
  列表区域或空态二选一、无错误弹窗。完整覆盖继续放在 `.mm` 和
  `manual_cases`，不要把所有验收点都塞进 Runner。
