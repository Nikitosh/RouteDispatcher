// Страница диспетчера: читает manifest.json и plan.json, которые пишет `lct site` (plan.json — результат lct plan / lct replan).
const PAL = ['#1F5FBF', '#B4530A', '#0F766E', '#6D3FB0', '#8A6A00', '#A8324E', '#2F6B2F', '#4E5BA6', '#8C4A2F', '#1D6F8C', '#7A2E7A', '#55606E', '#9C6B00', '#2E5E4E', '#6B3A1F', '#3B4CA8'];
const TC = {'Авария': 'ta', 'Подключение': 'tc', 'Ремонт': 'tl', 'Дозаказ': 'td'};
const TS = {'Авария': 'Авар', 'Подключение': 'Подкл', 'Ремонт': 'Рем', 'Дозаказ': 'Доз'};
const fmt = m => { m = Math.round(m) + 600; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(((m % 60) + 60) % 60).padStart(2, '0'); };
const km1 = x => (Math.round(x * 10) / 10).toFixed(1).replace('.', ',');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const badge = t => `<span class="badge ${TC[t] || 'tl'}">${esc(t)}</span>`;
const win = o => `${fmt(o.window[0])}–${fmt(o.window[1])}`;
const col = v => PAL[v % PAL.length];
const $ = id => document.getElementById(id);

let MAN, D, E = null, tab = 'plan', sel = null, hl = null, map, layer, playTimer = null;

async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); }

async function init() {
  MAN = await getJSON('manifest.json');
  $('district').innerHTML = MAN.districts.map((d, i) => `<option value="${i}">${esc(d.name)}</option>`).join('');
  $('district').onchange = () => loadDistrict(+$('district').value);
  $('event').onchange = () => loadEvent(+$('event').value);
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => { if (!b.disabled) setTab(b.dataset.tab); });
  map = L.map('map', {scrollWheelZoom: false, zoomSnap: 0.25});
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');   // без значка-флага по умолчанию
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; участники OpenStreetMap'}).addTo(map);
  map.on('click', () => map.scrollWheelZoom.enable());
  layer = L.layerGroup().addTo(map);
  $('play-range').oninput = () => drawPlay();
  $('play-btn').onclick = togglePlay;
  const h = new URLSearchParams(location.hash.slice(1));
  await loadDistrict(Math.max(0, MAN.districts.findIndex(d => d.slug === h.get('d'))));
  if (h.get('tab')) setTab(h.get('tab'));
}

async function loadDistrict(i) {
  const d = MAN.districts[i]; $('district').value = i;
  D = await getJSON(d.plan); E = null; sel = null; hl = null;
  $('date').textContent = D.meta.date ? `на ${D.meta.date}` : '';
  const ev = d.events || [];
  $('event').innerHTML = ev.map((e, j) => `<option value="${j}">${esc(e.label)}</option>`).join('');
  $('tab-event').disabled = !ev.length;
  if (ev.length) await loadEvent(0, false);
  if (tab === 'event' && !ev.length) tab = 'plan';
  syncTabs(); fitAll(); render();
}

async function loadEvent(j, rerender = true) {
  E = await getJSON(MAN.districts[+$('district').value].events[j].plan);
  sel = null; if (rerender) { if (tab !== 'event') setTab('event'); else render(); }
}

// Подсветка вкладки; выбор события нужен только на вкладке «Изменения».
function syncTabs() {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $('event-sel-wrap').classList.toggle('hidden', tab !== 'event');
}

function setTab(t) {
  tab = t; sel = null; stopPlay();
  syncTabs();
  location.hash = `d=${MAN.districts[+$('district').value].slug}&tab=${t}`;
  render();
}

const cur = () => (tab === 'event' && E) ? E : D;

function fitAll() {
  const O = D.orders; const pts = O.map(o => [o.lat, o.lon]).concat([[D.office.lat, D.office.lon]]);
  map.fitBounds(L.latLngBounds(pts), {padding: [30, 30]});
}

function routeLine(R, rt) {
  if (rt.geometry) return rt.geometry.flat();
  const b = R.brigades[rt.v];
  return [[b.lat, b.lon]].concat(rt.stops.map(s => [R.orders[s.k].lat, R.orders[s.k].lon]));
}

// ---------- отрисовка
function render() {
  renderKpis(); renderMap(); renderPanel(); renderGantt(); renderBottom();
  const play = tab === 'play';
  $('play-bar').classList.toggle('hidden', !play); $('play-panel').classList.toggle('hidden', !play);
  if (play) drawPlay();
}

function kpi(l, v, d, cls = 'good') { return `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="d ${cls}">${d}</div></div>`; }

function renderKpis() {
  const R = cur(), m = R.plan.metrics, b = D.baseline.metrics;
  if (tab === 'event' && E) {
    const e = E.event, bf = e.before;
    const dk = m.km_total - bf.km_total;
    $('kpis').innerHTML = kpi('Событие', fmt(e.at), eventTitle(e), 'muted')
      + kpi('Бригад', `${bf.brigades_used} → ${m.brigades_used}`, m.brigades_used > bf.brigades_used ? 'выведена бригада из резерва' : 'без новых бригад', m.brigades_used > bf.brigades_used ? 'bad' : 'good')
      + kpi('Пробег', `${km1(m.km_total)} км`, `${dk >= 0 ? '+' : ''}${km1(dk)} км к утреннему плану`, dk > 0 ? 'bad' : 'good')
      + kpi('Изменений', e.changes.length, `зафиксировано выполненных и начатых: ${e.fixed}`, 'muted')
      + kpi('Неназначенных', m.unassigned, m.unassigned ? 'см. список ниже' : 'все заявки назначены', m.unassigned ? 'bad' : 'good');
    return;
  }
  const lb = D.lower_bound;
  $('kpis').innerHTML = kpi('Бригад задействовано', m.brigades_used, `базовый вариант: ${b.brigades_used}`)
    + kpi('Пробег, всего', `${km1(m.km_total)} км`, `базовый: ${km1(b.km_total)} км`)
    + kpi('Выполнено заявок', `${m.served} из ${m.orders}`, `базовый: ${b.served} из ${b.orders}`)
    + kpi('Неназначенных', m.unassigned, `базовый: ${b.unassigned}`, m.unassigned ? 'bad' : 'good')
    + (lb != null ? kpi('Нижняя граница', `${lb} бригад`, lb === m.brigades_used ? 'меньше невозможно — доказано' : `план: ${m.brigades_used}`, lb === m.brigades_used ? 'good' : 'muted') : '');
}

function eventTitle(e) {
  const O = E.orders;
  if (e.kind === 'unavailable') return `${esc(e.name)} недоступна`;
  if (e.kind === 'cancel') return `отменена заявка ${esc(O[e.order].id)}`;
  return `новая заявка: ${esc((e.type || '').toLowerCase())}`;
}

function renderMap() {
  layer.clearLayers();
  if (tab === 'play') return;
  const R = cur(), O = R.orders;
  const changed = new Set(tab === 'event' && E ? E.event.changes.map(c => c.k) : []);
  const chgBr = new Set(tab === 'event' && E ? E.event.changes.flatMap(c => [c.was && c.was.brigade, c.now && c.now.brigade]).filter(x => x != null) : []);
  if (tab === 'event' && E) {                              // утренние маршруты затронутых бригад — пунктиром
    D.plan.routes.filter(rt => chgBr.has(rt.v)).forEach(rt =>
      L.polyline(routeLine(D, rt), {color: col(rt.v), weight: 3, opacity: .55, dashArray: '6 7'}).addTo(layer));
  }
  R.plan.routes.forEach(rt => {
    const focus = hl == null || hl === rt.v, dim = tab === 'event' && E && !chgBr.has(rt.v);
    L.polyline(routeLine(R, rt), {color: dim ? '#9A958C' : col(rt.v), weight: focus ? (hl === rt.v ? 6 : 3.5) : 2, opacity: focus ? (dim ? .5 : .9) : .15}).addTo(layer);
  });
  const owner = {}; R.plan.routes.forEach(rt => rt.stops.forEach(s => owner[s.k] = rt.v));
  O.forEach((o, k) => {
    if (o.cancelled) return;
    const v = owner[k], av = o.type === 'Авария', un = v == null;
    const m = L.circleMarker([o.lat, o.lon], {radius: av ? 7 : 5, color: un ? '#B42318' : av ? '#B42318' : '#fff', weight: un ? 3 : av ? 2.5 : 1.5,
      fillColor: un ? '#fff' : col(v), fillOpacity: (hl == null || hl === v) ? 1 : .3}).addTo(layer);
    m.bindTooltip(`<b>${esc(o.id)} ${esc(o.type)}</b><br>${esc(o.district)}, окно ${win(o)}` + (o.plan ? `<br>${esc(R.brigades[v].name)}, начало ${fmt(o.plan.start)}` : '<br>не назначена'));
    m.on('click', e => { L.DomEvent.stopPropagation(e); select(k); });
    if (changed.has(k) || sel === k) L.circleMarker([o.lat, o.lon], {radius: sel === k ? 14 : 11, color: '#1B1A18', weight: 2.5, fill: false, interactive: false}).addTo(layer);
  });
  L.marker([D.office.lat, D.office.lon], {icon: L.divIcon({className: '', html: '<div class="office"></div>', iconSize: [14, 14]})})
    .bindTooltip('Офис: ' + esc(D.office.addr)).addTo(layer);
}

function select(k) { sel = k; const o = cur().orders[k]; if (o) map.panTo([o.lat, o.lon]); renderMap(); renderPanel(); renderGantt(); }

function renderPanel() {
  const R = cur(), O = R.orders, B = R.brigades;
  if (tab === 'play') { $('panel').innerHTML = `<div class="ptitle">Проигрывание дня</div><div style="padding:0 14px 14px" class="muted">Бригады едут по своим маршрутам, пройденная часть — сплошной линией. Двигайте ползунок или нажмите «Пуск».</div>`; return; }
  if (sel != null) { $('panel').innerHTML = orderCard(R, sel); $('back').onclick = () => { sel = null; renderMap(); renderPanel(); renderGantt(); }; return; }
  if (tab === 'event' && E) {
    const e = E.event;
    $('panel').innerHTML = `<div class="ptitle"><span>Что изменилось в ${fmt(e.at)}</span></div>
      <div style="padding:0 14px 8px" class="muted">${eventTitle(e)}. Выполненные и начатые заявки не трогаем.${e.accident ? ` Авария начнётся в ${fmt(e.accident.start)} — через ${Math.round(e.accident.wait)} мин.` : ''}</div>
      <table class="t"><tr><th>Заявка</th><th>Было</th><th>Стало</th></tr>
      ${e.changes.map(c => `<tr class="click" data-k="${c.k}"><td>${badge(O[c.k].type)} ${esc(O[c.k].id)}<div class="muted" style="font-size:12px">${esc(c.why)}</div></td>
        <td>${c.was ? esc(B[c.was.brigade].name) + ' ' + fmt(c.was.start) : '—'}</td><td><b>${c.now ? esc(B[c.now.brigade].name) + ' ' + fmt(c.now.start) : 'не назначена'}</b></td></tr>`).join('') || '<tr><td colspan="3" class="muted">Изменений нет</td></tr>'}</table>`;
    $('panel').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => select(+tr.dataset.k));
    return;
  }
  const used = R.plan.routes;
  $('panel').innerHTML = `<div class="ptitle"><span>Бригады в работе</span><span class="muted">${used.length} из ${B.length}</span></div>`
    + used.map(rt => { const b = B[rt.v]; return `<button class="brow" data-v="${rt.v}"><span class="sw" style="background:${col(rt.v)}"></span><span class="n">${esc(b.name)}</span>
      <span class="x">${esc(b.mode_name)}; ${esc(b.skills.join(', '))}</span><span>${rt.stops.length}</span><b style="min-width:62px;text-align:right">${km1(rt.km)} км</b></button>`; }).join('')
    + `<div class="brow muted" style="cursor:default;font-size:12px">В резерве: ${esc(B.filter(b => !used.find(r => r.v === b.v)).map(b => b.name).join(', ') || '—')}</div>`;
  $('panel').querySelectorAll('button.brow').forEach(b => {
    b.onmouseenter = () => { hl = +b.dataset.v; renderMap(); };
    b.onmouseleave = () => { hl = null; renderMap(); };
    b.onclick = () => { const rt = used.find(r => r.v === +b.dataset.v); map.fitBounds(L.latLngBounds(routeLine(R, rt)), {padding: [40, 40]}); };
  });
}

function orderCard(R, k) {
  const o = R.orders[k], B = R.brigades, p = o.plan, e = o.explanation;
  let h = `<div class="ocard"><button class="linkbtn" id="back">← к списку</button>
    <div style="margin-top:8px">${badge(o.type)} <span class="muted">заявка ${esc(o.id)}</span></div>
    <h3>${esc(o.addr.replace(/^Город Москва, /, ''))}</h3><div class="muted">${esc(o.district)}; окно начала ${win(o)}; работа ${o.svc} мин${o.need ? '; только ' + esc(o.need.map(x => ({car: 'машина', pt: 'общ. транспорт', bike: 'велосипед', foot: 'пешком'}[x])).join(' или ')) : ''}</div>`;
  if (p) {
    const b = B[p.brigade];
    h += `<div class="assign"><div style="display:flex;align-items:center;gap:8px;font-size:15px"><span class="sw" style="background:${col(p.brigade)}"></span><b>${esc(b.name)}</b><span class="muted">${esc(b.mode_name)}</span></div>
      <div>Приезд ${fmt(p.arrive)}, начало ${fmt(p.start)}, конец ${fmt(p.end)}.</div>${e ? `<div style="margin-top:4px">${esc(e.summary.replace(/^[^:]+: /, ''))}</div>` : ''}</div>`;
    if (e && e.others && e.others.length) h += `<div style="font-weight:600">Почему не другие</div>` + e.others.map(g => `<div class="why"><b>${esc(g.names)}</b> — ${esc(g.text)}</div>`).join('');
  } else {
    h += `<div class="assign none"><b>${o.cancelled ? 'Отменена' : 'Не назначена'}</b><div>${esc(o.reason ? o.reason.text : '')}</div></div>`;
  }
  return h + '</div>';
}

function renderGantt() {
  const R = cur();
  if (tab === 'play') { $('gantt-card').classList.add('hidden'); return; }
  $('gantt-card').classList.remove('hidden');
  const P = m => `${m / 7.2}%`, chg = new Set(tab === 'event' && E ? E.event.changes.map(c => c.k) : []);
  let s = `<div class="gantt"><div class="gaxis">${[...Array(13).keys()].map(i => `<span class="tick" style="left:calc(190px + (100% - 204px) * ${i / 12})">${fmt(i * 60)}</span>`).join('')}</div>`;
  R.plan.routes.forEach(rt => {
    const b = R.brigades[rt.v]; let prev = 0;
    s += `<div class="grow2"><div class="glabel"><span class="sw" style="background:${col(rt.v)}"></span><b>${esc(b.name)}</b><small>${esc(b.mode_name)}; ${km1(rt.km)} км</small></div><div class="track">`;
    rt.stops.forEach(st => {
      const o = R.orders[st.k], dep = Math.max(prev, st.arr - (st.arr - prev));
      if (st.arr > prev) s += `<i class="travel" style="left:${P(prev)};width:${P(st.arr - prev)};background:${col(rt.v)}"></i>`;
      if (st.beg > st.arr + 1) s += `<i class="wait" style="left:${P(st.arr)};width:${P(st.beg - st.arr)}"></i>`;
      const dur = st.end - st.beg;
      s += `<i class="work${o.type === 'Авария' ? ' avar' : ''}${sel === st.k ? ' sel' : ''}${chg.has(st.k) ? ' chg' : ''}" data-k="${st.k}" title="${fmt(st.beg)}–${fmt(st.end)} ${esc(o.type)}, ${esc(o.district)}" style="left:${P(st.beg)};width:${P(dur)};background:${col(rt.v)}">${dur >= 30 ? TS[o.type] || '' : ''}</i>`;
      prev = st.end;
    });
    s += '</div></div>';
  });
  if (tab === 'event' && E) s += `<div class="nowline" style="left:calc(190px + (100% - 204px) * ${E.event.at / 720})"></div>`;
  $('gantt').innerHTML = s + '</div>';
  $('gantt').querySelectorAll('.work').forEach(w => w.onclick = () => select(+w.dataset.k));
}

function renderBottom() {
  const R = cur(), O = R.orders, bs = D.baseline;
  const un = O.map((o, k) => ({o, k})).filter(x => !x.o.plan && !x.o.cancelled);
  $('unassigned').innerHTML = `<div class="ptitle"><span>Неназначенные заявки</span><span class="muted">${un.length}</span></div>`
    + (un.length ? `<table class="t"><tr><th>Заявка</th><th>Окно</th><th>Причина</th></tr>${un.map(x => `<tr class="click" data-k="${x.k}"><td>${badge(x.o.type)} ${esc(x.o.id)}<div class="muted" style="font-size:12px">${esc(x.o.district)}</div></td><td>${win(x.o)}</td><td>${esc(x.o.reason ? x.o.reason.text : '')}</td></tr>`).join('')}</table>`
      : `<div style="padding:0 14px 12px">Все заявки назначены.</div>`)
    + (tab === 'plan' && bs.unassigned.length ? `<details style="padding:0 14px 12px"><summary class="muted">Базовый вариант не назначил бы ${bs.unassigned.length}</summary>
      <table class="t">${bs.unassigned.slice(0, 40).map(u => `<tr><td>${badge(D.orders[u.k].type)} ${esc(D.orders[u.k].id)}</td><td>${esc(u.text)}</td></tr>`).join('')}</table></details>` : '');
  $('unassigned').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => select(+tr.dataset.k));
  const ours = R.plan.routes, base = bs.routes, mx = Math.max(...ours.map(r => r.km), ...base.map(r => r.km), 1);
  const bars = (rs) => `<div class="bars">${rs.map(r => `<div><b>${Math.round(r.km)}</b><span class="b" style="height:${r.km / mx * 95}px;background:${col(r.v)}"></span><span class="muted">${esc(r.brigade.replace('Бригада ', 'Б'))}</span></div>`).join('')}</div>`;
  const m = R.plan.metrics, b = bs.metrics;
  $('compare').innerHTML = `<div class="ptitle"><span>Сравнение с базовым вариантом ТЗ</span></div>
    <table class="t"><tr><th></th><th>Наш план</th><th>Базовый</th></tr>
    <tr><td>Бригад</td><td><b>${m.brigades_used}</b></td><td>${b.brigades_used}</td></tr>
    <tr><td>Пробег</td><td><b>${km1(m.km_total)} км</b></td><td>${km1(b.km_total)} км</td></tr>
    <tr><td>Выполнено</td><td><b>${m.served} из ${m.orders}</b></td><td>${b.served} из ${b.orders}</td></tr></table>
    <div class="ptitle" style="font-size:13px"><span>Пробег по исполнителям: наш план</span></div>${bars(ours)}
    <div class="ptitle" style="font-size:13px"><span>Базовый вариант</span></div>${bars(base)}`;
}

// ---------- проигрывание
function cut(line, f) {
  if (f <= 0) return [line[0]]; if (f >= 1) return line;
  const d = [0]; for (let i = 1; i < line.length; i++) d.push(d[i - 1] + Math.hypot(line[i][0] - line[i - 1][0], (line[i][1] - line[i - 1][1]) * .56));
  const T = d[d.length - 1] * f; let i = 1; while (i < line.length && d[i] < T) i++;
  if (i >= line.length) return line; const g = (T - d[i - 1]) / ((d[i] - d[i - 1]) || 1);
  return line.slice(0, i).concat([[line[i - 1][0] + (line[i][0] - line[i - 1][0]) * g, line[i - 1][1] + (line[i][1] - line[i - 1][1]) * g]]);
}
function legsOf(R, rt) {
  if (rt.geometry) return rt.geometry;
  const b = R.brigades[rt.v]; const pts = [[b.lat, b.lon]].concat(rt.stops.map(s => [R.orders[s.k].lat, R.orders[s.k].lon]));
  return pts.slice(1).map((p, i) => [pts[i], p]);
}
function stateAt(R, rt, t) {
  const Lg = legsOf(R, rt); let pe = 0;
  for (let i = 0; i < rt.stops.length; i++) {
    const s = rt.stops[i], o = R.orders[s.k], q = [o.lat, o.lon];
    if (t < s.arr) { const dep = Math.max(pe, s.arr - (s.arr - pe)); const part = cut(Lg[i], Math.max(0, Math.min(1, (t - pe) / Math.max(1, s.arr - pe)))); return {path: Lg.slice(0, i).flat().concat(part), ll: part[part.length - 1], st: 'в пути', to: 'к ' + o.district}; }
    if (t < s.beg) return {path: Lg.slice(0, i + 1).flat(), ll: q, st: 'ждёт окно', to: o.district};
    if (t < s.end) return {path: Lg.slice(0, i + 1).flat(), ll: q, st: 'работает', to: `${o.type.toLowerCase()}, до ${fmt(s.end)}`};
    pe = s.end;
  }
  const last = R.orders[rt.stops[rt.stops.length - 1].k];
  return {path: Lg.flat(), ll: [last.lat, last.lon], st: 'закончила', to: ''};
}
function drawPlay() {
  const R = D, t = +$('play-range').value; layer.clearLayers();
  $('play-time').textContent = fmt(t);
  let done = 0; const rows = [];
  R.plan.routes.forEach(rt => {
    const st = stateAt(R, rt, t);
    L.polyline(routeLine(R, rt), {color: col(rt.v), weight: 2.5, opacity: .4, dashArray: '6 6'}).addTo(layer);
    L.polyline(st.path, {color: col(rt.v), weight: 4, opacity: .95}).addTo(layer);
    rt.stops.forEach(s => { const o = R.orders[s.k], fin = s.end <= t; done += fin;
      L.circleMarker([o.lat, o.lon], {radius: o.type === 'Авария' ? 7 : 5, color: o.type === 'Авария' ? '#B42318' : '#fff', weight: 1.5, fillColor: col(rt.v), fillOpacity: fin ? .3 : 1, opacity: fin ? .4 : 1}).addTo(layer); });
    L.marker(st.ll, {zIndexOffset: 1000, icon: L.divIcon({className: '', iconSize: [24, 24], html: `<div class="pin" style="background:${col(rt.v)}">${esc(R.brigades[rt.v].name.replace('Бригада ', 'Б'))}</div>`})}).addTo(layer);
    rows.push(`<div class="brow" style="cursor:default"><span class="sw" style="background:${col(rt.v)}"></span><span class="n">${esc(R.brigades[rt.v].name)}</span><span class="x"><b style="color:var(--ink)">${st.st}</b> ${esc(st.to)}</span></div>`);
  });
  L.marker([D.office.lat, D.office.lon], {icon: L.divIcon({className: '', html: '<div class="office"></div>', iconSize: [14, 14]})}).addTo(layer);
  $('play-panel').innerHTML = `<div class="card" style="padding:12px 16px;min-width:200px"><div style="font-size:32px;font-weight:700;line-height:1">${fmt(t)}</div><div style="margin-top:6px">Выполнено <b>${done}</b> из ${R.plan.metrics.served}</div></div><div class="grow"></div>`;
  $('panel').innerHTML = `<div class="ptitle">Где бригады в ${fmt(t)}</div>` + rows.join('');
}
function togglePlay() {
  if (playTimer) return stopPlay();
  $('play-btn').textContent = 'Пауза';
  playTimer = setInterval(() => { const r = $('play-range'); r.value = (+r.value + 2) % 722; drawPlay(); }, 50);
}
function stopPlay() { if (playTimer) clearInterval(playTimer); playTimer = null; $('play-btn').textContent = 'Пуск'; }

init().catch(e => { document.querySelector('main').innerHTML = `<div class="card" style="padding:20px">Не удалось загрузить данные: ${esc(e.message)}. Страницу нужно открывать через веб-сервер (например, <code>./lct.sh serve</code>).</div>`; });
