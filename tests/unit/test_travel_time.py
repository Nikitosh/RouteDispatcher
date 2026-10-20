"""Время в пути: общественный транспорт по расписаниям в MatrixBuilder (кэш, пешком быстрее, «не доехать», часы),
оценка для новых точек на сервере (NearbyEstimate), пробки по часам и районам в Problem."""
import pytest

from dispatch.config import TRAFFIC_HOURS, TravelSettings, traffic_ratio, window_hours, zone_of
from dispatch.geo.matrices import MatrixBuilder, TravelMatrices, public_transport
from dispatch.geo.nearby import NearbyEstimate
from dispatch.geo.store import JsonStore
from dispatch.model.problem import Problem
from dispatch.tools import transit
from tests.helpers import brigade, order

KREMLIN, INSIDE_TTK, INSIDE_MKAD, DOMODEDOVO = (55.752, 37.6175), (55.7805, 37.617), (55.65, 37.60), (55.44, 37.77)


class FakeOsrm:
    """Машина 2 мин/км, пешком 12 мин/км, велосипед 4 мин/км; км = |Δlat| × 100."""

    def table(self, profile, src, dst):
        pace = {'car': 2, 'foot': 12, 'bike': 4}[profile]
        return [[(abs(a[0] - b[0]) * 100 * pace, abs(a[0] - b[0]) * 100) for b in dst] for a in src]


@pytest.fixture
def timetable(monkeypatch):
    """Подмена расчёта по расписанию: пара (0, 1) — 25 мин (по часам 20 и 30), (1, 0) — не доехать, остальное — 7 мин."""
    calls = []

    def times(points, walk=None, hourly=False, **kw):
        calls.append(walk)
        out = {}
        for i in range(len(points)):
            for j in range(len(points)):
                if i != j and (i, j) != (1, 0):
                    out[i, j] = (25.0, [20.0, 30.0]) if (i, j) == (0, 1) else (7.0, [7.0, None])
        return out
    monkeypatch.setattr(transit, 'available', lambda folder=None: True)
    monkeypatch.setattr(transit, 'times', times)
    return calls


PTS = [(55.0, 37.0), (55.1, 37.0), (55.2, 37.0)]       # 10 км и 20 км по широте


def test_timetable_pairs_are_cached_with_hours(tmp_path, timetable):
    mb = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path), walk_osrm=False)
    m = mb.build(PTS, 1, TravelSettings(overhead={'pt': 3.0}))
    assert m.time['pt'][0][1] == 25 + 3 and m.dist['pt'][0][1] == pytest.approx(10)   # по расписанию; км — машинные
    assert m.pt_hours[0][1] == [23.0, 33.0]                                            # по часам, с подходом к двери
    assert m.time['pt'][1][0] == pytest.approx(120)                                    # по расписанию не доехать — только пешком
    assert m.time['pt'][1][2] == 7 + 3 and m.pt_hours[1][2] == [10.0, 10.0]           # нет данных за час — среднее
    assert m.pt_hours[1][0] is None and timetable == [None]
    assert JsonStore(str(tmp_path / 'matrix_pt.json')).get('55.10000,37.00000|55.00000,37.00000') == [-1.0, -1.0]
    MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path)).build(PTS, 1)
    assert len(timetable) == 1                                                         # второй раз — из кэша


def test_pt_factor_applies_to_hours_too(tmp_path, timetable):
    m = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path), walk_osrm=False).build(
        PTS, 1, TravelSettings(traffic={'pt': 1.2}, overhead={'pt': 3.0}))
    assert m.time['pt'][0][1] == pytest.approx(1.2 * 25 + 3) and m.pt_hours[0][1] == pytest.approx([1.2 * 20 + 3, 1.2 * 30 + 3])


def test_walking_beats_timetable_when_faster(tmp_path, timetable):
    near = [(55.0, 37.0), (55.001, 37.0)]                                              # 100 м: пешком 1,2 мин
    m = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path), walk_osrm=False).build(near, 2, TravelSettings(overhead={}))
    assert m.time['pt'][0][1] == pytest.approx(1.2) and m.dist['pt'][0][1] == pytest.approx(0.1)
    assert m.pt_hours[0][1] == [pytest.approx(1.2), pytest.approx(1.2)]


def test_street_walk_to_stops_is_used(tmp_path, timetable):
    MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path)).build(PTS, 1)
    assert isinstance(timetable[0], transit.OsrmWalk)


def test_without_network_formula(tmp_path, monkeypatch):
    monkeypatch.setattr(transit, 'times', lambda *a, **k: pytest.fail('сети нет — расписание не считаем'))
    m = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path)).build(PTS, 1, TravelSettings(overhead={}))
    assert m.time['pt'][0][2] == pytest.approx(public_transport(20, 240, 20)[0]) and m.pt_hours[0][2] is None
    off = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path / 'b'), timetable=False)
    assert off.build(PTS, 1).time['pt'][0][1] == pytest.approx(public_transport(10, 120, 10)[0] + 3)


def test_without_numpy_formula(tmp_path, monkeypatch):
    """Без numpy (Docker без него, сервер на Vercel без tools/) общественный транспорт — по формуле, без ошибки."""
    import sys

    import dispatch.tools
    monkeypatch.setattr(transit, 'available', lambda folder=None: True)           # был бы модуль — пошли бы в расписания
    monkeypatch.setattr(transit, 'times', lambda *a, **k: pytest.fail('модуля расписаний нет'))
    monkeypatch.delattr(dispatch.tools, 'transit')
    monkeypatch.setitem(sys.modules, 'dispatch.tools.transit', None)               # import даёт ImportError
    m = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path)).build(PTS, 1, TravelSettings(overhead={}))
    assert m.time['pt'][0][2] == pytest.approx(public_transport(20, 240, 20)[0])


def test_server_estimates_new_points_through_neighbours(tmp_path):
    """Сервер без сети и OSRM: новая точка в 300 м от известной — время известной пары плюс подход пешком."""
    pt = JsonStore(str(tmp_path / 'pt.json'))
    pt.put('55.00000,37.00000|55.10000,37.00000', [25.0, 10.0, [20.0, 30.0]])
    pt.put('55.10000,37.00000|55.00000,37.00000', [-1.0, -1.0])
    stores = {p: JsonStore(str(tmp_path / f'{p}.json')) for p in ('car', 'foot', 'bike')}
    near = NearbyEstimate(dict(stores, pt=pt))
    new = (55.0027, 37.0)                                                              # ≈ 300 м севернее
    t, km = near.table('pt', [new], [(55.1, 37.0)])[0][0]
    assert t == pytest.approx(25 + 0.3 * 1.3 / 4.5 * 60, abs=0.1) and km == pytest.approx(10 + 0.3 * 1.3, abs=0.01)
    assert near.table('pt', [(55.1, 37.0)], [new])[0][0] == (-1.0, -1.0)              # известная пара — «не доехать»
    mb = MatrixBuilder(client=near, cache_dir=str(tmp_path), timetable=False)
    mb._stores['pt'] = pt
    for p in stores:
        mb._stores[p] = stores[p]
        stores[p].put('55.00270,37.00000|55.10000,37.00000', [100.0, 10.0])
        stores[p].put('55.10000,37.00000|55.00270,37.00000', [100.0, 10.0])
    T, D, H = mb.transit([new, (55.1, 37.0)], [[0, 10], [10, 0]], [[0, 100], [100, 0]], [[0, 10], [10, 0]])
    assert T[0][1] == pytest.approx(25 + 0.3 * 1.3 / 4.5 * 60, abs=0.1) and H[0][1] is None
    assert T[1][0] == 100                                                              # «не доехать» — только пешком
    empty = NearbyEstimate(dict(stores, pt=JsonStore(str(tmp_path / 'empty.json'))))
    empty.points = lambda profile: [(55.0, 37.0), (55.1, 37.0)]
    assert empty.table('pt', [new], [(55.1, 37.0)])[0][0] == (-2.0, -2.0)             # пары нет — неизвестно
    pt.put('55.00270,37.00000|55.10000,37.00000', [-2.0, -2.0])
    T2, _, _ = mb.transit([new, (55.1, 37.0)], [[0, 10], [10, 0]], [[0, 100], [100, 0]], [[0, 10], [10, 0]])
    assert T2[0][1] == public_transport(10, 100, 10)[0]                                 # неизвестно — по формуле


# ---- пробки по часам и районам
def test_window_hours_and_city_ratio():
    assert window_hours(0, 59) == [0, 0, 0, 0] and window_hours(-30, 10) == [0] and window_hours(700, 900) == [11, 11]
    assert window_hours(480, 600) == [8] * 4 + [9] * 4 + [10]
    day = [traffic_ratio(h * 60, h * 60 + 59) for h in range(12)]
    assert sum(day) / 12 == pytest.approx(1.0)                                         # в среднем за день — прежний 1,3
    assert max(day) == day[8] and min(day) == day[11]                                  # пик в 18 ч, свободнее всего в 21 ч


def test_zones():
    assert zone_of(*KREMLIN)[0] == 'внутри Садового' and zone_of(*INSIDE_TTK)[0] == 'Садовое — ТТК'
    assert zone_of(*INSIDE_MKAD)[0] == 'ТТК — МКАД' and zone_of(*DOMODEDOVO)[0] == 'за МКАД'
    center, outside = zone_of(*KREMLIN)[1], zone_of(*DOMODEDOVO)[1]
    assert traffic_ratio(180, 239, share=center) > traffic_ratio(180, 239) > traffic_ratio(180, 239, share=outside)   # 13 ч
    assert traffic_ratio(480, 539, share=outside) > traffic_ratio(480, 539)          # в 18 ч за МКАД — выше среднего
    ones = [1.0] * 12
    assert traffic_ratio(0, 719, share=ones) == pytest.approx(traffic_ratio(0, 719))
    h = 8
    assert traffic_ratio(h * 60, h * 60 + 59, share=center) == pytest.approx(
        (1 + (TRAFFIC_HOURS[h] - 1) * center[h]) / (sum(TRAFFIC_HOURS) / 12))


def problem(starts, orders, hourly=True, pt_hours=None, offset=None, overhead=None):
    pts = starts + [(o.lat, o.lon) for o in orders]
    n = len(pts)
    T = {m: [[0.0 if i == j else 30.0 for j in range(n)] for i in range(n)] for m in ('car', 'pt', 'bike', 'foot')}
    D = {m: [[0.0 if i == j else 5.0 for j in range(n)] for i in range(n)] for m in T}
    return Problem('тест', orders, [brigade('Б1')], starts,
                   TravelMatrices(T, D, hourly=hourly, pt_hours=pt_hours, offset=offset, overhead=overhead))


def placed(oid, window, where):
    o = order(oid, 'Ремонт', window)
    o.lat, o.lon = where
    return o


def test_problem_car_by_window_and_zones_of_both_ends():
    evening_center = placed(1, (480, 600), KREMLIN)
    morning_out = placed(2, (0, 120), DOMODEDOVO)
    p = problem([DOMODEDOVO], [evening_center, morning_out])
    mid = [(a + b) / 2 for a, b in zip(zone_of(*DOMODEDOVO)[1], zone_of(*KREMLIN)[1])]
    assert p.time['car'][0][1] == pytest.approx(30 * traffic_ratio(480, p.latest(0), share=mid))
    assert p.time['car'][0][2] == pytest.approx(30 * traffic_ratio(0, 120, share=zone_of(*DOMODEDOVO)[1]))
    assert p.time['car'][1][2] == pytest.approx(30 * traffic_ratio(0, 120, share=mid))
    assert p.time['car'][2][0] == 30 and p.time['foot'][0][1] == 30                 # к старту и пешком — без поправки
    flat = problem([DOMODEDOVO], [evening_center, morning_out], hourly=False)
    assert flat.time['car'][0][1] == 30


def test_traffic_does_not_scale_door_approach():
    """30 мин = 25 мин дороги + 5 мин подхода: пробки множат только дорогу."""
    o = placed(1, (480, 600), KREMLIN)
    p = problem([KREMLIN], [o], overhead={'car': 5.0})
    r = traffic_ratio(480, p.latest(0), share=zone_of(*KREMLIN)[1])
    assert r != 1 and p.time['car'][0][1] == pytest.approx(5 + 25 * r)


def test_builder_keeps_overhead_for_problem(tmp_path):
    m = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path), timetable=False).build(PTS, 1, TravelSettings({'car': 1.3}, {'car': 5.0}))
    assert m.overhead == {'car': 5.0}


def test_problem_pt_by_window_hours_and_offset_after():
    o = placed(1, (480, 600), INSIDE_MKAD)
    hours = [[None, [float(10 + h) for h in range(12)]], [None, None]]
    p = problem([INSIDE_MKAD], [o], pt_hours=hours, offset=[100])
    expect = sum(10 + h for h in window_hours(480, p.latest(0))) / len(window_hours(480, p.latest(0)))
    assert p.time['pt'][0][1] == pytest.approx(expect + 100)                          # сдвиг — после часов, не умножается
    assert p.time['car'][0][1] == pytest.approx(30 * traffic_ratio(480, p.latest(0), share=zone_of(*INSIDE_MKAD)[1]) + 100)
    assert p.matrices.time['car'][0][1] == 30                                          # исходные матрицы не тронуты
