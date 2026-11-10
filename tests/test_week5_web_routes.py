import unittest
from unittest.mock import patch

from task_server.access_control import MainAccess, AccessDenied
from task_server import router
from task_server.response import request_body_limit


class Week5RouteAccessTest(unittest.TestCase):
    def test_only_named_fixed_read_only_scenarios_reach_the_service(self):
        class Handler:
            headers = {}
            def __init__(self, body): self.body=body
            def _body(self): return self.body
            def _json(self, value, status=200): self.result=(value,status)
        with patch.object(router, '_week5_web_actor', return_value=('id','user','token')), \
             patch.object(router, 'acquire_heavy_workload_slot', return_value=True), \
             patch.object(router, 'release_heavy_workload_slot'), \
             patch('task_server.services.week5_web_service.run',return_value={'status':'blocked'}) as run:
            for scenario in ('ui_catalog_filter','ui_catalog_empty','ui_catalog_failure_drill','ui_case_selection','ui_start_handoff','ui_start_switch','ui_start_unready','ui_case_revision','ui_catalog_paging','ui_catalog_module','ui_case_hidden_selection','ui_start_empty_guard'):
                handler=Handler({'scenario':scenario});router._post_week5_web_check(handler,{})
                self.assertEqual(handler.result[1],200)
                self.assertEqual(run.call_args.kwargs['scenario'],scenario)
            for body in ({'scenario':'evil'}, {'scenario':'ui_catalog_filter','query':'private'}, {'scenario':'ui_catalog_failure_drill','expected_total':3}, {'scenario':None}):
                handler=Handler(body);router._post_week5_web_check(handler,{})
                self.assertEqual(handler.result[1],400)
    def test_fixed_check_rejects_large_request_bodies(self):
        self.assertEqual(request_body_limit('/api/ui-executions/web-check'), 1024)

    def test_delete_requires_explicit_confirmation(self):
        class Handler:
            headers = {}
            def _body(self): return {}
            def _json(self, value, status=200): self.result=(value,status)
        handler = Handler()
        with patch.object(router, '_week5_web_actor', return_value=('id','user','test-token')), \
             patch('task_server.services.week5_web_service.delete') as delete:
            router._delete_week5_web_check(handler, {'id':'a'*32})
        self.assertEqual(handler.result[1],400)
        delete.assert_not_called()

    def test_heavy_budget_rejects_busy_and_releases_after_failure(self):
        from task_server.services import week1_smoke
        class Handler:
            headers = {}
            def _body(self): return {}
            def _json(self, value, status=200): self.result=(value,status)
        with patch.object(router, '_week5_web_actor', return_value=('id','user','token')), \
             patch.object(router, 'acquire_heavy_workload_slot', return_value=False), \
             patch.object(router, 'release_heavy_workload_slot') as release, \
             patch('task_server.services.week5_web_service.run') as run:
            handler=Handler();router._post_week5_web_check(handler,{})
            self.assertEqual(handler.result[1],503)
            run.assert_not_called();release.assert_not_called()
        with patch.object(router, '_week5_web_actor', return_value=('id','user','token')), \
             patch.object(router, 'acquire_heavy_workload_slot', return_value=True), \
             patch.object(router, 'release_heavy_workload_slot') as release, \
             patch('task_server.services.week5_web_service.run', side_effect=week1_smoke.SmokeBusy):
            handler=Handler();router._post_week5_web_check(handler,{})
            self.assertEqual(handler.result[1],429)
            release.assert_called_once_with('sync')

    def test_uses_verified_session_full_username_not_truncated_actor_display(self):
        username = 'a' * 100
        class Handler:
            headers = {'Authorization':'Bearer token'}
            def _json(self, value, status=200): self.result=(value,status)
        actor = {'kind':'user','user_id':'u','username':username[:64]}
        session = {'user':username,'user_id':'u'}
        with patch('task_server.services.operation_attribution.current_actor', return_value=actor), \
             patch.object(router, 'verify_session_token', return_value=session), \
             patch('task_server.services.week5_web_service._profile', return_value={'user_id':'u'}) as profile:
            self.assertEqual(router._week5_web_actor(Handler()), ('u',username,'token'))
            profile.assert_called_once_with('u', username, 'token')

    def test_rejects_user_supplied_target_before_running(self):
        class Handler:
            headers = {}
            def _body(self): return {'url':'http://other.invalid/'}
            def _json(self, value, status=200): self.result=(value,status)
        handler = Handler()
        with patch.object(router, '_week5_web_actor', return_value=('id','user','test-token')):
            router._post_week5_web_check(handler, {})
        self.assertEqual(handler.result[1], 400)

    def test_fixed_read_only_browser_check_uses_ui_view(self):
        profile = {'permissions':['ui.view'], 'is_superuser':False, 'scope':{'ui_apps':[]}}
        access = MainAccess(profile, catalog=[])
        access.check('POST', '/api/ui-executions/web-check', {}, {})
        access.check('GET', '/api/ui-executions/web-check', {}, {})
        access.check('GET', '/api/ui-executions/web-check/artifact', {}, {})
        access.check('DELETE', '/api/ui-executions/web-check', {}, {})
        with self.assertRaises(AccessDenied):
            access.check('POST', '/api/ui-executions', {}, {})
