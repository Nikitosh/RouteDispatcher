"""Ввод-вывод без сети и без C++: запуск решателей (solve/runner.py), геокодер и Nominatim (geo/geocoder.py),
клиент OSRM (geo/osrm.py), чтение plan.json (files/document.py), подписи событий страницы (site/builder.py).
Сеть — только подменённый в тесте urllib.request.urlopen."""
import io
import json
import subprocess
import urllib.error
import urllib.request

import pytest

from dispatch.files import document
from dispatch.geo import geocoder as geo
from dispatch.geo import osrm
from dispatch.geo.geocoder import GeocodeError, Geocoder
from dispatch.geo.osrm import OsrmClient, OsrmError
from dispatch.site.builder import event_label
from dispatch.solve import runner
from dispatch.solve.runner import SolverError, SolverRunner
from tests.helpers import brigade, line_problem, order


# ---- решатели
def small():
    return line_problem([order(1, 'Ремонт', x=1)], [brigade('Б1'), brigade('Б2')])


@pytest.fixture
def bins(tmp_path, monkeypatch):
    """Папка решателей с пустыми файлами и подмена subprocess.run: ответы по имени программы."""
    d = tmp_path / 'bin'
    d.mkdir()
    for name in ('plan', 'baseline', 'fleet_bound'):
        (d / name).write_text('')
    answers, calls = {}, []

    def run(cmd, **kw):
        name = cmd[0].rsplit('/', 1)[-1]
        calls.append((name, cmd[1:]))
        a = answers[name]
        if callable(a):
            a = a(cmd[1:])
        if isinstance(a, BaseException):
            raise a
        code, out = a if isinstance(a, tuple) else (0, a)
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr='сломалось')
    monkeypatch.setattr(runner.subprocess, 'run', run)
    return SolverRunner(str(d)), answers, calls


def test_missing_binary(tmp_path):
    with pytest.raises(SolverError, match='Нет .*plan. Соберите решатели'):
        SolverRunner(str(tmp_path)).plan_only(small(), workdir=str(tmp_path / 'w'))


def test_bad_answers(bins, tmp_path):
    r, answers, calls = bins
    answers['plan'] = 'Segmentation fault'
    with pytest.raises(SolverError, match='plan: ответ не JSON'):
        r.plan_only(small(), workdir=str(tmp_path))
    answers['plan'] = '[1, 2]'
    with pytest.raises(SolverError, match='не объект JSON'):
        r.plan_only(small(), workdir=str(tmp_path))
    answers['plan'] = '{"routes": [[0]]}'
    with pytest.raises(SolverError, match='должно быть 2 маршрутов'):
        r.plan_only(small(), workdir=str(tmp_path))
    answers['plan'] = (3, '')
    with pytest.raises(SolverError, match='plan: код 3\nсломалось'):
        r.plan_only(small(), workdir=str(tmp_path))
    answers['plan'] = '{"routes": [[0], []]}'
    assert r.plan_only(small(), 0.5, 7, str(tmp_path), maxv=2, wveh=0) == [[0], []]
    assert calls[-1][1][1:] == ['0.5', '7', 'maxv=2', 'wveh=0', f'wwait={runner.WAIT_KM_PER_MIN:g}']


def test_tries_keeps_best_plan_across_seeds(bins, tmp_path):
    """tries=3 — три запуска с сидами 5, 6, 7; лучший: сначала все заявки, потом меньше бригад, потом км."""
    r, answers, calls = bins
    plans = {'5': '{"routes": [[], []], "km": 1.0}',                  # заявка не выполнена
             '6': '{"routes": [[0], []], "km": 9.0, "wait": 10}',     # 9 + 0.1 × 10 = 10
             '7': '{"routes": [[0], []], "km": 9.5}'}                  # меньше км с учётом ожидания аварии
    answers['plan'] = lambda args: plans[args[2]]
    answers['baseline'] = '{"routes": [[0], []]}'
    ours, _, _ = r.solve(small(), 1, 5, str(tmp_path), bound=False, tries=3)
    assert [a[2] for n, a in calls if n == 'plan'] == ['5', '6', '7']
    km = lambda: json.load(open(tmp_path / 'answer.json'))['km']
    assert ours.routes == [[0], []] and km() == 9.5
    plans['7'] = '{"routes": [[0], []], "km": 1.0, "wait": 0}'
    r.solve(small(), 1, 5, str(tmp_path), bound=False, tries=3)
    assert km() == 1.0
    calls.clear()
    r.solve(small(), 1, 5, str(tmp_path), bound=False, tries=0)                      # хотя бы один запуск
    assert [a[2] for n, a in calls if n == 'plan'] == ['5'] and km() == 1.0


def _unreachable():
    return line_problem([order(1, 'Ремонт', window=(0, 0), x=50)], [brigade('Б1'), brigade('Б2')])


def test_previous_answer_competes_with_new_runs(bins, tmp_path):
    """answer — ответ прошлого прогона: берётся, если лучше новых запусков; с tries=0 решатель не запускается."""
    r, answers, calls = bins
    answers['plan'] = '{"routes": [[], []], "km": 1.0}'                       # новый запуск не выполнил заявку
    answers['baseline'] = '{"routes": [[0], []]}'
    old = '{"routes": [[0], []], "km": 5.0}'
    km = lambda: json.load(open(tmp_path / 'answer.json'))['km']
    assert r.solve(small(), 1, 1, str(tmp_path), bound=False, tries=2, answer=old)[0].routes == [[0], []] and km() == 5.0
    assert [a[2] for n, a in calls if n == 'plan'] == ['1', '2']
    calls.clear()
    assert r.solve(small(), 1, 1, str(tmp_path), bound=False, tries=0, answer=old)[0].routes == [[0], []]
    assert [n for n, _ in calls] == ['baseline']
    answers['plan'] = '{"routes": [[0], []], "km": 2.0}'                      # новый лучше старого
    r.solve(small(), 1, 1, str(tmp_path), bound=False, tries=1, answer=old)
    assert km() == 2.0
    with pytest.raises(SolverError, match='должно быть 2 маршрутов'):
        r.solve(small(), 1, 1, str(tmp_path), bound=False, tries=0, answer='{"routes": [[0]]}')
    late = '{"routes": [[0], []], "km": 0.1}'          # по новым данным заявку не успеть: старый ответ недопустим
    with pytest.raises(SolverError, match='недопустим'):
        r.solve(_unreachable(), 1, 1, str(tmp_path), bound=False, tries=0, answer=late)
    assert r.solve(_unreachable(), 1, 1, str(tmp_path), bound=False, tries=1, answer=late)[0].routes == [[0], []]
    assert km() == 2.0                                                        # взят новый запуск, старый пропущен


@pytest.mark.parametrize('bound', [subprocess.TimeoutExpired('fleet_bound', 10), (1, ''), 'not json'])
def test_fleet_bound_failure_gives_no_bound(bins, tmp_path, bound):
    r, answers, _ = bins
    answers.update(plan='{"routes": [[0], []]}', baseline='{"routes": [[], [0]]}', fleet_bound=bound)
    ours, base, task = r.solve(small(), workdir=str(tmp_path))
    assert (ours.routes, ours.lower_bound, base.routes) == ([[0], []], None, [[], [0]])
    assert json.loads((tmp_path / 'answer.json').read_text()) == {'routes': [[0], []]}


def test_fleet_bound_answer_and_no_bound(bins, tmp_path):
    r, answers, calls = bins
    answers.update(plan='{"routes": [[0], []]}', baseline='{"routes": [[0], []]}', fleet_bound='{"fleet_lower_bound": 1}')
    assert r.solve(small(), workdir=str(tmp_path))[0].lower_bound == 1
    assert calls[-1][0] == 'fleet_bound' and calls[-1][1][2:] == [str(runner.BOUND_SECONDS), str(runner.BOUND_NG)]
    calls.clear()
    assert r.solve(small(), workdir=str(tmp_path), bound=False)[0].lower_bound is None
    assert [c[0] for c in calls] == ['plan', 'baseline']


# ---- геокодер
class Reply:
    """Подмена urlopen: отдаёт JSON и запоминает адреса запросов."""

    def __init__(self, body=None, error=None):
        self.body, self.error, self.urls = body, error, []

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url if isinstance(req, urllib.request.Request) else req)
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.body).encode())


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(geo.time, 'sleep', lambda s: None)


def test_offline_geocoder_names_missing_addresses(tmp_path):
    g = Geocoder(path=str(tmp_path / 'g.json'), online=False)
    assert g.locate(['55.75, 37.62'], log=None) == {'55.75, 37.62': (55.75, 37.62)}
    with pytest.raises(GeocodeError, match='геокодинг выключен') as e:
        g.locate(['г.Город Москва, ул. Ленина, д. 1', '55.7;37.6'])
    assert 'ул. Ленина' in str(e.value) and '55.7' not in str(e.value)


def test_nominatim_success_is_cached(tmp_path, monkeypatch, no_sleep):
    reply = Reply([dict(lat='55.8', lon='37.7', display_name='Москва, улица Ленина, 1')])
    monkeypatch.setattr(urllib.request, 'urlopen', reply)
    path = str(tmp_path / 'g.json')
    addr = 'г.Город Москва, ул. Ленина, д. 1'
    assert Geocoder(path=path).locate([addr], log=lambda *_: None) == {addr: (55.8, 37.7)}
    assert len(reply.urls) == 1 and 'nominatim.openstreetmap.org' in reply.urls[0]
    assert 'q=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0%2C+%D1%83%D0%BB%D0%B8%D1%86%D0%B0' in reply.urls[0]   # «Москва, улица»
    off = Geocoder(path=path, online=False)                       # второй раз — из кэша, без сети
    assert off.locate([addr]) == {addr: (55.8, 37.7)} and off.store.get(addr)['level'] == 'nominatim'


@pytest.mark.parametrize('reply', [Reply([]), Reply(error=urllib.error.URLError('нет сети'))])
def test_nominatim_failure(tmp_path, monkeypatch, no_sleep, reply):
    monkeypatch.setattr(urllib.request, 'urlopen', reply)
    with pytest.raises(GeocodeError, match='Не нашёл координаты') as e:
        Geocoder(path=str(tmp_path / 'g.json')).locate(['Нигде, д. 0'], log=lambda *_: None)
    assert 'Нигде, д. 0' in str(e.value) and not (tmp_path / 'g.json').exists()


# ---- OSRM
URLS = {'car': 'http://car:1', 'bike': 'http://bike:2', 'foot': 'http://foot:3'}


def test_osrm_table_and_route(monkeypatch):
    monkeypatch.setattr(osrm.time, 'sleep', lambda s: None)
    a, b = (55.0, 37.0), (55.1, 37.2)
    reply = Reply(dict(code='Ok', durations=[[600, None], [120, 0]], distances=[[1500, None], [300, 0]]))
    monkeypatch.setattr(urllib.request, 'urlopen', reply)
    assert OsrmClient(URLS).table('car', [a, b], [b, a]) == [[(10.0, 1.5), None], [(2.0, 0.3), (0.0, 0.0)]]
    assert reply.urls[0].startswith('http://car:1/table/v1/driving/37.000000,55.000000;37.200000,55.100000?')
    assert 'sources=0;1&destinations=1;0' in reply.urls[0]
    reply.body = dict(code='Ok', routes=[dict(geometry=dict(coordinates=[[37.000001, 55.000004], [37.2, 55.1]]))])
    assert OsrmClient(URLS).route('foot', a, b) == [[55.0, 37.0], [55.1, 37.2]]
    assert reply.urls[-1].startswith('http://foot:3/route/v1/driving/37.0,55.0;37.2,55.1')
    assert osrm.profile_of('pt') == 'car' and osrm.profile_of('bike') == 'bike'


@pytest.mark.parametrize('reply, why', [
    (Reply(error=urllib.error.HTTPError('http://car:1/x', 503, 'Service Unavailable', {}, None)), '503'),
    (Reply(dict(code='NoSegment')), 'NoSegment')])
def test_osrm_errors_after_retries(monkeypatch, reply, why):
    sleeps = []
    monkeypatch.setattr(osrm.time, 'sleep', sleeps.append)
    monkeypatch.setattr(urllib.request, 'urlopen', reply)
    with pytest.raises(OsrmError, match=f'OSRM недоступен \\(car:1\\): .*{why}'):
        OsrmClient(URLS, retries=2).table('car', [(55.0, 37.0)], [(55.1, 37.1)])
    assert len(reply.urls) == 2 and sleeps == [1, 3]


# ---- plan.json и подписи событий
def test_document_load_file_or_folder(tmp_path):
    doc = dict(meta=dict(name='Восток'), orders=[dict(id='7', addr='ул. Ленина')])
    path = document.save(doc, str(tmp_path / 'out'))
    assert document.load(path) == doc and document.load(str(tmp_path / 'out')) == doc
    other = tmp_path / 'другой.json'
    other.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    assert document.load(str(other)) == doc


ORDERS = [dict(id='101'), dict(id='102')]


@pytest.mark.parametrize('event, label', [
    (dict(kind='urgent', at=60, order=1, type='Авария'), '11:00 — новая заявка: авария'),
    (dict(kind='finish', at=300, order=0, end=310, delta=-15), '15:00 — заявка 101 закончится в 15:10 (-15 мин)'),
    (dict(kind='window', at=120, order=1, now=[300, 420]), '12:00 — клиент перенёс окно заявки 102 на 15:00–17:00'),
    (dict(kind='breakdown', at=90, name='Бригада 2'), '11:30 — Бригада 2: сломалась машина'),
    (dict(kind='cancel', at=0, order=1), '10:00 — отмена заявки 102')])
def test_event_labels(event, label):
    assert event_label(dict(event=event, orders=ORDERS)) == label


def test_event_label_for_manual_move():
    label = event_label(dict(event=dict(kind='move', at=60, order=0, brigade=1, name='Бригада 2'), orders=ORDERS))
    assert 'новая заявка' not in label and '101' in label and 'Бригада 2' in label
