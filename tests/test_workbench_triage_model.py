import io
import json
import tempfile
import time
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ACTOR = ('owner', 'user', 'token')
RUN = 'c230d648-4ad1-4a03-94b7-8006fe2ba295'
BUNDLE = {'run_id':RUN,'suite_id':'catalog-failure-drill','is_drill':True,
          'facts':{'run.status':'failed','case.error_kind':'drill_assertion','observed.total':'17','observed.expected_total':'0'}}
ANSWER = {'category':'script','observations':[['observed.total','observed.expected_total']],
          'candidate_cause':{'text':'演练期望值与目录计数不一致','evidence_ids':['case.error_kind','observed.total','observed.expected_total']},
          'verification_action':'核对该次演练的数量断言。'}


class TriageTests(unittest.TestCase):
    def module(self):
        from task_server.services import workbench_triage_model
        return workbench_triage_model

    def test_unknown_when_only_generic_error_or_invalid_reference(self):
        m = self.module()
        self.assertEqual(m.interpret({'run.status':'failed'}, ANSWER, False)['category'], 'unknown')
        bad = {**ANSWER, 'candidate_cause':{'text':'不明','evidence_ids':['invented']}}
        self.assertEqual(m.interpret(BUNDLE['facts'], bad, True)['classification'], 'unknown')
        product = dict(ANSWER, category='product')
        self.assertEqual(m.interpret(BUNDLE['facts'], product, True)['category'], 'unknown')

    def test_observations_reconstruct_exact_stored_facts(self):
        m = self.module()
        review = m.interpret(BUNDLE['facts'], ANSWER, True)
        self.assertEqual(review['category'], 'script')
        self.assertEqual(review['observations'][0]['evidence_ids'], ['observed.total','observed.expected_total'])
        self.assertIn('17',review['observations'][0]['text'])
        self.assertEqual(review['severity'], 'unassessed')

    def test_projection_discards_free_text_and_script_headers(self):
        m = self.module()
        facts = m.project({'log':'secret', 'case.error_kind':'Bearer private', 'observed.http_status':'500','run.id':RUN})
        self.assertEqual(facts, {'observed.http_status':'500'})

    def test_published_review_bound_to_owner_and_facts_and_reuses_receipt(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'identity.db'), \
                patch.object(m,'_bundle',return_value=BUNDLE) as source, patch.object(m.platform,'_scope',return_value='scope'), \
                patch.object(m,'_request',return_value=(ANSWER,'test-model',{'total_tokens':43})) as request:
            result = m.generate(ACTOR,RUN)
            self.assertEqual(result['usage'], {'total_tokens':43})
            self.assertEqual(m.generate(ACTOR,RUN),result)
            self.assertEqual(request.call_count,1)
            self.assertEqual(m.read_review(ACTOR,BUNDLE),result)
            self.assertIsNone(m.read_review(('other','other','other'), BUNDLE))
            source.return_value = dict(BUNDLE, facts={'run.status':'failed'})
            self.assertIsNone(m.read_review(ACTOR,source.return_value))

    def test_provider_request_bounds_and_receipt_do_not_invent_usage_or_model(self):
        m = self.module()
        response = {'usage':{'total_tokens':50,'prompt_tokens':'12','price':7},'choices':[
            {'finish_reason':'stop','message':{'content':json.dumps(ANSWER,ensure_ascii=False)}}]}
        metrics = {'provider_calls':0,'requested_model':None}
        with patch.object(m.qwen,'get_text_model',return_value='qwen-test'), \
                patch.object(m.qwen,'get_dashscope_base_url',return_value='https://dashscope.aliyuncs.com/compatible-mode/v1'), \
                patch.object(m.qwen,'get_dashscope_api_key',return_value='dummy'), patch.object(m.request,'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            answer,actual,usage = m._request(m.project(BUNDLE['facts']),True,metrics)
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
            self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 25)
            self.assertNotIn(RUN, req.data.decode())
            self.assertLessEqual(json.loads(req.data)['max_tokens'], 600)
            self.assertEqual((answer,actual,usage,metrics),(ANSWER,None,{'total_tokens':50},{'provider_calls':1,'requested_model':'qwen-test'}))

    def test_generic_failed_status_forces_unknown_without_a_paid_request(self):
        m = self.module()
        bundle = dict(BUNDLE, is_drill=False, facts={'case.status':'failed','case.error_kind':'business'})
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'db'), \
                patch.object(m,'_bundle',return_value=bundle), patch.object(m.platform,'_scope',return_value='scope'), \
                patch.object(m,'_request') as request:
            result = m.generate(ACTOR,RUN)
            self.assertEqual((result['category'],result['provider_calls'],result['usage']), ('unknown',0,{}))
            request.assert_not_called()

    def test_unconfirmed_root_cause_cannot_be_published_as_confirmed(self):
        m = self.module()
        answer = dict(ANSWER,candidate_cause={**ANSWER['candidate_cause'],'text':'已确认线上缺陷'})
        self.assertEqual(m.interpret(BUNDLE['facts'], answer, True)['category'],'unknown')

    def test_paid_workbench_requires_execute_and_unsuccessful_owned_run(self):
        m = self.module()
        with patch.object(m.platform.web,'_profile',return_value={'permissions':['ui.view']}) as profile, \
                patch.object(m,'_bundle',return_value=dict(BUNDLE,facts={'run.status':'passed'})) as bundle, \
                patch.object(m,'_request') as provider:
            with self.assertRaises(m.platform.ToolError) as denied:
                m.generate_for_workbench(ACTOR,RUN)
            self.assertEqual(denied.exception.status,403)
            bundle.assert_not_called()
            profile.return_value = {'user_id':'owner','permissions':['ui.execute']}
            with self.assertRaises(m.platform.ToolError) as not_failed:
                m.generate_for_workbench(ACTOR,RUN)
            self.assertEqual(not_failed.exception.code,'run_not_failed')
            provider.assert_not_called()

    def test_failed_receipt_is_reused_and_does_not_consume_budget_twice(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'db'), \
                patch.object(m.platform.web,'_profile',return_value={'user_id':'owner','permissions':['ui.execute']}), \
                patch.object(m,'_bundle',return_value=BUNDLE), \
                patch.object(m,'_request',side_effect=TimeoutError()) as provider:
            first = m.generate_for_workbench(ACTOR,RUN)
            second = m.generate_for_workbench(ACTOR,RUN)
            self.assertEqual(first['status'],'unavailable')
            self.assertEqual(second,first)
            self.assertEqual(provider.call_count,1)

    def test_concurrent_click_cannot_send_a_second_paid_call(self):
        m = self.module()
        entered = threading.Event()
        release = threading.Event()
        completed = []
        def pending(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return ANSWER,'test-model',{'total_tokens':8}
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'db'), \
                patch.object(m.platform.web,'_profile',return_value={'user_id':'owner','permissions':['ui.execute']}), \
                patch.object(m,'_bundle',return_value=BUNDLE), patch.object(m,'_request',side_effect=pending) as provider:
            worker = threading.Thread(target=lambda:completed.append(m.generate_for_workbench(ACTOR,RUN)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(m.platform.ToolError) as busy:
                    m.generate_for_workbench(ACTOR,RUN)
                self.assertEqual((busy.exception.status,busy.exception.code),(429,'review_busy'))
            finally:
                release.set()
                worker.join(3)
            self.assertEqual(completed[0]['status'],'ready')
            self.assertEqual(m.generate_for_workbench(ACTOR,RUN), completed[0])
            self.assertEqual(provider.call_count,1)

    def test_hourly_budget_uses_rolling_window_and_is_owner_scoped(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'db'):
            now = time.time()
            with patch.object(m.time,'time',return_value=now):
                for _ in range(6):
                    m._reserve_attempt(ACTOR[0],str(uuid.uuid4()),workbench=True)
                with self.assertRaises(m.platform.ToolError) as limited:
                    m._reserve_attempt(ACTOR[0],str(uuid.uuid4()),workbench=True)
                self.assertEqual((limited.exception.status,limited.exception.code),(429,'review_budget_exhausted'))
                m._reserve_attempt('other',str(uuid.uuid4()),workbench=True)
            with patch.object(m.time,'time',return_value=now+3601):
                m._reserve_attempt(ACTOR[0],str(uuid.uuid4()),workbench=True)

    def test_interruption_after_dispatch_intent_never_resends_same_request(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'db'), \
                patch.object(m.platform.web,'_profile',return_value={'user_id':'owner','permissions':['ui.execute']}), \
                patch.object(m,'_bundle',return_value=BUNDLE), patch.object(m,'_request') as provider:
            # Simulate recovery after the sender died between durable dispatch intent and its final receipt.
            fp = m._fingerprint(BUNDLE,m.platform._scope(ACTOR))
            self.assertTrue(m._reserve_attempt(ACTOR[0],fp,workbench=True))
            response = m.generate_for_workbench(ACTOR,RUN)
            self.assertEqual(response['status'],'unavailable')
            self.assertIsNone(response['provider_calls'])
            self.assertEqual(response['dispatch_state'],'previous_attempt_unconfirmed')
            self.assertEqual(m.generate(ACTOR,RUN),response)
            provider.assert_not_called()

    def test_fixture_evaluation_requires_observed_run_and_leaves_human_gold_empty(self):
        m = self.module()
        from scripts.week12_triage.fixtures import evaluate
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.identity,'default_db_path',return_value=Path(tmp)/'identity.db'), \
                patch.object(m.platform,'_scope',return_value='scope'):
            self.assertEqual(m.read_evaluation(ACTOR)['fixtures'], [])
            rows = evaluate()
            self.assertEqual(len(rows),4)
            self.assertTrue(all(r['verified'] and r['source']=='isolated' for r in rows))
            m.publish_evaluation(ACTOR,rows)
            stored = m.read_evaluation(ACTOR)
            self.assertEqual((stored['human_labeled'],stored['gold_target'],stored['accuracy']), (0,20,None))
            self.assertEqual(stored['fixtures'], rows)
            self.assertEqual(m.read_evaluation(('other','other','other'))['fixtures'], [])


if __name__ == '__main__':
    unittest.main()
