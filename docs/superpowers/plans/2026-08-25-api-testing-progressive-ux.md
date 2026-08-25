# API Testing Progressive UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API case authoring understandable to new users by adding searchable workflow-step selection, progressive structured editing, visible variable provenance, actionable validation, and step-oriented debug results without changing stored case or execution semantics.

**Architecture:** Keep `WorkbenchView` as the orchestration boundary and keep standalone case/task management pages unchanged. Add focused Vue components for endpoint picking and repeated case-field editors, derive workflow variables entirely in the UI, and expose the executor's existing sanitized trace through the current debug result mapping. All saved payloads continue to use `CaseDraft` and `InlineWorkflowStep` unchanged.

**Tech Stack:** Vue 3 Composition API, TypeScript, Pinia, Vitest, Vue Test Utils, lucide-vue-next, existing Vite/Playwright test harness.

## Global Constraints

- Do not add a database migration or change the `CaseDraft` / `InlineWorkflowStep` request payload shape.
- Preserve setup -> main -> always-run cleanup execution order, polling limits, response assertion rules, secret redaction, and baseline gates.
- Preserve raw JSON authoring and round-trip all valid legacy case values, including unknown required variables and disabled steps.
- Keep `用例管理` and `任务管理` as standalone pages; the workbench remains an authoring/debugging surface.
- Do not add a beginner/expert mode switch; use one progressively disclosed interface.
- All behavior changes use test-first red-green cycles.

---

### Task 1: Searchable Endpoint Picker

**Files:**
- Create: `api-testing-ui/src/components/EndpointPicker.vue`
- Create: `api-testing-ui/src/components/EndpointPicker.spec.ts`
- Modify: `api-testing-ui/src/components/InlineWorkflowStepEditor.vue`
- Modify: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Consumes: `ApiEndpoint[]`, `groupEndpoints()`, `endpointGroupName()`.
- Produces: `EndpointPicker` with props `{ open: boolean; endpoints: ApiEndpoint[]; title: string }` and emits `select(endpoint)`, `manual`, `close`.
- Behavioral contract: opening the picker does not mutate `modelValue`; a workflow step is created only after `select` or `manual`.

- [ ] **Step 1: Write failing endpoint picker tests**

```ts
it('searches endpoint name path method and group with highlighted matches', async () => {
  const wrapper = mount(EndpointPicker, {
    props: { open: true, title: '添加前置步骤', endpoints: WORKFLOW_ENDPOINTS },
  })
  await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('cancel')
  expect(wrapper.text()).toContain('/printJob/cancel')
  expect(wrapper.findAll('mark').map(node => node.text().toLowerCase())).toContain('cancel')
  expect(wrapper.text()).not.toContain('/resource/page')
})

it('emits the selected endpoint and restores focus on close', async () => {
  const wrapper = mount(EndpointPicker, {
    attachTo: document.body,
    props: { open: true, title: '添加清理步骤', endpoints: WORKFLOW_ENDPOINTS },
  })
  await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('取消打印')
  await wrapper.get('[data-testid="endpoint-picker-option-print-cancel"]').trigger('click')
  expect(wrapper.emitted('select')?.[0]?.[0]).toMatchObject({ id: 'print-cancel' })
})
```

- [ ] **Step 2: Run the picker tests and verify RED**

Run: `npm --prefix api-testing-ui test -- --run src/components/EndpointPicker.spec.ts`

Expected: FAIL because `EndpointPicker.vue` does not exist.

- [ ] **Step 3: Implement the picker**

Implement grouped filtering, highlight segments, default collapsed groups, automatic expansion while searching, sticky search, result scrolling, keyboard Escape, focus entry/return, and explicit manual request selection. The component shell is:

```vue
<EndpointPicker
  :open="pickerOpen"
  :endpoints="endpointOptions || []"
  :title="`添加${stageLabel}`"
  @select="addEndpointStep"
  @manual="addManualStep"
  @close="pickerOpen = false"
/>
```

Rows use `data-testid="endpoint-picker-option-${endpoint.id}"`; search uses `endpoint-picker-search`; groups use buttons with `aria-expanded`.

- [ ] **Step 4: Write and run the no-placeholder-step regression test**

```ts
it('opens endpoint selection without publishing a blank workflow step', async () => {
  const wrapper = mount(CaseEditor, {
    props: { modelValue: DRAFT, endpointOptions: WORKFLOW_ENDPOINTS },
  })
  await wrapper.get('[data-testid="add-setup-step"]').trigger('click')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  expect(wrapper.get('[data-testid="endpoint-picker-search"]').exists()).toBe(true)
  await wrapper.get('[data-testid="endpoint-picker-option-resource-page"]').trigger('click')
  const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
  expect(emitted.processing.setup_steps).toHaveLength(1)
  expect(emitted.processing.setup_steps?.[0].request.path).toBe('/resource/page')
})
```

Run: `npm --prefix api-testing-ui test -- --run src/components/EndpointPicker.spec.ts src/components/CaseEditor.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add api-testing-ui/src/components/EndpointPicker.vue api-testing-ui/src/components/EndpointPicker.spec.ts api-testing-ui/src/components/InlineWorkflowStepEditor.vue api-testing-ui/src/components/CaseEditor.spec.ts api-testing-ui/src/styles/app.css
git commit -m "Add searchable workflow endpoint picker"
```

---

### Task 2: Accordion Workflow Steps and Actionable Summaries

**Files:**
- Create: `api-testing-ui/src/components/WorkflowStepCard.vue`
- Create: `api-testing-ui/src/components/WorkflowStepCard.spec.ts`
- Modify: `api-testing-ui/src/components/InlineWorkflowStepEditor.vue`
- Modify: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Consumes: one `InlineWorkflowStep`, index, stage, validation messages, active state.
- Produces: emits `toggle`, `enabled`, `move(offset)`, `duplicate`, `remove` and renders its body slot only when active.
- Summary counts use request map key counts plus non-null body, enabled assertion count, extraction count, polling and issue count.

- [ ] **Step 1: Write failing accordion and summary tests**

```ts
it('keeps only the active workflow step expanded', async () => {
  const wrapper = mount(CaseEditor, { props: { modelValue: workflowDraft() } })
  expect(wrapper.findAll('[data-testid^="setup-step-body-"]')).toHaveLength(1)
  await wrapper.get('[data-testid="setup-step-toggle-1"]').trigger('click')
  expect(wrapper.find('[data-testid="setup-step-body-0"]').exists()).toBe(false)
  expect(wrapper.get('[data-testid="setup-step-body-1"]').exists()).toBe(true)
})

it('shows configuration counts and errors while collapsed', () => {
  const wrapper = mount(CaseEditor, {
    props: {
      modelValue: workflowDraft(),
      validationErrors: { 'processing.setup_steps[1].request.path': '请求路径不能为空' },
    },
  })
  const summary = wrapper.get('[data-testid="setup-step-summary-1"]')
  expect(summary.text()).toContain('断言')
  expect(summary.text()).toContain('错误 1')
})
```

- [ ] **Step 2: Run tests and verify RED**

Run: `npm --prefix api-testing-ui test -- --run src/components/WorkflowStepCard.spec.ts src/components/CaseEditor.spec.ts`

Expected: FAIL because workflow steps still use uncontrolled native `details`.

- [ ] **Step 3: Implement controlled accordion state**

Use a stable active key derived from the step object identity fields and update it on add, duplicate, remove, and move. Do not use `:open="index === 0"`. The stage owns:

```ts
const activeIndex = ref<number | null>(props.modelValue.length ? 0 : null)

function activate(index: number): void {
  activeIndex.value = activeIndex.value === index ? null : index
}
```

After backend validation changes, compute the first errored step from `processing.setup_steps[N]` or `processing.cleanup_steps[N]`, then set it active.

- [ ] **Step 4: Implement copy and confirmed delete**

Copy deep-clones the step, assigns `${step.name} 副本`, inserts it after the source, and activates it. Delete asks `确认删除步骤“${step.name}”？`; cancel does not emit an update.

- [ ] **Step 5: Run component regression tests**

Run: `npm --prefix api-testing-ui test -- --run src/components/WorkflowStepCard.spec.ts src/components/CaseEditor.spec.ts`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add api-testing-ui/src/components/WorkflowStepCard.vue api-testing-ui/src/components/WorkflowStepCard.spec.ts api-testing-ui/src/components/InlineWorkflowStepEditor.vue api-testing-ui/src/components/CaseEditor.spec.ts api-testing-ui/src/styles/app.css
git commit -m "Make API workflow steps progressively expandable"
```

---

### Task 3: Shared Structured Request, Assertion, and Extraction Editors

**Files:**
- Create: `api-testing-ui/src/components/RequestConfigEditor.vue`
- Create: `api-testing-ui/src/components/RequestConfigEditor.spec.ts`
- Create: `api-testing-ui/src/components/AssertionListEditor.vue`
- Create: `api-testing-ui/src/components/AssertionListEditor.spec.ts`
- Create: `api-testing-ui/src/components/ExtractionListEditor.vue`
- Create: `api-testing-ui/src/components/ExtractionListEditor.spec.ts`
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/components/InlineWorkflowStepEditor.vue`
- Modify: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- `RequestConfigEditor`: props `{ modelValue: CaseRequest; errors?: Record<string,string>; prefix?: string; compact?: boolean }`, emits `update:modelValue`.
- `AssertionListEditor`: props `{ modelValue: Array<Record<string,unknown>>; errors?: Record<string,string>; warnings?: Record<string,string>; prefix?: string }`, emits `update:modelValue`.
- `ExtractionListEditor`: same array contract and prefix mapping, emits `update:modelValue`.
- Existing raw JSON mode remains in `CaseEditor`; workflow-step raw JSON moves under an advanced `details` block and updates the same arrays.

- [ ] **Step 1: Write failing shared editor tests**

```ts
it('edits request maps without publishing unfinished placeholder rows', async () => {
  const wrapper = mount(RequestConfigEditor, { props: { modelValue: REQUEST } })
  await wrapper.get('[data-testid="query-add"]').trigger('click')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  await wrapper.get('[data-testid="query-name"]').setValue('pageSize')
  await wrapper.get('[data-testid="query-value"]').setValue('20')
  expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({ query: { pageSize: '20' } })
})

it('keeps status code expectations numeric and business assertions structured', async () => {
  const wrapper = mount(AssertionListEditor, { props: { modelValue: ASSERTIONS } })
  await wrapper.get('[data-testid="assertion-expected-0"]').setValue('201')
  expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]?.[0].expected).toBe(201)
})

it('adds a required JSONPath extraction', async () => {
  const wrapper = mount(ExtractionListEditor, { props: { modelValue: [] } })
  await wrapper.get('[data-testid="add-extraction"]').trigger('click')
  expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]?.[0]).toMatchObject({
    type: 'json_path', path: '$.data', required: true,
  })
})
```

- [ ] **Step 2: Run shared editor tests and verify RED**

Run: `npm --prefix api-testing-ui test -- --run src/components/RequestConfigEditor.spec.ts src/components/AssertionListEditor.spec.ts src/components/ExtractionListEditor.spec.ts`

Expected: FAIL because the shared components do not exist.

- [ ] **Step 3: Implement shared editors and preserve field-path feedback**

All components deep-clone before emitting. Field feedback uses `${prefix}.query.${name}`, `${prefix}.assertions[${index}]`, and `${prefix}.extractions[${index}]`, omitting the leading dot when `prefix` is empty. Request string values remain strings, while assertion expectations continue using existing scalar/numeric parsing.

- [ ] **Step 4: Replace duplicated main and workflow JSON-first editors**

Main request uses:

```vue
<RequestConfigEditor
  v-model="local.request"
  :errors="displayValidationErrors"
  @update:model-value="publish"
/>
<AssertionListEditor
  v-model="local.assertions"
  :errors="displayValidationErrors"
  :warnings="validationWarnings"
  @update:model-value="publish"
/>
<ExtractionListEditor
  v-model="local.extractions"
  :errors="validationErrors"
  :warnings="validationWarnings"
  @update:model-value="publish"
/>
```

Workflow steps use the same editors with prefix `processing.${stageField}[${index}]`. The old request/assertion/extraction JSON textareas remain under `高级配置 / 原始 JSON` and must still show JSON parse errors without overwriting typed invalid text.

- [ ] **Step 5: Run editor and existing contract tests**

Run: `npm --prefix api-testing-ui test -- --run src/components/RequestConfigEditor.spec.ts src/components/AssertionListEditor.spec.ts src/components/ExtractionListEditor.spec.ts src/components/CaseEditor.spec.ts`

Expected: PASS, including existing unfinished row, numeric assertion, request body, dependency, polling, and raw JSON round-trip tests.

- [ ] **Step 6: Commit Task 3**

```bash
git add api-testing-ui/src/components/RequestConfigEditor.vue api-testing-ui/src/components/RequestConfigEditor.spec.ts api-testing-ui/src/components/AssertionListEditor.vue api-testing-ui/src/components/AssertionListEditor.spec.ts api-testing-ui/src/components/ExtractionListEditor.vue api-testing-ui/src/components/ExtractionListEditor.spec.ts api-testing-ui/src/components/CaseEditor.vue api-testing-ui/src/components/InlineWorkflowStepEditor.vue api-testing-ui/src/components/CaseEditor.spec.ts api-testing-ui/src/styles/app.css
git commit -m "Add structured API workflow field editors"
```

---

### Task 4: Variable Provenance and Searchable Variable Picker

**Files:**
- Create: `api-testing-ui/src/utils/workflowVariables.ts`
- Create: `api-testing-ui/src/utils/workflowVariables.spec.ts`
- Create: `api-testing-ui/src/components/VariablePicker.vue`
- Create: `api-testing-ui/src/components/VariablePicker.spec.ts`
- Create: `api-testing-ui/src/components/DependencyPicker.vue`
- Create: `api-testing-ui/src/components/DependencyPicker.spec.ts`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/context.ts`
- Modify: `api-testing-ui/src/stores/context.spec.ts`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.spec.ts`
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/components/InlineWorkflowStepEditor.vue`
- Modify: `api-testing-ui/src/components/RequestConfigEditor.vue`
- Modify: `api-testing-ui/src/components/RequestConfigEditor.spec.ts`
- Modify: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Add `WorkflowVariableOption { name: string; source: string; sourceKind: 'environment' | 'dependency' | 'setup' | 'main' | 'unknown'; available: boolean }`.
- Add pure function `workflowVariableOptions(draft, stage, index, environmentNames, dependencyOptions): WorkflowVariableOption[]`.
- `VariablePicker` props `{ modelValue: string[]; options: WorkflowVariableOption[] }`, emits `update:modelValue`.
- `DependencyPicker` props `{ modelValue: string; options: CaseDependencyOption[]; disabledIds: string[] }`, emits `update:modelValue`; it searches name, method, path, group and exported variable names.
- `RequestConfigEditor` adds `variableOptions?: WorkflowVariableOption[]`; parameter and body controls insert existing `{{variableName}}` templates without changing request semantics.
- Context store exposes only selected environment variable names; it never persists or renders values.

- [ ] **Step 1: Write failing variable ordering tests**

```ts
it('exposes only variables produced before a setup step', () => {
  const options = workflowVariableOptions(DRAFT, 'setup', 1, ['Biz'], DEPENDENCIES)
  expect(options.map(item => item.name)).toEqual(expect.arrayContaining(['Biz', 'loginToken', 'modelSn']))
  expect(options.find(item => item.name === 'printTaskSn')).toBeUndefined()
})

it('exposes main extractions to cleanup and preserves unknown legacy names', () => {
  const options = workflowVariableOptions(DRAFT, 'cleanup', 0, [], [])
  expect(options.find(item => item.name === 'printTaskSn')?.sourceKind).toBe('main')
  expect(withLegacyVariables(options, ['legacyId'])).toContainEqual(expect.objectContaining({
    name: 'legacyId', sourceKind: 'unknown', available: false,
  }))
})
```

- [ ] **Step 2: Run utility tests and verify RED**

Run: `npm --prefix api-testing-ui test -- --run src/utils/workflowVariables.spec.ts`

Expected: FAIL because the variable utility does not exist.

- [ ] **Step 3: Implement variable derivation and environment-name loading**

Use extraction `target` fields only. Ignore disabled producer steps. A variable becomes unavailable when its producer is disabled or follows the consumer. Load `/api/api-testing/v1/environment-revisions/${revisionId}` into context state as `Object.keys(snapshot.variables)`, and clear names when the environment changes or is removed. Do not store values.

- [ ] **Step 4: Write failing variable picker tests**

```ts
it('filters variables by name and source and marks missing legacy values', async () => {
  const wrapper = mount(VariablePicker, {
    props: { modelValue: ['legacyId'], options: OPTIONS_WITH_LEGACY },
  })
  expect(wrapper.text()).toContain('未找到来源')
  await wrapper.get('[data-testid="variable-search"]').setValue('主体')
  expect(wrapper.text()).toContain('printTaskSn')
  expect(wrapper.text()).not.toContain('modelSn')
})
```

- [ ] **Step 5: Implement the picker and replace comma-separated required variables**

Render selected values as removable tags. The option list searches both variable name and source. Keep unavailable legacy values selected until explicitly removed. Invalid source ordering appears as an inline error and in the stage summary.

- [ ] **Step 6: Write and run dependency search and variable insertion tests**

```ts
it('searches shared dependencies without requiring a version id', async () => {
  const wrapper = mount(DependencyPicker, {
    props: { modelValue: '', options: DEPENDENCIES, disabledIds: [] },
  })
  await wrapper.get('[data-testid="dependency-search"]').setValue('添加收藏')
  await wrapper.get('[data-testid="dependency-option-setup-version-1"]').trigger('click')
  expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['setup-version-1'])
})

it('inserts a selected workflow variable into a request value', async () => {
  const wrapper = mount(RequestConfigEditor, {
    props: { modelValue: REQUEST, variableOptions: VARIABLE_OPTIONS },
  })
  await wrapper.get('[data-testid="query-variable-0"]').trigger('click')
  await wrapper.get('[data-testid="variable-insert-modelSn"]').trigger('click')
  expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({
    query: { modelSn: '{{modelSn}}' },
  })
})
```

Run: `npm --prefix api-testing-ui test -- --run src/components/DependencyPicker.spec.ts src/components/RequestConfigEditor.spec.ts`

Expected: PASS after replacing the native dependency select and adding variable insertion to request fields and the body editor.

- [ ] **Step 7: Run variable, context, editor, and workbench tests**

Run: `npm --prefix api-testing-ui test -- --run src/utils/workflowVariables.spec.ts src/components/VariablePicker.spec.ts src/components/DependencyPicker.spec.ts src/components/RequestConfigEditor.spec.ts src/stores/context.spec.ts src/components/CaseEditor.spec.ts src/views/WorkbenchView.spec.ts`

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

```bash
git add api-testing-ui/src/utils/workflowVariables.ts api-testing-ui/src/utils/workflowVariables.spec.ts api-testing-ui/src/components/VariablePicker.vue api-testing-ui/src/components/VariablePicker.spec.ts api-testing-ui/src/components/DependencyPicker.vue api-testing-ui/src/components/DependencyPicker.spec.ts api-testing-ui/src/components/RequestConfigEditor.vue api-testing-ui/src/components/RequestConfigEditor.spec.ts api-testing-ui/src/api/contracts.ts api-testing-ui/src/stores/context.ts api-testing-ui/src/stores/context.spec.ts api-testing-ui/src/views/WorkbenchView.vue api-testing-ui/src/views/WorkbenchView.spec.ts api-testing-ui/src/components/CaseEditor.vue api-testing-ui/src/components/InlineWorkflowStepEditor.vue api-testing-ui/src/components/CaseEditor.spec.ts api-testing-ui/src/styles/app.css
git commit -m "Show API workflow variable provenance"
```

---

### Task 5: Case Validation Summary and Unified Save/Debug Actions

**Files:**
- Create: `api-testing-ui/src/components/CaseValidationSummary.vue`
- Create: `api-testing-ui/src/components/CaseValidationSummary.spec.ts`
- Modify: `api-testing-ui/src/components/CaseEditor.vue`
- Modify: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- `CaseEditor` adds prop `debugging?: boolean` and emit `debug`.
- `CaseValidationSummary` consumes errors/warnings plus setup/assertion/cleanup counts and emits `navigate(fieldPath)`.
- Workbench removes the detached debug button and handles the editor's `debug` event with existing `submitDebug()`.
- Empty test-data, main-extraction, and shared-dependency sections use native `details`; they open automatically when populated or when their field prefix contains an error/warning.

- [ ] **Step 1: Write failing validation summary tests**

```ts
it('summarizes workflow counts and navigates to an errored field', async () => {
  const wrapper = mount(CaseValidationSummary, {
    props: {
      setupCount: 2, assertionCount: 3, cleanupCount: 1,
      errors: { 'processing.cleanup_steps[0].request.path': '请求路径不能为空' },
      warnings: {},
    },
  })
  expect(wrapper.text()).toContain('前置 2')
  expect(wrapper.text()).toContain('错误 1')
  await wrapper.get('[data-testid="validation-issue-0"]').trigger('click')
  expect(wrapper.emitted('navigate')?.[0]).toEqual(['processing.cleanup_steps[0].request.path'])
})
```

- [ ] **Step 2: Run summary tests and verify RED**

Run: `npm --prefix api-testing-ui test -- --run src/components/CaseValidationSummary.spec.ts`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement summary and field navigation**

Map paths to labels `前置 N` / `主体请求` / `断言 N` / `提取 N` / `清理 N`. Navigation emits the path; `CaseEditor` expands the owning workflow step or advanced section, then on `nextTick()` scrolls `[data-error-for="${CSS.escape(path)}"]` into view and focuses its nearest input/select/textarea when one exists.

- [ ] **Step 4: Write failing unified action tests**

```ts
it('offers save and save-and-debug in one sticky action bar', async () => {
  const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })
  await wrapper.get('[data-testid="save-case-draft"]').trigger('click')
  await wrapper.get('[data-testid="save-and-debug"]').trigger('click')
  expect(wrapper.emitted('save')).toHaveLength(1)
  expect(wrapper.emitted('debug')).toHaveLength(1)
})

it('keeps optional sections collapsed until populated or invalid', () => {
  const empty = mount(CaseEditor, { props: { modelValue: EMPTY_OPTIONAL_DRAFT } })
  expect(empty.get('[data-testid="data-rows-section"]').attributes('open')).toBeUndefined()
  const invalid = mount(CaseEditor, {
    props: {
      modelValue: EMPTY_OPTIONAL_DRAFT,
      validationErrors: { 'extractions[0].path': 'JSONPath 格式不正确' },
    },
  })
  expect(invalid.get('[data-testid="extractions-section"]').attributes('open')).toBeDefined()
})
```

- [ ] **Step 5: Implement progressive optional sections, sticky actions, and Workbench wiring**

Wrap test data, main extraction, and shared dependency/variable handling in compact `details` sections. Default them closed only when empty and issue-free; preserve browser state after the user opens or closes them. The editor footer displays save state, validation count, `保存草稿`, and primary `保存并调试`. Disable debug when blocking errors, saving, or debugging. Preserve `submitDebug()` and its current save-for-debug/task-scope behavior.

- [ ] **Step 6: Run editor and workbench tests**

Run: `npm --prefix api-testing-ui test -- --run src/components/CaseValidationSummary.spec.ts src/components/CaseEditor.spec.ts src/views/WorkbenchView.spec.ts`

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add api-testing-ui/src/components/CaseValidationSummary.vue api-testing-ui/src/components/CaseValidationSummary.spec.ts api-testing-ui/src/components/CaseEditor.vue api-testing-ui/src/components/CaseEditor.spec.ts api-testing-ui/src/views/WorkbenchView.vue api-testing-ui/src/views/WorkbenchView.spec.ts api-testing-ui/src/styles/app.css
git commit -m "Unify API case validation and debug actions"
```

---

### Task 6: Structured Debug Workflow Trace

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/stores/cases.spec.ts`
- Create: `api-testing-ui/src/components/DebugTrace.vue`
- Create: `api-testing-ui/src/components/DebugTrace.spec.ts`
- Modify: `api-testing-ui/src/components/DebugDrawer.vue`
- Modify: `api-testing-ui/src/components/DebugDrawer.spec.ts`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.spec.ts`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Add `DebugTraceStep` with stage, index, name, status, failure category, assertions, extracted variable names, request, response, error, attempt metadata.
- Extend `DebugResult` with `durationMs: number`, `trace: DebugTraceStep[]`, `errorMessage: string`.
- `DebugDrawer` emits `edit-step({ stage: 'setup' | 'main' | 'cleanup'; index: number })`.

- [ ] **Step 1: Write failing debug mapping test**

```ts
it('preserves sanitized workflow trace for structured debugging', () => {
  const result = toDebugResult(EXECUTION_CASE_WITH_TRACE)
  expect(result.durationMs).toBe(321)
  expect(result.trace).toEqual([
    expect.objectContaining({ stage: 'setup', index: 0, name: '查询模型', status: 'PASSED' }),
    expect.objectContaining({ stage: 'cleanup', index: 0, name: '取消打印', status: 'FAILED' }),
  ])
})
```

- [ ] **Step 2: Run store test and verify RED**

Run: `npm --prefix api-testing-ui test -- --run src/stores/cases.spec.ts`

Expected: FAIL because `DebugResult` currently discards structured trace.

- [ ] **Step 3: Implement trace mapping**

Map only sanitized `result.trace` entries with `phase === 'workflow_step'`. Keep raw values as already redacted by the backend. Preserve current text logs for compatibility, but derive them from the structured trace.

- [ ] **Step 4: Write failing DebugTrace and drawer tests**

```ts
it('shows setup main and cleanup status before raw evidence', async () => {
  const wrapper = mount(DebugDrawer, { props: { ...BASE_PROPS, result: DEBUG_RESULT } })
  const trace = wrapper.get('[data-testid="debug-trace"]')
  expect(trace.text()).toContain('前置步骤')
  expect(trace.text()).toContain('主体请求')
  expect(trace.text()).toContain('清理步骤')
  expect(trace.text()).toContain('取消打印')
  await wrapper.get('[data-testid="edit-debug-step-cleanup-0"]').trigger('click')
  expect(wrapper.emitted('edit-step')?.[0]?.[0]).toEqual({ stage: 'cleanup', index: 0 })
})
```

- [ ] **Step 5: Implement result summary, trace details, and return-to-edit**

The drawer shows status, duration, failure category, first failed assertion/error, then `DebugTrace`. Request, response, assertions, extracted variable names, and logs remain in collapsed evidence details. Workbench closes the drawer and tells `CaseEditor` to expand/scroll to the requested stage through an exposed `focusStage(stage, index)` method.

- [ ] **Step 6: Run debug/store/workbench tests**

Run: `npm --prefix api-testing-ui test -- --run src/stores/cases.spec.ts src/components/DebugTrace.spec.ts src/components/DebugDrawer.spec.ts src/views/WorkbenchView.spec.ts`

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add api-testing-ui/src/api/contracts.ts api-testing-ui/src/stores/cases.ts api-testing-ui/src/stores/cases.spec.ts api-testing-ui/src/components/DebugTrace.vue api-testing-ui/src/components/DebugTrace.spec.ts api-testing-ui/src/components/DebugDrawer.vue api-testing-ui/src/components/DebugDrawer.spec.ts api-testing-ui/src/views/WorkbenchView.vue api-testing-ui/src/views/WorkbenchView.spec.ts api-testing-ui/src/styles/app.css
git commit -m "Present API debug results by workflow step"
```

---

### Task 7: Compatibility, Responsive Visual Verification, and State Handoff

**Files:**
- Modify: `api-testing-ui/src/views/CasesView.vue` only if focus-state wiring differs from Workbench.
- Modify: `api-testing-ui/src/views/CasesView.spec.ts` only with the corresponding behavior test.
- Modify: `api-testing-ui/src/styles/app.css`
- Modify: `tests/api_testing_ui_visual_check.js`
- Modify: `tests/api_testing_e2e.spec.mjs`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- No new persisted interface.
- Final UI must work in both Workbench and standalone CasesView because both mount `CaseEditor`.

- [ ] **Step 1: Add failing integration assertions for the final workflow**

Extend Playwright to verify:

```js
await page.getByTestId('add-setup-step').click()
await page.getByTestId('endpoint-picker-search').fill('favorite')
await page.getByTestId(/endpoint-picker-option-/).first().click()
await expect(page.getByTestId('setup-step-body-0')).toBeVisible()
await expect(page.getByTestId('save-and-debug')).toBeVisible()
```

Add a visual check that the picker search remains visible while its result panel is scrolled and that the editor footer does not cover the last field at desktop or mobile widths.

- [ ] **Step 2: Run focused integration checks and verify RED where assertions are new**

Run:

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js
```

Expected before final fixes: the new visual/E2E assertions identify any remaining focus, overflow, or selector gaps.

- [ ] **Step 3: Apply only compatibility and layout fixes exposed by checks**

Do not add new product behavior here. Fix responsive grids, focus restoration, scroll padding, standalone CasesView event wiring, or selectors required for the already specified workflow.

- [ ] **Step 4: Run mandatory repository checks**

```bash
python3 tests/frontend_static_checks.py
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js
TEST_DATABASE_URL="${TEST_DATABASE_URL}" TEST_REDIS_URL="${TEST_REDIS_URL}" npx playwright test tests/api_testing_e2e.spec.mjs --project=chromium
git diff --check
```

Expected: all commands exit 0; Vitest reports zero failures; build succeeds; desktop/mobile visual check passes; Playwright closed loop passes.

- [ ] **Step 5: Review the full diff against the design**

Check every acceptance item in `docs/superpowers/specs/2026-08-25-api-testing-progressive-ux-design.md`, with special attention to legacy JSON round-trip, unknown variables, disabled steps, setup/main/cleanup visibility, standalone management boundaries, and cleanup failure presentation.

- [ ] **Step 6: Update state and commit final integration**

```bash
git add CODEX_STATE.md api-testing-ui/src tests/api_testing_ui_visual_check.js tests/api_testing_e2e.spec.mjs
git commit -m "Polish progressive API testing workflow"
```

- [ ] **Step 7: Request code review and resolve all Critical/Important findings**

Provide the reviewer the design path, this plan path, base SHA `934f21e`, final SHA, and test evidence. Re-run the affected focused tests after every accepted fix, then re-run the full commands from Step 4.
