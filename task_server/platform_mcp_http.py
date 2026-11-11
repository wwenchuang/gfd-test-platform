"""Session-authenticated MCP adapter and workbench history routes."""
import json

from task_server import auth
from task_server.services import platform_mcp as tools
from task_server.services.operation_attribution import current_actor

BASE = '/api/platform/mcp'


def _serve(handler, qs, operation, field):
    actor = current_actor()
    token = auth.bearer_token(handler.headers)
    session = auth.verify_session_token(token)
    if (actor.get('kind') != 'user' or not session or not session.get('user_id')
            or actor.get('user_id') != session['user_id']):
        handler._json({'ok':False,'code':'unauthorized','error':'需要有效的个人登录会话'},403)
        return
    try:
        if qs:
            raise tools.ToolError(400,'invalid_arguments','此接口不接受查询参数')
        result = operation((session['user_id'],session['user'],token))
    except tools.ToolError as exc:
        handler._json({'ok':False,'code':exc.code,'error':str(exc)},exc.status)
        return
    handler._json({'ok':True,field:result})


def register_routes(route_get, route_post):
    @route_post(BASE+'/call')
    def call(handler, qs):
        def invoke(actor):
            try:
                body = handler._body()
            except (json.JSONDecodeError, RecursionError):
                raise tools.ToolError(400,'invalid_arguments','请求必须是有效 JSON 对象') from None
            return tools.call(actor,body)
        _serve(handler, qs, invoke, 'result')

    @route_get(BASE+'/runs')
    def runs(handler, qs):
        _serve(handler, qs, tools.history, 'runs')

    @route_get(BASE+'/acceptance')
    def acceptance(handler, qs):
        _serve(handler, qs, tools.acceptance, 'report')
