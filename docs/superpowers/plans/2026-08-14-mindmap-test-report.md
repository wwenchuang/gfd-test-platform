# Mindmap Test Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete mindmap-centered test report generator with concise scope summaries, custom metadata, template support, saved HTML/Markdown outputs, and optional execution-result enrichment.

**Architecture:** Add a focused backend service, `task_server/services/test_report_service.py`, that reads existing `summary.json` case sets, normalizes selectable cases, computes concise report data, renders Markdown/HTML, and persists report artifacts. Add small HTTP routes in `task_server/router.py`, then wire the existing vanilla frontend brain-map center to a two-column report builder.

**Tech Stack:** Python stdlib JSON/XML/HTML utilities, existing task server route decorators/storage helpers, vanilla JavaScript under `js/app.js`, existing CSS under `css/app.css`, and project static check scripts.

## Global Constraints

- Default template follows the user-provided structure: `基本信息 / 测试概要 / 测试数据 / 质量评估 / 发布建议 / 附录`.
- `测试范围` must be concise: group by feature/scenario, at most 3 core scenarios per feature and 8 scenario rows in the main body.
- Do not change existing `/api/reports` Runner/Midscene report semantics.
- Do not let AI decide execution gates or release continuation.
- Internal platform case sets prefer `summary.json`; FreeMind XML parsing is only a fallback for uploaded/external `.mm` content.
- First implementation supports Markdown and HTML outputs; Word/PDF are not included.

---

### Task 1: Backend Report Data And Rendering

**Files:**
- Create: `task_server/services/test_report_service.py`
- Create: `tests/test_mindmap_test_report_service.py`

**Interfaces:**
- Produces: `load_reportable_cases(case_set_id: str) -> dict`
- Produces: `preview_test_report(payload: dict) -> dict`
- Produces: `create_test_report(payload: dict) -> dict`
- Produces: `list_test_reports(case_set_id: str = "", limit: int = 100) -> list[dict]`
- Produces: `read_test_report(report_id: str) -> dict | None`
- Produces: `render_test_report(data: dict, template_id: str = "", output_format: str = "html") -> str`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_mindmap_test_report_service.py` with tests that monkeypatch `CASE_DIR`, `LEARNING_DIR`, and `REPORT_INDEX_FILE`, write a sample `summary.json`, then assert:

```python
def test_load_reportable_cases_defaults_to_p0_p1_and_smoke(tmp_path, monkeypatch):
    result = test_report_service.load_reportable_cases("case-a")
    selected = [case["case_id"] for group in result["groups"] for scene in group["scenarios"] for case in scene["cases"] if case["default_selected"]]
    assert selected == ["TC-001", "TC-002", "TC-004"]
```

```python
def test_preview_uses_concise_scope_and_unexecuted_quality(tmp_path, monkeypatch):
    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "TC-002", "TC-003", "TC-004"],
        "meta": {"report_title": "共享打印V1.2.2-测试报告", "tester": "王文闯"},
    })
    assert result["statistics"]["total"] == 4
    assert result["statistics"]["not_executed"] == 4
    assert result["quality"]["result"] == "未执行"
    assert "测试范围" not in result["scope_markdown"]
    assert result["scope_markdown"].count("\n") <= 10
```

```python
def test_create_report_persists_markdown_html_and_index(tmp_path, monkeypatch):
    result = test_report_service.create_test_report({...})
    assert Path(result["files"]["markdown"]).exists()
    assert Path(result["files"]["html"]).exists()
    assert "基本信息" in Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    assert test_report_service.read_test_report(result["report_id"])["report_id"] == result["report_id"]
```

- [ ] **Step 2: Run tests and verify red**

Run: `.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q`

Expected: fail because `task_server.services.test_report_service` does not exist.

- [ ] **Step 3: Implement minimal service**

Create `task_server/services/test_report_service.py` with:

- summary loading via existing `generation_summary_path`
- case normalization for `cases` and `manual_cases`
- grouping by `feature` then `scenario`
- default selection for `P0`, `P1`, or `smoke`
- concise scope rendering with `max_features=8`, `max_scenarios_per_feature=3`
- default metadata normalization
- default Markdown template matching the approved sections
- basic HTML escaping/wrapping
- artifact persistence under `CASE_DIR/<case_set_id>/test-reports/<report_id>/`
- global index under `LEARNING_DIR/test-report-index.json`

- [ ] **Step 4: Run service tests and verify green**

Run: `.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q`

Expected: all tests pass.

### Task 2: HTTP Routes

**Files:**
- Modify: `task_server/router.py`
- Modify: `docs/ROUTES.md`
- Create: route-focused tests inside `tests/test_mindmap_test_report_service.py` if direct handler testing is practical; otherwise rely on service tests plus static checks.

**Interfaces:**
- Consumes: Task 1 service functions.
- Produces:
  - `GET /api/test-reports/cases?case_set_id=...`
  - `POST /api/test-reports/preview`
  - `POST /api/test-reports`
  - `GET /api/test-reports?case_set_id=...`
  - `GET /api/test-reports/download?report_id=...&format=html|md`
  - `GET /api/test-reports/templates`
  - `POST /api/test-reports/templates`

- [ ] **Step 1: Add route expectations to tests**

Extend backend static expectations by ensuring `docs/ROUTES.md` and `task_server/router.py` contain all new route paths.

- [ ] **Step 2: Implement routes**

Add imports and route handlers that:

- validate `case_set_id` and `report_id`
- return JSON errors with actionable Chinese messages
- use `send_attachment` for downloads
- keep `/api/reports` unchanged

- [ ] **Step 3: Verify routes**

Run:

```bash
python3 -m py_compile task_server/services/test_report_service.py task_server/router.py
.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q
python3 tests/backend_static_checks.py
git diff --check
```

Expected: all pass.

### Task 3: Frontend Report Builder

**Files:**
- Modify: `js/app.js`
- Modify: `css/app.css`
- Modify: `tests/frontend_static_checks.py`

**Interfaces:**
- Consumes: Task 2 routes.
- Produces:
  - `openMindmapReportBuilder(caseSetId)`
  - `loadMindmapReportCases(caseSetId)`
  - `renderMindmapReportBuilder(data)`
  - `previewMindmapTestReport()`
  - `createMindmapTestReport()`

- [ ] **Step 1: Write failing frontend static checks**

Add checks to `tests/frontend_static_checks.py` requiring:

```python
require("生成报告" in html and "openMindmapReportBuilder" in html, "Mindmap center must expose test report generation")
require("/test-reports/cases" in html and "/test-reports/preview" in html and "/test-reports" in html, "Frontend must call mindmap test report APIs")
require("测试范围" in html and "测试人员" in html and "测试周期" in html and "涉及端侧" in html, "Report builder must expose required metadata fields")
require("mindmap-report-layout" in html and "mindmap-report-scope" in html, "Report builder styles must be present")
```

- [ ] **Step 2: Run frontend check and verify red**

Run: `python3 tests/frontend_static_checks.py`

Expected: fail because report builder functions/classes do not exist.

- [ ] **Step 3: Implement frontend builder**

In `js/app.js`:

- add a “生成报告” action to each brain-map record card
- add builder state variables
- render two columns: selectable case tree on the left, metadata/template/preview on the right
- default selected cases based on backend `default_selected`
- support quick actions: all visible, smoke only, include manual, clear
- call preview and create APIs
- show generated HTML/Markdown links

In `css/app.css`:

- add quiet, dense styles for report builder layout
- ensure no nested card-heavy page composition
- keep mobile layout as single column

- [ ] **Step 4: Run frontend checks**

Run:

```bash
python3 tests/frontend_static_checks.py
git diff --check
```

Expected: pass.

### Task 4: Final Verification And State Update

**Files:**
- Modify: `CODEX_STATE.md`

- [ ] **Step 1: Run full required checks**

Run:

```bash
python3 -m py_compile task_server/services/test_report_service.py task_server/router.py
.venv/bin/python -m pytest tests/test_mindmap_test_report_service.py -q
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
```

- [ ] **Step 2: Update project state**

Add a `2026-08-14 脑图用例测试报告：实现完成` entry to `CODEX_STATE.md` with changed files, behavior, and verification commands.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git add task_server/services/test_report_service.py task_server/router.py docs/ROUTES.md js/app.js css/app.css tests/test_mindmap_test_report_service.py tests/frontend_static_checks.py CODEX_STATE.md docs/superpowers/plans/2026-08-14-mindmap-test-report.md
git commit -m "Add mindmap test report generator"
```
