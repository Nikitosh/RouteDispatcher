"""Клиент Яндекс Расписаний: ответы сохраняются и не запрашиваются повторно, счётчик и лимит запросов в день, ключ из
окружения или .env. Сеть подменена (conftest.py запрещает настоящие запросы)."""
import io
import json

import pytest

from dispatch.tools import rasp


class FakeUrl:
    """Подмена urllib.request.urlopen: отдаёт ответ по методу API и запоминает адреса."""

    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        method = url.split('/v3.0/')[1].split('/')[0]
        body = self.answers[method]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def net(monkeypatch):
    def install(answers):
        fake = FakeUrl(answers)
        monkeypatch.setattr(rasp.urllib.request, 'urlopen', fake)
        return fake
    monkeypatch.setenv('YANDEX_RASP_KEY', 'тестовый-ключ')
    return install


def test_cache_and_counter(tmp_path, net):
    fake = net({'search': {'segments': [1, 2]}})
    r = rasp.Rasp(folder=str(tmp_path), log=lambda *_: None)
    a = r.get('search', **{'from': 's1', 'to': 's2'})
    b = rasp.Rasp(folder=str(tmp_path), log=lambda *_: None).get('search', **{'to': 's2', 'from': 's1'})
    assert a == b and a['response'] == {'segments': [1, 2]}
    assert len(fake.urls) == 1 and fake.urls[0].endswith('&apikey=тестовый-ключ')
    assert r._count() == 1                                           # второй раз — из сохранённого ответа
    assert 'тестовый' not in json.dumps(json.load(open(next(tmp_path.glob('search_*.json')))))   # ключ не сохраняется


def test_daily_limit_and_failed_request_counts(tmp_path, net):
    net({'search': OSError('нет сети'), 'thread': {'stops': []}})
    r = rasp.Rasp(folder=str(tmp_path), limit=2, log=lambda *_: None)
    with pytest.raises(OSError):
        r.get('search', **{'from': 'a'})
    assert r._count() == 1                                           # упавший запрос тоже тратит квоту
    r.get('thread', uid='u')
    with pytest.raises(RuntimeError, match='лимит 2'):
        r.get('thread', uid='v')


def test_offline_never_requests(tmp_path, net):
    fake = net({})
    r = rasp.Rasp(folder=str(tmp_path / 'нет'), offline=True)
    assert r.get('search', **{'from': 'a'}) is None and not fake.urls
    assert rasp.search(r, 'a', 'b') == {} and rasp.thread(r, 'u') is None and rasp.stations(r) == {}
    assert not (tmp_path / 'нет').exists()


def test_key_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv('YANDEX_RASP_KEY', raising=False)
    monkeypatch.setattr(rasp, 'ROOT', str(tmp_path))
    with pytest.raises(RuntimeError, match='нет ключа'):
        rasp.api_key()
    (tmp_path / '.env').write_text('OTHER=1\nYANDEX_RASP_KEY= abc \n')
    assert rasp.api_key() == 'abc'


def test_stations_of_moscow_region(tmp_path, net):
    net({'stations_list': {'countries': [{'title': 'Россия', 'regions': [
        {'title': 'Москва и Московская область', 'settlements': [{'stations': [
            {'title': 'Кашира', 'transport_type': 'train', 'codes': {'yandex_code': 's1'}, 'latitude': 54.8, 'longitude': 38.2},
            {'title': 'Автостанция', 'transport_type': 'bus', 'codes': {'yandex_code': 's2'}, 'latitude': 54.8, 'longitude': 38.2},
            {'title': 'Без координат', 'transport_type': 'train', 'codes': {'yandex_code': 's3'}, 'latitude': ''}]}]},
        {'title': 'Тульская область', 'settlements': []}]}]}})
    assert rasp.stations(rasp.Rasp(folder=str(tmp_path), log=lambda *_: None)) == {'s1': (54.8, 38.2, 'Кашира')}


def test_fetch_suburban_asks_each_part_both_ways_and_one_thread(tmp_path, monkeypatch):
    calls = []

    class FakeRasp:
        def _count(self):
            return len(calls)

        def get(self, method, **p):
            calls.append((method, p.get('from'), p.get('to'), p.get('uid')))
            if method == 'search':
                segs = [dict(thread=dict(uid='express'), stops='Домодедово'), dict(thread=dict(uid='local'), stops='везде')]
                return dict(response=dict(segments=segs))
            return dict(response=dict(stops=[]))
    monkeypatch.setattr(rasp, 'SUBURBAN', {'Тест': [('a', 'b'), ('b', 'c')]})
    monkeypatch.setattr(rasp, 'MCD', {'МЦД-9': ('m', 'n')})
    assert rasp.fetch_suburban(FakeRasp()) == 8
    assert [c[:3] for c in calls[:7]] == [('stations_list', None, None), ('search', 'm', 'n'), ('search', 'n', 'm'),
                                          ('search', 'a', 'b'), ('search', 'b', 'a'), ('search', 'b', 'c'), ('search', 'c', 'b')]
    assert calls[7] == ('thread', None, None, 'local')                # поезд, который везде останавливается
