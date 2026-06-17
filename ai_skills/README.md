# AI Skills

This directory keeps AI analysis and repair capabilities versioned outside the
server code. The first implementation keeps the existing Python orchestration,
but gives each skill an explicit contract so prompts can be improved and tested
without turning `midscene-upload.py` into one giant prompt file.

## Skill Contract

Each skill should have:

- `prompts/<skill>.v1.md`: prompt template or operator guide.
- `schemas/<skill>.schema.json`: required output shape.
- `evals/fixtures/*.json`: small cases that protect behavior during prompt changes.
- `evals/run_skill_evals.py`: local regression checks for prompt/schema contracts,
  with optional live model runs.
- `references/yaml_style_guide.md`: local Midscene YAML conventions distilled
  from existing Task baseline files.
- `references/midscene_executable_yaml_guide.md`: executable YAML guardrails
  based on Midscene runner docs, Android execution rules, and local Sonic
  baseline failures.
- `references/prompt_center_business_context.md`: shared Prompt Center rules.
  Skills should honor `payload.businessContext` and
  `payload.promptCenter.businessContext` when present, so generation, visual
  grounding, repair and execution decisions stay tied to the same business
  chain.

The backend should call skills through `run_ai_skill()` when a skill has been
fully extracted. Existing functions can continue to use the current prompts and
gradually migrate one skill at a time.

## Design References

- ISTQB black-box test techniques:
  https://astqb.org/4-2-black-box-test-techniques/
  Equivalence partitioning, boundary value analysis, decision table testing,
  and state transition testing are used by `scenario_designer`.
- OpenAI prompt guidance:
  https://platform.openai.com/docs/guides/prompt-engineering
  Complex tasks are split into sub-steps with clear output contracts.
- Qwen structured output:
  https://docs.qwencloud.com/developer-guides/text-generation/structured-output
  The backend asks Qwen for JSON objects and validates them through schemas.
- Midscene YAML runner:
  https://midscenejs.com/yaml-script-runner
  Model output stays as structured test intent first; backend code converts
  stable cases into `tasks -> flow` YAML.
- Midscene Android automation:
  https://midscenejs.com/automate-with-android
  Generated YAML must include stable launch, visible UI targets, waits,
  assertions, and cleanup.
- Platform rule: Task owns assets and lifecycle, Qwen owns analysis, Midscene
  owns real execution, Sonic owns stable baseline regression.

## Running Evals

Contract evals do not call Qwen and should be run after every prompt/schema
change:

```bash
python3 ai_skills/evals/run_skill_evals.py
```

Live evals call the configured model through the same backend skill pipeline.
Run them before changing models, upgrading prompts, or deploying a major AI
generation change:

```bash
set -a
. /opt/midscene.env
set +a
cd /opt/midscene-task-platform
python3 ai_skills/evals/run_skill_evals.py --live
```

The current fixture set protects three common platform scenarios:

- `mobile_print_record_generation`: UI稿不完整时保留缺口和人工清单。
- `consumables_mall_checkout_generation`: 支付、地址、优惠券等强数据依赖不能硬塞进自动化。
- `nameplate_print_boundary_generation`: 输入长度、空输入、特殊字符、返回中断和打印状态需要边界/异常覆盖。
