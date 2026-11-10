import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import candidate_replay as gate
from task_server.services import requirement_design_model as model


class ControlledReplayTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.src=self.root/'source'
        self.src.mkdir()
        self.out=self.root/'output'
        self.reviews=self.root/'reviews.json'
        cases=[];origins=[];fixtures=[];events=[]
        for i in range(10):
            cid=f'case-{i}'
            ver=f'uuid-v{i}'
            cases.append(dict(id=cid,title='测试夹具',source='平台用例版本 '+ver,steps=['读项目'],expected='业务码与名称匹配',layer='api',version=1,status='pending_review',risk='业务',precondition='',cleanup=''))
            origins.append(dict(id=cid,case_version_id=ver,platform_status='draft',run_id='r'+str(i),execution_case_id='x'+str(i),
                                active_baseline_id='baseline-test'))
            fixtures.append(dict(id=cid,case_version_id=ver,method='GET',path='/demo/items',run_id='r'+str(i),
                                 execution_case_id='x'+str(i),scope='sanitized_response_projection',http_status=200,
                                 body={'code':1001,'name':'示例'},assertions=[
                                     dict(type='json_path',path='$.code',operator='equals',expected=1001),
                                     dict(type='json_path',path='$.name',operator='equals',expected='示例')]))
            origins[-1]['version_assertions']=fixtures[-1]['assertions']
            origins[-1]['enabled_step_assertion_count']=0
            events.append(dict(record_type='case_review',decision='approved',case_id=cid,case_version_id=ver,
                               reviewed_at='2025-01-01T01:00:00Z',event_id='event'+str(i),reviewer='fixture',
                               source_ref='fixture://review/'+str(i)))
        for p,v in [(self.src/'cases.json',dict(schema_version=1,cases=cases)),(self.src/'provenance.json',dict(cases=origins)),
                    (self.src/'replay.json',dict(fixtures=fixtures)),(self.reviews,dict(reviews=events))]:
            p.write_text(json.dumps(v))
        _,self.eligible=gate.inspect_inputs(self.src,self.reviews)
        self.valid={'candidates':[dict(id=cid,method='GET',path='/demo/items',assertions=['A2','A1']) for cid in self.eligible]}

    def test_drafts_and_baseline_flags_alone_never_spend_or_generate(self):
        with patch.object(model,'_call',side_effect=AssertionError('must not call model')):
            result=gate.build(self.src,None,'generate',self.out)
        self.assertEqual(result['generated'],0)
        self.assertEqual(result['audit']['offline_controls'],10)
        self.assertEqual(result['audit']['reason_counts'],{'missing_approved_version_review':10})
        self.assertFalse(list(self.out.glob('*.py')))

    def test_latest_review_wins_and_wrong_version_cannot_admit(self):
        reviews=gate.read(self.reviews)
        reviews['reviews'].append(dict(reviews['reviews'][0],reviewed_at='2026-01-01T00:00:00Z',decision='revoked'))
        reviews['reviews'][1]['case_version_id']='other'
        self.reviews.write_text(json.dumps(reviews))
        audit,eligible=gate.inspect_inputs(self.src,self.reviews)
        self.assertEqual(audit['eligible_count'],8)
        self.assertNotIn('case-0',eligible)
        self.assertNotIn('case-1',eligible)

    def test_held_bad_corpora_reject_and_do_not_touch_baseline_or_existing_output(self):
        snapshot={p:p.read_bytes() for p in self.src.glob('*')}
        bad_samples=[
            ('huibian-endpoint',lambda c:c.update(path='/admin/erase')),
            ('delete-assertion',lambda c:c.update(assertions=['A1'])),
            ('invent-code',lambda c:c.update(python='import os')),
        ]
        for _,change in bad_samples:
            raw=copy.deepcopy(self.valid);change(raw['candidates'][0])
            with patch.object(model,'_call',return_value=(raw,{'id':'test'},'req-fixture')),self.assertRaises(ValueError):
                gate.build(self.src,self.reviews,'generate',self.out)
            self.assertFalse(self.out.exists())
        with patch.object(model,'_call',side_effect=ValueError('invalid_model_response')),self.assertRaises(ValueError):
            gate.build(self.src,self.reviews,'generate',self.out)
        self.out.mkdir()
        (self.out/'existing').write_text('baseline')
        with self.assertRaisesRegex(ValueError,'immutable'):
            gate.build(self.src,self.reviews,'generate',self.out)
        self.assertEqual((self.out/'existing').read_text(),'baseline')
        self.assertTrue(all(p.read_bytes()==b for p,b in snapshot.items()))

    def test_malformed_json_is_rejected_before_model_spend_or_output(self):
        source=self.src/'cases.json'
        original=source.read_bytes()
        source.write_text('{"cases": [}')
        with patch.object(model,'_call',side_effect=AssertionError('should not spend')), self.assertRaises(json.JSONDecodeError):
            gate.build(self.src,self.reviews,'generate',self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(source.read_text(),'{"cases": [}')
        source.write_bytes(original)

    def test_missing_expectation_and_status_only_excluded(self):
        cases=gate.read(self.src/'cases.json')
        cases['cases'][0]['expected']=''
        (self.src/'cases.json').write_text(json.dumps(cases))
        with self.assertRaisesRegex(ValueError,'expected'):
            gate.inspect_inputs(self.src,self.reviews)
        cases['cases'][0]['expected']='业务码与名称匹配'
        (self.src/'cases.json').write_text(json.dumps(cases))
        fixtures=gate.read(self.src/'replay.json')
        fixtures['fixtures'][1]['assertions']=[dict(type='status_code',operator='equals',expected=200)]
        (self.src/'replay.json').write_text(json.dumps(fixtures))
        audit,_=gate.inspect_inputs(self.src,self.reviews)
        self.assertEqual(audit['eligible_count'],9)

    def test_fixture_subset_cannot_discard_even_one_approved_assertion(self):
        fixtures=gate.read(self.src/'replay.json')
        fixtures['fixtures'][0]['assertions'].pop()
        (self.src/'replay.json').write_text(json.dumps(fixtures))
        audit,_=gate.inspect_inputs(self.src,self.reviews)
        self.assertEqual(audit['reason_counts']['replay_does_not_cover_frozen_assertions'],1)
        self.assertEqual(audit['eligible_count'],9)

    def test_full_body_schema_and_recorded_step_must_match_frozen_version(self):
        fixtures=gate.read(self.src/'replay.json')
        origins=gate.read(self.src/'provenance.json')
        row,origin=fixtures['fixtures'][0],origins['cases'][0]
        row['scope']='complete_sanitized_response'
        row['assertions'].append(dict(type='schema',operator='equals',expected={
            'type':'object','required':['name'],'properties':{'name':{'type':'string'}}}))
        origin['version_assertions']=copy.deepcopy(row['assertions'])
        step=dict(phase='setup_steps',index=0,method='GET',path='/demo/setup',
                  assertions=[dict(type='json_path',operator='equals',path='$.code',expected=0)])
        origin['version_step_assertions']=[copy.deepcopy(step)]
        origin['enabled_step_assertion_count']=1
        row['steps']=[dict(step,http_status=200,body={'code':0})]
        (self.src/'provenance.json').write_text(json.dumps(origins))

        def inspect():
            (self.src/'replay.json').write_text(json.dumps(fixtures))
            return gate.inspect_inputs(self.src,self.reviews)

        report,eligible=inspect()
        self.assertIn('case-0',eligible)
        order=list(range(len(eligible['case-0']['checks'])))
        bundle=[dict(id='case-0',order=order,script=gate.render('case-0',order),fixture=row)]
        self.assertEqual(gate.sandbox(bundle)['passed'],1)
        row['steps'][0]['body']['code']=200
        self.assertNotIn('case-0',inspect()[1])
        with self.assertRaisesRegex(ValueError,'isolated_replay_failed'):
            gate.sandbox(bundle)
        row['steps'][0]['body']['code']=0
        row['steps'][0]['path']='/other'
        self.assertNotIn('case-0',inspect()[1])
        row['steps']=[]
        self.assertNotIn('case-0',inspect()[1])
        row['steps']=[dict(step,http_status=200,body={'code':0})]
        row['scope']='sanitized_response_projection'
        self.assertNotIn('case-0',inspect()[1])

    def test_staged_ten_are_collectible_pass_business_checks_and_fail_mutated_body(self):
        receipt={'id':'fake-response','model':'fixture','usage':{'total_tokens':10}}
        with patch.object(model,'_call',return_value=(self.valid,receipt,'fake-request')) as call:
            result=gate.build(self.src,self.reviews,'generate',self.out)
        prompt=call.call_args.args[1][0]['text']
        self.assertNotIn('fixture://review',prompt)
        self.assertNotIn('1001',prompt)
        self.assertEqual(result['verification'],{'collected':10,'passed':10,'negative_controls':10})
        self.assertEqual(len(list(self.out.glob('test_candidate_*.py'))),10)
        item=gate.read(self.out/'replay-bundle.json')[0]
        for script in [item['script']+'\nimport socket\n',item['script'].replace('self.assertTrue','bool')]:
            with self.assertRaisesRegex(ValueError,'isolated_replay_failed'):
                gate.sandbox([dict(item,script=script)])
