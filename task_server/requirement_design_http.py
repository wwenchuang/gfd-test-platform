"""Authenticated requirements and generated design evidence, separate from execution."""
from task_server.services import requirement_design as design
from task_server.services import requirement_evaluation_view as evaluation
from task_server.services.operation_attribution import current_actor

BASE = '/api/platform/requirement-design'


def _actor(handler):
    actor=current_actor()
    owner, username=actor.get('user_id'),actor.get('username')
    if actor.get('kind') != 'user' or not owner or not design.authorized(owner,username):
        handler._json({'ok':False,'error':'需要 UI 查看、平台配置权限和完整应用范围'},403)
        return None
    return owner,username


def _serve(handler, operation, *, image=False, download=False, code=200):
    actor=_actor(handler)
    if not actor:
        return
    try:
        value=operation(*actor)
    except design.Missing:
        handler._json({'ok':False,'error':'材料不存在或无权访问'},404)
        return
    except evaluation.Unavailable:
        handler._json({'ok': False, 'error': '评估材料暂时无法读取'}, 503)
        return
    except design.Busy:
        handler._json({'ok':False,'error':'已有生成任务在运行，请稍后查看记录'},429)
        return
    except ValueError as exc:
        handler._json({'ok':False,'error':str(exc)},400)
        return
    if image or download:
        mime, filename = 'image/png', None
        if download:
            mime, filename, value = value
        handler.send_response(200)
        handler._cors()
        handler.send_header('Content-Type', mime)
        if filename:
            handler.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        handler.send_header('Cache-Control','private, no-store')
        handler.send_header('X-Content-Type-Options','nosniff')
        handler.send_header('Content-Length',str(len(value)))
        handler.end_headers()
        handler.wfile.write(value)
    else:
        handler._json(dict(ok=True,**value),code)


def _params(args, expected, optional=()):
    if not isinstance(args,dict) or set(args)-set(optional)!=set(expected):
        raise ValueError('请求参数无效')


def register_routes(route_get,route_post):
    from task_server.platform_mcp_http import register_routes as register_mcp_routes
    register_mcp_routes(route_get, route_post)
    from task_server.requirement_collection_http import register_collection_routes
    register_collection_routes(route_get, route_post, _actor, _serve)
    from task_server.services import practice_result_view as practice

    def read_result(handler, qs, kind):
        def read(owner, username):
            _params(qs, ())
            return {'report': practice.report(kind, owner, username)}
        _serve(handler, read)

    @route_get(BASE+'/practice-results/replay')
    def replay_result(handler, qs):
        read_result(handler, qs, 'replay')

    @route_get(BASE+'/practice-results/visual')
    def visual_result(handler, qs):
        read_result(handler, qs, 'visual')

    @route_get(BASE+'/practice-results/screenshot')
    def visual_screenshot(handler, qs):
        def read(owner, username):
            _params(qs, ('id',))
            return practice.screenshot(owner, username, qs['id'])
        _serve(handler, read, image=True)

    @route_get(BASE)
    def overview(handler,qs):
        def read(owner,username):
            _params(qs,(),('source_id',))
            return design.overview(owner,qs.get('source_id'))
        _serve(handler,read)

    def evaluation_result(handler, qs, *, blind=False):
        def read(owner, username):
            _params(qs, ())
            return evaluation.published(blind=blind)
        _serve(handler, read)

    @route_get(BASE+'/evaluation')
    def published_evaluation(handler, qs):
        evaluation_result(handler, qs)

    @route_get(BASE+'/evaluation/blind')
    def blind_handout(handler, qs):
        evaluation_result(handler, qs, blind=True)

    @route_get(BASE+'/standard-cases')
    def standard_cases(handler, qs):
        from task_server.services.requirement_standard_export import export
        def read(owner, username):
            _params(qs, ('source_id', 'run_id', 'format'))
            return export(owner, qs['source_id'], qs['run_id'], qs['format'])
        _serve(handler, read, download=True)

    @route_get(BASE+'/prototype')
    def prototype(handler,qs):
        def read(owner,username):
            _params(qs,('id','source_id'))
            return design.prototype(owner,qs['source_id'],qs['id'])
        _serve(handler,read,image=True)

    @route_post(BASE+'/source')
    def material(handler,qs):
        def upload(owner,username):
            _params(qs,())
            return {'source_id':design.import_source(owner,handler._body())}
        _serve(handler,upload,code=201)

    @route_post(BASE)
    def start(handler,qs):
        def generate(owner,username):
            _params(qs,())
            body=handler._body()
            _params(body,('source_id','request_id'))
            if not all(isinstance(v,str) for v in body.values()):
                raise ValueError('请求编号无效')
            return {'run':design.start(owner,username,body['source_id'],body['request_id'])}
        _serve(handler,generate,code=202)
