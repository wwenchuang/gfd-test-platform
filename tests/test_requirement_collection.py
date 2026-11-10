import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile, ZIP_DEFLATED

from task_server import requirement_collection_http as collection
from task_server.services import standard_cases


def row(cid='A', title='中文\n模型', version=1):
    return dict(id=cid,version=version,source='P1',title=title,risk='待评估',precondition='',
                steps=['点击添加'],expected='新增一个模型',cleanup='',layer='manual',status='pending_review')


def body(rows, fmt='json'):
    doc={'schema_version':1, 'cases':rows}
    if fmt=='json':
        raw=json.dumps(doc, ensure_ascii=False).encode()
    else:
        buf=io.BytesIO()
        standard_cases.write_xlsx(doc,buf)
        raw=buf.getvalue()
    return {'format':fmt, 'file_base64':base64.b64encode(raw).decode()}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ctx=patch.object(collection.design,'_root',return_value=Path(temp.name))
        ctx.start();self.addCleanup(ctx.stop)
        ctx=patch.object(collection.design,'authorized',return_value=True)
        self.access=ctx.start();self.addCleanup(ctx.stop)

    def import_rows(self, rows, fmt='json', owner='me'):
        return collection.import_file(owner,'username',body(rows,fmt))

    def test_first_import_round_trip_and_identity_isolation(self):
        rows=[row('case-'+str(i)) for i in range(100)]
        self.assertEqual(self.import_rows(rows,'xlsx')['added'],100)
        self.assertEqual(self.import_rows(rows)['unchanged'],100)
        self.assertEqual(collection.read('me')['cases'],rows)
        self.assertEqual(collection.read('other')['cases'],[])
        self.assertEqual(self.import_rows([row('case-0','另一个账号')],owner='other')['added'],1)
        self.assertEqual(collection.read('me')['cases'][0],rows[0])

    def test_concurrent_same_version_and_partial_failure_rollback(self):
        self.import_rows([row()])
        with ThreadPoolExecutor(2) as executor:
            jobs=[executor.submit(self.import_rows,[row(title=title)]) for title in ('版本甲','版本乙')]
        self.assertEqual(sum(isinstance(j.exception(),collection.Conflict) for j in jobs),1)
        current=collection.read('me')
        self.assertEqual(current['cases'][0]['version'],2)
        with self.assertRaises(collection.Conflict):
            self.import_rows([row('B'),row(title='旧文档')])
        self.assertEqual(collection.read('me'),current)
        with self.assertRaises(collection.Conflict):
            self.import_rows([row('never-imported',version=2)])

    def test_does_not_accept_excessive_or_untrusted_workbooks(self):
        for raw in (b'not a ZIP',):
            with self.assertRaises(ValueError):
                collection.import_file('me','username',{'format':'xlsx','file_base64':base64.b64encode(raw).decode()})
        for bad in [b'<!DOCTYPE x [<!ENTITY a "boom">]><x/>', b'x'*(21*1024*1024)]:
            buf=io.BytesIO()
            with ZipFile(buf,'w',ZIP_DEFLATED) as z:z.writestr('xl/workbook.xml',bad)
            with self.assertRaises(ValueError):
                collection.import_file('me','username',{'format':'xlsx','file_base64':base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(collection.read('me')['cases'],[])

    def test_denied_import_does_not_parse_or_persist(self):
        self.access.return_value=False
        with self.assertRaises(PermissionError):self.import_rows([row()])
        self.assertEqual(collection.read('me')['cases'],[])
        get,post={},{}
        def registrar(routes):
            def register(path):
                def save(fn):routes[path]=fn;return fn
                return save
            return register
        collection.register_collection_routes(registrar(get),registrar(post),lambda h:None,Mock())
        h=Mock();post[collection.PATH](h,{})
        h._body.assert_not_called()
