"""Оборудование: что нужно заявке, сколько выдано бригаде и сколько она унесёт, проверка маршрута целиком."""
import pytest

from dispatch.config import CARRY, equipment_for
from dispatch.files import document as docs
from dispatch.files.readers import InputError, parse_stock, read_brigades
from dispatch.model.domain import Stop
from dispatch.planning.explain import Explainer
from dispatch.planning.validate import check_plan
from tests.helpers import brigade, line_problem, order


@pytest.mark.parametrize('bk, hd, need', [
    ('Подключение', 'Конвергенция абонента', {'router': 1}),
    ('Подключение', 'Заказ подключения/Дозаказ оборудования', {'router': 1}),
    ('Дозаказ', 'Дозаказ оборудования', {'router': 1}),
    ('Подключение', 'Интернет + ТВ', {'router': 1, 'stb': 1}),
    ('Локальная заявка', 'Роутер. Замена техническим специалистом', {'router': 1}),
    ('Локальная заявка', 'ТВ. Замена приставки техником', {'stb': 1}),
    ('Локальная заявка', 'TVE/ENT. Замена приставки техником', {'stb': 1}),
    ('Локальная заявка', 'TVE/ENT. Другие ошибки', {}),
    ('Локальная заявка', 'Нет линка', {}),
    ('Глобальная проблема', 'Авария', {}),
])
def test_equipment_by_type(bk, hd, need):
    assert equipment_for(bk, hd) == need


def test_order_demand_derived_or_explicit():
    o = order(1, 'Подключение')
    assert o.demand == {'router': 1}
    o.equipment = {'router': 0, 'stb': 2}
    assert o.demand == {'stb': 2}
    assert order(2, 'Авария').demand == {}


def test_stock_column_and_carry(tmp_path):
    assert parse_stock('роутер 4, приставка 2') == {'router': 4, 'stb': 2}
    assert parse_stock('ТВ-приставка: 1') == {'router': 0, 'stb': 1}
    assert parse_stock(' ') is None
    with pytest.raises(InputError, match='оборудование'):
        parse_stock('модем 2')
    path = tmp_path / 'b.csv'
    path.write_text('Бригада;Транспорт;Навыки;Оборудование\nБ1;авто;ремонт;роутер 4, приставка 2\nБ2;пешком;подключение;\n')
    b1, b2 = read_brigades(str(path))
    assert (b1.stock, b1.carry) == ({'router': 4, 'stb': 2}, CARRY['car'])
    assert (b2.stock, b2.carry) == (None, 2)


def connections(n, x=1.0):
    return [order(i, 'Подключение', (0, 600), x=x + i) for i in range(n)]


def test_carry_limits_whole_route():
    walker = brigade('Пеший', 'foot', (0, 1))
    walker.carry = 2
    p = line_problem(connections(3) + [order('Р', 'Ремонт', (0, 600), x=2)], [walker])
    assert p.schedule(0, [0, 1, 3]).feasible
    s = p.schedule(0, [0, 1, 2])
    assert not s.feasible and s.on_time and s.short == {'carry': 1}
    assert any('нет оборудования' in e and 'унести можно 2' in e for e in check_plan(p, [[0, 1, 2]]).errors)


def test_explicit_stock_per_item():
    b = brigade('Б1', skills=(0, 1))
    b.stock = {'router': 1, 'stb': 0}
    tv = order('ТВ', 'Ремонт', (0, 600), x=3)
    tv.hd, tv.equipment = 'ТВ. Замена приставки техником', None        # выводится из подтипа: приставка
    p = line_problem(connections(2) + [tv], [b])
    assert p.schedule(0, [0]).feasible
    assert p.schedule(0, [0, 1]).short == {'router': 1}
    assert p.schedule(0, [2]).short == {'stb': 1}
    errs = check_plan(p, [[0, 1, 2]]).errors
    assert any('роутер — по маршруту 2, выдано 1' in e for e in errs) and any('ТВ-приставка' in e for e in errs)


def test_stock_for_document():
    car, bike = brigade('Машина'), brigade('Велосипед', 'bike')
    bike.carry = 3
    p = line_problem(connections(2), [car, bike])
    assert p.stock_for(0, [0, 1]) == {'router': 3, 'stb': 1}          # нужное + по одной запасной
    assert p.stock_for(1, [0, 1]) == {'router': 3, 'stb': 0}          # унесёт только 3
    assert p.stock_for(1, []) == {'router': 1, 'stb': 1}              # резерв: по одной каждого вида
    car.stock = {'router': 5, 'stb': 0}
    assert p.stock_for(0, [0]) == {'router': 5, 'stb': 0}             # задан в файле бригад


def test_document_round_trip_keeps_equipment_and_lunch():
    b = brigade('Б1', 'pt')
    b.carry, b.lunch = 2, False
    p = line_problem(connections(1), [b])
    d = docs.brigade_dict(0, b, {'router': 2, 'stb': 0})
    assert (d['stock'], d['carry'], d['lunch']) == ({'router': 2, 'stb': 0}, 2, False)
    back, _ = docs.brigades_from(dict(brigades=[d], office=dict(lat=0.0, lon=0.0, addr='офис')))
    assert (back[0].stock, back[0].carry, back[0].lunch) == ({'router': 2, 'stb': 0}, 2, False)
    old = {k: v for k, v in d.items() if k not in ('stock', 'carry', 'lunch')}   # документ до обеда и оборудования
    back, _ = docs.brigades_from(dict(brigades=[old], office=dict(lat=0.0, lon=0.0, addr='офис')))
    assert (back[0].stock, back[0].carry, back[0].lunch) == (None, None, False)
    assert docs.order_dict(p, 0)['equipment'] == {'router': 1}
    assert docs.orders_from(dict(orders=[docs.order_dict(p, 0)]))[0].demand == {'router': 1}


def test_replanning_counts_done_part():
    """Запас выдан утром на весь день: выполненные заявки его уже израсходовали."""
    b = brigade('Б1', skills=(1,))
    b.stock = {'router': 2, 'stb': 1}
    p = line_problem(connections(3), [b])
    done = [Stop(0, 1, 1, 71, 1), Stop(1, 72, 72, 142, 1)]
    s = p.schedule(0, [0, 1, 2], release=150, keep=2, done=done)
    assert not s.feasible and s.short == {'router': 1}
    rest = p.rest_brigade(0, [0, 1], lunch_taken=True)
    assert rest.stock == {'router': 0, 'stb': 1} and rest.lunch is False and b.stock == {'router': 2, 'stb': 1}
    walker = brigade('Пеший', 'foot')
    walker.carry = 2
    q = line_problem(connections(2), [walker])
    assert q.rest_brigade(0, [0]).carry == 1 and q.rest_brigade(0, [0]).lunch is True


def test_unassigned_reason_names_missing_equipment():
    b = brigade('Б1', skills=(1,))
    b.stock = {'router': 0, 'stb': 0}
    p = line_problem(connections(1), [b])
    r = Explainer(p).unassigned([[]], 0)
    assert r.code == 'equipment' and 'нет роутера' in r.text
    b.stock = {'router': 1, 'stb': 0}
    p = line_problem(connections(2), [b])
    r = Explainer(p).unassigned([[0], []], 1)
    assert r.code == 'equipment' and 'нет роутера' in r.text and 'Б1' in r.text


def test_write_has_demand_and_capacity(tmp_path):
    walker = brigade('Пеший', 'foot')
    walker.carry, walker.stock = 2, {'router': 1, 'stb': 0}
    tv = order('ТВ', 'Подключение', (0, 600), x=2)
    tv.hd, tv.equipment = 'ТВ', {'router': 1, 'stb': 1}
    p = line_problem([tv], [walker, brigade('Машина')])
    path = tmp_path / 'task.txt'
    p.write(str(path))
    lines = path.read_text().splitlines()
    assert lines[2].split()[6:] == ['1', '1']
    assert lines[3].split()[8:] == ['1', '0', '2'] and lines[4].split()[8:] == ['-1', '-1', '-1']


def test_connection_equipment_is_client_option():
    """Жюри: оборудование — опция клиента. Роутер у ~ROUTER_OPTION % подключений, одинаково при каждом запуске."""
    from dispatch.config import ROUTER_OPTION, STB_OPTION
    got = [equipment_for('Подключение', 'Конвергенция абонента', str(i)) for i in range(2000)]
    share = lambda item: 100 * sum(1 for g in got if g.get(item)) / len(got)
    assert abs(share('router') - ROUTER_OPTION) < 4 and abs(share('stb') - STB_OPTION) < 4
    assert got == [equipment_for('Подключение', 'Конвергенция абонента', str(i)) for i in range(2000)]
    assert equipment_for('Подключение', 'Интернет + ТВ', '7')['stb'] == 1                 # ТВ в подтипе — приставка всегда
    assert equipment_for('Дозаказ', 'Дозаказ оборудования', '7') == {'router': 1}       # дозаказ — всегда оборудование
    assert equipment_for('Локальная заявка', 'ТВ. Замена приставки техником', '7') == {'stb': 1}
