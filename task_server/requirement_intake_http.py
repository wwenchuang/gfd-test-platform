"""Personal, application-scoped requirement intake receipts."""
from task_server.services import requirement_intake as intake
from task_server.services.ui_execution_contract import AccessDenied

PATH = '/api/platform/requirement-design/intake'


def _serve(handler, operation, key='intake'):
    try:
        receipt = operation()
    except AccessDenied:
        handler._json({'ok':False, 'error':'账号缺少对应的应用权限'}, 403)
    except intake.MappingUnavailable as exc:
        handler._json({'ok':False, 'error':str(exc)}, 503)
    except intake.Conflict as exc:
        handler._json({'ok':False, 'error':str(exc)}, 409)
    except ValueError as exc:
        handler._json({'ok':False, 'error':str(exc)}, 400)
    else:
        if receipt is None:
            handler._json({'ok':False, 'error':'需求事件不存在或无权访问'}, 404)
        else:
            handler._json({'ok':True, key:receipt})


def register_routes(route_get, route_post):
    @route_get(PATH)
    def get_receipt(handler, qs):
        if set(qs) != {'source', 'event_id'}:
            return handler._json({'ok':False, 'error':'请提供来源和事件编号'}, 400)
        _serve(handler, lambda: intake.read(qs['source'], qs['event_id']))

    @route_get(PATH + '/recent')
    def recent(handler, qs):
        if set(qs) - {'limit'}:
            return handler._json({'ok':False, 'error':'查询参数无效'}, 400)
        _serve(handler, lambda: intake.recent(int(qs.get('limit', '10'))), key='items')

    @route_post(PATH + '/design-source')
    def design_source(handler, qs):
        def save():
            body = handler._body()
            if qs or not isinstance(body, dict) or set(body) != {'source', 'event_id', 'app_id'}:
                raise ValueError('请选择目标应用内的需求事件')
            return intake.design_source(**body)
        _serve(handler, save, key='source_id')

    @route_post(PATH)
    def receive(handler, qs):
        if qs:
            return handler._json({'ok':False, 'error':'此入口仅接受需求事件正文'}, 400)
        _serve(handler, lambda: intake.submit(handler._body()))
