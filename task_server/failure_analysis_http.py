"""Authenticated report-bound investigation endpoints; no execution tools."""
from task_server.services import failure_analysis_tasks as tasks
from task_server.services.operation_attribution import current_actor

BASE = '/api/failure-analysis'


def register_routes(route_get, route_post):
    def serve(handler, operation, code=200):
        actor = current_actor()
        if actor.get('kind') != 'user':
            handler._json({'ok': False, 'error': '请先登录测试平台。'}, 401)
            return
        try:
            length = int(handler.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            length = -1
        if length < 0 or length > 2048:
            handler.close_connection = True
            handler._json({'ok': False, 'error': '分析请求过大或格式无效。'}, 413)
            return
        try:
            task = operation(actor)
        except PermissionError:
            handler._json({'ok': False, 'error': '当前账号无权访问这份报告或权限已变更。'}, 403)
        except LookupError:
            handler._json({'ok': False, 'error': '报告或分析不存在，或无权查看。'}, 404)
        except tasks.Busy as error:
            handler._json({'ok': False, 'error': str(error)}, 429)
        except (ValueError, TypeError):
            handler._json({'ok': False, 'error': '请求参数无效，请从报告详情重新打开分析。'}, 400)
        except Exception:
            handler._json({'ok': False, 'error': '分析服务暂时不可用，请稍后重试。已有报告保持不变。'}, 503)
        else:
            handler._json({'ok': True, 'task': task}, code)

    @route_get(BASE + '/tasks')
    def read(handler, qs):
        def operation(actor):
            if set(qs) == {'id'}:
                return tasks.read(actor, qs['id'])
            if set(qs) == {'source', 'run_id'}:
                return tasks.latest(actor, qs['source'], qs['run_id'])
            raise ValueError('invalid query')
        serve(handler, operation)

    @route_post(BASE + '/tasks')
    def start(handler, qs):
        def operation(actor):
            if qs:
                raise ValueError('invalid query')
            return tasks.start(actor, handler._body())
        serve(handler, operation, 202)

    @route_post(BASE + '/cancel')
    def cancel(handler, qs):
        def operation(actor):
            body = handler._body()
            if qs or not isinstance(body, dict) or set(body) != {'id'}:
                raise ValueError('invalid cancel')
            return tasks.cancel(actor, body['id'])
        serve(handler, operation)
