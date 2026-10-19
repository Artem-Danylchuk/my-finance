"""Точність цілі, повні місяці, числові клітинки та захист експорту."""

import csv
import io
import unittest
from datetime import date
from decimal import Decimal as D
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from exports import csv_bytes, xlsx_bytes
from savings import target


class ExportSavingsTests(unittest.TestCase):
    def test_goal_uses_complete_months_and_preserves_money_precision(self):
        rows = [(date(2026, 8, 1), D("9800"), D("5703.70")),
                (date(2026, 9, 1), D("9800"), D("6090.91")),
                (date(2026, 10, 1), D("9800"), D("3232.39"))]
        goal = target(rows, date(2026, 10, 15))
        self.assertEqual(goal, {"average": D("5897.31"), "target": D("35383.83"), "months": 2})
        self.assertIsNone(target(rows, date(2026, 8, 15)))
        self.assertIsNone(target([(date(2026, 8, 1), D(0), D(0))], date(2026, 8, 31)))

    def test_csv_and_xlsx_are_readable_and_do_not_execute_text_formulas(self):
        rows = [["Категорія", "Сума PLN"], ['=HYPERLINK("bad")', D("-10.01")],
                ['Житло & <продукти>', D("4864.25")]]
        data = csv_bytes(rows)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        parsed = list(csv.reader(io.StringIO(data.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(parsed[1], ['\'=HYPERLINK("bad")', '-10,01'])
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(io.BytesIO(xlsx_bytes(rows))) as archive:
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(sheet.find('.//s:c[@r="B2"]/s:v', ns).text, '-10.01')
            self.assertEqual(sheet.find('.//s:c[@r="B3"]/s:v', ns).text, '4864.25')
            self.assertIsNone(sheet.find('.//s:f', ns))
            self.assertEqual(sheet.find('.//s:c[@r="A3"]//s:t', ns).text, 'Житло & <продукти>')


if __name__ == '__main__':
    unittest.main()
