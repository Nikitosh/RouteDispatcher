"""Командная строка: python -m dispatch <команда> ...  Логики здесь нет — только разбор аргументов и вызов модулей."""
import argparse
import os
import sys

from .config import TravelSettings
from .files.readers import InputError
from .geo.geocoder import GeocodeError
from .geo.osrm import OsrmError
from .model.problem import ProblemError
from .solve.runner import SolverError

MODES_HELP = 'например car=1.3,pt=1'


def mode_values(text):
    """«car=1.5,bike=1» → {'car': 1.5, 'bike': 1.0}"""
    out = {}
    for part in filter(None, text.split(',')):
        key, _, value = part.partition('=')
        if key not in ('car', 'pt', 'bike', 'foot') or not value:
            raise argparse.ArgumentTypeError(f'ожидаю car=1.5,pt=1,... а не «{part}»')
        out[key] = float(value)
    return out


def build_parser():
    ap = argparse.ArgumentParser(prog='python -m dispatch', description='Планирование выездов инженеров')
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = TravelSettings()

    p = sub.add_parser('plan', help='план на день')
    p.add_argument('orders', help='заявки: CSV организаторов или xlsx с теми же колонками')
    p.add_argument('--brigades', '-b', required=True, help='бригады: Бригада; Транспорт; Навыки; Старт')
    p.add_argument('--out', '-o', default='out', help='папка результата')
    p.add_argument('--name', help='название участка (по умолчанию из имени файла)')
    p.add_argument('--time', type=float, default=3.0, help='время решателя, с')
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--traffic', type=mode_values, default=d.traffic, help=f'коэффициент пробок, {MODES_HELP}')
    p.add_argument('--overhead', type=mode_values, default=d.overhead, help='минуты на подход к заявке, например car=5,bike=3')
    p.add_argument('--no-geometry', action='store_true', help='не рисовать маршруты по улицам')

    r = sub.add_parser('replan', help='перестроить план после события')
    r.add_argument('previous', help='папка предыдущего результата (с plan.json)')
    r.add_argument('--at', required=True, help='время события, например 14:00')
    ev = r.add_mutually_exclusive_group(required=True)
    ev.add_argument('--unavailable', help='бригада стала недоступна (имя или номер)')
    ev.add_argument('--cancel', help='номер отменённой заявки')
    ev.add_argument('--urgent', help='адрес новой заявки или «широта, долгота»')
    r.add_argument('--type', default='авария', help='тип новой заявки: авария, подключение, ремонт, дозаказ')
    r.add_argument('--window', help='окно начала работ новой заявки, например 15:00-17:00')
    r.add_argument('--need', help='требуемый транспорт новой заявки, например «авто»')
    r.add_argument('--id', help='номер новой заявки')
    r.add_argument('--district', default='', help='район новой заявки')
    r.add_argument('--out', '-o', required=True)
    r.add_argument('--no-geometry', action='store_true')

    s = sub.add_parser('site', help='страница диспетчера из папок результатов plan и replan')
    s.add_argument('dirs', nargs='+')
    s.add_argument('--out', '-o', default='site')
    sv = sub.add_parser('serve', help='открыть страницу локально')
    sv.add_argument('dir', nargs='?', default='site')
    sv.add_argument('--port', type=int, default=8080)

    pf = sub.add_parser('prefetch', help='линии по улицам для всех пар точек участков (для показа без OSRM)')
    pf.add_argument('dirs', nargs='+', help='папки результатов plan')
    dd = sub.add_parser('demo-data', help='файлы бригад и заявок для демо по данным организаторов')
    dd.add_argument('--transport', default='mix2', choices=['mix1', 'mix2', 'pt', 'car'])
    g = sub.add_parser('gen-district', help='новый участок Москвы с адресами из OpenStreetMap')
    g.add_argument('regions', nargs='+', help='Север, Запад, Северо-восток')
    g.add_argument('--seed', type=int, default=1)
    c = sub.add_parser('calibrate', help='калибровка коэффициента пробок по Яндекс Картам')
    c.add_argument('step', choices=['sample', 'fit'])
    c.add_argument('file', help='sample: файл заявок; fit: заполненный pairs.csv')
    c.add_argument('--out', '-o', default='pairs.csv')
    c.add_argument('-n', type=int, default=30)
    c.add_argument('--apikey', help='ключ API Матрицы расстояний Яндекса')
    return ap


def cmd_plan(a):
    from .files import document
    from .files.report import plan_summary
    from .files.xlsx import write_xlsx
    from .planning.planner import Planner
    planner = Planner(TravelSettings(a.traffic, a.overhead))
    problem, meta = planner.load(a.orders, a.brigades, a.name)
    doc = planner.run(problem, meta, a.out, a.time, a.seed, geometry=not a.no_geometry)
    print(plan_summary(doc))
    print(f"\nФайлы: {write_xlsx(doc, a.out)}, {document.save(doc, a.out)} ({doc['meta']['runtime_s']} с)")
    return 0 if doc['plan']['check']['ok'] else 2


def cmd_replan(a):
    from .files import document
    from .files.report import replan_summary
    from .files.xlsx import write_xlsx
    from .geo.geometry import RouteGeometry
    from .planning.replan import Cancel, Replanner, Unavailable, Urgent
    if a.unavailable:
        event = Unavailable(a.unavailable)
    elif a.cancel:
        event = Cancel(a.cancel)
    else:
        event = Urgent(a.urgent, a.type, a.window, a.need, a.id, a.district)
    doc = Replanner(document.load(a.previous)).apply(a.at, event)
    doc['event']['previous'] = os.path.abspath(a.previous)
    if not a.no_geometry:
        document.fill_geometry(doc, RouteGeometry())
    os.makedirs(a.out, exist_ok=True)
    print(replan_summary(doc))
    print(f'\nФайлы: {write_xlsx(doc, a.out)}, {document.save(doc, a.out)}')
    return 0 if doc['plan']['check']['ok'] else 2


def cmd_site(a):
    from .site.builder import build_site
    print(f'Страница: {build_site(a.dirs, a.out)}/index.html (открыть: python -m dispatch serve {a.out})')
    return 0


def cmd_serve(a):
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=a.dir)
    print(f'http://localhost:{a.port}/  (Ctrl+C — остановить)')
    http.server.ThreadingHTTPServer(('', a.port), handler).serve_forever()


def cmd_prefetch(a):
    from .files import document
    from .geo.geometry import RouteGeometry
    geometry = RouteGeometry()
    for d in a.dirs:
        doc = document.load(d)
        points = list(dict.fromkeys([(doc['office']['lat'], doc['office']['lon'])] + [(b['lat'], b['lon']) for b in doc['brigades']]
                                    + [(o['lat'], o['lon']) for o in doc['orders']]))
        n, missing = geometry.prefetch(points, {b['mode'] for b in doc['brigades']})
        print(f"{doc['meta']['name']}: {len(points)} точек, запрошено линий {n}, не получено {missing}")
    return 0


def cmd_demo_data(a):
    from .tools.demo_data import write_all
    for region, b, o, nb, total, need in write_all(a.transport):
        print(f'{region}: {nb} бригад → {b}\n  {total} заявок, только машина {need} → {o}')
    return 0


def cmd_gen_district(a):
    from .tools.districts import write
    for region in a.regions:
        x = write(region, a.seed)
        print(f"{region}: {x['n']} заявок, {x['nb']} бригад, только машина {x['need']}, офис {x['office']}\n  районы: "
              + ', '.join(f'{d} {c}' for d, c in x['per_district'].items()) + f"\n  {x['synth']}\n  {x['orders']}\n  {x['brigades']}")
    return 0


def cmd_calibrate(a):
    from .tools import calibrate
    if a.step == 'sample':
        path, n = calibrate.sample(a.file, a.out, a.n, apikey=a.apikey)
        print(f'{path}: {n} пар. Заполните «Яндекс, мин» (будни, отправление 14:00) и запустите: python -m dispatch calibrate fit {path}')
    else:
        r = calibrate.fit(a.file)
        print(f"Пар: {r['n']}. Коэффициент (медиана Яндекс/OSRM): {r['ratio']:.2f}. "
              f"Прямая: Яндекс ≈ {r['slope']:.2f} · OSRM + {r['intercept']:.1f} мин.\nЗапуск: python -m dispatch plan ... --traffic car={r['ratio']:.2f}")
    return 0


COMMANDS = dict(plan=cmd_plan, replan=cmd_replan, site=cmd_site, serve=cmd_serve, prefetch=cmd_prefetch,
                calibrate=cmd_calibrate, **{'demo-data': cmd_demo_data, 'gen-district': cmd_gen_district})


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except FileNotFoundError as e:
        print(f'Ошибка: нет файла {e.filename or e}', file=sys.stderr)
    except (InputError, GeocodeError, OsrmError, ProblemError, SolverError, ValueError) as e:
        print(f'Ошибка: {e}', file=sys.stderr)
    return 1
