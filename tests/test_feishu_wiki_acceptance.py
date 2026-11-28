import os
import unittest
from unittest.mock import patch

from task_server.services import feishu_wiki_acceptance as wiki


class WikiAcceptance(unittest.TestCase):
    def setUp(self):
        env = dict(MIDSCENE_FEISHU_DOCS_HOST='company.feishu.cn', FEISHU_DOCS_APP_ID='test',
                   FEISHU_DOCS_APP_SECRET='only-a-fixture')
        ctx = patch.dict(os.environ, env, clear=True)
        ctx.start()
        self.addCleanup(ctx.stop)
        self.url = 'https://company.feishu.cn/wiki/Abc123?from=copy'

    def test_reads_linked_document_and_preserves_paragraphs(self):
        with patch.object(wiki, '_api', side_effect=[
            {'tenant_access_token':'t'}, {'data':{'node':{'obj_type':'docx','obj_token':'D123'}}},
            {'data':{'content':'标题\n第一条\n\n第二条'}}]) as api:
            self.assertEqual(wiki.read_wiki_clauses({'link':self.url}), ['标题\n第一条\n第二条'])
            self.assertIn('token=Abc123', api.call_args_list[1].args[0])
            self.assertIn('/docx/v1/documents/D123/raw_content', api.call_args_list[2].args[0])

    def test_other_tenant_and_unavailable_content_never_become_requirements(self):
        with patch.object(wiki, '_api') as api:
            for url in ['https://other.feishu.cn/wiki/X','https://company.feishu.cn@other.example/wiki/X',
                        'http://company.feishu.cn/wiki/X']:
                with self.assertRaisesRegex(ValueError,'scope'):
                    wiki.read_wiki_clauses(url)
            api.assert_not_called()
        with self.assertRaisesRegex(ValueError,'limit'):
            wiki._clauses('中'*18001)
        with self.assertRaisesRegex(ValueError,'empty'):
            wiki._clauses('  ')

    def test_no_credentials_or_document_access(self):
        with patch.dict(os.environ, {'FEISHU_DOCS_APP_SECRET':''}), self.assertRaisesRegex(ValueError,'credentials'):
            wiki.read_wiki_clauses(self.url)
        with patch.object(wiki, '_api', side_effect=[{'tenant_access_token':'t'},
                {'data':{'node':{'obj_type':'sheet','obj_token':'ABC'}}}]), self.assertRaisesRegex(ValueError,'type'):
            wiki.read_wiki_clauses(self.url)
