"""Входные файлы: заявки (формат организаторов) и бригады.

Заявки — колонки «Заявка; Тип заявки BK; Тип заявки HD; Начало; Окончание; Район; Адрес» и необязательные
«Транспорт» (допустимые виды: «авто», «авто или велосипед»; пусто или «любой» — без ограничений) и «Гигабитное
подключение» (Да/Нет). Номер квартиры берётся из адреса («…, кв. 71»). Строка «Адрес офиса; <адрес>» задаёт офис участка.
Бригады — «Бригада; Транспорт; Навыки; Старт» и необязательные «График», «Смена» и «Оборудование»; навыки через
запятую, старт пустой (офис), адрес или «широта, долгота». График 2/2 — смена 10:00–22:00; 5/2 — девятичасовая: «Смена»
(например «13-22») или по очереди 10–19 и 13–22; без графика — 10:00–22:00. Оборудование — выданный утром запас:
«роутер 4, приставка 2» (чего нет в списке — 0); пусто — запас подбирается под маршрут (Problem.stock_for). Сколько
бригада унесёт, задаёт транспорт (config.CARRY). Необязательные «Уровень» (стажёр, инженер, старший; можно junior,
middle, senior; пусто — инженер) и «Темп» (множитель длительности работ, например 0,9) задают темп бригады: заявку она
делает за длительность × темп; «Темп» важнее уровня (config.LEVELS).
"""
import re
from dataclasses import dataclass

from ..config import (CARRY, DAY_START, DEFAULT_LEVEL, EQUIPMENT, LEVEL_WORDS, LEVELS, PRIORITY, SERVICE, SHIFT, SKILL,
                      SPEED_LIMITS, TYPE_NAMES)

SHIFTS_5_2 = ((0, 540), (180, SHIFT))   # 10:00–19:00 и 13:00–22:00
from ..model.domain import Brigade, Order, apartment
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


def parse_window(start, end, where=''):
    """Окно заявки в минутах от 10:00 как в файле (может выходить за смену: у аварий 0:01–23:59, Order.earliest
    и Problem.latest обрезают его сменой); «00:00» в конце — полночь. Окно, которое кончается не позже начала или
    целиком вне смены 10:00–22:00, — ошибка."""
    a, b = parse_minutes(start, where), parse_minutes(end, where)
    if b == -DAY_START:
        b = 24 * 60 - DAY_START
    if b <= a:
        raise InputError(f'{where}: окно {start} — {end} кончается не позже начала')
    if b <= 0 or a >= SHIFT:
        raise InputError(f'{where}: окно {start} — {end} вне смены 10:00–22:00')
    return a, b


def parse_mode(text, where=''):
    t = text.lower().replace('ё', 'е').strip()
    if not t:
        return None
    for word, mode in _MODE_WORDS:
        if t.startswith(word):
            return mode
    raise InputError(f'{where}: неизвестный транспорт «{text}» (авто, общ. транспорт, велосипед, пешком)')


def parse_shift(text, where=''):
    """«13-22» или «13:00–22:00» → (минуты от 10:00, минуты от 10:00), не шире 10:00–22:00."""
    m = re.fullmatch(r'\s*(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*', text)
    if not m:
        raise InputError(f'{where}: не понял смену «{text}» (например 13-22)')
    a = int(m[1]) * 60 + int(m[2] or 0) - DAY_START
    b = int(m[3]) * 60 + int(m[4] or 0) - DAY_START
    if not 0 <= a < b <= SHIFT:
        raise InputError(f'{where}: смена «{text}» должна быть внутри 10:00–22:00')
    return a, b


_ITEM_WORDS = (('роут', 'router'), ('router', 'router'), ('прист', 'stb'), ('тв', 'stb'), ('stb', 'stb'))


def parse_stock(text, where=''):
    """«роутер 4, приставка 2» → {'router': 4, 'stb': 2}; пусто — None (запас не задан)."""
    t = text.lower().strip()
    if not t:
        return None
    out = {i: 0 for i in EQUIPMENT}
    for part in re.split(r'[,;]', t):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r'(\D+?)\s*[:\-—]?\s*(\d+)\s*(?:шт\.?)?', part)
        item = m and next((i for w, i in _ITEM_WORDS if m[1].strip().startswith(w)), None)
        if not item:
            raise InputError(f'{where}: не понял оборудование «{part}» (например «роутер 4, приставка 2»)')
        out[item] += int(m[2])
    return out


def parse_level(text, where=''):
    """«Стажёр», «junior», «старший инженер» … → стажёр / инженер / старший; пусто — ''."""
    t = text.lower().replace('ё', 'е').strip()
    if not t:
        return ''
    for word, level in LEVEL_WORDS:
        if t.startswith(word.replace('ё', 'е')):
            return level
    raise InputError(f'{where}: неизвестный уровень «{text}» (стажёр, инженер, старший)')


def parse_speed(text, where=''):
    """«0,9», «×1.15», «115%» → множитель длительности работ; пусто — None."""
    t = text.lower().replace(' ', '').replace(',', '.').lstrip('×x*')
    if not t:
        return None
    try:
        x = float(t[:-1]) / 100 if t.endswith('%') else float(t)
    except ValueError:
        raise InputError(f'{where}: не понял темп «{text}» (множитель длительности работ, например 0,9)')
    if not SPEED_LIMITS[0] <= x <= SPEED_LIMITS[1]:
        raise InputError(f'{where}: темп «{text}» вне пределов {SPEED_LIMITS[0]:g}–{SPEED_LIMITS[1]:g}')
    return x


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
                   dict(hd='Тип заявки HD', need='Транспорт', gigabit='Гигабит'))
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
        window = parse_window(r[col['start']], r[col['end']], where)
        date = date or next(iter(re.findall(r'\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}', r[col['start']])), None)
        orders.append(Order(id=r[col['id']], bk=bk, type=TYPE_NAMES[bk], window=window, service=SERVICE[bk],
                            priority=PRIORITY[bk], skill=SKILL[bk], district=r[col['district']], address=r[col['address']],
                            hd=r[col['hd']] if col['hd'] is not None else '',
                            need=parse_modes(r[col['need']], where) if col['need'] is not None else None,
                            apt=apartment(r[col['address']]),
                            gigabit=col['gigabit'] is not None and r[col['gigabit']].strip().lower().startswith('да')))
    if office is None:
        raise InputError(f'{path}: нет строки «Адрес офиса»')
    if not orders:
        raise InputError(f'{path}: нет заявок')
    return OrdersFile(orders, office, date)


def read_brigades(path, schedule=''):
    """schedule — график для бригад, у которых он не указан в файле."""
    default = schedule
    rows = read_table(path)
    col = _columns(rows[0], path, dict(name='Бригада', mode='Транспорт', skills='Навык'),
                   dict(start='Старт', schedule='График', shift='Смена', stock='Оборудование', level='Уровень', speed='Темп'))
    out, five_two = [], 0
    for n, r in enumerate(rows[1:], start=2):
        r = r + [''] * (len(rows[0]) - len(r))
        where = f'{path}, строка {n}'
        mode = parse_mode(r[col['mode']], where)
        if mode is None:
            raise InputError(f'{where}: не указан транспорт')
        schedule = (r[col['schedule']] if col['schedule'] is not None else '').strip().replace(' ', '') or default
        if schedule not in ('', '2/2', '5/2'):
            raise InputError(f'{where}: неизвестный график «{schedule}» (2/2 или 5/2)')
        text = (r[col['shift']] if col['shift'] is not None else '').strip()
        if text:
            shift = parse_shift(text, where)
        elif schedule == '5/2':
            shift, five_two = SHIFTS_5_2[five_two % 2], five_two + 1
        else:
            shift = (0, SHIFT)
        level = parse_level(r[col['level']], where) if col['level'] is not None else ''
        speed = parse_speed(r[col['speed']], where) if col['speed'] is not None else None
        if speed is None:
            speed = LEVELS[level or DEFAULT_LEVEL]
        out.append(Brigade(name=r[col['name']] or f'Бригада {n - 1}', mode=mode, skills=parse_skills(r[col['skills']], where),
                           start_address=r[col['start']] if col['start'] is not None else '', schedule=schedule, shift=shift,
                           carry=CARRY[mode], stock=parse_stock(r[col['stock']], where) if col['stock'] is not None else None,
                           level=level, speed=speed))
    if not out:
        raise InputError(f'{path}: нет бригад')
    return out

