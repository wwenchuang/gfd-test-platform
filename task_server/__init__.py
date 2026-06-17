"""Task platform service boundaries.

This package is a migration target for the large legacy ``midscene-upload.py``
server. New backend code should prefer these small modules first, then wire
them into the legacy handler when the behavior is covered by tests.

Module map (Phase 1):
    config      — 环境变量、目录路径、锁、常量
    storage     — 原子文件读写、JSON 缓存、路径安全
    auth        — 认证鉴权（session / token / Sonic callback）
    response    — ResponseMixin（_json / _text / _html / _cors / _body 等）
    schemas     — 业务常量与枚举
    router      — 路由注册表、装饰器、dispatch 函数、legacy fallback
    app         — Handler / ThreadingHTTPServer / main（第二阶段启用）
"""

# 便捷导出：允许 ``from task_server.config import *`` 等用法
from . import config, storage, auth, response, schemas, router, app
