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
    lines += _unassigned(doc)
    return '\n'.join(lines)


def replan_summary(doc):
    e, B, O = doc['event'], doc['brigades'], doc['orders']
    if e['kind'] == 'unavailable':
        what = f"{e['name']} недоступна"
    elif e['kind'] == 'cancel':
        what = f"отмена заявки {O[e['order']]['id']}"
    else:
        what = f"новая заявка ({e['type'].lower()}) {e['addr']}"
    lines = [f"Событие в {hhmm(e['at'])}: {what}", f"Зафиксировано: {e['fixed']} заявок выполнены или начаты"]
    if e.get('accident'):
        a = e['accident']
        lines.append(f"Авария: {B[a['brigade']]['name']}, начало в {hhmm(a['start'])} — через {round(a['wait'])} мин после появления, "
                     f"вытеснено заявок: {a['displaced']}")
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
    return ['', ' Неназначенные:'] + [f"  {o['id']} {o['type']}, {o['district']}: {o['reason']['text']}" for o in un] if un else []
