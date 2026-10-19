"""Табличний експорт без залежностей: безпечні CSV та справжній XLSX."""

import base64
import csv
import io
from decimal import Decimal
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";")
    for row in rows:
        values = []
        for value in row:
            if isinstance(value, Decimal):
                values.append(str(value).replace(".", ","))
            else:
                value = str(value)
                # Do not allow descriptions to become spreadsheet formulas.
                values.append("'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value)
        writer.writerow(values)
    return stream.getvalue().encode("utf-8-sig")


def xlsx_bytes(rows):
    cells = []
    for index, row in enumerate(rows, 1):
        values = []
        for column, value in enumerate(row):
            reference = f"{chr(65 + column)}{index}"
            if isinstance(value, Decimal):
                values.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                values.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">'
                              f'{escape(str(value))}</t></is></c>')
        cells.append(f'<row r="{index}">' + ''.join(values) + '</row>')
    files = {
        '[Content_Types].xml': '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''',
        '_rels/.rels': '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        'xl/workbook.xml': '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Звіт" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        'xl/_rels/workbook.xml.rels': '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''',
        'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(cells) + '</sheetData></worksheet>',
    }
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + content)
    return output.getvalue()


def encoded(rows):
    return {"csv": base64.b64encode(csv_bytes(rows)).decode(),
            "xlsx": base64.b64encode(xlsx_bytes(rows)).decode()}
