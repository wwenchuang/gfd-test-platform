# 原片结构化轨迹与按需补帧隔离验证

状态：进行中，未切换生产 worker，原片内容及手机验收均未通过。

## 固定输入与权限

同来源 uvs_2c853ccbcb1d46c1a5b91e808f22fe49/v4、同75.376秒原片及299张选帧；原片SHA256370d92e2622686330d114ce14c67c50788a8e92e4aa1babbbd7e856e2796d893。固定人工基线只用于结果比较，不送入生产提示。仅指定手机ecbfd645和设备9888E0094F2A，已有归属明确测试任务有则取消、无则继续，不做最终确认物理打印。

## 实现

- 全片时间概览提供页面/输入/等待状态；传输包以真实触发帧确定动作所有权，输入续接通过稳定事件ID、字段/页面及独立边界事实验证。
- 每个原帧包均独立复核，空动作包也检查遗漏；每个输入原始片段分别核对触发及结果。未确认问题不能由后续规划清除。
- 新原片回看工具仅接受本包已知原帧端点及最长3秒区间，最多每次20张、累计64张新图、每次模型最多60张图。原图保持同来源解码器规范时间轴，不使用fps/setpts制造时间；新增帧唯一编号、唯一时间与唯一半开核心时间归属。
- 复用既有最终consolidate、输入值恢复及全部执行门禁；尚未改变worker默认旧解析器。新增trajectory模型传输仅精确skill/phase开放既有60图通道，其它入口不变。

## 真实模型第一轮

上传 /trajectory-shadow.zip 并解压 /acceptance-current/trajectory-shadow，实际运行 trajectory-shadow.py 1。脚本只读原来源，不修改来源/草稿/设备；请求及响应保存在 /acceptance-trajectory-v1/run-1。

完成9个观察包后独立复核失败audit_shape：模型把输入页面记忆复制到了本应为空的输出states。已保留原始响应，不剥掉字段冒充成功。下载的result.zip内嵌原证据ZIP，25份JSON保存外部new-touch/trajectory-v1/run-1/。

观察结果仍漏真实去打印、第二次下一步，并将多个真实返回写为aiWaitFor/uncertain；虽识别独立收键盘，不具备内容通过资格。未得到完整observer-result，更未生成正式用例。

## 原片缺帧的直接证据

原采样frame128为35967ms，frame129为37000ms，中间相隔1033ms。直接回看原片35.9–37.1秒可见36.63–36.83秒按钮持续触点，被原299张选帧漏掉。

实际补帧工具在35967–37000ms取得20张真实图，规范时间36000–36967ms；36667ms图清楚显示白色触点位于蓝色去打印按钮。修复前后均验证原片及旧帧不变。最终规范时间版本示例/tmp/trajectory-revisit-normalized-qwis1mc2/frames/frame-143.jpg。此证据证明需要按需回看，不等于模型整片已正确识别。

## 审查及回归

独立审查先后复现并修正：仅看已接受事件造成遗漏盲区、输入续接结果图丢失、上下文标签矛盾、规划清除审计问题、普通输入占位问题无法被安全恢复消除、补帧边界无主、非零视频起点导致画面错绑、新帧不能再次作为回看端点。

- 每项反例先红后绿；当前1013视频测试通过（18.63秒），63后端、46Gateway、两Node图像传输测试及主链/变更文件编译、diff检查通过。
- 非零起始时间实际FFmpeg反例：8秒视频偏移5秒；移除-copyts后与既有规范解码同5100ms画面对齐，避免时间戳看似合法但实际错绑。
- 最后独立复审无剩余P1/P2，仅批准提交和继续隔离实验，不代表生产或手机验收通过。

## 第二轮实际启动

新包trajectory-shadow-2.zip SHA2563a3717caf7bb945b1f8974b36c3ff65aed9cf0e830439ee60f893f8de1f4a85d，本机与实际终端输出一致；解压/acceptance-current/trajectory-shadow-2，实际启动trajectory-shadow.py 2。

第二轮在/acceptance-trajectory-v1/run-2/media/复制原图作为私有副本，原片仍只读；新增图写入此副本。模型仍通过线上既有whole-observe图片通道传输，但使用新trajectory提示和独立schema，脚本明确映射两者，不编辑模型结果。旧通道上下文标签使用可引用原图，提示中恢复正确的trajectory核心/上下文归属；后续生产仍须新skill通道真实部署复验。

当前等待完整模型结果及固定基线逐项检查；未记为成功。若模型请求已处理区间，明确失败而不生成无主触发证据。至少3次完整解析满足全部硬项，才具备正常平台接入的最低资格。


## 第二轮失败及正式通道部署

- run-2第一个包在两次共享纠正后仍trigger_ownership失败，实际04-observe-result触发依次009/027/048，048属于后续参考图。保留失败，未裁掉越界输出。兼容实验通过旧通道，逐图标签与新协议不同，故不能用它判断新正式通道结果。
- b041dba1已提交并推送当前分支与main。运行包ZIP SHA2563655857265a93d2954d4e0c44469e765138fb7eec5b2b161185e9256f8208c40，bundle SHA2566a07a90d899d19d57fea1c52a468cc165e9c8cf4a9097fcc562eb9d468b8c868；实际上传校验一致，ff-only及既有部署脚本完成，Gateway在线。
- 17:55:26公网http://101.34.197.12:8088/api/health为ok且完整revision b041dba1f2d4d4e0c713f1fca2a5fa2df10a5e3c。证据new-touch/current-platform/deploy-b041dba1.json，实际终端截图deploy-b041dba1-terminal.png。生产worker默认仍旧入口，尚未自动启用新观察器。
- 通过正式ui_video_trajectory通道运行trajectory-live.py 3，概览和第一包观察完成，首包观察经一次纠正后进入独立复核，04-audit因audit_shape拒绝，05-audit调用RuntimeError/model-service停止。未将其误判动作错误；/acceptance-trajectory-v1/run-3保留阶段请求和已完成响应。
- 用户明确本任务全部使用千问。已取回run-1所有实际runtime模型均为provider qwen_plus / model qwen3.8-max / fallbackUsed false；无Gemini等厂商调用证据。新定向诊断脚本先断言全部配置路由及fallback为qwen，再单独重放run-3实际05-audit-request，不修改原来源/草稿/设备，同一05请求定向重放实际返回qwen_plus/qwen3.8-max/fallbackUsed=false；不能反推原RuntimeError是合同错误。

## 第三轮定向诊断与复核合同修正

- 只读脚本实际执行：04输出states2/events2/checks2；定向05重放states/events空，但第1check引用008（原片段允许009/010/011），第2check有9张引用且034/037不在片段允许集合；validate_checks首先拒绝audit_evidence。未裁掉引用冒充通过。
- 实际所有Gateway路由及fallback先断言为qwen，原样05重放返回qwen_plus/qwen3.8-max/fallbackUsed=false，用户全部千问要求已经核对。服务异常未复现，独立保留原失败。
- 修正仅明确每个审计片段required/allowed帧、去除重复event副本，并将完整check合同校验纳入既有共享2次纠正；审计仍检查全部原图，服务RuntimeError不作为结构错误重试，24调用及1200秒上限保留。独立复审无新增P1/P2，39项独立测试及350相关回归通过；父任务最终58项、63后端、主链编译/diff通过。

- 第四轮隔离包trajectory-audit-4.zip SHA2568c8ec3bc872065194de6dde52d53ca6698d165fecdecf01d4ea7334b7f2271e7实际上传校验一致；终端解压目录实际为/acceptance-current/trajectory-aud。运行trajectory-live.py 4，当前概览完成进入observing0/9。使用正式ui_video_trajectory通道，私有覆盖当前候选模块/提示/schema，无旧transport映射；每次返回断言provider/model均qwen。结果仅写run-4私有目录，finally打包run2/3/4 JSON为evidence-4.zip。

## 第四轮失败后的职责修正与第五轮

- run-4完成前两个包，第三包08/09审计均将要求的111触发/112结果抄成110/111，audit_evidence失败；05复制states还消耗过一次纠正。完整run2/3/4原始JSON已原生下载并解压外部new-touch/trajectory-v1/，没有修改模型返回。
- 另直接发现两处流程根因：稀疏overview疑问被当永久阻断；packet1把context050搜索提交报遗漏，但packet2实际已负责记录050。不能靠累积自然语言疑问判断最终完整性。
- 新trajectory-v2审计严格阶段输出checks/issues/multiple_scenarios/revisit。模型分别判断不可变片段触发/结果为supported、unclear或contradicted，不抄帧ID；平台只从原片段绑定双锚，并明确reference_binding=platform_immutable_fragment，原始verdict保留。不是把旧审计升级为新判断，也不是平台预填confirmed。
- issues必须含kind、owned owner_frame、实际frame_refs、具体question；context只能提供结果背景，不能作为本包新动作归属。overview_hypotheses只供完整原图后续核查，不自动进入最终阻断；观察者真实问题和独立审计合法未决问题仍保护。
- 新反例先红后绿；368相关回归、父任务70项及全部1042视频回归18.82秒通过，63后端、主链/变更编译、diff通过。独立70项复审无P1/P2；补充了结果属于当前操作、不能借后续结果或把等待当完成。
- trajectory-verdict-5.zip SHA256ccaf0c1e94390ca0c606c7867d81c37ee54e6806f3b44b1a890a508825c61299已实际上传核验，解压/tmp/verdict5，运行trajectory-live.py 5。当前已完成前两个包进入2/9；仍为私有诊断，没有改生产worker默认入口。
- 实际线上模型配置页六项能力均显示Qwen3.8Max；此前实际全部Gateway路由及fallback均qwen，真实各轮runtime也为qwen_plus/qwen3.8-max/fallbackUsed=false。页面证据new-touch/current-platform/qwen-only-model-policy.png。指定手机ecbfd645与Runner18:34在线ready；快照时间仍旧，未将其当新页面验收。

## 第五轮实际终态及机械引用限制

- run-5前两个包新audit均双supported、issues空，未发生旧审计复制和未来context误报。第三包观察06/07/08输入引用分别11/10/10张真实、唯一、有序、当前已提供原帧，含081/trigger082/110，因原max8连续拒绝，终态frame_reference。完整原始JSON已下载new-touch/trajectory-v1/run-5。这不代表后续触发/结果已视觉通过。
- 新分层编译候选保留raw动作/结果各最多本包60张，先完整校验，再从原集合确定性选择最多8张供普通事件，保留事件首末与触发、完整raw/private审计及派生来源。原始错误不能被压缩隐藏。独立84项通过，run5保存响应绑定层11/10/10→8通过，原返回不变。
- 独立复核发现旧observer允许raw outcome不早于trigger，而final validate_analysis要求ordinary outcome不早于event.end；实际搜索证据048/050/052/054+outcome052/054触发跨层冲突。因此尚未启动run6，先统一原始响应区间与最终可见结果锚点的编译含义，不改legacy最终门禁。
- 原图人工只读复核：110(27167ms)键盘工具栏右侧收起控件有白色触点且键盘展开；111(27367ms)首次收起、仅淡残迹；112(27400ms)稳定结果。run5中trigger110/111的变化必须由新实际审计判断，不能把离线引用编译通过算内容正确。
- 19:01实际手机只读截图：应用1.19.71(52)、4F2A在线空闲/25℃，页面有“刀刃”剩余3盘待打印（含连杆/指关节）。这不是原片姓名牌任务，尚无可取消归属证明；不操作该任务。证据phone-4f2a-20261027-1901.png。未下发本轮新用例。

## 原始证据与普通终态的编译合同（第六轮候选）

- raw事件及结果各最多60引用，全部先做真实已提供/唯一/时序/归属校验；raw outcome不得早于trigger，且末帧不得早于raw事件末引用。真实错误不能通过选图隐藏。
- compiled事件从raw确定性保首末/触发及时间跨度，最多8；普通outcome只使用raw最后可见结果帧，来源terminal_outcome_anchor，描述/动作/verdict不改。raw响应过程、每段输入片段和审计原图完整保留，private reference_compilation可追溯。最终legacy validator和普通8帧限制未改。
- 实际保存run5的02→04→06、02→04→07、02→04→08三组均完成3包绑定并final validate_analysis通过，每组8事件、action最大8/outcome最大1。不是视觉内容通过。独立15项编译测试及复审无P1/P2，383相关范围、全1057视频18.67秒、63后端、主链/变更编译、Node传输/diff通过。
- 第六轮原上传遇到T_1006后明确会话过期；用户回复好了后，新会话实际回显可用，旧路径确实没有包。重新上传/trajectory-reference-6.zip SHA256333e5084f9d689e2ebda9e1e8ca69df7eb78efda8eefbe8f5684f2993e3515e0，本地/终端一致；实际解压/tmp/reference6并启动trajectory-live.py 6。尚未记为成功。
