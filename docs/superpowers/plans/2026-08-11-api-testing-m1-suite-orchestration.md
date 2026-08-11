# API Testing M1 Suite Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immutable API test suites with validated DAG dependencies, explicit variable mappings, safe declarative processing, and truthful suite execution.

**Architecture:** Add suite models and migration `0004`, isolate graph validation in a pure module, resolve a suite into an immutable execution snapshot, and extend the existing execution service/worker instead of creating another runner. The Vue workspace gains a compact ordered editor with an optional graph view.

**Tech Stack:** Python 3.10, SQLAlchemy 2, Alembic, PostgreSQL JSONB, Celery, Vue 3, Pinia, TypeScript, Vitest, Playwright.

## Global Constraints

- A suite node references an exact `ApiCaseVersion`, never a mutable case head.
- A suite version is immutable after publication or first execution.
- One suite version contains 1-100 nodes; concurrency defaults to 4 and is limited to 1-8.
- Only `SUCCESS` dependency edges are supported in M1.
- Variables cross nodes only through explicit export/import mappings.
- Arbitrary Python, JavaScript, Shell, and template-expression execution is forbidden.
- Dependency-blocked nodes are `SKIPPED`; platform errors are `BROKEN`; product assertions are `FAILED`.

---

### Task 1: Add suite schema and migration 0004

**Files:**
- Create: `task_server/api_testing/models/suite.py`
- Modify: `task_server/api_testing/models/__init__.py`
- Create: `task_server/api_testing/migrations/versions/0004_suite_orchestration.py`
- Modify: `tests/api_testing/test_migrations.py`

**Interfaces:**
- Produces: `ApiSuite`, `ApiSuiteVersion`, `ApiSuiteNode`, `ApiSuiteEdge`, `ApiSuiteAuditEvent`.

- [ ] **Step 1: Write failing migration metadata tests**

```python
def test_0004_creates_suite_graph_tables(migrated_connection):
    tables = set(inspect(migrated_connection).get_table_names())
    assert {
        "api_suites", "api_suite_versions", "api_suite_nodes",
        "api_suite_edges", "api_suite_audit_events",
    } <= tables


def test_suite_node_references_exact_case_version(migrated_connection):
    foreign_keys = inspect(migrated_connection).get_foreign_keys("api_suite_nodes")
    assert any(item["referred_table"] == "api_case_versions" for item in foreign_keys)
```

- [ ] **Step 2: Verify migration tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_migrations.py -q`

Expected: FAIL because migration `0004` and suite tables do not exist.

- [ ] **Step 3: Add focused SQLAlchemy models**

```python
class ApiSuite(PrimaryRecord, Base):
    __tablename__ = "api_suites"
    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("api_workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DRAFT")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ApiSuiteVersion(PrimaryRecord, Base):
    __tablename__ = "api_suite_versions"
    suite_id: Mapped[str] = mapped_column(ForeignKey("api_suites.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision_id: Mapped[str] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="RESTRICT"))
    environment_revision_id: Mapped[str | None] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"), nullable=True)
    graph_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PENDING")
    created_from: Mapped[str] = mapped_column(String(32), nullable=False)
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add unique constraints for suite/version number, version/node key, and version/upstream/downstream edge.

- [ ] **Step 4: Apply migration to a clean and upgraded database**

Run:

```bash
API_TESTING_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m alembic -c task_server/api_testing/migrations/alembic.ini upgrade head
.venv/bin/python -m pytest tests/api_testing/test_migrations.py -q
```

Expected: Alembic reaches `0004`; migration tests PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/models/suite.py task_server/api_testing/models/__init__.py task_server/api_testing/migrations/versions/0004_suite_orchestration.py tests/api_testing/test_migrations.py
git commit -m "Add immutable API suite schema"
```

### Task 2: Implement pure DAG and variable validation

**Files:**
- Create: `task_server/api_testing/suite_graph.py`
- Create: `tests/api_testing/test_suite_graph.py`

**Interfaces:**
- Produces: `SuiteNodeInput`, `SuiteEdgeInput`, `SuiteGraphResult` dataclasses.
- Produces: `validate_suite_graph(nodes, edges, case_exports, case_inputs) -> SuiteGraphResult`.

- [ ] **Step 1: Write graph failure tests**

```python
def test_rejects_cycle():
    result = validate_suite_graph(
        nodes=[node("a"), node("b")],
        edges=[edge("a", "b"), edge("b", "a")],
        case_exports={},
        case_inputs={},
    )
    assert result.valid is False
    assert result.issues[0].code == "suite_cycle"


def test_rejects_unmapped_required_input():
    result = validate_suite_graph(
        nodes=[node("create"), node("delete")],
        edges=[edge("create", "delete", {"favorite_id": "id"})],
        case_exports={"create": {"favorite_id"}},
        case_inputs={"delete": {"resource_id"}},
    )
    assert any(issue.code == "suite_input_unresolved" for issue in result.issues)
```

- [ ] **Step 2: Verify tests fail due to missing module**

Run: `.venv/bin/python -m pytest tests/api_testing/test_suite_graph.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement Kahn topological validation**

```python
@dataclass(frozen=True)
class SuiteNodeInput:
    key: str
    position: int


@dataclass(frozen=True)
class SuiteEdgeInput:
    upstream: str
    downstream: str
    condition: str
    variable_mapping: Mapping[str, str]


@dataclass(frozen=True)
class SuiteGraphResult:
    valid: bool
    levels: tuple[tuple[str, ...], ...]
    issues: tuple[ValidationIssue, ...]


def validate_suite_graph(nodes, edges, case_exports, case_inputs):
    issues = []
    if not 1 <= len(nodes) <= 100:
        issues.append(ValidationIssue("suite_node_count", "nodes", "测试集必须包含 1 到 100 个节点"))
    node_by_key = {}
    for node in nodes:
        if node.key in node_by_key:
            issues.append(ValidationIssue("suite_node_duplicate", f"nodes.{node.key}", "节点标识重复"))
        node_by_key[node.key] = node

    outgoing = {key: [] for key in node_by_key}
    indegree = {key: 0 for key in node_by_key}
    imported = {key: set() for key in node_by_key}
    seen_edges = set()
    for edge in edges:
        edge_key = (edge.upstream, edge.downstream)
        if edge_key in seen_edges:
            issues.append(ValidationIssue("suite_edge_duplicate", "edges", "依赖边重复"))
            continue
        seen_edges.add(edge_key)
        if edge.upstream == edge.downstream:
            issues.append(ValidationIssue("suite_self_dependency", f"nodes.{edge.upstream}", "节点不能依赖自身"))
            continue
        if edge.upstream not in node_by_key or edge.downstream not in node_by_key:
            issues.append(ValidationIssue("suite_edge_unknown_node", "edges", "依赖边引用了不存在的节点"))
            continue
        if edge.condition != "SUCCESS":
            issues.append(ValidationIssue("suite_condition_unsupported", "edges.condition", "当前只支持 SUCCESS 条件"))
        for source_name, target_name in edge.variable_mapping.items():
            if source_name not in case_exports.get(edge.upstream, set()):
                issues.append(ValidationIssue("suite_export_unknown", f"nodes.{edge.upstream}.exports", f"上游未导出变量 {source_name}"))
            if target_name in imported[edge.downstream]:
                issues.append(ValidationIssue("suite_import_conflict", f"nodes.{edge.downstream}.imports", f"变量 {target_name} 被重复导入"))
            imported[edge.downstream].add(target_name)
        outgoing[edge.upstream].append(edge.downstream)
        indegree[edge.downstream] += 1

    for key, required in case_inputs.items():
        for missing in sorted(set(required) - imported.get(key, set())):
            issues.append(ValidationIssue("suite_input_unresolved", f"nodes.{key}.inputs", f"缺少变量 {missing}"))

    levels = []
    ready = sorted((key for key, count in indegree.items() if count == 0), key=lambda key: (node_by_key[key].position, key))
    visited = 0
    while ready:
        level = tuple(ready)
        levels.append(level)
        next_ready = []
        for key in level:
            visited += 1
            for downstream in outgoing[key]:
                indegree[downstream] -= 1
                if indegree[downstream] == 0:
                    next_ready.append(downstream)
        ready = sorted(next_ready, key=lambda key: (node_by_key[key].position, key))
    if visited != len(node_by_key):
        issues.append(ValidationIssue("suite_cycle", "edges", "测试集依赖存在循环"))
    return SuiteGraphResult(not issues, tuple(levels), tuple(issues))
```

Keep this function network-free and deterministic. The implementation shown rejects invalid node counts, duplicate keys, self/duplicate/unknown edges, unsupported conditions, unknown exports, duplicate imports, unresolved required imports, and cycles. It sorts each level by node position then key for reproducible snapshots.

- [ ] **Step 4: Run graph tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_suite_graph.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/suite_graph.py tests/api_testing/test_suite_graph.py
git commit -m "Validate API suite dependency graphs"
```

### Task 3: Add suite repository, contracts, and service

**Files:**
- Create: `task_server/api_testing/contracts/suite.py`
- Create: `task_server/api_testing/repositories/suite_repository.py`
- Create: `task_server/api_testing/services/suite_service.py`
- Modify: `task_server/api_testing/contracts/__init__.py`
- Modify: `task_server/api_testing/repositories/__init__.py`
- Modify: `task_server/api_testing/services/__init__.py`
- Create: `tests/api_testing/test_suite_service.py`

**Interfaces:**
- Produces: `SuiteService.create_suite(payload, actor)`, `create_version(suite_id, payload, actor)`, `validate_version(version_id, actor)`, `publish(version_id, actor)`, `get()`, and `list()`.

- [ ] **Step 1: Write failing ownership and immutability tests**

```python
def test_published_version_cannot_be_mutated(suite_service, published_version):
    with pytest.raises(SuiteImmutableError):
        suite_service.replace_graph(published_version.id, graph_payload(), "admin")


def test_suite_rejects_cross_project_case_version(suite_service, suite, foreign_case_version):
    with pytest.raises(SuiteScopeError):
        suite_service.create_version(suite.id, graph_payload(foreign_case_version.id), "admin")
```

- [ ] **Step 2: Verify service tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_suite_service.py -q`

Expected: FAIL because suite service is missing.

- [ ] **Step 3: Implement transactional service methods**

Parse payloads into frozen contract dataclasses, verify actor/project/workspace/source/environment/case-version scope in one transaction, canonicalize graph JSON, calculate SHA-256 `graph_hash`, validate, then insert a new version. `publish()` requires `validation_status == "VALID"`, sets `published_at`, and moves `ApiSuite.current_version_id` with optimistic locking.

- [ ] **Step 4: Run service tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_suite_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/contracts/suite.py task_server/api_testing/repositories/suite_repository.py task_server/api_testing/services/suite_service.py task_server/api_testing/contracts/__init__.py task_server/api_testing/repositories/__init__.py task_server/api_testing/services/__init__.py tests/api_testing/test_suite_service.py
git commit -m "Manage versioned API test suites"
```

### Task 4: Add suite HTTP contracts

**Files:**
- Modify: `task_server/api_testing/http.py`
- Test: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Produces: suite list/create/get/version/validate/publish routes from the approved spec.

- [ ] **Step 1: Write failing route tests**

```python
def test_create_suite_version_is_owner_scoped(api_client, project, case_version):
    response = api_client.post(f"/api/api-testing/v1/suites/{project.suite_id}/versions", json=graph_payload(case_version.id))
    assert response.status_code == 200
    assert response.json["data"]["suite_version"]["validation_status"] == "VALID"


def test_publish_invalid_suite_returns_structured_issues(api_client, invalid_suite_version):
    response = api_client.post(f"/api/api-testing/v1/suite-versions/{invalid_suite_version.id}/publish", json={})
    assert response.status_code == 422
    assert response.json["error"]["code"] == "suite_invalid"
    assert response.json["error"]["details"]["issues"][0]["node_key"]
```

- [ ] **Step 2: Verify new routes return 404**

Run: `.venv/bin/python -m pytest tests/api_testing/test_http_contract.py -q`

Expected: FAIL with 404 for suite routes.

- [ ] **Step 3: Wire resource routes to SuiteService**

Keep routing dispatch in `http.py`; do not add suite routes to `router.py`. Use existing `_uuid`, `_view`, authentication, request ID, and scope error conventions.

- [ ] **Step 4: Run HTTP tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/http.py tests/api_testing/test_http_contract.py
git commit -m "Expose API suite resources"
```

### Task 5: Plan immutable suite execution snapshots

**Files:**
- Create: `task_server/api_testing/suite_execution.py`
- Modify: `task_server/api_testing/repositories/execution_repository.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Create: `tests/api_testing/test_suite_execution.py`

**Interfaces:**
- Produces: `SuiteExecutionPlanner.plan(suite_version_id, environment_revision_id, actor, concurrency) -> dict`.
- Extends: `ExecutionService.submit()` accepts exactly one of `case_version_ids` or `suite_version_id`.

- [ ] **Step 1: Write failing snapshot tests**

```python
def test_suite_snapshot_pins_versions_graph_environment_and_limits(planner, published_suite):
    snapshot = planner.plan(published_suite.id, published_suite.environment_revision_id, "admin", 4)
    assert snapshot["suite_version_id"] == published_suite.id
    assert snapshot["levels"] == [["create"], ["list", "detail"]]
    assert snapshot["concurrency"] == 4
    assert all(node["case_version_id"] for node in snapshot["nodes"])


def test_suite_concurrency_above_eight_is_rejected(planner, published_suite):
    with pytest.raises(SuiteExecutionInputError):
        planner.plan(published_suite.id, published_suite.environment_revision_id, "admin", 9)
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_suite_execution.py -q`

Expected: FAIL because planner is absent.

- [ ] **Step 3: Implement planner and execution snapshot creation**

Planner revalidates the published graph, checks environment compatibility, copies nodes/edges/levels/mappings into `request_snapshot`, and returns ordered case versions for child creation. Existing single-case execution snapshots remain backward compatible.

- [ ] **Step 4: Run snapshot and existing execution tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_suite_execution.py tests/api_testing/test_execution_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/suite_execution.py task_server/api_testing/repositories/execution_repository.py task_server/api_testing/services/execution_service.py tests/api_testing/test_suite_execution.py tests/api_testing/test_execution_service.py
git commit -m "Snapshot API suite executions"
```

### Task 6: Execute DAG levels and explicit variables

**Files:**
- Create: `task_server/api_testing/declarative_processing.py`
- Modify: `task_server/api_testing/executor.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Modify: `task_server/api_testing/tasks.py`
- Create: `tests/api_testing/test_declarative_processing.py`
- Modify: `tests/api_testing/test_execution_service.py`
- Modify: `tests/api_testing/test_tasks.py`

**Interfaces:**
- Produces: `apply_pre_steps(spec, variables)`, `extract_outputs(spec, response)`, `map_edge_outputs(mapping, outputs)`.
- Produces: suite execution by topological level with max concurrency 8.

- [ ] **Step 1: Write failing variable and status tests**

```python
def test_only_explicit_edge_outputs_reach_downstream():
    mapped = map_edge_outputs(
        {"favorite_id": "resource_id"},
        {"favorite_id": "42", "access_token": "secret"},
    )
    assert mapped == {"resource_id": "42"}


def test_failed_upstream_skips_downstream_and_preserves_failure(service, suite_execution):
    result = service.run(suite_execution.id)
    assert result.case("create").status == "FAILED"
    assert result.case("delete").status == "SKIPPED"
    assert result.case("delete").sanitized_result["blocked_by"] == ["create"]
    assert result.summary["failed"] == 1
    assert result.summary["skipped"] == 1
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_declarative_processing.py tests/api_testing/test_execution_service.py -q`

Expected: FAIL because mapping and suite scheduling are absent.

- [ ] **Step 3: Implement the whitelist processor and level scheduler**

Supported processing operations are `set`, `delete`, `copy`, `uuid`, `timestamp`, `random_string`, `urlencode`, `base64`, and `sha256`. Reject unknown operations, secret-namespace writes, outputs over configured size, and unresolved inputs as `BROKEN` before HTTP send.

Schedule each topological level with `ThreadPoolExecutor(max_workers=concurrency)`. Open a fresh SQLAlchemy session per worker result persistence; never share a Session across threads. After a level completes, map successful exports into downstream node contexts and mark dependency-blocked nodes `SKIPPED`.

- [ ] **Step 4: Run execution and task tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_declarative_processing.py tests/api_testing/test_execution_service.py tests/api_testing/test_tasks.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/declarative_processing.py task_server/api_testing/executor.py task_server/api_testing/services/execution_service.py task_server/api_testing/tasks.py tests/api_testing/test_declarative_processing.py tests/api_testing/test_execution_service.py tests/api_testing/test_tasks.py
git commit -m "Execute API suites as validated DAGs"
```

### Task 7: Build the compact suite editor

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/api/client.ts`
- Create: `api-testing-ui/src/stores/suites.ts`
- Create: `api-testing-ui/src/components/SuiteEditor.vue`
- Create: `api-testing-ui/src/components/SuiteGraph.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/views/AssetsView.vue`
- Create: `api-testing-ui/src/stores/suites.spec.ts`
- Create: `api-testing-ui/src/components/SuiteEditor.spec.ts`

**Interfaces:**
- Consumes: suite HTTP resources and validation issues.
- Produces: ordered-list editor, optional graph, publish action, and suite execution action.

- [ ] **Step 1: Write failing editor tests**

```ts
it('shows business names and dependency mappings without raw ids', async () => {
  const wrapper = mount(SuiteEditor, { props: { suite: favoriteSuiteFixture } })
  expect(wrapper.text()).toContain('添加收藏')
  expect(wrapper.text()).toContain('favorite_id → resource_id')
  expect(wrapper.text()).not.toContain(favoriteSuiteFixture.nodes[0].caseVersionId)
})


it('locates validation errors on their node', async () => {
  const wrapper = mount(SuiteEditor, { props: { suite: invalidSuiteFixture } })
  expect(wrapper.get('[data-node-key="delete"] [role="alert"]').text()).toContain('缺少变量')
})
```

- [ ] **Step 2: Verify tests fail**

Run: `npm --prefix api-testing-ui test -- --run src/stores/suites.spec.ts src/components/SuiteEditor.spec.ts`

Expected: FAIL because suite UI is absent.

- [ ] **Step 3: Implement progressive suite editing**

Default to a stable ordered list with sequence, parallel group, business name, method/path, dependencies, exports/imports, and validation status. Load `SuiteGraph.vue` only when requested or when a node has multiple edges. Add “保存为新版本”, “校验”, “发布”, and “执行” actions; no new first-level navigation item.

- [ ] **Step 4: Run frontend tests, build, and visual checks**

Run:

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
node tests/api_testing_ui_visual_check.js
```

Expected: PASS with no horizontal overflow or overlapping controls.

- [ ] **Step 5: Commit**

```bash
git add api-testing-ui/src/api/contracts.ts api-testing-ui/src/api/client.ts api-testing-ui/src/stores/suites.ts api-testing-ui/src/components/SuiteEditor.vue api-testing-ui/src/components/SuiteGraph.vue api-testing-ui/src/views/WorkbenchView.vue api-testing-ui/src/views/AssetsView.vue api-testing-ui/src/stores/suites.spec.ts api-testing-ui/src/components/SuiteEditor.spec.ts
git commit -m "Add API suite orchestration workspace"
```

### Task 8: Verify M1 end to end

**Files:**
- Modify: `tests/api_testing_e2e.spec.mjs`
- Modify: `CODEX_STATE.md`

- [ ] **Step 1: Add an end-to-end suite scenario**

Seed three deterministic fixture endpoints where create extracts `favorite_id`, list runs in parallel with detail, and delete imports `favorite_id`. Assert graph validation, execution order, explicit mapping, terminal status, and report counts.

- [ ] **Step 2: Run the full milestone gate**

Run the roadmap milestone gate.

Expected: all checks exit `0`.

- [ ] **Step 3: Deploy and run a production sequential suite**

Use safe read/list operations unless an explicit cleanup endpoint is confirmed. If creation/deletion is used, make cleanup an explicit final node and retain its result. Follow SSE to terminal state and verify any blocked node displays `SKIPPED` with `blocked_by`.

- [ ] **Step 4: Record and commit evidence**

```bash
git add tests/api_testing_e2e.spec.mjs CODEX_STATE.md
git commit -m "Verify API suite orchestration"
```
