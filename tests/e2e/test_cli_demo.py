"""Сквозные тесты: настоящие решатели и кэши на демо-данных Востока, без OSRM (адреса серверов заведомо недоступны)."""
import json
import os
import subprocess
import sys

import openpyxl
import pytest

from dispatch.config import DATA_DIR, SOLVER_BIN

pytestmark = pytest.mark.e2e
ORDERS = os.path.join(DATA_DIR, 'demo', 'Восток заявки.xlsx')
BRIGADES = os.path.join(DATA_DIR, 'demo', 'Восток бригады.xlsx')
OFFLINE = {**os.environ, 'OSRM_CAR': 'http://127.0.0.1:9', 'OSRM_BIKE': 'http://127.0.0.1:9', 'OSRM_FOOT': 'http://127.0.0.1:9'}

if not all(os.path.exists(os.path.join(SOLVER_BIN, b)) for b in ('plan', 'baseline', 'fleet_bound')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)


def cli(*args):
    p = subprocess.run([sys.executable, '-m', 'dispatch', *args], capture_output=True, text=True, env=OFFLINE, timeout=300)
    return p.returncode, p.stdout + p.stderr


@pytest.fixture(scope='module')
def planned(tmp_path_factory):
    out = tmp_path_factory.mktemp('vostok')
    code, text = cli('plan', ORDERS, '--name', 'Восток', '-b', BRIGADES, '-o', str(out))
    assert code == 0, text
    return out, json.load(open(out / 'plan.json')), text


def test_plan_is_valid_and_complete(planned):
    out, doc, text = planned
    m, base = doc['plan']['metrics'], doc['baseline']['metrics']
    assert doc['plan']['check'] == dict(ok=True, errors=[])
    assert m['served'] == m['orders'] == 66
    assert m['brigades_used'] == doc['lower_bound'] == 7            # минимум бригад доказан нижней границей
    assert m['brigades_used'] < base['brigades_used'] and m['km_total'] < base['km_total']
    assert all(o['explanation'] for o in doc['orders'] if o['plan'])
    assert all(r['geometry'] for r in doc['plan']['routes'])        # линии по улицам из кэша, без OSRM
    assert 'оптимален по числу бригад' in text


def test_car_only_orders_get_cars(planned):
    _, doc, _ = planned
    for o in doc['orders']:
        if o['need']:
            assert doc['brigades'][o['plan']['brigade']]['mode'] in o['need']


def test_xlsx_report(planned):
    out, _, _ = planned
    names = openpyxl.load_workbook(out / 'plan.xlsx').sheetnames
    assert names == ['Маршруты', 'Бригады', 'Метрики', 'Неназначенные', 'Объяснения', 'Базовый вариант', 'Базовый — неназначенные']


@pytest.mark.parametrize('event', [
    ['--unavailable', 'Бригада 4'],
    ['--cancel', '74198'],
    ['--urgent', '55.7155, 37.7625', '--type', 'подключение', '--window', '16:00-18:00'],
    ['--urgent', '55.7400, 37.7200', '--type', 'авария'],
])
def test_replan_events(planned, tmp_path, event):
    out, before, _ = planned
    code, text = cli('replan', str(out), '--at', '14:00', *event, '-o', str(tmp_path))
    assert code == 0, text
    doc = json.load(open(tmp_path / 'plan.json'))
    assert doc['plan']['check']['ok'], doc['plan']['check']['errors']
    at = doc['event']['at']
    old = {s['k']: s['beg'] for r in before['plan']['routes'] for s in r['stops']}
    new = {s['k']: s['beg'] for r in doc['plan']['routes'] for s in r['stops']}
    for k, start in old.items():                                     # начатое к 14:00 не сдвинулось
        if start < at and k in new:
            assert new[k] == start
    if '--type' in event and 'авария' in event:
        acc = doc['event']['accident']
        assert acc and acc['wait'] <= 120
    assert 'Изменения' in openpyxl.load_workbook(tmp_path / 'plan.xlsx').sheetnames


def test_site(planned, tmp_path):
    out, _, _ = planned
    code, text = cli('site', str(out), '-o', str(tmp_path / 'site'))
    assert code == 0, text
    man = json.load(open(tmp_path / 'site' / 'manifest.json'))
    assert man['districts'][0]['name'] == 'Восток'


def test_bad_input_reports_line(tmp_path):
    bad = tmp_path / 'b.csv'
    bad.write_text('Бригада;Транспорт;Навыки\nБ1;самокат;ремонт\n', encoding='utf-8')
    code, text = cli('plan', ORDERS, '-b', str(bad), '-o', str(tmp_path / 'o'))
    assert code == 1 and 'строка 2: неизвестный транспорт «самокат»' in text
