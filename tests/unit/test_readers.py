import datetime

import openpyxl
import pytest

from dispatch.files.readers import InputError, parse_minutes, parse_modes, parse_skills, read_brigades, read_orders
from dispatch.files.tables import read_table

ORDERS_CSV = """Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение
101;Подключение;Конвергенция абонента;17.08.2026 10:00;17.08.2026 12:00;Лефортово;Город Москва, ул.Юности, д. 1;Нет
102;Глобальная проблема;Авария;17.08.2026 0:01;17.08.2026 23:59;Кузьминки;Город Москва, ул.Юности, д. 2;Нет
Адрес офиса;г. Москва, ул Юных Ленинцев, д 83с 4
"""


def write(tmp_path, name, text, encoding='cp1251'):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


def test_orders_from_organizers_csv(tmp_path):
    f = read_orders(write(tmp_path, 'o.csv', ORDERS_CSV))
    assert f.office == 'г. Москва, ул Юных Ленинцев, д 83с 4'
    assert f.date == '17.08.2026'
    a, b = f.orders
    assert (a.type, a.window, a.service, a.priority, a.skill) == ('Подключение', (0, 120), 70, 2, 1)
    assert b.type == 'Авария' and b.window == (-599, 839) and b.earliest == 0
    assert a.need is None


def test_utf8_and_transport_column(tmp_path):
    text = ORDERS_CSV.replace('Гигабитное подключение', 'Гигабитное подключение;Транспорт').replace(';Нет\n101', ';Нет;авто\n101')
    lines = text.splitlines()
    lines[1] += ';авто или велосипед'
    lines[2] += ';'
    f = read_orders(write(tmp_path, 'o.csv', '\n'.join(lines), 'utf-8'))
    assert f.orders[0].need == frozenset({'car', 'bike'})
    assert f.orders[1].need is None


def test_orders_errors_point_to_line(tmp_path):
    bad = ORDERS_CSV.replace('Глобальная проблема', 'Что-то новое')
    with pytest.raises(InputError, match='строка 3: неизвестный тип заявки'):
        read_orders(write(tmp_path, 'o.csv', bad))
    with pytest.raises(InputError, match='Адрес офиса'):
        read_orders(write(tmp_path, 'o.csv', ORDERS_CSV.rsplit('Адрес офиса', 1)[0]))


def test_brigades(tmp_path):
    text = 'Бригада;Транспорт;Навыки;Старт\nБ1;Авто;Ремонт, Подключение и дозаказ;\nБ2;пешком;аварии;55.1, 37.2\n'
    b1, b2 = read_brigades(write(tmp_path, 'b.csv', text))
    assert (b1.mode, b1.skills, b1.start_address) == ('car', [0, 1], '')
    assert (b2.mode, b2.skills, b2.start_address) == ('foot', [2], '55.1, 37.2')
    with pytest.raises(InputError, match='строка 2: неизвестный транспорт «самокат»'):
        read_brigades(write(tmp_path, 'b.csv', 'Бригада;Транспорт;Навыки\nБ1;самокат;ремонт\n'))


def test_small_parsers():
    assert parse_minutes('17.08.2026 12:30') == 150
    assert parse_minutes('9:00') == -60
    assert parse_modes('') is None and parse_modes('любой') is None
    assert parse_modes('ОТ, пешком') == frozenset({'pt', 'foot'})
    assert parse_skills('L, c, Аварии') == [0, 1, 2]
    with pytest.raises(InputError):
        parse_skills('')


def test_xlsx_table(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Заявка', 'Начало', 'Число'])
    ws.append([7, datetime.datetime(2026, 8, 17, 14, 0), 3.0])
    ws.append([None, None, None])
    p = tmp_path / 't.xlsx'
    wb.save(p)
    assert read_table(str(p)) == [['Заявка', 'Начало', 'Число'], ['7', '17.08.2026 14:00', '3']]
