// Общие функции макетов: карта (SVG) и временная шкала. Данные в data.js (const D).
const O = D.orders;
const PAL = ['#1F5FBF', '#B4530A', '#0F766E', '#6D3FB0', '#8A6A00', '#A8324E', '#2F6B2F', '#4E5BA6'];
const col = {};
D.plan.forEach((r, i) => col[r.v] = PAL[i]);
let _ci = D.plan.length;
D.event.after.forEach(r => { if (!(r.v in col)) col[r.v] = PAL[_ci++]; });
const baseCol = v => `hsl(${(v * 137) % 360} 42% 38%)`;
const B = v => 'Б' + (v + 1);
const TR = {car: 'Авто', pt: 'Общ. транспорт', bike: 'Велосипед', foot: 'Пешком'};
const SKN = ['Ремонт', 'Подключение и дозаказ', 'Аварии'];
const SKILL = {'Ремонт': 0, 'Подключение': 1, 'Дозаказ': 1, 'Авария': 2};
const sk = m => SKN.filter((_, i) => (m >> i) & 1);
const TC = {'Авария': 'ta', 'Подключение': 'tc', 'Ремонт': 'tl', 'Дозаказ': 'td'};
const TS = {'Авария': 'Авар', 'Подключение': 'Подкл', 'Ремонт': 'Рем', 'Дозаказ': 'Доз'};
const fmt = m => { m = Math.round(m) + 600; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };
const num = x => x.toFixed(1).replace('.', ',');
const km = rs => rs.reduce((a, r) => a + r.km, 0);
const win = o => fmt(o.a) + '–' + fmt(o.wb);
const badge = t => `<span class="badge ${TC[t]}">${t}</span>`;
const owner = {};
D.plan.forEach(r => r.stops.forEach(s => owner[s.k] = {v: r.v, ...s}));
const baseOwner = {};
D.base.forEach(r => r.stops.forEach(s => baseOwner[s.k] = r.v));
const SERVED_BASE = O.length - D.base_unserved.length;

function proj(w, h, pad, pts) {
  pts = pts || O.map(o => [o.lat, o.lon]).concat([D.office]);
  const c = Math.cos(55.73 * Math.PI / 180);
  const xs = pts.map(p => p[1] * c), ys = pts.map(p => -p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const s = Math.min((w - 2 * pad) / (x1 - x0 || 1e-3), (h - 2 * pad) / (y1 - y0 || 1e-3));
  const ox = (w - (x1 - x0) * s) / 2, oy = (h - (y1 - y0) * s) / 2;
  return (lat, lon) => [ox + (lon * c - x0) * s, oy + (-lat - y0) * s];
}
const LL = k => [O[k].lat, O[k].lon];

// o.routes: [{pts:[[lat,lon]...], color, dash, op, w}], o.dot(k) -> {c, op} | null, o.hi, o.pos, o.unserved, o.rings
function mapSVG(w, h, o = {}) {
  const P = proj(w, h, o.pad ?? 28, o.fit);
  let s = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" class="map" aria-hidden="true">`;
  s += `<rect width="${w}" height="${h}" fill="var(--map)"/>`;
  for (let x = 24; x < w; x += 48) s += `<line x1="${x}" y1="0" x2="${x}" y2="${h}" stroke="var(--mapline)"/>`;
  for (let y = 24; y < h; y += 48) s += `<line x1="0" y1="${y}" x2="${w}" y2="${y}" stroke="var(--mapline)"/>`;
  if (o.districts !== false) {
    const g = {};
    O.forEach(q => (g[q.district] = g[q.district] || []).push(P(q.lat, q.lon)));
    for (const [d, ps] of Object.entries(g)) {
      const x = ps.reduce((a, p) => a + p[0], 0) / ps.length, y = ps.reduce((a, p) => a + p[1], 0) / ps.length;
      s += `<text x="${x}" y="${y - 14}" class="dist" text-anchor="middle">${d.toUpperCase()}</text>`;
    }
  }
  (o.routes || []).forEach(r => {
    const pts = r.pts.map(p => P(p[0], p[1]).join(',')).join(' ');
    s += `<polyline points="${pts}" fill="none" stroke="${r.color}" stroke-width="${r.w ?? 2.5}" stroke-opacity="${r.op ?? 0.9}" ${r.dash ? 'stroke-dasharray="6 5"' : ''} stroke-linejoin="round"/>`;
  });
  const dot = o.dot || (k => owner[k] ? {c: col[owner[k].v]} : {c: '#8a857c'});
  O.forEach((q, k) => {
    const d = dot(k); if (!d) return;
    const [x, y] = P(q.lat, q.lon), op = d.op ?? 1;
    if (q.type === 'Авария') s += `<rect x="${x - 5}" y="${y - 5}" width="10" height="10" transform="rotate(45 ${x} ${y})" fill="${d.c}" fill-opacity="${op}" stroke="#fff" stroke-width="1.5" stroke-opacity="${op}"/>`;
    else s += `<circle cx="${x}" cy="${y}" r="4.5" fill="${d.c}" fill-opacity="${op}" stroke="#fff" stroke-width="1.5" stroke-opacity="${op}"/>`;
  });
  (o.unserved || []).forEach(k => { const [x, y] = P(...LL(k)); s += `<rect x="${x - 6}" y="${y - 6}" width="12" height="12" fill="none" stroke="var(--bad)" stroke-width="2"/>`; });
  (o.rings || []).forEach(k => { const [x, y] = P(...LL(k)); s += `<circle cx="${x}" cy="${y}" r="10" fill="none" stroke="var(--ink)" stroke-width="2"/>`; });
  if (o.hi != null) { const [x, y] = P(...LL(o.hi)); s += `<circle cx="${x}" cy="${y}" r="14" fill="none" stroke="var(--ink)" stroke-width="2.5"/><circle cx="${x}" cy="${y}" r="20" fill="none" stroke="var(--ink)" stroke-opacity=".3" stroke-width="2"/>`; }
  const [ox, oy] = P(...D.office);
  s += `<rect x="${ox - 7}" y="${oy - 7}" width="14" height="14" fill="var(--ink)"/><text x="${ox + 11}" y="${oy + 4}" class="lbl">Офис</text>`;
  (o.pos || []).forEach(p => { const [x, y] = P(p.lat, p.lon); s += `<circle cx="${x}" cy="${y}" r="9" fill="${p.c}" stroke="#fff" stroke-width="2.5"/><text x="${x}" y="${y - 14}" class="lbl" text-anchor="middle">${p.label}</text>`; });
  return s + '</svg>';
}
const routePts = r => [D.office].concat(r.stops.map(s => LL(s.k)));

// o.rowH, o.labelW, o.windows, o.now, o.big (подписи в два ряда), o.color(v); w — px или '100%'
function gantt(w, routes, o = {}) {
  const LW = o.labelW ?? 170, rh = o.rowH ?? 32, C = o.color || (v => col[v]);
  const P = m => `${m / 7.2}%`, X = f => `calc(${LW}px + (100% - ${LW + 12}px) * ${f})`;
  let s = `<div class="gantt" style="width:${typeof w === 'number' ? w + 'px' : w}"><div class="gaxis">`;
  for (let i = 0; i <= 12; i++) s += `<span class="tick" style="left:${X(i / 12)}">${fmt(i * 60)}</span>`;
  s += '</div>';
  routes.forEach(r => {
    s += `<div class="grow" style="height:${rh}px"><div class="glabel" style="width:${LW}px"><span class="sw" style="background:${C(r.v)}"></span><b>${B(r.v)}</b><small>${TR[r.mode]} · ${r.stops.length} · ${num(r.km)} км</small></div>`;
    s += `<div class="track" style="left:${LW}px;right:12px;background-size:calc(100% / 12) 100%">`;
    let prev = r.t0 || 0;
    r.stops.forEach(st => {
      const q = O[st.k], c = C(r.v), dur = st.end - st.beg;
      if (st.arr > prev) s += `<i class="travel" title="дорога ${Math.round(st.arr - prev)} мин" style="left:${P(prev)};width:${P(st.arr - prev)};background:${c}"></i>`;
      if (st.beg > st.arr + 1) s += `<i class="wait" title="ожидание ${Math.round(st.beg - st.arr)} мин" style="left:${P(st.arr)};width:${P(st.beg - st.arr)}"></i>`;
      if (o.windows) s += `<i class="win" style="left:${P(q.a)};width:${P(q.b - q.a)}"></i>`;
      const lab = o.big && dur >= 60 ? `${TS[q.type]}<br>${q.district}` : dur >= 30 ? TS[q.type] : '';
      s += `<i class="work${q.type === 'Авария' ? ' avar' : ''}" title="${fmt(st.beg)}–${fmt(st.end)} · ${q.type} · ${q.district}" style="left:${P(st.beg)};width:${P(dur)};background:${c}">${lab}</i>`;
      prev = st.end;
    });
    s += '</div></div>';
  });
  if (o.now != null) s += `<div class="now" style="left:${X(o.now / 720)}"><span>${fmt(o.now)}</span></div>`;
  return s + '</div>';
}

// Карта Москвы (Leaflet + OpenStreetMap, приглушённая CSS-фильтром). Маршруты по улицам из OSRM (geom.js).
function lmap(el, opts = {}) {
  const m = L.map(el, {zoomControl: true, attributionControl: true, scrollWheelZoom: false, zoomSnap: 0.25});
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom: 19, attribution: '&copy; участники OpenStreetMap'}).addTo(m);
  m.on('click', () => m.scrollWheelZoom.enable());
  m.fitBounds(L.latLngBounds(O.map(q => [q.lat, q.lon]).concat([D.office])), opts.tl ? {paddingTopLeft: opts.tl, paddingBottomRight: opts.br} : {padding: [24, 24]});
  L.marker(D.office, {icon: L.divIcon({className: '', html: '<div class="office"></div>', iconSize: [14, 14]})}).addTo(m).bindTooltip('Офис · ул. Юных Ленинцев, 83');
  return m;
}
const legs = v => G[v];
const routeLine = v => legs(v).flat();
function orderDot(m, k, style = {}) {
  const q = O[k], w = owner[k], av = q.type === 'Авария';
  return L.circleMarker([q.lat, q.lon], {radius: av ? 7 : 5, color: av ? '#B42318' : '#fff', weight: av ? 2.5 : 1.5,
    fillColor: col[w.v], fillOpacity: 1, ...style}).addTo(m)
    .bindTooltip(`<b>${B(w.v)} · ${fmt(w.beg)}–${fmt(w.end)}</b><br>${q.type} · ${q.district}<br>${q.addr}<br>окно ${win(q)}`);
}

function topbar(title, tabs, act, right) {
  return `<div class="topbar"><div class="logo">Маршруты<span>·</span>${title}</div>
  <nav class="tabs">${tabs.map((t, i) => `<button class="${i === act ? 'on' : ''}">${t}</button>`).join('')}</nav>
  <div class="grow1"></div>${right || '<button class="btn">Загрузить CSV</button><button class="btn">Событие…</button><button class="btn primary">Экспорт xlsx</button>'}</div>`;
}
const kpi = (l, v, d, good = true) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="d ${good ? 'good' : 'bad'}">${d}</div></div>`;
const kpis = () => `<div class="kpis">
  ${kpi('Бригад задействовано', D.plan.length, `базовый вариант: ${D.base.length}`)}
  ${kpi('Пробег, всего', num(km(D.plan)) + ' км', `базовый: ${num(km(D.base))} км`)}
  ${kpi('Выполнено заявок', `${O.length} из ${O.length}`, `базовый: ${SERVED_BASE} из ${O.length}`)}
  ${kpi('Неназначенных', 0, `базовый: ${D.base_unserved.length}`)}
  ${kpi('Нижняя граница', '6 бригад', 'меньше невозможно — доказано')}</div>`;

// Причина неназначения в базовом варианте: жадный алгоритм дописывает в конец маршрута
const baseReason = k => `все бригады с навыком «${SKN[SKILL[O[k].type]]}» заняты в окно ${win(O[k])}`;

const VARIANTS = [];
