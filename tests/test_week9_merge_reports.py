import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.week9_visual import merge_reports as merge


class MergeReportsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.image = b'\x89PNG\r\n\x1a\nfixture'
        self.hash = hashlib.sha256(self.image).hexdigest()

    def batch(self, tag, ids):
        folder = self.root / tag
        images = folder / 'visual-artifacts'
        images.mkdir(parents=True)
        (images / (self.hash+'.png')).write_bytes(self.image)
        scenarios = [dict(id=s, title=s, assertion='独立断言') for s in ids]
        limits = dict(rounds=10,model_calls_total=110,model_calls_per_run=3,output_tokens_per_call=512)
        runs = []
        for s in ids:
            for arm in ('ai','control'):
                for n in range(1,11):
                    labels = ('填写前的用例目录','已填写真实基线名称','回车查询后，清单和目标版本已核对','清空后恢复原清单') if s==merge.SEARCH else ('操作前','操作后')
                    shots = [dict(file=self.hash+'.png',sha256=self.hash,label=k) for k in labels]
                    runs.append(dict(scenario_id=s,arm=arm,round=n,status='passed',reason='',actions=1,
                                     model_calls=int(arm=='ai'),tokens=8 if arm=='ai' else 0,
                                     condition_sha256=self.hash,steps=[dict(calls=[dict(tokens=8)] if arm=='ai' else [])],screenshots=shots))
        doc=dict(version=1,batch_id=tag,fingerprint=hashlib.sha256(tag.encode()).hexdigest(),completed=True,
                 model='model',family='qwen3',versions={'browser':'1'},viewport={'width':900}, limits=limits,
                 scenarios=scenarios,runs=runs,conditions={s:self.hash for s in ids},condition_sha256=self.hash,started_at='2026-11-10T01:00:00Z',
                 generated_at='2026-11-10T02:00:00Z',usage=dict(calls=len(runs)//2,reported_tokens=4*len(runs),unreported_calls=0))
        (folder/'w9-report.json').write_text(json.dumps(doc))
        return folder/'w9-report.json'

    def test_merge_preserves_every_round_and_source(self):
        base=self.batch('main',merge.PRIMARY)
        extra=self.batch('search',{merge.SEARCH})
        out=self.root/'merged'
        merge.merge(base,extra,out)
        doc=json.loads((out/'w9-report.json').read_bytes())
        self.assertEqual(len(doc['runs']),120)
        self.assertEqual(doc['usage']['calls'],60)
        self.assertEqual(len(doc['sources']),2)
        self.assertEqual(doc['runs'][-1]['source_batch_id'],'search')
        self.assertEqual(len(list((out/'visual-artifacts').iterdir())),1)
        self.assertEqual(set(doc['sources'][1]['limits']),set(doc['sources'][0]['limits']))

    def test_rejects_duplicate_trials_unreported_usage_and_damaged_picture(self):
        base=self.batch('base',merge.PRIMARY)
        extra=self.batch('search',{merge.SEARCH})
        original=json.loads(extra.read_bytes())
        for field in ('duplicate','usage','png'):
            changed=copy.deepcopy(original)
            if field=='duplicate': changed['runs'][0]=changed['runs'][1]
            elif field=='usage': changed['usage']['reported_tokens']+=1
            else: (extra.parent/'visual-artifacts'/(self.hash+'.png')).write_bytes(b'invalid')
            extra.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                merge.merge(base,extra,self.root/'out')
            self.assertFalse((self.root/'out').exists())
