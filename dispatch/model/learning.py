"""План учится на выездах: фактическая длительность работ уточняет коэффициенты сложности (model/complexity.py).

Двигаем множители: подтип работы (ключ — тип BK и начало подтипа HD, как в WORK_FACTOR; подтип без своего множителя —
тип BK целиком), тип дома (старый до 1960 года, новый с 2015-го) и личный темп бригады (Brigade.speed). Добавки в
минутах — гигабит, подъём без лифта, пропуск — не трогаем: они мелкие, и по длительности всей работы их не отделить от
множителя.

Каждый выезд даёт отношение r = факт / прогноз (прогноз — длительность по сложности × темп бригады). Для группы выездов
новый множитель = прежний × exp(Σ ln r / (n + PRIOR)): пока выездов мало, множитель почти не двигается — PRIOR условных
выездов «как сейчас». Сначала подтипы работ, затем дом по остаткам после поправки подтипов, затем темп каждой бригады по
остаткам после того и другого; начальный темп — по уровню бригады (config.LEVELS) или заданный в файле бригад. Итог — в
пределах LIMITS (темп — SPEED_LIMITS).

Факты: CSV истории выездов («Заявка; Тип заявки BK; Тип заявки HD; Адрес; Гигабит; План мин; Факт мин» и
необязательные «Участок», «Бригада») и события «закончили не по плану» (replan --finish): их факт — конец минус начало
работы. Бригада — по имени внутри участка (имена вроде «Бригада 3» повторяются на разных участках): ключ темпа —
«Участок: Бригада» (brigade_key), без участка — просто имя.
"""
import csv
import math
from collections import defaultdict
from dataclasses import dataclass

from ..config import NEW_HOUSE, OLD_HOUSE, PRIORITY, SERVICE, SKILL, SPEED_LIMITS, TYPE_NAMES, WORK_FACTOR
from .domain import Order, apartment

PRIOR = 8
LIMITS = (0.5, 2.0)


@dataclass
class Fact:
    order: Order
    actual: float                    # фактическая длительность работы, мин
    brigade: str = ''                # кто выполнял (имя бригады); пусто — неизвестно
    region: str = ''                 # участок бригады; пусто — неизвестен

    @property
    def key(self):
        return brigade_key(self.region, self.brigade) if self.brigade else None


def brigade_key(region, name):
    """Ключ темпа бригады: «Участок: Бригада»; без участка — имя."""
    return f'{region}: {name}' if region else name


def current():
    """Нынешние множители: {'work': {(BK, подтип): множитель}, 'old': …, 'new': …, 'brigades': {ключ: темп}}."""
    return dict(work=dict(WORK_FACTOR), old=OLD_HOUSE[1], new=NEW_HOUSE[1], brigades={})


def key_of(bk, hd):
    """Ключ подтипа работы: самое длинное совпавшее начало подтипа из WORK_FACTOR, иначе (BK, '')."""
    best = max((pre for b, pre in WORK_FACTOR if b == bk and (hd or '').startswith(pre)), key=len, default='')
    return bk, best


def _house_group(house):
    year = (house or {}).get('year')
    if year and year < OLD_HOUSE[0]:
        return 'old'
    if year and year >= NEW_HOUSE[0]:
        return 'new'
    return None


def fit(facts, houses, factors=None, speeds=None):
    """(новые множители, строки отчёта [{what, was, now, n}]) по фактам; houses — HouseIndex или dict. speeds —
    начальный темп бригад {ключ brigade_key: темп} (по уровню из файла бригад); кого нет — прежний из factors или 1.
    У строк темпа бригады ещё brigade и region."""
    from .complexity import estimate
    was = factors or current()
    speed0 = {**was.get('brigades', {}), **(speeds or {})}
    pace = lambda f: speed0.get(f.key, 1.0) if f.key else 1.0
    now = dict(work=dict(was['work']), old=was['old'], new=was['new'], brigades=dict(speed0))
    logs = defaultdict(list)
    for f in facts:
        pred = estimate(f.order, houses.get(f.order.house), was)[0] * pace(f)
        logs[key_of(f.order.bk, f.order.hd)].append(math.log(f.actual / pred))
    clip = lambda x: min(max(x, LIMITS[0]), LIMITS[1])
    rows = []
    for key, xs in sorted(logs.items()):
        old = was['work'].get(key, 1.0)
        now['work'][key] = round(clip(old * math.exp(sum(xs) / (len(xs) + PRIOR))), 3)
        kind = TYPE_NAMES.get(key[0], key[0])
        rows.append(dict(what=f'{kind}: {key[1]}' if key[1] else kind, bk=key[0], was=old, now=now['work'][key], n=len(xs)))
    logs = defaultdict(list)                       # дом — по остаткам после новых множителей подтипов
    for f in facts:
        g = _house_group(houses.get(f.order.house))
        if g:
            logs[g].append(math.log(f.actual / (estimate(f.order, houses.get(f.order.house), now)[0] * pace(f))))
    for g, xs in sorted(logs.items()):
        old = was[g]
        now[g] = round(clip(old * math.exp(sum(xs) / (len(xs) + PRIOR))), 3)
        rows.append(dict(what='старый дом' if g == 'old' else 'новый дом', bk='', was=old, now=now[g], n=len(xs)))
    logs, who = defaultdict(list), {}               # темп бригады — по остаткам после подтипов и дома
    for f in facts:
        if f.key:
            logs[f.key].append(math.log(f.actual / (estimate(f.order, houses.get(f.order.house), now)[0] * pace(f))))
            who[f.key] = (f.region, f.brigade)
    lo, hi = SPEED_LIMITS
    for key, xs in sorted(logs.items(), key=lambda x: (who[x[0]][0], _natural(who[x[0]][1]))):
        old = speed0.get(key, 1.0)
        now['brigades'][key] = round(min(max(old * math.exp(sum(xs) / (len(xs) + PRIOR)), lo), hi), 3)
        region, name = who[key]
        rows.append(dict(what=f'{name}: темп', bk='', was=old, now=now['brigades'][key], n=len(xs), brigade=name, region=region))
    return now, rows


def _natural(name):
    """«Бригада 10» после «Бригада 9»."""
    import re
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', name)]


def fit_for(facts, houses, brigades, region, factors=None):
    """Обучение для плана участка region: начальный темп бригад — их нынешний Brigade.speed (по уровню или «Темп»),
    выученный темп проставляется бригадам (по имени внутри участка; факты без участка — по имени). Возвращает (множители
    для complexity.apply, строки отчёта: подтипы, дом и темп только бригад этого участка)."""
    speeds = {brigade_key(region, b.name): b.speed for b in brigades}
    speeds.update({b.name: b.speed for b in brigades if b.name not in speeds})
    now, rows = fit(facts, houses, factors, speeds)
    mine = []
    for b in brigades:
        for key in (brigade_key(region, b.name), b.name):
            row = next((r for r in rows if 'brigade' in r and brigade_key(r['region'], r['brigade']) == key), None)
            if row:
                b.speed = now['brigades'][key]
                mine.append(row)
                break
    return now, [r for r in rows if 'brigade' not in r] + mine


def mae(facts, houses, factors):
    """Средняя ошибка прогноза длительности (по сложности × темп бригады), мин."""
    from .complexity import estimate
    speed = factors.get('brigades', {})
    return sum(abs(f.actual - estimate(f.order, houses.get(f.order.house), factors)[0] * speed.get(f.key, 1.0))
               for f in facts) / max(1, len(facts))


def make_order(oid, bk, hd, address, gigabit=False):
    return Order(id=str(oid), bk=bk, type=TYPE_NAMES[bk], window=(0, 0), service=SERVICE[bk], priority=PRIORITY[bk],
                 skill=SKILL[bk], address=address, hd=hd, apt=apartment(address), gigabit=gigabit)


COLUMNS = ['Дата', 'Участок', 'Бригада', 'Заявка', 'Тип заявки BK', 'Тип заявки HD', 'Адрес', 'Гигабит', 'План мин', 'Факт мин']


def read(path):
    """Факты из CSV истории выездов; «Участок» и «Бригада» необязательны. Строки без положительной фактической
    длительности (пусто, 0, не число) пропускаются."""
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f, delimiter=';'))

    def minutes(r):
        try:
            return float(str(r.get('Факт мин') or '').replace(',', '.'))
        except ValueError:
            return 0.0
    rows = [r for r in rows if minutes(r) > 0]
    return [Fact(make_order(r['Заявка'], r['Тип заявки BK'], r['Тип заявки HD'], r['Адрес'], r['Гигабит'] == 'Да'),
                 minutes(r), (r.get('Бригада') or '').strip(), (r.get('Участок') or '').strip()) for r in rows], rows


def write(path, rows):
    cols = COLUMNS if any(r.get('Бригада') or r.get('Участок') for r in rows) else [c for c in COLUMNS if c not in ('Участок', 'Бригада')]
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, cols, delimiter=';')
        w.writeheader()
        w.writerows(rows)


def from_events(docs):
    """Факты из событий «закончили не по плану»: длительность = фактический конец − начало работы; бригада — та, что
    выполняла заявку по плану, участок — из meta."""
    out = []
    for d in docs:
        e = d.get('event') or {}
        if e.get('kind') != 'finish':
            continue
        o = d['orders'][e['order']]
        v, brigades = o['plan'].get('brigade'), d.get('brigades') or []
        name = brigades[v]['name'] if v is not None and v < len(brigades) else ''
        out.append(Fact(make_order(o['id'], o['bk'], o['hd'], o['addr'], o.get('gigabit', False)),
                        e['end'] - o['plan']['start'], name, (d.get('meta') or {}).get('name', '') if name else ''))
    return out
