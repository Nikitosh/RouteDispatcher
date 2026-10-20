"""План на день: файлы → координаты → матрицы → решатели → проверка → причины и объяснения → документ."""
import dataclasses
import datetime
import os
import re
import time
from collections import Counter
from dataclasses import asdict

from ..config import SERVICE, SHIFT, TravelSettings, weekday_of
from ..files import document as docs
from ..files.readers import read_brigades, read_orders
from ..geo.geocoder import Geocoder
from ..geo.geometry import RouteGeometry
from ..geo.houses import HouseIndex
from ..geo.matrices import MatrixBuilder
from ..model import complexity
from ..model.problem import Problem
from ..solve.runner import SolverRunner
from .explain import Explainer
from .metrics import plan_metrics
from .validate import check_plan


class Planner:
    def __init__(self, travel=None, geocoder=None, matrices=None, solver=None, geometry=None, houses=None, log=print, lunch=True,
                 equipment=True):
        """lunch — обед бригадам со сменой от 6 часов (model/problem.py); False — без обеда. equipment=False — без учёта
        оборудования (заявкам ничего не нужно, бригады без предела)."""
        self.equipment = equipment
        self.travel = travel or TravelSettings()
        self.lunch = lunch
        self.houses = houses if houses is not None else HouseIndex()
        self.geocoder = geocoder or Geocoder()
        self.matrices = matrices or MatrixBuilder()
        self.solver = solver or SolverRunner()
        self.geometry = geometry or RouteGeometry()
        self.log = log

    def load(self, orders_path, brigades_path, name=None, schedule=''):
        """Задача по файлам заявок и бригад. Стартовая точка 0 — офис, дальше — дома бригад, если заданы."""
        f = read_orders(orders_path)
        self.travel.weekday = weekday_of(f.date)       # пробки по профилю дня недели плана
        brigades = read_brigades(brigades_path, schedule)
        for b in brigades:
            b.lunch = self.lunch
        name = name or re.split(r'[ _.]', os.path.basename(orders_path))[0]
        self.log(f'Загружено: {len(f.orders)} заявок, {len(brigades)} бригад, участок {name}')
        if any(b.speed != 1 for b in brigades):
            self.log('Темп бригад: ' + ', '.join(f'{b.name} ×{b.speed:.2f}'.replace('.', ',') for b in brigades if b.speed != 1))
        shifts = Counter(b.shift_text for b in brigades)
        if len(shifts) > 1 or (0, SHIFT) not in {b.shift for b in brigades}:
            self.log('Смены: ' + ', '.join(f'{t} — {n}' for t, n in sorted(shifts.items())))
        coords = self.geocoder.locate([f.office] + [o.house for o in f.orders] + [b.start_address for b in brigades if b.start_address],
                                      log=self.log)
        starts = [coords[f.office]]
        for b in brigades:
            c = coords[b.start_address] if b.start_address else starts[0]
            if c not in starts:
                starts.append(c)
            b.start, (b.lat, b.lon) = starts.index(c), c
            b.start_address = b.start_address or f.office
        for o in f.orders:
            o.lat, o.lon = coords[o.house]
            if not self.equipment:
                o.equipment = {}
        complexity.apply(f.orders, self.houses)
        known = sum(1 for o in f.orders if self.houses.get(o.house))
        self.log(f'Длительность по коэффициенту сложности: данные о доме есть у {known} из {len(f.orders)} заявок')
        points = starts + [(o.lat, o.lon) for o in f.orders]
        matrices = self.matrices.build(points, len(starts), self.travel)
        self.log(f'Матрицы: OSRM и кэш; пробки {self.travel.traffic}' + (' с поправкой на час окна заявки' if self.travel.hourly else '')
                 + f', подход к заявке {self.travel.overhead} мин')
        meta = dict(name=name, date=f.date, office=f.office, orders_file=os.path.basename(orders_path),
                    brigades_file=os.path.basename(brigades_path))
        return Problem(name, f.orders, brigades, starts, matrices), meta

    def run(self, problem, meta, out_dir, seconds=3.0, seed=1, geometry=True, tries=1, answer=None):
        t0 = time.time()
        ours, base, task = self.solver.solve(problem, seconds, seed, workdir=out_dir, tries=tries, answer=answer)
        check = check_plan(problem, ours.routes)
        self.log(f'Решатель {seconds:g} с; проверка: ' + ('нарушений нет' if check.ok else f'{len(check.errors)} нарушений'))
        doc = self.document(problem, meta, ours.routes, base.routes, ours.lower_bound, check)
        doc['meta'].update(seconds=seconds, seed=seed, tries=tries)
        doc['norms'] = self.norms_check(problem, seconds, seed, os.path.join(out_dir, 'norms'))
        if geometry:
            docs.fill_geometry(doc, self.geometry, log=self.log)
        doc['meta']['runtime_s'] = round(time.time() - t0, 1)
        return doc

    def norms_check(self, problem, seconds, seed, workdir):
        """План по фиксированным нормативам (без коэффициента сложности), проигранный с длительностями по
        коэффициенту: сколько заявок он бы сорвал. None, если длительности совпадают с нормативом."""
        if all(o.service == SERVICE[o.bk] and not o.car_access for o in problem.orders):
            return None
        flat = [dataclasses.replace(o, service=SERVICE[o.bk], car_access=0.0) for o in problem.orders]
        P = Problem(problem.name, flat, problem.brigades, problem.starts, problem.matrices)
        routes = self.solver.plan_only(P, seconds, seed, workdir)
        late, minutes = [], 0.0
        for v, r in enumerate(routes):
            for st in (problem.schedule(v, r).stops if r else ()):
                over = st.start - problem.latest(st.order, v)
                if over > 1e-6:
                    late.append(problem.orders[st.order].id)
                    minutes += over
        used = [r for r in routes if r]
        self.log(f'План по нормативу: {len(used)} бригад; с длительностью по коэффициенту опоздал бы к {len(late)} заявкам')
        return dict(brigades_used=len(used), km_total=round(sum(P.km(v, r) for v, r in enumerate(routes)), 1),
                    served=sum(len(r) for r in routes), late=len(late), late_minutes=round(minutes), late_ids=late,
                    work_norm=sum(o.service for o in flat), work=sum(o.service for o in problem.orders))

    def document(self, problem, meta, routes, base_routes, lower_bound, check):
        P, ex = problem, Explainer(problem)
        n = len(P.orders)
        served = {k for r in routes for k in r}
        unassigned = [k for k in range(n) if k not in served]
        base_served = {k for r in base_routes for k in r}
        base_unassigned = [k for k in range(n) if k not in base_served]
        sched = {v: P.schedule(v, r) for v, r in enumerate(routes) if r}
        base_sched = {v: P.schedule(v, r) for v, r in enumerate(base_routes) if r}
        orders = [docs.order_dict(P, k) for k in range(n)]
        stock = [P.stock_for(v, routes[v]) for v in range(len(P.brigades))]    # выдать утром: под маршрут + запасные
        for v, s in sched.items():
            for i, stop in enumerate(s.stops):
                docs.set_assignment(orders[stop.order], v, i + 1, stop)
                e = ex.assigned(routes, stop.order)
                orders[stop.order]['explanation'] = dict(summary=e.summary, added_km=round(e.added_km, 2), others=e.others)
        for k in unassigned:
            orders[k]['reason'] = asdict(ex.unassigned(routes, k))
        base_reasons = [dict(k=k, **asdict(ex.greedy_unassigned([[x for x in r if x < k] for r in base_routes], k)))
                        for k in base_unassigned]
        return dict(
            meta=dict(meta, solver='plan', traffic=self.travel.traffic, overhead=self.travel.overhead, traffic_hours=self.travel.hourly, equipment=self.equipment,
                      weekday=self.travel.weekday, traffic_grid=P.traffic_grid,
                      lunch=self.lunch, created=datetime.datetime.now().isoformat(timespec='seconds')),
            office=dict(addr=meta['office'], lat=P.starts[0][0], lon=P.starts[0][1]),
            brigades=[docs.brigade_dict(v, b, stock[v]) for v, b in enumerate(P.brigades)],
            orders=orders,
            plan=dict(routes=[docs.route_dict(P, v, s) for v, s in sched.items()], check=asdict(check),
                      metrics=plan_metrics(P, routes, unassigned, sched)),
            baseline=dict(routes=[docs.route_dict(P, v, s) for v, s in base_sched.items()],
                          metrics=plan_metrics(P, base_routes, base_unassigned, base_sched), unassigned=base_reasons),
            lower_bound=lower_bound)
