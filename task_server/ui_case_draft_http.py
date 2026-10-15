"""Thin private draft routes; all ownership and execution gates live in services."""
import sqlite3


def _run(handler, operation, *, admission=False):
    from .services.ui_batch_dispatch import enabled
    from .services.ui_execution_contract import AccessDenied
    from .services.ui_case_draft_store import DraftConflict
    from .services.ui_execution_store import BatchConflict
    if admission and not enabled():
        handler._json({'ok': False, 'code': 'feature_disabled', 'error': '测试服务暂未开放，请联系管理员'}, 503)
        return
    try:
        payload, resource = operation()
    except AccessDenied:
        handler._json({'ok': False, 'code': 'draft_access_denied', 'error': '当前账号没有访问或操作这份用例的权限'}, 403)
        return
    except (DraftConflict, BatchConflict):
        handler._json({'ok': False, 'code': 'draft_revision_conflict', 'error': '用例已更新或相同请求的内容已变更，请重新打开核对'}, 409)
        return
    except (KeyError, FileNotFoundError):
        handler._json({'ok': False, 'code': 'draft_not_found', 'error': '未找到这份用例，请返回用例库重新选择'}, 404)
        return
    except ValueError as error:
        code = getattr(error, 'code', '') or str(error)
        messages = {
            'recording_import_unfinished': '请先结束录制；进行中或已取消的录制不能整理',
            'recording_import_size': '录制需包含1至50步，且内容大小不能超过限制',
            'recording_import_cross_app': '录制包含其他应用的启动动作，请先核对所属应用',
            'draft_generation_busy': '已有用例正在生成，请稍后重试',
            'draft_gate_failed': '用例尚未通过检查，请先处理待确认内容',
            'draft_trial_required': '请先完成当前版本的手机试跑，再发布基线',
            'draft_gate_required': '请先确认草稿并通过校验',
            'draft_trial_gate_invalid': '草稿试跑快照校验失败，请重新核对',
            'draft_module_invalid': '所选模块不属于当前应用',
            'draft_trial_pass_required': '当前版本尚未成功试跑并确认执行结束',
            'draft_trial_steps_required': '试跑缺少完整的步骤通过证据，请更新执行器后重试',
            'draft_publish_conflict': '该版本已有不同发布申请，请重新打开核对',
            'draft_publish_path_exists': '发布路径已存在，已阻止覆盖原文件',
            'draft_publish_asset_changed': '已发布文件发生变化，请重新核对',
            'draft_previous_revision_empty': '上一版尚无中文步骤，无法恢复；当前内容已保留',
            'draft_previous_revision_missing': '当前没有可恢复的旧版本',
        }
        handler._json({'ok': False, 'code': code if code in messages else 'draft_request_invalid',
                       'error': messages.get(code, '用例内容或请求无效，请检查必填项和当前版本后重试')},
                      429 if code == 'draft_generation_busy' else 400)
        return
    except (OSError, sqlite3.Error):
        handler._json({'ok': False, 'code': 'draft_store_unavailable', 'error': '用例服务暂时不可用，请稍后重试'}, 503)
        return
    if resource:
        from .operation_http import mark_resource
        mark_resource(handler, 'ui_draft', resource)
    handler._json({'ok': True, **payload})


def register_routes(route_get, route_post, route_get_regex, route_post_regex):
    @route_get('/api/ui-case-drafts')
    def _get_ui_case_drafts(handler, qs):
        from .services.ui_case_draft_service import list_drafts
        _run(handler, lambda: (list_drafts(None, qs.get('app_id', '')), ''))

    @route_post('/api/ui-case-drafts')
    def _post_ui_case_draft(handler, qs):
        from .services.ui_case_draft_service import create_draft
        def operation():
            draft = create_draft(None, handler._body())
            return {'draft': draft}, draft['draft_id']
        _run(handler, operation, admission=True)

    @route_post('/api/ui-case-drafts/import-recording')
    def _post_recording_draft_import(handler, qs):
        from .services.ui_case_draft_service import import_recording_draft
        def operation():
            draft = import_recording_draft(None, handler._body())
            return {'draft': draft}, draft['draft_id']
        _run(handler, operation, admission=True)

    @route_get_regex(r'^/api/ui-case-drafts/([^/]+)$')
    def _get_ui_case_draft(handler, qs, match):
        from .services.ui_case_draft_service import get_draft
        def operation():
            draft = get_draft(None, match.group(1))
            return {'draft': draft}, draft['draft_id']
        _run(handler, operation)

    @route_post_regex(r'^/api/ui-case-drafts/([^/]+)/(update|restore|validate|try|publish)$')
    def _post_ui_case_draft_action(handler, qs, match):
        def operation():
            from .services import ui_case_draft_service as service
            identifier, action = match.groups()
            body = handler._body()
            if action == 'validate':
                gate = service.validate_draft(None, identifier, body)
                # Compiled YAML/target resolution are internal dispatch inputs.
                result = {'gate': {key: gate[key] for key in ('ready', 'blockers')}}
            elif action in {'update', 'restore'}:
                method = service.update_draft if action == 'update' else service.restore_draft
                result = {'draft': method(None, identifier, body)}
            else:
                from .services.ui_case_draft_execution import create_draft_trial, publish_draft
                if action == 'try':
                    result = {'batch': create_draft_trial(None, identifier, body, handler.headers.get('Idempotency-Key', ''))}
                else:
                    result = {'case': publish_draft(None, identifier, body)}
            return result, identifier
        _run(handler, operation, admission=True)
