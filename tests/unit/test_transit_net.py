"""Сеть общественного транспорта (dispatch/tools/transit.py, osm_bus.py) на крошечных данных: GTFS из трёх остановок,
линия метро, кольцо как МЦК, участок МЦД по «расписанию Яндекса», электричка вне МЦД, автобус области из OSM."""
import json
import os
import zipfile

import numpy as np
import pytest

from dispatch.config import SOLVER_BIN
from dispatch.tools import osm_bus, rasp, transit

LAT, LON = 55.70, 37.60
STEP = 0.01                                    # ≈ 1,1 км по широте


def gtfs_zip(path):
    files = {
        'calendar.txt': 'service_id,monday,tuesday,saturday\nW,1,1,0\nE,0,0,1\n',
        'trips.txt': 'route_id,service_id,trip_id\nr,W,T1\nr,E,T2\n',
        'stops.txt': f'stop_id,stop_name,stop_lat,stop_lon\nA,А,{LAT},{LON}\nB,Б,{LAT + STEP},{LON}\nC,В,{LAT + 2 * STEP},{LON}\n',
        'stop_times.txt': 'trip_id,arrival_time,departure_time,stop_id,stop_sequence\n'
                          'T1,10:20:00,10:20:00,C,3\nT1,10:05:00,10:05:00,A,1\nT1,10:12:00,10:12:00,B,2\n'
                          'T1,10:30:00,10:30:00,X,4\nT2,10:00:00,10:00:00,A,1\nT2,10:10:00,10:10:00,B,2\n'
                          'T1,07:00:00,07:00:00,B,0\n',          # раньше 8:00 — связь B→A не берём
    }
    with zipfile.ZipFile(path, 'w') as z:
        for name, text in files.items():
            z.writestr(name, '﻿' + text)


def station(i, line, name, lat, lon):
    return dict(id=i, lineId=line, name=dict(ru=name), location=dict(lat=lat, lon=lon))


def schema(path):
    st = [station(1, 1, 'Первая', LAT, LON + 0.01), station(2, 1, 'Вторая', LAT + 3 * STEP, LON + 0.01),
          station(3, 1, 'Третья', LAT + 6 * STEP, LON + 0.01),
          station(11, 16, 'Кольцо-1', LAT + 3 * STEP, LON + 0.0102), station(12, 16, 'Кольцо-2', LAT + 4 * STEP, LON + 0.03),
          station(13, 16, 'Кольцо-3', LAT + 2 * STEP, LON + 0.03),
          station(21, 14, 'Альфа', LAT + 6 * STEP, LON + 0.0102), station(23, 14, 'Гамма', LAT + 8 * STEP, LON + 0.0102),
          station(22, 14, 'Москва-Бета', LAT + 10 * STEP, LON + 0.0102)]
    conn = [(1, 2, 120), (2, 3, 120), (11, 12, 150), (12, 13, 150), (13, 11, 150), (21, 23, 100), (23, 22, 200),
            (3, 99, 60)]                                               # 99 — станции нет в схеме: связь пропускаем
    d = dict(stations=st, lines=[dict(id=1, name=dict(ru='Тестовая линия')), dict(id=16, name=dict(ru='МЦК')),
                                 dict(id=14, name=dict(ru='МЦД-1'))],
             connections=[dict(stationFromId=a, stationToId=b, pathLength=t, bi=True) for a, b, t in conn],
             transitions=[dict(stationFromId=2, stationToId=11, pathLength=180, bi=True)])
    json.dump(dict(success=True, data=d), open(path, 'w'), ensure_ascii=False)


def segment(uid, a, b, dep, dur, stops='везде'):
    return dict(thread=dict(uid=uid), stops=stops, duration=dur, departure=f'2026-10-20T{dep}:00+03:00',
                **{'from': dict(title=a), 'to': dict(title=b)})


@pytest.fixture
def data(tmp_path):
    """Папка данных как data/transit/: gtfs, metro, yandex (МЦД и электричка), mo/routes.json."""
    (tmp_path / 'gtfs').mkdir()
    gtfs_zip(tmp_path / 'gtfs' / 'latest.zip')
    (tmp_path / 'metro').mkdir()
    schema(tmp_path / 'metro' / 'schema.json')
    y = tmp_path / 'yandex'
    y.mkdir()
    mcd = [segment('m1', 'Альфа', 'Бета', '10:00', 600), segment('m1', 'Альфа', 'Бета', '10:00', 600),
           segment('m2', 'Бета', 'Альфа', '10:30', 660), segment('x', 'Альфа', 'Нигде', '10:00', 60),
           segment('m3', 'Альфа', 'Бета', '11:00', 300, stops='кроме: Гамма'),
           segment('m4', 'Альфа', 'Бета', '11:30', 300, stops='Москва-Бета')]
    json.dump(dict(method='search', params={}, response=dict(segments=mcd)), open(y / 'search_mcd.json', 'w'))
    (tmp_path / 'mo').mkdir()
    json.dump([], open(tmp_path / 'mo' / 'routes.json', 'w'))             # без него — разбор настоящей выгрузки OSM
    return tmp_path


# ---- мелочи
def test_names_stops_times_headways():
    assert transit._norm('Бирюлёво-Пасс.') == 'бирюлево' and transit._norm('Москва-Рижская') == 'рижская'
    assert transit._norm('Верхние Котлы (Павелецкое направление)') == 'верхниекотлы'
    names = ['а', 'б', 'в']
    assert transit._stopping('везде', names) == [True] * 3 and transit._stopping('', names) == [True] * 3
    assert transit._stopping('кроме: Б', names) == [True, False, True]
    assert transit._stopping('А, В', names) == [True, False, True]
    assert transit._stopping('до Б везде, кроме В', names) == [True] * 3       # сложное описание — везде
    assert transit._sec_of('2026-10-20 10:05:30') == 36330 == transit._sec_of('2026-10-20T10:05:30+03:00')
    assert transit._headway('metro', 18) == 2 and transit._headway('МЦК', 5) == 6 and transit._headway('МЦД', 23) == 15


def test_line_paths():
    assert transit._line_paths({1: [2], 2: [1, 3], 3: [2]}) == [[1, 2, 3], [3, 2, 1]]
    ring = transit._line_paths({1: [2, 3], 2: [1, 3], 3: [2, 1]})
    assert len(ring) == 2 and ring[0][0] == ring[0][-1] and sorted(ring[0][:-1]) == [1, 2, 3]
    fork = transit._line_paths({1: [2], 2: [1, 3, 4], 3: [2], 4: [2]})           # три конечные — шесть путей
    assert len(fork) == 6 and [1, 2, 4] in fork
    assert transit._bfs({1: [2], 2: [1], 3: []}, 1, 3) is None


def test_grid_near():
    g = transit.Grid([(LAT, LON), (LAT + 0.001, LON), (LAT + 0.01, LON)], 300)
    assert sorted(k for k, _ in g.near(LAT, LON, 300)) == [0, 1]
    assert dict(g.near(LAT, LON, 300))[1] == pytest.approx(111.3, abs=1)


# ---- загрузчики
def test_load_gtfs_weekday_sorted_and_served(data):
    net = transit.Network()
    net.load_gtfs(str(data / 'gtfs' / 'latest.zip'), log=lambda *_: None)
    c = net.conn[0]
    assert c[:, 2:4].tolist() == [[36300, 36720], [36720, 37200]]     # A→B, B→C рейса T1; субботний T2, остановка X и 7:00 — нет
    assert c[0, 4] == c[1, 4] and net.served == {0, 1, 2} and net.trips == 1


def test_mcd_timetable_from_saved_answers(data):
    tt = transit.mcd_timetable(str(data / 'yandex'), str(data / 'metro' / 'schema.json'))
    assert set(tt) == {'МЦД-1'}
    trains = sorted(tt['МЦД-1'], key=lambda t: t['dep'])
    assert [(t['from'], t['to'], t['dep'], t['duration'], t['uid']) for t in trains] == [
        ('альфа', 'бета', 36000, 600, 'm1'), ('бета', 'альфа', 37800, 660, 'm2'),
        ('альфа', 'бета', 39600, 300, 'm3'), ('альфа', 'бета', 41400, 300, 'm4')]   # повтор m1 и «Нигде» отброшены
    assert trains[2]['skip'] == {'гамма'} and trains[3]['only'] == {'бета'} and trains[0]['skip'] == set()
    assert transit.mcd_timetable(str(data / 'нет'), str(data / 'metro' / 'schema.json')) == {}


def test_load_metro_builds_trains_and_transfers(data):
    net = transit.Network()
    tt = transit.mcd_timetable(str(data / 'yandex'), str(data / 'metro' / 'schema.json'))
    net.load_metro(str(data / 'metro' / 'schema.json'), timetable=tt, log=lambda *_: None)
    c = np.concatenate(net.conn)
    names = {i: s[2] for i, s in enumerate(net.stops)}
    legs = {(names[a], names[b]) for a, b, *_ in c.tolist()}
    assert ('Первая', 'Вторая') in legs and ('Вторая', 'Первая') in legs and ('Кольцо-3', 'Кольцо-1') in legs
    mcd = sorted((names[a], names[b], dep, arr) for a, b, dep, arr, _ in c.tolist() if 'Гамма' in (names[a], names[b])
                 or (names[a], names[b]) in (('Альфа', 'Москва-Бета'), ('Москва-Бета', 'Альфа')))
    assert mcd == [('Альфа', 'Гамма', 36000, 36200), ('Альфа', 'Москва-Бета', 39600, 39900),   # m3 без Гаммы
                   ('Альфа', 'Москва-Бета', 41400, 41700),                                     # m4 — только до Беты
                   ('Гамма', 'Альфа', 38240, 38460), ('Гамма', 'Москва-Бета', 36200, 36600),
                   ('Москва-Бета', 'Гамма', 37800, 38240)]             # время перегонов схемы ×(ход поезда / схема)
    assert all(r[2] >= transit.DAY_FROM for r in c.tolist())
    assert ('Третья', '99') not in legs
    assert (1, 3, 180) in net.foot and (3, 1, 180) in net.foot        # пересадка из схемы
    at10 = sorted(r[2] for r in c.tolist() if names[r[0]] == 'Первая' and 36000 <= r[2] < 37800)
    assert np.diff(at10).max() == pytest.approx(150, abs=1)           # интервал метро в 10–17 ч — 2,5 мин


def test_thread_offsets_cross_midnight():
    st = lambda code, dep, arr=None: dict(station=dict(code=code, title=code), departure=dep, arrival=arr)
    stops = [st('a', '2026-10-20 23:50:00'), st('b', '2026-10-21 00:05:00'), st('c', None, '2026-10-21 00:20:00')]
    assert transit._thread_offsets(stops) == [('a', 'a', 0), ('b', 'b', 900), ('c', 'c', 1800)]


def test_suburban_trains_from_threads_and_searches(tmp_path, monkeypatch):
    folder = tmp_path / 'y'
    folder.mkdir()
    stops = [dict(station=dict(code=c, title=t), departure=f'2026-10-20 {d}', arrival=None)
             for c, t, d in (('s1', 'Вокзал', '09:00:00'), ('s2', 'Платформа', '09:10:00'), ('s3', 'Город', '09:30:00'))]
    json.dump(dict(response=dict(stops=stops)), open(folder / 'thread_a.json', 'w'))
    answers = {('s1', 's3'): [segment('t1', 'Вокзал', 'Город', '10:00', 1200),
                              segment('t2', 'Вокзал', 'Город', '11:00', 900, stops='Город'),
                              segment('mcd', 'Вокзал', 'Город', '12:00', 600)],
               ('s3', 's1'): [segment('t3', 'Город', 'Вокзал', '10:00', 1200)],
               ('s1', 's9'): [segment('t4', 'Вокзал', 'Деревня', '10:00', 3000)]}

    class FakeRasp:
        def __init__(self):
            self.folder = str(folder)

        def get(self, method, **p):
            return dict(response=dict(segments=answers.get((p['from'], p['to']), [])))
    monkeypatch.setattr(rasp, 'SUBURBAN', {'Тест': [('s1', 's3'), ('s1', 's9')]})
    trains = dict(transit.suburban_trains(FakeRasp(), skip_uids={'mcd'}))
    assert trains['t1'] == [('s1', 36000, 36000), ('s2', 36400, 36400), ('s3', 37200, 37200)]   # время по thread ×2
    assert trains['t2'] == [('s1', 39600, 39600), ('s3', 40500, 40500)]                        # экспресс без Платформы
    assert trains['t3'] == [('s3', 36000, 36000), ('s2', 36800, 36800), ('s1', 37200, 37200)]  # обратно — зеркально
    assert 'mcd' not in trains and trains['t4'] == [('s1', 36000, 36000), ('s9', 39000, 39000)]   # без thread — без остановок
    del trains['t4']
    net = transit.Network()
    net.load_rail(list(trains.items()), {'s1': (LAT, LON, 'Вокзал'), 's2': (LAT + 0.1, LON, 'Платформа'),
                                         's3': (LAT + 0.3, LON, 'Город')}, log=lambda *_: None)
    assert [s[3] for s in net.stops] == ['rail'] * 3 and len(net.conn[0]) == 2 + 1 + 2 and net.trips == 3


def test_osm_bus_rules():
    assert osm_bus.interval_min('00:15') == 15 and osm_bus.interval_min('10-20') == 15 and osm_bus.interval_min('') is None
    short = dict(stops=[[LAT, LON, 'а'], [LAT + 0.02, LON, 'б']])
    long = dict(stops=[[LAT, LON, 'а'], [LAT + 0.2, LON, 'б']], interval=None)
    assert osm_bus.headway(short) == (15, 'urban') and osm_bus.headway(long) == (45, 'suburban')
    assert osm_bus.headway(dict(short, interval='00:07')) == (7, 'urban')
    tr = osm_bus.trips(short)
    assert len(tr) == (osm_bus.LAST - osm_bus.FIRST) // 900 + 1
    run = tr[0][1][1] - tr[0][0][2]
    assert run == pytest.approx(osm_bus.km(short['stops'][0], short['stops'][1]) * 1.3 / 20 * 3600, abs=1)


def test_load_osm_bus_skips_moscow_duplicates(data):
    net = transit.Network()
    net.load_gtfs(str(data / 'gtfs' / 'latest.zip'), log=lambda *_: None)
    moscow = dict(stops=[[LAT, LON, 'А'], [LAT + STEP, LON, 'Б'], [LAT + 0.5, LON, 'далеко']])   # 2 из 3 — у GTFS
    region = dict(stops=[[LAT + 0.5, LON, 'Город'], [LAT + 0.52, LON, 'Вокзал']])
    logs = []
    net.load_osm_bus([moscow, region], log=logs.append)
    assert len(net.stops) == 3 + 2 and 'маршрутов 1 (ещё 1 — Москва' in logs[0]
    assert len(net.conn[1]) == len([t for t in osm_bus.trips(region) if t[0][2] >= transit.DAY_FROM])


def test_link_stops_adds_entrance_time():
    net = transit.Network()
    bus, metro, rail = (net.add_stop(LAT, LON + d, n, k) for d, n, k in ((0, 'автобус', 'bus'), (0.001, 'метро', 'metro'),
                                                                          (0.002, 'электричка', 'rail')))
    net.add_stop(LAT, LON + 0.0005, 'метро 2', 'metro')
    net.link_stops(log=lambda *_: None)
    f = {(a, b): s for a, b, s in net.foot}
    d = transit._xy(LAT, LON + 0.001)[1] - transit._xy(LAT, LON)[1]          # ≈ 63 м по долготе
    assert f[bus, metro] == int(d * transit.DETOUR / transit.WALK) + 120
    assert f[metro, rail] - f[rail, metro] == 0 and f[metro, rail] >= 120 + 60
    assert (metro, 3) not in f                                         # между станциями метро — только из схемы


def test_access_by_streets_with_fallback():
    stops = [(LAT, LON, 'а', 'bus'), (LAT + 0.005, LON, 'метро', 'metro'), (LAT + 0.02, LON, 'далеко', 'bus')]
    straight = transit.access([(LAT, LON)], stops)[0]
    assert [j for j, _ in straight] == [0, 1]                            # 2,2 км — дальше радиуса подхода
    assert dict(straight)[1] - 120 == pytest.approx(0.005 * 111320 * transit.DETOUR / transit.WALK, abs=2)
    by_street = transit.access([(LAT, LON)], stops, walk=lambda p, targets: [30, None][:len(targets)])[0]
    assert dict(by_street)[0] == 30 and dict(by_street)[1] == dict(straight)[1]   # None — по прямой
    class Saving:
        saved = False

        def __call__(self, p, targets):
            return [None] * len(targets)

        def save(self):
            Saving.saved = True
    transit.access([(LAT, LON)], stops, walk=Saving())
    assert Saving.saved
    too_far = transit.access([(LAT, LON)], stops, walk=lambda p, targets: [5000] * len(targets))[0]
    assert too_far == []                                               # по улицам дальше радиуса — не подходим


def test_osrm_walk_cache_and_dead_server(tmp_path):
    class Client:
        calls = 0

        def table(self, profile, src, dst):
            Client.calls += 1
            assert profile == 'foot'
            return [[(2.0, 0.1) if q[0] < 60 else None for q in dst]]
    w = transit.OsrmWalk(Client(), path=str(tmp_path / 'walk.json'))
    assert w((LAT, LON), [(LAT, LON), (61.0, LON)]) == [120.0, None]
    w.save()
    again = transit.OsrmWalk(Client(), path=str(tmp_path / 'walk.json'))
    assert again((LAT, LON), [(LAT, LON)]) == [120.0] and Client.calls == 1

    class Dead:
        def table(self, *a):
            raise OSError('нет сервера')
    dead = transit.OsrmWalk(Dead(), path=str(tmp_path / 'w2.json'))
    assert dead((LAT, LON), [(LAT, LON)]) == [None] and dead.dead


def test_save_sorts_connections(tmp_path):
    net = transit.Network()
    net.conn = [np.array([[0, 1, 37000, 37100, 0], [1, 2, 36000, 36100, 1]])]
    net.stops = [(0, 0, 'а', 'bus')] * 3
    net.foot = [(0, 1, 50), (0, 1, 50)]
    assert net.save(str(tmp_path)) == (2, 1)
    c = np.fromfile(tmp_path / 'conn.bin', dtype=np.int32).reshape(-1, 5)
    assert c[:, 2].tolist() == [36000, 37000]


@pytest.mark.skipif(not os.path.exists(os.path.join(SOLVER_BIN, 'transit')), reason='решатели не собраны')
def test_build_network_and_times(data, monkeypatch):
    """Вся сборка на крошечных данных и расчёт: от «Первой» до «Третьей» на метро быстрее, чем пешком 6,7 км."""
    json.dump([dict(stops=[[LAT + 0.5, LON, 'Город'], [LAT + 0.52, LON, 'Вокзал']])], open(data / 'mo' / 'routes.json', 'w'))
    logs = []
    monkeypatch.setattr(transit, 'suburban_trains', lambda r, skip_uids=(): [('e', [('s1', 36000, 36000), ('s2', 36600, 36600)])])
    monkeypatch.setattr(rasp, 'stations', lambda r: {'s1': (LAT + 0.5, LON, 'А'), 's2': (LAT + 0.6, LON, 'Б')})
    transit.build_network(str(data), log=logs.append)
    assert any('Электрички вне МЦД: станций 2, поездов 1' in line for line in logs)
    assert any('МЦД-1 (4 поездов)' in line for line in logs) and any('Автобусы области' in line for line in logs)
    monkeypatch.setattr(transit, 'DIR', str(data))
    assert transit.available()
    pts = [(LAT, LON + 0.0105), (LAT + 6 * STEP, LON + 0.0105), (LAT + 0.51, LON)]
    got = transit.times(pts, start=600, end=720, step=10, hourly=True)
    mean, hours = got[0, 1]
    # вход 2 мин + ожидание ≈ 1,25 мин + 4 мин в пути + выход 2 мин + подход ~1 мин
    assert 8 < mean < 12 and len(hours) == 2 and all(h is not None for h in hours)
    assert (0, 2) not in got                                           # до области — ни одного рейса в окне


def test_transit_command(data, monkeypatch, capsys):
    from dispatch.cli import main
    monkeypatch.setattr(transit, 'DIR', str(data))
    assert main(['transit']) in (0, None)
    out = capsys.readouterr().out
    assert 'Сеть: остановок' in out and (data / 'net' / 'conn.bin').exists() and 'cache/matrix_pt.json' in out


def test_osm_load_reads_saved_routes_or_skips(tmp_path):
    assert osm_bus.load(str(tmp_path), pbf=str(tmp_path / 'нет.pbf')) == []
    (tmp_path / 'mo').mkdir()
    json.dump([dict(stops=[])], open(tmp_path / 'mo' / 'routes.json', 'w'))
    assert osm_bus.load(str(tmp_path)) == [dict(stops=[])]


OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
  <node id="1" lat="54.85" lon="38.16"><tag k="name" v="Вокзал"/></node>
  <node id="2" lat="54.86" lon="38.16"><tag k="name" v="Больница"/></node>
  <node id="3" lat="54.87" lon="38.16"/>
  <node id="4" lat="54.871" lon="38.161"/>
  <node id="5" lat="54.872" lon="38.160"/>
  <way id="10"><nd ref="3"/><nd ref="4"/><nd ref="5"/><tag k="name" v="Рынок"/></way>
  <node id="6" lat="10.0" lon="10.0"/>
  <node id="7" lat="10.1" lon="10.0"/>
  <relation id="100">
    <member type="node" ref="1" role="stop"/><member type="node" ref="2" role="stop_entry_only"/>
    <member type="way" ref="10" role="platform"/>
    <tag k="type" v="route"/><tag k="route" v="bus"/><tag k="ref" v="1"/><tag k="interval" v="00:12"/>
  </relation>
  <relation id="101">
    <member type="node" ref="1" role="platform"/><member type="way" ref="10" role="platform"/>
    <member type="node" ref="2" role="stop"/>
    <tag k="type" v="route"/><tag k="route" v="minibus"/><tag k="ref" v="2"/>
  </relation>
  <relation id="102">
    <member type="node" ref="6" role="stop"/><member type="node" ref="7" role="stop"/>
    <tag k="type" v="route"/><tag k="route" v="bus"/><tag k="ref" v="далеко"/>
  </relation>
  <relation id="103">
    <member type="node" ref="1" role="stop"/><member type="node" ref="2" role="stop"/>
    <tag k="type" v="route"/><tag k="route" v="train"/>
  </relation>
</osm>
"""


def test_osm_extract_routes(tmp_path):
    pbf = tmp_path / 'region.osm'
    pbf.write_text(OSM)
    routes = osm_bus.extract(str(pbf))
    assert [(r['osm_id'], r['ref'], r['mode']) for r in routes] == [(100, '1', 'bus'), (101, '2', 'minibus')]
    assert [s[2] for s in routes[0]['stops']] == ['Вокзал', 'Больница'] and routes[0]['interval'] == '00:12'
    assert [s[2] for s in routes[1]['stops']] == ['Вокзал', 'Рынок']   # платформ больше, чем стоп-позиций
    assert routes[1]['stops'][1][:2] == [pytest.approx(54.871), pytest.approx(38.1603, abs=1e-3)]   # центр линии
    saved = osm_bus.load(str(tmp_path / 'data'), pbf=str(pbf), log=lambda *_: None)
    assert saved == routes and (tmp_path / 'data' / 'mo' / 'routes.json').exists()
