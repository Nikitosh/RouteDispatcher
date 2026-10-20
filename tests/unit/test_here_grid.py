"""Сетка пробок HERE (dispatch/geo/here_grid.py) на крошечном искусственном файле: без файла — None, доля поездки —
среднее клеток на прямой с весами, вне сетки — районы config.zone_of, обрезка, коэффициенты как config.traffic_ratio."""
import numpy as np
import pytest

from dispatch.config import OUTSIDE, TRAFFIC_HOURS, traffic_ratio, zone_of
from dispatch.geo import here_grid

LAT0, LON0, D = 55.70, 37.60, 0.01          # 2 × 2 клетки по 0,01°
FAR = 1e9                                    # шаг больше любой прямой: только два конца


def cell(i, j):
    return LAT0 + (i + 0.5) * D, LON0 + (j + 0.5) * D


@pytest.fixture
def grid_file(tmp_path):
    share = np.empty((12, 2, 2))
    share[:, 0, 0] = 1.0
    share[:, 0, 1] = 2.0
    share[:, 1, 0] = np.linspace(0.5, 1.6, 12)          # по часам
    share[:, 1, 1] = 5.0                                  # выше потолка
    weight = np.array([[1.0, 3.0], [1.0, 1.0]])
    path = tmp_path / 'grid.npz'
    np.savez_compressed(path, share=share.astype(np.float16), weight=weight.astype(np.float16), lat0=LAT0, lon0=LON0,
                        dlat=D, dlon=D, meta='{}')
    return str(path)


def test_missing_or_disabled_grid_gives_none(tmp_path, monkeypatch, grid_file):
    missing = str(tmp_path / 'nope.npz')
    assert here_grid.load(missing) is None and not here_grid.available(missing)
    assert here_grid.arc_shares([cell(0, 0)], path=missing) is None
    assert here_grid.traffic_ratios([cell(0, 0)], [cell(0, 1)], [(0, 60)], path=missing) is None
    monkeypatch.setenv('DISPATCH_HERE_GRID', 'off')
    assert here_grid.load() is None
    monkeypatch.setenv('DISPATCH_HERE_GRID', grid_file)
    assert here_grid.available()


def test_point_in_one_cell_takes_its_share(grid_file):
    S = here_grid.arc_shares([cell(0, 0), cell(1, 0)], path=grid_file)
    assert S.shape == (2, 2, 12)
    assert S[0, 0] == pytest.approx([1.0] * 12)
    assert S[1, 1] == pytest.approx(np.linspace(0.5, 1.6, 12), abs=2e-3)


def test_arc_is_weighted_mean_of_cells_and_symmetric(grid_file):
    a, b = cell(0, 0), cell(0, 1)
    S = here_grid.arc_shares([a, b], path=grid_file, step_m=FAR)
    assert S[0, 1] == pytest.approx([(1.0 * 1 + 2.0 * 3) / 4] * 12)         # концы: вес 1 и 3
    assert S[1, 0] == pytest.approx(S[0, 1])
    fine = here_grid.arc_shares([a], [b], path=grid_file, step_m=50)[0, 0]   # точки по всей прямой: те же две клетки
    assert 1.0 < fine[0] < 2.0


def test_clipped_to_sane_range(grid_file):
    S = here_grid.arc_shares([cell(1, 1)], path=grid_file)
    assert S[0, 0] == pytest.approx([here_grid.CLIP[1]] * 12)


def test_outside_grid_falls_back_to_zones(grid_file):
    far, kremlin = (55.0, 37.0), (55.752, 37.6175)
    S = here_grid.arc_shares([far], [kremlin], path=grid_file, step_m=FAR)[0, 0]
    expect = [np.clip((x + y) / 2, *here_grid.CLIP) for x, y in zip(zone_of(*far)[1], zone_of(*kremlin)[1])]
    assert S == pytest.approx(expect, abs=1e-6)
    assert here_grid.zone_shares([far[0]], [far[1]])[0] == pytest.approx(OUTSIDE[1])


def test_traffic_ratios_match_config_formula(grid_file):
    pts = [cell(0, 0), cell(1, 0), cell(0, 1)]
    spans = [(0, 60), (420, 600), (700, 719)]
    R = here_grid.traffic_ratios(pts, pts, spans, path=grid_file)
    S = here_grid.arc_shares(pts, pts, path=grid_file)
    for i in range(3):
        for j in range(3):
            assert R[i, j] == pytest.approx(traffic_ratio(*spans[j], share=list(map(float, S[i, j]))), rel=1e-6)
    flat = np.ones((3, 3, 12))
    W = here_grid.hour_weights(spans)
    T = np.asarray(TRAFFIC_HOURS)
    assert ((1 + np.einsum('jh,ijh->ij', W * (T - 1), flat)) / T.mean())[0, 0] == pytest.approx(traffic_ratio(0, 60))


def test_path_shares_follow_the_line(grid_file):
    a, b, c = (LAT0 + 0.3 * D, LON0 + 0.5 * D), (LAT0 + 1.6 * D, LON0 + 0.5 * D), cell(1, 1)   # точки не на границах
    grid = here_grid.load(grid_file)
    S = grid.path_shares([[a, b], [a], [a, c, b]])
    assert S[1] == pytest.approx([1.0] * 12)                                    # одна точка
    assert S[0] == pytest.approx(here_grid.arc_shares([a], [b], path=grid_file)[0, 0], abs=1e-6)   # прямая = прямая
    assert S[2][0] > S[0][0]                                                    # крюк через клетку с долей 5


def test_empty_inputs(grid_file):
    assert here_grid.arc_shares([], [cell(0, 0)], path=grid_file).shape == (0, 1, 12)


def test_problem_uses_grid_and_day_profile(grid_file, monkeypatch):
    """Problem: машина к заявке — по сетке, если она есть (иначе районы), и по профилю дня недели плана."""
    from dispatch.config import TRAFFIC_DAYS
    from dispatch.geo.matrices import TravelMatrices
    from dispatch.model.problem import Problem
    from tests.helpers import brigade, order
    o = order(1, 'Ремонт', (480, 539))
    o.lat, o.lon = cell(0, 1)
    start = cell(0, 0)
    mats = lambda day=None: TravelMatrices({m: [[0.0, 30.0], [30.0, 0.0]] for m in ('car', 'pt', 'bike', 'foot')},
                                           {m: [[0.0, 5.0], [5.0, 0.0]] for m in ('car', 'pt', 'bike', 'foot')},
                                           hourly=True, day=day)
    monkeypatch.setenv('DISPATCH_HERE_GRID', grid_file)
    p = Problem('т', [o], [brigade('Б')], [start], mats())
    expect = here_grid.traffic_ratios([start, (o.lat, o.lon)], [(o.lat, o.lon)], [(480, p.latest(0))], path=grid_file)[0, 0]
    assert p.traffic_grid and p.time['car'][0][1] == pytest.approx(30 * expect)
    sunday = Problem('т', [o], [brigade('Б')], [start], mats(day=6))
    assert sunday.time['car'][0][1] < p.time['car'][0][1]                     # воскресенье свободнее будней
    monkeypatch.setenv('DISPATCH_HERE_GRID', 'off')
    zones = Problem('т', [o], [brigade('Б')], [start], mats(day=6))
    share = [(x + y) / 2 for x, y in zip(zone_of(*start)[1], zone_of(o.lat, o.lon)[1])]
    assert not zones.traffic_grid
    assert zones.time['car'][0][1] == pytest.approx(30 * traffic_ratio(480, zones.latest(0), share=share, profile=TRAFFIC_DAYS[6]))
