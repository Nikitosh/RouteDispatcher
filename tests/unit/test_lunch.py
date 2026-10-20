"""Обед (model/problem.py): 45 минут, начало с 13:00 до 15:15 (180..315 от 10:00), между работами или после последней.
Задачи «на прямой»: машина, время в пути = расстояние; работы в одной точке — без переезда."""
import random

import pytest

from dispatch.files import document as docs
from dispatch.model.domain import Stop
from dispatch.planning.validate import check_plan
from tests.helpers import brigade, line_problem, order


def starts(s):
    return [st.start for st in s.stops]


def test_route_ending_before_lunch_needs_no_break():
    p = line_problem([order('A', window=(0, 60), x=10), order('B', window=(0, 200), x=20)], [brigade('Б1')])
    s = p.schedule(0, [0, 1])
    assert s.feasible and starts(s) == [10, 50]
    assert s.lunch is None                        # работа кончается до 13:00 — обед не нужен
    assert check_plan(p, [[0, 1]]).ok


def test_lunch_inside_waiting_time():
    """B открывается в 14:00: бригада приезжает в 10:50, обедает на месте с 13:00, начинает вовремя."""
    p = line_problem([order('A', window=(0, 60), x=10), order('B', window=(240, 400), x=20)], [brigade('Б1')])
    s = p.schedule(0, [0, 1])
    assert s.feasible and starts(s) == [10, 240]
    assert s.lunch == (180, 1) and s.stops[1].arrive == 50


def test_lunch_before_departure():
    """A кончается в 12:55, до B 195 минут: на месте B начать обед до 15:15 нельзя — обед с 13:00 перед выездом."""
    p = line_problem([order('A', window=(145, 145), x=5), order('B', window=(0, 450), x=200)], [brigade('Б1')])
    s = p.schedule(0, [0, 1])
    assert s.feasible and s.lunch == (180, 1)
    assert s.stops[1].arrive == 180 + 45 + 195 == s.stops[1].start
    assert check_plan(p, [[0, 1]]).ok


def test_lunch_before_departure_shifts_arrival_even_without_delay():
    """A кончается в 13:20 (уже в окне обеда), B открывается в 15:00: обед сразу после A, перед выездом. Начало B не
    сдвигается, но приезд — не раньше конца обеда: обед не может идти в дороге."""
    p = line_problem([order('A', window=(170, 170), x=5), order('B', window=(300, 450), x=40)], [brigade('Б1')])
    s = p.schedule(0, [0, 1])
    assert s.feasible and s.lunch[1] == 1
    ls = s.lunch[0]
    a_end = s.stops[0].end
    assert ls == a_end and s.stops[1].arrive >= ls + 45 and s.stops[1].start == 300


def test_lunch_never_overlaps_travel():
    """Случайные маршруты: обед в промежутке перед остановкой g — либо на месте (после приезда), либо перед выездом
    (тогда приезд не раньше конца обеда)."""
    rng = random.Random(7)
    checked = 0
    for _ in range(400):
        orders = []
        for i in range(5):
            a = rng.choice([0, 60, 120, 150, 170, 200, 240, 300])
            orders.append(order(str(i), rng.choice(['Ремонт', 'Подключение']), (a, a + rng.choice([30, 120, 240])),
                                x=rng.uniform(0, 60)))
        p = line_problem(orders, [brigade('Б1')])
        route = sorted(range(5), key=lambda k: orders[k].earliest)
        s = p.schedule(0, route)
        if not s.feasible or s.lunch is None or s.lunch[1] >= len(s.stops):
            continue
        ls, g = s.lunch
        st = s.stops[g]
        assert ls >= st.arrive - 1e-6 or st.arrive >= ls + 45 - 1e-6, (route, ls, st)
        checked += 1
    assert checked > 20


def test_gap_that_delays_least_is_chosen():
    """Работы в одной точке без перерыва; обед в любом промежутке сдвигает E на 355. Выбирается промежуток перед E —
    тогда B, C и D начинаются без сдвига (не первый подходящий промежуток перед B)."""
    orders = [order('A', 'Подключение', (110, 110), x=10), order('B', window=(180, 400), x=10),
              order('C', window=(210, 400), x=10), order('D', 'Подключение', (240, 400), x=10),
              order('E', window=(330, 400), x=10)]
    p = line_problem(orders, [brigade('Б1')])
    s = p.schedule(0, list(range(5)))
    assert s.feasible and s.lunch == (310, 4)
    assert starts(s) == [110, 180, 210, 240, 355]


def test_no_place_for_lunch_is_infeasible():
    orders = [order(i, 'Подключение', (w, w), x=10) for i, w in enumerate((100, 170, 240, 310))]
    p = line_problem(orders, [brigade('Б1')])
    s = p.schedule(0, [0, 1, 2, 3])
    assert not s.feasible and s.lunch is None and s.on_time is False
    assert any('нет места для обеда' in e for e in check_plan(p, [[0, 1, 2, 3]]).errors)
    off = brigade('Без обеда')
    off.lunch = False
    p = line_problem(orders, [off])
    assert p.lunch_window(0) is None and p.schedule(0, [0, 1, 2, 3]).feasible and check_plan(p, [[0, 1, 2, 3]]).ok


def test_short_or_late_shift_has_no_lunch():
    short = brigade('5 ч 40 мин')
    short.shift = (0, 340)
    late = brigade('С 15:30')
    late.shift = (330, 720)
    p = line_problem([order('A', window=(240, 400), x=10)], [short, late, brigade('Полная')])
    assert p.lunch_window(0) is None and p.lunch_window(1) is None and p.lunch_window(2) == (180, 315, 45)
    assert p.schedule(0, [0]).lunch is None and p.schedule(2, [0]).lunch == (180, 0)


def test_late_shift_lunch_not_before_shift_start():
    late = brigade('С 13:00')
    late.shift = (180, 720)
    orders = [order('A', 'Подключение', (190, 190), x=10), order('B', 'Подключение', (260, 260), x=10),
              order('C', window=(330, 400), x=10)]
    p = line_problem(orders, [late])
    s = p.schedule(0, [0, 1, 2])
    assert not s.feasible                          # 13:10–15:30 без перерыва, обед начать до 15:15 негде
    s = p.schedule(0, [1, 2])
    assert s.feasible and s.lunch == (180, 0)      # свободна с 13:00 — обед перед выездом 13:00–13:45, B в 14:20


def test_replan_keeps_taken_lunch_and_skips_missed_one():
    orders = [order('A', window=(0, 60), x=10), order('B', window=(240, 400), x=20), order('C', window=(0, 600), x=30)]
    p = line_problem(orders, [brigade('Б1')])
    s = p.schedule(0, [0, 1])
    assert s.lunch == (180, 1)
    done = s.stops[:2]
    # обед был в выполненной части: второй раз не нужен, C сразу после B
    again = p.schedule(0, [0, 1, 2], release=260, keep=2, done=done, lunch=s.lunch)
    assert again.feasible and again.lunch == (180, 1) and again.stops[2].start == 280
    # обед начался до события и ещё идёт: к следующей заявке не раньше его конца
    p2 = line_problem([order('A', window=(160, 160), x=10), order('C', window=(0, 600), x=30)], [brigade('Б1')])
    first = p2.schedule(0, [0])
    assert first.lunch == (190, 1)
    moved = p2.schedule(0, [0, 1], release=200, keep=1, done=first.stops, lunch=first.lunch)
    assert moved.feasible and moved.stops[1].start == 235 + 20
    assert check_plan(p2, [[0, 1]], release=200, keep={0: 1}, done={0: (1, 190)}, lunches={0: first.lunch}).ok
    # событие после 15:15, а обеда не было (бригада ждала заявок): обед не нужен
    late = p2.schedule(0, [0, 1], release=330, keep=1, done=first.stops)
    assert late.feasible and late.lunch is None and late.stops[1].start == 350
    assert check_plan(p2, [[0, 1]], release=330, keep={0: 1}, done={0: (1, 190)}).ok


def test_route_dict_and_lunches_round_trip():
    p = line_problem([order('A', window=(0, 60), x=10), order('B', window=(240, 400), x=20)], [brigade('Б1'), brigade('Б2')])
    s = p.schedule(0, [0, 1])
    rt = docs.route_dict(p, 0, s)
    assert rt['lunch'] == [180, 225] and rt['lunch_after'] == 1
    assert docs.lunches(dict(plan=dict(routes=[rt]))) == {0: (180, 1)}
    off = brigade('Б3')
    off.lunch = False
    q = line_problem([order('A', window=(0, 60), x=10)], [off])
    assert docs.route_dict(q, 0, q.schedule(0, [0]))['lunch'] is None


def test_schedule_agrees_with_independent_check():
    """Случайные маршруты: Problem.schedule и check_plan одинаково решают, допустим ли маршрут с обедом."""
    rng = random.Random(7)
    agree = feasible = 0
    for trial in range(300):
        orders = []
        for i in range(rng.randint(2, 7)):
            a = rng.choice([0, 60, 120, 150, 180, 200, 240, 300, 360])
            orders.append(order(i, rng.choice(['Ремонт', 'Подключение', 'Дозаказ', 'Авария']), (a, a + rng.choice([0, 10, 30, 120])),
                                x=rng.uniform(0, 60)))
        b = brigade('Б', rng.choice(['car', 'bike']))
        if rng.random() < 0.3:
            b.shift = rng.choice([(0, 540), (180, 720), (60, 600)])
        p = line_problem(orders, [b])
        route = list(range(len(orders)))
        rng.shuffle(route)
        if trial % 2:
            route.sort(key=lambda k: orders[k].window)
        s = p.schedule(0, route)
        assert s.feasible == check_plan(p, [route]).ok, (trial, route)
        feasible += s.feasible
        if s.feasible and s.lunch:                 # обед — в окне и между работами, ни с чем не пересекается
            ls, after = s.lunch
            assert 180 - 1e-9 <= ls <= 315 + 1e-6
            assert after == 0 or s.stops[after - 1].end <= ls + 1e-9
            assert after == len(s.stops) or ls + 45 <= s.stops[after].start + 1e-9
        agree += 1
    assert feasible > 25


@pytest.mark.parametrize('release', [None, 200])
def test_done_part_schedule_matches_check(release):
    orders = [order('A', window=(0, 60), x=10), order('B', window=(0, 400), x=20), order('C', window=(200, 400), x=25)]
    p = line_problem(orders, [brigade('Б1')])
    base = p.schedule(0, [0, 1, 2])
    done = [Stop(0, 10, 10, 40, 10)]
    s = p.schedule(0, [0, 1, 2], release=release, keep=1, done=done, lunch=base.lunch)
    assert s.feasible == check_plan(p, [[0, 1, 2]], release=release, keep={0: 1}, done={0: (1, 40)},
                                    lunches={0: base.lunch}).ok
