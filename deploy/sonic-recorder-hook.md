# Sonic 2.7.2 操作录制钩子

`sonic-recorder-hook.js` 必须在 Sonic Web 主应用 bundle 之前加载。它只包装浏览器端 `WebSocket.send`，原消息先发送给 Sonic，再异步镜像到 Task 平台；不代理视频流，不执行 ADB 操作，也不把录制令牌放进 URL。

部署时把脚本复制到 Sonic Web 静态目录，并在 `index.html` 主 bundle 前加入：

```html
<script src="/sonic-recorder-hook.js"></script>
```

Task 服务的 `SONIC_BASE_URL` 必须配置为实际 Sonic 入口。服务启动时会把该入口的 origin 加入允许跨域来源；动作接口仍要求短期录制令牌。
