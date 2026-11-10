---
name: test-authoring
description: 将现有已审核且能绑定到真实回放的 API 用例整理为受控 Python 候选，并验证业务断言。
---

# 从已审核用例生成离线候选

使用平台既有的 JSON 用例、不可变版本、实际执行记录和响应断言。该流程只读取冻结材料。已有基线、已调试通过、模型自评、用户暂缓审核都不能充当已有人工结论。

1. 将从平台获取的标准用例、来源对应表和脱敏回放置于私有输入目录，分别命名为 `cases.json`（cases 数组）、`provenance.json`（cases 数组）与 `replay.json`（fixtures 数组）。三份数据按 ID 对齐。用例的 source 包含平台不可变版本 ID，来源、回放必须具有相同版本、运行编号及子执行编号。来源表的 version_assertions 保留平台不可变版本的完整已开启断言；只有返回体投影断言与它们全部匹配，才可生成正式候选。另需从同一版本记录 enabled_step_assertion_count；存在前置或清理步骤断言时，该单响应回放器不具备覆盖它们的能力。回放只含受控 HTTP 方法、操作路径、HTTP 状态、必要字段投影和已冻结的响应断言。保留投影只读来源，去掉认证头、请求参数、设备及用户标识。
2. 若有现成人审，从相应可信系统的记录只读导出为 `reviews.json` 的 reviews 数组，保留事件编号、原始记录引用、评审人、时间、准确版本和决策。输入转换示例为 `{"record_type":"case_review","case_id":"<用例ID>","case_version_id":"<不可变版本ID>","event_id":"<原事件ID>","source_ref":"<原记录链接或档案定位>","reviewer":"<记录中的审核人>","reviewed_at":"<含时区的原时间>","decision":"approved"}`。若原系统记录内容摘要，可一并保留 case_version_sha256。保留已撤回、拒绝等后续事件；最新决定生效。切勿将示例复制成业务审批。CLI 核对导出与版本的绑定，导出者需能指回真实事件。缺少这份记录时直接省略参数，仍可做准入审计。
3. 在项目根运行审计，将下面路径换成实际私有目录。
   ```bash
   python3 -m scripts.candidate_replay audit --source /private/case-snapshot --output /private/admission-001
   ```
   已有评审时加 `--review-records /private/reviews.json`。查看 report.json 的 eligible_count、offline_controls 和各项 reasons。即使未提供评审，历史响应仍可按现有断言做离线正反检查。
4. 有合格输入后，以独立工作分支和**新输出目录**运行。无需联网访问业务系统；生成阶段仅调用服务器配置的模型端点。提交模型的内容限于用例编号、标题、已确认接口及脱敏断言描述。模型只返回用例编号、冻结的路径/方法及断言编号排序；严禁采纳新接口、自由代码或删减断言。技能流程在此文件，提示词和执行适配在 `scripts/candidate_replay.py`，断言实现复用 `task_server/api_testing/assertions.py`。
   ```bash
   python3 -m scripts.candidate_replay generate --source /private/case-snapshot --review-records /private/reviews.json --output /private/candidates-001
   python3 -m scripts.candidate_replay verify --source /private/case-snapshot --review-records /private/reviews.json --output /private/candidates-001
   ```
5. 每次最多生成十条。正式脚本在临时目录编译，使用 unittest 收集，在隔离 Python 子进程逐条执行。受控模板仅可调用已绑定的回放检查；沙箱清除继承的应用凭据，拒绝网络、外部进程及进一步打开文件。正控验证真实脱敏响应；负控保留 HTTP 状态及断言，改变业务响应体。当前支持状态码相等及明确 JSON 路径的 equals、exists、not_exists；遇到其他断言时整条拒收。任何生成、收集、业务判断异常均不写入候选目录；既有基线和候选不会被覆盖。
6. 交付前用 `python3 -m unittest discover -s tests -p 'test_candidate_replay.py'` 核对坏样本与回退。report.json 记录独立回放数、合格数和实际候选数；generate 不满十条时退出 1。人工审查断言和准入仍在原有审核流程中进行。候选不自动执行生产请求或写入基线。
