import io
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy

from openpyxl import Workbook, load_workbook
from task_server.services import standard_cases as cases


def sample(number):
    return {
        'id': f'QA-{number:03}', 'version': 1, 'source': '冻结需求 P1',
        'title': '=中文标题与公式前缀', 'risk': '实体更新后数据未同步',
        'precondition': '', 'steps': ['打开页面\n保持登录', '检查关联模型'],
        'expected': '字段存在\n且顺序正确', 'cleanup': '', 'layer': 'ui',
        'status': 'pending_review',
    }


def book(records):
    buf = io.BytesIO()
    cases.write_xlsx({'schema_version': 1, 'cases': records}, buf)
    buf.seek(0)
    return buf


def edit(stream, change):
    wb = load_workbook(stream)
    change(wb)
    out = io.BytesIO()
    wb.save(out)
    wb.close()
    out.seek(0)
    return out


class StandardCaseExchange(unittest.TestCase):
    def test_100_rows_chinese_empty_multiline_literal_formula_roundtrip(self):
        records = [sample(i) for i in range(100)]
        doc = {'schema_version': 1, 'cases': records}
        rows = cases.read_xlsx(book(records))
        self.assertEqual(cases.merge_rows(doc, rows), doc)
        self.assertEqual([r['id'] for r in rows], [r['id'] for r in records])

    def test_update_add_and_keep_unmentioned_rows(self):
        records = [sample(1), sample(2)]
        doc = {'schema_version': 1, 'cases': records}
        rows = cases.read_xlsx(book([sample(1), sample(3)]))
        rows[0]['expected'] = '新的期望'
        rows[1]['expected_version'] = 0
        result = cases.merge_rows(doc, rows)
        self.assertEqual([r['version'] for r in result['cases']], [2, 1, 1])
        self.assertEqual(result['cases'][1], records[1])
        self.assertEqual(result['cases'][0]['expected'], '新的期望')
        self.assertEqual(doc['cases'][0]['expected'], '字段存在\n且顺序正确')

    def test_conflict_is_atomic_and_no_implicit_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cases.json'
            xlsx = Path(tmp) / 'roundtrip.xlsx'
            original = {'schema_version': 1, 'cases': [sample(1), sample(2)]}
            path.write_bytes(json.dumps(original).encode())
            before = path.read_bytes()
            def conflict(wb):
                sheet = wb['标准用例']
                sheet['D2'] = '有效修改'
                sheet['B3'] = 2
            xlsx.write_bytes(edit(book(original['cases']), conflict).getvalue())
            with self.assertRaises(SystemExit) as rejected:
                cases.main(['import', str(path), str(xlsx)])
            self.assertEqual(rejected.exception.code, 2)
            self.assertEqual(path.read_bytes(), before)

    def test_duplicate_empty_expectation_unknown_and_bad_types_refused(self):
        case = sample(1)
        for field,value in [('expected', ' '), ('status','approved'), ('layer','sql'),
                            ('version',True), ('steps',[]), ('id','x/y'), ('risk','\x01')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                cases.validate({'schema_version':1, 'cases':[dict(case, **{field:value})]})
        for records in ([case, case], [dict(case, external='unknown')]):
            with self.assertRaises(ValueError):
                cases.validate({'schema_version':1, 'cases':records})
        with self.assertRaises(ValueError):
            cases.parse_json('{"schema_version":1,"cases":[],"cases":[]}')

    def test_reject_formula_unknown_step_and_missing_column(self):
        changes = (
            lambda wb: wb['标准用例'].__setitem__('D2', '=2+2'),
            lambda wb: wb['操作步骤'].__setitem__('A2', 'unknown'),
            lambda wb: wb['操作步骤'].__setitem__('B2', 3),
            lambda wb: wb['标准用例'].delete_cols(2),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                cases.read_xlsx(edit(book([sample(1)]), change))
        with self.assertRaises(ValueError):
            cases.read_xlsx(io.BytesIO(b'not a workbook'))

    def test_reordered_step_sheet_renders_in_step_number_order(self):
        def shuffle(wb):
            ws = wb['操作步骤']
            a, b = [tuple(c.value for c in row) for row in ws.iter_rows(min_row=2)]
            ws.delete_rows(2, 2)
            ws.append(b)
            ws.append(a)
        rows = cases.read_xlsx(edit(book([sample(1)]), shuffle))
        self.assertEqual(rows[0]['steps'], sample(1)['steps'])

    def test_schema_describes_the_same_fields(self):
        schema = json.loads((Path(__file__).resolve().parents[1] /
            'task_server/fixtures/test_authoring/testcase.schema.json').read_bytes())
        item = schema['properties']['cases']['items']
        self.assertEqual(set(item['required']),set(cases.FIELDS))
        self.assertEqual(set(item['properties']),set(cases.FIELDS))
