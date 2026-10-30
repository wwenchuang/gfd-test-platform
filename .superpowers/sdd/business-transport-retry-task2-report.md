# Same-page independent-response guidance Task2 report

Requirements: Task2 and global constraints in `docs/superpowers/specs/2026-10-31-video-business-transport-retry.md` and matching plan. Start9cf01e44 after root's fresh Task1 SPEC PASS/QUALITY APPROVED. No new code, tests, schema, model routing, gate or source-specific answer changes. No agents, provider calls, source26, deployment, push, API/browser/device action.

## Prompt changes and semantic self-review

Only `ai_skills/prompts/ui_video_trajectory.observe.md` and `.audit.md` change. One shared paragraph clarifies that stable page_before/page_after identities and absence of a later new touch do not make a response automatic. Keyboard collapse/expand, toggle/select value and fold/unfold can respond to a distinct control interaction on the same page. Such a candidate requires control-associated visible touch or explicit pressed-control feedback, then temporally and target-related response; existing causal ownership and input grouping still apply.

The same paragraph forbids adding a click from keyboard disappearance alone, an unrelated gray circle or an unbound marker. Prior-action continuous automatic response stays automatic when supported; otherwise retain specific target/mechanism uncertainty. Physical press count is not inferred. The audit paragraph requires checking original pixels when automatic attribution conflicts with control feedback plus relevant same-page response; it forbids erasing recorded feedback or forcing a click in the other direction and preserves unclear/unresolved_evidence.

Semantic positive review (instruction logic only): a visibly associated keyboard control press followed by keyboard collapse while page identity stays unchanged remains an independent-interaction candidate; no subsequent new touch is needed to investigate that candidate. Original trigger ownership, target, time/response and independent audit still decide admission. This text does not assert that any actual source event exists.

Semantic negative review (instruction logic only): submission followed by automatic keyboard collapse without separate control feedback does not gain an invented keyboard click. An unrelated/ambiguous circle plus keyboard disappearance also fails to establish a click; continuous automatic causality or specific unresolved questions remain. Same-field IME/edit changes continue to use the original grouping rules. These are self-review of the conditional instructions, not model/pixel test verdicts.

All prior prompt text is retained verbatim. JSON examples, action/trigger kinds, continuation identity, input value withholding, owned/context boundaries, revisits, no_new_frames, terminal-frame predicates, uncertainty and physical-mechanism limits remain. No fixed source names, timestamps, strings, frame IDs, expected event counts, coordinate guards or answer labels added. No string-mirroring tests introduced.

## Verification

- `python3 -m pytest -q tests/test_ui_video_business_episode_contract.py tests/test_ui_video_navigation_contract.py tests/test_ui_video_continuation_guidance.py tests/test_ui_video_audit_batches.py tests/test_ui_video_business_runtime.py tests/test_platform_trajectory_skill_catalog.py` —137PASS/1.49seconds. Existing controls cover actual rendered JSON example/schema validity, payload/tool capability preservation, continuation/navigation/binding and full audit-shard semantics. They do not prove live model interpretation.
- `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py` —PASS.
- `python3 tests/backend_static_checks.py` —63checks PASS.
- `git diff --check` —PASS.

Root's live controlled Qwen positive/negative four-call probe is pending fresh setup review and execution. This task deliberately does not claim that check passed. Root owns actual receipt/proof/model/PNG hashes, semantic inspection, independent Task2/whole-release review and later full-source/normal-draft/SDK/designated-phone acceptance. Original24/25 failures remain preserved. No known prompt implementation blocker. STATE append remains dirty and excluded from commit.
