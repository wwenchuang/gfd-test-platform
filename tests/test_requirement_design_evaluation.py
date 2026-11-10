import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import requirement_design_evaluation as evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inputs = self.root/'inputs'
        self.output = self.root/'results'
        for group in evaluation.GROUPS:
            path = self.inputs/group
            path.mkdir(parents=True)
            data = dict(title=group,app='fixture',version='test',clauses=[dict(id='P1',text=group)],prototypes=[])
            (path/'source.json').write_text(json.dumps(data))
        self.calls = []
        self.reject = None
        self.model = patch.object(evaluation.design,'dashscope_vl_model',return_value='qwen-test')
        self.model.start()
        self.addCleanup(self.model.stop)
        self.call = patch.object(evaluation.design,'_call',side_effect=self.request)
        self.call.start()
        self.addCleanup(self.call.stop)

    def request(self, model, content, *, system_prompt, capture):
        group = json.loads(content[0]['text'])['title']
        arm = 'few' if evaluation.EXAMPLES in system_prompt else 'zero'
        self.calls.append((group,arm))
        raw = dict(points=[dict(id='T1',title='入口',expected='可见',refs=['P1'])],
                   cases=[dict(id='C1',title='查看',steps=[group],precondition='无',expected=group,point_ids=['T1'])],
                   questions=[],observations=[dict(kind='fact',text='可见',refs=['P1'])])
        if (group,arm)==self.reject:
            raw['observations'][0]['kind']='visual'
        receipt = dict(id='resp-'+group+'-'+arm,model=model,usage={'total_tokens':9},
                       choices=[dict(finish_reason='stop', message={'content':json.dumps(raw)})])
        capture(evaluation.encode(receipt),'req-test')
        return raw, receipt,'req-test'

    def run_eval(self):
        return evaluation.evaluate(self.inputs,self.output)

    def test_real_responses_only_mechanical_metrics_and_private_mapping(self):
        report = self.run_eval()
        self.assertEqual(len(self.calls),8)
        self.assertEqual(self.calls[:4],[('g1','zero'),('g1','few'),('g2','few'),('g2','zero')])
        self.assertEqual(report['status'],'complete')
        self.assertEqual(report['candidate_count'],4)
        self.assertFalse(report['target_met'])
        self.assertEqual(len(report['groups']),4)
        self.assertEqual(report['groups'][0]['variants'][0]['referenced_source_ids'],['P1'])
        self.assertEqual(report['candidates'][0]['refs'],['P1'])
        self.assertEqual(report['blind_review'],dict(status='pending',candidate_count=8,metrics=None))
        mapping = evaluation.read(self.output/'private/blind-key.json')
        blind = evaluation.read(self.output/'blind-review.json')
        for item in blind['items']:
            self.assertNotIn('variant',item)
            self.assertNotIn('id',item)
            self.assertIsNone(item['review'])
            self.assertIn(mapping[item['label']]['variant'],evaluation.ARMS)
            self.assertNotIn(item['label'],json.dumps(report))
        raws = list(self.output.glob('private/g*/**/raw-response.json'))
        self.assertEqual(len(raws),8)
        self.assertTrue(all(p.stat().st_mode & 0o077==0 for p in raws))
        self.calls.clear()
        self.assertEqual(self.run_eval(),report)
        self.assertFalse(self.calls)
        self.assertEqual(evaluation.read(self.output/'blind-review.json'),blind)

    def test_rejected_rows_private_raw_and_retry_only_missing(self):
        self.reject=('g2','few')
        partial = self.run_eval()
        part = partial['groups'][1]['variants'][1]
        self.assertEqual(part['error'],'visual_without_prototype')
        self.assertEqual(partial['candidate_count'],3)
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(len(list((self.output/'private/g2/few').glob('attempt-*/raw-response.json'))),1)
        self.reject=None
        self.calls.clear()
        complete = self.run_eval()
        self.assertEqual(self.calls,[('g2','few')])
        self.assertEqual(complete['status'],'complete')
        self.assertEqual(len(list((self.output/'private/g2/few').glob('attempt-*/raw-response.json'))),2)

    def test_changed_source_rejected_before_spend_even_for_cached_results(self):
        self.run_eval()
        srcfile = self.inputs/'g1/source.json'
        src=evaluation.read(srcfile)
        src['clauses'][0]['text']='new'
        evaluation.save(srcfile,src)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError,'frozen_inputs'):
            self.run_eval()
        self.assertEqual(self.calls,[])

    def test_output_private_and_normalized_duplicate_signatures(self):
        self.output.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError,'private_0700'):
            self.run_eval()
        case1=dict(title='alpha',steps=['Click A'],expected='OK!')
        case2=dict(title='beta',steps=['click Ａ'],expected='ok')
        self.assertEqual(evaluation.fingerprint(case1),evaluation.fingerprint(case2))
        case2['expected']='rejected'
        self.assertNotEqual(evaluation.fingerprint(case1),evaluation.fingerprint(case2))

class UnknownEvidenceTests(unittest.TestCase):
    def test_only_unknowns_can_have_no_source(self):
        source={'P1':{'kind':'clause'}}
        row={'kind':'unknown','text':'没有提供交互原型，按钮状态待确认','refs':[]}
        self.assertEqual(evaluation.observations({'observations':[row]},source),[row])
        for invalid in [dict(row,kind='visual'),dict(row,kind='fact'),dict(row,refs=['V1'])]:
            with self.assertRaises(ValueError):
                evaluation.observations({'observations':[invalid]},source)

    def test_replay_revalidates_original_receipt_without_another_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            attempt=out/'private/g4/few/attempt-0001'
            attempt.mkdir(parents=True)
            draft={'points':[],'cases':[],'questions':['是否支持退回？'],
                   'observations':[{'kind':'unknown','text':'无可见原型','refs':[]}]}
            raw=evaluation.encode({'model':'fixture','id':'test','choices':[{'finish_reason':'stop','message':{'content':json.dumps(draft)}}]})
            (attempt/'raw-response.json').write_bytes(raw)
            evaluation.save(attempt/'response-metadata.json',{'request_id':'req','sha256':evaluation.digest(raw)})
            evaluation.save(attempt/'failure.json',{'report':{'status':'failed','error':'invalid_refs','elapsed_ms':20,'started_at':'a','completed_at':'b'}})
            group={'source_manifest':{'P1':{'kind':'clause'}}}
            with patch.object(evaluation.design,'_call',side_effect=AssertionError):
                row=evaluation.replay_arm(out,'g4',group,'few')
            self.assertTrue(row['report']['revalidated'])
            self.assertEqual(row['report']['request_id'],'req')
            self.assertTrue((attempt/'failure.json').exists())
