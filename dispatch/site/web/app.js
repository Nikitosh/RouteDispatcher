// Страница диспетчера: читает manifest.json и plan.json, которые пишет `lct site` (plan.json — результат lct plan / lct replan).
const PAL = ['#1F5FBF', '#B4530A', '#0F766E', '#6D3FB0', '#8A6A00', '#A8324E', '#2F6B2F', '#4E5BA6', '#8C4A2F', '#1D6F8C', '#7A2E7A', '#55606E', '#9C6B00', '#2E5E4E', '#6B3A1F', '#3B4CA8'];
const TC = {'Авария': 'ta', 'Подключение': 'tc', 'Ремонт': 'tl', 'Дозаказ': 'td'};
const TS = {'Авария': 'Авар', 'Подключение': 'Подкл', 'Ремонт': 'Рем', 'Дозаказ': 'Доз'};
const fmt = m => { m = Math.round(m) + 600; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(((m % 60) + 60) % 60).padStart(2, '0'); };
const km1 = x => (Math.round(x * 10) / 10).toFixed(1).replace('.', ',');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const cap = t => t ? t[0].toUpperCase() + t.slice(1) : t;
const badge = t => `<span class="badge ${TC[t] || 'tl'}">${esc(t)}</span>`;
const rng = (a, b) => `<span class="nw">${fmt(a)}–${fmt(b)}</span>`;               // время без переноса внутри
const win = o => o.window[0] <= 0 && o.window[1] >= 720 ? 'в течение дня' : rng(o.window[0], o.window[1]);
const plural = (n, one, few, many) => { n = Math.abs(n); const a = n % 10, b = n % 100; return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 10 || b >= 20) ? few : many; };
const pl = (n, one, few, many) => `${n} ${plural(n, one, few, many)}`;
const short = n => String(n).replace(/^Бригада\s+/, 'Б');
const gen = n => String(n).replace(/^Бригада /, 'Бригады ');      // «у Бригады 6»
const bn = n => esc(n).replace(/ (\d)/g, '\u00a0$1');                                // «Бригада 12» без переноса
const signed = (x, f = km1) => (x > 0.05 ? '+' : x < -0.05 ? '−' : '±') + f(Math.abs(x));   // типографский минус
const col = v => PAL[v % PAL.length];
const num2 = x => x.toFixed(2).replace('.', ',');
const $ = id => document.getElementById(id);

// тексты из данных — без кодов и канцелярита: «Бригады 1, 2, 3», «ул. Михайлова», «Авария» вместо «АВАРИЯ»
function tidy(s) {
  return esc(String(s ?? '')
    .replace(/Бригада (\d+)((?:, Бригада \d+)+)/g, (m, a, rest) => 'Бригады ' + [a, ...rest.match(/\d+/g)].join(', '))
    .replace(/(^|[^А-Яа-яЁё])([А-ЯЁ]{4,})(?=[^А-Яа-яЁё]|$)/g, (m, pre, w) => pre + cap(w.toLowerCase())))
    .replace(/Бригад(а|ы) (\d)/g, 'Бригад$1\u00a0$2');
}
const brList = names => tidy(names);
function addrText(a) {
  const s = String(a || '').trim();
  if (/^\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*$/.test(s)) return 'Точка на карте';
  return s.replace(/^(город\s+москва|г\.\s*москва|москва)\s*,?\s*/i, '')
    .replace(/(^|[\s,])(пр-кт|б-р|проезд|аллея|туп|ш-се)\.\s*/gi, '$1$2 ')
    .replace(/(^|[\s,])(ул|пер|наб|ш|г|обл|пгт|пл|мкр|пос|стр|корп)\.(?=[^\s,])/gi, '$1$2. ')
    .replace(/\s{2,}/g, ' ');
}
// названия работ и подтипов заявок — WORK и work() в status.js (общие с экраном инженера)
const SRC = {'кадастр': 'по данным дома', 'Реформа ЖКХ': 'по данным дома', 'OSM': 'по карте', 'оценка по этажности': 'оценка', 'оценка': 'оценка'};

// Состояние: участок DI и его план D; событие сценария E (пересчёт) с моментом E.event.at; T — время на шкале дня
// (null — весь день). До момента события показан утренний план, с него — пересчитанный.
let MAN, D, E = null, BASE = null, DI = -1, EVKEY = null, SCN = [], SC = null, T = null, sel = null, hl = null, map, layer, hiLayer, playTimer = null;
let FOCUS = [];                                     // точки, к которым приближать карту в момент события
// Пересчёт на сервере (dispatch/api.py), если сайт собран с --api: свои события и передача заявки. Версии — в браузере.
let USER = [], USERS = {}, FORM = null, MOVE = null, BUSY = false;   // свои версии — по участкам, на время сессии
const api = (path, body) => fetch('api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
  .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `ошибка ${r.status}`); return j; });
const regionSlug = () => MAN.districts[DI].slug;
const hhmm2min = s => { const m = /^(\d{1,2}):(\d{2})$/.exec((s || '').trim()); return m ? +m[1] * 60 + +m[2] - 600 : null; };
const PAD = {paddingTopLeft: [40, 40], paddingBottomRight: [40, 150]};          // снизу — место под шкалой дня

async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); }

async function init() {
  MAN = await getJSON('manifest.json');
  document.addEventListener('keydown', e => {    // строки, блоки диаграммы и бригады — и с клавиатуры
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[role="button"][tabindex]')) { e.preventDefault(); e.target.click(); }
  });
  SCN = scenarios();
  if (!SCN.length) throw new Error('в сборке нет ни одного плана — соберите: python -m dispatch site <папки plan> -o site');
  $('scn').onchange = () => openScenario($('scn').value);
  $('scn-face').onclick = () => menu();
  $('scn-face').onkeydown = menuKeys;
  document.addEventListener('click', e => { if (!e.target.closest('.scn')) menu(false); });
  $('scn-prev').onclick = () => stepScenario(-1);
  $('scn-next').onclick = () => stepScenario(1);
  map = L.map('map', {scrollWheelZoom: false, zoomSnap: 0.25});
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');   // без значка-флага по умолчанию
  // OSM, приглушённая до светло-серой (фильтр в style.css): улицы и их названия читаются, маршруты — главные на карте
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; участники OpenStreetMap'}).addTo(map);
  map.on('click', e => { map.scrollWheelZoom.enable(); if (FORM && (FORM.kind === 'urgent' || FORM.kind === 'order')) { FORM.lat = e.latlng.lat; FORM.lon = e.latlng.lng; FORM.variants = null; renderPanel(); renderMap(); } });
  if (MAN.api && MAN.events) { $('ev-btn').classList.remove('hidden'); $('ev-btn').onclick = openForm; }
  window.addEventListener('storage', e => { if ((!e.key || e.key.startsWith(FieldStatus.PREFIX)) && D && !FORM && T == null) { renderPanel(); renderGantt(); renderBrigades(); } });
  layer = L.layerGroup().addTo(map);
  hiLayer = L.layerGroup().addTo(map);
  $('tl-ticks').innerHTML = [...Array(13).keys()].map(i => `<span class="${i % 2 ? 'mn' : ''}" style="left:calc(8px + (100% - 16px) * ${i / 12})">${i % 2 ? '' : fmt(i * 60)}</span>`).join('');
  $('play-range').oninput = () => { stopPlay(); setT(+$('play-range').value); };
  $('play-btn').onclick = togglePlay;
  $('play-all').onclick = () => { stopPlay(); setT(null); };
  $('tl-ev').onclick = e => {                       // к моменту события — без прокрутки страницы
    e.preventDefault();
    const y = window.scrollY;
    stopPlay(); setT(E.event.at); fitEvent(true);
    window.scrollTo(0, y);
  };
  $('skip-tl').onclick = e => { e.preventDefault(); $('play-bar').scrollIntoView({block: 'nearest'}); $('play-btn').focus(); };
  await openScenario(fromHash(new URLSearchParams(location.hash.slice(1))));
  $('tour-btn').onclick = () => tour(0);
  let seen = true;
  try { seen = localStorage.getItem('pulse.tour') === '1' || navigator.webdriver; } catch (e) { /* нет хранилища */ }
  if (!seen) setTimeout(() => tour(0), 600);                 // первый заход — показать экран по шагам
  window.addEventListener('hashchange', () => {           // ссылка с другой страницы или правка адреса вручную
    const k = fromHash(new URLSearchParams(location.hash.slice(1)));
    if (!SC || k !== SC.key) openScenario(k);
  });
}

// ---------- сценарии: один список; группы — планы участков, затем шаги историй показа (scripts/scenarios.json),
// события вне историй и свои события. Адрес — #sc=<ключ>; старые #j=&s= и #d= тоже открываются.
function scenarios() {
  const steps = [], plans = [], other = [], seen = new Set();
  (MAN.journeys || []).forEach(j => j.steps.forEach(s => {
    const d = MAN.districts.findIndex(x => x.slug === s.d);
    if (s.link || d < 0) return;
    seen.add(`${d}:${s.ev ?? ''}`);
    steps.push({key: `${j.slug}-${s.slug}`, group: j.title, title: s.title, text: s.text || '', d, ev: s.ev ?? null, order: s.order, show: s.show || ''});
  }));
  MAN.districts.forEach((x, d) => { if (!x.name.includes(' — ') || !seen.has(`${d}:`))
    plans.push({key: 'd-' + x.slug, group: 'Планы участков', title: x.name, text: '', d, ev: null}); });
  MAN.districts.forEach((x, d) => (x.events || []).forEach((e, i) => { if (!seen.has(`${d}:${i}`))
    other.push({key: `e-${x.slug}-${i}`, group: 'Другие события', title: `${x.name}: ${e.label}`, text: '', d, ev: i}); }));
  return plans.concat(steps, other);
}

function scnSelect() {
  const groups = [];
  SCN.forEach(s => { let g = groups.find(x => x.t === s.group); if (!g) groups.push(g = {t: s.group, items: []}); g.items.push(s); });
  $('scn').innerHTML = groups.map(g => `<optgroup label="${esc(g.t)}">${g.items.map(s => `<option value="${esc(s.key)}">${esc(s.title)}</option>`).join('')}</optgroup>`).join('')
    + (USER.length ? `<optgroup label="Мои события">${USER.map((u, j) => `<option value="u${j}">${esc(u.label)}</option>`).join('')}</optgroup>` : '');
  // планы участков — сверху на всю ширину, группы показа — в две колонки (поровну по числу пунктов), прочее — ниже
  const user = USER.length ? [{t: 'Мои события', items: USER.map((u, j) => ({key: 'u' + j, title: u.label}))}] : [];
  const kind = g => g.items[0].key.slice(0, 2);
  const top = groups.filter(g => kind(g) === 'd-'), shown = groups.filter(g => !/^[de]-/.test(kind(g))), rest = groups.filter(g => kind(g) === 'e-').concat(user);
  const cols = [[], []], size = [0, 0];
  shown.forEach(g => { const c = size[0] <= size[1] ? 0 : 1; cols[c].push(g); size[c] += g.items.length + 1.5; });
  const grp = (g, wide) => `<div class="scn-grp${wide ? ' wide' : ''}" role="group" aria-label="${esc(g.t)}"><h4>${esc(g.t)}</h4><div class="scn-its">`
    + g.items.map(s => `<button class="scn-it" role="option" data-key="${esc(s.key)}">${esc(s.title)}</button>`).join('') + '</div></div>';
  $('scn-menu').innerHTML = top.map(g => grp(g, true)).join('')
    + (shown.length ? `<div class="scn-cols">${cols.map(c => `<div>${c.map(g => grp(g)).join('')}</div>`).join('')}</div>` : '')
    + rest.map(g => grp(g, true)).join('');
  $('scn-menu').querySelectorAll('.scn-it').forEach(b => b.onclick = () => { menu(false); openScenario(b.dataset.key); });
}

// свой список сценариев вместо системного: открыть/закрыть, стрелки, Enter, Esc, клик мимо
function menu(open) {
  const m = $('scn-menu');
  open = open ?? m.classList.contains('hidden');
  m.classList.toggle('hidden', !open);
  $('scn-face').setAttribute('aria-expanded', open);
  if (!open) return;
  m.querySelectorAll('.scn-it').forEach(b => { b.classList.toggle('on', b.dataset.key === SC.key); b.setAttribute('aria-selected', b.dataset.key === SC.key); b.classList.remove('kb'); });
  const on = m.querySelector('.scn-it.on');
  if (on) { on.classList.add('kb'); on.scrollIntoView({block: 'nearest'}); }
}
function menuKeys(e) {
  const m = $('scn-menu');
  if (m.classList.contains('hidden')) { if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); menu(true); } return; }
  const items = [...m.querySelectorAll('.scn-it')], i = items.findIndex(b => b.classList.contains('kb'));
  if (e.key === 'Escape') { menu(false); return; }
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (items[i]) items[i].click(); return; }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  e.preventDefault();
  const n = items[Math.max(0, Math.min(items.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
  items.forEach(b => b.classList.toggle('kb', b === n)); n.scrollIntoView({block: 'nearest'});
}

function fromHash(h) {
  const J = MAN.journeys || [], has = k => SCN.some(s => s.key === k);
  if (has(h.get('sc'))) return h.get('sc');
  const j = h.get('j') ? J.find(x => x.slug === h.get('j')) : h.get('s') && J.find(x => x.steps.some(s => s.slug === h.get('s')));
  const st = j && (h.get('j') ? j.steps[+(h.get('s') || 0)] : j.steps.find(s => s.slug === h.get('s')));
  if (st && has(`${j.slug}-${st.slug}`)) return `${j.slug}-${st.slug}`;
  const d = MAN.districts.findIndex(x => x.slug === h.get('d') || x.name === h.get('d'));
  const v = h.get('v') != null && h.get('v') !== '' ? +h.get('v') : null;          // с экрана инженера: план или событие
  if (d >= 0 && v != null && SCN.some(s => s.d === d && s.ev === v)) return SCN.find(s => s.d === d && s.ev === v).key;
  if (d >= 0) return (SCN.find(s => s.key === 'd-' + MAN.districts[d].slug) || SCN.find(s => s.d === d && s.ev == null) || SCN.find(s => s.d === d)).key;
  return (SCN.find(s => s.key.startsWith('d-')) || SCN[0]).key;
}

async function openScenario(key) {
  stopPlay(); FORM = null; MOVE = null;
  if (/^u\d+$/.test(key)) return showVersion(key);
  const s = SCN.find(x => x.key === key) || SCN[0];
  if (s.d !== DI) await loadDistrict(s.d);
  E = s.ev != null ? await getJSON(MAN.districts[s.d].events[s.ev].plan) : null;
  SC = s; BASE = null; EVKEY = s.ev != null ? 'p' + s.ev : null;
  T = E ? E.event.at : null;                     // событие — шкала на его моменте, карта у затронутых заявок
  sel = null; hl = null; COV.on = (s.show || '').split(',').includes('coverage');
  if (!E) fitAll();
  context(); render();
  if (E) fitEvent(false);
  if (s.order) { const k = cur().orders.findIndex(o => o.id === String(s.order)); if (k >= 0) select(k); }
  history.replaceState(null, '', '#sc=' + encodeURIComponent(s.key));
}

function stepScenario(dir) {
  const i = SCN.findIndex(x => x.key === SC.key), n = SCN[(i < 0 ? -1 : i) + dir];
  if (n) openScenario(n.key);
}

function showVersion(key) {
  const u = USER[+key.slice(1)];
  E = u.doc; BASE = u.base; EVKEY = key; SC = {key, group: 'Мои события', title: u.label, text: '', d: DI, ev: null, user: true};
  T = E.event.at; sel = null; FORM = null; MOVE = null;
  context(); render(); fitEvent(false);
}

// строка «где я»: участок, дата и коротко — что на экране; стрелки — соседние сценарии списка
function context() {
  const d = MAN.districts[DI], i = SCN.findIndex(x => x.key === SC.key);
  $('scn').value = SC.key;
  $('scn-g').textContent = SC.group;
  $('scn-t').textContent = SC.user ? 'Моё: ' + SC.title : SC.title;
  $('scn-n').textContent = i >= 0 ? `${i + 1} из ${SCN.length}` : '';
  const m = (E || D).plan.metrics;
  const auto = E ? `${cap(eventTitle(E.event, E))}. Шкала стоит на моменте события; справа — что изменилось.`
    : `План на день: ${pl(m.brigades_used, 'бригада', 'бригады', 'бригад')}, ${pl(m.orders, 'заявка', 'заявки', 'заявок')}. Нажмите на заявку на карте или на диаграмме — справа появится её карточка.`;
  $('ctx').innerHTML = `<b>${esc(d.name)}</b>${D.meta.date ? `<span class="muted">на ${esc(D.meta.date)}</span>` : ''}`
    + `<span class="ctx-t">${SC.user ? 'Моё: ' + esc(SC.title) : SC.text ? esc(SC.text) : auto}</span>`;
  $('scn-prev').disabled = i <= 0; $('scn-next').disabled = i < 0 || i >= SCN.length - 1;
  $('eng-link').href = `engineer.html#d=${encodeURIComponent(d.slug)}${EVKEY && EVKEY[0] === 'p' ? '&v=' + EVKEY.slice(1) : ''}`;
}

async function loadDistrict(i) {
  DI = i; D = await getJSON(MAN.districts[i].plan); E = null; sel = null; hl = null;
  USER = USERS[i] = USERS[i] || []; FORM = null; MOVE = null;
  scnSelect(); fitAll();
}

// ---------- свои события и передача заявки (сервер)
function openForm() {
  const base = cur();
  FORM = {kind: 'urgent', type: 'авария', at: fmt(base.event ? base.event.at + 30 : 120), variants: null, base, E0: E, T0: T};      // E0, T0 — что было на экране: «закрыть» вернёт
  sel = null; MOVE = null; T = null; render();
}

function addVersion(label, doc, base) {
  USER.push({label, doc, base});               // base — план, от которого посчитано (до момента события на шкале)
  scnSelect();
  showVersion('u' + (USER.length - 1));
}

async function calcVariants() {
  const at = hhmm2min(FORM.at), base = FORM.base;
  if (at == null) return alert('Время в формате ЧЧ:ММ');
  let event;
  if (FORM.kind === 'urgent' || FORM.kind === 'order') {
    if (FORM.lat == null) return alert('Укажите адрес кликом по карте');
    event = {kind: 'urgent', lat: FORM.lat, lon: FORM.lon, type: FORM.kind === 'urgent' ? 'авария' : FORM.type,
             window: FORM.kind === 'order' ? FORM.window : null, id: 'М' + (USER.length + 1)};
  } else if (FORM.kind === 'cancel') event = {kind: 'cancel', order: FORM.order};
  else if (FORM.kind === 'finish') {
    if (!/^\d{1,2}:\d{2}$/.test(FORM.end || '')) return alert('Окончание работ: ЧЧ:ММ');
    event = {kind: 'finish', order: FORM.order, end: hhmm2min(FORM.end)};
  }
  else if (FORM.kind === 'window') {
    if (!/^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$/.test(FORM.nwin || '')) return alert('Новое окно: ЧЧ:ММ-ЧЧ:ММ');
    event = {kind: 'window', order: FORM.order, window: FORM.nwin.replace(/\s/g, '')};
  }
  else if (FORM.kind === 'breakdown') event = {kind: 'breakdown', brigade: FORM.brigade, mode: FORM.mode || 'pt'};
  else event = {kind: 'unavailable', brigade: FORM.brigade};
  BUSY = true; renderPanel();
  try { FORM.variants = (await api('variants', {region: regionSlug(), doc: base, at, event})).variants; FORM.at_min = at; }
  catch (e) { alert(e.message); }
  BUSY = false; renderPanel();
}

function formPanel(R) {
  const f = FORM, B = R.brigades, O = R.orders;
  const kinds = [['urgent', 'Авария'], ['order', 'Новая заявка'], ['cancel', 'Отмена заявки'], ['finish', 'Закончили не по плану'], ['window', 'Перенос окна'], ['breakdown', 'Сломалась машина'], ['unavailable', 'Бригада недоступна']];
  let h = `<div class="ptitle"><span>Событие</span><button class="linkbtn" id="f-close">закрыть</button></div><div class="form">
    <label>Время <input id="f-at" value="${esc(f.at)}" size="5"></label>
    <div class="seg">${kinds.map(([k, t]) => `<button class="${f.kind === k ? 'on' : ''}" aria-pressed="${f.kind === k}" data-k="${k}">${t}</button>`).join('')}</div>`;
  if (f.kind === 'urgent' || f.kind === 'order')
    h += `<div class="muted">${f.lat != null ? `Точка: ${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}` : 'Укажите адрес кликом по карте'}</div>`;
  if (f.kind === 'order')
    h += `<label>Тип <select id="f-type">${['подключение', 'ремонт', 'дозаказ'].map(t => `<option ${f.type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
      <label>Окно <input id="f-win" value="${esc(f.window || '')}" placeholder="16:00-18:00" size="11"></label>`;
  if (f.kind === 'cancel')
    h += `<label>Заявка <select id="f-order">${O.filter(o => o.plan && !o.cancelled).map(o => `<option ${f.order === o.id ? 'selected' : ''}>${esc(o.id)}</option>`).join('')}</select></label>`;
  if (f.kind === 'finish') {
    // заявки, начатые к моменту события и не закончившиеся раньше чем за час до него: бригада сообщает, когда закончила или закончит
    const at = hhmm2min(f.at), started = O.filter(o => o.plan && !o.cancelled && o.plan.start < at && o.plan.end > at - 60).sort((a, b) => b.plan.start - a.plan.start);
    const cur = started.find(o => o.id === f.order) || started.find(o => o.plan.end > at) || started[0];
    if (cur && (f.order !== cur.id || !f.end)) { f.order = cur.id; f.end = fmt(Math.round(cur.plan.end) + 30); }
    h += started.length ? `<label>Заявка <select id="f-order">${started.map(o => `<option value="${esc(o.id)}" ${f.order === o.id ? 'selected' : ''}>${esc(o.id)} — ${esc(B[o.plan.brigade].name)}, по плану до ${fmt(o.plan.end)}</option>`).join('')}</select></label>
      <label>Окончание <input id="f-end" value="${esc(f.end || '')}" size="5"></label>
      <div class="pnote">Раньше плана: освободится время. Позже: бригада может не успеть к следующим клиентам.</div>`
      : `<div class="muted">К ${esc(f.at)} нет начатых заявок</div>`;
  }
  if (f.kind === 'window') {
    // клиент перенёс окно: заявки, которые ещё не начаты к моменту события
    const at = hhmm2min(f.at), left = O.filter(o => !o.cancelled && (!o.plan || o.plan.start > at));
    if (!left.some(o => o.id === f.order) && left.length) { f.order = left[0].id; f.nwin = null; }
    const o = left.find(x => x.id === f.order);
    if (o && !f.nwin) f.nwin = `${fmt(Math.min(o.window[0] + 120, 600))}-${fmt(Math.min(o.window[1] + 120, 720))}`;
    h += left.length ? `<label>Заявка <select id="f-order">${left.map(x => `<option value="${esc(x.id)}" ${f.order === x.id ? 'selected' : ''}>${esc(x.id)} — окно ${x.window[0] <= 0 && x.window[1] >= 720 ? 'в течение дня' : fmt(x.window[0]) + '–' + fmt(x.window[1])}${x.plan ? ', ' + esc(B[x.plan.brigade].name) : ''}</option>`).join('')}</select></label>
      <label>Новое окно <input id="f-nwin" value="${esc(f.nwin || '')}" placeholder="16:00-18:00" size="11"></label>
      <div class="pnote">Если бригада успевает в новое окно, заявка остаётся у неё; иначе её берёт другая бригада.</div>`
      : `<div class="muted">Все заявки уже начаты</div>`;
  }
  if (f.kind === 'breakdown') {
    const cars = R.plan.routes.filter(rt => B[rt.v].mode === 'car');
    if (!cars.some(rt => B[rt.v].name === f.brigade) && cars.length) f.brigade = B[cars[0].v].name;
    h += cars.length ? `<label>Бригада <select id="f-brig">${cars.map(rt => `<option ${f.brigade === B[rt.v].name ? 'selected' : ''}>${esc(B[rt.v].name)}</option>`).join('')}</select></label>
      <label>Далее <select id="f-mode">${[['pt', 'на общественном транспорте'], ['foot', 'пешком'], ['bike', 'на велосипеде']].map(([m, t]) => `<option value="${m}" ${(f.mode || 'pt') === m ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
      <div class="pnote">Бригада продолжает день с места поломки. Заявки, где нужна машина, переходят другим бригадам.</div>`
      : `<div class="muted">В плане нет бригад на машине</div>`;
  }
  if (f.kind === 'unavailable')
    h += `<label>Бригада <select id="f-brig">${R.plan.routes.map(rt => `<option ${f.brigade === B[rt.v].name ? 'selected' : ''}>${esc(B[rt.v].name)}</option>`).join('')}</select></label>`;
  h += `<button class="btn primary" id="f-go" ${BUSY ? 'disabled' : ''}>${BUSY ? 'Расчёт…' : 'Рассчитать варианты'}</button></div>`;
  if (f.variants) {
    const m = R.plan.metrics;
    h += `<div class="ptitle"><span>Варианты</span><span class="muted">сейчас ${pl(m.brigades_used, 'бригада', 'бригады', 'бригад')}, ${km1(m.km_total)} км</span></div>`
      + f.variants.map((v, i) => { const s = v.summary, a = s.accident;
        return `<div class="variant"><b>${esc(v.title)}</b><div class="vrow">${pl(s.brigades, 'бригада', 'бригады', 'бригад')} · ${km1(s.km)} км · изменений ${s.changes}${s.unassigned ? ` · <span class="bad">без исполнителя ${s.unassigned}</span>` : ''}</div>`
          + (a ? `<div class="vrow">Начало работ по аварии через ${Math.round(a.wait)} мин${s.turned ? ', разворот бригады в пути' : ''}</div>` : '')
          + `<div class="vbtn"><button class="btn" data-show="${i}">Показать</button><button class="btn primary" data-apply="${i}">Применить</button></div></div>`; }).join('');
  }
  return h;
}

function bindForm() {
  $('f-close').onclick = () => { E = FORM.E0 !== undefined ? FORM.E0 : E; T = FORM.T0 ?? null; FORM = null; render(); };
  $('f-at').onchange = e => { FORM.at = e.target.value; FORM.variants = null; if (FORM.kind === 'finish') renderPanel(); };
  document.querySelectorAll('.seg button').forEach(b => b.onclick = () => { FORM.kind = b.dataset.k; FORM.variants = null; renderPanel(); renderMap(); });
  if ($('f-type')) $('f-type').onchange = e => FORM.type = e.target.value;
  if ($('f-win')) $('f-win').onchange = e => FORM.window = e.target.value || null;
  if ($('f-nwin')) $('f-nwin').onchange = e => { FORM.nwin = e.target.value.trim(); FORM.variants = null; };
  if ($('f-mode')) $('f-mode').onchange = e => { FORM.mode = e.target.value; FORM.variants = null; };
  if ($('f-end')) $('f-end').onchange = e => { FORM.end = e.target.value.trim(); FORM.variants = null; };
  if ($('f-order')) { FORM.order = FORM.order || $('f-order').value; $('f-order').onchange = e => { FORM.order = e.target.value; FORM.variants = null; if (FORM.kind === 'finish' || FORM.kind === 'window') { FORM.end = FORM.nwin = null; renderPanel(); } }; }
  if ($('f-brig')) { FORM.brigade = FORM.brigade || $('f-brig').value; $('f-brig').onchange = e => FORM.brigade = e.target.value; }
  $('f-go').onclick = calcVariants;
  document.querySelectorAll('[data-show]').forEach(b => b.onclick = () => { E = FORM.variants[+b.dataset.show].doc; T = null; renderKpis(); renderMap(); renderGantt(); renderBottom(); timeline(); });
  document.querySelectorAll('[data-apply]').forEach(b => b.onclick = () => {
    const v = FORM.variants[+b.dataset.apply];
    addVersion(`${fmt(FORM.at_min)} — ${eventTitle(v.doc.event, v.doc)} (${v.title.toLowerCase()})`, v.doc, FORM.base);
  });
}

async function moveOptions(k) {
  const R = cur(), at = R.event ? R.event.at : hhmm2min(prompt('Время передачи, ЧЧ:ММ', '12:00'));
  if (at == null) return;
  MOVE = {k, at, options: null}; renderPanel();
  try { MOVE.options = (await api('options', {region: regionSlug(), doc: R, at, order: R.orders[k].id})).options; }
  catch (e) { alert(e.message); MOVE = null; }
  renderPanel();
}

async function doMove(name) {
  const R = cur(), o = R.orders[MOVE.k];
  BUSY = true; renderPanel();
  try { const r = await api('move', {region: regionSlug(), doc: R, at: MOVE.at, order: o.id, brigade: name});
        addVersion(`${fmt(MOVE.at)} — заявка ${o.id} → ${name}`, r.doc, R); }
  catch (e) { alert(e.message); }
  BUSY = false; renderPanel();
}

function moveBlock() {
  if (!MOVE) return '';
  if (!MOVE.options) return '<div class="muted" style="margin-top:8px">Расчёт…</div>';
  return `<div style="font-weight:700;margin-top:12px">Передача в ${fmt(MOVE.at)}</div>` + MOVE.options.map(o => o.ok
    ? `<button class="mopt" data-b="${esc(o.name)}" ${BUSY ? 'disabled' : ''}><b>${esc(o.name)}</b><span>${o.km >= 0 ? '+' : ''}${km1(o.km)} км${o.shifted ? `, сдвинет ${esc(o.shifted)}` : ''}${o.new_brigade ? ', +1 бригада' : ''}</span><span class="muted">начало ${fmt(o.start)}</span></button>`
    : `<div class="mopt off"><b>${esc(o.name)}</b><span class="muted">${tidy(o.why)}</span></div>`).join('');
}

// событие показано, если оно есть и время на шкале не раньше него
const evOn = () => !!E && (T == null || T >= E.event.at);
const cur = () => evOn() ? E : (BASE || D);
const parent = () => BASE || D;                   // план до события
// момент события на шкале, проигрывание стоит: карта и панель — «что изменилось»
const atEvent = () => !!E && T === E.event.at && !playTimer;

function fitAll() {
  const O = D.orders.filter(o => !o.cancelled);
  const pts = O.map(o => [o.lat, o.lon]).concat(D.plan.routes.filter(r => r.stops.length).flatMap(r => routeLine(D, r)));   // заявки и маршруты целиком
  if (!pts.length) pts.push([D.office.lat, D.office.lon]);
  map.fitBounds(L.latLngBounds(pts), {...PAD, paddingTopLeft: [30, 30]});
}

function routeLine(R, rt) {
  if (rt.geometry) return rt.geometry.flat();
  const b = R.brigades[rt.v], pts = [[b.lat, b.lon]].concat(rt.stops.map(s => [R.orders[s.k].lat, R.orders[s.k].lon]));
  if (rt.via) pts.splice(rt.via.pos + 1, 0, [rt.via.lat, rt.via.lon]);   // точка разворота
  return pts;
}

// бригады, которых коснулось событие: сменили заявки, выбыли, сломались, развернулись
function affected(e) {
  const s = new Set();
  e.changes.forEach(c => { if (c.was) s.add(c.was.brigade); if (c.now) s.add(c.now.brigade); });
  if (e.brigade != null) s.add(e.brigade);
  (e.turned || []).forEach(t => s.add(t.brigade));
  if (e.kind === 'cancel' && E && E.orders[e.order]) {          // отменённая заявка: бригада, у которой она была
    const po = parent().orders.find(x => x.id === E.orders[e.order].id);
    if (po && po.plan) s.add(po.plan.brigade);
  }
  return s;
}
// заявка события и подпись к ней: новая, новое окно, отменена, задержка, передана
function eventOrder(e, R) {
  if (e.order == null || !R.orders[e.order]) return null;
  const o = R.orders[e.order];
  const what = {urgent: o.type === 'Авария' ? 'авария' : 'новая', window: 'новое окно', cancel: 'отменена', finish: e.end != null ? `до ${fmt(e.end)}` : 'задержка', move: 'передана'}[e.kind] || 'событие';
  return {k: e.order, o, label: `${what} · ${o.id}`, kind: e.kind === 'cancel' ? 'off' : ''};
}

// ---------- отрисовка
function render() {
  renderKpis(); renderMap(); renderPanel(); renderBrigades(); renderGantt(); renderBottom(); timeline();
}

function kpi(l, v, d, cls = '', up = '') {
  return `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}${up ? ` <span class="up">${up}</span>` : ''}</div><div class="d ${cls}">${d}</div></div>`;
}
const chip = (text, cls = '', tip = '') => `<span class="chip ${cls}"${tip ? ` title="${esc(tip)}"` : ''}>${text}</span>`;

// ранний старт помогает, если закрывает больше заявок, меньшим числом бригад или с меньшим пробегом
function earlyUseful(R) {
  const e = R.early, m = R.plan.metrics;
  if (!e) return false;
  if (e.useful != null) return !!e.useful;
  return (e.served ?? m.served) > m.served || e.brigades < m.brigades_used || (e.brigades === m.brigades_used && e.km < m.km_total - 0.05);
}

// «меньше нельзя»: доказанный минимум бригад; если и весь парк не закрывает день — так и пишем
function boundText(R) {
  const lb = R.lower_bound, m = R.plan.metrics;
  if (lb == null) return null;
  const fleet = R.brigades.filter(b => b.unavailable_from == null).length;
  if (m.unassigned && m.brigades_used >= fleet)
    return {text: `${pl(m.brigades_used, 'бригада', 'бригады', 'бригад')} — весь парк, его не хватает на ${pl(m.unassigned, 'заявку', 'заявки', 'заявок')}`, ok: false};
  if (lb >= m.brigades_used)
    return {text: `Меньше ${m.brigades_used} ${plural(m.brigades_used, 'бригады', 'бригад', 'бригад')} нельзя — доказано${m.unassigned ? ` для ${m.served} назначенных заявок` : ''}`, ok: true};
  return {text: `Меньше ${lb} ${plural(lb, 'бригады', 'бригад', 'бригад')} нельзя — доказано; в плане ${m.brigades_used}`, ok: false};
}

// плитка «Минимум бригад»: доказанная нижняя граница парка
function boundKpi(R) {
  const lb = R.lower_bound, m = R.plan.metrics, bt = boundText(R);
  if (lb == null) return kpi('Минимум бригад', '—', 'граница не считалась');
  const full = bt && !bt.ok && m.unassigned && lb >= m.brigades_used;
  const d = full ? `весь парк, не хватает на ${pl(m.unassigned, 'заявку', 'заявки', 'заявок')}`
    : lb >= m.brigades_used ? 'меньше нельзя — доказано' : `доказано; в плане ${m.brigades_used}`;
  return kpi('Минимум бригад', lb, d, lb >= m.brigades_used && !full ? 'ok' : '');
}

function renderKpis() {
  const R = cur(), m = R.plan.metrics, b = D.baseline.metrics;
  if (evOn()) {
    const e = E.event, bf = e.before, dk = m.km_total - bf.km_total, dv = m.brigades_used - bf.brigades_used;
    const n = e.changes.length + (e.kind === 'cancel' && !e.changes.some(c => c.k === e.order) ? 1 : 0);
    $('kpis').innerHTML = kpi('Событие', fmt(e.at), eventTitle(e))
      + kpi('Бригад', `${bf.brigades_used} → ${m.brigades_used}`, dv > 0 ? `+${pl(dv, 'бригада', 'бригады', 'бригад')} из резерва` : dv < 0 ? `на ${pl(-dv, 'бригаду', 'бригады', 'бригад')} меньше` : 'без новых бригад', dv > 0 ? 'bad' : '')
      + kpi('Пробег', `${km1(m.km_total)} км`, `${signed(dk)} км к плану до события`, dk > 0.05 ? 'bad' : '')
      + kpi('Без исполнителя', m.unassigned, m.unassigned ? `до события: ${bf.unassigned}; список ниже` : 'все заявки назначены', m.unassigned ? 'bad' : '');
    $('chips').innerHTML = chip(`${plural(n, 'Изменилась', 'Изменились', 'Изменилось')} ${pl(n, 'заявка', 'заявки', 'заявок')}`)
      + chip(`Выполненные и начатые (${e.fixed}) не меняются`, 'muted')
      + (e.accident ? chip(`Авария: начало ${fmt(e.accident.start)}, через ${Math.round(e.accident.wait)} мин`) : '');
    return;
  }
  const ds = m.served - b.served, dv = m.brigades_used - b.brigades_used, dk = m.km_total - b.km_total;
  $('kpis').innerHTML = kpi('Заявки', `${m.served} из ${m.orders}`, `базовый: ${b.served} из ${b.orders}`, '', ds > 0 ? `+${ds}` : '')
    + kpi('Бригад в работе', m.brigades_used, `базовый: ${b.brigades_used}`, '', dv < 0 ? `−${-dv}` : '')
    + kpi('Пробег', `${km1(m.km_total)} км`, ds > 0 ? `базовый: ${km1(b.km_total)} км на ${b.served} заявок` : `базовый: ${km1(b.km_total)} км`, '', dk < -0.05 && ds >= 0 ? `−${km1(-dk)}` : '')
    + boundKpi(R);                                 // «без исполнителя» уже видно в «Заявки X из Y»
  const e = R.early;
  $('chips').innerHTML = ''
    + (R.norms ? chip(`Без учёта сложности работ: ${pl(R.norms.late, 'опоздание', 'опоздания', 'опозданий')}`, '', 'Если планировать по нормативу, не учитывая этаж, лифт, дом и вид работ; в нашем плане опозданий нет') : '')
    + (e ? earlyUseful(R) ? chip(`Ранний старт: ${pl(e.brigades, 'бригада', 'бригады', 'бригад')}, ${km1(e.km)} км`, '', `Если ${(e.calls || []).length} клиентов согласятся начать раньше окна`) : chip('Ранний старт не даёт выигрыша', 'muted') : '')
    + (m.rating ? chip(`★ ${num2(m.rating)} — рейтинг на заявке${R.quality ? `; лучшие ${R.quality.k} из ${R.quality.pool}` : ''}`) : '');
}

function stars(R, name) {
  const r = R.ratings && R.ratings[name];
  if (!r) return '';
  const v = Object.values(r.by_kind), avg = v.reduce((a, x) => a + x.value, 0) / v.length;
  const tip = Object.entries(r.by_kind).map(([k, x]) => `${k}: ${num2(x.value)} (${x.reviews} отз.${x.few ? ', мало данных' : ''})`).join('; ')
    + (r.on_time != null ? `; пунктуальность ${num2(r.on_time)} без опозданий по вине плана` : '');
  return `<span class="star" title="${esc(tip)}">★ ${num2(avg)}${v.some(x => x.few) ? '<sup>?</sup>' : ''}</span>`;
}

function earlyList(R) {
  const e = R.early;
  if (!earlyUseful(R)) return `<div class="pnote early-no">Ранний старт не даёт выигрыша: если клиенты начнут раньше окна, бригад и пробега не станет меньше.</div>`;
  return `<details class="early"><summary><b>Ранний старт: обзвон клиентов</b> <span class="muted">${pl(e.calls.length, 'заявка', 'заявки', 'заявок')} → ${pl(e.brigades, 'бригада', 'бригады', 'бригад')}, ${km1(e.km)} км</span></summary>
    <div class="pnote" style="padding:4px 0 6px">Если клиенты согласятся начать до ${e.minutes} мин раньше окна, хватит ${pl(e.brigades, 'бригады', 'бригад', 'бригад')} (сейчас ${R.plan.metrics.brigades_used}), пробег ${km1(e.km)} км. Опытные инженеры часто так и договариваются с клиентом.</div>
    <table class="t">${e.calls.map(c => `<tr class="click" tabindex="0" role="button" data-k="${c.k}"><td>${esc(c.id)}</td><td>окно ${rng(c.window[0], c.window[1])}</td><td class="nw"><b>начать в ${fmt(c.start)}</b> <span class="muted">−${c.earlier} мин</span></td></tr>`).join('')}</table></details>`;
}

const EQ = {router: 'роутер', stb: 'ТВ'};
const eqText = e => Object.entries(e || {}).filter(([, n]) => n).map(([k, n]) => `<span class="nw">${EQ[k] || esc(k)}${n > 1 ? ' ×' + esc(n) : ''}</span>`).join(', ');

// столбики «вариант → пробег»: ось от нуля, текущий вариант жёлтым, красный — не все заявки закрыты
function bars(pts, o) {
  const W = 360, H = 172, top = 20, base = H - 40, bw = (W - 8) / pts.length, maxKm = Math.max(...pts.map(p => p.km)) || 1;
  const y = km => base - (base - top) * km / maxKm;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(o.aria)}"><line class="ax" x1="4" x2="${W - 4}" y1="${base}" y2="${base}"/>${pts.map((p, i) => {
    const x = 4 + i * bw + 6, w = bw - 12, h = base - y(p.km), cx = x + w / 2, bad = o.bad(p), on = o.on(p, i);
    return `<rect x="${x}" y="${y(p.km)}" width="${w}" height="${h}" rx="3" class="eb${bad ? ' bad' : ''}${on ? ' on' : ''}"><title>${esc(o.tip(p))}</title></rect>
      <text x="${cx}" y="${y(p.km) - 5}" class="ql-t">${Math.round(p.km)}</text>
      ${o.inside(p) && h > 24 ? `<text x="${cx}" y="${y(p.km) + 16}" class="in-t${bad ? ' light' : ''}">${esc(o.inside(p))}</text>` : ''}
      <text x="${cx}" y="${base + 16}" class="ax-t">${esc(o.x(p))}</text>`; }).join('')}
    <text x="${W / 2}" y="${H - 4}" class="ax-c">${esc(o.caption)}</text></svg>`;
}
const legend3 = (bad) => `<div class="cleg"><span><i class="on"></i>сейчас</span><span><i></i>все заявки закрыты</span><span><i class="bad"></i>${bad}</span></div>`;

// кривая «бригады против км»: предел бригад → выполнено и км (план с нулевой ценой бригады)
function curveChart(c, used) {
  const full = Math.max(...c.map(p => p.served));
  const a = c.find(p => p.limit === used), b = c.find(p => p.limit === used + 1), less = c.find(p => p.limit === used - 1);
  return `<div class="quality"><div class="ptitle" style="padding:0 0 4px"><span>Бригады и пробег</span><span class="muted">пробег за день, км</span></div>
    ${bars(c, {aria: 'Пробег при разном числе бригад', bad: p => p.served < full, on: p => p.limit === used, inside: p => p.served < full ? '−' + (full - p.served) : '',
      x: p => p.limit, caption: 'бригад в работе', tip: p => `${p.limit} бригад: ${p.served} заявок, ${km1(p.km)} км`})}
    ${legend3('не все заявки закрыты, −N — сколько не закрыто')}
    <div class="pnote" style="padding:6px 0 0">${a && b ? `+1 бригада: пробег ${signed(b.km - a.km)} км.` : ''}${less && less.served < full ? ` Меньше ${used} ${plural(used, 'бригады', 'бригад', 'бригад')}: не все заявки закрыты.` : ''}</div></div>`;
}

// цена ограничений: план без одного правила за раз
const RULES = {'все умеют всё': ['Навыки', 'любая бригада берёт любую заявку'], 'все на машинах': ['Транспорт', 'все бригады на машинах'],
  'без требований к транспорту': ['«Только машина»', 'заявки без требования к транспорту'], 'все смены 10:00–22:00': ['Смены', 'у всех 10:00–22:00'],
  'без обеда': ['Обед', 'без обеда 45 минут'], 'без учёта оборудования': ['Оборудование', 'без ограничений по запасу и «унесёт»']};
function priceList(rows, m) {
  const max = Math.max(1, ...rows.map(r => Math.abs(r.km - m.km_total)));
  return `<div class="ptitle"><span>Во что обходится каждое правило</span></div>
    <div class="pnote">Тот же день, посчитанный заново без одного правила. Сейчас ${pl(m.brigades_used, 'бригада', 'бригады', 'бригад')}, ${km1(m.km_total)} км.</div>
    <div class="rules">${rows.map(r => { const [name, how] = RULES[r.what] || [r.what, ''], db = r.brigades - m.brigades_used, dk = r.km - m.km_total, ds = r.served - m.served;
      return `<div class="rule"><b>${esc(name)}</b><span class="rv">${signed(dk)} км</span>
        <span class="muted">${esc(how)}${db ? ` · <b class="${db < 0 ? 'good' : 'bad'}">${signed(db, x => x)} ${plural(db, 'бригада', 'бригады', 'бригад')}</b>` : ''}${ds ? ` · ${signed(ds, x => x)} ${plural(ds, 'заявка', 'заявки', 'заявок')}` : ''}</span>
        <i style="width:${Math.round(Math.abs(dk) / max * 100)}%"></i></div>`; }).join('')}</div>`;
}

// цена точного времени: точка 0% — текущий план, разница считается от неё
function exactChart(pts) {
  const a = pts[0], h = pts.find(p => p.share === 0.5) || pts[Math.floor(pts.length / 2)], f = pts[pts.length - 1];
  const dk = h.km - a.km, db = h.brigades - a.brigades;
  return `<div class="quality"><div class="ptitle" style="padding:0 0 4px"><span>Цена точного времени</span><span class="muted">окно 1 ч вместо 2 ч</span></div>
    ${bars(pts, {aria: 'Пробег при разной доле клиентов с часовым окном', bad: p => p.served < p.orders, on: (p, i) => i === 0, inside: p => `${p.brigades} бр.`,
      x: p => `${Math.round(p.share * 100)}%`, caption: 'клиентов с часовым окном', tip: p => `${Math.round(p.share * 100)}% клиентов: ${p.brigades} бригад, ${km1(p.km)} км${p.served < p.orders ? ', не закрыто ' + (p.orders - p.served) : ''}`})}
    ${legend3('не все заявки закрыты')}
    <div class="pnote" style="padding:6px 0 0">Часовое окно для ${Math.round(h.share * 100)}% клиентов: ${signed(dk)} км${db > 0 ? `, +${pl(db, 'бригада', 'бригады', 'бригад')}` : db < 0 ? `, ${signed(db, x => x)} ${plural(db, 'бригада', 'бригады', 'бригад')}` : ' при том же парке'}. Для всех: ${pl(f.brigades, 'бригада', 'бригады', 'бригад')}${f.served < f.orders ? `, без исполнителя ${f.orders - f.served}` : ''}.</div></div>`;
}

function qualityChart(q) {
  // по числу лучших K: столбик — бригады (по делению на бригаду), над ним — км другим цветом; км всегда меньше одной
  // бригады, поэтому выше тот вариант, где больше бригад, а при равных — где больше км. Линия — средний рейтинг на заявке
  const all = [...q.curve].sort((a, b) => a.k - b.k);
  const W = 340, H = 250, L0 = 8, R0 = 8, B0 = 36, n = all.length, bw = (W - L0 - R0) / n;
  const kmMax = Math.max(...all.map(p => p.km)) * 1.05, units = p => p.brigades + p.km / kmMax;
  const uMax = Math.max(...all.map(units)), r0 = Math.min(...all.map(p => p.rating)), r1 = Math.max(...all.map(p => p.rating));
  const base = H - B0, top = 92, yU = u => base - (base - top) * u / uMax;
  const yR = r => 22 + 40 * (r1 > r0 ? (r1 - r) / (r1 - r0) : .5);
  const cx = i => L0 + bw * i + bw / 2, x0 = i => cx(i) - bw * .32, w = bw * .64;
  const line = all.map((p, i) => `${cx(i)},${yR(p.rating)}`).join(' ');
  return `<div class="quality"><div class="ptitle" style="padding:0 0 4px"><span>Цена качества</span><span class="muted">лучшие K из ${q.pool}</span></div>
    <div class="pnote" style="padding:0 0 4px">Берём на день лучших по рейтингу. Шире выбор — меньше нужно бригад, но ниже средний рейтинг на заявке.</div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Цена качества: бригады, пробег и рейтинг при разном числе лучших">
    <line x1="${L0}" y1="${base}" x2="${W - R0}" y2="${base}" class="ax"/>
    ${all.map((p, i) => { const on = p.k === q.k ? ' on' : '', yb = yU(p.brigades), yk = yU(units(p));
      const tip = `<title>${p.k === q.pool ? 'все' : 'лучшие ' + p.k}: ${pl(p.brigades, 'бригада', 'бригады', 'бригад')}, ${km1(p.km)} км, ★ ${num2(p.rating)}</title>`;
      const cuts = [...Array(p.brigades - 1).keys()].map(b => `<line x1="${x0(i)}" x2="${x0(i) + w}" y1="${yU(b + 1)}" y2="${yU(b + 1)}" class="ecut"/>`).join('');
      return `<rect x="${x0(i)}" y="${yk}" width="${w}" height="${yb - yk}" class="ekm${on}">${tip}</rect>
      <rect x="${x0(i)}" y="${yb}" width="${w}" height="${base - yb}" class="eb${on}">${tip}</rect>${cuts}
      <text x="${cx(i)}" y="${base - 6}" class="in-t">${p.brigades}</text>
      <text x="${cx(i)}" y="${yk - 4}" class="km-t">${Math.round(p.km)}</text>
      <text x="${cx(i)}" y="${base + 15}" class="ax-t">${p.k === q.pool ? 'все' : p.k}</text>`; }).join('')}
    <polyline points="${line}" class="ql"/>
    ${all.map((p, i) => `<circle cx="${cx(i)}" cy="${yR(p.rating)}" r="${p.k === q.k ? 5.5 : 3.5}" class="qp${p.k === q.k ? ' sel' : ''}"/>
      <text x="${cx(i)}" y="${yR(p.rating) - 8}" class="ql-t">${num2(p.rating)}</text>`).join('')}
    <text x="${W / 2}" y="${H - 4}" class="ax-t">лучших K · столбик — бригады, сверху — км · линия — ★ на заявке</text></svg>
    <div class="pnote" style="padding:2px 0 0">Высота: сначала бригады, потом км (км всегда меньше одной бригады). Выбрано ${q.k}: меньше лучших не закрывают день${q.proven ? ' (доказано)' : ''}.</div>
    <details class="qtab"><summary>Таблица</summary><table class="t qt"><tr><th>Лучших</th><th>Бригад</th><th>Пробег</th><th>Рейтинг</th></tr>
    ${all.map(p => `<tr class="${p.k === q.k ? 'on' : ''}"><td>${p.k === q.pool ? 'все ' + p.k : p.k}</td><td>${p.brigades}</td><td class="nw">${km1(p.km)} км</td><td class="nw">★ ${num2(p.rating)}</td></tr>`).join('')}</table></details></div>`;
}

function eventTitle(e, doc) {
  const O = (doc || E).orders;
  if (e.kind === 'unavailable') return `${esc(e.name)} недоступна`;
  if (e.kind === 'cancel') return `отменена заявка ${esc(O[e.order].id)}`;
  if (e.kind === 'move') return `заявка ${esc(O[e.order].id)} передана: ${esc(e.name)}`;
  if (e.kind === 'window') return `клиент перенёс окно заявки ${esc(O[e.order].id)} на ${fmt(e.now[0])}–${fmt(e.now[1])}`;
  if (e.kind === 'breakdown') return `${esc(e.name)}: сломалась машина`;
  if (e.kind === 'finish') return `заявка ${esc(O[e.order].id)} ${e.end > e.at ? 'закончится' : 'закончена'} в ${fmt(e.end)} (${e.delta > 0 ? '+' : '−'}${Math.abs(e.delta)} мин)`;
  return `новая заявка: ${esc((e.type || '').toLowerCase())}`;
}

// что изменилось — словами из данных события: кто, когда, сколько заявок перешло и как изменился пробег
function changeWords(e) {
  const moved = e.changes.filter(c => c.was && c.now && c.was.brigade !== c.now.brigade && c.k !== e.order).length;
  const shifted = e.changes.filter(c => c.was && c.now && c.was.brigade === c.now.brigade && c.k !== e.order).length;
  const lost = e.changes.filter(c => c.was && !c.now).length;
  const out = [];
  if (moved) out.push(moved === 1 ? '1 заявка переходит другой бригаде' : `${moved} ${plural(moved, 'заявка переходит', 'заявки переходят', 'заявок переходят')} другим бригадам`);
  if (shifted) out.push(`у ${pl(shifted, 'заявки', 'заявок', 'заявок')} сдвигается время`);
  if (lost) out.push(`${pl(lost, 'заявка остаётся', 'заявки остаются', 'заявок остаются')} без исполнителя`);
  return out.length ? cap(out.join(', ')) + '.' : 'Другие заявки не меняются.';
}
function evSentence(e, R) {
  const O = R.orders, B = R.brigades, o = e.order != null ? O[e.order] : null, c = o && e.changes.find(x => x.k === e.order);
  const dk = R.plan.metrics.km_total - e.before.km_total, km = `Пробег ${signed(dk)} км.`;
  let s;
  if (e.kind === 'urgent') {
    s = `${o.type === 'Авария' ? 'Авария' : 'Новая заявка'} ${esc(o.id)}${o.type !== 'Авария' ? ` (${esc(o.type.toLowerCase())})` : ''}${o.type === 'Авария' || o.window[1] - o.window[0] >= 720 ? '' : `, окно ${win(o)}`}. `
      + (c && c.now ? `Берёт ${bn(B[c.now.brigade].name)}, начало в ${fmt(c.now.start)}.` : 'Взять её некому.');
    const rest = {...e, changes: e.changes.filter(x => x.k !== e.order)};
    s += ' ' + (rest.changes.length ? changeWords(rest) : 'Другие заявки остаются у своих бригад.');
  } else if (e.kind === 'finish') {
    const later = e.delta > 0;
    s = `${bn(e.name || (o.plan ? B[o.plan.brigade].name : 'Бригада'))} ${e.end > e.at ? 'закончит' : 'закончила'} заявку ${esc(o.id)} в ${fmt(e.end)} — на ${Math.abs(e.delta)} мин ${later ? 'позже' : 'раньше'} плана. ${changeWords(e)}`;
  } else if (e.kind === 'window') {
    const same = c && c.was && c.now && c.was.brigade === c.now.brigade;
    s = `Клиент перенёс окно заявки ${esc(o.id)}: было ${rng(e.was[0], e.was[1])}, стало ${rng(e.now[0], e.now[1])}. `
      + (!c || !c.now ? 'В новое окно заявку взять некому.' : same ? `Заявка остаётся у ${bn(gen(B[c.now.brigade].name))}, начало в ${fmt(c.now.start)}.` : `Заявку берёт ${bn(B[c.now.brigade].name)}, начало в ${fmt(c.now.start)}.`)
      + ' ' + changeWords({...e, changes: e.changes.filter(x => x.k !== e.order)});
  } else if (e.kind === 'cancel') {
    const po = parent().orders.find(x => x.id === o.id);
    s = `Клиент отменил заявку ${esc(o.id)}${po && po.plan ? ` (${bn(parent().brigades[po.plan.brigade].name)}, ${rng(po.plan.start, po.plan.end)})` : ''}. ${e.changes.length ? changeWords(e) : 'Другие заявки не меняются.'}`;
  } else if (e.kind === 'breakdown') {
    s = `У ${bn(gen(e.name))} сломалась машина, дальше — ${esc((e.now_name || 'общественный транспорт').toLowerCase())}. ${changeWords(e)}`;
  } else if (e.kind === 'unavailable') {
    s = `${bn(e.name)} недоступна с ${fmt(e.at)}: начатую заявку доделывает. ${changeWords(e)}`;
  } else if (e.kind === 'move') {
    s = `Заявку ${esc(o.id)} передали: ${bn(e.name)}. ${changeWords({...e, changes: e.changes.filter(x => x.k !== e.order)})}`;
  } else s = cap(eventTitle(e, R)) + '. ' + changeWords(e);
  return `${s} ${km}${e.accident ? ` Начало работ по аварии в ${fmt(e.accident.start)}, через ${Math.round(e.accident.wait)} мин.` : ''}`;
}

// карта аварийного покрытия: клетки сетки по времени ответа в выбранный срез
let COV = {on: false, slot: 8};
function coverageLayer(c) {
  const t = c.slots[COV.slot], row = c.minutes[COV.slot], half = c.step / 2 / 111.32;
  c.grid.forEach(([lat, lon], i) => {
    const m = row[i], dlon = half / Math.cos(lat * Math.PI / 180);
    const color = m == null || m > 120 ? '#D92D20' : m > 60 ? '#FFC800' : '#141414';
    const op = m == null || m > 120 ? .42 : m > 60 ? .45 : .04 + .22 * m / 60;   // до часа: чем дольше, тем темнее
    L.rectangle([[lat - half, lon - dlon], [lat + half, lon + dlon]], {stroke: false, fillColor: color, fillOpacity: op})
      .bindTooltip(m == null ? 'никто не успеет' : `через ${m} мин — ${esc(D.brigades[c.brigade[COV.slot][i]].name)}`).addTo(layer);
  });
  $('cov-time').textContent = fmt(t);
}

// рамка «Покрытие аварий» — только в своём сценарии
function coverageControl() {
  const c = cur().coverage, box = $('cov-box'), mine = String(SC && SC.show || '').split(',').includes('coverage');
  if (!c || !mine || evOn() || T != null) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  $('cov-on').checked = COV.on;
  $('cov-range').max = c.slots.length - 1; $('cov-range').value = COV.slot;
  $('cov-ctl').classList.toggle('hidden', !COV.on);
  $('cov-on').onchange = e => { COV.on = e.target.checked; coverageControl(); renderMap(); };
  $('cov-range').oninput = e => { COV.slot = +e.target.value; renderMap(); };
}

// где ещё можно предложить подключение: районы × двухчасовые окна, сколько ещё подключений помещается
function deficitTable(d) {
  const cls = n => n === 0 ? 'df0' : n <= 2 ? 'df1' : 'df2', h = m => String((600 + m) / 60 | 0);
  return `<div class="ptitle"><span>Где ещё можно предложить подключение</span></div>
    <div class="pnote">Сколько ещё подключений по ${d.service} мин поместится у работающих бригад. «—» — окно клиентам не предлагать.</div>
    <div class="dfw"><table class="t dft"><tr><th>Район</th>${d.windows.map(w => `<th class="nw">${h(w[0])}–${h(w[1])}</th>`).join('')}</tr>
    ${d.districts.map((n, i) => `<tr><td>${esc(n)}</td>${d.free[i].map((f, j) => `<td class="${cls(f)}" title="в плане ${d.demand[i][j]}; свободно ${f}${d.free_all[i][j] > f ? `, с резервом ${d.free_all[i][j]}` : ''}">${f === 0 ? '—' : f >= d.cap ? d.cap + '+' : f}</td>`).join('')}</tr>`).join('')}</table></div>`;
}

// ---------- карта
const icon = (html, size = [0, 0]) => L.divIcon({className: 'mk', iconSize: size, html});
const pinHtml = (v, name) => `<div class="pin" style="background:${col(v)}">${esc(short(name))}</div>`;

function routeDraw(line, color, o) {   // белая подложка под цветной линией — маршрут читается поверх улиц
  if (o.casing) L.polyline(line, {color: '#fff', weight: o.weight + 3, opacity: o.opacity > .5 ? .85 : .4, lineCap: 'round', lineJoin: 'round', interactive: false}).addTo(layer);
  L.polyline(line, {color, weight: o.weight, opacity: o.opacity, lineCap: 'round', lineJoin: 'round', dashArray: o.dash || null, interactive: false}).addTo(layer);
}

// маркеры карты кликаются мышью, но не стоят в порядке Tab: до шкалы дня — десяток нажатий, а не сотня
const noTab = () => document.querySelectorAll('#map path.leaflet-interactive, #map .leaflet-marker-icon').forEach(el => el.setAttribute('tabindex', '-1'));

function renderMap() {
  layer.clearLayers(); hiLayer.clearLayers();
  coverageControl();
  if (T != null) { drawPlay(); noTab(); return; }
  const R = cur(), O = R.orders;
  if (COV.on && R.coverage) coverageLayer(R.coverage);
  const ev = evOn(), changed = new Set(ev ? E.event.changes.map(c => c.k) : []), chgBr = ev ? affected(E.event) : new Set();
  const owner = {}; R.plan.routes.forEach(rt => rt.stops.forEach(s => owner[s.k] = rt.v));
  const fv = hl != null ? hl : sel != null ? owner[sel] : null;          // выделенная бригада: наведение или выбранная заявка
  const has = {plan: false, solid: false, was: false, av: false, un: false, chg: false, home: false};
  if (ev && fv == null) {                                // утренние маршруты затронутых бригад — пунктиром
    parent().plan.routes.filter(rt => chgBr.has(rt.v) && rt.stops.length).forEach(rt => {
      routeDraw(routeLine(parent(), rt), col(rt.v), {weight: 2.5, opacity: .55, dash: '2 6'}); has.was = true; });
  }
  R.plan.routes.forEach(rt => {
    if (!rt.stops.length) return;
    const on = fv === rt.v, dim = fv != null ? !on : ev && !chgBr.has(rt.v);
    // план на день — пунктиром, как в начале проигрывания: бригады ещё не выехали; после события — сплошным
    const dash = !ev && !on ? '6 7' : null;
    dash ? has.plan = true : has.solid = true;
    routeDraw(routeLine(R, rt), dim && fv == null ? '#9A958C' : col(rt.v), {weight: on ? 6 : 3.5, opacity: dim ? (fv != null ? .2 : .45) : .95, dash, casing: !dim});
    if (!ev && rt.stops.length) { const b = R.brigades[rt.v]; has.home = true;
      L.marker([b.lat, b.lon], {keyboard: false, zIndexOffset: 900, opacity: dim ? .45 : 1, icon: icon(pinHtml(rt.v, b.name))})
        .bindTooltip(`${bn(b.name)}: старт — ${esc(addrText(b.start_addr) || 'офис')}`).addTo(layer); }
    if (ev && reserveCalled(rt.v)) {                     // вызвана событием: значок дома с подписью
      const b = R.brigades[rt.v]; has.home = true;
      L.marker([b.lat, b.lon], {keyboard: false, zIndexOffset: 950, icon: icon(pinHtml(rt.v, b.name))})
        .bindTooltip(`${bn(b.name)} из резерва: выезд из дома — ${esc(addrText(b.start_addr) || 'офис')}`).addTo(layer);
      L.marker([b.lat, b.lon], {keyboard: false, interactive: false, zIndexOffset: 1100,
        icon: L.divIcon({className: '', html: '<div class="rsv-tag">из резерва</div>', iconSize: [0, 0]})}).addTo(layer);
    }
  });
  O.forEach((o, k) => {
    if (o.cancelled) return;
    const v = owner[k], av = o.type === 'Авария', un = v == null, ll = orderPoint(o), dim = fv != null && fv !== v;
    has.av ||= av; has.un ||= un;
    const m = L.circleMarker(ll, {radius: av ? 7 : 5.5, color: un || av ? '#C4231C' : '#fff', weight: un ? 3 : av ? 2.5 : 1.5,
      fillColor: un ? '#fff' : col(v), fillOpacity: dim ? .3 : 1, opacity: dim ? .4 : 1, className: 'ord'}).addTo(layer);
    m.bindTooltip(`<b>${esc(o.type)} · ${esc(o.id)}</b><br>${esc(addrText(o.addr))}<br>${esc(o.district)}, окно ${win(o)}` + (o.plan ? `<br>${bn(R.brigades[v].name)}, начало ${fmt(o.plan.start)}` : '<br>не назначена'));
    m.on('click', e => { L.DomEvent.stopPropagation(e); select(k); });
    if (changed.has(k) && sel !== k) { has.chg = true; L.circleMarker(ll, {radius: 11, color: '#141414', weight: 2, fill: false, interactive: false}).addTo(layer); }
  });
  if (sel != null && O[sel]) selRing(orderPoint(O[sel]));
  if (ev) { const eo = eventOrder(E.event, R); if (eo && sel !== eo.k) evMark(eo); }
  L.marker([D.office.lat, D.office.lon], {keyboard: false, icon: icon('<div class="office"></div>')})
    .bindTooltip('Офис: ' + esc(addrText(D.office.addr))).addTo(layer);
  if (FORM && FORM.lat != null && (FORM.kind === 'urgent' || FORM.kind === 'order'))
    L.marker([FORM.lat, FORM.lon], {keyboard: false, zIndexOffset: 3000, icon: icon('<div class="pick">!</div>')}).addTo(layer);
  if (ev) (E.event.turned || []).forEach(t => turnMark(t, false));
  mapLegend([has.plan && ['dash', 'маршрут на день'], has.solid && ['line', ev ? 'маршрут после события' : fv != null ? 'выбранная бригада' : 'маршрут'], has.was && ['was', 'было до события'],
    ['dot', 'заявка'], has.av && ['av', 'авария'], has.un && ['un', 'без исполнителя'], has.chg && ['chg', 'изменилась'],
    has.home && ['pin', 'дом бригады'], ['office', 'офис']]);
  noTab();
}

function selRing(ll) {            // выбранная заявка — жёлтое кольцо с тёмной обводкой
  L.circleMarker(ll, {radius: 15, color: '#141414', weight: 1.5, opacity: .8, fill: false, interactive: false}).addTo(layer);
  L.circleMarker(ll, {radius: 12, color: '#FFC800', weight: 5, fill: false, interactive: false}).addTo(layer);
}
function evMark(eo) {             // заявка события — крупный жёлтый маркер с подписью и мягкой пульсацией
  L.marker(orderPoint(eo.o), {keyboard: false, zIndexOffset: 2600, icon: icon(`<div class="evm ${eo.kind}"><i></i><span>${esc(eo.label)}</span></div>`)})
    .on('click', () => select(eo.k)).addTo(layer);
}
function hiOrder(k) {             // подсветка заявки по наведению на строку списка
  hiLayer.clearLayers();
  const o = cur().orders[k];
  if (!o) return;
  L.circleMarker(orderPoint(o), {radius: 16, color: '#141414', weight: 1.5, fill: false, interactive: false}).addTo(hiLayer);
  L.circleMarker(orderPoint(o), {radius: 13, color: '#FFC800', weight: 5, fill: false, interactive: false}).addTo(hiLayer);
}

// легенда карты — только то, что сейчас на ней есть
const LEG = {
  line: '<svg viewBox="0 0 24 10"><path d="M2 5h20" stroke="#141414" stroke-width="3" stroke-linecap="round"/></svg>',
  dash: '<svg viewBox="0 0 24 10"><path d="M2 5h20" stroke="#141414" stroke-width="2.5" stroke-dasharray="5 4" stroke-linecap="round"/></svg>',
  was: '<svg viewBox="0 0 24 10"><path d="M2 5h20" stroke="#9A958C" stroke-width="2.5" stroke-dasharray="1 4" stroke-linecap="round"/></svg>',
  dot: '<svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="#1F5FBF" stroke="#fff" stroke-width="1.5"/></svg>',
  av: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="#1F5FBF" stroke="#C4231C" stroke-width="2.5"/></svg>',
  un: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5" fill="#fff" stroke="#C4231C" stroke-width="2.5"/></svg>',
  chg: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="4" fill="#1F5FBF"/><circle cx="10" cy="10" r="8" fill="none" stroke="#141414" stroke-width="1.8"/></svg>',
  sel: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7" fill="none" stroke="#FFC800" stroke-width="3.5"/></svg>',
  pin: '<span class="lpin">Б1</span>',
  office: '<svg viewBox="0 0 14 14"><rect x="2" y="2" width="10" height="10" fill="#141414" stroke="#FFC800" stroke-width="2"/></svg>',
};
function mapLegend(items) {
  $('map-leg').innerHTML = items.filter(Boolean).map(([k, t]) => `<span>${LEG[k]}${esc(t)}</span>`).join('');
}

// смена маршрута в пути: значок — бригада сворачивает с прежней дороги; big — момент события на шкале: подсветка,
// брошенный переезд красным пунктиром
function turnMark(t, big) {
  const R = E, was = R.orders[t.was], now = t.now != null ? R.orders[t.now] : null;
  if (big) L.polyline([[t.lat, t.lon], orderPoint(was)], {color: '#D92D20', weight: 3, opacity: .9, dashArray: '4 7', lineCap: 'round', interactive: false}).addTo(layer);
  return L.marker([t.lat, t.lon], {keyboard: false, zIndexOffset: 2000, icon: L.divIcon({className: '', iconSize: [big ? 34 : 26, big ? 34 : 26],
    html: `<div class="reroute${big ? ' big' : ''}" style="--c:${col(t.brigade)}"><svg viewBox="0 0 24 24"><path d="M9 21V4" class="old"/><path d="M9 21v-6a5 5 0 0 1 5-5h4"/><path d="M15.5 6.8L19 10l-3.5 3.2"/></svg>${big ? `<span>новый маршрут ${fmt(E.event.at)}</span>` : ''}</div>`})})
    .bindTooltip(`<b>${bn(R.brigades[t.brigade].name)}</b> ${t.kind === 'transit' ? 'была в пути' : 'ждала окна'} к заявке ${esc(was.id)}<br>в ${fmt(E.event.at)} поехала ${now ? 'к заявке ' + esc(now.id) + ' (' + esc(now.type.toLowerCase()) + ')' : 'по новому маршруту'}`).addTo(layer);
}

// карта к затронутым событием заявкам и бригадам; снизу — место под шкалой дня
function fitEvent(anim) {
  if (!FOCUS.length) return;
  let b = L.latLngBounds(FOCUS);
  if (b.getNorth() - b.getSouth() < .02 && b.getEast() - b.getWest() < .03) b = L.latLngBounds([[b.getCenter().lat - .012, b.getCenter().lng - .02], [b.getCenter().lat + .012, b.getCenter().lng + .02]]);   // одна точка — с кварталами вокруг
  b = b.pad(.15);
  const opt = {paddingTopLeft: [60, 80], paddingBottomRight: [60, 150], maxZoom: 14};
  if (anim) map.flyToBounds(b, {...opt, duration: .6}); else map.fitBounds(b, opt);
}

function select(k, scroll) {
  sel = k; const o = cur().orders[k];
  if (o) map.panInside(orderPoint(o), PAD);
  renderMap(); renderPanel(); renderGantt();
  if (scroll) toPanel();
}
// к карточке заявки в панели: плавно, с учётом липкой шапки
function toPanel() {
  const p = $('panel'), bar = document.querySelector('.topbar');
  p.scrollTop = 0;
  const h = getComputedStyle(bar).position === 'sticky' ? bar.offsetHeight : 0, top = p.getBoundingClientRect().top;
  if (top < h + 8 || top > innerHeight * .6) window.scrollTo({top: window.scrollY + top - h - 12, behavior: 'smooth'});
}

// «план учится на выездах»: какие коэффициенты сложности сдвинулись по фактическим длительностям — таблицей
function learnedList(l) {
  const rows = [...l.rows].sort((a, b) => Math.abs(b.now / b.was - 1) - Math.abs(a.now / a.was - 1));
  return `<div class="ptitle"><span>По историческим данным</span><span class="muted">${pl(l.facts, 'выезд', 'выезда', 'выездов')}</span></div>
    <div class="pnote">Множитель к нормативу времени: было и стало после уточнения по прошлым выездам. План рассчитан с новыми значениями.</div>
    <table class="t lt"><tr><th>Работа</th><th>было</th><th>стало</th><th>выездов</th></tr>${rows.map(r => { const p = Math.round((r.now / r.was - 1) * 100);
      return `<tr><td>${esc(cap(work(r.what)))}</td><td class="nw">×${num2(r.was)}</td><td class="nw"><b>×${num2(r.now)}</b> <span class="pct">${p > 0 ? '+' : p < 0 ? '−' : '±'}${Math.abs(p)}%</span></td><td>${r.n}</td></tr>`; }).join('')}</table>
    <div class="pnote" style="padding-top:8px">Показаны изменения от 3%, сверху — самые большие.</div>`;
}

// точка заявки на карте — у подъезда квартиры, если он известен (номер подъезда — в карточке заявки)
function orderPoint(o) {
  const pl = o.detail && o.detail.place;
  return pl && pl.lat != null ? [pl.lat, pl.lon] : [o.lat, o.lon];
}

// блоки панели по участку; сценарий выбирает, какие показать сверху (поле show в scripts/scenarios.json)
const BLOCKS = {
  deficit: R => R.deficit && deficitTable(R.deficit), curve: R => R.curve && curveChart(R.curve, R.plan.metrics.brigades_used),
  price: R => R.price && priceList(R.price, R.plan.metrics), quality: R => R.quality && qualityChart(R.quality),
  learned: R => R.learned && learnedList(R.learned), exact: R => R.exact_time && exactChart(R.exact_time), early: R => R.early && earlyList(R),
};
// блоки только для своего сценария (show): рейтинг, обед и оборудование, старт из дома, смены
const ONLY = {
  ratings: R => R.ratings && ratingsTable(R), lunch: R => lunchTable(R), homes: R => homesTable(R), shifts: R => shiftsTable(R),
};
const BLOCK_T = {deficit: 'где предложить подключение', curve: 'бригады и пробег', price: 'во что обходятся правила', quality: 'цена качества', learned: 'по историческим данным',
                 exact: 'цена точного времени', early: 'ранний старт'};

function ratingsTable(R) {
  const avg = r => { const v = Object.values(r.by_kind); return v.reduce((a, x) => a + x.value, 0) / v.length; };
  const n = r => Object.values(r.by_kind).reduce((a, x) => a + x.reviews, 0);
  const used = new Set(R.plan.routes.filter(rt => rt.stops.length).map(rt => R.brigades[rt.v].name));
  const rows = Object.entries(R.ratings).sort((a, b) => avg(b[1]) - avg(a[1]));
  return `<div class="ptitle"><span>Рейтинг монтажников</span><span class="muted">по отзывам клиентов</span></div>
    <table class="t rt"><tr><th></th><th>Рейтинг</th><th>Отзывов</th><th></th></tr>${rows.map(([name, r], i) =>
      `<tr class="${used.has(name) ? '' : 'dim'}"><td class="nw"><span class="muted">${i + 1}.</span> ${bn(name)}</td><td class="nw"><b>★ ${num2(avg(r))}</b></td><td>${n(r)}</td><td class="nw">${used.has(name) ? 'на смене' : '<span class="muted">не выходит</span>'}</td></tr>`).join('')}</table>
    <div class="pnote" style="padding-top:8px">Мало отзывов — рейтинг ближе к среднему по парку.</div>`;
}
function lunchTable(R) {
  const rows = R.plan.routes.filter(rt => rt.stops.length);
  return `<div class="ptitle"><span>Обед и оборудование</span><span class="muted">${pl(rows.length, 'бригада', 'бригады', 'бригад')}</span></div>
    <table class="t"><tr><th></th><th>Обед</th><th>С собой утром</th></tr>${rows.map(rt => { const b = R.brigades[rt.v];
      return `<tr><td class="nw"><span class="sw" style="background:${col(rt.v)}"></span> ${bn(b.name)}</td><td>${rt.lunch ? rng(rt.lunch[0], rt.lunch[1]) : '<span class="muted">не нужен</span>'}</td><td>${eqText(b.stock) || '—'}</td></tr>`; }).join('')}</table>`;
}
function homesTable(R) {
  const rows = R.plan.routes.filter(rt => rt.stops.length);
  return `<div class="ptitle"><span>Старт из дома</span><span class="muted">до первого адреса</span></div>
    <table class="t"><tr><th></th><th>Дом</th><th>Первый адрес</th></tr>${rows.map(rt => { const b = R.brigades[rt.v], s = rt.stops[0];
      return `<tr><td class="nw"><span class="sw" style="background:${col(rt.v)}"></span> ${bn(b.name)}</td><td>${esc(addrText(b.start_addr))}</td><td class="nw">${km1(s.km)} км, ${fmt(s.arr)}</td></tr>`; }).join('')}</table>`;
}
function shiftsTable(R) {
  const work = new Map(R.plan.routes.map(rt => [rt.v, rt]));
  return `<div class="ptitle"><span>Смены бригад</span><span class="muted">график ${esc(R.brigades.find(b => b.schedule)?.schedule || '')}</span></div>
    <table class="t"><tr><th></th><th>Смена</th><th>Работа</th></tr>${R.brigades.map(b => { const rt = work.get(b.v);
      return `<tr class="${rt && rt.stops.length ? '' : 'dim'}"><td class="nw">${bn(b.name)}</td><td class="nw">${esc(b.shift_text || '10:00–22:00')}</td><td>${rt && rt.stops.length ? `${rng(rt.stops[0].beg, rt.stops[rt.stops.length - 1].end)}, ${pl(rt.stops.length, 'адрес', 'адреса', 'адресов')}` : '<span class="muted">не выходит</span>'}</td></tr>`; }).join('')}</table>`;
}

// почему базовый вариант (заявки по очереди) не справился: три главные причины, по окну и числу заявок
function baseReasons(R) {
  const un = (R.baseline && R.baseline.unassigned) || [], g = new Map();
  un.forEach(u => {
    const w = /окн[еоа]\s+(\d\d:\d\d–\d\d:\d\d)/.exec(u.text), who = /\(([^)]*)\)/.exec(u.text);
    const key = u.code === 'busy' && w ? `busy|${w[1]}` : `${u.code}|${u.text}`;
    const x = g.get(key) || {n: 0, w: w && w[1], code: u.code, text: u.text, all: 0};
    x.n++; if (who && /^все\s/.test(who[1])) x.all++;
    g.set(key, x);
  });
  return [...g.values()].sort((a, b) => b.n - a.n).slice(0, 3).map(x => ({n: x.n, busy: x.code === 'busy' && !!x.w,
    text: x.code === 'busy' && x.w ? `${/^0\d:\d\d–2[23]:\d\d$/.test(x.w) ? 'без окна, в течение дня' : 'с окном ' + x.w}${x.all * 2 >= x.n ? '' : ' — подходят немногие бригады'}` : tidy(x.text)}));
}

// сводка участка справа: заявки, бригады, км, «меньше нельзя», почему базовый вариант не справился
function districtSummary(R) {
  const m = R.plan.metrics, b = R.baseline && R.baseline.metrics, bt = boundText(R), why = baseReasons(R);
  const fleet = R.brigades.filter(x => x.unavailable_from == null).length;
  return `<div class="ptitle"><span>Итог по участку</span></div>
    <div class="sum"><div><span>Заявки</span><b>${m.served} из ${m.orders}</b></div><div><span>Бригад</span><b>${m.brigades_used} из ${fleet}</b></div><div><span>Пробег</span><b class="nw">${km1(m.km_total)} км</b></div></div>
    ${bt ? `<div class="sum-n${bt.ok ? ' ok' : ''}">${esc(bt.text)}.</div>` : ''}
    ${b && why.length ? `<div class="ptitle sub"><span>Почему по очереди не справились бы</span><span class="muted">закрыто ${b.served} из ${b.orders}</span></div>
      ${why.some(w => w.busy) ? '<div class="pnote">Заявки раздаются по очереди, без плана: к очереди этих заявок маршруты подходящих бригад уже заполнены.</div>' : ''}
      <ol class="why3">${why.map(w => `<li><b>${pl(w.n, 'заявка', 'заявки', 'заявок')}</b> ${w.busy ? '' : '— '}${w.text}</li>`).join('')}</ol>` : ''}
    <div class="pnote">Нажмите на заявку на карте или на диаграмме — здесь появится её карточка: почему эта бригада и из чего сложилось время работы.</div>`;
}

// «что изменилось»: событие словами, до → после, список изменений по заявкам
function eventPanel(R) {
  const e = E.event, O = R.orders, B = R.brigades, m = R.plan.metrics, bf = e.before;
  const rows = e.changes.map(c => ({...c}));
  if (e.kind === 'cancel' && O[e.order] && !rows.some(r => r.k === e.order)) {     // отменённая заявка — первой строкой
    const po = parent().orders.find(x => x.id === O[e.order].id);
    rows.unshift({k: e.order, why: 'клиент отменил', was: po && po.plan ? {brigade: po.plan.brigade, start: po.plan.start} : null, now: null, cancel: true});
  }
  const nm = v => bn(B[v].name);
  const what = c => c.cancel ? `${c.was ? `${nm(c.was.brigade)}, ${fmt(c.was.start)}` : ''} → <b>отменена</b>`
    : !c.was ? `<span class="muted">новая</span> → <b>${nm(c.now.brigade)}, ${fmt(c.now.start)}</b>`
    : !c.now ? `${nm(c.was.brigade)}, ${fmt(c.was.start)} → <b class="bad">без исполнителя</b>`
    : c.was.brigade === c.now.brigade ? `${nm(c.now.brigade)}, ${fmt(c.was.start)} → <b>${fmt(c.now.start)}</b>`
    : `${nm(c.was.brigade)} → <b>${nm(c.now.brigade)}</b>, ${fmt(c.was.start)} → <b>${fmt(c.now.start)}</b>`;
  const ba = (l, a, b) => `<div><span>${l}</span><b class="nw">${a} → ${b}</b></div>`;
  return `<div class="ptitle"><span>Что изменилось в ${fmt(e.at)}</span></div>
    <div class="evtext">${evSentence(e, R)}</div>
    <div class="sum ba">${ba('Бригад', bf.brigades_used, m.brigades_used)}${ba('Пробег, км', km1(bf.km_total), km1(m.km_total))}${ba('Без исполнителя', bf.unassigned, m.unassigned)}</div>
    ${(e.turned || []).map(t => `<div class="turned"><span class="reroute" style="--c:${col(t.brigade)};width:28px;height:28px;flex:none"><svg viewBox="0 0 24 24"><path d="M9 21V4" class="old"/><path d="M9 21v-6a5 5 0 0 1 5-5h4"/><path d="M15.5 6.8L19 10l-3.5 3.2"/></svg></span><div><b>${nm(t.brigade)}</b>: ${t.kind === 'transit' ? 'в пути к заявке' : 'ожидание окна, заявка'} ${esc(O[t.was].id)}. С текущей точки — сразу${t.now != null ? ' к заявке ' + esc(O[t.now].id) + ' (' + esc(O[t.now].type.toLowerCase()) + ')' : ' по новому маршруту'}</div></div>`).join('')}
    <div class="chg">${rows.length ? `<div class="chg-t">Изменения по заявкам: ${rows.length}</div>` : ''}${rows.map(c => `<div class="chg-r click" tabindex="0" role="button" data-k="${c.k}">
      <div class="chg-h">${badge(O[c.k].type)} <b>${esc(O[c.k].id)}</b> <span class="muted">${tidy(c.why)}</span></div>
      <div class="chg-m">${what(c)}</div></div>`).join('')}</div>
    <div class="pnote" style="padding-top:8px">Выполненные и начатые заявки (${e.fixed}) не меняются.</div>`;
}

function renderPanel() {
  const R = cur();
  if (FORM) { $('panel').innerHTML = formPanel(R); bindForm(); return; }
  if (sel != null) {
    $('panel').innerHTML = orderCard(R, sel);
    $('back').onclick = () => { sel = null; MOVE = null; renderMap(); renderPanel(); renderGantt(); };
    if ($('move-btn')) $('move-btn').onclick = () => moveOptions(sel);
    if ($('field-finish')) $('field-finish').onclick = () => fieldFinish(sel);
    document.querySelectorAll('.mopt[data-b]').forEach(b => b.onclick = () => doMove(b.dataset.b));
    return;
  }
  if (T != null && !atEvent()) { $('panel').innerHTML = `<div class="ptitle"><span>Бригады в ${fmt(T)}</span></div>` + PLAYROWS.join(''); return; }
  if (evOn()) $('panel').innerHTML = eventPanel(R);
  else {
    // справа — своё для сценария (show), остальное по участку свёрнуто; без show — сводка участка и всё по участку раскрыто
    const want = String(SC && SC.show || '').split(',').filter(k => (BLOCKS[k] || ONLY[k]) && (BLOCKS[k] || ONLY[k])(R));
    const rest = Object.keys(BLOCKS).filter(k => !want.includes(k) && BLOCKS[k](R));
    $('panel').innerHTML = !want.length ? districtSummary(R) + rest.map(k => BLOCKS[k](R)).join('')
      : want.map(k => (BLOCKS[k] || ONLY[k])(R)).join('')
        + (rest.length ? `<details class="more"><summary>Ещё по участку: ${rest.map(k => BLOCK_T[k]).join(', ')}</summary>${rest.map(k => BLOCKS[k](R)).join('')}</details>` : '');
  }
  $('panel').querySelectorAll('tr.click, .chg-r').forEach(tr => {
    tr.onclick = () => select(+tr.dataset.k);
    tr.onmouseenter = tr.onfocus = () => hiOrder(+tr.dataset.k);
    tr.onmouseleave = tr.onblur = () => hiLayer.clearLayers();
  });
  $('panel').querySelectorAll('details.more').forEach(d => d.ontoggle = () => { if (d.open) $('panel').scrollTo({top: d.offsetTop - 8, behavior: 'smooth'}); });
}

// бригады под диаграммой: резерв, навыки и транспорт (свёрнуто), факт выездов по отметкам инженеров
function renderBrigades() {
  const R = cur(), B = R.brigades, used = R.plan.routes.filter(rt => rt.stops.length), on = new Set(used.map(r => r.v));
  const reserve = B.filter(b => !on.has(b.v) && b.unavailable_from == null), n = FieldStatus.facts(R, regionSlug(), fieldVer()).length;
  const speed = b => b.speed && Math.abs(b.speed - 1) > 0.005 ? `на ${Math.round(Math.abs(1 - b.speed) * 100)}% ${b.speed < 1 ? 'быстрее' : 'медленнее'} норматива` : '';
  $('brigades').innerHTML = `<div class="bline"><span><b>В работе ${used.length} из ${B.length}.</b> В резерве: ${reserve.length ? brList(reserve.map(b => b.name).join(', ')) : 'никого'}</span>
    ${n ? `<button class="linkbtn" id="facts-csv" title="Фактическое время работ по отметкам инженеров — для уточнения длительностей">факт выездов ↓ ${n}</button>` : ''}</div>
    <details class="bdet"><summary>Транспорт, навыки и запас оборудования бригад</summary>
    <table class="t bt"><tr><th>Бригада</th><th>Транспорт</th><th>Навыки</th><th>С собой</th><th></th></tr>${used.map(rt => { const b = B[rt.v];
      return `<tr data-v="${rt.v}"><td class="nw"><span class="sw" style="background:${col(rt.v)}"></span> <b>${bn(b.name)}</b> ${stars(R, b.name)}</td><td class="nw">${esc(b.mode_name)}${shiftNote(b, ', смена ')}</td><td>${esc(b.skills.join(', '))}</td><td>${eqText(b.stock) || '—'}</td><td class="muted">${speed(b)}</td></tr>`; }).join('')}</table></details>`;
  $('brigades').querySelectorAll('tr[data-v]').forEach(r => {
    r.onmouseenter = () => { hl = +r.dataset.v; renderMap(); };
    r.onmouseleave = () => { hl = null; renderMap(); };
  });
  if ($('facts-csv')) $('facts-csv').onclick = () => FieldStatus.download(FieldStatus.factsCSV(FieldStatus.facts(R, regionSlug(), fieldVer())),
    `факт_выездов_${regionSlug()}_${(R.meta.date || 'день').replace(/\./g, '-')}.csv`);
}

function placeText(d) {
  const p = d.place, src = p.src || {}, bits = [], from = k => src[k] && SRC[src[k]] !== undefined ? ` <span class="muted">(${esc(SRC[src[k]])})</span>` : '';
  if (p.apt) bits.push(`кв. ${esc(p.apt)}${p.apt_assumed ? ' (нет в адресе)' : ''}`);
  if (p.entrance) bits.push(`подъезд ${esc(p.entrance)}${from('entrance')}`);
  if (p.floor) bits.push(`этаж ${esc(p.floor)}${from('floor')}`);
  if (p.lift != null) bits.push(p.lift ? 'лифт есть' : 'без лифта');
  return bits.join(', ');
}

function durationBlock(o) {
  const d = o.detail;
  if (!d) return '';
  const h = d.house, house = h ? [h.levels && `${h.levels} эт.`, h.flats && `${h.flats} кв.`, h.year && `${h.year} г.`, h.material && h.material.toLowerCase()].filter(Boolean).join(', ') : '';
  return `<div class="dur"><div class="dur-h"><b>Работа ${o.svc} мин</b><span class="muted">норматив ${d.base} мин</span></div>`
    + d.parts.slice(1).map(x => `<div class="dur-r"><span>${esc(cap(work(x.what)))}</span><b class="nw">${esc(x.value)}</b></div>`).join('')
    + (placeText(d) ? `<div class="dur-p">${placeText(d)}</div>` : '')
    + (house ? `<div class="dur-p muted">Дом: ${esc(house)}</div>` : '') + '</div>';
}

function orderCard(R, k) {
  const o = R.orders[k], B = R.brigades, p = o.plan, e = o.explanation;
  let h = `<div class="ocard"><button class="linkbtn" id="back">← Список</button>
    <div style="margin-top:10px">${badge(o.type)} <span class="muted">заявка ${esc(o.id)}</span></div>
    <h3>${esc(addrText(o.addr))}</h3><div class="muted">${o.district ? esc(o.district) + '; ' : ''}окно ${win(o)}; работа ${o.svc} мин${o.need ? '; только ' + esc(o.need.map(x => ({car: 'машина', pt: 'общ. транспорт', bike: 'велосипед', foot: 'пешком'}[x])).join(' или ')) : ''}</div>`;
  h += durationBlock(o);
  if (o.plan && Math.round(o.plan.end - o.plan.start) !== Math.round(o.svc))           // скорость работы бригады: дольше или быстрее норматива
    h += `<div class="muted" style="margin-top:6px">У ${bn(gen(B[o.plan.brigade].name))} — ${Math.round(o.plan.end - o.plan.start)} мин (скорость работы бригады)</div>`;
  if (o.equipment && Object.keys(o.equipment).length) h += `<div class="muted" style="margin-top:6px">Оборудование: ${eqText(o.equipment)}</div>`;
  if (p) {
    const b = B[p.brigade];
    h += `<div class="assign"><div style="display:flex;align-items:center;gap:8px;font-size:16px"><span class="sw" style="background:${col(p.brigade)}"></span><b>${bn(b.name)}</b><span class="muted">${esc(b.mode_name)}</span></div>
      <div>Приезд ${fmt(p.arrive)}, начало ${fmt(p.start)}, конец ${fmt(p.end)}.</div>${(() => { const c = R.early && earlyUseful(R) && R.early.calls.find(c => c.k === k); return c ? `<div class="earlyhint">Можно предложить начало в <b>${fmt(c.start)}</b>, на ${c.earlier} мин раньше окна</div>` : ''; })()}${p.rating ? `<div>Рейтинг по виду работ: <b>★ ${num2(p.rating)}</b></div>` : ''}${e ? `<div style="margin-top:4px">${tidy(cap(e.summary.replace(/^[^:]+: /, '')))}</div>` : ''}</div>`;
    h += fieldBlock(R, k);
    if (e && e.others && e.others.length) h += `<div style="font-weight:700;margin-top:14px">Почему не другие бригады</div>` + e.others.map(g => `<div class="why"><b>${brList(g.names)}</b> — ${tidy(g.text)}</div>`).join('');
  } else {
    h += `<div class="assign none"><b>${o.cancelled ? 'Отменена' : 'Не назначена'}</b><div>${tidy(o.reason ? o.reason.text : '')}</div></div>`;
  }
  if (MAN.api && p && !o.cancelled) h += `<button class="btn" id="move-btn" style="margin-top:12px">Передать другой бригаде</button>` + moveBlock();
  return h + '</div>';
}

// ---------- отметки инженеров: экран инженера пишет их в localStorage этого же сайта (status.js)
const fieldVer = () => evOn() && EVKEY && EVKEY[0] === 'p' ? +EVKEY.slice(1) : -1;
function fieldOf(R, rt) {
  const marks = FieldStatus.read(regionSlug(), fieldVer(), R.brigades[rt.v].name, FieldStatus.sig(R, rt.v));
  return {marks, p: FieldStatus.progress(rt.stops.map(s => FieldStatus.stop(R.orders[s.k], s)), marks)};
}
function fieldBadge(R, rt) {
  const {p} = fieldOf(R, rt);
  if (!p.last) return '';
  const t = FieldStatus.delayText(p.delay);
  return `<span class="fs ${t.cls}" title="Последняя отметка инженера: ${p.last.kind === 'done' ? 'закончил' : 'приехал'} в ${fmt(p.last.t)}">${t.text}</span>`;
}
function fieldBlock(R, k) {
  const o = R.orders[k], rt = o.plan && R.plan.routes.find(r => r.v === o.plan.brigade);
  const m = rt && fieldOf(R, rt).marks[String(o.id)];
  if (!m || m.arrived == null) return '';
  return `<div class="field"><b>Инженер:</b> приехал ${fmt(m.arrived)}${m.done != null ? ` / закончил ${fmt(m.done)}` : ''}
    <span class="muted">(план ${fmt(o.plan.arrive)}–${fmt(o.plan.end)})</span>
    ${MAN.api && m.done != null ? `<div><button class="btn" id="field-finish">Пересчитать: закончили в ${fmt(m.done)}</button></div>` : ''}</div>`;
}
function fieldFinish(k) {   // форма «закончили не по плану» с отметкой инженера; момент события — в пределах, где форма видит заявку
  const R = cur(), o = R.orders[k], m = fieldOf(R, R.plan.routes.find(r => r.v === o.plan.brigade)).marks[String(o.id)];
  FORM = {kind: 'finish', at: fmt(Math.min(Math.max(m.done, Math.floor(o.plan.start) + 1), o.plan.end + 59)), order: o.id, end: fmt(m.done), variants: null, base: R, E0: E, T0: T};
  sel = null; MOVE = null; T = null; render();
}

// смена показывается, только если она не весь день 10:00–22:00
const shiftNote = (b, pre, short) => {
  if (!b.shift || (b.shift[0] <= 0 && b.shift[1] >= 720)) return '';
  const h = m => (600 + m) % 60 ? fmt(m) : String((600 + m) / 60);
  return pre + (short ? `${h(b.shift[0])}–${h(b.shift[1])}` : esc(b.shift_text));
};
const engHref = name => `engineer.html#d=${encodeURIComponent(regionSlug())}${fieldVer() >= 0 ? '&v=' + fieldVer() : ''}&b=${encodeURIComponent(name)}`;
const PHONE = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="1.5" width="14" height="21" rx="3.2" fill="#141414"/><rect x="6.8" y="4.2" width="10.4" height="13.6" rx="1.4" fill="#FFC800"/><path d="M8.9 15.2l2.4-3.1 2.1 1.4 2.3-4.1" fill="none" stroke="#141414" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><rect x="10" y="19.4" width="4" height="1.3" rx=".65" fill="#FFC800"/></svg>';
const gx = f => `calc(var(--gl) + (100% - var(--gl) - 14px) * ${f})`;     // позиция времени на диаграмме

function renderGantt() {
  const R = cur(), ev = evOn();
  const P = m => `${m / 7.2}%`, chg = new Set(ev ? E.event.changes.map(c => c.k) : []);
  // бригада из резерва: в плане до события маршрута не было — работает с момента события
  const before = ev ? new Set(parent().plan.routes.filter(r => r.stops.length).map(r => r.v)) : null;
  let s = `<div class="gantt"><div class="gaxis">${[...Array(13).keys()].map(i => `<span class="tick" style="left:${gx(i / 12)}">${fmt(i * 60)}</span>`).join('')}</div>`;
  R.plan.routes.forEach(rt => {
    const b = R.brigades[rt.v], [s0, s1] = b.shift || [0, 720], fresh = ev && rt.stops.length && !before.has(rt.v);
    let prev = fresh ? Math.max(s0, E.event.at) : s0;
    const info = fieldBadge(R, rt) || `<small>${esc(b.mode_name.replace('Общ. транспорт', 'Общ. тр.'))} · ${km1(rt.km)} км${fresh ? ` · из резерва` : ''}${shiftNote(b, ' · ', true)}</small>`;
    s += `<div class="grow2" data-b="${esc(b.name)}" data-v="${rt.v}"><div class="glabel"><span class="sw" style="background:${col(rt.v)}"></span><span class="gn"><b>${bn(b.name)}</b>${info}</span>`
      + `<a class="phone" href="${engHref(b.name)}" title="Экран инженера" aria-label="Экран инженера: ${esc(b.name)}">${PHONE}</a></div><div class="track">`;
    if (s0 > 0) s += `<i class="off" title="вне смены" style="left:0;width:${P(s0)}"></i>`;
    if (s1 < 720) s += `<i class="off" title="вне смены" style="left:${P(s1)};width:${P(720 - s1)}"></i>`;
    if (fresh && prev > s0) s += `<i class="off rsv" title="в резерве до ${fmt(prev)}" style="left:${P(s0)};width:${P(prev - s0)}"></i>`;
    rt.stops.forEach(st => {
      const o = R.orders[st.k];
      if (st.arr > prev) s += `<i class="travel" style="left:${P(prev)};width:${P(st.arr - prev)};background:${col(rt.v)}"></i>`;
      if (st.beg > st.arr + 1) s += `<i class="wait" style="left:${P(st.arr)};width:${P(st.beg - st.arr)}"></i>`;
      const dur = st.end - st.beg;
      s += `<i tabindex="0" role="button" class="work${o.type === 'Авария' ? ' avar' : ''}${sel === st.k ? ' sel' : ''}${chg.has(st.k) ? ' chg' : ''}" data-k="${st.k}" ${MAN.api && !(R.event && st.beg < R.event.at) ? 'draggable="true"' : ''} title="${fmt(st.beg)}–${fmt(st.end)} ${esc(o.type)}, ${esc(o.district)}" style="left:${P(st.beg)};width:${P(dur)};background:${col(rt.v)}">${dur >= 30 ? TS[o.type] || '' : ''}</i>`;
      prev = st.end;
    });
    if (rt.lunch) s += `<i class="lunch" title="обед ${fmt(rt.lunch[0])}–${fmt(rt.lunch[1])}" style="left:${P(rt.lunch[0])};width:${P(rt.lunch[1] - rt.lunch[0])}">обед</i>`;
    s += '</div></div>';
  });
  if (E) s += `<div class="nowline" title="событие ${fmt(E.event.at)}" style="left:${gx(E.event.at / 720)}"></div>`;
  s += `<div class="tline${T == null ? ' hidden' : ''}" style="left:${gx((T || 0) / 720)}"></div>`;
  $('gantt').innerHTML = s + '</div>';
  $('gantt').querySelectorAll('.work').forEach(w => w.onclick = () => select(+w.dataset.k, true));
  $('gantt').querySelectorAll('.grow2').forEach(r => {
    const v = +r.dataset.v, lab = r.querySelector('.glabel');
    lab.onmouseenter = () => { hl = v; renderMap(); };
    lab.onmouseleave = () => { hl = null; renderMap(); };
    lab.onclick = e => { if (e.target.closest('a')) return; const rt = cur().plan.routes.find(x => x.v === v); if (rt) map.fitBounds(L.latLngBounds(routeLine(cur(), rt)), PAD); };
  });
  if (MAN.api) bindDrag();
}

// перетаскивание работы на строку другой бригады: цена передачи — при наведении, передача — по отпусканию
let DRAG = null;
function bindDrag() {
  const tip = $('drag-tip');
  $('gantt').querySelectorAll('.work[draggable]').forEach(w => {
    w.ondragstart = e => {
      const R = cur(), k = +w.dataset.k, at = R.event ? R.event.at : 0;
      DRAG = {k, at, opts: null};
      e.dataTransfer.setData('text/plain', String(k));
      api('options', {region: regionSlug(), doc: R, at, order: R.orders[k].id})
        .then(r => { if (DRAG && DRAG.k === k) DRAG.opts = Object.fromEntries(r.options.map(o => [o.name, o])); })
        .catch(() => { DRAG = null; });
    };
    w.ondragend = () => { DRAG = null; tip.classList.add('hidden'); $('gantt').querySelectorAll('.grow2').forEach(r => r.classList.remove('drop-ok', 'drop-no')); };
  });
  $('gantt').querySelectorAll('.grow2').forEach(row => {
    row.ondragover = e => {
      if (!DRAG) return;
      e.preventDefault();
      const o = DRAG.opts && DRAG.opts[row.dataset.b];
      $('gantt').querySelectorAll('.grow2').forEach(r => r.classList.remove('drop-ok', 'drop-no'));
      row.classList.add(o && o.ok ? 'drop-ok' : 'drop-no');
      tip.textContent = !DRAG.opts ? 'Считаем…' : !o ? 'та же бригада' : o.ok
        ? `${row.dataset.b}: ${o.km >= 0 ? '+' : '−'}${km1(Math.abs(o.km))} км${o.shifted ? `, сдвинет ${o.shifted}` : ''}` : `${row.dataset.b}: ${o.why}`;
      tip.style.left = e.clientX + 14 + 'px'; tip.style.top = e.clientY + 14 + 'px'; tip.classList.remove('hidden');
    };
    row.ondrop = async e => {
      e.preventDefault();
      const d = DRAG;
      tip.classList.add('hidden');
      if (!d) return;
      if (!d.opts) {                                   // отпустили раньше, чем пришли цены, — ждём их
        const R = cur(), r = await api('options', {region: regionSlug(), doc: R, at: d.at, order: R.orders[d.k].id});
        d.opts = Object.fromEntries(r.options.map(x => [x.name, x]));
      }
      const o = d.opts[row.dataset.b];
      if (!o) return;
      if (!o.ok) return alert(o.why);
      MOVE = {k: d.k, at: d.at, options: Object.values(d.opts)};
      await doMove(row.dataset.b);
    };
  });
}

function renderBottom() {
  const R = cur(), O = R.orders;
  const un = O.map((o, k) => ({o, k})).filter(x => !x.o.plan && !x.o.cancelled);
  $('unassigned').innerHTML = `<div class="ptitle"><span>Без исполнителя</span><span class="muted">${un.length}</span></div>`
    + (un.length ? `<table class="t"><tr><th>Заявка</th><th>Окно</th><th>Причина</th></tr>${un.map(x => `<tr class="click" tabindex="0" role="button" data-k="${x.k}"><td class="nw">${badge(x.o.type)} ${esc(x.o.id)}<div class="muted">${esc(x.o.district)}</div></td><td>${win(x.o)}</td><td>${tidy(x.o.reason ? x.o.reason.text : '')}</td></tr>`).join('')}</table>`
      : `<div style="padding:0 16px 14px">Все заявки назначены.</div>`)
    + (un.length && R.shortage && R.shortage.length ? `<div class="short"><b>Чтобы закрыть все заявки</b>${R.shortage.map((x, i) => x.mode
        ? `<div>+${i + 1} бригада: <b>${esc(x.mode_name)}</b>; ${esc(x.skills.join(', '))}; смена ${esc(x.shift)}; старт: ${esc(addrText(x.start))} <span class="muted">закроет ${esc(x.orders.join(', '))}</span></div>`
        : `<div class="muted">Не закрываются дополнительными бригадами: ${esc(x.orders.join(', '))}</div>`).join('')}</div>` : '');
  $('unassigned').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => select(+tr.dataset.k, true));
  $('unassigned').classList.toggle('hidden', !un.length);              // все назначены — карточки нет
  $('compare').classList.toggle('wide', !un.length);
  const m = R.plan.metrics, total = (a, b) => Math.max(a, b, 1);
  // пробег по бригадам — полосой: отрезок бригады длиной в её км, общая длина — весь пробег плана
  const strip = (rs, kmTotal, max) => `<div class="strip">${rs.map(r => { const w = r.km / max * 100;
    return `<i style="width:${w}%;background:${col(r.v)}" title="${esc(r.brigade)}: ${km1(r.km)} км">${w > 4.5 ? esc(short(r.brigade)) : ''}</i>`; }).join('')}<span>${km1(kmTotal)} км</span></div>`;
  const pair = (l, ours, theirs, d, good, lead, pre = 'у базового') => `<div class="cmp${lead && d ? ' lead' : ''}"><div class="l">${l}</div><div class="v">${ours}</div>
    <div class="d">${pre} ${theirs}${d ? ` <b class="${good ? 'good' : 'bad'}">${d}</b>` : ''}</div></div>`;
  const ours = R.plan.routes.filter(r => r.stops.length);
  if (evOn()) {                                     // после события сравниваем с планом до события, а не с базовым
    const P = parent(), pm = P.plan.metrics, bf = E.event.before, was = P.plan.routes.filter(r => r.stops.length), max = total(m.km_total, pm.km_total);
    const dv = m.brigades_used - bf.brigades_used, dk = m.km_total - bf.km_total, du = m.unassigned - bf.unassigned;
    $('compare').innerHTML = `<div class="ptitle"><span>До события и после</span><span class="muted">${fmt(E.event.at)} — ${eventTitle(E.event)}</span></div>
      <div class="cmps">${pair('Бригад', m.brigades_used, bf.brigades_used, dv ? signed(dv, x => x) : '', dv <= 0, false, 'до события')}
        ${pair('Пробег', km1(m.km_total) + ' км', km1(bf.km_total) + ' км', signed(dk) + ' км', dk <= 0.05, false, 'до события')}
        ${pair('Без исполнителя', m.unassigned, bf.unassigned, du ? signed(du, x => x) : '', du <= 0, false, 'до события')}</div>
      <div class="strips"><div class="sl">До события<small>${pm.served} из ${pm.orders} заявок</small></div>${strip(was, pm.km_total, max)}<div class="sl">После<small>${m.served} из ${m.orders} заявок</small></div>${strip(ours, m.km_total, max)}</div>`;
    return;
  }
  const bs = D.baseline, b = bs.metrics, base = bs.routes.filter(r => r.stops.length), max = total(m.km_total, b.km_total);
  const dv = m.brigades_used - b.brigades_used, dk = m.km_total - b.km_total, ds = m.served - b.served;
  const per = x => x.served ? x.km_total / x.served : 0, dp = per(m) - per(b);
  // базовый выполнил меньше — сравнивать общий пробег нечестно: сравниваем пробег на выполненную заявку
  const kmPair = ds > 0
    ? pair('Пробег на заявку', km1(per(m)) + ' км', km1(per(b)) + ' км', signed(dp) + ' км', dp <= 0)
      + `<div class="cmp-note">Всего ${km1(m.km_total)} км против ${km1(b.km_total)}: базовый вариант проехал ${dk > 0 ? 'меньше' : 'больше'}, но не выполнил ${pl(b.orders - b.served, 'заявку', 'заявки', 'заявок')}.</div>`
    : pair('Пробег', km1(m.km_total) + ' км', km1(b.km_total) + ' км', signed(dk) + ' км', dk <= 0);
  $('compare').innerHTML = `<div class="ptitle"><span>Наш план и базовый вариант</span><span class="muted">базовый: заявки раздаются по очереди</span></div>
    <div class="cmps">${pair('Выполнено заявок', `${m.served} из ${m.orders}`, `${b.served} из ${b.orders}`, ds ? signed(ds, x => x) : '', ds >= 0, true)}
      ${pair('Бригад', m.brigades_used, b.brigades_used, dv ? signed(dv, x => x) : '', dv <= 0)}
      ${kmPair}</div>
    <div class="strips"><div class="sl">Наш план<small>${m.served} из ${m.orders} заявок</small></div>${strip(ours, m.km_total, max)}<div class="sl">Базовый<small>${b.served} из ${b.orders} заявок</small></div>${strip(base, b.km_total, max)}</div>
    ${bs.unassigned.length ? `<details class="more"><summary>Почему базовый вариант не выполнил ${pl(bs.unassigned.length, 'заявку', 'заявки', 'заявок')}</summary>
      <table class="t">${bs.unassigned.slice(0, 40).map(u => `<tr><td class="nw">${badge(D.orders[u.k].type)} ${esc(D.orders[u.k].id)}</td><td>${tidy(u.text)}</td></tr>`).join('')}</table></details>` : ''}`;
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
  if (rt.geometry) {
    const g = rt.geometry.slice(), p = rt.via ? rt.via.pos : -1;
    if (p >= 0 && g.length === rt.stops.length + 1)   // точка разворота делит переезд к заявке pos на две линии
      g.splice(p, 2, g[p].concat(g[p + 1].slice(1)));
    return g;
  }
  const b = R.brigades[rt.v]; const pts = [[b.lat, b.lon]].concat(rt.stops.map(s => [R.orders[s.k].lat, R.orders[s.k].lon]));
  return pts.slice(1).map((p, i) => [pts[i], p]);
}
function stateAt(R, rt, t) {
  const Lg = legsOf(R, rt); let pe = 0;
  for (let i = 0; i < rt.stops.length; i++) {
    const s = rt.stops[i], o = R.orders[s.k], q = [o.lat, o.lon];
    if (t < s.arr) { const part = cut(Lg[i], Math.max(0, Math.min(1, (t - pe) / Math.max(1, s.arr - pe)))); return {path: Lg.slice(0, i).flat().concat(part), ll: part[part.length - 1], st: 'в пути', to: 'к ' + (o.district || o.type.toLowerCase())}; }
    if (t < s.beg) return {path: Lg.slice(0, i + 1).flat(), ll: q, st: 'ждёт окно', to: o.district};
    if (t < s.end) return {path: Lg.slice(0, i + 1).flat(), ll: q, st: 'работает', to: `${o.type.toLowerCase()}, до ${fmt(s.end)}`};
    pe = s.end;
  }
  const last = R.orders[rt.stops[rt.stops.length - 1].k];
  return {path: Lg.flat(), ll: [last.lat, last.lon], st: 'закончила', to: ''};
}
let PLAYROWS = [];
// бригада вызвана событием: в плане до события у неё не было маршрута
function reserveCalled(v) {
  if (!evOn() || !E || !E.event) return false;
  const was = parent().plan.routes.find(rt => rt.v === v);
  return !was || !was.stops.length;
}

function drawPlay() {
  const R = cur(), t = T, focus = atEvent() && evOn();
  const e = focus ? E.event : null, chgBr = focus ? affected(e) : null, changed = new Set(focus ? e.changes.map(c => c.k) : []);
  let done = 0; const rows = [];
  FOCUS = [];
  let av = false;
  R.plan.routes.forEach(rt => {
    if (!rt.stops.length) return;
    const st = stateAt(R, rt, t), dim = focus && !chgBr.has(rt.v);
    // впереди — пунктиром, пройденное — сплошной линией поверх; в момент события остальные маршруты чуть приглушены,
    // но остаются на месте — карта не «переключается», а подсвечивает затронутое
    routeDraw(routeLine(R, rt), col(rt.v), {weight: 3, opacity: dim ? .55 : .8, dash: '6 7'});
    routeDraw(st.path, col(rt.v), {weight: 5, opacity: dim ? .6 : .95, casing: !dim});
    rt.stops.forEach(s => { const o = R.orders[s.k], fin = s.end <= t, a = o.type === 'Авария'; done += fin; av ||= a;
      L.circleMarker([o.lat, o.lon], {radius: a ? 7 : 5.5, color: a ? '#C4231C' : '#fff', weight: a ? 2.5 : 1.5, fillColor: col(rt.v), fillOpacity: fin ? .3 : dim ? .6 : 1, opacity: fin ? .4 : dim ? .7 : 1, className: 'ord', interactive: false}).addTo(layer);
      if (focus && changed.has(s.k)) { L.circleMarker([o.lat, o.lon], {radius: 11, color: '#141414', weight: 2, fill: false, interactive: false}).addTo(layer); FOCUS.push([o.lat, o.lon]); } });
    L.marker(st.ll, {keyboard: false, zIndexOffset: dim ? 800 : 1000, icon: icon(pinHtml(rt.v, R.brigades[rt.v].name))}).addTo(layer);
    if (reserveCalled(rt.v) && t >= E.event.at)                              // вызвана событием: подпись, откуда она взялась
      L.marker(st.ll, {keyboard: false, interactive: false, zIndexOffset: 1100,
        icon: L.divIcon({className: '', html: '<div class="rsv-tag">из резерва</div>', iconSize: [0, 0]})}).addTo(layer);
    if (focus && !dim) FOCUS.push(st.ll);
    rows.push(`<div class="brow"><span class="sw" style="background:${col(rt.v)}"></span><span class="n">${bn(R.brigades[rt.v].name)}</span><span class="x"><b>${st.st}</b> ${esc(st.to)}</span></div>`);
  });
  if (E && E.event && t < E.event.at)                    // до события: бригады, которые оно вызовет, ждут дома
    E.plan.routes.filter(rt => rt.stops.length && !(parent().plan.routes.find(x => x.v === rt.v) || {stops: []}).stops.length)
      .forEach(rt => { const b = E.brigades[rt.v];
        L.marker([b.lat, b.lon], {keyboard: false, opacity: .55, zIndexOffset: 700, icon: icon(pinHtml(rt.v, b.name))})
          .bindTooltip(`${bn(b.name)}: в резерве, дома`).addTo(layer);
        L.marker([b.lat, b.lon], {keyboard: false, interactive: false, zIndexOffset: 1100,
          icon: L.divIcon({className: '', html: '<div class="rsv-tag muted">в резерве</div>', iconSize: [0, 0]})}).addTo(layer); });
  L.marker([D.office.lat, D.office.lon], {keyboard: false, icon: icon('<div class="office"></div>')}).addTo(layer);
  if (focus) {
    (e.turned || []).forEach(x => { turnMark(x, true); FOCUS.push([x.lat, x.lon]); });   // остановились на событии
    const eo = eventOrder(e, R);
    if (eo) { evMark(eo); FOCUS.push(orderPoint(eo.o)); }
  }
  $('play-panel').innerHTML = `<div class="card clock"><div class="t">${fmt(t)}</div><div>Выполнено <b>${done}</b> из ${R.plan.metrics.served}</div></div><div class="grow"></div>`;
  PLAYROWS = rows;
  mapLegend([['line', 'пройдено'], ['dash', 'впереди'], ['dot', 'заявка'], av && ['av', 'авария'], focus && changed.size && ['chg', 'изменилась'], ['pin', 'где сейчас бригада'], ['office', 'офис']]);
}

// шкала дня под картой: время, отметка события, «весь день»
function timeline() {
  const te = E ? E.event.at : null;
  $('play-range').value = T ?? 0;
  $('play-time').textContent = T == null ? 'весь день' : fmt(T);
  $('play-all').classList.toggle('hidden', T == null);
  $('play-panel').classList.toggle('hidden', T == null);
  $('tl-ev').classList.toggle('hidden', te == null);
  if (te != null) {
    $('tl-ev').style.left = `calc(8px + (100% - 16px) * ${te / 720})`;
    $('tl-ev').textContent = fmt(te);
    const tip = document.createElement('div'); tip.innerHTML = cap(eventTitle(E.event, E));   // текст без экранирования
    $('tl-ev').title = tip.textContent;
  }
}

function setT(t) {
  const was = cur(), wasAt = atEvent(); T = t;
  if (cur() !== was) { sel = null; render(); return; }
  if (atEvent() !== wasAt) renderKpis();
  renderMap(); renderPanel(); timeline();
  const n = $('gantt').querySelector('.tline');
  if (n) { n.classList.toggle('hidden', T == null); n.style.left = gx((T || 0) / 720); }
}

function togglePlay() {
  if (playTimer) return stopPlay();
  if (T == null || T >= 720) setT(0);
  $('play-btn').textContent = 'Пауза'; $('play-btn').classList.add('on');
  playTimer = setInterval(() => {
    const te = E ? E.event.at : null, nt = Math.min(720, T + 2);
    if (te != null && T < te && nt >= te) { stopPlay(); return setT(te); }   // остановка на событии; вид карты не трогаем
    setT(nt);
    if (nt >= 720) stopPlay();
  }, 50);
}
function stopPlay() {
  const was = !!playTimer;
  if (playTimer) clearInterval(playTimer); playTimer = null; $('play-btn').textContent = 'Пуск'; $('play-btn').classList.remove('on');
  if (was && atEvent()) { renderMap(); renderPanel(); }
}

// ---------- тур по экрану: подсветка элемента, остальное затемнено, карточка с пояснением
const TOUR = [
  ['.scn', 'Сценарий', 'Что показываем. Сверху — планы участков, ниже — сценарии по требованиям: рейтинг, сложность работ, старт из дома и авария в пути, изменения дня, сколько людей нужно. Стрелки — соседний сценарий.'],
  ['#ctx', 'Где вы сейчас', 'Участок, дата плана и коротко — что видно в этом сценарии.'],
  ['.kpiwrap', 'Главные цифры', 'Заявки, бригады, пробег и сколько заявок без исполнителя — с базовым вариантом мелко под числом. Ниже значки: доказанный минимум бригад, опоздания при планировании по нормативу. После события — что изменилось.'],
  ['#map', 'Карта', 'Маршруты бригад по улицам, точки — заявки у подъезда. Обозначения — в левом нижнем углу. Клик по точке — карточка заявки: почему эта бригада и из чего сложилось время работы.'],
  ['#play-bar', 'Шкала дня', '«Пуск» показывает, как бригады едут в течение дня. Красная отметка — событие: до неё утренний план, после — пересчитанный.'],
  ['#panel', 'Панель', 'Итог по участку, карточка заявки или что изменилось после события. В сценариях сверху — то, о чём сценарий: кривая, свободные окна, цена правил.'],
  ['#gantt-card', 'Диаграмма дня', 'Работы каждой бригады по времени, обед — жёлтым. Клик по работе — карточка заявки. Значок телефона в строке — экран инженера этой бригады.'],
  ['.bottom', 'Ниже — подробности', 'Заявки без исполнителя с причиной и какой бригады не хватает; сравнение с базовым вариантом, а после события — «до и после».'],
  ['#ev-btn', 'Событие', 'Ввести аварию, отмену, поломку или перенос окна — сервер предложит варианты пересчёта.'],
  ['#eng-link', 'Экран инженера', 'День бригады на телефоне: адреса, подъезд и этаж, кому позвонить, отметки «приехал» и «закончил».'],
];
let TI = -1;
function tourSteps() { return TOUR.filter(([s]) => { const e = document.querySelector(s); return e && e.offsetParent !== null; }); }
function tour(i) {
  const steps = tourSteps();
  if (i < 0 || i >= steps.length) { tourEnd(); return; }
  TI = i; menu(false);
  const [sel, title, text] = steps[i], el = document.querySelector(sel);
  el.scrollIntoView({block: 'nearest'});
  let box = $('tour');
  if (!box) {
    box = document.createElement('div'); box.id = 'tour';
    box.innerHTML = '<div class="tour-hole"></div><div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-t"></div>';
    document.body.appendChild(box);
    box.addEventListener('click', e => { if (e.target === box) tourEnd(); });
    window.addEventListener('resize', tourPlace); window.addEventListener('scroll', tourPlace, true);
  }
  box.querySelector('.tour-card').innerHTML = `<div class="tour-n">${i + 1} из ${steps.length}</div><b id="tour-t">${esc(title)}</b><p>${esc(text)}</p>
    <div class="tour-nav"><button class="linkbtn" id="tour-skip">Пропустить</button><span class="grow"></span>
      ${i ? '<button class="btn" id="tour-prev">Назад</button>' : ''}<button class="btn primary" id="tour-next">${i < steps.length - 1 ? 'Далее' : 'Понятно'}</button></div>`;
  $('tour-skip').onclick = tourEnd; $('tour-next').onclick = () => tour(i + 1);
  if ($('tour-prev')) $('tour-prev').onclick = () => tour(i - 1);
  tourPlace(); $('tour-next').focus();
}
function tourPlace() {
  const box = $('tour'); if (!box || TI < 0) return;
  const el = document.querySelector(tourSteps()[TI][0]); if (!el) return;
  const r = el.getBoundingClientRect(), pad = 6, hole = box.querySelector('.tour-hole'), card = box.querySelector('.tour-card');
  Object.assign(hole.style, {left: r.left - pad + 'px', top: r.top - pad + 'px', width: r.width + 2 * pad + 'px', height: r.height + 2 * pad + 'px'});
  const cw = Math.min(360, innerWidth - 24), ch = card.offsetHeight;
  let top = r.bottom + pad + 12;
  if (top + ch > innerHeight - 12) top = r.top - pad - 12 - ch;                // не помещается снизу — сверху
  if (top < 12) top = Math.max(12, Math.min(innerHeight - ch - 12, r.top + 12)); // крупный элемент — поверх него
  const left = Math.max(12, Math.min(innerWidth - cw - 12, r.left + r.width / 2 - cw / 2));
  Object.assign(card.style, {width: cw + 'px', left: left + 'px', top: top + 'px'});
}
function tourEnd() {
  TI = -1; const box = $('tour'); if (box) box.remove();
  window.removeEventListener('resize', tourPlace); window.removeEventListener('scroll', tourPlace, true);
  try { localStorage.setItem('pulse.tour', '1'); } catch (e) { /* без хранилища тур покажется снова */ }
}
document.addEventListener('keydown', e => {
  if (TI < 0) return;
  if (e.key === 'Escape') tourEnd();
  else if (e.key === 'ArrowRight') tour(TI + 1);
  else if (e.key === 'ArrowLeft' && TI > 0) tour(TI - 1);
});

init().catch(e => { document.querySelector('main').innerHTML = `<div class="card" style="padding:20px">Данные не загружены: ${esc(e.message)}. Откройте страницу через веб-сервер: <code>python -m dispatch serve</code>.</div>`; });
