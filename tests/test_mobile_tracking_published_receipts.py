import json
import unittest
from xml.etree import ElementTree
from zipfile import ZipFile

from task_server.services.mobile_tracking_receipt import ASSETS, published_receipt


class PublishedReceiptsTest(unittest.TestCase):
    def test_every_published_link_matches_its_original_excel_picture(self):
        reports = json.loads((ASSETS.parent / 'tracking-acceptance.json').read_text())['reports']
        honor, iphone = reports
        self.assertTrue(all('receiptIds' not in row for row in iphone['rows']))
        links = [row for row in honor['rows'] if row.get('receiptIds')]
        self.assertTrue(links)
        self.assertTrue(all(row['status'] != '待实测' for row in links))
        linked = {rid for row in links for rid in row['receiptIds']}
        with ZipFile(ASSETS / 'honor-tracking.xlsx') as book:
            shared = [''.join(item.itertext()) for item in ElementTree.fromstring(book.read('xl/sharedStrings.xml'))]
            expected = {}
            for sheet in ('sheet3', 'sheet4'):  # exposure rules and individual action checks
                rows = ElementTree.fromstring(book.read('xl/worksheets/'+sheet+'.xml')).findall('.//{*}row')
                for row in rows[1:]:
                    cells = {}
                    for cell in row.findall('{*}c'):
                        value = cell.findtext('{*}v')
                        if value is not None:
                            cells[cell.get('r').rstrip('0123456789')] = shared[int(value)] if cell.get('t')=='s' else value
                    if cells.get('F') and cells.get('D') != '待实测':
                        expected.setdefault(cells['A'],set()).add(cells['F'])
            self.assertEqual({row['origin']:set(row['receiptIds']) for row in links}, expected)
            labels = {cell.get('r'): cell.findtext('{*}v') for cell in
                      ElementTree.fromstring(book.read('xl/worksheets/sheet8.xml')).findall('.//{*}c')}
            rels = {rel.get('Id'): rel.get('Target').lstrip('/') for rel in
                    ElementTree.fromstring(book.read('xl/drawings/_rels/drawing1.xml.rels'))}
            members = {}
            for anchor in ElementTree.fromstring(book.read('xl/drawings/drawing1.xml')):
                rid = labels['A' + anchor.findtext('{*}from/{*}row')]
                relation = anchor.find('.//{*}blip').get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                members[rid] = rels[relation]
            for rid in linked:
                self.assertEqual(published_receipt({'report': 'honor', 'id': rid}), book.read(members[rid]))
            for rid in members.keys() - linked:
                with self.assertRaises(ValueError):
                    published_receipt({'report': 'honor', 'id': rid})

    def test_only_fixed_report_and_receipt_ids(self):
        for payload in [None, {'report': 'iphone', 'id': 'c3eeb81075320e61'},
                        {'report': 'honor', 'id': '../sessions.sqlite'}, {'report': 'honor', 'id': []}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                published_receipt(payload)
