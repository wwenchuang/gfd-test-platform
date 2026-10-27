# 原视频真机验收接续：重叠复核与失败证据（2026-10-26）

## 最新真实状态

- 用户再次要求继续之前未做好的真机验证。视频任务页面改版已在1a8e54e0上线并实际验收，此处继续原有整片和设备链路。
- 原来源uvs_2c853ccbcb1d46c1a5b91e808f22fe49在实际Chrome点击“重新生成用例”，第2次分析覆盖75.38秒、299帧，终态failed，界面提示“用例整理遗漏必要业务操作”。没有新草稿、没有手机执行。尚未取得v2完整服务端原数据，不推断具体缺失位置。
- 23:24本机ADB核对ecbfd645在线；Runner PID43512，平台online/preflight ready；应用1.19.71(52)。实时截图显示EP3-9888E0094F2A在线、空闲；旧平台snapshot时间不作实时证据。未创建、取消或打印任务。
- 堡垒机再次“用户已退出登录，会话已结束”，登录页需用户完成凭据验证；已异步请求重登。依赖该连接的日志获取、离线包上传和部署尚未执行。

## 待部署修正

1. 原v1保留raw存在两个同控件点击：54033–54400ms refs205/212，与54233–54400ms refs209/210/211/212。时间严格重叠却只共享一帧，旧触发器要求两帧而未请求原图复核。触发条件改为任一共享帧，保留同动作/目标/值、相邻交互及严格时间重叠。仅端点相接、不同目标、独立输入均不触发。这里只请求既有有界原图复核，绝不直接合并/裁剪/删操作；不据raw直接认定最终失败根因。
2. 最终候选在全部局部修复及延迟响应复核后、加载规划图片前统一报告到既有进度回调。后续规划失败也能在原owner/app/version私有来源保存实际raw_events；原先else分支只留第一轮，另一分支在响应复核前保存。原始first_pass单独保留，输入恢复、门禁与调用预算不变。

新增严格单共享帧反例先2失败后通过；最终快照两分支反例也先2失败后通过。全量939视频回归、63后端检查、主链及变动文件编译、diff通过。独立审查121专项及95完整whole-context通过，无剩余P1/P2。两个旧进度测试保留窗口3/3历史报告和原单事件/精确起止验证，新增最终报告必须等于实际raw_events。

## 证据和后续

外部目录 `/Users/adouceshi/Documents/projects/video-reliability-20261026/new-touch/`：
- current-platform/accept-eaae4d5c-failed.json：v1完整失败数据。
- current-platform/accept-1a8e54e0-v2-failed-ui.json：v2 UI事实与局限。
- current-platform/phone-preflight-1a8e54e0.{json,png}：实时前置检查。
- ui-journey-acceptance/original-retry-v2-failed.{ax.txt,png}：v2实际页面证据。

恢复连接后先取v2完整数据/日志，保留失败，再上传校验并部署本修正。原片重新生成、逐项核对、正常门禁和完整Runner真机仍须实际完成；固定ecbfd645/9888E0094F2A，已有归属明确测试任务有则取消、无则继续，不确认物理打印。不能把939离线回归或局部模型复验算作整片/真机通过。


## 2026-10-27 实际部署及第3次生成

- ceb4bb1c离线包在当前堡垒机会话上传，SHA256 `3c4b95a5a89cc32207db8e9b27a92cc10af16389ad16e6aee73eaaf9c0f7aa57`核对；终端显示部署完成，公网health精确revision一致。部署记录current-platform/deploy-ceb4bb1c.json。
- 已从只读来源存储取回v2完整文档，保存current-platform/accept-1a8e54e0-v2-failed.json；46 raw、44 first_pass，没有最终候选审计，不能确定具体遗漏操作。
- 实际Chrome对同来源、同原片、无补充目标重新生成v3。10:35:16终态failed/video_coverage_resolution_invalid；12/12观察完成，52 first_pass、55 raw。服务端脱敏栈到reconcile→_repair_coverage→_repair_window→coverage_resolution.validate:200，交互帧不属于所声明全部旧锚点。修复失败发生在最终planning回调之前，因此raw仍为独立复核输出，不能当已修复候选。完整文档保存current-platform/accept-ceb4bb1c-v3-failed.json。
- 10:31实时ADB及Runner在线，手机应用1.19.71(52)，真实截图指定EP3-9888E0094F2A在线空闲、喷头25°C。current-platform/phone-preflight-ceb4bb1c.{json,png}。没有生成新草稿或下发真机；不算手机验收通过。
- 继续用保存的两轮原观察重放局部修复并捕获请求/响应，隔离进程不修改来源/草稿/设备；重放只用于定位，不能替代之后正常平台整片生成及真机执行。


## 共同触点位于修复窗口末尾

- 隔离v3重放相同错误：call-2/3都将205–211的持续按压对应为一个确认，但声明interaction_frame=209，不属于205/212稀疏锚点。唯一结构共同帧212恰为可引用窗口末帧；当原图仍显示持续触点时，也缺少严格更晚的可引用后态。现有213只作上下文，不能满足引用合同。捕获请求、响应、窗口和失败栈保存new-touch/replay-v3/，终端证据current-platform/v3-replay-failure-terminal.png。
- 不修改覆盖映射验证器。仅对严格时间重叠同目标点击的结构共同末帧，在未跨入保留交互、未达视频末尾、未桥接相邻片段且40帧/组、160帧总预算允许时，补紧随其后一张原图到可引用修复窗口。追加说明共同持续按压可作为interaction，不把转场当触点；实际合并仍须模型明确像素证据及全部原门禁。
- 完整映射回归先因f5仅在context不在frames失败，修正后通过；下一业务交互、视频末尾、40帧预算、仅端点相接四反例通过。944视频回归、63后端、主链和修改文件编译及diff通过。独立265专项审查无P1/P2。
- 修正副本上传到独立诊断目录，使用同保存两轮观察和真实模型重放中，未替换线上模块；尚不能声称局部或整片已解决。


## 后态补帧后的真实拒绝与提示矛盾

- 两次最新请求及结果已通过堡垒机下载到new-touch/latest-replays/。两轮call-2/3均实际frames205–213，context203/204/214/215；唯一common_frame212。第一版仍复述209属于所有锚点；第二版移除invalid_resolution_candidate后也选209，均被原验证器200行拒绝。补帧本身不足以解决模型输出，不能宣称局部通过。
- 新增拒绝反馈仅限定interaction_frame_not_shared普通点击。独立审查发现初版措辞会影响返回手势例外，补其他错误反例先红后绿并收紧作用域，P2复审解决；保留全部结构诊断和原图，不回填或修改模型输出。
- coverage.md原诊断段把“当前选择帧不属于锚点”直接要求为“锚点必须移出”，与共同按压帧改选规则矛盾。修改为先核对共同原图，合法共同持续按压则改选并完整保留全部旧锚点；无证据则独立解释或保留疑问。返回中间帧例外不变。该单段提示修正尚待真实模型隔离复验。
- 一度推测相邻选区连接保护导致补帧跳过，实际选择器证明213已提供，故撤回额外连接改动及对应测试。以实际请求角色为准，不保留无必要的扩大选区修正。
- 最终当前946视频回归、63后端、主链/改动编译与diff通过，日志/tmp/video-following-state-{regression,backend}.log。尚未提交或部署本批，不计原片生成或手机通过。

## 实际两窗重识别及异步响应误判

- coverage提示隔离重放/acceptance-replay-prompt仍被原共同帧验证拒绝，不能称修复成功。
- 使用当前v1真实提示、原片window7(183–212)/window8(209–238)、原图片及touch-target probe做隔离观察，12:04/12:05均通过既有validate_analysis，生成6项观察。前窗确认refs205/211/212，后窗209/211/212，保留共同真实按压211；不是以弹窗消失212冒充触点。重建prior只取已保存前段最后8项，未运行touch-target conflict及整片流程，故仅诊断。服务端/acceptance-press-anchors保留每窗request/result/checked；第二窗终端截图current-platform/press-anchor-window-8-terminal.png。
- 第二窗仍明写frame214无明确触点、仅依据随后转场推断额外下一步。修改既有因果段：先核对前一次提交/确认尚在继续的异步响应，短暂露原页与入口可见不足以证明再点击；保留新触点、按下变化或其它独立证据的真实操作，因果不明保留具体疑问。独立审查无P1/P2，此新因果段尚待正常原片验证。
- 当前946视频回归19.10秒、63后端、主链/改动编译/diff通过；最后因果提示编辑后320专项及63后端再次通过。日志/tmp/video-press-anchor-final{,-backend}.log及/tmp/video-causal-{final,backend}.log。仍没有完整可执行草稿或手机试跑通过，不把两窗观察成功当整片验收。

## fd413f4f交付及浏览器连接阻塞

- 用户12:43恢复新主机会话，能读取根目录文件列表。后续确认电脑控制层异常：坐标操作返回Computer Use server error -10005: noWindowsAvailable；原生粘贴pwd及Return无终端回显，ZIP选择后打开仍不可用。已请求用户确认Mac解锁、无屏保、Chrome前台及手动pwd回显，区分于登录或服务端问题。没有上传或部署成功输出；只发送了只读pwd，不能据此认定命令已执行。
- fd413f4f0bbed270b22c653a26d4af8f83fcde26已提交并推送origin/main及当前分支，保留其它已有脏文件；CODEX_STATE仅stage本次前缀。12:36公网health仍为ceb4bb1c，本批尚未部署。
- Chrome堡垒机会话反复T_514/T_1006，重连、页面重载及已登录desktop入口新建连接未恢复可操作终端；原生文件选择器选bundle和zip均显示打开不可用，不能据选择操作宣称上传成功。页面内容空白或画面不刷新。已请求用户手动恢复QA终端并确认可输入；无确认输出时不继续发部署命令。
- 12:33真实ADB device、Runner在线ready；12:35手机实际截图指定EP3-9888E0094F2A在线空闲、喷头25°C。current-platform/phone-preflight-fd413f4f.{json,png}；截图不依赖旧平台snapshot。没有创建/取消/物理打印或Runner试跑。
- /Users/adouceshi/Downloads/press-release.zip内含press-fix.bundle和deploy-press.sh，ZIP SHA256 3c70d95edfc281be07949c9df0da892787fd4b6322ab9032467feb7234508d28；bundle SHA256 32ec558aee293cfc2f2967d2a712a2e33b663c5cda432a146ec40b5b9b21fd67。脚本bash -n通过，解压目的/acceptance-current/press-release；先校验bundle、clean工作区、ff-only、精确fd413f4f再运行既有部署脚本。上传、解压、运行及正常v4原片生成仍未完成。
