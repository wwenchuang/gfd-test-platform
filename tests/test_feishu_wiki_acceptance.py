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

    def test_personal_identity_reads_only_approved_documents_without_bot_token(self):
        env = {'MIDSCENE_FEISHU_DOCS_IDENTITY':'user','MIDSCENE_FEISHU_DOCS_USER_DOCUMENTS':'wiki/Abc123'}
        with patch.dict(os.environ,env), patch.object(wiki,'_personal_get',create=True,side_effect=[
                {'data':{'node':{'obj_type':'docx','obj_token':'D123'}}}, {'data':{'content':'拼图正文'}}]) as get, patch.object(wiki,'_api') as bot:
            self.assertEqual(wiki.read_wiki_clauses(self.url),['拼图正文'])
            with self.assertRaisesRegex(ValueError,'scope'):
                wiki.read_wiki_clauses('https://company.feishu.cn/wiki/Other')
            self.assertEqual(get.call_count,2)
            bot.assert_not_called()

    def test_personal_login_failure_does_not_fall_back(self):
        env={'MIDSCENE_FEISHU_DOCS_IDENTITY':'user','MIDSCENE_FEISHU_DOCS_USER_DOCUMENTS':'wiki/Abc123'}
        with patch.dict(os.environ,env), patch.object(wiki,'_personal_get',create=True,side_effect=ValueError('prd_document_user_authorization_required')),patch.object(wiki,'_api') as bot:
            with self.assertRaisesRegex(ValueError,'user_authorization'):
                wiki.read_wiki_clauses(self.url)
            bot.assert_not_called()

    def test_cli_encodes_query_parameters_and_excludes_service_secrets(self):
        import subprocess
        import json
        with patch.object(wiki.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'{"ok":true,"identity":"user","data":{}}',b'')) as run:
            wiki._personal_get('/wiki/v2/spaces/get_node?token=Abc123')
            args = run.call_args.args[0]
            self.assertEqual(args[3],'/open-apis/wiki/v2/spaces/get_node')
            self.assertEqual(json.loads(args[args.index('--params')+1]),{'token':'Abc123'})
            self.assertEqual(args[args.index('--as')+1],'user')
            self.assertNotIn('FEISHU_DOCS_APP_SECRET',run.call_args.kwargs['env'])

    def test_cli_rejects_application_identity_in_personal_mode(self):
        import subprocess
        with patch.object(wiki.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'{"ok":true,"identity":"bot","data":{}}',b'')):
            with self.assertRaisesRegex(ValueError,'rejected'):
                wiki._personal_get('/docx/v1/documents/A/raw_content')
