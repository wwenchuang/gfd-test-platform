"""Authenticated requirements and generated design evidence, separate from execution."""
from task_server.services import requirement_design as design
from task_server.services import requirement_evaluation_view as evaluation
from task_server.services.operation_attribution import current_actor
from task_server.services.ui_execution_contract import AccessDenied

BASE = '/api/platform/requirement-design'


def _actor(handler):
    actor=current_actor()
    owner, username=actor.get('user_id'),actor.get('username')
    if actor.get('kind') != 'user' or not owner or not design.authorized(owner,username):
        handler._json({'ok':False,'error':'需要 UI 查看、平台配置权限和完整应用范围'},403)
        return None
    return owner,username


def _serve(handler, operation, *, image=False, download=False, code=200, authorize=None):
    actor=(authorize or _actor)(handler)
    if not actor:
        return
    try:
        value=operation(*actor)
    except AccessDenied:
        handler._json({'ok': False, 'error': '应用超出当前账号的查看范围'}, 403)
        return
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
    from task_server.practice_knowledge_http import register_routes as register_knowledge_routes
    register_knowledge_routes(route_get, route_post)
    from task_server.platform_mcp_http import register_routes as register_mcp_routes
    register_mcp_routes(route_get, route_post)
    from task_server.requirement_intake_http import register_routes as register_intake_routes
    register_intake_routes(route_get, route_post)
    from task_server.requirement_collection_http import register_collection_routes
    register_collection_routes(route_get, route_post, _actor, _serve)
    from task_server.services import practice_result_view as practice

    from task_server.services import requirement_visual as visual
    visual_path = BASE + '/visual'

    @route_get(visual_path + '/context')
    def visual_context(handler, qs):
        def read(owner, username):
            _params(qs, ('source',))
            with design._db() as db:
                _, source = design._source(db, owner, qs['source'])
            jobs = [dict(id=j['job_id'], name=j.get('target_task_name') or j.get('file'), device=j.get('device_id'),
                         time=j.get('started_at')) for j in visual.job_service.load_jobs(500)
                    if j.get('created_by') == username and j.get('report_url')]
            return dict(refs=source.get('figma_refs', []), jobs=jobs, history=visual.recent(owner, qs['source']))
        _serve(handler,read)

    @route_post(visual_path + '/boards')
    def visual_boards(handler, qs):
        def read(owner,username):
            body=handler._body()
            _params(body, ('link',))
            return visual.boards(body['link'])
        _serve(handler,read)

    @route_get(visual_path + '/steps')
    def visual_steps(handler, qs):
        def read(owner,username):
            _params(qs, ('job',))
            return dict(steps=visual.report_steps(visual.job_record(username,qs['job'])))
        _serve(handler,read)

    @route_get(visual_path + '/result')
    def visual_result(handler, qs):
        def read(owner,username):
            _params(qs, ('id',))
            return visual.result(owner,qs['id'],images=True)
        _serve(handler,read)

    @route_post(visual_path + '/investigate')
    def visual_investigate(handler, qs):
        def read(owner, username):
            body=handler._body()
            _params(body, ('id',))
            if not isinstance(body['id'],str) or len(body['id'])!=64:
                raise ValueError('对照报告编号无效')
            return visual.puzzle_investigation.investigate(owner,username,body['id'])
        _serve(handler,read)

    @route_post(visual_path + '/compare')
    def visual_compare(handler, qs):
        def create(owner,username):
            from task_server.background_jobs import acquire_heavy_workload_slot, release_heavy_workload_slot
            body=handler._body()
            _params(body,('source','link','version','board','job','step','environment'))
            if not all(isinstance(v,str) and 0<len(v)<=1000 for v in body.values()):
                raise ValueError('请选择需求、画板、执行记录和步骤')
            if not acquire_heavy_workload_slot('sync'):
                raise design.Busy()
            try:
                return visual.compare(owner,username,body)
            finally:
                release_heavy_workload_slot('sync')
        _serve(handler,create)

    def read_result(handler, qs, kind):
        def read(owner, username):
            _params(qs, ())
            return {'report': practice.report(kind, owner, username)}
        _serve(handler, read)

    def mobile_actor(handler):
        from task_server.services.ui_execution_contract import AccessDenied, require_ui_actor
        try:
            require_ui_actor(permission='ui.view', app_id='com.kfb.model')
        except AccessDenied:
            handler._json({'ok': False, 'error': '需要智小白3D的用例查看权限'}, 403)
            return None
        actor = current_actor()
        return actor['user_id'], actor['username']

    @route_get(BASE+'/practice-results/mobile')
    def mobile_result(handler, qs):
        def read(owner, username):
            _params(qs, ())
            return {'report': practice.mobile_observations()}
        _serve(handler, read, authorize=mobile_actor)

    @route_get(BASE+'/practice-results/mobile-frame')
    def mobile_result_frame(handler, qs):
        def read(owner, username):
            _params(qs, ('id',))
            return practice.mobile_frame(qs['id'])
        _serve(handler, read, image=True, authorize=mobile_actor)

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

    @route_post(BASE+'/installed-qa')
    def installed_qa(handler,qs):
        from .services.requirement_scripts import installed_qa as submit_trial
        def run(owner,username):
            _params(qs,())
            body=handler._body()
            _params(body,('source_id','design_id','platform','expected_version'))
            if (not all(isinstance(v,str) for v in body.values()) or body['platform'] not in ('android','ios')):
                raise ValueError('请选择需求设计及 QA 目标平台')
            return {'intake':submit_trial(owner,username,body['source_id'],body['design_id'],body['platform'],body['expected_version'])}
        _serve(handler,run)

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
