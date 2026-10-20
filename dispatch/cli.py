"""Командная строка: python -m dispatch <команда> ...  Логики здесь нет — только разбор аргументов и вызов модулей."""
import argparse
import json
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


def travel_settings(a):
    """--traffic и --overhead дополняют значения по умолчанию, а не заменяют их целиком."""
    d = TravelSettings()
    return TravelSettings({**d.traffic, **a.traffic}, {**d.overhead, **a.overhead}, hourly=not a.flat_traffic)


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
    p.add_argument('--tries', type=int, default=1, help='запусков решателя с сидами seed, seed+1, …; берётся лучший план')
    p.add_argument('--answer', help='ответ решателя прошлого прогона на те же файлы (answer.json): соревнуется с новыми '
                   'запусками, берётся лучший; с --tries 0 — только он')
    p.add_argument('--traffic', type=mode_values, default={}, help=f'коэффициент пробок, {MODES_HELP}; '
                   f'не указанные — по умолчанию {d.traffic}')
    p.add_argument('--overhead', type=mode_values, default={}, help='минуты на подход к заявке, например car=5,bike=3; '
                   f'не указанные — по умолчанию {d.overhead}')
    p.add_argument('--flat-traffic', action='store_true', help='пробки одним коэффициентом на весь день, без поправки на час')
    p.add_argument('--coverage', action='store_true', help='карта аварийного покрытия: через сколько минут начнут аварию в '
                   'каждой точке участка в каждые полчаса (нужен OSRM)')
    p.add_argument('--deficit', action='store_true', help='карта дефицита «районы × окна»: сколько ещё подключений помещается')
    p.add_argument('--curve', action='store_true', help='кривая «бригады против км»: план с пределом бригад от минимума −3 до +3')
    p.add_argument('--price', action='store_true', help='цена ограничений: план без одного правила за раз (навыки, транспорт, '
                   'смены, обед, оборудование) с доказанной нижней границей')
    p.add_argument('--no-lunch', action='store_true', help='без обеда (по умолчанию 45 мин с 13:00 до 16:00 при смене от 6 ч)')
    p.add_argument('--no-equipment', action='store_true', help='без учёта оборудования (роутеры, приставки)')
    p.add_argument('--no-geometry', action='store_true', help='не рисовать маршруты по улицам')
    p.add_argument('--schedule', choices=('2/2', '5/2'), help='график бригад, у которых он не указан в файле: '
                   '2/2 — смена 10:00–22:00, 5/2 — по очереди 10:00–19:00 и 13:00–22:00')
    p.add_argument('--early', action='store_true', help='что если клиенты согласятся начать до часа раньше окна: '
                   'бригады, км и кому звонить')
    p.add_argument('--windows', action='store_true', help='цена точного времени: часовые окна вместо двухчасовых у 25/50/100%% клиентов')
    p.add_argument('--reviews', help='отзывы клиентов (CSV, см. python -m dispatch reviews): рейтинги монтажников в плане')
    p.add_argument('--facts', nargs='+', help='история фактических длительностей выездов (CSV, python -m dispatch facts): '
                   'коэффициенты сложности уточняются по ней')
    p.add_argument('--best', action='store_true', help='режим «лучшие»: наименьшее K, при котором лучшие по рейтингу K '
                   'монтажников из файла бригад закрывают день, и кривая цены качества (нужен --reviews)')

    r = sub.add_parser('replan', help='перестроить план после события')
    r.add_argument('previous', help='папка предыдущего результата (с plan.json)')
    r.add_argument('--at', required=True, help='время события, например 14:00')
    ev = r.add_mutually_exclusive_group(required=True)
    ev.add_argument('--unavailable', help='бригада стала недоступна (имя или номер)')
    ev.add_argument('--cancel', help='номер отменённой заявки')
    ev.add_argument('--finish', nargs=2, metavar=('ЗАЯВКА', 'ЧЧ:ММ'),
                    help='заявка выполнена (или будет выполнена) в это время — быстрее или медленнее плана')
    ev.add_argument('--reschedule', nargs=2, metavar=('ЗАЯВКА', 'ОКНО'), help='клиент перенёс окно, например 74198 16:00-18:00')
    ev.add_argument('--breakdown', metavar='БРИГАДА', help='у бригады сломалась машина: дальше день на транспорте --to')
    r.add_argument('--to', default='pt', choices=('pt', 'foot', 'bike'), help='на чём бригада продолжает после поломки')
    ev.add_argument('--urgent', help='адрес новой заявки или «широта, долгота»')
    r.add_argument('--type', default='авария', help='тип новой заявки: авария, подключение, ремонт, дозаказ')
    r.add_argument('--window', help='окно начала работ новой заявки, например 15:00-17:00')
    r.add_argument('--need', help='требуемый транспорт новой заявки, например «авто»')
    r.add_argument('--id', help='номер новой заявки')
    r.add_argument('--district', default='', help='район новой заявки')
    r.add_argument('--out', '-o', required=True)
    r.add_argument('--strategy', default='insert', choices=['insert', 'optimize', 'keep'],
                   help='insert — минимум перестановок; optimize — лучший план на остаток дня решателем; keep — ничего не менять')
    r.add_argument('--time', type=float, default=3.0, help='время решателя для --strategy optimize, с')
    r.add_argument('--no-geometry', action='store_true')

    s = sub.add_parser('site', help='страница диспетчера из папок результатов plan и replan')
    s.add_argument('dirs', nargs='+')
    s.add_argument('--out', '-o', default='site')
    s.add_argument('--api', action='store_true', help='пакеты данных для сервера (события и правка плана на странице); нужен OSRM')
    s.add_argument('--scenarios', help='сценарии страницы по группам (JSON: [{slug, title, text, steps: [{slug, title, text, '
                                       'dir, order?, show?}]}], пример — scripts/scenarios.json)')
    sv = sub.add_parser('serve', help='открыть страницу локально')
    sv.add_argument('dir', nargs='?', default='site')
    sv.add_argument('--port', type=int, default=8080)

    pf = sub.add_parser('prefetch', help='линии по улицам для всех пар точек участков (для показа без OSRM)')
    pf.add_argument('dirs', nargs='+', help='папки результатов plan')
    dd = sub.add_parser('demo-data', help='файлы бригад и заявок для демо по данным организаторов')
    dd.add_argument('--transport', default='mix2', choices=['mix1', 'mix2', 'pt', 'car'])
    h = sub.add_parser('houses', help='данные о домах заявок для коэффициента сложности (cache/houses.json)')
    h.add_argument('orders', nargs='*', help='файлы заявок (по умолчанию все data/demo/* заявки.xlsx)')
    h.add_argument('--osm', help='выгрузка OSM, по умолчанию osrm/cfd.osm.pbf')
    sub.add_parser('facts', help='синтетика для «план учится»: история фактических длительностей выездов → data/demo/выезды.csv')
    sub.add_parser('transit', help='сеть общественного транспорта по расписаниям (data/transit/net): GTFS Мосгортранса, '
                   'схема Мосметро, расписания МЦД из Яндекса, если загружены')
    hm = sub.add_parser('homes', help='дома бригад для демо: старт рабочего дня из дома (колонка «Старт»)')
    hm.add_argument('regions', nargs='*', help='участки (по умолчанию все шесть демо-участков)')
    rv = sub.add_parser('reviews', help='синтетика для рейтинга: парк монтажников и история отзывов клиентов')
    rv.add_argument('regions', nargs='*', help='участки (по умолчанию Восток)')
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
    planner = Planner(travel_settings(a), lunch=not a.no_lunch,
                      equipment=not a.no_equipment)
    problem, meta = planner.load(a.orders, a.brigades, a.name, a.schedule or '')
    learned = None
    if a.facts:                                         # план учится на выездах (model/learning.py)
        from .model import complexity, learning
        from .model.problem import Problem
        facts = [f for path in a.facts for f in learning.read(path)[0]]
        region = os.path.basename(a.orders).split(' заявки')[0]          # участок — как в истории выездов
        factors, rows = learning.fit_for(facts, planner.houses, problem.brigades, region)   # и темп бригад участка
        complexity.apply(problem.orders, planner.houses, factors)
        problem = Problem(problem.name, problem.orders, problem.brigades, problem.starts, problem.matrices)
        learned = dict(facts=len(facts), rows=[r for r in rows if abs(r['now'] - r['was']) >= 0.03])
        print(f'Длительности уточнены по {len(facts)} фактическим выездам: '
              + ', '.join(f"{r['what']} ×{r['was']:.2f}→×{r['now']:.2f}" for r in learned['rows']))
    if a.best and not a.reviews:
        raise ValueError('для --best нужен --reviews')
    ratings = None
    if a.reviews:
        from .model import rating
        ratings = rating.compute(rating.read(a.reviews), {b.name: b.skills for b in problem.brigades})
    if a.best:
        from .planning.best import BestMode
        doc = BestMode(planner, ratings).run(problem, meta, a.out, a.time, a.seed)
    else:
        doc = planner.run(problem, meta, a.out, a.time, a.seed, geometry=not a.no_geometry, tries=a.tries,
                          answer=open(a.answer, encoding='utf-8').read() if a.answer else None)
    if learned:
        doc['learned'] = learned
    if ratings:
        from .planning.best import attach
        attach(doc, ratings)
    if a.coverage or a.deficit:                         # карты покрытия аварий и дефицита «районы × окна»
        from .planning import coverage
        routes = [[] for _ in problem.brigades]
        for r in doc['plan']['routes']:
            routes[r['v']] = [s['k'] for s in r['stops']]
        if a.coverage:
            doc['coverage'] = coverage.coverage(problem, routes, planner.matrices.client, planner.travel)
        if a.deficit:
            doc['deficit'] = coverage.deficit(problem, routes)
    if a.curve or a.price:
        from .planning import fleet
        if a.curve:
            doc['curve'] = fleet.curve(planner.solver, problem, doc['plan']['metrics']['brigades_used'], a.time, a.seed, a.out)
        if a.price:
            doc['price'] = fleet.price(planner.solver, problem, a.time, a.seed, a.out)
    unassigned = [o['k'] for o in doc['orders'] if o['plan'] is None]
    if unassigned and not a.best:                       # сколько и каких бригад не хватает (planning/fleet.py)
        from .planning import fleet
        doc['shortage'] = fleet.shortage(problem, unassigned, office='офис')
    if a.early or a.windows:
        from .planning import whatif
        from .planning.best import subproblem
        base = subproblem(problem, [next(i for i, b in enumerate(problem.brigades) if b.name == x['name']) for x in doc['brigades']])
        current = [[] for _ in doc['brigades']]          # основной план в нумерации base
        for rt in doc['plan']['routes']:
            current[rt['v']] = [st['k'] for st in rt['stops']]
        if a.early:
            doc['early'] = whatif.early_start(planner.solver, base, a.time, a.seed, a.out, current=current)
        if a.windows:
            doc['exact_time'] = whatif.exact_time(planner.solver, base, a.time, a.seed, a.out, current=current)
    print(plan_summary(doc))
    print(f"\nФайлы: {write_xlsx(doc, a.out)}, {document.save(doc, a.out)} ({doc['meta']['runtime_s']} с)")
    return 0 if doc['plan']['check']['ok'] else 2


def cmd_replan(a):
    from .files import document
    from .files.report import replan_summary
    from .files.xlsx import write_xlsx
    from .geo.geometry import RouteGeometry
    from .planning.replan import Breakdown, Cancel, Finish, Replanner, Reschedule, Unavailable, Urgent
    if a.finish:
        event = Finish(*a.finish)
    elif a.reschedule:
        event = Reschedule(*a.reschedule)
    elif a.breakdown:
        event = Breakdown(a.breakdown, a.to)
    elif a.unavailable:
        event = Unavailable(a.unavailable)
    elif a.cancel:
        event = Cancel(a.cancel)
    else:
        event = Urgent(a.urgent, a.type, a.window, a.need, a.id, a.district)
    from .solve.runner import SolverRunner
    doc = Replanner(document.load(a.previous)).apply(a.at, event, strategy=a.strategy, solver=SolverRunner(),
                                                      seconds=a.time, workdir=os.path.join(a.out, 'rest'))
    doc['event']['previous'] = os.path.relpath(os.path.abspath(a.previous))   # без личных путей; site читает от той же папки
    if not a.no_geometry:
        document.fill_geometry(doc, RouteGeometry())
    os.makedirs(a.out, exist_ok=True)
    print(replan_summary(doc))
    print(f'\nФайлы: {write_xlsx(doc, a.out)}, {document.save(doc, a.out)}')
    return 0 if doc['plan']['check']['ok'] else 2


def cmd_site(a):
    from .site.builder import build_site
    scenarios = json.load(open(a.scenarios, encoding='utf-8')) if a.scenarios else None
    print(f'Страница: {build_site(a.dirs, a.out, api=a.api, scenarios=scenarios)}/index.html (открыть: python -m dispatch serve {a.out})')
    return 0


def cmd_serve(a):
    print(f'http://localhost:{a.port}/  (Ctrl+C — остановить)')
    if os.path.isdir(os.path.join(a.dir, 'api')):          # есть пакеты данных — страница с пересчётом (dispatch/api.py)
        from .api import create_app
        create_app(os.path.abspath(a.dir)).run(port=a.port, threaded=True)
        return 0
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=a.dir)
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


def cmd_facts(a):
    from .tools import facts
    facts.build()


def cmd_transit(a):
    from .tools import transit
    transit.build_network()
    print('Матрицы «Общ. транспорт» пересчитаются при следующем plan; старые пары — в cache/matrix_pt.json (удалить, '
          'чтобы пересчитать)')


def cmd_houses(a):
    import glob
    from .tools import houses
    files = a.orders or sorted(glob.glob(os.path.join(houses.DATA_DIR, 'demo', '* заявки.xlsx')))
    r = houses.build(files, a.osm or houses.OSM_PBF)
    print(f"{r['path']}: домов {r['found']} из {r['total']}; этажность {r['levels']}, лифты {r['lifts']}, год {r['year']}, "
          f"этажи квартир (кадастр) {r['floors']}, подъезды OSM {r['entrances']} (с квартирами {r['ranges']}), "
          f"офис {r['office']}, шлагбаум {r['gated']}")
    return 0


def cmd_homes(a):
    from .tools import homes
    for region in a.regions or ['Восток', 'Юго-восток', 'Югоцентр', 'Север', 'Запад', 'Северо-восток']:
        path, rows = homes.assign(region)
        print(f'{region}: {path}\n' + '\n'.join(f'  {name}: {start}' for name, _, _, start in rows))
    return 0


def cmd_reviews(a):
    from .tools import reviews
    for region in a.regions or ['Восток']:
        r = reviews.write(region)
        print(f"{region}: {r['n']} монтажников → {r['pool']}\n  {r['visits']} выездов за 60 дней, оценок {r['rated']} → {r['reviews']}\n"
              f"  относительно истинного качества: ошибка рейтинга {r['mae_bayes']:.2f}, простого среднего {r['mae_naive']:.2f}; "
              f"порядок монтажников (Спирмен) {r['rank_bayes']:.2f} против {r['rank_naive']:.2f}")
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
                calibrate=cmd_calibrate, houses=cmd_houses, transit=cmd_transit, facts=cmd_facts, homes=cmd_homes, reviews=cmd_reviews, **{'demo-data': cmd_demo_data, 'gen-district': cmd_gen_district})


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except FileNotFoundError as e:
        print(f'Ошибка: нет файла {e.filename or e}', file=sys.stderr)
    except (InputError, GeocodeError, OsrmError, ProblemError, SolverError, ValueError) as e:
        print(f'Ошибка: {e}', file=sys.stderr)
    return 1
