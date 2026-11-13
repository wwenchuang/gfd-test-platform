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
        self.assertEqual(len(links), 57)
        self.assertTrue(all(row['status'] != '待实测' for row in links))
        linked = {rid for row in links for rid in row['receiptIds']}
        self.assertEqual(len(linked), 66)
        with ZipFile(ASSETS / 'honor-tracking.xlsx') as book:
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
