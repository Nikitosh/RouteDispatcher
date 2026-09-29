import pytest

from dispatch.config import TravelSettings
from dispatch.geo import polyline
from dispatch.geo.geometry import RouteGeometry
from dispatch.geo.matrices import UNREACHABLE, MatrixBuilder, public_transport
from dispatch.geo.osrm import OsrmError, profile_of


def test_polyline_known_example():
    # пример из документации Google Maps
    pts = [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]
    assert polyline.encode(pts) == '_p~iF~ps|U_ulLnnqC_mqNvxq`@'
    assert polyline.decode('_p~iF~ps|U_ulLnnqC_mqNvxq`@') == pts


def test_public_transport_model():
    assert public_transport(car_km=1.0, foot_min=15, foot_km=1.2) == (15, 1.2)       # близко — пешком
    t, km = public_transport(car_km=10.0, foot_min=150, foot_km=9.0)
    assert (round(t), km) == (42, 10.0)                                                 # 12 мин + 10 км при 20 км/ч
    t, _ = public_transport(car_km=45.0, foot_min=600, foot_km=40)
    assert round(t) == round(12 + 75 + 20 / 45 * 60)                                   # после 25 км — 45 км/ч


class FakeOsrm:
    """table: время = 2 мин на км, расстояние = |Δlat| × 100 км; точка с lat 99 недостижима."""

    def __init__(self):
        self.tables = 0

    def table(self, profile, src, dst):
        self.tables += 1
        return [[None if 99 in (a[0], b[0]) else (abs(a[0] - b[0]) * 200, abs(a[0] - b[0]) * 100) for b in dst] for a in src]

    def route(self, profile, a, b):
        if profile == 'foot':
            raise OsrmError('нет сервера')
        return [list(a), [(a[0] + b[0]) / 2, a[1]], list(b)]


def test_matrix_cache_and_settings(tmp_path):
    osrm = FakeOsrm()
    builder = MatrixBuilder(client=osrm, cache_dir=str(tmp_path))
    pts = [(55.0, 37.0), (55.1, 37.0), (55.2, 37.0)]
    m = builder.build(pts, n_starts=1, settings=TravelSettings(traffic={'car': 1.5}, overhead={'car': 5}))
    assert m.dist['car'][0][2] == pytest.approx(20)
    assert m.time['car'][0][2] == pytest.approx(40 * 1.5 + 5)       # пробки и подход к заявке
    assert m.time['car'][1][0] == pytest.approx(20 * 1.5)            # к стартовой точке подхода нет
    assert m.time['car'][1][1] == 0
    calls = osrm.tables
    MatrixBuilder(client=osrm, cache_dir=str(tmp_path)).build(pts, 1)
    assert osrm.tables == calls                                       # второй раз — только кэш


def test_unreachable_pair(tmp_path):
    m = MatrixBuilder(client=FakeOsrm(), cache_dir=str(tmp_path)).profile('car', [(55.0, 37.0), (99.0, 37.0)])
    assert m[0][0][1] == UNREACHABLE


def test_geometry_cache_and_offline(tmp_path):
    g = RouteGeometry(client=FakeOsrm(), path=str(tmp_path / 'g.json'))
    line = g.route('pt', [(55.0, 37.0), (55.2, 37.0), (55.2, 37.0)])
    assert len(line) == 2 and line[0][1] == [55.1, 37.0] and line[1] == [[55.2, 37.0], [55.2, 37.0]]
    g.save()
    offline = RouteGeometry(client=FakeOsrm(), path=str(tmp_path / 'g.json'), online=False)
    assert offline.leg('car', (55.0, 37.0), (55.2, 37.0)) == line[0]       # из кэша, pt = машинные дороги
    assert offline.leg('bike', (55.0, 37.0), (55.2, 37.0)) is None
    assert g.route('foot', [(55.0, 37.0), (55.2, 37.0)]) is None
    assert profile_of('pt') == 'car'
