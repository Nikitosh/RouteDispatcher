"""Клиент API Яндекс Расписаний (https://yandex.ru/dev/rasp/) для расписаний МЦК и МЦД.

Бесплатный ключ даёт 500 запросов в сутки, поэтому:
- каждый ответ сохраняется в data/transit/yandex/ и больше не запрашивается;
- счётчик запросов за день лежит там же, после LIMIT запросов клиент отказывается работать.
Ключ — переменная окружения YANDEX_RASP_KEY или строка YANDEX_RASP_KEY=... в .env в корне проекта (не в git).
Данные предоставлены сервисом Яндекс Расписания.
"""
import datetime
import hashlib
import json
import os
import urllib.parse
import urllib.request

from ..config import ROOT

DIR = os.path.join(ROOT, 'data', 'transit', 'yandex')
API = 'https://api.rasp.yandex.net/v3.0/'
LIMIT = 40                       # запросов в день с этой машины — с большим запасом от 500


def api_key():
    key = os.environ.get('YANDEX_RASP_KEY')
    path = os.path.join(ROOT, '.env')
    if not key and os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            if line.startswith('YANDEX_RASP_KEY='):
                key = line.split('=', 1)[1].strip()
    if not key:
        raise RuntimeError('нет ключа: YANDEX_RASP_KEY в окружении или в .env')
    return key


class Rasp:
    def __init__(self, folder=None, limit=LIMIT, log=print, offline=False):
        """offline — только сохранённые ответы: чего нет, того нет (None), запросов не делаем."""
        self.folder, self.limit, self.log, self.offline = folder or DIR, limit, log, offline

    def _count(self, add=0):
        path = os.path.join(self.folder, 'requests.json')
        data = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
        today = datetime.date.today().isoformat()
        data[today] = data.get(today, 0) + add
        if add:
            json.dump(data, open(path, 'w', encoding='utf-8'), indent=1)
        return data[today]

    def get(self, method, **params):
        """Ответ метода API; из кэша, если такой запрос уже был."""
        query = urllib.parse.urlencode(sorted(params.items()))
        name = f"{method}_{hashlib.sha1(query.encode()).hexdigest()[:12]}.json"
        path = os.path.join(self.folder, name)
        if os.path.exists(path):
            return json.load(open(path, encoding='utf-8'))
        if self.offline:
            return None
        used = self._count()
        if used >= self.limit:
            raise RuntimeError(f'сегодня уже {used} запросов к Яндекс Расписаниям, лимит {self.limit}')
        os.makedirs(self.folder, exist_ok=True)
        self._count(1)                                   # считаем до запроса: упавший запрос тоже тратит квоту
        url = f"{API}{method}/?{query}&apikey={api_key()}"
        self.log(f'Яндекс Расписания: {method} {query} (запрос {used + 1} за сегодня)')
        with urllib.request.urlopen(url, timeout=120) as r:
            data = json.loads(r.read().decode('utf-8'))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(dict(method=method, params=params, response=data), f, ensure_ascii=False)
        return dict(method=method, params=params, response=data)


# Электрички вне МЦД: (направление, участки «станция — станция» по коду Яндекса). На каждом участке — поиск поездов в обе
# стороны; плюс один запрос thread — порядок станций и время хода поезда, который останавливается везде.
SUBURBAN = {
    'Павелецкое': [('s2000005', 's9600811'), ('s9600811', 's9601202'), ('s9601202', 's9600886')],  # вокзал — Домодедово — Ступино — Кашира
    'Ярославское': [('s2000002', 's9600681')],                                                   # вокзал — Мытищи
    'Казанское': [('s2000003', 's9601636')],                                                     # вокзал — Люберцы 1
    'Горьковское': [('s2000001', 's9601675')],                                                   # Курский вокзал — Железнодорожная
    'Курское': [('s2000001', 's9600731')],                                                       # Курский вокзал — Подольск
}
DATE = '2026-10-20'                      # будний день, на который берём расписание
# МЦД: конечные (код Яндекса); поезда — поиском между ними в обе стороны, остановки — по схеме Мосметро
MCD = {'МЦД-1': ('s9600781', 's9600721'), 'МЦД-2': ('s9601122', 's9600731'),       # Лобня — Одинцово, Нахабино — Подольск
       'МЦД-3': ('s9601197', 's9600212'), 'МЦД-4': ('s9601102', 's9601675')}       # Ипподром — Зеленоград-Крюково, Апрелевка — Железнодорожная


def search(r, a, b, date=DATE):
    got = r.get('search', **{'from': a, 'to': b, 'date': date, 'transport_types': 'suburban', 'limit': 500,
                             'lang': 'ru_RU', 'format': 'json'})
    return got['response'] if got else {}


def thread(r, uid, date=DATE):
    got = r.get('thread', uid=uid, date=date, lang='ru_RU', format='json')
    return got['response'] if got else None


def stations(r):
    """{код Яндекса: (широта, долгота, название)} железнодорожных станций Москвы и области."""
    got = r.get('stations_list', lang='ru_RU', format='json')
    if not got:
        return {}
    out = {}
    for c in got['response']['countries']:
        for reg in c.get('regions', []):
            if reg.get('title') != 'Москва и Московская область':
                continue
            for x in reg['settlements']:
                for s in x['stations']:
                    code = s['codes'].get('yandex_code')
                    if s['transport_type'] == 'train' and code and s.get('latitude'):
                        out[code] = (s['latitude'], s['longitude'], s['title'])
    return out


def fetch_suburban(r=None, date=DATE, log=print):
    """Список станций, поезда МЦД (MCD) и электрички (SUBURBAN); уже сохранённые ответы не запрашиваются.
    Возвращает число новых запросов."""
    r = r or Rasp(log=log)
    before = r._count()
    r.get('stations_list', lang='ru_RU', format='json')
    for a, b in MCD.values():
        search(r, a, b, date)
        search(r, b, a, date)
    for name, parts in SUBURBAN.items():
        found = {}
        for a, b in parts:
            for f, t in ((a, b), (b, a)):
                for s in search(r, f, t, date).get('segments', []):
                    if (f, t) == (a, b):
                        found.setdefault(s['thread']['uid'], []).append(s['stops'] == 'везде')
        # поезд, который останавливается везде на всех участках (или на большинстве) — его остановки и время хода
        uid = max(found, key=lambda u: (len(found[u]), sum(found[u])), default=None)
        if uid:
            thread(r, uid, date)
    return r._count() - before
