# Apifox 家用接口日常回归梳理

数据来源：Apifox 3D 项目主分支；2026-11-04 平台预览。原活动来源对比得到新增 171、变更 42、移除 10；同一方法和路径同时出现新增与移除时可能只是接口标识变化，不能简单计为新增业务接口。

已将 28 条查询接口加入原家用日常任务，覆盖 AI 建模、用户课程、模型库、成长报告、勋章、创意广场和工具首页；每条实际调试三次通过，保留 HTTP 状态、业务码和响应结构断言。广场最新曾出现一次 worker OperationalError，随后三次调试和组合回归通过；底层异常原因未确认。

未加入项目是待评估，并非已认定不适合；需补充有效动态参数、验证读写副作用或设计创建清理生命周期。原生产环境和原任务配置保留。

## 新增接口预览清单

| 方法 | 路径 | 当前处理 |
| --- | --- | --- |
| GET | `/eol/api/v1/app/photos` | 待评估：需要动态业务参数 |
| POST | `/eol/api/v1/app/photos` | 待评估：写操作和数据生命周期 |
| POST | `/eol/api/v1/app/photos/upload-ticket` | 待评估：写操作和数据生命周期 |
| POST | `/eol/api/v1/auth/login` | 待评估：写操作和数据生命周期 |
| GET | `/eol/api/v1/devices/by-mac` | 待评估：需要动态业务参数 |
| GET | `/pmc/api/v1/device/info` | 待评估：其他查询场景 |
| POST | `/pmc/api/v1/deviceCmd/cmdAndGet` | 待评估：写操作和数据生命周期 |
| POST | `/print3d-ash/api/v1/client/auth` | 待评估：写操作和数据生命周期 |
| GET | `/print3d-ash/api/v1/device/command/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d-ash/api/v1/device/command/send` | 待评估：写操作和数据生命周期 |
| GET | `/print3d-ash/api/v1/device/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d-ash/api/v1/device/page` | 待评估：其他查询场景 |
| GET | `/print3d-ash/api/v1/device/report/page` | 待评估：需要动态业务参数 |
| GET | `/print3d-ash/api/v1/device/status` | 待评估：需要动态业务参数 |
| POST | `/print3d-ash/api/v1/device/status/refresh` | 待评估：写操作和数据生命周期 |
| POST | `/print3d-ash/api/v1/open/report` | 待评估：写操作和数据生命周期 |
| POST | `/print3d-ash/api/v1/open/stateChange` | 待评估：写操作和数据生命周期 |
| POST | `/print3d-mcp/mcp` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/aiModeling/everyoneDoing` | 同路径接口标识变化，非新增路径 |
| POST | `/print3d/api/v1/aiModeling/session/batchDelete` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/aiModeling/session/chat` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/aiModeling/session/create` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/aiModeling/session/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/aiModeling/session/image/cancel` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/aiModeling/session/image/create` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/aiModeling/session/image/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/aiModeling/session/image/retry` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/aiModeling/session/messages` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/aiModeling/session/model/create` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/aiModeling/session/model/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/aiModeling/session/page` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/aiModeling/session/reference/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/aiModeling/session/reference/import` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/aiModeling/theme/list` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/aiModeling/theme/welcome/audio/detail` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/aiModeling/voice/audio/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/aiModeling/voice/preference/detail` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/aiModeling/voice/preference/save` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/badge/acquired/page` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/badge/home-progress` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/badge/overviewV2` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/badge/task/detailV2` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/calibration/doLeveling` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/clientFeature/record` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/clientFeature/status` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/cloisonne/config` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/cloisonne/generate` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/cloisonne/home` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/cloisonne/line-art/tasks` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/cloisonne/line-art/tasks/3db44b40ba064a668eeca97d0f6479c2` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/cloisonne/line-art/tasks/{taskSn}` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/cloisonne/records` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/cloisonne/records/save` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/cloisonne/records/{recordSn}` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/cloisonne/uploads` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/entry/tags` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/course/science/badge/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/course/science/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/course/science/explore` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/science/home` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/course/science/stage/complete` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/science/work/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/course/science/work/gallery` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/course/science/work/like` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/science/work/mine` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/course/science/work/share` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/steam/config` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/course/sync/chest/open` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/sync/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/course/sync/learning/complete` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/sync/learning/recent` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/course/sync/learning/start` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/sync/list` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/course/sync/practice/progress` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/course/sync/practice/start` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/course/sync/stars/summary` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/course/sync/subjects` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/createModel/works/delete` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/deviceSettings/resetDefaults` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/devices/deviceState` | 待评估：其他查询场景 |
| GET | `/print3d/api/v1/devices/deviceStateBySn` | 待评估：其他查询场景 |
| GET | `/print3d/api/v1/devices/ftSeriesTree` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/epone/actions/homingSensitivity/query` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/epone/actions/homingSensitivity/set` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/epone/actions/nozzleTemperature/query` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/epone/actions/nozzleTemperature/set` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/epone/actions/offset/adjust` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/epone/actions/offset/query` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/epone/actions/testPrint` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/generalCategory/modelLibraryTheme/tree` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/growth/ability/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/growth/behavior/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/growth/behavior/page` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/growth/behavior/prompt/consume` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/growth/home` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/growth/report/month` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/growth/report/week` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/level/magicDoodle/badge/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/level/magicDoodle/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/level/magicDoodle/list` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/level/magicDoodle/story/experience` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/level/magicDoodle/task/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/level/magicDoodle/task/start` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/level/magicDoodle/task/submit` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/materialBatch/query` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/model/active` | 同路径接口标识变化，非新增路径 |
| GET | `/print3d/api/v1/model/feed` | 同路径接口标识变化，非新增路径 |
| GET | `/print3d/api/v1/model/homeFeed` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/model/homeFeed/query` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/model/latest` | 同路径接口标识变化，非新增路径 |
| GET | `/print3d/api/v1/model/libraryFilterOptions` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/model/share` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/model/share/page` | 已加入，三次调试通过 |
| DELETE | `/print3d/api/v1/model/share/{shareSn}` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/model/simplePage` | 同路径接口标识变化，非新增路径 |
| GET | `/print3d/api/v1/modelSeries/video/view` | 待评估：需确认副作用或业务语义 |
| GET | `/print3d/api/v1/modelStory/list` | 待评估：其他查询场景 |
| GET | `/print3d/api/v1/plaza/content/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v1/plaza/content/favorite` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/plaza/content/favorite/page` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/plaza/content/latest` | 已加入，三次调试通过 |
| GET | `/print3d/api/v1/plaza/content/recommend` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/plaza/content/view` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/qidi/realVideo/secret` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/slice-param-config/conf-infos` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/stamp/home` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/stamp/line-art/optimize` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/api/v1/stamp/line-art/tasks/19d54cb19cd94e688d47ccfc0b5d23f8` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/stamp/records/5668421c5de04158ab6929cfe77a2431` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/stamp/records/69773142afea48ec98f290297b27449a/status` | 待评估：需要动态业务参数 |
| GET | `/print3d/api/v1/users/info` | 同路径接口标识变化，非新增路径 |
| GET | `/print3d/api/v1/users/modelPreference/options` | 已加入，三次调试通过 |
| POST | `/print3d/api/v1/users/profileSetup/shown` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v1/users/updateInfo` | 同路径接口标识变化，非新增路径 |
| GET | `/print3d/api/v2/cloisonne/config` | 待评估：需要动态业务参数 |
| POST | `/print3d/api/v2/scan/course/unlock` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v2/scan/kit/targets` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v2/scan/resolve` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/api/v2/scan/science/unlock` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/aiGenerationQuota/reset` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/aiModeling/theme/changeStatus` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/aiModeling/theme/create` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/manage/v1/aiModeling/theme/detail` | 待评估：需要动态业务参数 |
| GET | `/print3d/manage/v1/aiModeling/theme/page` | 待评估：其他查询场景 |
| POST | `/print3d/manage/v1/aiModeling/theme/update` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/course/science/whitelist/batch-add` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/course/science/whitelist/delete` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/course/science/whitelist/import` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/manage/v1/course/science/whitelist/page` | 待评估：其他查询场景 |
| POST | `/print3d/manage/v1/course/science/whitelist/save` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/course/science/whitelist/status` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/course/science/work/audit` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/manage/v1/course/sync/badge/config` | 待评估：需确认副作用或业务语义 |
| POST | `/print3d/manage/v1/course/sync/badge/config/save` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/manage/v1/course/sync/badge/model/parameters` | 待评估：需要动态业务参数 |
| POST | `/print3d/manage/v1/modelTag/backfill` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/plaza/content/add` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/manage/v1/plaza/content/detail` | 待评估：需要动态业务参数 |
| POST | `/print3d/manage/v1/plaza/content/search/sync` | 待评估：写操作和数据生命周期 |
| POST | `/print3d/manage/v1/plaza/content/sync` | 待评估：写操作和数据生命周期 |
| GET | `/print3d/manage/v1/plaza/content/target/page` | 待评估：需要动态业务参数 |
| POST | `/print3d/manage/v1/plaza/content/update` | 待评估：写操作和数据生命周期 |
| POST | `/print3dBlender/api/v1/seal/svg/printability` | 待评估：写操作和数据生命周期 |
| POST | `/print3dCamera/api/v2/device/camera/wan-sessions/publish-credentials` | 待评估：写操作和数据生命周期 |
| POST | `/print3dCamera/api/v2/internal/camera/wan-sessions/heartbeat` | 待评估：写操作和数据生命周期 |
| POST | `/print3dCamera/api/v2/internal/camera/wan-sessions/playback` | 待评估：写操作和数据生命周期 |
| POST | `/print3dCamera/api/v2/internal/camera/wan-sessions/prepare` | 待评估：写操作和数据生命周期 |
| POST | `/print3dCamera/api/v2/internal/camera/wan-sessions/status` | 待评估：写操作和数据生命周期 |
| POST | `/print3dCamera/api/v2/internal/camera/wan-sessions/stop` | 待评估：写操作和数据生命周期 |
| POST | `/print3dDebugAssistant/partner/v1/check/cmdAndGet` | 待评估：写操作和数据生命周期 |
| POST | `/print3dDebugAssistant/partner/v1/check/login` | 待评估：写操作和数据生命周期 |

## 标识为移除的接口

方法与路径来自文档差异；不代表服务端接口已经下线。

- `POST /print3d/api/v1/users/updateInfo`（同路径仍在最新文档）
- `GET /print3d/api/v1/model/feed`（同路径仍在最新文档）
- `GET /print3d/api/v1/model/latest`（同路径仍在最新文档）
- `GET /print3d/api/v1/stamp/line-art/tasks/{taskSn}`
- `GET /print3d/api/v1/model/active`（同路径仍在最新文档）
- `GET /print3d/api/v1/model/simplePage`（同路径仍在最新文档）
- `GET /print3d/api/v1/aiModeling/everyoneDoing`（同路径仍在最新文档）
- `GET /print3d/api/v1/users/info`（同路径仍在最新文档）
- `POST /partner/v1/check/cmdAndGet`
- `GET /pmc/api/v1/print/status`

## 文档标识为变更的接口

是否在原任务按方法与路径匹配，不能替代参数及响应兼容性逐项审核。

| 方法 | 路径 | 原任务含该路径 | 变化字段 |
| --- | --- | --- | --- |
| GET | `/business/v1/model/detail` | 否 | resolved_dependencies |
| GET | `/business/v1/userModel/detail` | 否 | resolved_dependencies |
| GET | `/manage/v1/model/info` | 否 | resolved_dependencies |
| GET | `/pmc/api/v1/deviceCmd/deviceStatus` | 是 | parameters, responses, summary, tags, x-apifox-folder, x-run-in-apifox |
| POST | `/print3d/api/v1/appRating/postpone` | 是 | resolved_dependencies |
| GET | `/print3d/api/v1/appRating/shouldShow` | 是 | resolved_dependencies |
| GET | `/print3d/api/v1/badge/overview` | 是 | deprecated, description, responses, summary, x-apifox-status |
| GET | `/print3d/api/v1/badge/task/detail` | 是 | deprecated, description, parameters, responses, summary, x-apifox-status |
| GET | `/print3d/api/v1/bizKit/detail` | 否 | description |
| GET | `/print3d/api/v1/codeBooks/queryGrades` | 是 | description, parameters, responses, summary |
| POST | `/print3d/api/v1/collection/page` | 是 | description, responses |
| POST | `/print3d/api/v1/deviceSettings/set` | 否 | parameters, requestBody |
| POST | `/print3d/api/v1/deviceSettings/setSleepWindow` | 否 | requestBody |
| GET | `/print3d/api/v1/devices/info` | 是 | description, parameters, responses, summary, tags, x-apifox-folder, x-run-in-apifox |
| GET | `/print3d/api/v1/devices/list` | 是 | description, parameters, responses, summary, tags, x-apifox-folder, x-apifox-status, x-run-in-apifox |
| POST | `/print3d/api/v1/epone/actions/execute` | 否 | description, parameters, requestBody, tags |
| POST | `/print3d/api/v1/feedback/user-read` | 是 | resolved_dependencies |
| GET | `/print3d/api/v1/model/detail` | 是 | description, parameters, responses |
| GET | `/print3d/api/v1/model/libraryMetaOptions` | 是 | description, parameters, responses |
| GET | `/print3d/api/v1/model/page` | 是 | description, responses |
| GET | `/print3d/api/v1/model/related` | 是 | description, resolved_dependencies, responses |
| GET | `/print3d/api/v1/model/search` | 是 | description, responses |
| GET | `/print3d/api/v1/modelSeries/detail` | 是 | description, responses |
| POST | `/print3d/api/v1/printJob/modelTransforms` | 否 | resolved_dependencies |
| POST | `/print3d/api/v1/printTaskQueue/prepare` | 否 | description |
| POST | `/print3d/api/v1/stamp/line-art/tasks` | 否 | requestBody, responses |
| POST | `/print3d/api/v1/stamp/line-art/upload` | 否 | requestBody, summary |
| POST | `/print3d/api/v1/stamp/single/generate` | 否 | summary, x-run-in-apifox |
| POST | `/print3d/api/v2/camera/lan-sessions/heartbeat` | 否 | description, parameters, requestBody, responses, summary, tags, x-apifox-status |
| POST | `/print3d/api/v2/camera/lan-sessions/prepare` | 否 | description, parameters, requestBody, responses, summary, tags, x-apifox-status |
| POST | `/print3d/api/v2/camera/lan-sessions/stop` | 否 | description, parameters, requestBody, responses, summary, tags, x-apifox-status |
| POST | `/print3d/open/v1/youzan/message` | 否 | deprecated, description, x-apifox-status |
| GET | `/print3dModel/api/v1/model/info` | 否 | resolved_dependencies |
| GET | `/print3dModel/manage/v1/model/info` | 否 | resolved_dependencies |
| GET | `/share/user/v1/userModel/detail` | 否 | resolved_dependencies |
| POST | `/user/v1/order/apply-after-sale` | 否 | x-apifox-folder |
| POST | `/user/v1/order/cancel` | 否 | x-apifox-folder |
| POST | `/user/v1/order/createOnlineOrder` | 否 | x-apifox-folder |
| POST | `/user/v1/order/createParamOrder` | 否 | x-apifox-folder |
| GET | `/user/v1/order/page` | 否 | x-apifox-folder |
| POST | `/user/v1/order/prepay` | 否 | x-apifox-folder |
| POST | `/user/v1/order/print-now` | 否 | x-apifox-folder |

## 联合执行记录

- 首批新增 22 条合并原 297 条后，平台完整运行 `f9072e80-b664-4c2b-bfe2-3497236a232a`：319/319 通过。后续补充的 6 条单独组合运行 `c091b138-e5ce-4929-b0da-d82abd58c1e1`：6/6 通过。
- 最终 325 项运行 `0dfab6ed-b597-4c56-81bf-cc8d7dcb6342`：294 通过、31 异常；文字建模聊天请求在 30 秒超时，另 30 项被标记执行器中断，不能视为接口业务失败或已验证通过。
- 服务器日志显示 2026-11-04 15:11:07 本地源码更新至 6fd43bc9，15:11:15 起主服务、API Worker 和调度器先后停止并启动；运行中的 Worker 收到 SIGTERM，回归未完成。该时间线与部署过程吻合，但本记录不认定具体操作人。报告保留原结果。
- 任务已保持启用，325 项，每天北京时间 08:00；截至记录时下一次为 2026-11-05 08:00。完整 325 项未取得成功的整轮证据。
- 服务恢复后，新加入的 28 条基线作为一组再次执行 `d4939e02-4c5b-46e4-b083-bc9f4459ed2d`，28/28 通过；这不改变中断的 325 项完整回归结果，也不能证明整轮通过。
