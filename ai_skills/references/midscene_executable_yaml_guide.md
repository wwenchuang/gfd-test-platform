# Midscene Executable YAML Guide

This guide turns requirement-derived cases into YAML that is more likely to run
stably in Midscene and Sonic. It complements `yaml_style_guide.md`.

## Sources

- Midscene YAML script runner documentation:
  https://midscenejs.com/yaml-script-runner
- Midscene Android automation documentation:
  https://midscenejs.com/automate-with-android
- Local baseline samples under Task YAML library and Sonic execution reports.

## Executability Gate

Only generate an automated YAML task when all checks pass:

1. The start page is clear, or the task can reset to a stable App home page.
2. Each navigation step targets visible text, a stable icon meaning, or a
   clearly described UI region.
3. At least one assertion checks a visible business result, not just
   "success" or "normal".
4. Required data, account state, files, device state, and hardware state are
   available or safely mockable.
5. The task can clean up after itself and must not leave a real payment,
   deletion, print, or irreversible state behind.

If any item fails, keep the scenario in `manual_cases` with a concrete
preparation suggestion instead of forcing YAML.

## Action Rules

- Use `launch` near the start for Android app execution.
- Use short `sleep` only for stabilization after launch, back, or page change.
  Avoid repeated blind sleeps.
- Use `aiWaitFor` for asynchronous UI states such as page loaded, list loaded,
  button visible, empty state visible, progress complete, or dialog shown.
- Use `aiTap` only with an unambiguous visible target:
  "底部 Tab「我的」", "右上角搜索图标", "弹窗按钮「确认」".
- Use `aiInput` with sibling `value`; prefer `mode: replace` when filling
  search boxes or text fields.
- Use `aiKeyboardPress: Enter` for search submission when the UI supports it.
- Use `aiAssert` for the final business-visible result.
- Use `runAdbShell: "input keyevent 4"` for Android back when a native back is
  expected.
- Use `terminate` or `runAdbShell: "am force-stop <package>"` for cleanup.

## Prompt Quality

Bad prompts are expensive and unstable. Avoid:

- One-word prompts such as "确认", "下一步", "返回" unless the surrounding page
  context is included.
- Purely abstract prompts such as "完成操作", "结果符合预期", "页面正常".
- Coordinate, XPath, CSS selector, or hierarchy-based descriptions.
- Using a Figma page that is unrelated to the current requirement.

Good prompts include page or region context:

- "耗材确认弹窗中的「确认」按钮"
- "模型详情页底部「去打印」按钮"
- "打印配置页中耗材颜色选项「红色」"
- "搜索结果页列表区域或空态提示"

## Speed Rules

- Prefer local static checks and skill constraints over extra model calls.
- Do not call visual grounding for unrelated Figma pages.
- Use the smallest relevant screenshot set that covers requirement, device
  shape, and state variants; do not send duplicate or low-match pages.
- Keep task flow direct. A long chain with many uncertain taps is slower and
  less reliable than a shorter task plus a manual/preparation case.

## Manual Case Rules

Move to manual/preparation when the requirement depends on:

- Real printer completion, hardware feeding state, or device-side long running
  completion that cannot be safely cancelled.
- Payment, destructive deletion, coupon/address/account-specific state.
- Backend data seeding, cloud sync timing, concurrent users, network blocking.
- External apps, albums, file pickers, or permissions without stable test data.

Manual cases must still include priority, risk, preparation suggestion, and
report checkpoints.
