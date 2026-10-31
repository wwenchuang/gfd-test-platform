# Whole original run31: failed planning, separate semantic gaps

This is a failed acceptance record, not a successful generated case or phone result.

The QA service actually deployed `689b679edaced4aad2419de305e24e22aeaad5a1`. A normal upload of the unchanged 75.376-second original created source `uvs_4763262bb5fb4e7f9d4485e32bebe03b`; its actual analysis version was **2**. “run31” labels the acceptance evidence directory, not this source's version. The original MP4 SHA256 remains `370d92e2622686330d114ce14c67c50788a8e92e4aa1babbbd7e856e2796d893`.

All nine observation packets completed. At 17:41:21 CST on 2026-10-31, the normal workflow reported `failed`, `video_action_invalid`, planning 1/1, 354 frames, zero final events and no draft. It reserved 32 of the existing 38 provider attempts and persisted 31 receipts. One earlier grounding attempt reached its local deadline without a receipt; the ordinary isolated retry succeeded, and the unknown attempt remained charged. All 32 owned attempts reached terminal cleanup; both owners reported no cleanup failures. All persisted replies used Qwen with no fallback.

## Evidence integrity and reproducibility

The normal read-only helper exported the terminal source and model workspace to a new `/acceptance-normal-video/run31`. All 354 original JPEGs were copied into that independent evidence directory. No source history, previous draft or phone execution was edited.

- Server inner ZIP SHA256: `4b59c6a74e910088844f106e3cd4b7082790a1e205b1301fec66afc77de48ddb`.
- Native download wrapper SHA256: `3dabf3cc671e8658a2e3b75bdc04ecb533fafe5a51a6bb75423ef16ab0e8d618`.
- Download: 368 files, 13,613,802 bytes; both ZIP CRC checks and safe-path checks passed.
- Source JSON SHA256: `3f3dae3003dfae553ea11023a96d75da476e8ffe83b5f9722dfc3b36d28e84b9`.
- Root terminal inspection SHA256: `00972c8fccfb089e1a0fd2bc727cbe90d8f0f4d0a800e743e03a8e95704884ab`: six workspace manifests, 96 canonical records, 217 record references and all 354 JPEGs verified. Grounding/audit policy budgets were 2048; other captured stages remained 1024.
- Zero-provider trajectory reconstruction SHA256: `2c3f9b69206bb155f766092b422a40011c67eee3cf8a1ac5290deb705f3948ac`: 27 normal engine calls, 28 captured attempts including the transparent captured transport retry, and four original acquisitions. All actual output-schema hashes, image counts and supplied JPEG bytes matched. The resulting 41 temporary events exactly matched the server's `raw_events`.

The offline reconstruction consumes historical replies as fixtures. It does not recreate dynamic Gateway request/correlation bytes and does not prove fresh provider performance.

## Direct planning failure

Captured planning receipts 57/59 and 61/63 both produce four targets such as `打印准备页左上角返回箭头(原片为右滑返回手势)`. The unchanged gesture-target gate rejects these targets because they include a gesture description. Both captured passes reproduce the same `video_action_invalid` in `_bind_candidate_evidence`.

The existing correction received generic validation guidance rather than a specific explanation of the target/observation separation. Commit `97592a71a92541adea2d8c29f3b1c87227e3bf1d` adds fixed safe feedback at the same rejection and makes that separation explicit in the consolidate prompt. It preserves the rejection, original observations, candidate ownership and correction limits. Its evidence-binding tests went from six expected failures to 57 passes; 511 adjacent tests and mandatory compilation/backend/diff checks passed. Independent task review returned SPEC PASS and QUALITY Approved. The commit was pushed to the backup development branch; **it was not yet deployed at this checkpoint**.

A separate local what-if diagnostic moved only those annotations out of targets in detached copies. The next existing gate rejected result-frame references in both passes. This is evidence that real downstream qualification must cover normal outcome repair too. No modified copy was imported or accepted as a model result.

## Semantic qualification remains failed

The full source still contains an invented extra Next around 54.567–56.367 seconds after the genuine material confirmation, and misses the independent blank-area keyboard dismissal around frame282. The actual packet007 audit received the prior confirmation and original causal context. Packet009 observer and audit received frame282, and the schema allowed it as a missing-action owner. The audit retained unresolved questions while supporting the incorrect causal classifications. This is not missing image delivery or a successful automatic interpretation.

The earlier successful bounded D-R3 test used a different context distribution: the Next case had additional dense frames; the keyboard case had identical pixels but inherited correction feedback and different upstream context. That result qualified only that bounded test. It did not establish whole-source reliability, and another budget increase is not supported by this evidence alone.

A bounded independent causal-question recheck is being designed and preflighted. It must use structural eligibility, original evidence and unverified questions without seeded answers, preserve all earlier evidence and locks, and keep every actual provider attempt charged. A successful isolated recheck would still require normal whole-source generation, draft review/static/SDK checks and a new designated-phone trial before acceptance.

The previous manually reviewed r7 phone result remains a separate historical result. It cannot be reused as run31 acceptance. No new phone trial, final confirmation to print or physical print occurred in this failed run.
