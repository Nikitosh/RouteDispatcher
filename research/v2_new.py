"""Задачи для новых участков (Север, Запад, Северо-восток; python -m dispatch gen-district) в модели продукта — те же варианты, что у
старых участков:
- instances_newctl_v2: 3 участка × транспорт (pt, car, mix1, mix2) × навыки (inf — как в файле бригад, all — все) = 24;
- instances_newroad_v2: 3 участка × 10 случайных составов бригад (как solve2.roster: 50% ОТ, 25% машина, 15% велосипед,
  остальные пешком, 1–3 навыка, каждый навык хотя бы у трёх бригад) = 30.
Задачи пишет dispatch.model.problem.Problem (коэффициент пробок и надбавки как в python -m dispatch plan по умолчанию). Требование «только машина»
снимается с заявки, если её не может взять ни одна бригада задачи (как в v2.py)."""
import copy, os, random, sys, zlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dispatch.config import TravelSettings
from dispatch.files.readers import read_brigades, read_orders
from dispatch.geo.geocoder import Geocoder
from dispatch.geo.matrices import MatrixBuilder
from dispatch.model.domain import Brigade
from dispatch.model.problem import Problem
from dispatch.tools.demo_data import mode_of

REG = {'Север': 'sever', 'Запад': 'zapad', 'Северо-восток': 'severovostok'}
BASE = os.environ.get('OUT_BASE', '')          # префикс папок вывода (для сверки)


def roster(n, seed):
    rnd = random.Random(seed)
    modes = ['pt'] * round(n * 0.5) + ['car'] * round(n * 0.25) + ['bike'] * round(n * 0.15)
    modes += ['foot'] * (n - len(modes)); rnd.shuffle(modes); out = []
    for v in range(n):
        k = rnd.choices([1, 2, 3], [0.3, 0.3, 0.4])[0]
        out.append(Brigade(name=f'Бригада {v + 1}', mode=modes[v], skills=sorted(rnd.sample([0, 1, 2], k)), start=0))
    for s in range(3):
        have = [b for b in out if s in b.skills]
        for b in out:
            if len(have) >= 3: break
            if s not in b.skills: b.skills = sorted(b.skills + [s]); have.append(b)
    return out


def build(mats, starts, orders, brs, name, path):
    orders = copy.deepcopy(orders)
    for o in orders:
        if o.need and not any(o.skill in b.skills and b.mode in o.need for b in brs): o.need = None
    Problem(name, orders, brs, starts, mats).write(path)
    return sum(1 for o in orders if o.need)


def main():
    for d in ('instances_newctl_v2', 'instances_newroad_v2'): os.makedirs(BASE + d, exist_ok=True)
    for region, tr in REG.items():
        inp = read_orders(os.path.join(ROOT, 'data', 'demo', f'{region} заявки.xlsx')); orders = inp.orders
        base = read_brigades(os.path.join(ROOT, 'data', 'demo', f'{region} бригады.xlsx'))
        coords = Geocoder().locate([inp.office] + [o.address for o in orders], log=lambda *_: None)
        starts = [coords[inp.office]]
        for o in orders: o.lat, o.lon = coords[o.address]
        mats = MatrixBuilder().build(starts + [(o.lat, o.lon) for o in orders], 1, TravelSettings())
        need = 0
        for tmode in ('pt', 'car', 'mix1', 'mix2'):
            for sv in ('inf', 'all'):
                brs = [Brigade(b.name, mode_of(f'{region} {b.name}', False, tmode), b.skills if sv == 'inf' else [0, 1, 2]) for b in base]
                name = f'ctl_{tr}_{tmode}_{sv}'
                need += build(mats, starts, orders, brs, name, f'{BASE}instances_newctl_v2/{name}.txt')
        for s in range(10):
            name = f'{tr}_s{s}'
            need += build(mats, starts, orders, roster(len(base), zlib.crc32(f'{region}|{s}'.encode())), name, f'{BASE}instances_newroad_v2/{name}.txt')
        print(f'{region}: {len(orders)} заявок, {len(base)} бригад, 18 задач, заявок «только машина» всего {need}')


if __name__ == '__main__':
    main()
