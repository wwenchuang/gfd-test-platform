# 测试设计 Prompt 替换验证 2026-09-28

## 范围与适配

按用户粘贴文本替换测试用例设计规范。原文两个text块已与归档逐字内容比较（忽略首尾空白）一致。固定Prompt SHA256：981260d73c2957c6478e69e0d9fc2c3d6c9c333b050ba90724fa7fd47592eb4d；输入模板SHA256：b37b5caf30c3da904ae410973c17247f68d90006eb59e1c37beb2c308698a020。

运行时保留证据边界、完整覆盖、P0/P1、未执行、冲突待确认和输入字段，按现有分阶段JSON接口适配原文MM交付部分。平台序列化MM，不要求模型在JSON响应中混入XML。没有添加DOCX/Figma/视频读取能力，不把文件路径或链接当作已解析材料。YAML/Runner专用提示词和执行门禁未改。

## 验证

- 新测试初始9失败1通过，证明缺失共享规则、输入字段、导出字段和优先级约束。
- 独立审查发现5项接入缺口；新增回归均先失败，再修复：人工步骤截断、风险P2、覆盖补全遗漏、视觉来源丢失、legacy全量/增量合同冲突。
- 最终pytest：test_test_design_prompt.py、test_mindmap_manual_only.py、test_agent_requirement_boundaries.py、test_case_business.py、test_mindmap_test_report_service.py，81 passed。
- backend_static_checks.py：63 checks，ok；PromptCenter、agent_service、ai_skill_service、case_service、yaml_service、yaml_executable_scorer编译通过；git diff --check通过。
- MM测试通过XML解析与节点内容核对，覆盖特殊字符转义、14步完整保留、11条预期、前置条件、文档/视频引用和未执行状态。
- 独立复审五项问题闭环，复审单独运行15项新测试通过。

## 验收边界与发布

本地模型桩验证实际Skill调用携带新Prompt和现有JSON解析合同；没有调用真实模型，不代表真实材料的语义覆盖已验收。未做Xmind/MindManager桌面导入。没有生产设备操作、测试账号或测试资产需删除。没有增加模型调用次数，但更长提示词有token/时延成本，未做性能专项。

本次从3480376建立隔离分支codex/test-design-prompt-20260928，未改用户主工作区。发布通过既有堡垒机运行：

```bash
cd /opt/midscene-task-platform-src
bash deploy/update-main-server.sh
```

堡垒机终端登录恢复尚待完成，本次未部署；部署后仍需以实际需求材料进行线上生成和MM下载验收。
