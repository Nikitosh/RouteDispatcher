// Варианты 1–3
VARIANTS.push({
  short: 'Диспетчерская', name: 'Диспетчерская', fluid: true,
  idea: 'Классический экран: метрики против базового варианта сверху, карта Москвы с маршрутами по улицам и список бригад посередине, временная шкала снизу. Наведите на бригаду в списке — её маршрут выделится. Наведите на точку — карточка заявки.',
  how: 'CLI пишет один plan.html рядом с plan.xlsx', cost: 'средняя',
  render: () => `<div class="app">${topbar('Восток · 17.08.2026', ['План', 'Изменения', 'Сравнение'], 0)}${kpis()}
  <div style="display:flex;gap:14px;padding:0 20px">
    <div class="card" style="flex:1;min-width:0;overflow:hidden"><div class="lmap" id="m1" style="height:460px"></div></div>
    <div class="card" style="width:390px;flex:none"><div class="ctitle">Бригады в работе · ${D.plan.length} из ${D.brigades.length}</div>
      ${D.plan.map(r => `<div class="brow b1row" data-v="${r.v}" style="cursor:default"><span class="sw" style="background:${col[r.v]}"></span><span class="n">${B(r.v)}</span>
        <span class="x">${TR[r.mode]} · ${sk(r.mask).join(', ')}</span><span>${r.stops.length} заявок</span><b style="width:62px;text-align:right">${num(r.km)} км</b></div>`).join('')}
      <div class="brow muted" style="font-size:12px">Не задействованы: ${D.brigades.filter(b => !D.plan.find(r => r.v === b.v)).map(b => B(b.v)).join(', ')}</div></div>
  </div>
  <div class="card" style="margin:14px 20px 20px;padding:4px 0 6px">${gantt('100%', D.plan, {rowH: 32})}</div></div>`,
  mount: s => {
    const m = lmap(s.querySelector('#m1')), lines = {};
    D.plan.forEach(r => lines[r.v] = L.polyline(routeLine(r.v), {color: col[r.v], weight: 3.5, opacity: .85}).addTo(m));
    D.plan.forEach(r => r.stops.forEach(st => orderDot(m, st.k)));
    s.querySelectorAll('.b1row').forEach(row => {
      row.onmouseenter = () => D.plan.forEach(r => lines[r.v].setStyle(r.v === +row.dataset.v ? {weight: 6, opacity: 1} : {weight: 2.5, opacity: .2}));
      row.onmouseleave = () => D.plan.forEach(r => lines[r.v].setStyle({weight: 3.5, opacity: .85}));
    });
  }
});

VARIANTS.push({
  short: 'Шкала', name: 'Временная шкала на весь экран',
  idea: 'Главное — день каждой бригады: дорога, ожидание, работа и окна клиентов. Сразу видно, где запас времени и почему бригада не может взять ещё заявку. Карта маленькая, для ориентира.',
  how: 'тот же plan.html, другая компоновка', cost: 'средняя',
  render: () => `<div class="app" style="height:720px">${topbar('Восток · 17.08.2026', ['Шкала', 'Карта', 'Таблица'], 0,
    `<span class="muted">6 бригад · ${num(km(D.plan))} км · все 66 заявок</span><button class="btn">Событие…</button>`)}
  <div style="display:flex;gap:14px;padding:16px 20px">
    <div class="card" style="padding:4px 0 8px">${gantt(900, D.plan, {rowH: 96, labelW: 150, windows: true, big: true, now: 220})}
      <div style="display:flex;gap:18px;padding:10px 14px 0;font-size:12px" class="muted">
        <span><i style="display:inline-block;width:22px;height:10px;border-radius:3px;background:#1F5FBF;vertical-align:-1px"></i> работа</span>
        <span><i style="display:inline-block;width:22px;height:4px;background:#1F5FBF66;vertical-align:2px"></i> дорога</span>
        <span><i style="display:inline-block;width:22px;border-top:2px dotted #A39E95;vertical-align:3px"></i> ожидание окна</span>
        <span><i style="display:inline-block;width:22px;height:3px;background:#1B1A1826;vertical-align:2px"></i> окно начала работ</span>
        <span><i style="display:inline-block;width:12px;height:10px;border-radius:3px;box-shadow:0 0 0 1.5px #B42318;vertical-align:-1px"></i> авария</span></div></div>
    <div style="flex:1;display:flex;flex-direction:column;gap:14px">
      <div class="card" style="overflow:hidden">${mapSVG(314, 300, {districts: false, pad: 16, routes: D.plan.map(r => ({pts: routePts(r), color: col[r.v], w: 1.8}))})}</div>
      <div class="card" style="padding:12px 14px"><b>Неназначенных нет</b><div class="muted" style="margin-top:4px;line-height:1.45">Все 66 заявок начинаются внутри окна, смены заканчиваются до 22:00.</div></div>
      <div class="card" style="padding:12px 14px;line-height:1.5"><b>Сейчас 13:40</b><div class="muted">Выполнено ${D.plan.reduce((a, r) => a + r.stops.filter(s => s.end <= 220).length, 0)} из 66. Красная линия — текущее время.</div></div>
    </div></div></div>`
});

VARIANTS.push({
  short: 'Отчёт', name: 'Отчёт одной страницей',
  idea: 'Статичный документ: главный вывод, сравнение с базовым вариантом, карта, таблицы и допущения. Читается сверху вниз, печатается в PDF, прикладывается к сдаче. Интерактивности почти нет.',
  how: 'шаблон HTML → plan.html (и PDF)', cost: 'низкая',
  render: () => {
    const bar = (l, a, b, u, fmtv = x => x) => { const m = Math.max(a, b); return `<div style="display:grid;grid-template-columns:170px 1fr;gap:6px 14px;align-items:center;margin:12px 0">
      <div class="muted">${l}</div><div><div style="display:flex;align-items:center;gap:8px"><div style="height:16px;width:${a / m * 520}px;background:var(--accent);border-radius:3px"></div><b>${fmtv(a)} ${u}</b><span class="muted">наш план</span></div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px"><div style="height:16px;width:${b / m * 520}px;background:#B4530A;border-radius:3px"></div><b>${fmtv(b)} ${u}</b><span class="muted">базовый вариант ТЗ</span></div></div></div>`; };
    return `<div class="app" style="height:900px;background:#fff;overflow-y:auto"><div style="width:900px;margin:0 auto;padding:44px 0 60px;font-size:14px;line-height:1.55">
    <div class="muted">План выездов · Восток · 17.08.2026</div>
    <h2 style="font-size:32px;line-height:1.2;margin:8px 0 10px;letter-spacing:-.01em">6 бригад выполняют все 66 заявок. Базовый вариант задействует 12 и оставляет 19 заявок без исполнителя.</h2>
    <p class="muted" style="margin:0 0 20px">Меньше 6 бригад невозможно: это доказано нижней границей. Пробег на 26% меньше, чем у базового варианта.</p>
    ${bar('Бригад задействовано', 6, 12, '')}${bar('Пробег, км', km(D.plan), km(D.base), 'км', num)}${bar('Выполнено заявок', 66, SERVED_BASE, 'из 66')}
    <h3 style="margin:30px 0 10px">Маршруты</h3><div style="border:1px solid var(--line);border-radius:9px;overflow:hidden">${mapSVG(898, 430, {routes: D.plan.map(r => ({pts: routePts(r), color: col[r.v]}))})}</div>
    <h3 style="margin:30px 0 6px">Бригады</h3><table class="t"><tr><th>Бригада</th><th>Транспорт</th><th>Навыки</th><th>Заявок</th><th>Первая</th><th>Последняя до</th><th style="text-align:right">Км</th></tr>
    ${D.plan.map(r => `<tr><td><span class="sw" style="display:inline-block;background:${col[r.v]};margin-right:6px"></span>${B(r.v)}</td><td>${TR[r.mode]}</td><td>${sk(r.mask).join(', ')}</td><td>${r.stops.length}</td><td>${fmt(r.stops[0].beg)}</td><td>${fmt(r.stops.at(-1).end)}</td><td style="text-align:right">${num(r.km)}</td></tr>`).join('')}
    <tr><td><b>Итого</b></td><td></td><td></td><td><b>66</b></td><td></td><td></td><td style="text-align:right"><b>${num(km(D.plan))}</b></td></tr></table>
    <h3 style="margin:30px 0 6px">Что не выполнил бы базовый вариант</h3><table class="t"><tr><th>Заявка</th><th>Тип</th><th>Район</th><th>Окно</th><th>Причина</th></tr>
    ${D.base_unserved.slice(0, 7).map(k => `<tr><td>${O[k].id}</td><td>${badge(O[k].type)}</td><td>${O[k].district}</td><td>${win(O[k])}</td><td>${baseReason(k)}</td></tr>`).join('')}
    <tr><td colspan="5" class="muted">и ещё ${D.base_unserved.length - 7}</td></tr></table>
    <h3 style="margin:30px 0 6px">Допущения</h3><ul style="margin:0;padding-left:20px"><li>Смена у всех бригад с 10:00 до 22:00.</li><li>Работа на месте по нормативам: подключение 70 мин, авария 80, ремонт 30, дозаказ 20; дорогу считаем по картам.</li><li>Работу начинаем внутри окна, приехать раньше и подождать можно.</li></ul>
    </div></div>`;
  }
});

// Варианты 4–6
VARIANTS.push({
  short: 'Колонки', name: 'Колонки бригад',
  idea: 'Колонка на бригаду: мини-карта её маршрута и заявки по порядку. Удобно проверять «что делает бригада 3» и перетаскивать заявки между колонками при ручном переназначении.',
  how: 'plan.html, вкладка «Бригады»', cost: 'низкая–средняя',
  render: () => `<div class="app" style="height:880px">${topbar('Восток · 17.08.2026', ['Карта', 'Бригады', 'Шкала'], 1)}
  <div style="display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;padding:14px 16px">
  ${D.plan.map(r => `<div class="card" style="overflow:hidden">
    <div style="height:5px;background:${col[r.v]}"></div>
    <div style="padding:9px 11px 6px"><b style="font-size:15px">${B(r.v)}</b> <span class="muted">· ${TR[r.mode]}</span>
      <div class="muted" style="font-size:11px;margin-top:2px">${sk(r.mask).join(', ')}</div>
      <div style="margin-top:4px;font-size:12px">${r.stops.length} заявок · <b>${num(r.km)} км</b> · до ${fmt(r.stops.at(-1).end)}</div></div>
    ${mapSVG(200, 116, {districts: false, pad: 10, routes: [{pts: routePts(r), color: col[r.v], w: 2}], dot: k => owner[k] && owner[k].v === r.v ? {c: col[r.v]} : {c: '#C9C3B8', op: .6}})}
    <div>${r.stops.map(s => `<div style="padding:6px 11px;border-top:1px solid var(--line);font-size:12px;line-height:1.35">
      <div style="display:flex;justify-content:space-between;gap:6px"><b>${fmt(s.beg)}–${fmt(s.end)}</b>${badge(O[s.k].type)}</div>
      <div class="muted" style="font-size:11px">${O[s.k].district} · окно ${win(O[s.k])}</div></div>`).join('')}</div></div>`).join('')}
  </div></div>`
});

VARIANTS.push({
  short: 'Сравнение', name: 'Сравнение бок о бок',
  idea: 'Две карты: базовый вариант из ТЗ и наш план, под ними пробег по каждому исполнителю — это вторая обязательная метрика. Прямо закрывает шаг 7 демо.',
  how: 'plan.html, вкладка «Сравнение»', cost: 'средняя',
  render: () => {
    const mx = Math.max(...D.base.map(r => r.km), ...D.plan.map(r => r.km));
    const bars = (rs, c) => `<div style="display:flex;align-items:flex-end;gap:6px;height:150px;padding:0 14px">${rs.map(r => `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:11px">
      <b>${num(r.km)}</b><div style="width:100%;max-width:34px;height:${r.km / mx * 110}px;background:${c(r.v)};border-radius:3px 3px 0 0"></div><span class="muted">${B(r.v)}</span></div>`).join('')}</div>`;
    const head = (t, a, b) => `<div class="ctitle" style="display:flex;justify-content:space-between"><span>${t}</span><span class="${b ? 'bad' : 'good'}">${a}</span></div>`;
    return `<div class="app" style="height:790px">${topbar('Восток · 17.08.2026', ['План', 'Изменения', 'Сравнение'], 2)}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px 20px">
      <div class="card" style="overflow:hidden">${head('Базовый вариант ТЗ', `12 бригад · ${num(km(D.base))} км · ${D.base_unserved.length} не выполнено`, 1)}
        ${mapSVG(606, 380, {routes: D.base.map(r => ({pts: routePts(r), color: baseCol(r.v), w: 2})), dot: k => baseOwner[k] != null ? {c: baseCol(baseOwner[k])} : null, unserved: D.base_unserved})}
        <div class="muted" style="padding:8px 14px;font-size:12px">Красные квадраты — заявки, которые жадный алгоритм не смог назначить.</div></div>
      <div class="card" style="overflow:hidden">${head('Наш план', `6 бригад · ${num(km(D.plan))} км · всё выполнено`)}
        ${mapSVG(606, 380, {routes: D.plan.map(r => ({pts: routePts(r), color: col[r.v]}))})}
        <div class="muted" style="padding:8px 14px;font-size:12px">Меньше 6 бригад невозможно — доказано нижней границей.</div></div>
      <div class="card" style="padding-bottom:10px"><div class="ctitle">Пробег по исполнителям, км</div>${bars(D.base, baseCol)}</div>
      <div class="card" style="padding-bottom:10px"><div class="ctitle">Пробег по исполнителям, км</div>${bars(D.plan, v => col[v])}</div>
    </div></div>`;
  }
});

// Проигрывание: положение бригады в момент t вдоль улиц
function cut(line, f) {
  if (f <= 0) return [line[0]]; if (f >= 1) return line;
  const c = Math.cos(0.9727), d = [0];
  for (let i = 1; i < line.length; i++) d.push(d[i - 1] + Math.hypot(line[i][0] - line[i - 1][0], (line[i][1] - line[i - 1][1]) * c));
  const T = d.at(-1) * f; let i = 1; while (i < line.length && d[i] < T) i++;
  if (i >= line.length) return line;
  const g = (T - d[i - 1]) / ((d[i] - d[i - 1]) || 1);
  return line.slice(0, i).concat([[line[i - 1][0] + (line[i][0] - line[i - 1][0]) * g, line[i - 1][1] + (line[i][1] - line[i - 1][1]) * g]]);
}
function stateAt(r, t) {
  let pe = r.t0 || 0; const Lg = legs(r.v);
  for (let i = 0; i < r.stops.length; i++) {
    const s = r.stops[i], q = O[s.k];
    if (t < s.arr) { const part = cut(Lg[i], Math.max(0, Math.min(1, (t - pe) / Math.max(1, s.arr - pe)))); return {path: Lg.slice(0, i).flat().concat(part), ll: part.at(-1), st: 'в пути', to: 'к ' + q.district}; }
    if (t < s.beg) return {path: Lg.slice(0, i + 1).flat(), ll: LL(s.k), st: 'ждёт окно', to: q.district + ', с ' + fmt(q.a)};
    if (t < s.end) return {path: Lg.slice(0, i + 1).flat(), ll: LL(s.k), st: 'работает', to: q.type.toLowerCase() + ', ' + q.district + ' до ' + fmt(s.end)};
    pe = s.end;
  }
  return {path: Lg.flat(), ll: LL(r.stops.at(-1).k), st: 'закончила', to: 'в ' + fmt(r.stops.at(-1).end)};
}
function playPanels(t) {
  const P = D.plan.map(r => ({r, p: stateAt(r, t)}));
  const done = D.plan.reduce((a, r) => a + r.stops.filter(s => s.end <= t).length, 0);
  const c = st => P.filter(x => x.p.st === st).length;
  return `<div class="card over" style="left:60px;top:16px;padding:14px 18px;width:270px"><div style="font-size:36px;font-weight:700;line-height:1">${fmt(t)}</div>
    <div style="margin-top:8px">Выполнено <b>${done}</b> из 66</div><div class="muted" style="margin-top:4px;line-height:1.45">работают ${c('работает')} · в пути ${c('в пути')} · ждут окна ${c('ждёт окно')} · закончили ${c('закончила')}</div></div>
    <div class="card over" style="right:16px;top:16px;width:330px">${P.map(({r, p}) => `<div class="brow"><span class="sw" style="background:${col[r.v]}"></span><span class="n">${B(r.v)}</span><span class="x"><b style="color:var(--ink)">${p.st}</b> · ${p.to}</span></div>`).join('')}</div>`;
}
VARIANTS.push({
  short: 'Проигрывание', name: 'Проигрывание дня на карте', fluid: true,
  idea: 'Карта Москвы на весь экран и ползунок времени: бригады едут по улицам, пройденное сплошной линией, оставшееся пунктиром, выполненные заявки бледнеют. Для демо: можно остановиться на моменте события. Ползунок и «Пуск» работают.',
  how: 'plan.html + немного JS', cost: 'выше средней',
  render: () => `<div class="app" style="height:720px"><div class="lmap" id="m6" style="position:absolute;inset:0"></div><div id="pp">${playPanels(220)}</div>
    <div class="card over" style="left:16px;right:16px;bottom:26px;padding:10px 16px;display:flex;align-items:center;gap:14px">
      <button class="btn primary" id="pplay" style="width:96px">Пуск</button><label for="pr" class="muted">Время</label>
      <input id="pr" type="range" min="0" max="720" step="2" value="220" style="flex:1;accent-color:#1F5FBF">
      <span class="muted">10:00 — 22:00</span></div></div>`,
  mount: s => {
    const m = lmap(s.querySelector('#m6'), {tl: [300, 30], br: [350, 90]}), r = s.querySelector('#pr'), p = s.querySelector('#pp'), b = s.querySelector('#pplay');
    const doneL = {}, pin = {}, dots = [];
    D.plan.forEach(rt => {
      L.polyline(routeLine(rt.v), {color: col[rt.v], weight: 2.5, opacity: .45, dashArray: '6 6'}).addTo(m);
      doneL[rt.v] = L.polyline([], {color: col[rt.v], weight: 4, opacity: .95}).addTo(m);
    });
    D.plan.forEach(rt => rt.stops.forEach(st => dots.push([st, orderDot(m, st.k)])));
    D.plan.forEach(rt => pin[rt.v] = L.marker(D.office, {zIndexOffset: 1000, icon: L.divIcon({className: '', iconSize: [22, 22],
      html: `<div class="pin" style="background:${col[rt.v]}">${B(rt.v)}</div>`})}).addTo(m));
    let tm = null;
    const draw = () => {
      const t = +r.value;
      D.plan.forEach(rt => { const st = stateAt(rt, t); doneL[rt.v].setLatLngs(st.path); pin[rt.v].setLatLng(st.ll); });
      dots.forEach(([st, d]) => d.setStyle({fillOpacity: st.end <= t ? .3 : 1, opacity: st.end <= t ? .4 : 1}));
      p.innerHTML = playPanels(t);
    };
    r.oninput = draw; draw();
    b.onclick = () => { if (tm) { clearInterval(tm); tm = null; b.textContent = 'Пуск'; return; }
      b.textContent = 'Пауза'; tm = setInterval(() => { r.value = (+r.value + 2) % 722; draw(); }, 50); };
  }
});

// Варианты 7–10
const EV = D.event, GONE = EV.gone, T0 = EV.t0;
const fixedN = D.plan.reduce((a, r) => a + r.stops.filter(s => s.beg < T0).length, 0);
const pad = (s, n) => String(s).padEnd(n), lpad = (s, n) => String(s).padStart(n);
VARIANTS.push({
  short: 'Терминал', name: 'Только терминал',
  idea: 'Вывод самого CLI: таблица бригад, метрики против базового варианта, перепланирование отдельной командой. Честно показывает, что у нас stateless CLI, но жюри просит «понятно без кода», поэтому годится только как дополнение к html.',
  how: 'это и есть CLI', cost: 'низкая',
  render: () => {
    const c = (cl, t) => `<span class="${cl}">${t}</span>`;
    let s = c('c-cmd', '$ lct plan data/vostok.csv --brigades data/vostok_brigades.csv -o out/plan.xlsx --html out/plan.html') + '\n';
    s += `Загружено: ${O.length} заявок, ${D.brigades.length} бригад, участок Восток\n` + c('c-dim', 'Матрицы: OSRM (машина, велосипед, пешком), общественный транспорт — оценка') + '\n';
    s += `Решатель s60_coop, 1,0 с · validate: ${c('c-ok', 'нарушений нет')} · нижняя граница: ${c('c-hi', 'меньше 6 бригад невозможно')}\n\n`;
    s += c('c-dim', ' ' + pad('Бригада', 9) + pad('Транспорт', 16) + pad('Навыки', 12) + lpad('Заявок', 7) + '   ' + pad('Работа', 14) + lpad('Км', 7)) + '\n';
    D.plan.forEach(r => s += ' ' + pad(B(r.v), 9) + pad(TR[r.mode], 16) + pad(sk(r.mask).map(x => x[0]).join(' '), 12) + lpad(r.stops.length, 7) + '   ' + pad(fmt(r.stops[0].beg) + '–' + fmt(r.stops.at(-1).end), 14) + lpad(num(r.km), 7) + '\n');
    s += ' ' + pad('Итого', 37) + lpad(66, 7) + '   ' + pad('', 14) + lpad(num(km(D.plan)), 7) + '\n\n';
    s += c('c-dim', ' ' + pad('', 18) + lpad('Наш план', 10) + lpad('Базовый ТЗ', 13)) + '\n';
    s += ' ' + pad('Бригад', 18) + lpad(6, 10) + lpad(12, 13) + '\n ' + pad('Пробег, км', 18) + lpad(num(km(D.plan)), 10) + lpad(num(km(D.base)), 13) + '\n ' + pad('Выполнено', 18) + lpad('66/66', 10) + lpad(SERVED_BASE + '/66', 13) + '\n';
    s += c('c-dim', 'Файлы: out/plan.xlsx, out/plan.html') + '\n\n';
    s += c('c-cmd', `$ lct replan out/plan.xlsx --at ${fmt(T0)} --unavailable ${GONE + 1} -o out/plan_v2.xlsx --html out/plan_v2.html`) + '\n';
    s += `Зафиксировано: ${fixedN} заявок выполнены или начаты до ${fmt(T0)}\n${B(GONE)} недоступна: ${EV.changes.length} неначатых заявок к переназначению\n\n`;
    s += c('c-dim', ' ' + pad('Заявка', 9) + pad('Тип', 13) + pad('Район', 15) + pad('Было', 12) + pad('Стало', 12) + 'Примечание') + '\n';
    EV.changes.forEach(ch => s += ' ' + pad(O[ch.k].id, 9) + pad(O[ch.k].type, 13) + pad(O[ch.k].district, 15) + pad(B(ch.was) + ' ' + fmt(ch.old), 12) + pad(B(ch.now) + ' ' + fmt(ch.new), 12) + (ch.fresh ? c('c-hi', 'бригада выведена из резерва') : `+${num(ch.dkm)} км`) + '\n');
    s += `\nИтог: ${EV.after.length} бригад (${c('c-bad', '+' + (EV.after.length - 6))}), ${num(km(EV.after))} км (${c('c-bad', '+' + num(km(EV.after) - km(D.plan)))}), все заявки назначены`;
    return `<div class="app" style="height:780px;background:#2A2C31;padding:26px"><div class="term"><div class="tbar"><i></i><i></i><i></i><span>lct — zsh</span></div><pre>${s}</pre></div></div>`;
  }
});

VARIANTS.push({
  short: 'Excel', name: 'Excel-книга',
  idea: 'Результат — xlsx: лист «План» с маршрутами и шкалой из закрашенных ячеек, листы метрик, неназначенных и изменений. Диспетчер вписывает событие в лист «События» и запускает replan. Карты нет, поэтому только в паре с html.',
  how: 'openpyxl, условное форматирование', cost: 'средняя',
  render: () => {
    const rows = D.plan.slice(0, 2).flatMap(r => r.stops.map((s, i) => ({r, s, i})));
    const H = [...Array(12).keys()];
    const cell = (t, st = '') => `<td style="${st}">${t}</td>`;
    const letters = 'ABCDEFGHIJKLMNOPQRSTU'.split('');
    return `<div class="app xl" style="height:780px;background:#fff">
    <div style="height:40px;background:#1E6B41;color:#fff;display:flex;align-items:center;gap:18px;padding:0 16px;font-weight:600">plan.xlsx<span style="font-weight:400;opacity:.85">Главная · Вставка · Данные · Вид</span></div>
    <div style="display:flex;border-bottom:1px solid #D4D4D4;font-size:12px"><div style="width:70px;padding:5px 8px;border-right:1px solid #D4D4D4">D5</div><div style="padding:5px 8px" class="muted">fx&nbsp;&nbsp;${O[rows[3].s.k].id}</div></div>
    <div style="overflow:hidden;height:676px"><table class="xt"><tr><th></th>${letters.map(l => `<th>${l}</th>`).join('')}</tr>
    <tr><th>1</th>${['Бригада', 'Транспорт', '№', 'Заявка', 'Тип', 'Район', 'Окно', 'Приезд', 'Начало', 'Конец'].map(h => cell(h, 'font-weight:600;background:#F2F2F2')).join('')}${H.map(h => cell(fmt(h * 60).slice(0, 2), 'font-weight:600;background:#F2F2F2;text-align:center')).join('')}</tr>
    ${rows.map(({r, s, i}, n) => `<tr><th>${n + 2}</th>${cell(B(r.v))}${cell(TR[r.mode])}${cell(i + 1)}${cell(O[s.k].id, n === 3 ? 'outline:2px solid #1E6B41;outline-offset:-2px' : '')}${cell(O[s.k].type)}${cell(O[s.k].district)}${cell(win(O[s.k]))}${cell(fmt(s.arr))}${cell(fmt(s.beg))}${cell(fmt(s.end))}
      ${H.map(h => cell('', s.beg < h * 60 + 60 && s.end > h * 60 ? `background:${col[r.v]}` : '')).join('')}</tr>`).join('')}
    </table></div>
    <div style="height:32px;border-top:1px solid #D4D4D4;display:flex;font-size:12px">${['План', 'Бригады', 'Метрики', 'Неназначенные', 'События', 'Изменения'].map((t, i) => `<span style="padding:7px 16px;border-right:1px solid #D4D4D4;${i === 0 ? 'background:#fff;color:#1E6B41;font-weight:600;border-bottom:2px solid #1E6B41' : 'background:#F2F2F2'}">${t}</span>`).join('')}</div></div>`;
  }
});

function explainCard(k) {
  const q = O[k], e = D.explain[k], w = owner[k], r = D.plan.find(x => x.v === w.v);
  const g = {skill: [], idle: [], time: [], ok: []};
  e.others.forEach(x => g[x.why].push(x));
  const skn = SKN[SKILL[q.type]];
  const line = (t, b) => `<div style="padding:9px 0;border-top:1px solid var(--line);line-height:1.45"><b>${t}</b> — ${b}</div>`;
  return `<div style="padding:16px 18px"><div style="display:flex;gap:8px;align-items:center">${badge(q.type)}<span class="muted">заявка ${q.id}</span></div>
    <div style="font-size:18px;font-weight:600;margin:8px 0 2px">${q.addr}</div><div class="muted">${q.district} · окно начала ${win(q)} · работа ${q.svc} мин</div>
    <div style="margin:16px 0;padding:14px;border-radius:9px;background:#EEF3FB;border:1px solid #CBD9EF;line-height:1.5">
      <div style="display:flex;align-items:center;gap:8px;font-size:15px"><span class="sw" style="background:${col[w.v]}"></span><b>Назначена ${B(w.v)}</b><span class="muted">· ${TR[r.mode]}</span></div>
      <div>Приедет в ${fmt(w.arr)}, начнёт в ${fmt(w.beg)}${w.beg > w.arr + 1 ? ' (ждёт начала окна)' : ''}, закончит в ${fmt(w.end)}.</div>
      <div>Добавляет к маршруту бригады ${num(e.dkm)} км — меньше, чем у любой другой бригады, которая успевает.</div></div>
    <div style="font-weight:600;margin-bottom:2px">Почему не другие</div>
    ${g.ok.length ? line(g.ok.map(x => B(x.v)).join(', '), 'тоже успевают, но ' + g.ok.map(x => `${B(x.v)}: +${num(x.dkm)} км`).join(', ')) : ''}
    ${g.time.length ? line(g.time.map(x => B(x.v)).join(', '), 'навык есть, но при своём маршруте не успевают начать в окне ' + win(q)) : ''}
    ${g.idle.length ? line(g.idle.map(x => B(x.v)).join(', '), 'свободны, но пришлось бы выводить ещё одну бригаду') : ''}
    ${g.skill.length ? line(g.skill.map(x => B(x.v)).join(', '), `нет навыка «${skn}»`) : ''}</div>`;
}
function explainMap(k) {
  const w = owner[k].v, cand = D.explain[k].others.filter(x => x.why === 'ok' || x.why === 'time').map(x => x.v);
  return mapSVG(520, 700, {pad: 36,
    routes: D.plan.filter(r => r.v === w || cand.includes(r.v)).map(r => ({pts: routePts(r), color: col[r.v], dash: r.v !== w, op: r.v === w ? .95 : .5, w: r.v === w ? 3 : 2})),
    dot: j => owner[j].v === w ? {c: col[w]} : cand.includes(owner[j].v) ? {c: col[owner[j].v], op: .5} : {c: '#B9B3A8', op: .5}, hi: k});
}
const K0 = O.findIndex(q => q.type === 'Авария' && D.explain[O.indexOf(q)] && D.explain[O.indexOf(q)].others.some(x => x.why === 'time'));
VARIANTS.push({
  short: 'Заявка', name: 'Заявка в центре',
  idea: 'Список заявок, по клику — карточка «почему эта бригада и почему не другие». Объяснения настоящие: проверка вставки заявки в маршрут каждой бригады. Закрывает шаг 4 демо. Кликните любую заявку в списке.',
  how: 'plan.html, объяснения считает CLI', cost: 'средняя',
  render: () => {
    const list = D.plan.flatMap(r => r.stops.map(s => s.k)).sort((a, b) => owner[a].beg - owner[b].beg);
    return `<div class="app" style="height:760px;display:grid;grid-template-columns:330px 520px 1fr">
    <div style="border-right:1px solid var(--line);background:var(--surface);overflow-y:auto">
      <div style="padding:14px 14px 8px;font-weight:600">Заявки · 66</div>
      ${list.map(k => `<button class="orow" data-k="${k}"><span style="display:flex;justify-content:space-between;gap:6px"><b>${fmt(owner[k].beg)}</b>${badge(O[k].type)}</span>
        <span class="muted" style="font-size:12px">${O[k].district} · окно ${win(O[k])}</span><span class="chip" style="background:${col[owner[k].v]}">${B(owner[k].v)}</span></button>`).join('')}</div>
    <div class="xmap">${explainMap(K0)}</div><div class="xcard" style="background:var(--surface);border-left:1px solid var(--line);overflow-y:auto">${explainCard(K0)}</div></div>`;
  },
  mount: s => {
    const pick = k => { s.querySelector('.xmap').innerHTML = explainMap(k); s.querySelector('.xcard').innerHTML = explainCard(k);
      s.querySelectorAll('.orow').forEach(b => b.classList.toggle('on', +b.dataset.k === k)); };
    s.querySelectorAll('.orow').forEach(b => b.onclick = () => pick(+b.dataset.k)); pick(K0);
  }
});

VARIANTS.push({
  short: 'Журнал', name: 'Журнал версий плана',
  idea: 'Каждый запуск CLI — новая версия плана. Слева версии и события, в центре карта различий (старое пунктиром, новое сплошным), справа таблица «было → стало». Естественно ложится на stateless CLI и закрывает шаги 5–6 демо.',
  how: 'plan_v1.xlsx → plan_v2.xlsx, html показывает разницу', cost: 'средняя',
  render: () => {
    const gone = D.plan.find(r => r.v === GONE), recv = new Set(EV.changes.map(c => c.now));
    const i0 = gone.stops.findIndex(s => s.beg >= T0);
    const oldTail = [i0 > 0 ? LL(gone.stops[i0 - 1].k) : D.office].concat(gone.stops.slice(i0).map(s => LL(s.k)));
    const aft = Object.fromEntries(EV.after.map(r => [r.v, r]));
    const routes = EV.after.filter(r => !recv.has(r.v)).map(r => ({pts: r.t0 ? [D.office].concat(r.stops.map(s => LL(s.k))) : routePts(r), color: '#B9B3A8', w: 1.6, op: .8}))
      .concat([{pts: oldTail, color: col[GONE], dash: true, w: 3}])
      .concat([...recv].map(v => ({pts: routePts(aft[v]), color: col[v], w: 3})));
    const moved = new Set(EV.changes.map(c => c.k));
    const ver = (t, h, b, on) => `<div style="padding:14px 16px;border-top:1px solid var(--line);${on ? 'background:#EEF3FB;box-shadow:inset 3px 0 0 var(--accent)' : ''}"><div class="muted" style="font-size:12px">${t}</div><div style="font-weight:600;margin:2px 0">${h}</div><div class="muted" style="line-height:1.45">${b}</div></div>`;
    return `<div class="app" style="height:760px;display:grid;grid-template-columns:290px 600px 1fr">
    <div style="background:var(--surface);border-right:1px solid var(--line)"><div style="padding:14px 16px;font-weight:600">Версии плана</div>
      ${ver('v1 · 10:00 · plan.xlsx', 'Утренний план', `6 бригад · ${num(km(D.plan))} км · 66 заявок`)}
      ${ver(`v2 · ${fmt(T0)} · plan_v2.xlsx`, `${B(GONE)} недоступна`, `${EV.changes.length} заявок переназначены · ${EV.after.length} бригад · ${num(km(EV.after))} км`, 1)}
      <div style="padding:14px 16px"><button class="btn" style="width:100%">Новое событие…</button></div></div>
    <div>${mapSVG(600, 760, {pad: 40, routes, dot: k => moved.has(k) ? {c: col[EV.changes.find(c => c.k === k).now]} : {c: '#B9B3A8', op: .7}, rings: [...moved]})}</div>
    <div style="background:var(--surface);border-left:1px solid var(--line);padding:16px 18px;overflow-y:auto">
      <div style="font-weight:600;font-size:15px">Что изменилось в v2</div>
      <div class="muted" style="margin:4px 0 12px;line-height:1.45">${fixedN} заявок, выполненных или начатых до ${fmt(T0)}, не трогаем. Остальные заявки ${B(GONE)} раздаём другим бригадам.</div>
      <table class="t"><tr><th>Заявка</th><th>Было</th><th>Стало</th></tr>
      ${EV.changes.map(c => `<tr><td>${badge(O[c.k].type)}<div class="muted" style="font-size:12px;margin-top:3px">${O[c.k].district}</div></td><td>${B(c.was)} ${fmt(c.old)}</td><td><b>${B(c.now)} ${fmt(c.new)}</b><div class="muted" style="font-size:12px">${c.fresh ? 'из резерва' : '+' + num(c.dkm) + ' км'}</div></td></tr>`).join('')}</table>
      <div style="margin-top:14px;padding:12px;border-radius:8px;background:var(--bg);line-height:1.5">Бригад: 6 → <b>${EV.after.length}</b><br>Пробег: ${num(km(D.plan))} → <b>${num(km(EV.after))} км</b><br>Неназначенных: 0</div>
      <div class="muted" style="margin-top:12px;font:12px var(--mono)">lct replan plan.xlsx --at ${fmt(T0)} --unavailable ${GONE + 1}</div></div></div>`;
  }
});
