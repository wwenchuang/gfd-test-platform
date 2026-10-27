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
