"""Session-authenticated MCP adapter and workbench history routes."""
import json

from task_server import auth
from task_server.services import platform_mcp as tools
from task_server.services.operation_attribution import current_actor

BASE = '/api/platform/mcp'


def _serve(handler, qs, operation, field, *, xml=False):
    actor = current_actor()
    token = auth.bearer_token(handler.headers)
    session = auth.verify_session_token(token)
    if (actor.get('kind') != 'user' or not session or not session.get('user_id')
            or actor.get('user_id') != session['user_id']):
        handler._json({'ok':False,'code':'unauthorized','error':'需要有效的个人登录会话'},403)
        return
    try:
        if xml and set(qs) != {'run_id'}:
            raise tools.ToolError(400,'invalid_arguments','需要运行编号')
        if not xml and qs:
            raise tools.ToolError(400,'invalid_arguments','此接口不接受查询参数')
        result = operation((session['user_id'],session['user'],token))
    except tools.ToolError as exc:
        handler._json({'ok':False,'code':exc.code,'error':str(exc)},exc.status)
        return
    if xml:
        handler.send_response(200)
        handler._cors()
        handler.send_header('Content-Type','application/xml; charset=utf-8')
        handler.send_header('Content-Disposition',f'attachment; filename="junit-{qs["run_id"]}.xml"')
        handler.send_header('Cache-Control','private, no-store')
        handler.send_header('X-Content-Type-Options','nosniff')
        handler.send_header('Content-Length',str(len(result)))
        handler.end_headers()
        handler.wfile.write(result)
    else:
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

    @route_get(BASE+'/junit')
    def junit(handler, qs):
        _serve(handler, qs, lambda actor: tools.junit(actor,qs['run_id']), None, xml=True)

    @route_get(BASE+'/recovery-drill')
    def recovery_drill(handler, qs):
        _serve(handler, qs, tools.recovery_drill, 'report')
