import json
from pathlib import Path
import tempfile
import re
import unittest
from unittest.mock import patch

from task_server.services import practice_knowledge_search as knowledge


class KnowledgeSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source_patch = patch.object(knowledge, 'SOURCE_DIR', self.root)
        source_patch.start()
        self.addCleanup(source_patch.stop)
        self.write_doc('visible', 'mobile', '红色按钮会打开照片预览窗口。')
        self.write_doc('private', 'other', '退款金额只能由主管修改。')

    def write_doc(self, doc_id, app, text):
        doc = {'document_id': doc_id, 'title': doc_id, 'kind': 'PRD', 'version': 'v1',
               'app_id': app, 'paragraphs': [{'paragraph_id': 'p1', 'locator': '第1段', 'excerpt': text}]}
        (self.root / (doc_id + '.json')).write_text(json.dumps(doc, ensure_ascii=False))

    def test_search_cites_original(self):
        for method in ('vector', 'keyword'):
            res = knowledge.search('红色按钮打开什么？', allowed_apps=['mobile'], method=method)
            self.assertEqual(res['status'], 'cited')
            self.assertEqual(res['results'][0]['document_id'], 'visible')
            self.assertEqual(res['answer'], res['results'][0]['excerpt'])
            self.assertEqual(res['results'][0]['locator'], '第1段')
            self.assertEqual(res['document_count'], 1)

    def test_scope_denial_and_missing_answer(self):
        for apps, question in [([], '红色按钮'), (['mobile'], '退款金额'), (['mobile'], '红色按钮的海外保修政策是什么')]:
            for method in ('keyword', 'vector'):
                res = knowledge.search(question, allowed_apps=apps, method=method)
                self.assertEqual(res['status'], 'unknown')
                self.assertEqual(res['results'], [])

    def test_withdrawal_and_replacement(self):
        question = '红色按钮'
        self.assertEqual(knowledge.search(question, allowed_apps=['mobile'])['status'], 'cited')
        (self.root / 'visible.json').unlink()
        self.assertEqual(knowledge.search(question, allowed_apps=['mobile'])['document_count'], 0)
        self.write_doc('visible', 'mobile', '售后规则已更新。')
        self.assertEqual(knowledge.search(question, allowed_apps=['mobile'])['results'], [])
        self.assertEqual(knowledge.search('售后规则', allowed_apps=['mobile'])['status'], 'cited')

    def test_invisible_sources_cannot_change_ranking_or_answer(self):
        for method in ('keyword', 'vector'):
            before = knowledge.search('红色按钮', allowed_apps=['mobile'], method=method)
            self.write_doc('shadow', 'other', '红色按钮改为打开转账窗口。')
            after = knowledge.search('红色按钮', allowed_apps=['mobile'], method=method)
            self.assertEqual(before, after)
            (self.root / 'shadow.json').unlink()

    def test_no_stale_excerpt_after_version_change(self):
        self.assertTrue(knowledge.search('红色按钮', allowed_apps=['mobile'])['results'])
        path = self.root / 'visible.json'
        doc = json.loads(path.read_text())
        doc.update(version='v2', app_id='other')
        path.write_text(json.dumps(doc, ensure_ascii=False))
        hidden = knowledge.search('红色按钮', allowed_apps=['mobile'])
        self.assertEqual((hidden['document_count'], hidden['paragraph_count'], hidden['results']), (0, 0, []))
        visible = knowledge.search('红色按钮', allowed_apps=['other'])
        self.assertEqual(visible['results'][0]['version'], 'v2')

    def test_query_validation(self):
        for query, method in [('', 'keyword'), ('abc', 'bad'), ('a' * 501, 'vector')]:
            with self.assertRaises(ValueError):
                knowledge.search(query, allowed_apps=['mobile'], method=method)


class PublishedCorpusTest(unittest.TestCase):
    def test_bounded_sources_and_unambiguous_citations(self):
        files = sorted(knowledge.SOURCE_DIR.glob('*.json'))
        self.assertGreater(len(files), 0)
        self.assertLessEqual(len(files), 20)
        keys = set()
        for path in files:
            doc = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(doc['document_id'], path.stem)
            self.assertRegex(doc['source_sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(doc['source_sha256'].startswith(doc['version'].split(':')[1]))
            for para in doc['paragraphs']:
                key = (doc['document_id'], para['paragraph_id'])
                self.assertNotIn(key, keys)
                keys.add(key)
                self.assertTrue(para['locator'] and para['excerpt'])
                self.assertIsNone(re.search(r'https?://|/Users/|@[\w.-]+|[a-f0-9]{24,}', para['excerpt'], re.I))


if __name__ == '__main__':
    unittest.main()
