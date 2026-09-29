"""Входные файлы: заявки (формат организаторов) и бригады.

Заявки — колонки «Заявка; Тип заявки BK; Тип заявки HD; Начало; Окончание; Район; Адрес» и необязательная
«Транспорт» (допустимые виды: «авто», «авто или велосипед»; пусто или «любой» — без ограничений). Строка
«Адрес офиса; <адрес>» задаёт офис участка.
Бригады — «Бригада; Транспорт; Навыки; Старт»; навыки через запятую, старт пустой (офис), адрес или «широта, долгота».
"""
import re
from dataclasses import dataclass

from ..config import DAY_START, PRIORITY, SERVICE, SKILL, TYPE_NAMES
from ..model.domain import Brigade, Order
from .tables import read_table


class InputError(Exception):
    pass


_MODE_WORDS = (('авто', 'car'), ('маш', 'car'), ('car', 'car'), ('общ', 'pt'), ('от', 'pt'), ('pt', 'pt'), ('метро', 'pt'),
               ('вел', 'bike'), ('bike', 'bike'), ('пеш', 'foot'), ('foot', 'foot'))
# навык: (начала слов, однобуквенное обозначение)
_SKILL_WORDS = {0: (('рем', 'лок'), 'l'), 1: (('подкл', 'доз'), 'c'), 2: (('авар', 'глоб'), 'a')}


def parse_minutes(text, where=''):
    """«17.08.2026 12:00» или «12:00» → минуты от 10:00."""
    m = re.search(r'(\d{1,2}):(\d{2})', text)
    if not m:
        raise InputError(f'{where}: не понял время «{text}»')
    return int(m[1]) * 60 + int(m[2]) - DAY_START


def parse_mode(text, where=''):
    t = text.lower().replace('ё', 'е').strip()
    if not t:
        return None
    for word, mode in _MODE_WORDS:
        if t.startswith(word):
            return mode
    raise InputError(f'{where}: неизвестный транспорт «{text}» (авто, общ. транспорт, велосипед, пешком)')


def parse_modes(text, where=''):
    """Допустимые виды транспорта заявки: frozenset или None (любой)."""
    t = text.lower().strip()
    if not t or t.startswith('люб'):
        return None
    return frozenset(parse_mode(x, where) for x in re.split(r'[,;/]|\s+или\s+', t) if x.strip())


def parse_skills(text, where=''):
    found = set()
    for token in re.split(r'[,;/+]+|\s+и\s+', text.lower()):
        token = token.strip()
        if not token:
            continue
        skill = next((s for s, (prefixes, letter) in _SKILL_WORDS.items() if token == letter or token.startswith(prefixes)), None)
        if skill is None:
            raise InputError(f'{where}: неизвестный навык «{token}» (ремонт, подключение, аварии)')
        found.add(skill)
    if not found:
        raise InputError(f'{where}: у бригады нет навыков')
    return sorted(found)


def _columns(header, path, names, optional=()):
    low = [c.lower() for c in header]
    found = {}
    for key, title in {**names, **optional}.items():
        idx = next((i for i, c in enumerate(low) if c.startswith(title.lower())), None)
        if idx is None and key in names:
            raise InputError(f'{path}: нет колонки «{title}»')
        found[key] = idx
    return found


@dataclass
class OrdersFile:
    orders: list
    office: str
    date: str = None


def read_orders(path):
    rows = read_table(path)
    head = next((i for i, r in enumerate(rows) if r[0].lower() == 'заявка'), None)
    if head is None:
        raise InputError(f'{path}: нет строки заголовка, начинающейся с «Заявка»')
    col = _columns(rows[head], path,
                   dict(id='Заявка', bk='Тип заявки BK', start='Начало', end='Окончание', district='Район', address='Адрес'),
                   dict(hd='Тип заявки HD', need='Транспорт'))
    office, orders, date = None, [], None
    for n, r in enumerate(rows[head + 1:], start=head + 2):
        r = r + [''] * (len(rows[head]) - len(r))
        if r[0].lower().startswith('адрес'):
            office = r[1] or r[col['address']]
            continue
        where = f'{path}, строка {n}'
        bk = r[col['bk']]
        if bk not in SERVICE:
            raise InputError(f'{where}: неизвестный тип заявки «{bk}»')
        window = (parse_minutes(r[col['start']], where), parse_minutes(r[col['end']], where))
        date = date or next(iter(re.findall(r'\d{2}\.\d{2}\.\d{4}', r[col['start']])), None)
        orders.append(Order(id=r[col['id']], bk=bk, type=TYPE_NAMES[bk], window=window, service=SERVICE[bk],
                            priority=PRIORITY[bk], skill=SKILL[bk], district=r[col['district']], address=r[col['address']],
                            hd=r[col['hd']] if col['hd'] is not None else '',
                            need=parse_modes(r[col['need']], where) if col['need'] is not None else None))
    if office is None:
        raise InputError(f'{path}: нет строки «Адрес офиса»')
    if not orders:
        raise InputError(f'{path}: нет заявок')
    return OrdersFile(orders, office, date)


def read_brigades(path):
    rows = read_table(path)
    col = _columns(rows[0], path, dict(name='Бригада', mode='Транспорт', skills='Навык'), dict(start='Старт'))
    out = []
    for n, r in enumerate(rows[1:], start=2):
        r = r + [''] * (len(rows[0]) - len(r))
        where = f'{path}, строка {n}'
        mode = parse_mode(r[col['mode']], where)
        if mode is None:
            raise InputError(f'{where}: не указан транспорт')
        out.append(Brigade(name=r[col['name']] or f'Бригада {n - 1}', mode=mode, skills=parse_skills(r[col['skills']], where),
                           start_address=r[col['start']] if col['start'] is not None else ''))
    if not out:
        raise InputError(f'{path}: нет бригад')
    return out

