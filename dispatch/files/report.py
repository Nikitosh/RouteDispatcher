"""Сводки для терминала."""
from ..formatting import hhmm, km_text


def plan_summary(doc):
    m, mb, B = doc['plan']['metrics'], doc['baseline']['metrics'], doc['brigades']
    lines = ['', f" {'Бригада':<14}{'Транспорт':<17}{'Заявок':>7}   {'Работа':<13}{'Км':>7}"]
    for r in doc['plan']['routes']:
        b = B[r['v']]
        work = hhmm(r['stops'][0]['beg']) + '–' + hhmm(r['stops'][-1]['end'])
        lines.append(f" {b['name']:<14}{b['mode_name']:<17}{len(r['stops']):>7}   {work:<13}{km_text(r['km']):>7}")
    lines += ['', f" {'':<28}{'Наш план':>10}{'Базовый ТЗ':>13}",
              f" {'Бригад':<28}{m['brigades_used']:>10}{mb['brigades_used']:>13}",
              f" {'Пробег, км':<28}{km_text(m['km_total']):>10}{km_text(mb['km_total']):>13}",
              f" {'Выполнено':<28}{str(m['served']) + '/' + str(m['orders']):>10}{str(mb['served']) + '/' + str(mb['orders']):>13}"]
    lb = doc['lower_bound']
    if lb is not None:
        lines.append(f' Нижняя граница: меньше {lb} бригад невозможно' + (' — план оптимален по числу бригад' if lb == m['brigades_used'] else ''))
    q = doc.get('quality')
    if q:
        lines.append(f" Режим «лучшие»: лучшие {q['k']} из {q['pool']} монтажников закрывают день"
                     + (' — меньше нельзя, доказано' if q['proven'] else ''))
        lines += [' Цена качества:', f"  {'K':>4}{'Бригад':>9}{'Км':>9}{'Рейтинг':>10}"]
        lines += [f"  {p['k']:>4}{p['brigades']:>9}{km_text(p['km']):>9}{p['rating']:>10.2f}" for p in q['curve']]
    elif doc['plan']['metrics'].get('rating'):
        lines.append(f" Средний рейтинг исполнителя на заявке: {doc['plan']['metrics']['rating']:.2f}")
    e = doc.get('early')
    if e:
        lines.append(f" Ранний старт по согласованию (до {e['minutes']} мин раньше окна): {e['brigades']} бригад, "
                     f"{km_text(e['km'])} км — если согласятся {len(e['calls'])} клиентов")
    w = doc.get('exact_time')
    if w:
        lines.append(' Цена точного времени (часовое окно вместо двухчасового): ' + '; '.join(
            f"{round(p['share'] * 100)}% клиентов — {p['brigades']} бригад, {km_text(p['km'])} км"
            + ('' if p['served'] == p['orders'] else f", не влезло {p['orders'] - p['served']}") for p in w))
    n = doc.get('norms')
    if n:
        lines.append(f" Работы на месте: {n['work']} мин по коэффициенту сложности, {n['work_norm']} по нормативу. "
                     f"План по нормативу ({n['brigades_used']} бригад) опоздал бы к {n['late']} заявкам, на {n['late_minutes']} мин всего")
    if doc.get('curve'):
        lines += ['', ' Бригады против км (предел бригад → выполнено, км):']
        lines += [f"  {c['limit']:>3} → {c['served']} заявок, {km_text(c['km'])} км" for c in doc['curve']]
    if doc.get('price'):
        lines += ['', ' Цена ограничений (без одного правила):']
        lines += [f"  {x['what']}: {x['brigades']} бригад" + (' (минимум доказан)' if x['bound'] == x['brigades'] else '')
                  + f", {km_text(x['km'])} км, выполнено {x['served']}" for x in doc['price']]
    lines += _unassigned(doc)
    return '\n'.join(lines)


def replan_summary(doc):
    e, B, O = doc['event'], doc['brigades'], doc['orders']
    if e['kind'] == 'unavailable':
        what = f"{e['name']} недоступна"
    elif e['kind'] == 'cancel':
        what = f"отмена заявки {O[e['order']]['id']}"
    elif e['kind'] == 'finish':
        d = e['delta']
        what = (f"{e['name']} закончит заявку {O[e['order']]['id']} в {hhmm(e['end'])} — "
                + (f"на {d} мин позже плана" if d > 0 else f"на {-d} мин раньше плана" if d < 0 else "по плану"))
    elif e['kind'] == 'window':
        what = (f"клиент перенёс окно заявки {O[e['order']]['id']}: {hhmm(e['was'][0])}–{hhmm(e['was'][1])} → "
                f"{hhmm(e['now'][0])}–{hhmm(e['now'][1])}")
    elif e['kind'] == 'breakdown':
        what = f"{e['name']}: сломалась машина, дальше — {e['now_name'].lower()}"
    elif e['kind'] == 'move':
        what = f"заявка {O[e['order']]['id']} передана: {e['name']}"
    else:
        what = f"новая заявка ({e['type'].lower()}) {e['addr']}"
    lines = [f"Событие в {hhmm(e['at'])}: {what}", f"Зафиксировано: {e['fixed']} заявок выполнены или начаты"]
    if e.get('accident'):
        a = e['accident']
        lines.append(f"Авария: {B[a['brigade']]['name']}, начало в {hhmm(a['start'])} — через {round(a['wait'])} мин после появления, "
                     f"вытеснено заявок: {a['displaced']}")
    for t in e.get('turned') or ():
        where = 'была в пути к' if t['kind'] == 'transit' else 'ждала начала окна у'
        now = f"едет к {O[t['now']]['id']}" if t['now'] is not None else 'дальше заявок нет'
        lines.append(f"{B[t['brigade']]['name']} {where} заявке {O[t['was']]['id']}: разворачивается в {hhmm(e['at'])}, {now}")
    if e['changes']:
        slot = lambda x: f"{B[x['brigade']]['name']} {hhmm(x['start'])}" if x else '—'
        lines += ['', f" {'Заявка':<9}{'Тип':<13}{'Было':<18}{'Стало':<18}Причина"]
        lines += [f" {O[c['k']]['id']:<9}{O[c['k']]['type']:<13}{slot(c['was']):<18}{slot(c['now']) if c['now'] else 'не назначена':<18}{c['why']}"
                  for c in e['changes']]
    m, b = doc['plan']['metrics'], e['before']
    lines += ['', f" Бригад: {b['brigades_used']} → {m['brigades_used']}   Пробег: {km_text(b['km_total'])} → {km_text(m['km_total'])} км   "
                  f"Неназначенных: {b['unassigned']} → {m['unassigned']}",
              ' Проверка: ' + ('нарушений нет' if doc['plan']['check']['ok'] else '; '.join(doc['plan']['check']['errors']))]
    lines += _unassigned(doc)
    return '\n'.join(lines)


def _unassigned(doc):
    un = [o for o in doc['orders'] if o['plan'] is None and o.get('reason') and not o.get('cancelled')]
    if not un:
        return []
    lines = ['', ' Неназначенные:'] + [f"  {o['id']} {o['type']}, {o['district']}: {o['reason']['text']}" for o in un]
    if doc.get('shortage'):
        from ..planning.fleet import text
        lines += ['', ' Чтобы закрыть все:'] + [f'  {x}' for x in text(doc['shortage'])]
    return lines
