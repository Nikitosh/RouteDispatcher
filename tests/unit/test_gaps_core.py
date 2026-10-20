"""Режим «лучшие монтажники» (planning/best.py), кривая и цена ограничений (planning/fleet.py), отзывы в CSV
(model/rating.py), --best без --reviews в CLI. Решатель — подмена на жадных вставках, без C++."""
import pytest

from dispatch.cli import main
from dispatch.model import rating
from dispatch.model.rating import KINDS, EngineerRating, Rating, Review
from dispatch.planning import best, fleet
from dispatch.planning.planner import Planner
from dispatch.solve.runner import SolverResult
from tests.helpers import LineMatrices, brigade, line_problem, order


class Fleet:
    """Подмена SolverRunner: жадные вставки; maxv — только первые maxv бригад; lb — «нижняя граница» в solve."""

    def __init__(self, lb=None, short=None):
        self.lb, self.short, self.calls = lb, short, []

    def plan_only(self, p, seconds=0, seed=0, workdir=None, maxv=None, wveh=None, **kw):
        self.calls.append((len(p.brigades), maxv))
        n = len(p.brigades) if maxv is None else min(maxv, len(p.brigades))
        routes = [[] for _ in p.brigades]
        for k in range(len(p.orders)):
            found = None
            for v in range(n):
                for pos in range(len(routes[v]) + 1):
                    c = routes[v][:pos] + [k] + routes[v][pos:]
                    s = p.schedule(v, c)
                    if s.feasible and (found is None or s.km < found[0]):
                        found = (s.km, v, c)
            if found:
                routes[found[1]] = found[2]
        return routes

    def solve(self, p, seconds=0, seed=0, workdir=None, **kw):
        routes = self.plan_only(p)
        if self.short == len(p.brigades):               # «длинный прогон» на стольких бригадах теряет заявку
            routes = [r[:-1] if r else r for r in routes]
        return SolverResult(routes, self.lb), SolverResult(routes), None


class NoGeometry:
    def route(self, mode, pts):
        return None

    def save(self):
        pass


def eng(value, skills=(0, 1, 2)):
    return EngineerRating(by_skill={s: Rating(value, 20, False) for s in skills}, on_time=4.8, on_time_reviews=20)


RATINGS = {'Слабый': eng(3.0), 'Сильный': eng(5.0), 'Средний': eng(4.5)}


def day():
    """Две заявки в разные стороны с окном 15 мин: одной бригаде не успеть. Слабый живёт у заявки на x=10."""
    orders = [order('П', 'Ремонт', (0, 15), x=10), order('Л', 'Ремонт', (0, 15), x=-10)]
    bs = [brigade('Слабый', start=1, x=9), brigade('Сильный'), brigade('Средний')]
    for b in bs:
        b.lunch = False
    return line_problem(orders, bs, starts=((0.0, 0.0), (9.0, 0.0)))


def planner(solver, logs=None):
    return Planner(geocoder=object(), matrices=LineMatrices(), solver=solver, geometry=NoGeometry(),
                   log=(logs.append if logs is not None else lambda *_: None))


META = dict(name='т', office='офис', traffic={}, overhead={})


def test_best_mode_takes_top_rated_engineers(tmp_path):
    doc = best.BestMode(planner(Fleet(lb=2)), RATINGS, log=lambda *_: None).run(day(), META, str(tmp_path), 0.1)
    assert [b['name'] for b in doc['brigades']] == ['Сильный', 'Средний']          # слабого не вывели
    q = doc['quality']
    assert (q['k'], q['pool'], q['proven']) == (2, 3, True)
    assert [p['k'] for p in q['curve']] == [2, 3]
    assert q['curve'][0] == dict(k=2, brigades=2, km=20.0, served=2, rating=4.75)
    # весь парк («экономия»): ближнюю заявку берёт слабый — км меньше, рейтинг исполнителя ниже
    assert q['curve'][1]['km'] < q['curve'][0]['km'] and q['curve'][1]['rating'] == 4.0


def test_best_mode_takes_k_plus_one_when_long_run_falls_short(tmp_path):
    logs = []
    doc = best.BestMode(planner(Fleet(lb=2, short=2)), RATINGS, log=logs.append).run(day(), META, str(tmp_path), 0.1)
    assert doc['quality']['k'] == 3 and not doc['quality']['proven'] and len(doc['brigades']) == 3
    assert any('беру 3' in x for x in logs)


def test_best_mode_curve_has_no_duplicate_points(tmp_path):
    doc = best.BestMode(planner(Fleet(lb=2, short=2)), RATINGS, log=lambda *_: None).run(day(), META, str(tmp_path), 0.1)
    ks = [p['k'] for p in doc['quality']['curve']]
    assert len(ks) == len(set(ks))


def test_best_mode_needs_whole_fleet_to_close_day(tmp_path):
    p = day()
    p = line_problem(p.orders + [order('Далеко', 'Ремонт', (0, 5), x=50)], p.brigades, starts=p.starts)  # не успеть никому
    with pytest.raises(ValueError, match='весь парк'):
        best.BestMode(planner(Fleet()), RATINGS, log=lambda *_: None).run(p, META, str(tmp_path), 0.1)


def test_point_and_order_rating():
    p = day()
    assert best.point(p, [[0], [1], []], RATINGS, 3) == dict(k=3, brigades=2, km=11.0, served=2, rating=4.0)
    assert best.order_rating(p, [[], [], []], RATINGS) is None
    assert best.ranked(p, RATINGS) == [1, 2, 0]


def test_attach_adds_ratings_to_document(tmp_path):
    doc = best.BestMode(planner(Fleet(lb=2)), RATINGS, log=lambda *_: None).run(day(), META, str(tmp_path), 0.1)
    partial = {'Сильный': eng(5.0, skills=(0, 1))}                 # у «Среднего» отзывов нет
    best.attach(doc, partial)
    assert list(doc['ratings']) == ['Сильный']
    r = doc['ratings']['Сильный']
    assert r['on_time'] == 4.8 and set(r['by_kind']) == {KINDS[0], KINDS[1]}
    assert r['by_kind'][KINDS[0]] == dict(value=5.0, reviews=20, few=False)
    rated = [o['plan'].get('rating') for o in doc['orders']]
    assert sorted(rated, key=str) == [5.0, None] and doc['plan']['metrics']['rating'] == 5.0
    best.attach(doc, {})
    assert doc['ratings'] == {} and doc['plan']['metrics']['rating'] is None


def test_cli_best_needs_reviews(monkeypatch, capsys):
    monkeypatch.setattr(Planner, 'load', lambda self, *a, **k: (day(), dict(META)))
    assert main(['plan', 'заявки.csv', '-b', 'бригады.xlsx', '--best']) == 1
    assert 'для --best нужен --reviews' in capsys.readouterr().err


def test_fleet_curve_points_per_fleet_size():
    solver = Fleet()
    out = fleet.curve(solver, day(), used=2, seconds=0.1, span=1)
    assert out == [dict(brigades=1, limit=1, served=1, km=1.0), dict(brigades=2, limit=2, served=2, km=11.0),
                   dict(brigades=2, limit=3, served=2, km=11.0)]
    assert [m for _, m in solver.calls] == [1, 2, 3]
    assert [x['limit'] for x in fleet.curve(solver, day(), used=1, span=5)] == [1, 2, 3]   # в пределах парка


def test_price_variants_only_for_rules_that_matter():
    orders = [order(1, 'Подключение', (0, 600), x=5, need={'car'}), order(2, 'Ремонт', (0, 600), x=-5)]
    bs = [brigade('Б1', skills=(0,)), brigade('Б2', mode='foot')]
    bs[0].shift = (0, 540)
    p = line_problem(orders, bs)
    names = [n for n, _, _ in fleet._variants(p)]
    assert names == ['все умеют всё', 'все на машинах', 'без требований к транспорту', 'все смены 10:00–22:00',
                     'без обеда', 'без учёта оборудования']
    v = dict((n, (b, o)) for n, b, o in fleet._variants(p))
    assert all(b.skills == [0, 1, 2] for b in v['все умеют всё'][0]) and p.brigades[0].skills == [0]   # исходные не тронуты
    assert v['без требований к транспорту'][1][0].need is None and p.orders[0].need == {'car'}
    assert all(b.mode == 'car' for b in v['все на машинах'][0])
    out = fleet.price(Fleet(lb=1), p, seconds=0.1)
    assert [x['what'] for x in out] == names and all(x['bound'] == 1 for x in out)
    # подключению нужна машина, а умеет его только пешая Б2: закрывается, лишь когда снято одно из этих правил
    assert [x['served'] for x in out] == [2, 2, 2, 1, 1, 1]

    plain = [brigade('Б1')]
    plain[0].lunch = False
    assert fleet._variants(line_problem([order(1, 'Ремонт', x=1)], plain)) == []


def test_reviews_csv_round_trip(tmp_path):
    rows = [(Review(age=3, brigade='Иванов', skill=0, work=5, on_time=4), '101'),
            (Review(age=10, brigade='Петров', skill=2, work=None, on_time=2, plan_late=True), '102'),
            (Review(age=0, brigade='Иванов', skill=1, work=3, on_time=None), '103')]
    path = tmp_path / 'r.csv'
    rating.write(str(path), rows)
    assert path.read_text(encoding='utf-8').splitlines()[0] == ';'.join(rating.HEADER)
    assert rating.read(str(path)) == [r for r, _ in rows]
