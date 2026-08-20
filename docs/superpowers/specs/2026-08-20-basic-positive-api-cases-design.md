# Basic Positive API Case Generation Design

## Goal

Add a platform-generated case action that creates the simplest positive API test case drafts from already imported API endpoint contracts.

## Scope

- Source data is the active API source revision already loaded by API Testing.
- Generation is deterministic and synchronous; it does not call the AI Gateway.
- Authentication tokens and business headers must come from the selected environment revision through default headers or variable placeholders.
- Generated cases are editable drafts and can be debugged, saved into tasks, and adopted as baselines through existing flows.

## Behavior

- Input: `endpoint_ids`, `environment_revision_id`, and optional `task_id`.
- Output: created `case_versions`.
- For each selected endpoint, create one draft named `<接口名> - 基础正向流程`.
- Request method and path are bound to the selected endpoint.
- Path, query, and cookie parameters are filled from examples, defaults, enum values, or safe synthetic values for required fields.
- Required runtime-managed headers use environment defaults first; when a required header is not in default headers but a same-name environment variable exists, use `{{VariableName}}`.
- Request body uses JSON examples/defaults first, then required schema properties with safe synthetic values.
- Assertions include a documented 2xx status assertion and optional business success assertion for obvious `code == 0` or `success == true` response contracts.

## API

`POST /api/api-testing/v1/cases/basic-positive`

```json
{
  "endpoint_ids": ["..."],
  "environment_revision_id": "...",
  "task_id": "..."
}
```

Response:

```json
{
  "case_versions": []
}
```

## UI

In the Workbench AI assistant panel, add a separate action named `生成基础正向用例`. It uses the current selected interface scope and selected execution environment, then registers the returned drafts in the existing case store.

## Validation

- Backend unit tests cover payload generation from OpenAPI examples, required parameters, environment-managed headers, and reportable case versions.
- Frontend store/view tests cover API call payload, version registration, task scope saving, and first generated endpoint activation.
