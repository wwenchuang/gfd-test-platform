"""Authenticated requirements and generated design evidence, separate from execution."""
from task_server.services import requirement_design as design
from task_server.services.operation_attribution import current_actor

BASE = '/api/platform/requirement-design'


def _actor(handler):
    actor=current_actor()
    owner, username=actor.get('user_id'),actor.get('username')
    if actor.get('kind') != 'user' or not owner or not design.authorized(owner,username):
        handler._json({'ok':False,'error':'需要 UI 查看、平台配置权限和完整应用范围'},403)
        return None
    return owner,username


def _serve(handler, operation, *, image=False, code=200):
    actor=_actor(handler)
    if not actor:
        return
    try:
        value=operation(*actor)
    except design.Missing:
        handler._json({'ok':False,'error':'材料不存在或无权访问'},404)
        return
    except design.Busy:
        handler._json({'ok':False,'error':'已有生成任务在运行，请稍后查看记录'},429)
        return
    except ValueError as exc:
        handler._json({'ok':False,'error':str(exc)},400)
        return
    if image:
        handler.send_response(200)
        handler._cors()
        handler.send_header('Content-Type','image/png')
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
    @route_get(BASE)
    def overview(handler,qs):
        def read(owner,username):
            _params(qs,(),('source_id',))
            return design.overview(owner,qs.get('source_id'))
        _serve(handler,read)

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
