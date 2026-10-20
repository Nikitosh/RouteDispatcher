// Отметки инженера «приехал» и «закончил». Экран инженера пишет их в localStorage, диспетчер читает на том же сайте.
// Ключ: участок, версия плана (-1 — утренний план, иначе номер события), бригада.
// Значение: {номер заявки: {arrived, done}}, минуты от 10:00, и _plan — подпись маршрута бригады: после пересчёта
// плана (другие заявки или времена) прежние отметки к нему не относятся и не читаются.
(function (g) {
  const PREFIX = 'pulse.marks|';
  const key = (slug, ver, brigade) => `${PREFIX}${slug}|${ver}|${brigade}`;

  // подпись маршрута бригады v: заявки и плановые начала (короткий хэш)
  function sig(doc, v) {
    const rt = doc.plan.routes.find(r => r.v === v);
    const text = rt ? rt.stops.map(s => `${doc.orders[s.k].id}@${Math.round(s.beg)}`).join(',') : '';
    let h = 5381;
    for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  }
  function read(slug, ver, brigade, sg) {
    try {
      const m = JSON.parse(localStorage.getItem(key(slug, ver, brigade)) || '{}') || {};
      if (sg && m._plan && m._plan !== sg) return {};               // отметки к прежнему плану
      delete m._plan;
      return m;
    } catch (e) { return {}; }
  }
  function write(slug, ver, brigade, marks, sg) {
    try {
      if (Object.keys(marks).length) localStorage.setItem(key(slug, ver, brigade), JSON.stringify(sg ? {...marks, _plan: sg} : marks));
      else localStorage.removeItem(key(slug, ver, brigade));
    } catch (e) { /* приватный режим: отметки живут до перезагрузки */ }
  }

  // остановка маршрута в плановых минутах; s — элемент route.stops (запасной источник времени)
  function stop(o, s) {
    const p = o.plan || {};
    return {id: String(o.id), arrive: p.arrive ?? s.arr, start: p.start ?? s.beg, end: p.end ?? s.end, w0: o.window[0], svc: o.svc};
  }

  // stops по порядку → {cur: первая незаконченная, last: последняя отметка {i, kind, t}, delay: + опоздание, − опережение}
  function progress(stops, marks) {
    let cur = stops.length, last = null;
    for (let i = 0; i < stops.length; i++) {
      const m = marks[stops[i].id] || {};
      if (m.done != null) { last = {i, kind: 'done', t: m.done}; continue; }
      cur = i;
      if (m.arrived != null) last = {i, kind: 'arrived', t: m.arrived};
      break;
    }
    let delay = 0;
    if (last) {
      const s = stops[last.i];
      delay = last.kind === 'done' ? last.t - s.end : Math.max(last.t, s.w0) - s.start;
    }
    return {cur, last, delay: Math.round(delay)};
  }

  // ±5 минут считаем «по плану»
  const OK = 5;
  function delayText(d) {
    if (d > OK) return {cls: 'late', text: `опаздывает на ${d} мин`};
    if (d < -OK) return {cls: 'early', text: `опережает на ${-d} мин`};
    return {cls: 'ok', text: 'по плану'};
  }

  // факт выездов для `python -m dispatch plan --facts`: бригады участка в этой версии плана с «приехал» и «закончил»
  const FACT_COLS = ['Дата', 'Заявка', 'Тип заявки BK', 'Тип заявки HD', 'Адрес', 'Гигабит', 'План мин', 'Факт мин', 'Бригада'];
  function facts(doc, slug, ver) {
    const rows = [];
    doc.plan.routes.forEach(rt => {
      const b = doc.brigades[rt.v], m = read(slug, ver, b.name, sig(doc, rt.v));
      rt.stops.forEach(s => {
        const o = doc.orders[s.k], x = m[String(o.id)];
        if (x && x.arrived != null && x.done != null)
          rows.push([doc.meta.date || '', o.id, o.bk || o.type, o.hd || '', o.addr, o.gigabit ? 'Да' : 'Нет', o.svc, Math.round(x.done - x.arrived), b.name]);
      });
    });
    return rows;
  }
  const cell = v => { v = String(v ?? ''); return /[;"\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  const factsCSV = rows => '\ufeff' + [FACT_COLS].concat(rows).map(r => r.map(cell).join(';')).join('\r\n') + '\r\n';
  function download(text, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], {type: 'text/csv;charset=utf-8'}));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  g.FieldStatus = {PREFIX, key, sig, read, write, stop, progress, delayText, OK, FACT_COLS, facts, factsCSV, download};
})(window);

// названия работ и подтипов заявок — человеческими словами (экран диспетчера и экран инженера)
const WORK = [[/^(TVE\/ENT|ТВ)\.\s*Замена приставки техником/i, 'замена ТВ-приставки'], [/^TVE\/ENT\.\s*Другие ошибки/i, 'ошибки телевидения'],
  [/Конвергенция( абонента)?/i, 'пакет услуг: интернет и ТВ'], [/Нет линка/i, 'нет связи'], [/Переключение на Гбит(\/с)?/i, 'переход на гигабит'],
  [/Рост ошибок( на порту)?/i, 'ошибки на линии'], [/Роутер\.\s*Замена техническим специалистом/i, 'замена роутера'],
  [/IP-адрес 169\.*/i, 'не выдаётся IP-адрес'], [/Разрывы/i, 'обрывы связи'], [/Заказ подключения\/Дозаказ оборудования/i, 'подключение и оборудование'],
  [/Заказ подключения/i, 'новое подключение'], [/: темп$/i, ': скорость работы']];
const work = s => WORK.reduce((t, [re, to]) => t.replace(re, to), String(s || ''));
