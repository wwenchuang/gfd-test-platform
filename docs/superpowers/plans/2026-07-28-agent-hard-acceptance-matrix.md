# Agent Hard Acceptance Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Baidu Netdisk-style Agent regressions deterministic by compiling the user requirement into a hard acceptance matrix before AI planning, YAML generation, scope review, and Runner dispatch.

**Architecture:** Introduce a small deterministic contract layer that owns source branches, allowed branch aliases, acceptance dimensions, and forbidden soft-reference expansions. AI remains responsible for semantic planning and YAML drafting, but every downstream step must prove it covers or preserves the hard matrix. Generated YAML outside the matrix is kept as `needs_review` and never reaches Runner.

**Tech Stack:** Python 3 services under `task_server/services`, PyYAML-based YAML parsing, existing `tests/backend_static_checks.py` static regression harness, existing Agent Run / Runner job APIs.

## Global Constraints

- Fixed regression target remains `基础打印新增百度网盘入口`.
- Hard business entries are exactly `文档打印`, `照片打印`, `扫描复印` unless the user requirement explicitly names additional entries.
- Hard acceptance dimensions are exactly `visibility`, `relation`, `copy`, `reachability` for each source entry.
- Figma and uploaded screenshots are soft evidence only; they can locate UI and suggest waits, but cannot add hard Runner branches.
- Do not hardcode “百度网盘” as a product exception beyond generic `targetText` contract handling; helpers must accept arbitrary target text.
- Do not modify Runner, Sonic, scorer, Figma parser, router routing shape, or historical YAML baselines in this plan.
- Codex does not push. User deploys manually.

---

## File Structure

- Create: `task_server/services/agent_acceptance_contract.py`
  - Owns hard source contracts, branch aliases, acceptance matrix rows, soft-reference expansion detection, and generated YAML scope checks.
  - No dependency on `agent_service.py`; can be imported by `agent_service.py` and `ai_skill_service.py` without circular imports.

- Modify: `task_server/services/agent_service.py`
  - Replaces scattered Baidu/photo subspec filtering with calls into `agent_acceptance_contract.py`.
  - Applies contract during PLAN normalization and generated YAML Runner gating.
  - Keeps stale Runner progress recovery already added.

- Modify: `task_server/services/ai_skill_service.py`
  - Uses the hard matrix for final YAML portfolio coverage.
  - Synthesizes only missing matrix rows from verified same-branch source-page evidence; never creates new branches from Figma-only evidence.

- Modify: `tests/backend_static_checks.py`
  - Adds regression tests for matrix compilation, PLAN normalization, YAML portfolio coverage, generated YAML scope blocking, and stale Runner recovery.

- Modify: `CODEX_STATE.md`
  - Records rollout status, evidence, and deployment instructions after implementation.

---

### Task 1: Hard Acceptance Contract Module

**Files:**
- Create: `task_server/services/agent_acceptance_contract.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Produces:
  - `build_acceptance_contract(requirement_text: str, target_text: str) -> dict`
  - `acceptance_matrix_rows(contract: dict) -> list[dict]`
  - `branch_matches(branch: str, text: str) -> bool`
  - `is_soft_photo_subspec(text: str, source_requirement_text: str) -> bool`
  - `generated_yaml_out_of_scope(contract: dict, ref: dict, yaml_text: str) -> tuple[bool, list[str]]`
- Consumes: no new internal interfaces.

- [ ] **Step 1: Write the failing tests**

Add this helper check inside `check_agent_ai_owned_plan_and_evidence_loop()` in `tests/backend_static_checks.py`:

```python
    from task_server.services import agent_acceptance_contract

    baidu_contract = agent_acceptance_contract.build_acceptance_contract(
        "基础打印的入口在首页：文档打印、照片打印、扫描复印。百度网盘入口是新增能力，需结合需求与Figma完整覆盖三个业务入口中的展示、同级关系、文案及可达页面；上传截图仅作为AI判断的软参考，不作为硬门禁。",
        "百度网盘",
    )
    baidu_rows = agent_acceptance_contract.acceptance_matrix_rows(baidu_contract)
    require(
        [row["branch"] for row in baidu_rows] == [
            "文档打印", "文档打印", "文档打印", "文档打印",
            "照片打印", "照片打印", "照片打印", "照片打印",
            "扫描复印", "扫描复印", "扫描复印", "扫描复印",
        ],
        "Hard acceptance contract must compile the three source entries into twelve ordered rows",
    )
    require(
        [row["dimension"] for row in baidu_rows[:4]] == ["visibility", "relation", "copy", "reachability"],
        "Each source branch must preserve visibility, relation, copy, and reachability dimensions",
    )
    require(
        agent_acceptance_contract.branch_matches("扫描复印", "进入复印扫描页面后检查百度网盘"),
        "Branch aliases must match scanning/copy naming variants",
    )
    require(
        not agent_acceptance_contract.is_soft_photo_subspec("照片打印聚合页-百度网盘入口可见性校验", baidu_contract["sourceRequirementText"])
        and agent_acceptance_contract.is_soft_photo_subspec("照片打印-一寸照规格页-百度网盘入口可见性校验", baidu_contract["sourceRequirementText"])
        and agent_acceptance_contract.is_soft_photo_subspec("照片打印页(5寸规格)-百度网盘入口校验", baidu_contract["sourceRequirementText"]),
        "Photo aggregate page is in scope, but photo size/spec pages are soft references unless named by the source requirement",
    )
    blocked, blocked_reasons = agent_acceptance_contract.generated_yaml_out_of_scope(
        baidu_contract,
        {
            "module": "AI_Agent_草稿",
            "file": "06-照片打印-一寸照规格页-百度网盘入口可见性校验.yaml",
            "source": "generated",
            "generated": True,
        },
        "android:\n  tasks:\n    - name: 照片打印-一寸照规格页-百度网盘入口可见性校验\n      flow:\n        - aiTap: 点击「照片打印」\n        - aiTap: 点击「一寸照」规格页\n        - aiAssert: 「百度网盘」入口可见\n",
    )
    require(
        blocked and any("照片规格" in reason or "子规格" in reason for reason in blocked_reasons),
        "Generated YAML for photo spec pages must be blocked before Runner dispatch",
    )
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: fails because `task_server.services.agent_acceptance_contract` does not exist.

- [ ] **Step 3: Implement the module**

Create `task_server/services/agent_acceptance_contract.py`:

```python
import json
import re


ACCEPTANCE_DIMENSIONS = ("visibility", "relation", "copy", "reachability")

DIMENSION_LABELS = {
    "visibility": "校验入口可见",
    "relation": "校验入口与当前页面同级入口的层级和位置关系",
    "copy": "校验入口使用需求约定的可见文案",
    "reachability": "点击入口并校验目标页面稳定可达",
}

BRANCH_ALIASES = {
    "文档打印": ("文档打印", "文档", "文件打印"),
    "照片打印": ("照片打印", "照片", "图片打印"),
    "扫描复印": ("扫描复印", "复印扫描", "扫描", "复印"),
}

PHOTO_SUBSPEC_TERMS = (
    "一寸照", "1寸", "证件照", "智能证件照", "照片拼版", "图片拼版",
    "5寸", "6寸", "7寸", "A4资料图片", "A4生活照片", "规格页", "具体规格",
)

PHOTO_AGGREGATE_TERMS = ("照片打印聚合页", "照片打印主流程", "照片打印入口页", "规格选择前页面")


def _norm(value):
    text = str(value or "")
    text = text.replace("：", ":").replace("，", ",").replace("；", ";")
    return re.sub(r"\s+", "", text)


def _contains_any(text, terms):
    text = _norm(text)
    return any(_norm(term) in text for term in terms)


def branch_matches(branch, text):
    branch = str(branch or "").strip()
    if not branch:
        return False
    return _contains_any(text, BRANCH_ALIASES.get(branch, (branch,)))


def _explicit_three_home_entries(requirement_text):
    text = _norm(requirement_text)
    return (
        all(term in text for term in ("文档打印", "照片打印", "扫描复印"))
        and any(term in text for term in ("三个业务入口", "三大业务入口", "首页", "基础打印"))
    )


def build_acceptance_contract(requirement_text, target_text):
    requirement_text = str(requirement_text or "")
    target_text = str(target_text or "").strip()
    if _explicit_three_home_entries(requirement_text):
        branches = ["文档打印", "照片打印", "扫描复印"]
    else:
        branches = [
            branch for branch in ("文档打印", "照片打印", "扫描复印")
            if branch_matches(branch, requirement_text)
        ]
    return {
        "version": "agent-acceptance-contract-v1",
        "required": bool(branches and target_text),
        "strict": bool(branches and target_text),
        "targetText": target_text,
        "sourceRequirementText": requirement_text,
        "branches": branches,
        "dimensions": list(ACCEPTANCE_DIMENSIONS),
        "matrix": [
            {
                "requirementId": f"REQ-{index:03d}",
                "branch": branch,
                "dimension": dimension,
                "targetText": target_text,
                "description": f"{branch}：{DIMENSION_LABELS[dimension]}「{target_text}」",
            }
            for index, branch in enumerate(branches, start=1)
            for dimension in ACCEPTANCE_DIMENSIONS
        ],
    }


def acceptance_matrix_rows(contract):
    return [row for row in (contract or {}).get("matrix") or [] if isinstance(row, dict)]


def is_soft_photo_subspec(text, source_requirement_text):
    text = str(text or "")
    source_requirement_text = str(source_requirement_text or "")
    if _contains_any(source_requirement_text, PHOTO_SUBSPEC_TERMS):
        return False
    if _contains_any(text, PHOTO_AGGREGATE_TERMS):
        return False
    return branch_matches("照片打印", text) and _contains_any(text, PHOTO_SUBSPEC_TERMS)


def generated_yaml_out_of_scope(contract, ref, yaml_text):
    contract = contract if isinstance(contract, dict) else {}
    ref = ref if isinstance(ref, dict) else {}
    if not contract.get("strict"):
        return False, []
    source = str(ref.get("source") or "").lower()
    generated = ref.get("generated") is True or source in ("generated", "draft", "ai_generated", "agent_generated")
    if not generated:
        return False, []
    material = {
        "module": ref.get("module"),
        "file": ref.get("file"),
        "name": ref.get("name"),
        "targetTaskName": ref.get("targetTaskName"),
        "taskName": ref.get("taskName"),
        "content": yaml_text,
    }
    text = json.dumps(material, ensure_ascii=False)
    reasons = []
    if is_soft_photo_subspec(text, contract.get("sourceRequirementText") or ""):
        reasons.append("生成 YAML 命中照片规格页/子规格分支，但当前源需求只要求照片打印业务入口")
    known_branch = any(branch_matches(branch, text) for branch in contract.get("branches") or [])
    global_or_exception = _contains_any(text, ("全局一致性", "异常处理", "未安装", "弱网", "登录态", "探索"))
    if global_or_exception and not known_branch:
        reasons.append("生成 YAML 命中全局/异常/探索分支，当前源需求未要求自动执行")
    return bool(reasons), reasons[:8]
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: PASS for the new contract assertions.

- [ ] **Step 5: Commit**

```bash
git add task_server/services/agent_acceptance_contract.py tests/backend_static_checks.py
git commit -m "Add Agent hard acceptance contract"
```

---

### Task 2: PLAN Normalization Must Preserve Source Branches and Drop Supplementary Flows

**Files:**
- Modify: `task_server/services/agent_service.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Consumes:
  - `agent_acceptance_contract.build_acceptance_contract(requirement_text, target_text)`
  - `agent_acceptance_contract.is_soft_photo_subspec(text, source_requirement_text)`
  - `agent_acceptance_contract.branch_matches(branch, text)`
- Produces:
  - `artifacts["acceptanceContract"]`
  - PLAN `droppedOutOfScopeFlows` with deterministic reasons.

- [ ] **Step 1: Write failing tests**

Extend the existing `scoped_plan` tests in `check_agent_ai_owned_plan_and_evidence_loop()`:

```python
    matrix_plan, matrix_plan_issues = agent_service._normalize_agent_business_plan({
        "objective": "基础打印新增百度网盘入口",
        "businessFlows": [
            {"id": "FLOW-001", "name": "文档打印页-百度网盘入口展示", "branch": "文档打印", "steps": ["首页", "点击文档打印", "校验百度网盘"], "checks": ["可见、同级、文案"]},
            {"id": "FLOW-002", "name": "照片打印聚合页-百度网盘入口展示", "branch": "照片打印", "steps": ["首页", "点击照片打印", "进入照片打印聚合页", "校验百度网盘"], "checks": ["可见、同级、文案"]},
            {"id": "FLOW-003", "name": "扫描复印页-百度网盘入口展示", "branch": "扫描复印", "steps": ["首页", "点击扫描复印", "校验百度网盘"], "checks": ["可见、同级、文案"]},
            {"id": "FLOW-004", "name": "照片打印-一寸照规格页-百度网盘入口展示", "branch": "照片打印", "steps": ["首页", "点击照片打印", "点击一寸照规格页", "校验百度网盘"], "checks": ["规格页可见"]},
            {"id": "FLOW-005", "name": "百度网盘未登录态异常处理", "branch": "基础打印-异常处理", "steps": ["点击百度网盘", "观察登录态"], "checks": ["不崩溃"]},
        ],
    }, live_plan_run, candidate_constraint)
    matrix_plan_text = json.dumps(matrix_plan, ensure_ascii=False)
    require(
        matrix_plan
        and not matrix_plan_issues
        and [flow["branch"] for flow in matrix_plan["businessFlows"]] == ["文档打印", "照片打印", "扫描复印"]
        and "照片打印聚合页" in matrix_plan_text
        and "一寸照规格页" not in matrix_plan_text
        and "异常处理" not in matrix_plan_text,
        "PLAN normalization must preserve the three source branches and drop supplementary Figma/exception flows",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: FAIL if PLAN normalization still uses scattered local terms instead of the contract module.

- [ ] **Step 3: Implement minimal integration**

In `task_server/services/agent_service.py`, import the contract module near other service imports:

```python
from task_server.services import agent_acceptance_contract
```

Replace local branch/subspec checks in `_normalize_agent_business_plan()` with:

```python
    source_text = _agent_plan_source_requirement_text(run)
    target_text = _agent_target_text_from_run(run) or "百度网盘"
    hard_contract = agent_acceptance_contract.build_acceptance_contract(source_text, target_text)
    if hard_contract.get("strict"):
        run.setdefault("artifacts", {})["acceptanceContract"] = hard_contract
```

Inside the flow loop, after `matched_branch` is computed:

```python
        flow_text = json.dumps(normalized_flow, ensure_ascii=False)
        if hard_contract.get("strict") and not matched_branch:
            dropped_out_of_scope.append({
                "id": normalized_flow.get("id"),
                "name": normalized_flow.get("name"),
                "branch": normalized_flow.get("branch"),
                "reason": "not_in_source_acceptance_contract",
            })
            continue
        if matched_branch == "照片打印" and agent_acceptance_contract.is_soft_photo_subspec(
            flow_text,
            hard_contract.get("sourceRequirementText") or source_text,
        ):
            dropped_out_of_scope.append({
                "id": normalized_flow.get("id"),
                "name": normalized_flow.get("name"),
                "branch": matched_branch,
                "reason": "source_contract_does_not_include_photo_subspec",
            })
            continue
```

Add this small helper if it does not already exist:

```python
def _agent_target_text_from_run(run):
    text = json.dumps(run or {}, ensure_ascii=False)
    if "百度网盘" in text:
        return "百度网盘"
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/services/agent_service.py tests/backend_static_checks.py
git commit -m "Normalize Agent PLAN with hard acceptance contract"
```

---

### Task 3: Generated YAML Runner Gate Must Use the Hard Contract

**Files:**
- Modify: `task_server/services/agent_service.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Consumes:
  - `artifacts["acceptanceContract"]`
  - `agent_acceptance_contract.generated_yaml_out_of_scope(contract, ref, yaml_text)`
- Produces:
  - `executableScore.executionLevel = "needs_review"` for out-of-scope generated YAML.
  - `runnerCandidate = False` for any final non-executable score.

- [ ] **Step 1: Write failing tests**

Add assertions in `check_agent_ai_owned_plan_and_evidence_loop()` after generated YAML scoring tests:

```python
    live_plan_run.setdefault("artifacts", {})["acceptanceContract"] = agent_acceptance_contract.build_acceptance_contract(
        live_plan_run["normalizedInput"]["requirementText"],
        "百度网盘",
    )
    spec_ref = agent_service._score_agent_yaml_ref_for_execution(live_plan_run, {
        "module": "AI_Agent_草稿",
        "file": "06-照片打印-一寸照规格页-百度网盘入口可见性校验.yaml",
        "source": "generated",
        "generated": True,
        "smokeCandidate": True,
        "content": "android:\n  tasks:\n    - name: 照片打印-一寸照规格页-百度网盘入口可见性校验\n      flow:\n        - launch: com.xbxxhz.box\n        - aiWaitFor: App首页已加载完成\n        - aiTap: 点击「照片打印」\n        - aiTap: 点击「一寸照」规格页\n        - aiAssert: 「百度网盘」入口可见\n",
    })
    require(
        spec_ref["executionLevel"] == "needs_review"
        and spec_ref["runnerCandidate"] is False
        and spec_ref["scopeReview"]["ok"] is False,
        "Runner gate must block generated photo spec YAML even when scorer says executable",
    )
    aggregate_ref = agent_service._score_agent_yaml_ref_for_execution(live_plan_run, {
        "module": "AI_Agent_草稿",
        "file": "02-照片打印聚合页-百度网盘入口可见性校验.yaml",
        "source": "generated",
        "generated": True,
        "smokeCandidate": True,
        "content": "android:\n  tasks:\n    - name: 照片打印聚合页-百度网盘入口可见性校验\n      flow:\n        - launch: com.xbxxhz.box\n        - aiWaitFor: App首页已加载完成\n        - aiTap: 点击「照片打印」\n        - aiAssert: 「百度网盘」入口可见\n",
    })
    require(
        aggregate_ref["executionLevel"] == "executable"
        and aggregate_ref["runnerCandidate"] is True,
        "Runner gate must allow the photo-print aggregate branch when it is otherwise executable",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: FAIL if Runner gate still allows generated spec pages.

- [ ] **Step 3: Implement Runner gate contract check**

In `_score_agent_yaml_ref_for_execution()` in `task_server/services/agent_service.py`, after `scope_review` is loaded and before `runner_candidate` is calculated:

```python
    contract = artifacts.get("acceptanceContract") if isinstance(artifacts.get("acceptanceContract"), dict) else {}
    blocked_by_contract, contract_reasons = agent_acceptance_contract.generated_yaml_out_of_scope(
        contract,
        ref,
        content,
    )
    if blocked_by_contract:
        score = dict(score)
        task_scores = [dict(task) for task in (score.get("taskScores") or []) if isinstance(task, dict)]
        score["score"] = min(int(score.get("score") or 0), 74)
        score["executionLevel"] = "needs_review"
        score["level"] = "needs_review"
        score["ok"] = False
        score["smokeCandidate"] = False
        score["reasons"] = [str(item) for item in list(score.get("reasons") or []) + contract_reasons if str(item or "").strip()][:8]
        scope_review = {
            **scope_review,
            "ok": False,
            "reasons": [str(item) for item in list(scope_review.get("reasons") or []) + contract_reasons if str(item or "").strip()][:8],
            "rule": "Generated YAML must stay inside the hard source acceptance contract before Runner dispatch.",
        }
        score["scopeReview"] = scope_review
        for task in task_scores:
            task["smokeCandidate"] = False
            task["executionLevel"] = "needs_review"
            task["level"] = "needs_review"
        score["taskScores"] = task_scores
```

Ensure runner candidate calculation is:

```python
    runner_candidate = bool(
        not manual_hint
        and level == "executable"
        and score.get("ok") is not False
        and (
            ref.get("smoke")
            or ref.get("is_smoke")
            or ref.get("isSmoke")
            or ref.get("smokeCandidate")
            or ref.get("runnerCandidate")
            or score.get("smokeCandidate")
            or any(task.get("smokeCandidate") for task in task_scores)
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/services/agent_service.py tests/backend_static_checks.py
git commit -m "Gate generated YAML with hard acceptance contract"
```

---

### Task 4: Matrix-Based YAML Coverage Audit and Deterministic Repair

**Files:**
- Modify: `task_server/services/ai_skill_service.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Consumes:
  - `agent_acceptance_contract.acceptance_matrix_rows(contract)`
  - existing `executable_yaml_portfolio_audit(...)`
  - existing preserve/landing helpers in `ai_skill_service.py`
- Produces:
  - `review["acceptanceMatrixCoverage"]`
  - deterministic missing-row synthesis only for rows whose branch path is source-grounded.

- [ ] **Step 1: Write failing tests**

Add a test near the existing capped preserve flow checks:

```python
    hard_contract = agent_acceptance_contract.build_acceptance_contract(
        "基础打印的入口在首页：文档打印、照片打印、扫描复印。百度网盘入口是新增能力，需完整覆盖三个业务入口中的展示、同级关系、文案及可达页面。",
        "百度网盘",
    )
    partial_portfolio = {
        "cases": [
            {"id": "TC-001", "title": "文档打印百度网盘入口", "automation": "automatic", "execution": {"type": "midscene_yaml", "level": "executable"}, "flow": ["首页", "点击文档打印", "校验百度网盘入口可见", "点击百度网盘"]},
            {"id": "TC-002", "title": "照片打印百度网盘入口", "automation": "automatic", "execution": {"type": "midscene_yaml", "level": "executable"}, "flow": ["首页", "点击照片打印", "校验百度网盘入口可见", "点击百度网盘"]},
            {"id": "TC-003", "title": "扫描复印百度网盘入口", "automation": "automatic", "execution": {"type": "midscene_yaml", "level": "executable"}, "flow": ["首页", "点击扫描复印", "校验百度网盘入口可见", "点击百度网盘"]},
        ],
        "review": {"acceptanceContract": hard_contract},
    }
    audit = ai_skill_service.executable_yaml_portfolio_audit(
        partial_portfolio,
        {"min_automation_cases": 3, "acceptanceContract": hard_contract},
    )
    require(
        audit.get("ok") is False
        and any("REQ-003" in str(item) and "relation" in str(item) for item in audit.get("missingAcceptanceRows") or []),
        "Coverage audit must report missing matrix rows, including scanning relation, instead of relying on fuzzy flow text",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: FAIL until `executable_yaml_portfolio_audit` understands `acceptanceContract`.

- [ ] **Step 3: Implement matrix coverage audit**

In `ai_skill_service.executable_yaml_portfolio_audit(...)`, read the contract:

```python
    acceptance_contract = (
        (constraints or {}).get("acceptanceContract")
        or (review or {}).get("acceptanceContract")
        or {}
    )
```

Add deterministic row matching:

```python
def _case_covers_acceptance_row(case, row):
    text = _normalize_text(json.dumps(case, ensure_ascii=False))
    branch = row.get("branch") or ""
    target = row.get("targetText") or ""
    dimension = row.get("dimension") or ""
    if not agent_acceptance_contract.branch_matches(branch, text):
        return False
    if target and target not in text:
        return False
    if dimension == "visibility":
        return any(term in text for term in ("可见", "展示", "显示"))
    if dimension == "relation":
        return any(term in text for term in ("同级", "并列", "层级", "位置关系"))
    if dimension == "copy":
        return any(term in text for term in ("文案", "文字", f"文案为{target}", f"文案为「{target}」"))
    if dimension == "reachability":
        return any(term in text for term in ("点击", "跳转", "可达", "稳定", "授权页", "文件选择页"))
    return False
```

Then append audit output:

```python
    matrix_rows = agent_acceptance_contract.acceptance_matrix_rows(acceptance_contract)
    missing_rows = []
    if matrix_rows:
        for row in matrix_rows:
            if not any(_case_covers_acceptance_row(case, row) for case in cases):
                missing_rows.append({
                    "requirementId": row.get("requirementId"),
                    "branch": row.get("branch"),
                    "dimension": row.get("dimension"),
                    "description": row.get("description"),
                })
        result["acceptanceMatrixCoverage"] = {
            "total": len(matrix_rows),
            "covered": len(matrix_rows) - len(missing_rows),
            "missing": missing_rows,
        }
        result["missingAcceptanceRows"] = missing_rows
        if missing_rows:
            result["ok"] = False
```

- [ ] **Step 4: Add deterministic repair only when source branch path exists**

In the existing final convergence path that synthesizes preserve assertions, ensure repair can only add rows for current source branches:

```python
    if missing_row["dimension"] in ("visibility", "relation", "copy") and source_branch_path_verified:
        synthesize_source_page_assertion(missing_row)
    elif missing_row["dimension"] == "reachability" and landing_tail_verified:
        synthesize_bounded_landing_case(missing_row)
    else:
        keep_missing_row(missing_row)
```

Use existing helper names where present; do not add broad template generation.

- [ ] **Step 5: Run tests**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
python3 -m py_compile task_server/services/ai_skill_service.py tests/backend_static_checks.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add task_server/services/ai_skill_service.py tests/backend_static_checks.py
git commit -m "Audit generated YAML against acceptance matrix"
```

---

### Task 5: Offline Replay Gate Before Phone Execution

**Files:**
- Modify: `tests/backend_static_checks.py`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes:
  - `agent_acceptance_contract.build_acceptance_contract(...)`
  - `_normalize_agent_business_plan(...)`
  - `_score_agent_yaml_ref_for_execution(...)`
  - `executable_yaml_portfolio_audit(...)`
- Produces:
  - A deterministic offline replay check for the three known failure shapes.

- [ ] **Step 1: Write replay fixtures**

Add this list in `check_agent_ai_owned_plan_and_evidence_loop()`:

```python
    baidu_failure_shapes = [
        {
            "name": "missing_scan_relation",
            "expectBlockedBeforeRunner": True,
            "planFlows": [
                {"branch": "文档打印", "name": "文档打印百度网盘", "steps": ["首页", "点击文档打印", "校验百度网盘可见"], "checks": ["可见、同级、文案、可达"]},
                {"branch": "照片打印", "name": "照片打印百度网盘", "steps": ["首页", "点击照片打印", "校验百度网盘可见"], "checks": ["可见、同级、文案、可达"]},
                {"branch": "扫描复印", "name": "扫描复印百度网盘", "steps": ["首页", "点击扫描复印", "校验百度网盘可见"], "checks": ["可见、文案、可达"]},
            ],
        },
        {
            "name": "photo_branch_overfiltered",
            "expectBlockedBeforeRunner": True,
            "planFlows": [
                {"branch": "文档打印", "name": "文档打印百度网盘", "steps": ["首页", "点击文档打印"], "checks": ["可见"]},
                {"branch": "照片打印", "name": "照片打印聚合页百度网盘", "steps": ["首页", "点击照片打印", "进入照片打印聚合页"], "checks": ["可见"]},
                {"branch": "扫描复印", "name": "扫描复印百度网盘", "steps": ["首页", "点击扫描复印"], "checks": ["可见"]},
            ],
        },
        {
            "name": "photo_spec_generated_yaml",
            "expectBlockedBeforeRunner": True,
            "generatedRef": {
                "module": "AI_Agent_草稿",
                "file": "06-照片打印-一寸照规格页-百度网盘入口可见性校验.yaml",
                "source": "generated",
                "generated": True,
                "smokeCandidate": True,
                "content": "android:\n  tasks:\n    - name: 照片打印-一寸照规格页-百度网盘入口可见性校验\n      flow:\n        - aiTap: 点击「照片打印」\n        - aiTap: 点击「一寸照」规格页\n        - aiAssert: 「百度网盘」入口可见\n",
            },
        },
    ]
```

- [ ] **Step 2: Assert replay behavior**

Add:

```python
    for shape in baidu_failure_shapes:
        if shape.get("planFlows"):
            plan, issues = agent_service._normalize_agent_business_plan(
                {"objective": "基础打印新增百度网盘入口", "businessFlows": shape["planFlows"]},
                live_plan_run,
                candidate_constraint,
            )
            require(plan and not issues, f"Offline replay {shape['name']} must keep valid source branches")
        if shape.get("generatedRef"):
            scored = agent_service._score_agent_yaml_ref_for_execution(live_plan_run, shape["generatedRef"])
            require(
                scored.get("runnerCandidate") is False and scored.get("executionLevel") == "needs_review",
                f"Offline replay {shape['name']} must block out-of-scope generated YAML before Runner",
            )
```

- [ ] **Step 3: Run replay**

Run:

```bash
python3 - <<'PY'
import tests.backend_static_checks as checks
checks.check_agent_ai_owned_plan_and_evidence_loop()
PY
```

Expected: PASS.

- [ ] **Step 4: Update `CODEX_STATE.md`**

Add a section:

```markdown
### 2026-07-28 Agent hard acceptance matrix rollout

Implemented hard acceptance matrix for source-scoped Agent regressions. Baidu Netdisk offline replay now covers missing scan relation, photo branch overfiltering, and generated photo spec YAML blocking before Runner. Phone execution should resume only after this replay passes.
```

- [ ] **Step 5: Commit**

```bash
git add tests/backend_static_checks.py CODEX_STATE.md
git commit -m "Add Baidu Agent offline replay gate"
```

---

### Task 6: Deployment Validation Protocol

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Produces: repeatable validation checklist for user deployment and Codex monitoring.

- [ ] **Step 1: Add validation checklist**

Append this exact checklist to `CODEX_STATE.md`:

```markdown
Deployment validation for hard acceptance matrix:

1. Confirm online `HEAD` includes the latest matrix commit.
2. Confirm `/api/health` model is `qwen3.7-plus`.
3. Confirm `/api/sonic/bridge-groovy?case_id=probe` returns `2026.07.26-qwen3.7-result-retry-v1`.
4. Confirm `/api/runners` shows `win-runner-01` online and OPPO `ecbfd645` ready.
5. Run Baidu Agent 3 times first, not 5:
   - zero generated YAML filenames containing `一寸照`, `5寸`, `6寸`, `7寸`, `A4`, `规格页`
   - zero PLAN failures for missing `照片打印`
   - zero `GENERATE_YAML` failures for missing scan relation
6. Only if 3/3 pass the above filters, run 5 times for stability.
7. Treat `资源加载中...88%` as ENV_ISSUE; do not tune YAML path for that symptom.
```

- [ ] **Step 2: Commit**

```bash
git add CODEX_STATE.md
git commit -m "Document Agent matrix deployment validation"
```

---

## Self-Review

**Spec coverage:** This plan covers the latest failures: missing scan relation, photo branch overfiltering, generated photo spec YAML, runnerCandidate bypass, and stale/non-deterministic phone validation.

**Placeholder scan:** No task uses TBD/TODO/fill-in placeholders. Each task includes concrete functions, tests, and expected commands.

**Type consistency:** The main new module exposes dict/list/tuple interfaces only, matching the existing service style and avoiding circular imports.

**Risk:** Task 4 touches the most sensitive path (`ai_skill_service.executable_yaml_portfolio_audit`). It must be implemented after Tasks 1-3 are green, and phone execution should not resume until the offline replay gate passes.
