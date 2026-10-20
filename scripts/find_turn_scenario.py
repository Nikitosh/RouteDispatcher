"""Подбор демо-сценария «Разворот»: момент и адрес аварии, при которых инженер на машине, который едет к
заявке, разворачивается к аварии. Перебирает время события с шагом 5 минут, для каждого инженера на машине в пути
берёт жилые дома OSM позади него (против направления движения, 1–2,5 км) и проверяет перепланированием, что авария
достаётся именно ему и он разворачивается.

Запуск из корня: .venv/bin/python scripts/find_turn_scenario.py out/Восток [--from 11:00 --to 17:00] [-o out/Восток_разворот]
Печатает найденный сценарий и команду replan; с -o сразу выполняет её. Решатель многопоточный и от запуска к запуску
даёт немного разные планы, поэтому scripts/demo.sh подбирает сценарий заново по свежему плану. Нужен OSRM.
"""
import argparse
import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dispatch.files import document                                   # noqa: E402
from dispatch.files.readers import parse_minutes                      # noqa: E402
from dispatch.formatting import hhmm                                  # noqa: E402
from dispatch.geo.geocoder import Geocoder                            # noqa: E402
from dispatch.planning.replan import Replanner, Urgent                 # noqa: E402
from dispatch.tools.homes import residential                          # noqa: E402

M_LAT = 111320.0


def vec(a, b):
    return ((b[0] - a[0]) * M_LAT, (b[1] - a[1]) * M_LAT * math.cos(math.radians(a[0])))


def angle(u, w):
    nu, nw = math.hypot(*u), math.hypot(*w)
    return math.degrees(math.acos(max(-1, min(1, (u[0] * w[0] + u[1] * w[1]) / (nu * nw))))) if nu and nw else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('plan_dir')
    ap.add_argument('--from', dest='t0', default='10:30')
    ap.add_argument('--max-wait', type=float, default=35, help='авария должна начаться не позже, мин')
    ap.add_argument('--to', dest='t1', default='12:55', help='до окна обеда (13:00): иначе бригада сначала обедает')
    ap.add_argument('--tries', type=int, default=4, help='домов на одну бригаду и момент')
    ap.add_argument('-o', '--out', help='сразу выполнить replan в эту папку')
    a = ap.parse_args()
    doc = document.load(a.plan_dir)
    geocoder = Geocoder(online=False)
    cache = {}
    for at in range(parse_minutes(a.t0), parse_minutes(a.t1) + 1, 5):
        r = Replanner(copy.deepcopy(doc))
        r.at = at
        r._build()
        r.frozen_len = {v: r.frozen(v) for v in range(len(r.brigades))}
        for v, w in r._locate().items():
            b = r.brigades[v]
            if w['kind'] != 'transit' or 2 not in b.skills or b.mode != 'car':
                continue
            here, goal = (w['lat'], w['lon']), (r.orders[w['toward']].lat, r.orders[w['toward']].lon)
            heading = vec(here, goal)
            key = (round(here[0], 2), round(here[1], 2))
            if key not in cache:
                cache[key] = residential([here])
            behind = [h for h in cache[key] if 1000 < math.hypot(*vec(here, h)) < 2500 and angle(heading, vec(here, h)) > 140]
            for h in sorted(behind, key=lambda h: -angle(heading, vec(here, h)))[:a.tries]:
                address = f'Город Москва, {h[2]}'
                geocoder.remember(address, h[0], h[1], 'osm')
                geocoder.save()
                res = Replanner(copy.deepcopy(doc)).apply(at, Urgent(address, 'авария', need='авто', id='90010'))
                acc, turned = res['event']['accident'], res['event']['turned']
                ok = acc and acc['brigade'] == v and any(t['brigade'] == v and t['kind'] == 'transit' for t in turned)
                ok = ok and acc['wait'] <= a.max_wait and res['plan']['metrics']['unassigned'] == 0   # быстро и никого не потеряли
                if ok and res['plan']['check']['ok']:
                    print(f"{hhmm(at)}: {b.name} ехала к заявке {r.orders[w['toward']].id}, авария «{address}» "
                          f"в {math.hypot(*vec(here, h)) / 1000:.1f} км позади (угол {angle(heading, vec(here, h)):.0f}°), "
                          f"начало аварии через {acc['wait']:.0f} мин")
                    args = ['replan', a.plan_dir, '--at', hhmm(at), '--urgent', address, '--type', 'авария', '--need', 'авто',
                            '--id', '90010', '-o', a.out or f'{a.plan_dir}_разворот']
                    print('python -m dispatch ' + ' '.join(f'"{x}"' if ' ' in x else x for x in args))
                    if a.out:
                        from dispatch.cli import main as cli
                        return cli(args)
                    return 0
    print('сценарий не найден')
    return 1


if __name__ == '__main__':
    sys.exit(main())
