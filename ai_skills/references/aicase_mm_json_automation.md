# AiCase mm / JSON / Midscene Automation Reference

This reference captures the external AiCase skill pattern used by this
platform when generating test assets.

## Required Flow

1. Read requirement documents, UI drafts, screenshots, and page knowledge.
2. Extract business goals, feature points, rules, roles, data assumptions,
   visible outcomes, risks, blockers, and missing inputs.
3. Design test scenarios before creating cases.
4. Cover normal flows, negative flows, and boundary or critical state flows.
5. Mark each scenario with design methods such as equivalence partitioning,
   boundary value analysis, state transition, permission matrix, error guessing,
   or business rule validation.
6. Generate automation cases only when the current environment can run them
   stably with UI-visible assertions.
7. Put high-risk, destructive, payment, account switching, data seeding,
   external dependency, and non-UI-verifiable checks into manual_cases.
8. Mark smoke cases with `smoke=true` and tag/flag `冒烟`.
9. Use P0/P1/P2/P3 priority carefully:
   - P0: release-blocking core baseline flows.
   - P1: core feature paths and important business risks.
   - P2: common branches, empty states, and stable negative prompts.
   - P3: low-frequency boundaries or experience checks.
10. Self-review coverage, automation suitability, assertion quality,
    duplication, data dependency, and remaining risk before saving outputs.
11. Scale case volume by requirement complexity. Tiny requirements may produce
    6-12 automation cases, but medium requirements with 3-5 requirement points
    should usually produce 12-28 automation cases. Complex requirements should
    usually produce 18-45 automation cases. Manual cases do not count toward the
    automation case target.
12. Do not inflate count by duplicating the same path. Additional cases must
    cover different requirement points, data states, empty states, boundary
    values, negative prompts, permission or state transitions, loading failures,
    return interruption, or repeated operation risks.

## Output Assets

The generation result should preserve these artifacts:

- FreeMind `.mm`: for manual review, grouped as feature -> scenario -> case ->
  steps -> expected results.
- Midscene YAML: for direct upload or execution by the Task platform.
- JSON summary: for structured review, UI rendering, and secondary processing.
- Markdown summary: for readable audit and handoff.

## FreeMind Structure

Root: `<topic>-测试用例`

Hierarchy:

- Feature point
- Scenario name with design method label
- Case name with priority and `flag=冒烟` when applicable
- Test steps as numbered lines
- Expected results as numbered UI-visible checks

## Midscene YAML Guidance

- Prefer natural-language Midscene actions over brittle selectors or coordinates.
- Use `ai`, `aiTap`, `aiInput`, `aiAssert`, `aiWaitFor`, `aiScroll`, and
  Android back shell commands when appropriate.
- Avoid fixed long sleeps. Prefer waiting for visible UI conditions.
- Every task should have clear baseline metadata: case id, priority, smoke,
  goal, start page, business path, expected result, and repair hints.
- Assertions must verify visible business signals such as page title, tab state,
  entry card, button text, result region, list or empty state, dialog title,
  status label, or operation prompt.

## Sensitive Data Rule

Never write real tokens, API keys, passwords, phone numbers, ID numbers, or
private accounts into generated `.mm`, YAML, JSON, Markdown, logs, or reports.
Use placeholders and data requirement notes instead.
