# 原视频重复生成：滚动边界与触点名称

## 线上复验状态

固定版本524c3e94，原触点视频及小白学习视频各计划10次独立上传。队列与完整响应：`/tmp/video-reliability-524c3e/`。本补丁准备时队列仍运行，不能当作完整10次成功率。

- 触点第2次 `uvs_e689743215c4499d8b2845fb69ccbd09`：服务日志确认frame099显式触点目标冲突。完整前四窗口重放捕获另外两次同类冲突：局部“查看生成记录”，整窗“我的作品区域上方的查看生成记录入口/文字链接”。补上方/下方限定，同帧唯一点击和单侧限定约束不变；两个捕获响应回放通过。服务器这一条的具体名称未保存，不能断言与重放文字完全相同。
- 小白学习第2次 `uvs_167bbbcb04cd46af89b37285998647fb`：日志确认修复越过保留交互边界。原响应重放连续两次失败。首轮刷新068/070，复核刷新069/072，名称“页面内容区域”和“页面列表”不同，旧代码截断复核窗口并把069当保留边界。修复仅扩展跨轮同参数、严格时间重叠的滚动证据，不合并或删动作；40帧/4调用上限保留。原数据重放经两次模型调用后通过覆盖检查，仍须最终规划核对方向。
- 触点第3次 `uvs_14dfc5af704045428df2f699d61f3ad6`：返回手势合并证据校验失败。相同原数据局部重放通过，尚未证明线上稳定，继续保留此失败。
- 小白学习第1次ready10，预期遗漏“单面”，终态混入“任务完成提示”。生成ready不能算语义通过。

## 验证边界

纯暂存快照511项视频回归、主链编译、后端63项检查、diff检查通过（含未提交实验补丁的工作树另有544项通过）。滚动扩窗经独立审查，方向不同、参数不同、时间仅相接不通过新增扩窗规则。

实验中的拖动误型修复仍未完成真实模型验收，未纳入本补丁。未执行真实手机/打印，局部重放不计入平台整片成功率。

## 返回手势日志定位

复合门禁拆为相同顺序的七个判定，只增加固定原因枚举，不改变校验条件。服务器日志白名单记录原因，不记录模型正文。新增测试先观察6项失败，再修复为115项相关测试通过；独立审查无阻断问题。

固定版本复验第4轮结束：触点累计2/4 ready，小白学习3/4 ready。小白学习第4轮已由平台导入草稿uid_3b174443af3bcbcb7bba528a68c59f3d，11个业务步骤、6项打印设置和任务传输终态齐全；刷新后稳定预期已对照frame082，严格静态校验通过。仍未进行手机执行。

## Result-quality follow-up

Platform learning-1 draft uid_b7dda149cd63eec3fb3a626073ede479 confirms missing duplex setting and reward completion text mixed into transmission expectation. The bounded result review now checks missing concrete setting categories and transmission/completion conflicts without changing actions or supplying values. Dynamic object and print recovery paths retain their dedicated gates.

Three local Qwen result-frame replays with old feedback all missed at least one of the two expectations. Three with explicit category and business-state feedback retained all six settings and transmission-only status. Evidence: /tmp/video-reliability/recheck-result-quality-v2.log and result-quality-{1,2,3}.json. These are local visual repairs, not whole-platform success-rate or device-execution results. Review findings concerning category-only answers and dropping transmission were fixed with negative tests; 100 scoped checks passed. This limited detector does not validate every value and does not replace manual source comparison.

## 追加的通用文字处理问题

小白学习第9次虽ready，平台实际草稿仍固定自动轮播促销广告，且“目标年级高亮选中”被按点击反馈过滤，退化为旧年级灰色检查，记录为内容不合格。新增规则保留持续的“高亮选中”，明确短暂/一帧仍拒绝；普通浏览结果混入轮播切换内容走原图复核，不改变关闭广告等业务结果。

独立结果重放捕获“份数显示为1”被当缺值，两个实际响应都含正确数值；补通用显示表达，要求仍有数字，不接受纯类别。捕获数据 /tmp/video-reliability/generic-outcome-capture-{1,2,3}.json。纯暂存583项视频回归、后端63、主链编译和独立190项审查通过。以上不计入平台整片成功率。
