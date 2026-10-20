"""Сервер страницы диспетчера: статика и пересчёт плана по запросу (локально — python -m dispatch serve, на Vercel —
app.py в папке сайта). OSRM не нужен: матрицы и линии берутся из пакетов данных участков (site/bundle.py), новые точки
считаются через ближайшие известные (geo/nearby.py). Решатель — бинарники в DISPATCH_SOLVER_BIN.

Состояние не хранится: страница присылает текущий план (документ plan.json или результат прошлого события) и событие,
сервер возвращает новый документ. Черновик и версии живут в браузере.

POST /api/variants  {region, doc, at, event}  → {variants: [{strategy, title, summary, doc}]}
POST /api/options   {region, doc, at, order}  → {options: [{brigade, name, ok, km, shifted, start, why}]}
POST /api/move      {region, doc, at, order, brigade} → {doc}
event: {kind: urgent, lat, lon, type, window?, need?, id?} | {kind: cancel, order} | {kind: unavailable, brigade}
       | {kind: finish, order, end} (end — минуты от 10:00) | {kind: window, order, window: «16:00-18:00» или [a, b]}
       | {kind: breakdown, brigade, mode: pt|foot|bike}
"""
import copy
import os
import re
import tempfile

from flask import Flask, jsonify, request, send_from_directory

from .files import document as docs
from .files.readers import InputError
from .geo.geocoder import Geocoder
from .geo.geometry import RouteGeometry
from .geo.houses import HouseIndex
from .geo.matrices import MatrixBuilder
from .geo.nearby import NearbyEstimate
from .planning.replan import Breakdown, Cancel, Finish, Move, Replanner, Reschedule, Unavailable, Urgent
from .solve.runner import SolverError, SolverRunner

SECONDS = float(os.environ.get('DISPATCH_SECONDS', '3'))
TITLES = dict(insert='Минимум перестановок', optimize='Лучший план на остаток дня', keep='Ничего не менять')


class StraightLegs(RouteGeometry):
    """Линии из пакета; чего нет — прямой отрезок (сервер без OSRM)."""

    def leg(self, mode, a, b):
        return super().leg(mode, a, b) or [list(a), list(b)]


class Region:
    def __init__(self, folder):
        self.matrices = MatrixBuilder(cache_dir=folder, ext='.json.gz', timetable=False, readonly=True)
        profiles = ('car', 'foot', 'bike') + (('pt',) if os.path.exists(os.path.join(folder, 'matrix_pt.json.gz')) else ())
        self.matrices.client = NearbyEstimate({p: self.matrices._store(p) for p in profiles})
        self.geometry = StraightLegs(path=os.path.join(folder, 'geometry.json.gz'), online=False)
        self.houses = HouseIndex(os.path.join(folder, 'houses.json.gz'))

    def replanner(self, doc):
        geocoder = Geocoder(path=os.path.join(tempfile.gettempdir(), 'dispatch_geocode.json'), online=False)
        return Replanner(copy.deepcopy(doc), matrices=self.matrices, geocoder=geocoder, houses=self.houses,
                         geometry=self.geometry)


def create_app(site_dir):
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024    # документ плана с линиями — до нескольких МБ
    regions = {}
    solver = SolverRunner()

    def region(name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9-]+', name):
            raise InputError('участок: ожидаю латинское имя папки, например vostok')
        folder = os.path.join(site_dir, 'api', name)
        if not os.path.isdir(folder):
            raise InputError(f'нет данных участка «{name}» для пересчёта')
        if name not in regions:
            regions[name] = Region(folder)
        return regions[name]

    def event_of(e):
        kind = e.get('kind')
        if kind == 'urgent':
            if not all(isinstance(e.get(x), (int, float)) for x in ('lat', 'lon')):
                raise InputError('новая заявка: нужна точка lat, lon')
            return Urgent(f"{e['lat']:.6f}, {e['lon']:.6f}", e.get('type', 'авария'), e.get('window'), e.get('need'),
                          e.get('id'), e.get('district', ''))
        if kind == 'cancel':
            return Cancel(e['order'])
        if kind == 'unavailable':
            return Unavailable(e['brigade'])
        if kind == 'finish':
            return Finish(e['order'], float(e['end']))
        if kind == 'window':
            return Reschedule(e['order'], e['window'])
        if kind == 'breakdown':
            return Breakdown(e['brigade'], e.get('mode', 'pt'))
        raise InputError(f'неизвестное событие «{kind}»')

    def summary(doc):
        m, e = doc['plan']['metrics'], doc['event']
        return dict(brigades=m['brigades_used'], km=m['km_total'], unassigned=m['unassigned'], changes=len(e['changes']),
                    accident=e.get('accident'), turned=len(e.get('turned') or []), ok=doc['plan']['check']['ok'])

    @app.errorhandler(InputError)
    def bad_input(err):
        return jsonify(error=str(err)), 400

    @app.errorhandler(SolverError)
    def solver_failed(err):
        return jsonify(error=f'решатель: {err}'), 500

    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    @app.errorhandler(ValueError)
    def bad_fields(err):                           # поле не того вида внутри документа или события
        return jsonify(error=f'неверные данные запроса: {err}'), 400

    def body(*keys):
        """Тело запроса: JSON-объект с ключами keys, doc — документ плана, at — минуты от 10:00 в пределах смены."""
        b = request.get_json(silent=True)
        if not isinstance(b, dict):
            raise InputError('ожидаю JSON-объект')
        miss = [k for k in keys if k not in b]
        if miss:
            raise InputError(f"нет полей: {', '.join(miss)}")
        if 'doc' in keys and not (isinstance(b['doc'], dict) and isinstance(b['doc'].get('plan'), dict)
                                  and isinstance(b['doc'].get('orders'), list) and isinstance(b['doc'].get('brigades'), list)):
            raise InputError('doc — не документ плана')
        if 'at' in keys and not (isinstance(b['at'], (int, float)) and 0 <= b['at'] <= 720):
            raise InputError('at — минуты от 10:00, от 0 до 720')
        if 'event' in keys and not isinstance(b['event'], dict):
            raise InputError('event — объект')
        return b

    @app.post('/api/variants')
    def variants():
        b = body('region', 'doc', 'at', 'event')
        r, event = region(b['region']), event_of(b['event'])
        out = []
        with tempfile.TemporaryDirectory(prefix='dispatch_') as work:
            for strategy in ('insert', 'optimize', 'keep'):
                doc = r.replanner(b['doc']).apply(b['at'], event, strategy=strategy, solver=solver, seconds=SECONDS,
                                                  workdir=os.path.join(work, strategy))
                docs.fill_geometry(doc, r.geometry, log=lambda *_: None)
                out.append(dict(strategy=strategy, title=TITLES[strategy], summary=summary(doc), doc=doc))
        return jsonify(variants=out)

    @app.post('/api/options')
    def options():
        b = body('region', 'doc', 'at', 'order')
        return jsonify(options=region(b['region']).replanner(b['doc']).options(b['at'], b['order']))

    @app.post('/api/move')
    def move():
        b = body('region', 'doc', 'at', 'order', 'brigade')
        r = region(b['region'])
        doc = r.replanner(b['doc']).apply(b['at'], Move(b['order'], b['brigade']))
        docs.fill_geometry(doc, r.geometry, log=lambda *_: None)
        return jsonify(doc=doc)

    @app.get('/')
    def index():
        return send_from_directory(site_dir, 'index.html')

    @app.get('/<path:path>')
    def static_files(path):
        return send_from_directory(site_dir, path)

    return app
