"""solver/bin/transit (алгоритм Connection Scan) на сетях из нескольких остановок: ожидание, пересадки, пешие переходы,
среднее по часам, «не доехать»."""
import json
import os

import numpy as np
import pytest

from dispatch.config import SOLVER_BIN
from dispatch.tools import transit

pytestmark = pytest.mark.e2e

if not os.path.exists(os.path.join(SOLVER_BIN, 'transit')):
    pytest.skip('решатели не собраны: make -C solver', allow_module_level=True)

LAT, LON = 55.70, 37.60


def network(tmp_path, stops, conn, foot=()):
    """stops — широты остановок (долгота LON); conn — (откуда, куда, отправление, прибытие, рейс), секунды."""
    net = tmp_path / 'net'
    net.mkdir(exist_ok=True)
    c = np.array(sorted(conn, key=lambda r: r[2]), dtype=np.int32).reshape(-1, 5)
    c.tofile(net / 'conn.bin')
    np.array(list(foot), dtype=np.int32).reshape(-1, 3).tofile(net / 'foot.bin')
    json.dump(dict(stops=[(lat, LON, str(i), 'bus') for i, lat in enumerate(stops)]), open(net / 'stops.json', 'w'))
    return str(tmp_path)


def at(h, m=0):
    return h * 3600 + m * 60


def test_waits_for_bus_and_rides(tmp_path):
    folder = network(tmp_path, [LAT, LAT + 0.05], [(0, 1, t, t + 600, i) for i, t in enumerate(range(at(10), at(12, 30), 1800))])
    got = transit.times([(LAT, LON), (LAT + 0.05, LON)], folder=folder, start=600, end=660, step=10)
    # выход в 10:00, 10:10 … 10:50; на посадку нужна минута, поэтому на автобус «впритык» не успеть:
    # ждать 30, 20, 10, 30, 20, 10 мин, ехать 10
    assert (1, 0) not in got                                           # обратно автобуса нет
    assert got[0, 1] == pytest.approx(10 + sum([30, 20, 10, 30, 20, 10]) / 6, abs=0.01)


def test_hourly_means_and_unreachable_hours(tmp_path):
    # автобус каждые 10 мин только в 10-м часу, в 11-м — ни одного
    folder = network(tmp_path, [LAT, LAT + 0.05], [(0, 1, t, t + 300, i) for i, t in enumerate(range(at(10, 5), at(11), 600))])
    got = transit.times([(LAT, LON), (LAT + 0.05, LON)], folder=folder, start=600, end=720, step=20, hourly=True)
    assert got[0, 1] == (pytest.approx(10), [pytest.approx(10), None])  # доехали в 3 из 6 отправлений; в 11 ч — никак
    folder = network(tmp_path, [LAT, LAT + 0.05], [(0, 1, at(10, 5), at(10, 10), 0)])
    got = transit.times([(LAT, LON), (LAT + 0.05, LON)], folder=folder, start=600, end=720, step=20, hourly=True)
    assert (0, 1) not in got                                           # доехали меньше чем в половине отправлений
    folder = network(tmp_path, [LAT, LAT + 0.05], [(0, 1, t, t + 300, i) for i, t in enumerate(range(at(10, 5), at(11, 50), 600))])
    mean, hours = transit.times([(LAT, LON), (LAT + 0.05, LON)], folder=folder, start=600, end=720, step=20, hourly=True)[0, 1]
    assert hours == [pytest.approx(10), pytest.approx(10)] and mean == pytest.approx(10)   # 5 мин ждать + 5 ехать


def test_staying_on_board_needs_no_transfer_time_but_changing_does(tmp_path):
    # рейс 0: A → B → C без запаса на B; рейс 1 уходит с B ровно в момент прибытия рейса 0 — на него не пересесть
    stops = [LAT, LAT + 0.05, LAT + 0.1, LAT + 0.15]
    conn = [(0, 1, at(10, 5), at(10, 10), 0), (1, 2, at(10, 10), at(10, 15), 0),
            (1, 3, at(10, 10), at(10, 12), 1), (1, 3, at(10, 30), at(10, 32), 2)]
    folder = network(tmp_path, stops, conn)
    got = transit.times([(lat, LON) for lat in stops], folder=folder, start=600, end=601, step=1)
    assert got[0, 2] == pytest.approx(15)                              # остался в рейсе 0
    assert got[0, 3] == pytest.approx(32)                              # следующий рейс 1 не успеть, ждём 10:30


def test_footpath_transfer(tmp_path):
    stops = [LAT, LAT + 0.05, LAT + 0.0505, LAT + 0.1]                 # 1 и 2 — соседние остановки, 50 с пешком
    conn = [(0, 1, at(10, 5), at(10, 10), 0), (2, 3, at(10, 12), at(10, 20), 1)]
    folder = network(tmp_path, stops, conn, foot=[(1, 2, 50)])
    got = transit.times([(LAT, LON), (LAT + 0.1, LON)], folder=folder, start=600, end=601, step=1)
    assert got[0, 1] == pytest.approx(20)


def test_ride_longer_than_limit_is_unreachable(tmp_path, monkeypatch):
    folder = network(tmp_path, [LAT, LAT + 0.05], [(0, 1, at(10, 5), at(14), 0)])
    monkeypatch.setattr(transit, 'MAX_RIDE', 3 * 3600)
    assert transit.times([(LAT, LON), (LAT + 0.05, LON)], folder=folder, start=600, end=601, step=1) == {}
