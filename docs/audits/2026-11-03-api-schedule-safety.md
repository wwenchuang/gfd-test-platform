# 家用接口定时任务安全范围核查

核查时间：2026-11-03。对象：家用每日回归任务当前配置的 297 条基线。原定时任务暂时暂停，保留全部目标和配置。共享业务任务未修改。

以下分类依据请求与前置、清理步骤做保守筛查；不是接口逐项人工认证。尤其 GET 不一定只读，不能仅凭分类直接启用或批量执行。设备相关测试只允许 9888E0094F2A，且不能取消非本次创建的任务。

已实测 5 条基线即时回归两次各 5/5；图片优化独立调试 2/2，均核对清理。重新切片和队列仍依赖动态数据；图片生成 60 秒超时不保证外部服务一定及时响应。打印安全草稿只验证只读前置和失败门禁，尚未执行完整重打，也未采用为基线。

## 分类统计

- 已实测通过，仍需环境前置条件：6 条
- 涉及设备或打印：需逐项改造并核验：77 条
- 可能修改业务数据：需审查数据归属和清理：96 条
- 待读取类复核：不能自动视作安全：118 条

## 恢复标准

1. 只读候选逐项核实真实方法、前置和清理均无写操作，且无其他设备依赖。
2. 设备或打印用例固定指定设备，校验归属；发现已有任务时停止，不自动清除或取消。
3. 写入类用例仅操作本次明确创建的测试数据，失败也能按归属清理；账号注销、清空数据、设备升级等先从自动回归排除并保留原基线，直到隔离环境可用。
4. 对通过审查的用例即时执行并核对清理，再明确变更定时范围与覆盖数量；不得把未执行或跳过算作通过。

## 可能修改业务数据：需审查数据归属和清理

| 用例 | 请求 |
| --- | --- |
| AI 涂鸦创建 - 真实图片并自动清理 | POST /print3d/api/v1/aiGraffiti/create |
| AI 涂鸦删除 - 动态创建后删除 | POST /print3d/api/v1/aiGraffiti/delete |
| AI 涂鸦进度查询 - 动态任务 | GET /print3d/api/v1/aiGraffiti/query |
| HTML转PDF接口 - 基础正向流程 | POST /print3d/api/v1/fileConvert/h2p |
| 上报任务聊天步骤 - 基础正向流程 | POST /print3d/api/v1/learningAdventure/task/chat/step |
| 下载任务新增 - 新增后清理 | POST /print3d/api/v1/modelDownloads/add |
| 下载状态上报 - 动态已有记录 | POST /print3d/api/v1/modelDownloads/edit |
| 下载记录删除 - 动态创建后删除 | POST /print3d/api/v1/modelDownloads/drops |
| 体素模型创建 - 创建后自动清理 | POST /print3d/api/v1/voxelModel/create |
| 体素模型删除 - 创建后清理 | POST /print3d/api/v1/voxelModel/drop |
| 体素生成check - 生成中任务互斥场景 | GET /print3d/api/v1/voxelModel/check |
| 保存消息设置 - 基础正向流程 | POST /print3d/api/v1/message/saveMsgConfig |
| 全部已读 - 基础正向流程 - 响应数据驱动3-用户消息已读 | POST /print3d/api/v1/message/read-all |
| 分页获取报告周列表 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/report/historyWeeks |
| 创建3D任务 - 基础正向流程 | POST /print3dAI/api/v1/to3dModel |
| 创建图片建模V2 - 基础正向流程 | POST /print3d/api/v1/imageModel/createV2 |
| 创建建模任务 - 基础正向流程 | GET /print3dAI/api/v1/imageModel |
| 创建文字建模 V2 - 创建后自动取消 | POST /print3d/api/v1/createTextModel/textV2 |
| 创建系列分享 - 基础正向流程 | POST /print3d/api/v1/modelSeries/createShard |
| 创建邀请 - 基础正向流程 | POST /print3d/api/v1/invitation/create |
| 删除搜索记录 - 基础正向流程 | GET /print3d/api/v1/searchHistory/delete |
| 删除搜索记录byId - 基础正向流程 | GET /print3d/api/v1/searchHistory/deleteById |
| 删除生成记录 - 等待最终记录并彻底清理 | POST /print3d/api/v1/createModel/delete |
| 删除用户涂鸦模型 - 基础正向流程 | POST /print3d/api/v1/userGraffitiModel/delete |
| 刷新token接口(鉴权) - 基础正向流程 | GET /print3d/api/v1/users/getNewToken |
| 匹配模型素材 - 基础正向流程 | POST /print3d/api/v1/aiModeling/text/matchModelAssets |
| 匹配模型素材V2(只匹配IP) - 基础正向流程 | POST /print3d/api/v1/aiModeling/text/matchModelAssetsV2 |
| 发送被动消息（开发测试用） - 基础正向流程 | POST /print3d/api/v1/message/sendPassiveMsg |
| 发送验证码(废弃) - 基础正向流程 | GET /print3d/api/v1/auth/sendSms |
| 取消收藏 - 基础正向流程 | POST /print3d/api/v1/collection/cancel |
| 取消文字建模 - 创建后取消并兜底 | GET /print3d/api/v1/createTextModel/cancel |
| 叠叠乐像素模型库SN列表查询 - 基础正向流程 - 响应数据驱动2 | POST /print3d/api/v1/modelMaterials/pixelBySns |
| 固件更新说明 - 基础正向流程 - code200业务成功断言 | POST /pmc/api/v1/iot/ota/fireware/version/released |
| 图片匹配模型素材 - 基础正向流程 | POST /print3d/api/v1/aiModeling/image/matchModelAssets |
| 图片建模取消 - 动态创建后取消 | GET /print3d/api/v1/imageModel/cancel |
| 图生图创建 - 真实图片并取消 | POST /print3d/api/v1/imageModel/generate3dImage |
| 学习冒险任务提交 - 可重复生命周期 | POST /print3d/api/v1/learningAdventure/task/submit |
| 导入模型 V2 - 动态 STL 并按任务清理 | POST /print3d/api/v1/model/importV2 |
| 常见问题反馈 - 动态问题 | POST /print3d/api/v1/question/feedback |
| 开始冒险任务 - 基础正向流程 | POST /print3d/api/v1/learningAdventure/task/start |
| 我的下载查询 - 基础正向流程 | POST /print3d/api/v1/modelDownloads/page |
| 我的收藏列表 - 基础正向流程 | POST /print3d/api/v1/collection/page |
| 手机号验证码验证 - 基础正向流程 | POST /print3d/api/v1/auth/checkCode |
| 批量删除用户涂鸦模型 - 基础正向流程 | POST /print3d/api/v1/userGraffitiModel/deleteBatch |
| 批量取消系列收藏 - 基础正向流程 | POST /print3d/api/v1/modelSeries/cancelCollection |
| 批量查询模型接口详情 - 基础正向流程 | POST /print3d/api/v1/model/detailByModelSns |
| 接受邀请 - 基础正向流程 | POST /print3d/api/v1/invitation/invited |
| 提交画板AI美化 - 基础正向流程 | POST /print3d/api/v1/userGraffitiModel/beautify/submit |
| 提交课程记录 - 基础正向流程 | POST /print3d/api/v1/survey/add |
| 搜索空结果 AI 提示词推荐 - 基础正向流程 | POST /print3d/api/v1/model/emptyPromptRecommend |
| 文字建模聊天 - 基础正向流程 | POST /print3d/api/v1/aiModeling/text/chat |
| 新增用户涂鸦模型 - 基础正向流程 | POST /print3d/api/v1/userGraffitiModel/add |
| 旧版文字建模 - API Test 一次性 | POST /print3d/api/v1/createTextModel/text |
| 暂缓展示 App 好评弹窗 - 基础正向流程 | POST /print3d/api/v1/appRating/postpone |
| 查询互联网图片 - 基础正向流程 | POST /print3d/api/v1/aiModeling/text/searchImages |
| 查询任务是否可提交 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/learningAdventure/task/canSubmit |
| 查询体素详情 - 基础正向流程 | GET /print3d/api/v1/voxelModel/query |
| 查询当前任务进度 - 基础正向流程 | GET /print3d/api/v1/createModel/processModels |
| 查询当前生成的任务 - 基础正向流程 | GET /print3d/api/v1/createTextModel/queryCurrentModel |
| 查询文字建模进度（V1/V2共用） - 基础正向流程 - 响应数据驱动 | GET /print3d/api/v1/createTextModel/query |
| 查询是否提交过 - 基础正向流程 | POST /print3d/api/v1/survey/get |
| 标记反馈已读 - 基础正向流程 | POST /print3d/api/v1/feedback/user-read |
| 校验是否有权限接口(新) - 基础正向流程 | POST /print3d/api/v1/auth/checkAppAuth |
| 核心链路-DeepSeek CAD创建查询删除 | POST /print3d/api/v1/aiModeling/cad/create |
| 核心链路-下载记录创建查询清理 | POST /print3d/api/v1/modelDownloads/add |
| 检测图片打印可行性 - 基础正向流程 | POST /print3d/api/v1/aiModeling/image/checkPrintability |
| 模型下载 STL - 动态真实模型地址 | POST /print3dParam/api/v1/library/download |
| 模型下载信息(测试用） - 基础正向流程 | POST /print3dParam/api/v1/library/list |
| 模型举报 - API Test 一次性 | POST /print3d/api/v1/modelComplaints/add |
| 模型愿望新增 - 基础正向流程 | POST /print3d/api/v1/modelExpectations/add |
| 模型操作上报接口 - 基础正向流程 | POST /print3d/api/v1/modelUserLog/action |
| 模型生产图片 Copy - 基础正向流程 | POST /print3dParam/api/v1/loadModel |
| 模型生成记录 - 基础正向流程 | GET /print3d/api/v1/createModel/page |
| 添加修改收藏 - 基础正向流程 | POST /print3d/api/v1/collection/add |
| 添加收藏 - 动态有效模型闭环 | POST /print3d/api/v1/collection/add |
| 添加系列收藏 - 基础正向流程 | POST /print3d/api/v1/modelSeries/addCollection |
| 清空用户学习法数据 - 基础正向流程 | POST /print3d/api/v1/learningAdventure/user/clear |
| 点击最新模型接口 - 基础正向流程 | GET /print3d/api/v1/model/clickLatestModel |
| 生肖印章 STL - 真实模板参数 | POST /print3dParam/api/v1/model |
| 用户信息修改 - 基础正向流程 | POST /print3d/api/v1/users/updateInfo |
| 用户注销 - 基础正向流程 | POST /print3d/api/v1/users/logoff |
| 用户涂鸦作品编辑 - 修改后恢复 | POST /print3d/api/v1/userGraffitiModel/edit |
| 精确匹配模型素材 - 基础正向流程 | POST /print3d/api/v1/aiModeling/text/exactMatchModelAssets |
| 继续文字建模 - 创建后继续并取消 | GET /print3d/api/v1/createTextModel/continue |
| 获取刷新模型数量 最新模型数量 - 基础正向流程 | GET /print3d/api/v1/model/getUpdateModelCount |
| 获取历史成长报告 - 无记录错误契约 | GET /print3d/api/v1/learningAdventure/report/history |
| 获取当前周成长报告 - 当前周响应契约 | GET /print3d/api/v1/learningAdventure/report/current |
| 获取章节奖励 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/learningAdventure/reward/chapter |
| 获取结业报告 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/report/graduation |
| 获取能力成长趋势 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/report/abilityTrend |
| 获取腾讯云临时token接口 - 基础正向流程 | POST /print3d/api/v1/tencent/sts/federationToken |
| 解密测试（测试用） - 基础正向流程 | POST /print3d/api/v1/auth/decrypt |
| 记录当前问卷已进入 - 基础正向流程 | POST /print3d/api/v1/survey/entry/click |
| 走马灯接口 - 基础正向流程 | GET /print3d/api/v1/createTextModel/queryLastSuccessModel |
| 选择当前冒险任务 - 基础正向流程 | POST /print3d/api/v1/learningAdventure/task/current |
| 验证滑块验证码 - 基础正向流程 | POST /print3d/api/v1/auth/verifyCaptcha |

## 涉及设备或打印：需逐项改造并核验

| 用例 | 请求 |
| --- | --- |
| AI摆放提示接口 - 基础正向流程 | POST /print3d/api/v1/printJob/modelPlacementAiTip |
| EPOne actionRequestId 含非法字符（违反 pattern） | POST /api/v1/devices/editFt |
| temperature 超出合法范围（190~220） | POST /api/v1/devices/editFt |
| 今日打印推荐 - 当前选中设备 | GET /print3d/api/v1/model/todayPrintRecommend |
| 关闭已完成打印任务卡片 - 基础正向流程 | POST /print3d/api/v1/printTaskQueue/dismiss |
| 分页查询京东之家体验模型 | GET /print3d/api/v1/jdHome/model/page |
| 切片打印任务查询 - 动态打印记录 | POST /print3d/api/v1/printJob/query |
| 切片详情 - 基础正向流程 | GET /pds/api/v1/slice-param-config/sliceInfo |
| 创建设备分享 - 基础正向流程 | POST /print3d/api/v1/userDevices/createShard |
| 判断是否重新切片 - 动态有效模型场景 | GET /print3d/api/v1/printJob/checkReslice |
| 历史设备信息清理（物理删除慎用） - 基础正向流程 | POST /print3d/api/v1/distbution/devices/clean |
| 发送消息 - 基础正向流程 | GET /pmc/api/v1/iot/topicMsg |
| 合盘分盘详情 - 不存在队列项错误契约 | GET /print3d/api/v1/printTaskQueue/itemDetail |
| 图片信息 - 基础正向流程 | GET /print3d/api/v1/calibration/imgInfo |
| 待打印切片任务 - 当前设备响应契约 | GET /print3d/api/v1/printJob/currentSliceJob |
| 所有设备耗材信息 - 基础正向流程 | GET /print3d/api/v1/qidi/filament/tree |
| 打印任务进度 - 基础正向流程 - 响应数据驱动2 | POST /print3d/api/v1/qidi/printInfo |
| 打印作业列表 - 基础正向流程 - 响应数据驱动2 | GET /print3d/api/v1/printJob/page |
| 打印作业详情 - 基础正向流程 | GET /print3d/api/v1/printJob/detail |
| 打印反馈 - 已反馈任务重复提交场景 | POST /print3d/api/v1/printJob/feedbackList |
| 打印是否满意接口 - 基础正向流程 | POST /print3d/api/v1/printJob/satisfied |
| 打印结果分享 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/open/printShard |
| 打印进度 - 基础正向流程 | GET /pmc/api/v1/deviceCmd/printStatus |
| 新手必学列表 - 基础正向流程 | GET /print3d/api/v1/guidance/list |
| 新手必学完成标记 - 动态已完成项 | POST /print3d/api/v1/guidance/mark |
| 新手必学完成详情 - 基础正向流程 | GET /print3d/api/v1/guidance/finishInfo |
| 新手必学是否完成 - 基础正向流程 | GET /print3d/api/v1/guidance/finishMark |
| 新手必学详情 - 基础正向流程 | GET /print3d/api/v1/guidance/info |
| 旋钮设置 - 基础正向流程 | POST /print3d/api/v1/knobSetting/edit |
| 旋钮设置查询 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/knobSetting/query |
| 旋钮设置重置 - 基础正向流程 | POST /print3d/api/v1/knobSetting/reset |
| 最后一次打印结束任务弹窗 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/printJob/lastFinishJob |
| 查询我的打印申请(用户) | GET /print3d/api/v1/jdHome/print/application/myPage |
| 查询接口详情 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/model/detail |
| 查询是否共享设备 | GET /print3d/api/v1/jdHome/device/isSharedDevice |
| 查询模型编辑高级切片参数配置 - 基础正向流程 - 稳定正向数据 - 真实响应数据联动补跑 - 真实作品设备关联-数值比例 | GET /print3d/api/v1/devices/advancedSliceParamConfig |
| 查询组件详情 - 固定设备机型动态组件 | GET /print3d/api/v1/model/detailByComponentSn |
| 查询设备列表 - 基础正向流程 | GET /print3d/api/v1/devices/list |
| 查询设备详情 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/info |
| 核心链路-中文搜索结果与模型详情 | GET /print3d/api/v1/model/search |
| 核心链路-打印历史与详情查询 | GET /print3d/api/v1/printJob/detail |
| 核心链路-模型浏览与收藏闭环 | POST /print3d/api/v1/collection/add |
| 核心链路-真实打印创建查询取消（4F2A） | POST /print3d/api/v1/printJob/createReprint |
| 核心链路-设备状态与耗材能力 | GET /print3d/api/v1/devices/status |
| 根据二维码查询设备耗材温度 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/ftByBarcode |
| 根据原始SN查询设备及新手引导视频 - 基础正向流程 | GET /print3d/api/v1/devices/infoByOriSn |
| 模型分页查询V2 - 夜间节日筛选契约 | GET /print3d/api/v1/model/simplePage |
| 模型切片分页查询 - 动态设备查询参数 | POST /print3d/api/v1/printJob/page |
| 模型制作指导 - 官方模型查询场景 | GET /print3d/api/v1/model/modelDirection |
| 模型盲盒接口 - 基础正向流程 | GET /print3d/api/v1/model/blindBox |
| 点击正品耗材提示 - 基础正向流程 | GET /print3d/api/v1/devices/clickMaterialDeplete |
| 猜你想问 - 基础正向流程 | GET /print3d/api/v1/serviceCenter/guessYouMayAsk |
| 生成模型详情 - 基础正向流程 - 响应数据驱动 | GET /print3d/api/v1/createModel/detail |
| 耗材列表 - 基础正向流程 | GET /print3d/api/v1/qidi/filament/slots/current |
| 获取obstoken - 基础正向流程 | GET /print3d/api/v1/qidi/token |
| 获取去购买商城链接 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/gotoBuy |
| 获取设备状态 - 基础正向流程 - 响应数据驱动 | GET /pmc/api/v1/deviceCmd/deviceStatus |
| 设备分享人查询 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/userDevices/queryShard |
| 设备分享删除 - 基础正向流程 | POST /print3d/api/v1/userDevices/delShard |
| 设备实时状态 - 基础正向流程 | POST /print3d/api/v1/devices/deviceState |
| 设备工作详情 - 固定设备动态打印记录 | GET /print3d/api/v1/devices/workInfo |
| 设备手动升级 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/upgrade |
| 设备查询用户列表 - 固定设备只读验证 | GET /print3d/api/v1/qidi/users |
| 设备查询用户列表 - 基础正向流程 | GET /print3d/api/v1/qidi/users |
| 设备状态 - 基础正向流程 | GET /print3d/api/v1/devices/status |
| 设备状态上报 - 基础正向流程 | POST /pmc/api/v1/open/qidiReport |
| 设备状态上报 Copy - 基础正向流程 | POST /pmc/api/v1/open/report |
| 设备组件重置 - 基础正向流程 | POST /print3d/api/v1/devices/comReset |
| 设备耗材信息 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/filamentInfo |
| 设备耗材温度信息树形列表 - 基础正向流程 | GET /print3d/api/v1/devices/ftTree |
| 设备耗材颜色自动匹配 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/mulColorMatch |
| 设备进料标记 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/devices/deviceStateMark |
| 设备重命名 - 动态当前设备 | POST /print3d/api/v1/devices/rename |
| 设备默认用户信息 - 固定设备只读验证 | GET /print3d/api/v1/qidi/user |
| 选中设备 - 基础正向流程 | POST /print3d/api/v1/devices/changeSelected |
| 重新打印判断 - 创建后取消并清理 | POST /print3d/api/v1/printJob/createReprint |
| 首页默认设备 - 基础正向流程 | GET /print3d/api/v1/devices/hpDevice |

## 已实测通过，仍需环境前置条件

| 用例 | 请求 |
| --- | --- |
| OpenSCAD 转 STL - 动态模板参数 | POST /print3d/api/v1/model/scad2stl |
| 一键优化生成图片 - 等待就绪并双任务清理 | POST /print3d/api/v1/aiModeling/image/optimizeAndGenerate |
| 判断重新打印是否重新切片 - 已有切片温度一致场景 | GET /print3d/api/v1/printJob/checkRePrintReSlice |
| 查询当前打印任务队列 - 当前模型未入队场景 | GET /print3d/api/v1/printTaskQueue/current |
| 核心链路-CAD产物导入处理与清理 | POST /print3d/api/v1/model/importV2 |
| 设备耗材温度信息列表 - 基础正向流程 | GET /print3d/api/v1/devices/ftList |

## 待读取类复核：不能自动视作安全

| 用例 | 请求 |
| --- | --- |
| CAD任务分页查询 - 当前账号 | GET /print3d/api/v1/aiModeling/cad/page |
| CAD生成状态 - 当前账号 | GET /print3d/api/v1/aiModeling/cad/hasGeneratingTask |
| H5分享页面查询模型详情（不鉴权） - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/share/modelDetail |
| H5分享页面热门模型（不鉴权） - 基础正向流程 | GET /print3d/api/v1/share/activeModel |
| H5分享页面用户模型详情（不鉴权） Copy - 基础正向流程 - 稳定正向数据 - 响应数据驱动 | GET /print3d/api/v1/share/modelInfo |
| feed - 基础正向流程 | GET /print3d/api/v1/model/feed |
| getDeviceAndSliceType - 基础正向流程 | GET /pds/api/v1/slice-param-config/device-and-slice-type |
| related - 基础正向流程 | GET /print3d/api/v1/model/related |
| 一级分类查询 - 基础正向流程 | GET /print3d/api/v1/generalCategory/graphicGraffiti/parent |
| 上传模型查询 - 基础正向流程 | GET /print3d/api/v1/model/importModels |
| 下载详情 - 基础正向流程 - 响应数据驱动 | GET /print3d/api/v1/modelDownloads/info |
| 临摹涂鸦模型库查询 - 基础正向流程 | GET /print3d/api/v1/modelMaterials/tracingPage |
| 分类树查询 - 基础正向流程 | GET /print3d/api/v1/generalCategory/graphicGraffiti/tree |
| 分页搜索CAD组件 - 基础正向流程 - 真实响应数据联动补跑 | GET /print3d/api/v1/userCadComponent/page |
| 分页查询切片参数配置 - 基础正向流程 | GET /pds/api/v1/slice-param-config/page |
| 分页查询生成记录 - 基础正向流程 | GET /print3d/api/v1/voxelModel/page |
| 分页查询用户涂鸦模型 - 基础正向流程 | GET /print3d/api/v1/userGraffitiModel/page |
| 区查询 - 基础正向流程 | GET /print3d/api/v1/region/areas |
| 叠叠乐像素模型库查询 - 基础正向流程 | GET /print3d/api/v1/modelMaterials/pixelPage |
| 固件更新说明 Copy - 基础正向流程 - 响应数据驱动 | GET /pmc/api/v1/iot/ota/firmware-release-history |
| 图形涂鸦模型库查询 - 基础正向流程 | GET /print3d/api/v1/modelMaterials/graphicPage |
| 图生图查询进度（轮询） - 基础正向流程 - 响应数据驱动 | GET /print3d/api/v1/imageModel/query3dImage |
| 大家都在做 - 基础正向流程 | GET /print3d/api/v1/aiModeling/everyoneDoing |
| 子分类查询 - 基础正向流程 | GET /print3d/api/v1/generalCategory/children |
| 完成任务跑马灯提示 - 基础正向流程 | GET /print3d/api/v1/imageModel/queryLastSuccessModel |
| 导入模型状态 - 动态导入记录 | GET /print3d/api/v1/model/importV2/status |
| 市查询 - 基础正向流程 | GET /print3d/api/v1/region/cities |
| 常见分类下问题列表 - 基础正向流程 | GET /print3d/api/v1/question/list |
| 常见问题分类树 - 不传设备筛选 | GET /print3d/api/v1/questionCategory/tree |
| 当前模型所属系列查询 - 基础正向流程 | GET /print3d/api/v1/modelSeries/queryList |
| 我收藏模型系列 - 基础正向流程 | GET /print3d/api/v1/modelSeries/myCollection |
| 我的作品 - 基础正向流程 | GET /print3d/api/v1/aiModeling/myWorks |
| 我的徽章 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/reward/my |
| 我的组件聚合列表 - 基础正向流程 | GET /print3d/api/v1/voxelModel/componentPage |
| 我的组件聚合列表 - 基础正向流程 | GET /print3d/api/v1/voxelModel/componentPage |
| 推荐模型 - 基础正向流程 | GET /print3d/api/v1/model/hot |
| 搜索 - 基础正向流程 | GET /print3d/api/v1/model/search |
| 文字建模推荐提示词 - 基础正向流程 | GET /print3d/api/v1/aiModeling/text/recommendPrompts |
| 最新模型 - 基础正向流程 | GET /print3d/api/v1/model/latest |
| 有赞商品连接配置接口 - 基础正向流程 | GET /print3d/api/v1/open/webPage |
| 未命名接口 - 基础正向流程 | GET /print3dAI/api/v1/queueInfo |
| 查询3D任务 - 基础正向流程 | GET /print3dAI/api/v1/queryTo3DDetail |
| 查询CAD组件分类 - 基础正向流程 | GET /print3d/api/v1/userCadComponentCategory/list |
| 查询一级反馈分类（Print3DService） - 基础正向流程 | GET /print3d/api/v1/feedback/category/first |
| 查询任务 - 基础正向流程 | GET /print3dAI/api/v1/queryImageDetail |
| 查询全部未读信息数量 - 基础正向流程 | GET /print3d/api/v1/message/queryUnreadCount |
| 查询印章模版 - 基础正向流程 | GET /print3d/api/v1/openscadTemplate/seals |
| 查询反馈未读数 - 基础正向流程 | GET /print3d/api/v1/feedback/user-unread-count |
| 查询图片建模进度（V1/V2共用） - 基础正向流程 - 响应数据驱动 | GET /print3d/api/v1/imageModel/query |
| 查询奖励任务进度 - 基础正向流程 | GET /print3d/api/v1/invitation/awardProgress |
| 查询当前的任务 - 基础正向流程 | GET /print3d/api/v1/imageModel/queryCurrentModel |
| 查询我的反馈列表 - 基础正向流程 | GET /print3d/api/v1/feedback/user-list |
| 查询我的总积分 - 基础正向流程 | GET /print3d/api/v1/point/total |
| 查询打印完成人数 - 基础正向流程 | GET /print3d/api/v1/invitation/completedPrintCnt |
| 查询搜索记录 - 基础正向流程 | GET /print3d/api/v1/searchHistory/query |
| 查询是否展示 App 好评弹窗 - 基础正向流程 | GET /print3d/api/v1/appRating/shouldShow |
| 查询最新生成记录 - 基础正向流程 | GET /print3d/api/v1/voxelModel/topSuccess |
| 查询标牌模版 - 基础正向流程 | GET /print3d/api/v1/openscadTemplate/query |
| 查询模型精简状态 - 基础正向流程 - 稳定正向数据 - 响应数据驱动 - 轻量化状态-/print3d/api/v1/imageModel/query | GET /print3d/api/v1/model/lightweight/status |
| 查询消息分类 - 基础正向流程 | GET /print3d/api/v1/message/categories |
| 查询消息设置 - 基础正向流程 | GET /print3d/api/v1/message/queryMsgConfig |
| 查询消息详情 - 基础正向流程 | GET /print3d/api/v1/message/detail |
| 查询画板AI美化 - 基础正向流程 - 稳定正向数据 - 响应数据驱动 | GET /print3d/api/v1/userGraffitiModel/beautify/query |
| 查询科目下年级 - 基础正向流程 | GET /print3d/api/v1/codeBooks/queryGrades |
| 查询科目类别 - 基础正向流程 | GET /print3d/api/v1/codeBooks/querySubjects |
| 查询端侧Banner列表 - 基础正向流程 | GET /print3dOperationCenter/api/v1/banner/list |
| 查询邀请记录 - 基础正向流程 | GET /print3d/api/v1/invitation/invitedRecord |
| 核心链路-学习冒险地图与任务详情 | GET /print3d/api/v1/learningAdventure/task/detail |
| 核心链路-账户会话与用户资料 | GET /print3d/api/v1/users/info |
| 根据关键词获取推荐模型 - 基础正向流程 | GET /print3d/api/v1/model/recommend |
| 根据父节点ID查询子分类 - 基础正向流程 | GET /print3d/api/v1/questionCategory/children |
| 模型分组树状结构 - 基础正向流程 | GET /print3d/api/v1/modelCategory/tree |
| 模型分页查询 - 基础正向流程 | GET /print3d/api/v1/model/page |
| 模型列表 - 基础正向流程 | GET /print3dParam/api/v1/library/list |
| 模型列表下载 - 动态真实模型地址 | GET /print3dParam/api/v1/library/download |
| 模型系列分页查询 - 基础正向流程 | GET /print3d/api/v1/modelSeries/page |
| 模型详情 - 基础正向流程 | GET /print3d/api/v1/modelMaterials/info |
| 消息列表 - 当前用户真实消息 | GET /print3d/api/v1/message/list |
| 热门搜索 - 基础正向流程 | GET /print3d/api/v1/searchHistory/hot |
| 热门模型 - 基础正向流程 | GET /print3d/api/v1/model/active |
| 用户详情 - 基础正向流程 | GET /print3d/api/v1/users/info |
| 省及城市接口 - 基础正向流程 | GET /print3d/api/v1/region/proCities |
| 省接口 - 基础正向流程 | GET /print3d/api/v1/region/provinces |
| 积分兑换商品分页 - 基础正向流程 | GET /print3d/api/v1/point/exchange/product/page |
| 积分明细分页 - 基础正向流程 | GET /print3d/api/v1/point/flow/page |
| 端侧课程详情 - 基础正向流程 | GET /print3d/api/v1/course/info |
| 系列详情 - 基础正向流程 | GET /print3d/api/v1/modelSeries/detail |
| 系列详情分享 - 基础正向流程 | GET /print3d/api/v1/open/seriesShard |
| 背包徽章 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/reward/my/backpack |
| 获取一级分组 - 基础正向流程 | GET /print3d/api/v1/modelCategory/firstLevel |
| 获取一级分组下的二级分组 - 基础正向流程 | GET /print3d/api/v1/modelCategory/children |
| 获取世界地图配置 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/map/world |
| 获取二级分组 - 基础正向流程 | GET /print3d/api/v1/modelCategory/secondLevel |
| 获取冒险code - 基础正向流程 | GET /print3d/api/v1/learningAdventure/current |
| 获取冒险任务详情 - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/learningAdventure/task/detail |
| 获取决战魔王信息 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/battle/info |
| 获取分享信息 - 基础正向流程 | GET /print3d/api/v1/course/share |
| 获取加密公钥 - 基础正向流程 | GET /print3d/api/v1/auth/publicKey |
| 获取勋章任务详情 | GET /print3d/api/v1/badge/task/detail |
| 获取勋章总览 - 基础正向流程 | GET /print3d/api/v1/badge/overview |
| 获取字体列表 - 基础正向流程 | GET /print3d/api/v1/fonts/list |
| 获取当前问卷入口 - 基础正向流程 | GET /print3d/api/v1/survey/entry/current |
| 获取模型库查询条件 - 基础正向流程 | GET /print3d/api/v1/model/libraryMetaOptions |
| 获取滑块信息接口 - 基础正向流程 | GET /print3d/api/v1/auth/generateCaptcha |
| 获取用户主线进度 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/user/progress |
| 获取用户涂鸦模型详情 - 当前有效记录场景 | GET /print3d/api/v1/userGraffitiModel/detail |
| 获取课程介绍H5 - 基础正向流程 | GET /print3d/api/v1/course/introduce |
| 设备上报日志AI分析 - 基础正向流程 | GET /print3d/manage/v1/diagnosis/pageData |
| 设备分享查询 Copy - 基础正向流程 | GET /print3d/api/v1/open/deviceShard |
| 课程TOP列表(公开) - 基础正向流程 | GET /print3d/api/v1/open/courseTop |
| 课程目录(公开) - 基础正向流程 | GET /print3d/api/v1/open/courseList |
| 课程目录分页 - 基础正向流程 | GET /print3d/api/v1/course/pageList |
| 课程详情(公开) - 基础正向流程 - 稳定正向数据 | GET /print3d/api/v1/open/courseInfo |
| 课程首页TOP - 基础正向流程 | GET /print3d/api/v1/course/top |
| 邀请分享详情 - 基础正向流程 | GET /print3d/api/v1/open/inviteInfo |
| 问题详情 - 基础正向流程 | GET /print3d/api/v1/question/detail |
| 首页地图信息 - 基础正向流程 | GET /print3d/api/v1/learningAdventure/map/home |
| 首页推荐模型系列 - 基础正向流程 | GET /print3d/api/v1/modelSeries/recommendList |
