# Project Structure

## 固定工程目录

当前平台已经从历史对话目录独立出来：

```text
/Users/wenchuang/Documents/Codex/midscene-task-platform
```

以后新对话继续优化时，默认让 Codex 先进入这个目录：

```bash
cd /Users/wenchuang/Documents/Codex/midscene-task-platform
```

## 必改文件归属

- 页面样式、交互、新手引导：`task-manager.html`
- 后端接口、AI 编排、Sonic/Midscene/Figma/报告：`midscene-upload.py`
- AI 生成质量和策略：`ai_skills/prompts/`、`ai_skills/references/`
- AI 输出结构约束：`ai_skills/schemas/`
- skills 回归检查：`ai_skills/evals/`
- 部署相关：`deploy/`
- 平台说明和设计沉淀：`docs/`

## 不建议再改的位置

```text
/Users/wenchuang/Documents/Codex/2026-05-08/new-chat
```

该目录只保留历史，不再作为主工程。

## 新能力落地顺序

1. 先在 `docs/` 写清业务流程和风险。
2. 如果是 AI 生成策略，先沉淀到 `ai_skills/references/` 或 `ai_skills/prompts/`。
3. 如果需要接口，再改 `midscene-upload.py`。
4. 如果需要页面入口，再改 `task-manager.html`。
5. 最后跑检查并用 `deploy/package-server.sh` 打包。

