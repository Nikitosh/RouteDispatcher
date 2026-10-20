"""Карта аварийного покрытия и дефицит «районы × окна» (planning/coverage.py) на задачах «на прямой»."""
from dispatch.config import TravelSettings
from dispatch.planning import coverage
from tests.helpers import brigade, line_problem, order


class LineOsrm:
    """table: минуты = 2 × |Δlat|·111 (≈ 30 км/ч по широте), км = |Δlat|·111."""

    def table(self, profile, src, dst):
        return [[(abs(a[0] - b[0]) * 111 * 2, abs(a[0] - b[0]) * 111) for b in dst] for a in src]


def test_coverage_minutes_from_free_time_and_ride(monkeypatch):
    monkeypatch.setattr(coverage, 'STEP', 1.0)
    orders = [order(1, 'Ремонт', (0, 600), x=0.01), order(2, 'Ремонт', (0, 600), x=0.02)]
    crew = brigade('Аварийная', 'car', (0, 2))                       # навык аварий
    p = line_problem(orders, [crew, brigade('Без аварий', 'car', (0,))])
    cov = coverage.coverage(p, [[0], [1]], LineOsrm(), TravelSettings(traffic={'car': 1.0}, overhead={}), log=lambda *_: None)
    assert cov and len(cov['slots']) == 24 and len(cov['minutes'][0]) == len(cov['grid'])
    s = p.schedule(0, [0]).stops[0]
    i = next(i for i, t in enumerate(cov['slots']) if s.start <= t < s.end)       # Аварийная работает у заявки 1
    t = cov['slots'][i]
    from dispatch.config import traffic_ratio
    p0 = min(range(len(cov['grid'])), key=lambda j: abs(cov['grid'][j][0] - 0.01))
    ride = abs(cov['grid'][p0][0] - 0.01) * 111 * 2 * traffic_ratio(t, t)
    assert cov['minutes'][i][p0] == round(s.end - t + ride) and cov['brigade'][i][p0] == 0


def test_coverage_none_without_accident_crew_or_osrm():
    p = line_problem([order(1, x=0.01)], [brigade('Б', 'car', (0,))])
    assert coverage.coverage(p, [[0]], LineOsrm(), log=lambda *_: None) is None

    class Dead:
        def table(self, *a):
            from dispatch.geo.osrm import OsrmError
            raise OsrmError('нет')
    p = line_problem([order(1, x=0.01)], [brigade('Б', 'car', (0, 2))])
    logs = []
    assert coverage.coverage(p, [[0]], Dead(), log=logs.append) is None and 'не построена' in logs[0]


def test_deficit_counts_free_slots_per_district_and_window():
    """Одна бригада занята утром у заявки 1; вечером у неё свободно — подключения помещаются только туда."""
    orders = [order(1, 'Подключение', (0, 120), x=1), order(2, 'Ремонт', (0, 720), x=2)]
    for o in orders:
        o.district = 'Центр'
    p = line_problem(orders, [brigade('Б1'), brigade('Резерв')])
    df = coverage.deficit(p, [[0, 1], []])
    assert df['districts'] == ['Центр'] and df['windows'][0] == [0, 120] and df['demand'] == [[2, 0, 0, 0, 0, 0]]   # окно на весь день — по началу, тоже 10–12
    # подключение — 70 мин: в окно влезает два начала; утром Б1 занята заявкой 1, вечером работу надо закончить до 22:00
    assert df['service'] == 70 and df['free'] == [[1, 2, 2, 2, 2, 1]]
    assert df['free_all'] == [[3, 4, 4, 4, 4, 2]]                    # резерв добавляет по два, вечером — одно
