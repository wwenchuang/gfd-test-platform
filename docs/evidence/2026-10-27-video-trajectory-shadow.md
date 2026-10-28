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

## 第六轮完整返回但内容验收不通过

19:26:21 run6完成全部9包，27事件/27审计决定/21调用，315帧，顶层questions空、multiple_scenarios=false；16张补帧仅用于198→200第二次下一步。完整JSON已下载new-touch/trajectory-v1/run-6。首次完整返回不能计为资格成功。

独立原图审核确认三个硬失败：去打印用128(35967ms)输入框右侧触点倒推，raw自己已有位置不符疑问，audit仍双supported；110→111独立收键盘被吞入输入；282→283返回搜索后空白收键盘完全遗漏，audit无issue。147(42967ms)起已在下一步上出现触点，run6却把触发定150(44833ms)，仍需42.9–44.9秒密帧判定按压生命周期。两次下一步/两次取消/最终首页/无最终物理确认在结构上保留，不抵消上述失败。人工对照保存manual-quality-review.json，未下发手机。

Gateway现对所有qwen3.5/6/7/8 JSON请求enable_thinking=false。官方结构化输出及深度思考文档列Qwen3.8-Max可思考JSON Object，但目前没有证据证明关闭思考就是本轮根因。只做同Qwen配置/同原图/同提示的开启思考隔离对照，先选packet4和9，enable_thinking=true/thinking_budget4096，不改生产、不更换厂商，不保存或打印reasoning_content。脚本trajectory-thinking-probe.py已上传/acceptance-trajectory-v1并实际启动，等待结果。

## 同千问思考模式对照与有界能力候选

- 首个脚本未找到Gateway专用环境变量时assert停止，尚未调用模型；修正只读取既有provider声明的同一个key。44图/4096思考预算的实际第一调用120秒TimeoutError，无结果，保留thinking-probes-v1，不把它解释成识别成败。
- focused-probes-v1对三组相同6图/相同提示/同qwen3.8-max分别false与true1024，全部真实返回：direct18.92/4.56/5.74秒，thinking37.22/32.43/37.54秒，真实usage均reasoning_tokens1024。print direct仍用128倒推去打印；thinking分清输入行触点并请求35967→37000回看。late direct误认键盘搜索，thinking正确识别空白收键盘。early thinking仍未确认触发并请求27167→27367回看。只是局部对照，不是整片通过。
- thinking-probes-v2使用正式观察提示、原完整packet4/9/3与1024思考预算，81.69/61.15/49.22秒返回，usage均1024。packet3正确分别聚焦/完整输入/110收键盘；packet9新增收键盘但目标写工具栏箭头而原点在空白；packet4仍用128推断去打印并保位置疑问，无revisit。因此不能仅开启思考便宣称整片问题解决。接着用同图审计对照检查候选因果叙述是否导致确认偏差。
- 新bounded能力仅exact ui_video_trajectory/whole-trajectory/reasoning_mode=bounded/JSON及官方DashScope精确qwen3.8-max或flash启用固定1024，其余Gateway请求不变。直连内部专用flag同限制；图片/调用/纠正/120秒/1200秒门禁不变，无思考文本读取。private audit只记录4项有限非负usage及requested模式，实际reasoningTokens>0才证明执行，缺失不造0。
- 402相关回归、父全1072视频19.44秒、63后端/46Gateway/7Node/主链编译/JS语法/diff通过，独立15Python/6Node及复审无新增P1/P2。候选尚未部署，生产仍旧observer。
- 官方依据：[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking)。官方支持不等于本原片已合格，效果仍以实际对照及正常链路验收为准。

## 独立审计去因果叙述对照

同44图/同Qwen3.8Max/同1024思考/同audit提示：full-narrative29.95秒，print trigger=supported/outcome=unclear，并提出128触点错位issue但不回看；claim-only29.40秒，trigger=unclear/outcome=supported，主动请求127→129核实128触点及漏按，原始结果均保留new-touch/trajectory-v1/audit-thinking-probes-v1。不是把原模型判断改写成新判断。

claim-only输入只在深拷贝中移除当前track.observation、audit prior.event.observation、audit prior.fragments[].observation；结果主张、疑问、身份边界与全部原图保留，observe历史和private原始记录不变。private audit_input_mode明确标记。真实补图后的动作/目标及审计仍需重新验证，不因出现revisit就计通过。

新两项反例先红后绿，406相关范围、父全1074视频19.66秒、63后端/编译/diff通过，独立48项及复审无P1/P2。有界能力16681f61已提交推送；claim-only候选继续提交，用于正式Gateway新诊断，不切换生产worker默认。

## 第七轮实际终态与输入续接修正

- 2da79646994c527b8b285538073c835af1481f72 已通过既有部署脚本实际部署；20:45:32 公网健康精确 revision 一致。安装 Gateway 文件 SHA256 为 b8ba21587e57fe00f189801cfde37c62c416e13fef54e9e3237e0464a3420064，与本地一致。生产 worker 默认入口仍未切换。
- 第七轮使用部署后的真实模块与 Gateway，无提示或模块私有替换；后台 PID3138436，20:46:57 启动，20:53:04 终止。首包回看018→027新增20张原帧；第二包连续三次返回均在 input_continuation 被拒绝，仅完成1/9包。完整请求、响应、run-meta、失败与补帧记录通过原生堡垒机下载，保留在外部 new-touch/trajectory-v1/run-7，没有修改模型回答。
- 05/06/07 响应的同字段 target、页面、canonical ID、相邻时间和无独立边界均符合续接；拒绝来自 prior“本包末编辑未结束”和新片段“候选按键未采样”等疑问。候选修正仅移除疑问存在即拒续接的条件，保留原有全部结构身份/提交/导航/字段/时间/中间操作门禁；canonical 问题取前后稳定去重并集，原始 fragments 不变，后续结果或 audit supported 都不自动清除疑问。累计问题超限仍由原最终校验拒绝。
- Gateway HTTP usage 对已归一化摘要再次归一化时丢失顶层 reasoningTokens，导致 run7 七次显示0。已用 provider嵌套1024→第一次1024→HTTP0 的红测试证明；候选兼容自身顶层摘要并保留嵌套真实零值。此为回传观测错误，不能据此断言 run7 未执行思考；请求 reasoning_mode 没有被删除，真实原始日志用量待只读导出。
- 下一批同时准备版本工作目录与私有字段边界：只允许服务器构造 analysis-vN/frames，严格正整数版本、无效marker不回退、旧来源无marker兼容；帧HTTP保留owner/app与成员校验，支持可选版本拒绝过期，拒符号链接；公开响应剔除私有审计与路径，retry清私有索引但保留旧证据目录。helper不负责数据库CAS，未来worker必须显式使用expected_version发布；当前未接线新worker。
- skill目录健康检查识别trajectory实际overview/observe/audit三份提示，缺任一仍不ready，不新增v1别名；ready仅表示目录配套完整。
- 本批父任务1112项视频及catalog测试20.69秒通过、后端63/Gateway46/Node10/主链及改动编译/JS语法/diff通过；独立metadata7项、workspace18项及续接/usage60Python+2Node复审批准。本轮不是整片合格，没有正式新草稿或新手机下发，固定设备与不确认物理打印约束不变。

## 第八轮已部署启动与实际思考计数核实

41c5d8ce510feb146a94e9665102ff8b35d978f5已提交推送分支和main，并通过原生堡垒机运行部署脚本。ZIP SHA25637597a8119bde542d4028da68e56e4b6740456a3733d80dc7819efe2a2445dc7，上传后实际sha256sum一致；终端显示部署完成41c5d8ce，公网health完整revision一致。新worker尚未启用。后台launch-trajectory.py 8实际启动PID3158115，私有日志/acceptance-trajectory-v1/run-8.log，待完整终态。

只读导出Gateway原始日志，仅保留id/time/action/providerId/model/success/usage/durationMs/fallbackIndex，取得2026-10-27T12:48:32.051Z至12:53:04.029Z七行，恰与run7七次调用顺序和prompt/completion计数一致。每行providerId=qwen_plus、model=qwen3.8-max、success=true、fallbackIndex=0、usage.reasoningTokens=1024。由此确认run7实际已执行固定1024思考，HTTP投影0是观测错误；不再把run7视为未启用思考。原始白名单JSON与ZIP在服务器/acceptance-trajectory-v1/run7-gateway-usage.json及同名zip，实际终端截图保留在外部current-platform/run7-original-usage.png。这不改变run7的input_continuation失败终态。

## 第八轮空补帧终态与焦点对照未合格

- run8第一包audit正确将016键盘升起的点击主张判trigger unclear/outcome supported，请求015(1633ms)→017(1700ms)回看；016(1667ms)已经提供。实际loader完整解码该区间返回空新增列表，observer却要求至少一张而revisit_result终止。完整JSON及run7原始用量白名单通过原生下载到外部run-8和run-7；三次HTTP用量均已正确显示1024。不能将0/9失败记为整片通过。
- 最小候选区分成功解码但无新增帧与真实解码错误：空尝试仍消费4次预算，私有ledger保存原请求/时间范围/no_new_frames，下一次observe/audit同组原图接收耗尽区间反馈；同区间不重复解码或无限循环，原请求问题保留人工blocker，不制造图或自动确认。实际新增图片时的问题生命周期保持原样。重复耗尽请求即使revisit仍非空，最终checks与issues也完整校验，三个恶意issue反例先红后绿。
- 1122项视频及catalog测试19.84秒、后端63/主链编译/diff通过；独立100项及复审批准继续隔离诊断，无P1/P2。正式worker及手机验收仍未启用。
- 四次同Qwen3.8Max/1024/同图同payload的focus-evidence-probes-v1真实对照已下载。负例58图：原audit仍支持013触发但拒绝结果描述，候选拒绝013触发并请求012→014。正例44图：原audit三项supported，候选拒绝081的touch主张并误报094输入法切换、101候选上屏为两项missing_action，违反同轮输入颗粒度。全部原始结果/模板/图片SHA保留，候选提示尚未提交部署，不能称已改善稳定性。
- 正例实际来自thinking-probes-v2/packet-003-result重建，包含聚焦081/连续输入/收键盘三项；各对照合同及图像fingerprints匹配。外部local-decode JPEG哈希不同于服务器实际wire图，需取得实际发送JPEG后再判断细小触点的模型误认，不能仅用本机重新解码图作像素一致证明。
- 21:10–21:11只读Runner审计：mac-android-adouceshi心跳在线、PID43512、仅ecbfd645注册ready，CLI/core/android/shared均1.13.0，实时模型及心跳caps均qwen3.8-max/family=qwen3、服务host均官方DashScope。设备快照仍10/23旧值，不作为当前页面或新执行验收。

## 第九轮越界回看与有界请求纠正

- befbc6d857c44218129f5bd22ecf04ed2b3ecddc已实际部署，ZIP SHA dfd9ac6c56f9e6485c6114382874515832e6e97b1e21c2652f948e1578bb93b4与远端一致，终端及公网health完整revision一致。launch-trajectory.py9后台PID3181904，21:32:44第一包请求047→053越过owned区间[0,11867]，立即revisit_outside_owned_interval终止0/9。完整JSON已原生下载new-touch/trajectory-v1/run-9；没有成功整片。
- 回看工具提示此前未清楚列出owned范围限制。候选仅在observe/audit提示补齐端点/区间/后包归属及no_new规则；失败的focus定义候选已经存入外部parked-focus-candidate并从仓库工作文件恢复，不随本批部署。
- 所有模型回看请求现在先在ask内整体验证，合法后才调用decoder；未知帧、非法shape/question/owned跨度使用既有全局共享两次纠正，同组原图和原owner不变，无夹取、改派或静默删请求。真实4次资源/容量/服务/解码/取消/非法loader仍硬失败。audit现同样显式收到owned_time_range_ms。右端点可为end作边界夹帧，但其ownership保持半开区间、不移交context。
- 修正中独立发现_envelope因revisit_shape提前失败会留下未绑定raw；先红后绿处理为每次ask循环raw=None、仅dict才记请求摘要，避免旧轮raw污染。85专项、父1138视频及catalog19.74秒、后端63/主链编译/diff通过；独立116及复审无P1/P2，批准继续真实诊断，未声称内容正确。

## 实际六图结果与后续分离实验

两次六图仍未解决：actual wire079/080/081均有灰色圆点、082淡出，但千问仍说没有触点；081也不是已见圆点最早起点，不能把supported作为实验成功目标。负例末帧018/300只见5条热搜，原主张8条仍被错误supported。12张实际发送JPEG全部与请求SHA一致，已下载外部focus-six-images-v1，消除了本机重解码哈希不同的证据疑问。

后续两项独立实验通过同批ZIP上传，SHA d268896b65b4d4275ea6aae6102821e653b1ac963de63a925aab55f05d312d3d实际核对一致。第一项同44图同claims仅增加共享输入粒度说明，观察是否去除094/101假遗漏且保留110独立边界；第二项同6原图同claims同提示仅增加两个父帧的固定四象限2倍局部图，所有原图保留，绑定父帧与原时间，无标注/答案/人工ROI，不作新事件。各2次同Qwen3.8Max/1024/120秒，顺序后台运行，原始结果及实际wire/派生图哈希均保留；不修改生产Gateway图片合同或默认worker。

局部放大参照千问官方[工具列表](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/features/)与[Qwen3-VL放大工具实现](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/tools/image_zoom_in_qwen3vl.py)。本实验固定四象限，未冒充官方模型选择bbox流程；官方支持该方法不代表本来源已通过。

## 第十轮实际终态与预算限制

f7f8a426213e80ff2b0ff5d54a330c93862ea70b已推送分支/main并部署，ZIP SHA fb070932a93b4a145e9bd737ea6075250dc4b337ae0b6774952e96916c25a6d9远端一致，终端与公网health精确revision一致。run10后台PID3205097最终完成6/9包，25事件，packet7达到60图再次请求回看，video_vision_limit终止。完整398项目录归档（含该轮实际原帧与盲测）已原生下载到外部trajectory-v1/run-10及visual-inventory-probes-v1；没有新正式草稿或手机执行。

已用20/24模型调用，捕获模型耗时合计1192.315秒；剩余4调用恰被后两包基础观察/审计占用，没有额外纠正或回看余量。3次补帧为一次空新增、13张及16张，累计29/64，但packet7为60/60，无当前图片空位。audit20还将239(context)写作issue owner，尚未通过最终合同；不能仅放宽容量错误就宣称完成本包或整片。当前全片串行策略的质量及时间预算均未合格，不继续逐项扩大上限。

实际内容仍有硬失败：110真实收键盘触发吞入输入、以111误作新触发；117/121两次生成合为一条raw；128文本行触点误作去打印（audit已unclear但没补35967→37000）；第一下一步147与149/150仍当连续残留；199新按钮触点误作返回后自动校验；214→216自动处理链又推断下一步；230→231无新触点却改写为点击直接打印。旧任务去处理174→取消185→确定187→返回193及与新任务区分基本保留。晚收键盘282和最终首页未覆盖，不能记为本轮通过或漏报。

## 视觉诊断结果与本地千问路径

- 固定四象限放大未改善：12 full JPEG、16 tile SHA及RGB SHA逐一匹配，16 tile按父图bbox/2xNEAREST重建像素完全一致，模型仍漏看079/081灰圆并支持末帧8条热搜。输入粒度对照base/shared两次均issues空、110 supported，未复现旧094/101误报，无法归因改善。完整结果外部focus-quadrant-probes-v1及input-granularity-probes-v1保留，候选未上线。
- 无动作主张的两次六图盲测（PID3219971，包SHA df998b637f9fc3dc2d3b8358602e8eb51eb5f601f497f9b5e87a399cd296644a）真实返回：看见079—082浅灰圆形的持续/变淡，且描述018/300终帧只能看到5条热搜；仍对圆形功能不确定，没有把像素等同于按下。两次均同Qwen3.8Max/1024。这个局部事实改善不证明稳定因果修复，尤其081并非最早可见圆点。
- 堡垒机再次明确退出登录，入口密码页无可用保存密码，已请求用户恢复；正常平台通过Chrome已保存登录恢复成功，六项模型仍Qwen3.8Max，旧27步/25问题草稿只读查看、未改或下发。
- 发现本地Runner runtime已有同官方DashScope/qwen3.8-max/qwen3配置，凭据仅内存使用，从而继续独立本地诊断。inventory-assisted-audit-local-v1两次完成：negative37.548秒/positive30.965秒，均1024；positive现在承认灰圆自079已存在，但negative仍支持末帧8条，不可称已解决审计。原始JSON及12实际图保留。
- 原生整片视频调用仅诊断，无Runner或源码改动。原片12249942字节、base64请求16335644字节在16MiB内。v1一次请求HTTP400，仅safe status；v2另获授权一次同请求，safe error明确total_pixels必须<=268435456，实际endpoint与文档819200000存在差异。v3仅按实际错误改这一参数，其余原片/fps10/temp0.6/1024/120秒/零重试保持，正在运行。各轮独占native-video-local-v1/v2/v3，保留失败，不隐藏重试，不存base64/key/thoughts。
- 官方参数文档提示Qwen3.8思考视觉temperature低于0.6会按0.6处理；此前实验记录0.1是请求值，不能据此保证实际低温或确定性。新原生诊断明确请求0.6。依据[官方参数参考](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)。


## 原生视频流式响应与职责拆分对照

同一原片 SHA370d92e2622686330d114ce14c67c50788a8e92e4aa1babbbd7e856e2796d893，同Qwen3.8Max、fps10、max_pixels655360、实际total_pixels268435456、temperature0.6、thinking1024，所有调用独立输出、单次零重试，无凭据/base64/思考内容落盘。以下均是隔离诊断，没有生成正式可执行草稿或下发手机。

| 轮次 | 唯一主要差别 | 实际结果 | 内容资格 |
| --- | --- | --- | --- |
| native v3 | 按实际endpoint错误修正total_pixels | 非流式120.238秒超时 | 失败保留 |
| native v4 | stream+include_usage，单次诊断deadline300秒 | 127.366秒，首正文67.706秒，HTTP200/stop，19事件，reasoning1024 | 主线更连贯，但多个跨控件点击、两次重复触点及多级返回被合并，未合格 |
| native v5 | 同v4参数，只改原子交互提示 | 101.776秒，首正文46.717秒，HTTP200/stop，38事件，reasoning1024 | 两个取消与确定分别列出、收键盘独立；但同字段输入拆7段、遗漏模型卡片点击、重复触发与返回仍漏，未合格 |
| native v6 | 同v4参数，只做连续输入会话专项 | 57.096秒，首正文44.931秒，HTTP200/stop，原始2父会话，reasoning1024 | 本轮删除/英中切换/IME/候选留在同父会话，改善v5碎裂；不是整片资格通过 |
| native v7 | 同v4参数，只做非输入原子交互 | 90.777秒，首正文52.208秒，HTTP200/stop，25事件，reasoning1024 | 恢复卡片点击，但漏早收键盘、首次耗材确认及分离next；55500附近又主张无原帧触点的next，未合格 |

v4保留旧任务与新姓名牌任务的不同预览及取消路径，没有像run10那样在214附近新造下一步、230附近把自动刷新写成点击。不能据此使用其近似秒钟绑定帧号：早收键盘吞进input、晚收键盘不明，117/121与42967/44033仍被合并。v5分清下一步/耗材确认和取消/确定，但搜索输入4段、模型输入3段，多级返回仍有合并，说明同一提示同时承担业务摘要、输入合并与原子触发识别存在目标冲突。

v6原始两个会话无需事后合并，但其时间与状态仍有硬限制：搜索last_edit11000仅近似，04711600才有最终可见状态、04811867有提交触点；模型last_edit26000时109仍组合态，11027167才见已上屏与独立收键盘触点，不能把26000改造成已验证终态。第二final_visible_state写键盘已收起，越过独立结束边界；部分phase.question非空而根/session uncertainties为空，还有question=null的非严格合同。未来必须逐路径保留疑问，不能只看根层空数组。父任务实际读取raw并与原图复核，独立review亦确认上述限制。

隔离后续路线：整片只给业务/状态候选，输入会话单独识别，非输入独立操作单独识别；近似时间只用于检索真实原帧，不能自动变为trigger、抹除疑问或绕过旧consolidation/执行门禁。再用不带动作主张的原帧像素清单核对触点、文字、键盘与状态；人工选取的诊断窗口明确标记，不能称整片自动覆盖。v7非输入专项随后已单次结束，25事件仍未合格，完整review保留native-video-local-v7；58图盲时序清单实际结果见下一节，均未接入默认worker。旧串行方案不再仅靠放宽预算继续试错。

外部证据：new-touch/trajectory-v1/native-video-local-v3/v4/v5/v6/v7（request-metadata、原始response-content、status、content-review）；生产仍f7f8a426，正常平台旧27步25问题草稿未改未下发。22:36:46指定ecbfd645只读在取消原因/反馈页，没有4F2A任务归属证据，不执行取消或打印；堡垒机仍等待用户恢复登录，独立本地诊断可继续。


## 盲时序事实清单与当前手机前置状态

- 58图/6组单call实际118.539秒，Qwen3.8Max、reasoning1024、stop，prompt27834/completion6522/total34356。所有原frame ID/顺序逐一齐全，没有漏/多/重复；这仅是机械覆盖，非视觉通过。原content SHA41cd3f9fa36123cbe9e5e0d8c8f35fbe7d066d7f0c2f84aa92025222949a2184，实际图与元数据/时钟证据保留外部blind-temporal-evidence-inventory-v1。
- 父与独立review实际看原图：C正确列117生成按钮圆、118/119/120无圆、121再现；D实际补8均匀帧后，147首次所给圆→34743733/34844000无圆→14944033新圆；E282键盘上方空白圆→283隐藏键盘；F128及329..331文本行圆与336..339去打印按钮圆不混。33536533按钮无圆、33636600已见圆，早先36667是已有清晰证据点，不是已证最早按下时刻；外部baseline amendment追加说明，业务基线不变。
- 仍有实际漏检：055卡片内圆、110键盘工具栏最右端圆未报告；111布局变后邻近尺寸条的残圆虽然几何描述可成立，但不得反向当成尺寸条新点击或111收键盘触发。此轮qualified=false。新8原图与同8+8固定象限对照只测试小标记复查；旧58与新8模板/attention scope也不同，不能宣称缩包或放大是已证单因素改善。
- 23:22–23:23指定ecbfd645实际截图：仍在空白取消反馈页；本轮仅一次KEYCODE_BACK返回当前设备页，未点取消/提交反馈/确认打印。清楚显示EP3-9888E0094F2A、在线/异常、喷头115°C、巧克力印章0%、第1盘把手打印中/第2盘印面待打印。这不是原片姓名牌，任务归属未证，未取消。外部current-platform/phone-20261027-232320-*.png及preflight.json。当前不能进行影响打印任务的试跑；不得沿用19:01的空闲/25°C快照。
- 新路线的小样本与预算检查记录在docs/superpowers/plans/2026-10-27-video-evidence-trajectory.md后半部。源码默认worker仍不变，没有正式新视频草稿或Runner整例执行证据。


## 2026-10-28 空间与模型原生分辨率对照、跨包组装终态

同8原JPEG/同盲prompt/同system/payload/文字，base60.361秒与+8固定象限53.191秒均Qwen3.8Max/stop/reasoning1024、全8原帧机械覆盖。base仍漏055/056但已看见110；+tiles报告055/056同位卡片圆和110工具栏圆，所以110变化不能归因空间放大。独立最初看056为无圆是人眼漏看，原review保留并追加v2更正；父任务实际看056原JPEG确认仍有圆。父也逐像素复算8块NEAREST派生图全部相同，原图从未修改。仍是单次局部信号，qualified=false。

新增单参数实验保持所有JPEG wire bytes/文字/图数不动，仅每个image part增加sibling min_pixels=1658880，遵循[官方图像输入参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)。实际单call47.626秒、Qwen3.8Max/stop/reasoning1024，8帧全部返回；provider明确image_tokens12976/text_tokens956、prompt13932/completion2799/total16731。该轮看见055/056卡片圆及110工具栏圆，同时保留功能不确定；054/112没有报告短暂标记，111明确可能为残留。实际provider重采样尺寸没有返回，不能仅凭请求值宣称已证分辨率或稳定改善；原base没有image分项，不编造其计数。无需人工选ROI、原JPEG字节不变的高细节机制值得下一步试验，但尚未接入Gateway/默认worker。

固定原299网格sample p3owned065..096/p4owned097..128，各44原JPEG(前后6context)，未为已知动作移动owner边界。实际单call76.672秒与69.461秒；每包32owned顺序、role、context引用身份严格通过。p3原文带完整JSON围栏，raw strict JSONDecodeError原样保留；另外使用现有yaml_service.normalize_model_json派生解析并标parser_mode，未手改raw或花API纠正。两实际请求缺response_format=json_object，与正式Gateway有协议差异，已记录且不追改。p4清单正确指110工具栏收起箭头圆、111键盘隐藏、117按钮圆→118/120无圆→121再现、128输入行圆；p3看见删除清空等编辑阶段。

跨包mapping实际单call120.005秒超时，首正文33.225秒，仅15855字节partial，无stop/DONE/usage，JSON截断；没有闭合补JSON、删字段、补call或称成功。共享ledger从15:46:52.966UTC起原deadline16:06:52.966不重置，实际3calls/0API纠正/1040.489秒wall，其中模型耗时合计266.138秒，其余准备review也计入。原样候选、nested问题和未完成dispositions都保留。不能据partial宣布输入父会话、独立收键盘或重复触发已通过；但其已返回部分显示重复抄SHA/JSONpath且又将IME内部按键列入independent_actions，下一步应缩短模型输出合同并由平台绑定证据，拒绝重复业务动作，而不是只加超时。

旧sample终止failed/incomplete。另准备紧凑映射协议对照时会明确是新独立诊断，复用冻结输入不视为fresh整片或复活旧预算。候选/证据使用短alias，owner/PTS/hash/path由平台查不可变索引，原问题直接保留在ledger无需模型逐项抄写；同字段输入内删除/切键盘/组合/候选保为phase，不另下发Runner动作。尚未调用新compact mapper或编写生产接入。外部证据candidate-driven-spatial-review-v1、native-min-pixels-probe-v1、cross-packet-blind-inventory-v1-prepared。

23:56:33公网health再次确认release_revision=f7f8a426213e80ff2b0ff5d54a330c93862ea70b。此后无新运行时代码提交/部署，默认旧worker仍未切换；没有正式新视频草稿或新Runner整例。当前认证及4F2A异常/归属未证任务依赖仍未解除。


## 受限高细节传输组件与紧凑协议完整失败

- 可选传输组件2c12aa52199f7b8e5abcb2f2a663b580f44e34a8：仅exact ui_video_trajectory/whole-trajectory显式perception_mode=fine-detail、official HTTPS DashScope exact qwen3.8-max路由增加每图sibling min_pixels1658880；未指定模式的调用保持原状。错误模式/其它路由拒绝，frame/JPEG/context标签不改，不单独启用thinking、不改24/1200/图片/重试/默认worker/模型路由。此参数未按sourceID/帧号/坐标/输入值分支。新25行生产差异配TDD红26Python/3Node后绿166相关Python+Node5/6；独立spec+quality均批准，父全1181视频+catalog20.17秒、后端63/Gateway46/主链编译/Node语法/diff通过。计划与完整报告见qwen-video-fine-detail计划和.superpowers/sdd/qwen-fine-detail；尚未部署、没有整片或Runner通过。
- 新独立compact probe单callHTTP200/stop，105.465秒、首正文78.783秒、正文1485tokens，reasoning1024，prompt34859(图片25320/文字9539)/completion2510/total37369。此次完整返回仍失败，不能再只用timeout解释：parts3>2、q数组、整帧代替fact、ph越part、111..116无编辑仍当第三part、final错选生成后的128、键盘内部a3..a9重复独立tap、110真实触点/111残留拆为2动作、第一生成结果122越过第二触发121。119引用均exact可查，说明短alias解决复制不能解决角色/因果。raw与全部不利结果保留compact-mapping-protocol-probe-v1/execution-v1；res为空，原问题完整不消除，旧B账簿SHA9f46d48b6267fd9a90d1cfbcbb2d2b2613096ce82b1b8ad11d0558ea89ea7d30未变，无补call。
- 下一层应继续区分视觉感知与业务语义而非给同一视觉mapper堆提示。官方结构化文档说明多模态json_schema会降级json_object，文本Schema模式才约束类型；准备纯文本语义映射对照，只用冻结视觉观察及native候选、实际raw图等待另一独立视觉审核，任何结果都不可据此称光学确认或可执行。模型仍千问，不放宽本地schema/owner/fact角色/真实PTS/源问题门禁。[官方结构化输出说明](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。此新text-only probe尚未调用。


## 纯文本映射失败、真实Gateway烟测与整片fine-detail启动

纯文本strict json_schema单次49.061秒HTTP200/stop，所有76帧/268事实文字保留，图片0；8处uniqueItems只从wire projection去除、本地schema不变。schema零错误，但本地phase_outside_part，键盘内部按键重复为独立操作、111残点新点击、088父anchor晚于085删除phase，内容不合格。六处原问题保持未决，res空、compilable=false、requires_visual_audit=true。停止新增全局mapper或提示变体，回到现有stateful observer的单参数图像细节对照。原始失败保留new-touch/trajectory-v1/text-only-semantic-role-probe-v1/execution-v1。

真实本地Gateway代码拷贝使用2c12aa52组件，唯一POST55.345秒HTTP200/stop、actual qwen_plus/qwen3.8-max、fallback=false/index0、prompt13886/completion2812/total16698/reasoning1024。8张原JPEG不变、每图min_pixels1658880；标准Gateway system/标签与旧direct probe不同，只说明组件兼容，不能归因视觉增益。原auth保持、LOG=false、独占127.0.0.1 child终于清理，无凭据/思考落盘。完整报告及原文在Downloads/qwen-fine-detail-gateway-smoke-20261028-5iet9cje。

唯一整片shadow经父审后从Downloads/qwen-fine-detail-trajectory-shadow-prepared-20261028-xwcxpkvv/shadow.py启动，脚本SHA535f62ae5cc395ec8acd03a75a55d09a8f4871d69b5817baf71a6433e81e76ab。仅原metadata299 JPEG，不混入run10补帧；原source SHA370d92e2622686330d114ce14c67c50788a8e92e4aa1babbbd7e856e2796d893、75376ms/432x960。直接调用当前observer和canonical loader；只observe/audit加入fine-detail、overview普通。原callback请求保留wrapper前内容，actual payload增量/图SHA另ledger，不伪称callback已带mode。共享原24calls/2纠正/4补帧64新图/60图10MiB/120秒/0retry，并补严格外层1200秒中断，不重置/不自动重跑。此前内部仅调用前检查wall的事实不变。actual soleMax/fallbackfalse每call校验；所有结果仍qualified=false，待独立全片审核。无consolidation/正常平台草稿/Runner启动。

01:10:07新手机只读截图显示指定4F2A在线/空闲23°C，活动任务区域消失，本agent未取消旧巧克力任务。证据new-touch/current-platform/phone-20261028-011007-preflight.json及同名前缀PNG。仅视觉前置，不是API归属证明或手机验收。Chrome当前仍密码登录，部署等待真实可用终端；本地解析诊断继续，不新下发打印。


## 整片fine-detail实际失败与上下文重叠根因修复

外部qwen-fine-detail-trajectory-shadow-prepared-20261028-xwcxpkvv原run终态failed/input_continuation，stage1/9/events3、8calls合计592.990模型秒；actual全qwen_plus/qwen3.8-max/fallbackfalse，cleanup确认owned child停止、原source/initial299/code在当时不变。没有consolidation/平台新草稿/Runner下发。原run原样终止，未重置预算。第一包实际canonical补20帧：3063900无键位圆、3073933 WXYZ圆、3084000按下、3094033组合串，重新观察04与audit05均支持edit；最早所给证据不等于精确物理onset。audit03误称027为context，但请求实际owned001..032/context033..038，错误已分开保留。

离线重建实际accepted04+revisit1和下一包prior_events完全一致。06/08的exact target/id/pages/boundaries均合法，唯一old predicate冲突是当前refs首0334767早于前输入futurecontext末0365067，差-300ms；08 trigger0375567已晚于旧end仍被拒，因此不是简单模型命名问题。07另外page_before=s2不匹配旧s3，且自身前后page不等，仍应拒绝。不裁剪rawrefs或改写s2/s3。完整只读分析/原SHA与base/fixed replay保留外部input-continuation-readonly-*和.superpowers/sdd/input-context/actual-replay.json。

修复8c09e4c6ee836ac272967cfbe92e88a61e46fc44只改时间谓词，允许同一输入futurecontext交叠；currenttrigger>lastfragmenttrigger、currentfirst>=canonicalstart、actionend与rawoutcome终点各自nonregress、max(0,currenttrigger-oldactionend)<=5000。exactidentity/target/pages、adjacency、真实边界、owner、所有问题/raw/8ref编译及逐fragment独立audit不变。14新测试，RED5失败/8通过后GREEN158专项，父1195视频/catalog20.28秒、主链compile/backend63/diff绿；独立spec/quality批准。实际06/08分别保32/34rawrefs编译8，问题与片段不可变；07继续拒绝。计划docs/superpowers/plans/2026-10-28-trajectory-input-context-overlap.md、报告/复核.superpowers/sdd/input-context。当前仍0合格整片/正式用例/新手机trial，不能把binding修复当内容完成。

后续新独立299原帧整片模板已准备，未混前轮补帧/overview/response/state；旧run不复活。另对audit03把edit误要求最早physicaltouch，以及audit05无触点推聚焦点击/光标隐去推失焦，准备唯一同38图prompt模态澄清对照；不植入原片帧号、目标或期望答案，保原始疑问及结果，尚待实测。此前9个堡垒机标签均实际密码登录，生产main/runtime仍f7，未部署默认observer。通用分层参照[ShowUI-Aloha实际parser](https://raw.githubusercontent.com/showlab/ShowUI-Aloha/main/Aloha_Learn/parser.py)：其依赖独立事件日志先合并动作再提图，不是只有录屏便可照搬；本来源缺日志，仍需Qwen真实画面证据。


## temporal修正后的整片终态与未通过的模态提示

新独立whole目录Downloads/qwen-fine-detail-trajectory-continuation-template-20261028-77genb3q，reviewed code8c09e4c6，observerSHAb39dce660b8da0afad9a9e53aac7cee34c6fa829cf593d3970e7b26a0c2b53ef，原299/源SHA/原提示不变。终态failed RuntimeError、5/9包、18canonicalevents、17attempts、模型调用合1193.323秒，图片呈现840次；16份成功actual route均qwen_plus/qwen3.8-max/fallbackfalse。最后audit17 dispatch只剩17.454865秒，timeout同值，15.039秒后失败，无actual provider/usage及具体safe reason，不能断言outerSIGALRM或已确认上游route。cleanup/sourcehashtrue。observe16仅第6包raw，未审计；第6至9包不能计完成，后半返回/第二取消尚无完整结果。performance-summary.json及independent-review-terminal.json保留实际限制。

离线binding修复在真实第二包生效，两个搜索输入immutablefragment合并身份、原refs与第一fragment未决不丢；048提交/055卡片独立动作及audit支持。内容hard错误仍在：owned110收键盘推给后包，下一包111残点误绑绿色确定且audit11支持；117有点→118/120无点→121再现却合一次“有效触发”并借第二次后125结果；12835967字段行圆反推去打印，audit11正确unclear但未请求解码36.6，本轮没见旧诊断336不能借证；147/149两生命周期合一次，没补43.6..44.0无点间隔，audit15全支持。firstfragment audit05将未声称的“文字上屏”加入outcome而判unclear；raw14又写143..150无触点同时列147/148点，audit未发现。故图像细节提高不足以解决所有权/生命周期/主张理解，也没有整片性能资格。

本轮新补go处理局部有效：33247900无圆、33347967圆首现、17448200续圆，raw14绑定333→176并audit支持；不能混成旧36.6秒同编号补帧。新原raw和独立报告均不改问题与verdict；不是源内容或Runner通过。不得再次盲目重复全片fine-detail或只扩超时。

另独立ONE模态澄清probe目录Downloads/qwen-audit-modality-oneprobe-20261028-onir0gak：真实helper与Gateway wire证明body仅prompt末追加通用edit/touch/光标语义，38原JPEG/labels/payload/1024/10000/soleMax全不变。实际53.381秒HTTP200/stop、prompt71183/completion1554/total72737/reasoning1024、fallbackfalse；三check双supported但仍无证推聚焦点击，并输出owner026不含refs018..025，现有validator拒audit_issue_owner。整体FAIL，不land、不补call、不清原问题；原auditpromptSHA8745d65ea164d95b7b2f92fdddcb84674bc21a7de35a80d78164621d9c2ac088保持。

现有触点辅助离线诊断见new-touch/trajectory-v1/existing-auto-detail-probe-v1/diagnosis-20261028：054→0551033ms和109→1101167ms先被500ms门限跳过；055在当前色差85时与图片加载连成48x23/density.291，诊断45可18x18/.861004但非通用参数；110 .859922<.86；110→111全图差58.654>20反向也拒。原source/JPEG未动，不直接调阈值后宣称解决。下一候选-only离线实验按[OpenCV官方Hough方法](https://docs.opencv.org/4.13.0/d4/d70/tutorial_hough_circle.html)冻结参数和syntheticcontrols后唯一扫描，实际PTS、全源未处理范围和假阳性必须可审查；没有AI/API/生产改动或动作授权。


## 候选原型前两次冻结结果与当前线上配置

原型v1六synthetic通过并冻结后，唯一fullsource尝试被本机FFmpeg9的-vsync0语法拒绝，ffprobe2258真PTS0..75233.333ms/432x960、实际0帧解码、1.129秒，不能推断Hough效果。独立v2-decoder-only经本机help/手册核实仅改-fps_mode passthrough，保v1所有seal/结果，Hough/tracking参数不变。v2首帧0ms得到129圆>32冻结cap，全部129保留，无截断；解码/Hough1帧，fullyprocessed0、后2257帧未检查，全部2258未完成，3.305654秒。输出约2.14MB/4联系表及总览、source原frame PNG、raw全部几何候选。父看总览，普通文字/图标/模型纹理大量成圆形，不能把geometryproposal当动作。既没有min2frame候选，也没有055/110召回结论。所有原记录和源不改，0AI/0Runner/0生产改动。v3仅准备通用局部时序变化筛选，尚未执行原片，不把已知点用于过滤或调参。

18:20:14UTC公网/api/health返回HTTP200/oktrue、完整revision f7f8a426213e80ff2b0ff5d54a330c93862ea70b；git ls-remote refs/heads/main同SHA。18:20:41UTChealth.models text_model与vl_model都qwen3.8-max，officialhost合法、URL不含凭据。外部current-platform/health-20261028-verified.json和model-health-20261028-verified.json。较早health-live摘要用不存在status字段为None，原摘要保留，另存正确ok字段，不能据投影None推服务错误。线上默认旧worker，分支输入guard修正已推；当前仍没有原片合格新用例或手机整例通过。


## 原生全帧测量、变化分组与单包真实输入改善

OFFLINE原型v3在第38解码帧因36有效候选超过32停止；v4整帧overflow改UNKNOWN后到67.867秒因203 transient超过200停止。v5只容量200→512，原核心/阈值不变，2258/2258 native帧69.631秒完成，PTS/RGB/PNG零绑定差异。468轨迹中234候选、207单帧、27背景；131整帧unknown、36间隔、585局部unknown、139截断及4EOF均保留。独立预选8个困难点仅6处空间匹配；两次生成被同一长期文字几何桥接。均不是物理触摸或动作确认，12个均匀ID样本9普通UI、2似标记、1不明，非随机精度估计。

通用变化分组未再解码/调API，保留468父及7077观察；646novel按既有100ms真PTS间隔分508组，另40中点检查/2072证据引用。index SHA05b9a7f4a61fd0f788fb327ff0f1e437dd27ab2dd09af27bb9c545e10274e6df。独立原PNG支持生成局部圆首次在(29166.667,29200]、第二次在(30966.667,31000]出现，30100/30966.667采样无圆；不推断连续全部缺席、物理press/release或因果。原v5的6/8不回改，新增图也不回算旧模型已见。

marker-cue-packet-probe-v1从immutable旧audit按trigger不清→issue顺序自动选2窗口(128及121)，原44JPEG+16独立native Q90派生共60图，40变化候选，2800644编码字节。原3包prefix仅复用9事件/8问题，未声称fresh whole；所有2258原生绑定/319原图与prefix/原call10 render均验证。唯一实际observer96.281秒、actual qwen_plus/qwen3.8-max/fallbackfalse、prompt151591/completion2722/total154313/reasoning1024，原auth独占local child清理true；无重试/自动audit/conso/Runner。完整raw/bind/outcome/trace及独立content-review在外部同名目录。

局部改善明确：110context收键盘没有再次造111绿色确定，实际print触发从128文本行误判改为33136600（33036566.667无圆）；本stage33336967不可混旧47967。仍不合格：117与32231000的两次可见overlay已经被模型描述，却以一次生成响应为由并成一个aiTap，outcome122跨第二尝试借结果；40条候选没有逐项保留处置，冻结prefix原dismiss遗漏仍在。contract_valid不等内容通过，独立模型audit尚无、正常平台新用例/Runner仍0。下一处理须把视觉尝试账簿与业务归并分层，不能把CV直接编成aiTap，也不继续整片相同高细节循环。

## 2026-10-28 16:42 实际部署与短窗口视觉账簿

- 5368b0e8精确调用禁止fallback独立规格/质量复审通过；代码分支已推送。原生Chrome上传archive SHA ae7487dad1b1de2923bebccc08df2d576ec5ef7211ffef1c3eb8cdfad3eb09c5与服务器sha256sum实际一致，bundle SHA701b3ae8e1165ed2ff739572d63d1cd62740354dc5ace5fb804228ca38fcdf3d。既有deploy/update-main-server.sh实际终态部署完成5368b0e8，16:42:34+08公网HTTP200/oktrue/完整revision一致，Sonic容器保护及服务/登录/前端检查完成。origin/main仍旧f7、服务器main已ff到536；默认worker仍旧分析入口，无正式新草稿/Runner通过。
- v2单包唯一实际请求118.091秒后RuntimeError，无原response/HTTPstatus/code/usage/actualprovider，仅trace source=ai_gateway；不是可确认模型语义结果，不能断言具体timeout来源。ownedchild清理true。原60图/25owneddispositions方案失败封存，不重复同输入。下个harness已在实际guarded urlopen捕HTTPstatus/精确白名单code/type及通用classification，不存任意错误正文/凭据/思考。
- marker-cue-focused-probe-v3从原machine issue frame121窗口[30967,33000]及其parents自动带出3ownedcue，保全部6witness/gap+2context共8原JPEG，6fine/2default、prompt10330UTF8B。唯一实际调用32.529秒，qwen_plus/qwen3.8-max/fallbackfalse，usage14158/1641/15799/reasoning1024；contract_valid_only，清理true。root与独立8图目视均确认117圆出现、118/120无圆、322/121再次出现；raw ledger没有再合掉这两次视觉出现。323/122是中央生成中矩形加载面板，原prompt overlay一词未区分录屏触点与应用浮层；第三cue不能当第三触摸。独立报告待写，不直接生产集成/不计整片通过。
- 下一小步v4纯prepare：相同8原JPEG，明确input_visualization/app_ui/unknown并关联原trajectory事件及不可变cue，未决关系保留，不强制点击数、不借后次结果。旧v1/v2/v3及raw均不改，待rootreview再唯一调用。
- 15:44:03指定ecbfd645只读图显示EP3-9888E0094F2A在线/工作中27°C，掐丝珐琅任务下载或打印进度超时、重新打印按钮可见；归属未证，未取消/重印/下发。早4:14的idle不能代表当前。没有新手机试跑或最终物理确认打印。

## 2026-10-28 hybrid首轮与局部绑定结果

- 87b4a564状态元数据合同修正已提交推送分支，独立spec+quality PASS：明确state五字段、实际outercatch给固定安全state反馈；RED7fail8pass→GREEN15，271trajectory/backend63/compile/diff绿。origin/main已ff5368b0e8与实际线上一致；87尚未部署，worker默认旧入口不变。
- v4单次真实68.545秒，Max/fallbackfalse、15979/3242/19221/reasoning1024；raw两aiTap分117与322，firstoutcome118/120在second前、second323/122只处理中，app_ui独立分类改善。终态failed ValueError；离线精确诊断state_shape缺三字段。直接下层bind_packet_events对原2events成功/rawfragments不变，rawSHA80be1b6e...前后一致；没有删state后冒充通过。独立发现0216局部指代混用、同分辨率误述、光标回看过度猜测，整轮仍不合格。
- Flash盲packet004一次46.174秒/stop，actualqwen_plus/qwen3.8-flash/fallbackfalse，22001/4062/26063/reasoning1024，32owned/44图合同完整；root选择性看097/110/111/128及同原C图，支持收键盘/两次marker/文本框触点归属的局部事实，不声称32条全部准确；历史Maxstream/JSON/system设置差异已列，非纯模型A/B。无生产Flash路由改动。
- 新wholehybrid-v1 /Users/adouceshi/Downloads/qwen-hybrid-whole-prepared-20261028-v1仅原299/sourceSHA370d92...，Flashoverview/observe默认细节，Maxaudit自动requiredrefs至多12fine，Maxconso/input默认；每请求fallbackConfig与所选model一致、真实route均无回退。root审脚本f2a5f7b6a8aa800b1a84063e4f014c62372d8a64fc7dd39e258c9e5a7713e40b及sourcepinsfc6cb151e30d6b5ccefd72350ba2ead61fe93343b68c6f6b91e0665e2c65c482后唯一运行，24calls/1200秒共享到tail、4revisit64新帧60图等不变；12synthetics/五阶段实际GatewayVM及60/64文件预算检查通过。私有诊断未携带baseline_examples，服务器真实Top3尚未读取；诊断不等于正常平台资格。
- 实际hybrid-v1 failed/video_vision_limit，1/9完成、5successfulcalls：Flash41.609/30.061/29.208，Maxaudit36.158，Flash30.956；模型合167.992秒，均actualQwen/1024/fallbackfalse。3revisit新8/0/16，323帧，总新24未触全局64上限；无conso/input/正式草稿/Runner。第二包原44图32owned12context，请求两个回看，expand把16空位全给首区间达到60，第二区间capacity0失败。原失败永久保留，不复活预算。
- 正在surgical revisit_capacity_fix：按剩余有效回看请求公平分配局部容量，保原图/owned/context/所有原限制；简明澄清IME内部按键/片段未结束不是独立业务边界、续接身份逐字复用、光标/键盘状态不能反推点击；不写自动清疑问或删步骤代码。brief .superpowers/sdd/attempt-cue-probe/revisit-capacity-brief.md，待TDD/独立审查再从原299开新轮。
- 指定ecbfd645/9888E0094F2A，15:44只读为工作中/掐丝珐琅进度超时非归属证明，未取消/重新打印；没有新真机执行或最终物理确认。当前瓶颈是源解析及门禁，没有要求用户再次给素材/确认方案。

## 2026-10-28 hybrid-v2实际终止：视觉记录与步骤关联仍错位

- 公平回看容量02e0c501已完成独立spec/quality PASS；RED5fail3pass→GREEN8，306trajectory/revisit/catalog、backend63、主链编译及diff通过。已推送分支，尚未部署；线上仍5368b0e8且默认旧入口未变。
- 新独立wholehybrid-v2只原299帧，sourcepins SHA04c467c510f100d1ec117e2128da5d2a8529da6173bb5f9082234f33cfc6cf04；实际终态failed/revisit_budget/video_model_invalid，3/9包完成，15次成功千问调用合501.798模型秒，actual Flash/Max按阶段且fallbackfalse/1024。四次回看新增7/11/0/8帧，第四包第二请求触4次上限；没有conso/input/正式草稿/Runner。旧轮不复活、不扩预算。
- 第一包假聚焦点击改为键盘状态断言；但第二包055搜索卡片真实指示点漏看，060被当触发，回看消耗三次；第三包111结果/残点误作收键盘触发（实际110），audit错误支持。第四包原raw13空数组被合同拒，纠正raw14仍合并117/121视觉出现并借122后次加载，误把128文本框圆绑定去打印；audit15仅后项抓到，整体不合格。
- 下一步私有typed-frame-evidence-pilot-v1：保原44图/原事件合同，增加逐帧视觉记录及事件关联，只检查模型自洽与真实原图，不把每个圆自动变点击、不强制两次物理按压、不自动清疑问。三冻结包局部诊断不计整片资格，准备者禁止API，root审后各最多一次实际调用。brief .superpowers/sdd/attempt-cue-probe/typed-frame-pilot-brief.md。
- 原生平台只读确认16已发布基线、指定Runner连接就绪、原片最新仍分析失败；没有导入/保存/启动。Top3服务只读导出脚本仅本地准备，堡垒机当前再次会话结束，未运行服务器命令。手机最近只读15:44为掐丝珐琅任务进度超时、非本次归属证明，未取消或重新打印。

## 2026-10-28 typed混合请求三包均失败，定位输出合同冲突

- 私有v1准备27synthetic/独立spec+quality PASS、165输入pins及3historical核对，root三次独立真实请求已全部终态FAILED。002/003/004实际Flash/fallbackfalse/1024，25.119/26.593/45.945秒，prompt29174/33169/34860，completion2206/1963/4432，total31380/35132/39292。各attempt1、ownedchild已清理，无自动重试/补帧/审计/正常用例/Runner。
- 002/003仅输出旧六字段，遗漏新增bank/links；004输出44bank但新增phase/packet_id且3revisit超2，schema失败。004把110/117/121/128均记absent，且先events后bank；原步骤仍有结果反推触发/重复去打印。新typed记录并未使视觉自动正确，原始raw及失败保留。
- 控制器发现自己新增prefix与旧prompt末尾仅旧root JSON示例冲突，不能归因成千问服务错误。下一次只对原packet004隔离此项：唯一规范输出合同、bank首先输出，原业务规则/44原图/完整payload不变，不新增全局mapper，不复活旧预算。brief typed-bank-first-control-brief.md，prepare-only，无新API。
- 独立像素review覆盖108unique/132memberships，确认055卡片/110收键盘/117与121间有无点/128文本行；报告明确STATE与memory背景暴露，非完全盲测。无raw/manualbaseline输入到producer。对原三结果的独立contentreview进行中。

## 2026-10-28 单一规范bank-first对照仍失败，停止混合提示变体

- typed-frame-bank-first-control-v2独立spec+quality PASS，root核对后唯一实际52.944秒，actualFlash/fallbackfalse/1024，34856prompt/4426completion/39282total，ownedchild清理true。raw八键顺序正确、44bank、2revisit；但3events均漏continuation_of导致schema failed，44rows仍全absent、110/117/121/128漏看，事件仍122/129状态反推及137额外导航。没有补字段后冒充通过，不续跑旧轮。
- 此次prompt清除旧root-only例子、bank最先输出并保持原44图/payload；格式冲突确实解除，视觉/因果仍错，不能继续同类mixedprompt变体。报告与精确SHA在.superpowers/sdd/attempt-cue-probe/typed-bank-first-control-report.md；原raw保留外部同名目录。
- 下一纯感知对照perception-only-control-v3仅复用先前成功Flashblindinventory的原prompt/32ownedrows协议，改为当前packet004同44图/role/time，去掉不属于感知职责的prior_events/states/hypotheses；不输入旧模型答案/手工源标签。纯画面形状/位置/控件/字段和键盘状态，零业务动作。准备者禁止API，root审后一次实际；不是新globalmapper/不重置旧预算。后续语义与独立审核须另做，当前未生成新用例/Runner。
- 独立实际内容review确认旧v1三包不合格且raw6份/原账簿2份SHA不变；审阅者重看065发现确有去编辑淡圆，更正原账簿absent判断并在新报告公开，不覆盖旧ledger。此更正不输入producer。堡垒机最新原生AX仍密码登录，登录请求待答，本地继续。

## 2026-10-28 纯感知职责分离恢复关键画面事实

- perception-only-control-v3独立spec/quality PASS、76pins及44图一致，root离线verify后唯一实际46.627秒，official qwen_plus/qwen3.8-flash/fallbackfalse/stop/1024，prompt22001/completion4121/total26122。32ownedrows合同完整，child清理true。root选择性复核117/121分别圆出现、118-120圆消失、128文本行灰圆，均被事实记录保留；122/129加载面板也以transient视觉元素记录，不能当触点。所有32行的独立像素内容review尚在进行。
- 此输入与失败bankfirst-v2同44图/role/time，但producer只做纯画面描述，不携带prior_events/states/hypotheses。prompt6517B，完整旧request私有保留不删除；不是纯模型A/B或单参数归因。scriptccc81b965a4e902c4711f666629aac0876a2e6c219569a67136fab7ae919d8c9/manifestb8646458ece25cf685d1c2ce611309f13b3ada5f0b5a8f4b3013d08657a15e26。
- 下步prepare-only perception-backed-observer-v1：从真实32行视觉事实做单packet原trajectory事件/状态语义映射，Max纯文本strictschema，0图/1024/10000/120/0retry/1call；不是旧全局phase/aliasmapper。保原历史全文private，模型prior只给身份/refs/问题投影，去未验证因果叙述；不注入人工baseline/review/旧失败答案。schema维持原event/state字段及原binder，事实问题原样保留provenance，真实业务疑问必须输出。独立视觉review及root审后才实际调用。brief perception-backed-observer-brief.md。
- 尚无whole-source通过/正常新草稿/Runner；当前仅支持职责分离能恢复视觉事实，不代表生成业务用例已通过。堡垒机仍密码登录，凭据恢复请求待答。

## 2026-10-28 分离后的Max语义阶段合同通过，内容仍待审核

- perception-backed-observer-v1独立spec+quality PASS，rootexact离线verify后唯一44.613秒 official qwen3.8-max/stop/1024，prompt8868/completion2030/total10898。纯文本严格schema，0图；原event/state/binder校验通过，raw不变。没有fallback路径/child/listener/key落盘，不伪造providerId或fallback返回字段。
- 新输出2事件（生成模型、去打印），去打印的准备toast与后续导航未再拆为两次操作；但生成observation明确说两次点击而仅一event、outcome跨121，触发129被称navigation而实际转场137，标题OCR少字和按钮颜色功能推断继续传播。recordingmarks不证明物理次数，当前仍needsindependentvisualaudit/qualifiedfalse，不能当whole/正常用例/Runner通过。
- source事实32行与8priortrack/fragment身份/原问题/refs精确投影保留，先前因果叙述不进入semantic；完整oldrequest私有，未人工补值/改raw/注入review结论。script235f573231a5d245814536df409e546c4531f201d8dc9fba288b8916828be137/manifest0a16a3bc17757075d0279ef513462d70eb08047d06e3aab6bb9418b1eeb3ba0b；10focused tests/24inputs/56sourceprovenance pins已核对。
- 纯感知32owned独立像素review完成：核心117/121/128支持，但112越context未填refs、按钮/字段外观变化遗漏、141标题少字等缺口完整保留，不将事实清单视为全真。独立semantic内容review进行中。下一次完整fresh诊断将复用既有trajectory/conso/input门禁并加入独立感知阶段；默认worker仍未切换。旧失败全部保留，不再试mixedprompt变体。
