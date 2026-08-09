# Task 7 Report

## Header Secret Boundary

- RED: 新增敏感请求头回归后，`api_key`、`API-KEY`、`X-Api-Key`、`access_token`、`Client-Secret`、`PASSWORD`、`Cookie` 共 7 个字面量用例均被错误接受；完整 `{{variable}}` 占位符用例通过。结果为 `7 failed, 1 passed`。
- 修复: 请求头统一复用 `SENSITIVE_KEY`，所有匹配的非空值必须是完整变量占位符，不再只检查 Authorization/Proxy-Authorization。
- GREEN: `tests/api_testing/test_ai_service.py` 聚焦测试 `30 passed in 27.88s`。
