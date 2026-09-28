# 2026-09-28 平台发布与验收

## 发布范围

用户明确授权直接发布现有改动。发布分支从账号分支 eeb31f3 建立，dc0d880 完整撤回 92e70a0，保留其余已完成操作审计、YAML资产归属/版本、个人操作记录、录制同名目标语义和HTTP修复。撤回的是整个新任务输入快照/来源功能，未实现或绕过自动审核拒绝的修复；账号原分支完整保留。

发布 f50f4870563b8e9e8ef97bb2fd3e72d72eb17c3a 已同时推送 origin/main 与 origin/codex/platform-release-20260928。原工作区未提交文档和截图未纳入。

## 发布前验证

- 后端身份、权限、审计、资产、录制、Windows证据：305项通过。
- 审计引用/就绪与修复：27项通过。
- 操作记录、身份、录制、Sonic桥接、导航与报告前端：140项通过。
- 必跑后端静态63项、前端静态84项、主链和Runner Python编译、git diff --check通过。
- Chromium真实本地HTTP角色验收38项通过、20张截图；隔离数据清理；布局1366×768、768×480、390×600通过。

## 部署证据

- 用户恢复华为堡垒机终端后，通过Chrome在qa主机 /opt/midscene-task-platform-src 执行既有 bash deploy/update-main-server.sh。部署前工作区干净，版本a351e95。
- 脚本最终显示“部署完成：f50f487”，后端8091/8088健康版本、登录401 JSON合同、API页面资源、Sonic hook散列和容器状态检查通过。
- 外网 /api/health 返回 ok=true、release_revision=f50f4870563b8e9e8ef97bb2fd3e72d72eb17c3a、PyYAML就绪。
- task-manager.html、operation-history.js/css、device-recorder.js与本地发布逐字节一致，Sonic3000端口hook与本地相同。
- 未认证 /api/operations 返回401；/api-test/返回200。
- Chrome刷新平台后会话需要重新登录，已请求用户完成登录；生产页面验收结果后补。
- npm安装输出报告5项漏洞（3 moderate、2 high），未在本次自动升级依赖，需另行核对具体依赖和影响。

## 未完成边界

本次是已完成改动的部署，不是全平台归属计划完成。B1新任务输入快照/可信来源已撤回；异步来源与终态、报告/API深度集成仍待完成。性能专项按用户要求暂停，历史延迟/CPU开销不标为通过。新录制语义真实模型与三轮Chrome/PHM110/Windows Runner验收尚未重跑；Sonic默认触控APK补丁仍未构建安装，不能用平台部署代替。
