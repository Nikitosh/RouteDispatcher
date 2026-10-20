"""Сколько и каких бригад не хватает (planning/fleet.py) на задачах «на прямой»."""
from dispatch.planning import fleet
from tests.helpers import brigade, line_problem, order


def test_shortage_names_mode_skills_and_short_shift():
    """Вечерняя авария только на машине и утренний ремонт далеко: Б1 (ремонт, пешком) не берёт ни одну."""
    orders = [order(1, 'Авария', (600, 700), x=5, need={'car'}), order(2, 'Ремонт', (0, 60), x=40),
              order(3, 'Ремонт', (0, 600), x=1)]
    p = line_problem(orders, [brigade('Б1', 'foot', (0,))])
    items = fleet.shortage(p, [0, 1])
    assert len(items) == 1                                             # одна машина успевает и туда, и туда
    x = items[0]
    assert (x['mode'], x['skills'], x['orders']) == ('car', ['Ремонт', 'Аварии'], ['2', '1'])
    assert x['shift'] == '10:00–22:00' and x['start'] == 'офис'
    only_evening = fleet.shortage(p, [0])[0]
    assert only_evening['shift'] == '13:00–22:00' and only_evening['skills'] == ['Аварии']
    assert fleet.text([only_evening]) == ['+1 бригада: Авто; Аварии; старт — офис; 13:00–22:00 → закроет 1 (5,0 км)']


def test_shortage_reports_impossible_orders():
    orders = [order(1, 'Ремонт', (0, 5), x=50)]                        # за 5 минут на 50 км не доехать никому
    items = fleet.shortage(line_problem(orders, [brigade('Б1')]), [0])
    assert items == [dict(mode=None, orders=['1'])]
    assert fleet.text(items) == ['не закрыть добавкой бригад: 1']
