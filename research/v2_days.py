"""Задачи из новых реальных дней (папка «Реальные дни 28-29.09») в модели продукта — те же 8 вариантов, что у
контрольного дня (instances_control_v2): транспорт (pt, car, mix1, mix2) × навыки (inf — как у бригады на контрольном
дне, all — все) → instances_days_v2/day_<участок>_<ддмм>_<транспорт>_<навыки>.txt.

- Отменённые заявки («Статус BK» = «Отменена») выкидываем (решение от 29.09).
- Реального распределения в новых днях нет, поэтому состав бригад, навыки и дома в области берём с контрольного дня
  участка (dispatch.tools.demo_data.roster); транспорт mix1/mix2 назначается по настоящему имени бригады, как в control_mix.py.
- Адрес офиса — из синтетики того же участка. «Только машина» — правило dispatch.tools.demo_data.needs_car; снимается, если заявку
  не может взять ни одна бригада задачи (как в v2.py / v2_new.py).
- Югоцентр — один день: «Югоцентр 28.09 повторная выгрузка.csv» — повтор того же дня, не используем.
Коэффициент пробок и надбавки — как в python -m dispatch plan по умолчанию."""
import copy, csv, io, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dispatch.config import TravelSettings
from dispatch.files.readers import read_orders
from dispatch.files.tables import read_table
from dispatch.geo.geocoder import Geocoder
from dispatch.geo.matrices import MatrixBuilder
from dispatch.model.domain import Brigade
from dispatch.model.problem import Problem
from dispatch.tools.demo_data import needs_car, roster
from dispatch.tools.sources import organizers_file

DAYS = os.path.join(ROOT, 'data', 'organizers', 'Реальные дни 28-29.09')
FILES = [('Восток', '28.09'), ('Восток', '29.09'), ('Юго-восток', '28.09'), ('Юго-восток', '29.09'), ('Югоцентр', '28.09')]
TR = {'Восток': 'vostok', 'Юго-восток': 'yugovostok', 'Югоцентр': 'yugocentr'}
OUT = os.environ.get('OUT', 'instances_days_v2')


def as_csv(rows):
    s = io.StringIO(); csv.writer(s, delimiter=';').writerows(rows); return s.getvalue()


def orders_of(region, day, tmp):
    rows = read_table(os.path.join(DAYS, f'{region} {day}.csv')); h = rows[0]
    st, bk, hd = h.index('Статус BK'), h.index('Тип заявки BK'), h.index('Тип заявки HD')
    synth = read_table(organizers_file(region, 'Синтетические'))
    office = next(r[1] for r in synth if r[0].lower().startswith('адрес'))
    keep = [r for r in rows[1:] if r[0] and r[st] != 'Отменена']
    out = [h + ['Транспорт']] + [r + [''] * (len(h) - len(r)) + ['авто' if needs_car(r[bk], r[hd]) else ''] for r in keep]
    out.append(['Адрес офиса', office])
    p = os.path.join(tmp, f'{TR[region]}_{day}.csv'); open(p, 'w', encoding='utf-8').write(as_csv(out))
    return read_orders(p), len(rows) - 1 - len(keep)


def brigades_of(region, tmode):
    return [Brigade(name=n, mode=m, skills=sk, start_address=start) for n, m, sk, start in roster(region, tmode)]


def main():
    os.makedirs(OUT, exist_ok=True); tmp = os.path.join(OUT, '_src'); os.makedirs(tmp, exist_ok=True)
    for region, day in FILES:
        inp, cancelled = orders_of(region, day, tmp); orders = inp.orders
        rosters = {tm: brigades_of(region, tm) for tm in ('pt', 'car', 'mix1', 'mix2')}
        homes = [b.start_address for b in rosters['pt'] if b.start_address]
        coords = Geocoder().locate([inp.office] + [o.address for o in orders] + homes, log=print)
        starts = [coords[inp.office]]
        for h in homes:
            if coords[h] not in starts: starts.append(coords[h])
        for o in orders: o.lat, o.lon = coords[o.address]
        mats = MatrixBuilder().build(starts + [(o.lat, o.lon) for o in orders], len(starts), TravelSettings())
        need = 0
        for tmode, base in rosters.items():
            for sv in ('inf', 'all'):
                brs = [Brigade(b.name, b.mode, b.skills if sv == 'inf' else [0, 1, 2], b.start_address,
                               starts.index(coords[b.start_address]) if b.start_address else 0) for b in base]
                os_ = copy.deepcopy(orders)
                for o in os_:
                    if o.need and not any(o.skill in b.skills and b.mode in o.need for b in brs): o.need = None
                name = f"day_{TR[region]}_{day.replace('.', '')}_{tmode}_{sv}"
                Problem(name, os_, brs, starts, mats).write(f'{OUT}/{name}.txt')
                need += sum(1 for o in os_ if o.need)
        print(f'{region} {day}: {len(orders)} заявок (отменённых выкинуто {cancelled}), {len(base)} бригад, стартов {len(starts)}, '
              f'8 задач, заявок «только машина» всего {need}')


if __name__ == '__main__':
    main()
