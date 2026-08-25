# API Testing Management UX Implementation Plan

> **Execution note:** Implement this plan in order with test-first changes. Keep every step compatible with the current API contracts and database schema.

**Goal:** Make the standalone API test management pages usable at the current production scale by adding navigation zones, hierarchical case grouping and batch movement, practical baseline/run/report filters, consistent execution conclusions, and deduplicated environment service presentation.

**Architecture:** Keep all new view state in Vue components and pure presentation utilities. Existing stores and HTTP endpoints remain the source of truth. Case group batch updates reuse the current single-version group endpoint sequentially so partial success can be reported without adding a backend contract. Execution/report classification reuses `executionPresentation.ts` rather than duplicating status semantics.

**Tech stack:** Vue 3 Composition API, Pinia, TypeScript, Vitest, Vue Test Utils, Vite, lucide-vue-next, Playwright.

**Non-goals:** No migrations, no new API fields, no changes to setup/main/always-run-cleanup execution semantics, no changes to baseline admission, and no persistence of filters, selection, or expanded tree state.

---

## Task 1: Group the global navigation by workflow stage

**Files:**
- Modify: `api-testing-ui/src/App.spec.ts`
- Modify: `api-testing-ui/src/App.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write the failing test**

Extend `App.spec.ts` to assert that:

- four navigation section labels are rendered in order: `设计准备`, `回归编排`, `结果分析`, `项目配置`;
- `工作台/接口资产/用例管理` belong to `设计准备`;
- existing route hrefs and the dedicated task/case page assertions remain unchanged.

**Step 2: Run the focused test and confirm it fails**

```bash
npm --prefix api-testing-ui test -- --run src/App.spec.ts
```

Expected: fail because the flat navigation has no section labels.

**Step 3: Implement the minimum UI change**

Replace the flat `navigation` array with a `navigationSections` structure and render one labelled group per section. Add stable `data-testid` values to section containers. Keep every existing route, icon, title, and `nav-tasks`/`nav-cases` test id.

Add restrained section label and spacing styles. At the existing compact rail breakpoint, hide section labels and preserve icon tooltips.

**Step 4: Re-run the focused test**

```bash
npm --prefix api-testing-ui test -- --run src/App.spec.ts
```

Expected: pass.

---

## Task 2: Add pure case-list presentation utilities

**Files:**
- Create: `api-testing-ui/src/utils/caseListPresentation.spec.ts`
- Create: `api-testing-ui/src/utils/caseListPresentation.ts`

**Step 1: Write failing utility tests**

Cover these contracts:

- split trimmed slash-delimited paths into a recursive tree;
- merge common ancestors without losing items directly assigned to a parent;
- count each node's direct and descendant cases;
- preserve the existing `compareGroupNames` ordering at every level;
- classify work views: `all`, `task`, `orchestrated`, `one-time`, `candidate`;
- orchestration is true when enabled setup or cleanup steps exist;
- one-time matching is case-insensitive and checks name, group, and tags without misclassifying ordinary cases.

Use a shared `CaseListItem` presentation type so the tree and component tests use the same model.

**Step 2: Run the focused test and confirm it fails**

```bash
npm --prefix api-testing-ui test -- --run src/utils/caseListPresentation.spec.ts
```

Expected: fail because the module does not exist.

**Step 3: Implement pure functions**

Export:

```ts
export type CaseWorkView = 'all' | 'task' | 'orchestrated' | 'one-time' | 'candidate'
export interface CaseGroupNode { id: string; label: string; fullPath: string; count: number; items: CaseListItem[]; children: CaseGroupNode[] }
export function buildCaseGroupTree(items: CaseListItem[]): CaseGroupNode[]
export function matchesCaseWorkView(item: CaseListItem, view: CaseWorkView, selectedEndpointIds: Set<string>): boolean
export function caseSearchText(item: CaseListItem): string
```

Keep the functions deterministic and independent of Vue.

**Step 4: Re-run the focused test**

```bash
npm --prefix api-testing-ui test -- --run src/utils/caseListPresentation.spec.ts
```

Expected: pass.

---

## Task 3: Replace the flat case-group list with a lazy recursive tree

**Files:**
- Create: `api-testing-ui/src/components/CaseGroupBranch.spec.ts`
- Create: `api-testing-ui/src/components/CaseGroupBranch.vue`
- Modify: `api-testing-ui/src/components/CaseListPanel.spec.ts`
- Modify: `api-testing-ui/src/components/CaseListPanel.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write failing component tests**

Add tests proving:

- only the first root is expanded by default when several roots exist;
- collapsed descendants are not rendered until their parent is expanded;
- a parent count includes descendants;
- the active saved case path expands automatically;
- searching group/name/path expands all matching paths and emits highlighted `<mark>` text;
- `展开全部` and `收起全部` still control all nodes;
- the existing preview/save/edit/run/delete/scope behavior remains available inside the recursive tree.

**Step 2: Run the focused tests and confirm they fail**

```bash
npm --prefix api-testing-ui test -- --run src/components/CaseGroupBranch.spec.ts src/components/CaseListPanel.spec.ts
```

Expected: fail because the branch component and hierarchical behavior are absent.

**Step 3: Implement recursive rendering**

Create `CaseGroupBranch.vue` with:

- a directory header button using `ChevronRight/ChevronDown`;
- `aria-expanded` and full-path title;
- descendant rendering only while expanded or search-forced;
- an item slot forwarded recursively.

Refactor `CaseListPanel.vue` to build the tree from `caseListPresentation.ts`, own a set of expanded node ids, expand the first root/current active path, and force matched paths open while searching. Add a small local highlight renderer/component using escaped text fragments, not `v-html`.

**Step 4: Re-run the focused tests**

```bash
npm --prefix api-testing-ui test -- --run src/components/CaseGroupBranch.spec.ts src/components/CaseListPanel.spec.ts
```

Expected: pass.

---

## Task 4: Add case work views and searchable single/batch group movement

**Files:**
- Create: `api-testing-ui/src/components/CaseGroupPicker.spec.ts`
- Create: `api-testing-ui/src/components/CaseGroupPicker.vue`
- Modify: `api-testing-ui/src/components/CaseListPanel.spec.ts`
- Modify: `api-testing-ui/src/components/CaseListPanel.vue`
- Modify: `api-testing-ui/src/views/CasesView.spec.ts`
- Modify: `api-testing-ui/src/views/CasesView.vue`
- Modify: `api-testing-ui/src/stores/cases.spec.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write failing tests**

Cover:

- work-view tabs show correct counts and combine with search;
- an empty filtered view explains that the filter is empty rather than claiming no project cases exist;
- saved rows have checkboxes and no native `<select>` containing every group;
- the row movement button opens a searchable picker and can create a typed group name;
- selecting several rows shows a batch toolbar and emits all selected version ids with a target group;
- clearing selection removes the batch toolbar;
- store batch movement sends sequential PUT requests to the existing `/case-versions/{id}/group` endpoint;
- successful updates are applied locally; on partial failure, later calls stop and the thrown error includes succeeded/total and the failed version id.

**Step 2: Run focused tests and confirm they fail**

```bash
npm --prefix api-testing-ui test -- --run src/components/CaseGroupPicker.spec.ts src/components/CaseListPanel.spec.ts src/views/CasesView.spec.ts src/stores/cases.spec.ts
```

Expected: fail because work views, picker, selection, and batch store method are absent.

**Step 3: Implement the picker and panel state**

`CaseGroupPicker.vue` must:

- use a search input fixed at the top of its popover;
- show matching existing groups and result count;
- support keyboard `Escape` and outside close;
- expose an explicit `创建并移动到「...」` command when the typed value is new;
- never mutate data itself; emit the chosen group name.

`CaseListPanel.vue` must:

- render compact segmented work-view controls with counts;
- filter before tree construction;
- add saved-row selection and a batch toolbar;
- use the same picker for single and batch movement;
- preserve all existing row actions and preview behavior.

`CasesView.vue` forwards single and batch movement to the store and reports failures through the existing notification/error path.

`cases.ts` adds `updateVersionGroups(versionIds, groupName)` using the current endpoint sequentially. Do not wrap or recurse through `updateVersionGroup` because that would toggle saving for every item.

**Step 4: Re-run focused tests**

```bash
npm --prefix api-testing-ui test -- --run src/components/CaseGroupPicker.spec.ts src/components/CaseListPanel.spec.ts src/views/CasesView.spec.ts src/stores/cases.spec.ts
```

Expected: pass.

---

## Task 5: Add baseline work filters that preserve one-time cases

**Files:**
- Modify: `api-testing-ui/src/views/BaselinesView.spec.ts`
- Modify: `api-testing-ui/src/views/BaselinesView.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write failing tests**

Add fixtures for regular and `API Test / 一次性` baselines with multiple methods, priorities, and origins. Assert:

- type/method/priority/origin filters combine;
- visible count and empty state follow the combined filters;
- “select all visible” only selects filtered rows;
- a one-time baseline remains runnable/selectable and is visibly labelled, not disabled.

**Step 2: Run and confirm failure**

```bash
npm --prefix api-testing-ui test -- --run src/views/BaselinesView.spec.ts
```

**Step 3: Implement view-local filters**

Add compact filter controls and pure computed filtering. Derive baseline type from explicit `API Test`/`一次性` group/name/tag markers. Reuse existing execution and selection handlers without changing persistence.

**Step 4: Re-run the focused test**

```bash
npm --prefix api-testing-ui test -- --run src/views/BaselinesView.spec.ts
```

Expected: pass.

---

## Task 6: Add execution search/source/conclusion filters and business conclusions

**Files:**
- Modify: `api-testing-ui/src/components/ExecutionConsole.spec.ts`
- Modify: `api-testing-ui/src/components/ExecutionConsole.vue`
- Modify: `api-testing-ui/src/utils/executionPresentation.spec.ts`
- Modify: `api-testing-ui/src/utils/executionPresentation.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write failing tests**

Cover:

- source classification for saved/formal executions and debug executions;
- conclusion filters for pass, problem, and running;
- search by task/environment/type;
- terminal rows display `通过`, `未通过`, `运行异常`, or `执行不完整` from `executionConclusion` instead of raw `DONE`;
- queue/running rows keep lifecycle wording;
- filtering does not emit a selection change or close the currently opened detail.

**Step 2: Run and confirm failure**

```bash
npm --prefix api-testing-ui test -- --run src/utils/executionPresentation.spec.ts src/components/ExecutionConsole.spec.ts
```

**Step 3: Implement shared classification and console filters**

Add a small source classifier to `executionPresentation.ts`, based on existing execution type/snapshot semantics. Reuse it in the console and later report view. Keep unknown historical types in the formal/audit-safe bucket unless they are explicitly debug.

**Step 4: Re-run focused tests**

```bash
npm --prefix api-testing-ui test -- --run src/utils/executionPresentation.spec.ts src/components/ExecutionConsole.spec.ts
```

Expected: pass.

---

## Task 7: Isolate formal reports from online debugging

**Files:**
- Modify: `api-testing-ui/src/views/ReportsView.spec.ts`
- Modify: `api-testing-ui/src/views/ReportsView.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write failing tests**

Assert:

- the source selector offers `正式回归`, `在线调试`, and `全部记录`;
- default source is formal when any formal execution exists;
- default falls back to all when the project only has debug records;
- dashboard metrics use the source-scoped set and do not change when the secondary conclusion filter changes;
- task/environment search only affects the report list;
- switching source preserves access to every historical report.

**Step 2: Run and confirm failure**

```bash
npm --prefix api-testing-ui test -- --run src/views/ReportsView.spec.ts
```

**Step 3: Implement two-level filtering**

Derive `sourceScopedReports`, `dashboard`, and `visibleReports` separately. Use the source classifier from Task 6. Keep the current active report if it remains available; otherwise choose the first visible report without mutating stored data.

**Step 4: Re-run the focused test**

```bash
npm --prefix api-testing-ui test -- --run src/views/ReportsView.spec.ts
```

Expected: pass.

---

## Task 8: Deduplicate environment service presentation without changing editor data

**Files:**
- Create: `api-testing-ui/src/utils/environmentPresentation.spec.ts`
- Create: `api-testing-ui/src/utils/environmentPresentation.ts`
- Modify: `api-testing-ui/src/views/SettingsView.spec.ts`
- Modify: `api-testing-ui/src/views/SettingsView.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Step 1: Write failing tests**

Cover:

- services with the same normalized Base URL become one effective address item with multiple service keys;
- trailing slash differences do not create duplicate effective addresses;
- empty addresses are combined into one unconfigured item;
- effective address count and service-key count are both returned;
- `SettingsView` overview uses the grouped presentation;
- entering edit mode still renders and saves the original service key rows unchanged.

**Step 2: Run and confirm failure**

```bash
npm --prefix api-testing-ui test -- --run src/utils/environmentPresentation.spec.ts src/views/SettingsView.spec.ts
```

**Step 3: Implement read-only grouping**

Create a pure `environmentServicePresentation(services)` function. Use it only in overview computed state and labels. Do not change `applyEnvironment`, the editor rows, payload generation, or backend `service_count` semantics.

**Step 4: Re-run focused tests**

```bash
npm --prefix api-testing-ui test -- --run src/utils/environmentPresentation.spec.ts src/views/SettingsView.spec.ts
```

Expected: pass.

---

## Task 9: Full regression, production build, and browser acceptance

**Files:**
- Modify: `CODEX_STATE.md`
- Inspect: `api-testing-ui/dist/`
- Inspect: local production preview and deployed QA after user deployment

**Step 1: Run the complete frontend unit suite**

```bash
npm --prefix api-testing-ui test -- --run
```

Expected: all tests pass with no unhandled Vue warnings.

**Step 2: Run required static checks and production build**

```bash
python3 tests/frontend_static_checks.py
npm --prefix api-testing-ui run build
git diff --check
```

Expected: all commands exit 0.

**Step 3: Run the API testing gate when local PostgreSQL/Redis are available**

```bash
bash tests/run_api_testing_gate.sh
```

Expected: backend API testing tests, frontend tests, build, visual check, and Chromium E2E pass. If infrastructure is unavailable, record the exact blocker and still run the non-database checks above.

**Step 4: Browser acceptance on desktop and compact viewport**

Verify with Playwright:

1. Navigation shows four zones and every route remains reachable.
2. Cases default to a compact tree; search expands/highlights; work views count correctly.
3. Single and batch group pickers open, search, cancel, and preserve unsaved data. Do not persist browser QA changes unless using disposable fixtures.
4. Baseline filters combine and one-time baselines remain executable.
5. Execution rows display business conclusions and source/conclusion filters work.
6. Reports default to formal scope and can switch to debug/all without metric drift.
7. Environment overview groups duplicate/empty addresses while edit mode retains original service keys.
8. No horizontal overflow or overlapping controls at desktop and compact viewport widths.

Capture screenshots outside tracked source directories and delete temporary browser artifacts before commit.

**Step 5: Update state and prepare integration**

Add a dated entry to `CODEX_STATE.md` listing behavior, boundaries, and exact verification output. Review `git status` to ensure no browser artifacts, secrets, generated dist, or unrelated files are staged.

**Step 6: Commit and push**

```bash
git add api-testing-ui/src CODEX_STATE.md docs/superpowers/plans/2026-08-25-api-testing-management-ux.md
git commit -m "Improve API testing management views"
git push origin main
```

Expected: local `main` and `origin/main` point to the new commit.

---

## Final quality checklist

- Every behavior change has a focused failing test before implementation.
- Existing API request and response contracts are unchanged.
- Filters and tree expansion are local UI state only.
- Batch group updates report partial success and never imply an atomic backend operation.
- One-time cases remain visible and runnable.
- Reports distinguish leadership-facing formal results from online debugging.
- Environment grouping is presentation-only; service keys remain intact for execution routing.
- No sensitive headers, tokens, credentials, Biz values, or MFA data appear in source, tests, screenshots, logs, or commits.
- `git diff --check`, frontend static checks, full unit suite, production build, and browser acceptance all pass before push.
