import json
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from task_server.services import requirement_standard_export as export
from task_server.services import standard_cases


class StandardDownload(TestCase):
    def test_export_keeps_ids_status_sources_and_observable_expectation(self):
        sid, rid = 'a' * 64, 'b' * 32
        source = dict(title='套件', version='v2', id=sid)
        result = dict(points=[dict(id='T1', refs=['P1','V1']), dict(id='T2', refs=['P1'])],
                      cases=[dict(id='C1', title='=查看', precondition='已登录', steps=['打开','查看'],
                                  expected='显示已关联的模型', point_ids=['T1','T2'])])
        with patch.object(export.requirement_design, 'completed_design', return_value=(source, dict(id=rid, result=result))) as load:
            data = json.loads(export.export('me',sid,rid,'json')[2])
            xlsx = export.export('me',sid,rid,'xlsx')[2]
        load.assert_called_with('me',sid,rid)
        case=data['cases'][0]
        self.assertEqual(case['id'], rid+':C1')
        self.assertEqual(case['status'], 'pending_review')
        self.assertEqual(case['layer'], 'manual')
        self.assertTrue(case['source'].endswith('P1, V1'))
        self.assertEqual(standard_cases.merge_rows(data,standard_cases.read_xlsx(BytesIO(xlsx))),data)

    def test_foreign_result_cannot_be_downloaded(self):
        with patch.object(export.requirement_design,'completed_design',side_effect=export.requirement_design.Missing):
            with self.assertRaises(export.requirement_design.Missing):
                export.export('other','a'*64,'b'*32,'json')
