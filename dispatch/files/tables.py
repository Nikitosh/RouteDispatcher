"""Чтение таблиц: CSV (UTF-8 или CP1251, разделитель «;» или «,») и xlsx (первый лист)."""
import csv
import datetime
import io


def _cell(c):
    if c is None:
        return ''
    if isinstance(c, datetime.datetime):
        return c.strftime('%d.%m.%Y %H:%M')
    if isinstance(c, datetime.time):
        return c.strftime('%H:%M')
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c).strip()


def read_table(path):
    """Строки таблицы как списки строк; пустые строки отброшены."""
    if path.lower().endswith(('.xlsx', '.xlsm')):
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
        rows = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
    else:
        raw = open(path, 'rb').read()
        for enc in ('utf-8-sig', 'cp1251'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        delim = ';' if text.count(';') >= text.count(',') else ','
        rows = [[x.strip() for x in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    return [r for r in rows if any(r)]
