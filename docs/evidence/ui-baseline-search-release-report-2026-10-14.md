# 基线搜索与发版用途报告验收

## 用户选择与范围

- 开始测试/用例库默认仅已发布基线；用例库可勾选显示未发布用例。搜索按钮、Enter、300ms输入筛选、清空均支持。
- 开始测试勾选“用于发版”，结束后自动生成正式报告。普通测试仅保存执行结果与证据。飞书通知独立可选，默认关闭。
- 不改Runner、YAML、设备选择、结果真实性和现有执行门禁；不迁移历史报告内容。

## 候选验证

- 搜索4dc3f81：136 Node、80 Python；后端63/前端84；Chrome实际候选页面按钮/Enter/清空/草稿切换和390px布局。
- 报告最终：152 Node、273 Python；后端63/前端84；适用Python/JS语法与git diff检查通过。root独立关键Python71、Node152复跑通过。
- 独立审查修复旧版幂等重试兼容与跨批次截图残留；旧请求需同可信账号/同key/完整旧规范化摘要且当前权限仍有效，禁止借此改变意图。切换测试销毁旧视图并abort图片请求。
- Chrome独立窗口、真实HTTP与服务/SQLite/worker，使用模拟设备事件的隔离fixture：普通批次uib_ffa1008cb8a24f5dafa9238c02954d2d执行完毕及证据入库后，0次DOCX渲染、0报告快照、2附件；发版批次uib_5b4ebfb5d8e0429090287f29b9a82f3b自动完成后，1次DOCX渲染、1报告快照、两批总共2Jobs及4附件。
- Word实际下载38234字节、ZIP可读；含智小白3D、测试版本1.19.7（70）、本地验证执行人、测试时间、场景及结果。图片实际预览，390px页面布局正常；普通证据切换到另一个已就绪报告，旧证据面板已移除。
- 临时证据目录：/tmp/codex-release-report-20261014。以上不是手机Runner实跑，不替代跨端稳定性验收。未发真实飞书。

## 部署保护

- 上线前生产release_revision=c3828e0d93df6a64005fa81ccd256c6de65b1afb。旧部署终端明确GitHub拉取超时，不能以“已部署”回复判断成功。
- SQLite在线备份：/opt/midscene-auth-backups/release-purpose-20261014-155617/ui-executions.sqlite3；schema4，34705408字节，quick_check=ok。备份目录0700/文件0600。
- 本版schema5；旧代码不识别schema5，不能只回滚源码。故障优先向前修复，若数据库恢复必须评估备份后的新执行数据。
- 待提交后使用SHA256与git bundle verify校验的增量包，REMOTE=/上传路径.bundle bash deploy/update-main-server.sh；保留标准安装、服务检查与Sonic保护。

## 线上验收

- 用户随后自行部署成功，root停止重复部署。实际health release_revision=421d718025e4ac5a2bb45969484822d8869d0404；7个HTML/JS/CSS文件字节与候选精确一致。备用51901字节bundle已在本地校验，但未上传、未执行第二次部署。
- Chrome用例库默认搜索“基线”14条；勾选显示未发布用例19条，取消回14条。搜索按钮查不存在词0条；Enter及清空恢复15条已发布目录。原5条草稿不再混入默认结果。
- “用于发版”默认false，通知选项隐藏；勾选用途后通知显示且false，勾选通知再取消用途，两者都回false。第一次浏览器断言用getByRole检查隐藏控件超时，改成正确定位后通过，属于验证脚本定位错误，非产品失败。
- 历史第三轮报告uib_b0202cd57fa74ff89bf9380da1c752de仍ready，中文版本/执行人/时间/场景完整；实际第一张图片加载1170×2532，截图见/tmp/codex-release-report-20261014/live-old-preview.png。
- 设备换线期间短暂fetch失败、iPhone退出列表；用户明确已拔iPhone换安卓并要求接入Mac。后续实际报告用途验收与安卓接入计划分开记录，不把隔离fixture当实机通过。

## Mac 安卓实机用途验收

用户换线后明确要求先接入Mac安卓。独立Runner mac-android-adouceshi/ecbfd645 已用新脚本完成真实平台任务：

|用途|批次|真实结果|正式报告|通知|
|---|---|---|---|---|
|普通|uib_e383b988a9c64c91bd5010b78b2e4747|首次6/6通过，停止已确认|not_requested，截图与静态原始附件保留|not_requested|
|用于发版|uib_3ff2061f8c7b4a5699fe240460ccd4f8|首次6/6通过，45.83秒，停止已确认|ready，安卓2.0.0（51）/管理员/17:09:10–17:09:56/导航覆盖场景|not_requested|

源基线AI测试/跨端导航验收20261013.yaml revision55aac2e5c87eb76d213b4e5e928f4a007651ed3e5e5a9e08295ffc630f5b0790未修改，动作只启动/我的/首页/断言。普通截图实际Chrome打开1080×2412，/tmp/codex-release-report-20261014/android-ordinary-preview.png可见真实首页。没有把普通原始HTML证据当正式发版报告。

Mac安装/启动/停止/状态说明在 /Users/adouceshi/Applications/MidsceneAndroid/INSTALL.md；独立配置0600，凭据不入仓库。本版Mac安卓预览/平台字段回归197项，root相关最终组合137Python/157Node及静态/语法通过。当前旧Windows离线来源尚需列表去重补丁部署；未删除旧Runner或历史任务。
