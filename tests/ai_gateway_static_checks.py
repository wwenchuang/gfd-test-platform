#!/usr/bin/env python3
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "ai-gateway"
DOCS = ROOT / "docs"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    required = [
        "package.json",
        ".env.example",
        "server.js",
        "config/agent-whitelist.json",
        "config/model-router.json",
        "config/providers.json",
        "prompts/generate-case-v1.txt",
        "prompts/generate-yaml-v1.txt",
        "prompts/analyze-failure-v1.txt",
        "prompts/optimize-yaml-v1.txt",
        "prompts/generate-bug-v1.txt",
        "validators/midscene-yaml-validator.js",
        "agent/agent-state-machine.js",
        "agent/agent-memory.js",
        "agent/agent-logger.js",
        "agent/agent-policy.js",
        "agent/agent-tools.js",
        "agent/agent-orchestrator.js",
        "README.md",
    ]
    for rel in required:
        require((GATEWAY / rel).exists(), f"missing ai-gateway/{rel}")

    package = json.loads((GATEWAY / "package.json").read_text(encoding="utf-8"))
    deps = package.get("dependencies", {})
    for dep in ("express", "cors", "dotenv", "openai", "js-yaml", "uuid"):
      require(dep in deps, f"ai-gateway dependency missing: {dep}")
    require(package.get("type") == "module", "ai-gateway must use ESM modules")

    env = (GATEWAY / ".env.example").read_text(encoding="utf-8")
    require("QWEN_API_KEY=your_dashscope_api_key" in env, "env example must not contain a real API key")
    require("HIGHWAY_API_KEY=your_highway_api_key" in env, "env example must include HighwayAPI placeholder")
    for removed in ("AI_PROVIDER=", "QWEN_MODEL=", "HIGHWAY_MODEL=", "QWEN_BASE_URL=", "HIGHWAY_BASE_URL="):
        require(removed not in env, f"env example must not hardcode single-model setting: {removed}")
    require("AI_GATEWAY_MOCK=0" in env, "mock mode must be disabled by default")

    providers = json.loads((GATEWAY / "config/providers.json").read_text(encoding="utf-8")).get("providers", {})
    for provider_id in ("highway_gpt5_mini", "highway_gpt4_1_mini", "highway_deepseek", "qwen_plus"):
        require(provider_id in providers, f"providers.json missing provider: {provider_id}")
        provider = providers[provider_id]
        require(provider.get("type") == "openai_compatible", f"{provider_id} must be OpenAI compatible")
        require(provider.get("apiKeyEnv") in ("HIGHWAY_API_KEY", "QWEN_API_KEY"), f"{provider_id} must reference key env only")
        require("apiKey" not in provider, f"{provider_id} must not store a real API key")
    require(providers["highway_gpt5_mini"].get("temperatureLocked") is True, "gpt-5-mini must lock temperature")
    require(providers["highway_gpt5_mini"].get("fixedTemperature") == 1, "gpt-5-mini must force temperature 1")

    router = json.loads((GATEWAY / "config/model-router.json").read_text(encoding="utf-8"))
    expected_routes = {
        "generate_case": "qwen_plus",
        "generate_yaml": "qwen_plus",
        "analyze_failure": "qwen_plus",
        "optimize_yaml": "qwen_plus",
        "agent_plan": "qwen_plus",
        "generate_bug": "qwen_plus",
    }
    for action, provider_id in expected_routes.items():
        require(action in router, f"model router missing action: {action}")
        require(router[action].get("providerId") == provider_id, f"{action} must route to {provider_id}")

    whitelist = json.loads((GATEWAY / "config/agent-whitelist.json").read_text(encoding="utf-8"))
    require(whitelist.get("fullAutoEnabled") is False, "FULL_AUTO must be disabled by default")
    for key in ("allowedTasks", "blockedKeywords"):
        require(isinstance(whitelist.get(key), list), f"agent whitelist must contain list: {key}")
    for keyword in ("确认打印", "支付", "删除", "覆盖基线"):
        require(keyword in whitelist.get("blockedKeywords", []), f"blocked keyword missing: {keyword}")

    server = (GATEWAY / "server.js").read_text(encoding="utf-8")
    require("/ai/generate-yaml" in server and "/ai/validate-yaml" in server, "server must expose P0 endpoints")
    require("req.body?.requirement" in server and "req.body?.sourceContext" in server and "req.body?.businessContext" in server, "generate-yaml endpoint must pass requirement/source/Figma context to the model")
    require("/ai/providers" in server and "/ai/providers/test" in server and "/ai/model-router" in server, "server must expose provider and model-router endpoints")
    require("/ai/optimize-yaml" in server and "/ai/chat" in server, "server must expose AI Gateway integration endpoints")
    require("PROVIDERS_FILE" in server and "apiKeyEnv" in server and "providerId" in server, "server must route by providers.json and apiKeyEnv")
    require("clientForRoute" in server and "process.env[route.apiKeyEnv]" in server, "server must read API keys server-side only")
    require("temperatureLocked" in server and "fixedTemperature" in server and "options.temperature = typeof route.fixedTemperature" in server, "locked-temperature providers must be enforced")
    for forbidden_param in ("top_p", "presence_penalty", "frequency_penalty"):
        require(forbidden_param not in server, f"server must not send {forbidden_param} to gpt-5")
    for endpoint in ("/agent/run", "/agent/runs/:runId/confirm", "/agent/runs/:runId/cancel"):
        require(endpoint in server, f"server must expose Agent endpoint: {endpoint}")
    require("QWEN_API_KEY" in server and "new OpenAI" in server, "server must use server-side OpenAI-compatible Qwen client")
    require("'logs'" in server and "'ai-calls.jsonl'" in server, "server must log AI calls to JSONL")
    require("AI_GATEWAY_MOCK" in server and "mockAiOutput" in server, "server must support local mock mode for checks")
    require(not re.search(r"sk-[A-Za-z0-9_-]{12,}", server), "server must not contain real OpenAI/DashScope keys")
    require(not re.search(r"hk-[A-Za-z0-9_-]{12,}", server), "server must not contain real HighwayAPI keys")
    require(not re.search(r"figd_[A-Za-z0-9_-]{12,}", server), "server must not contain real Figma tokens")

    state_machine = (GATEWAY / "agent/agent-state-machine.js").read_text(encoding="utf-8")
    for state in (
        "START",
        "GENERATE_CASE",
        "GENERATE_YAML",
        "VALIDATE_YAML",
        "SAVE_ASSET",
        "WAIT_CONFIRM_RUN",
        "RUN_TASK",
        "VALIDATE_REPAIRED_YAML",
        "RERUN_TASK",
        "GENERATE_BUG_DRAFT",
        "WAIT_CONFIRM_BUG",
        "WAIT_CONFIRM",
        "CREATE_FEISHU_TICKET",
        "NOTIFY_FEISHU",
        "FAILED",
        "CANCELLED",
    ):
        require(state in state_machine, f"Agent state missing: {state}")
    require("hardMaxRetries: 3" in state_machine, "Agent retry hard limit must be 3")

    policy = (GATEWAY / "agent/agent-policy.js").read_text(encoding="utf-8")
    for mode in ("SEMI_AUTO", "AUTO_SAFE", "FULL_AUTO"):
        require(mode in policy, f"Agent mode missing: {mode}")
    require("fullAutoEnabled" in policy and "allowedTasks" in policy, "FULL_AUTO must be guarded by whitelist")
    require("blockedKeywords" in policy and "强制降级为 SEMI_AUTO" in policy, "blocked keywords must downgrade mode")

    tools = (GATEWAY / "agent/agent-tools.js").read_text(encoding="utf-8")
    require("不允许 Agent 直接操作 Sonic 页面" in tools, "Sonic tool must stay confirmation-only in phase one")
    require("autoCreateBug 默认关闭" in tools, "Feishu bug tool must require confirmation")
    require("agent-assets" in tools and "writeFile" in tools, "saveYamlAsset must persist local draft before Task API exists")

    orchestrator = (GATEWAY / "agent/agent-orchestrator.js").read_text(encoding="utf-8")
    for step in ("generateCase", "generateYaml", "validateYaml"):
        require(step in orchestrator, f"Agent orchestrator must call {step}")
    require("applyAgentPolicy" in orchestrator and "saveYamlAsset" in orchestrator, "Agent must apply policy and save draft asset")
    require("WAIT_CONFIRM" in orchestrator, "Agent must stop at human confirmation in phase one")

    refs = (DOCS / "OFFICIAL_REFERENCE_SOURCES.md").read_text(encoding="utf-8")
    for url in (
        "https://soniccloudorg.github.io/",
        "https://gitee.com/sonic-cloud",
        "https://www.testerhome.com/search?q=Sonic",
        "https://midscenejs.com/yaml-script-runner",
        "https://midscenejs.com/model-configuration",
    ):
        require(url in refs, f"official reference source missing: {url}")

    design = (DOCS / "AGENT_MODE_DESIGN.md").read_text(encoding="utf-8")
    require("受控 Agent" in design and "不直接改现有 Task/Sonic 基线链路" in design, "Agent design must preserve baseline flow")
    require("先跑 `AUTO_SAFE`" in design and "开启 `FULL_AUTO` 夜间回归" in design, "Agent design must include rollout stages")
    require("不允许 Agent 删除已有用例资产" in design, "Agent design must forbid deleting assets")
    require("不允许 Agent 覆盖人工锁定的基线 YAML" in design, "Agent design must protect locked baseline YAML")

    validator = (GATEWAY / "validators/midscene-yaml-validator.js").read_text(encoding="utf-8")
    for forbidden in ("repeat", "click", "tap", "wait", "loop"):
        require(forbidden in validator, f"validator must reject {forbidden}")
    for allowed in ("sleep", "aiTap", "aiAction", "aiAssert"):
        require(allowed in validator, f"validator must allow {allowed}")

    prompt = (GATEWAY / "prompts/generate-yaml-v1.txt").read_text(encoding="utf-8")
    require("禁止使用 repeat" in prompt and "只输出 YAML" in prompt, "generate YAML prompt must enforce Midscene constraints")

    print({"ok": True, "dir": str(GATEWAY), "checks": 46})


if __name__ == "__main__":
    main()
