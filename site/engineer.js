// Экран инженера: маршрут одной бригады на день. Те же данные, что у диспетчера: manifest.json → plan.json / event_*.json.
// Адрес страницы: engineer.html#d=<slug или название участка>&v=<номер события или пусто>&b=<название бригады>.
// Поля order.equipment, brigade.stock / brigade.carry и route.lunch необязательны: блоки показываются, только если они есть.
const fmt = m => { m = Math.round(m) + 600; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(((m % 60) + 60) % 60).padStart(2, '0'); };
const km1 = x => (Math.round(x * 10) / 10).toFixed(1).replace('.', ',');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const $ = id => document.getElementById(id);
const TC = {'Авария': 'ta', 'Подключение': 'tc', 'Ремонт': 'tl', 'Дозаказ': 'td'};
const badge = t => `<span class="badge ${TC[t] || 'tl'}">${esc(t)}</span>`;
const RTT = {car: 'auto', pt: 'mt', bike: 'bc', foot: 'pd'};
const EQ = {router: 'Роутер', stb: 'ТВ-приставка'};
const eqName = k => EQ[k] || k;
// «Город Москва, ул.Михайлова, д. 14» → «ул. Михайлова, д. 14»; срочная заявка по координатам — «точка на карте»
const shortAddr = a => /^\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*$/.test(a || '') ? 'точка на карте (' + a.trim() + ')'
  : String(a || '').replace(/^(г\.?\s*)?(город\s+)?Москва,?\s*/i, '').replace(/(^|[\s,(])([А-Яа-яЁё-]{1,6})\.(?=[А-Яа-яЁё0-9])/g, '$1$2. ').replace(/(^|[\s,])(проезд|пр-кт)\.\s*/g, '$1$2 ');
const noApt = a => a.replace(/,?\s*кв\.?\s*[^,\s]+\s*$/i, '');               // квартира — отдельной плашкой
// название работы без внутренних кодов: «TVE/ENT. Другие ошибки» → «Телевидение: другие ошибки»
const workName = o => { const t = work(o.hd && o.hd !== o.type ? o.hd : o.type); return t.charAt(0).toUpperCase() + t.slice(1); };
// телефон клиента (в демо — номер с кодом +7 000, его ставит сборка сайта)
const phoneOf = o => { const d = o.detail || {}, v = o.phone || o.tel || o.client_phone || d.phone || (o.client || {}).phone; return v ? String(v) : ''; };
const telHref = v => { let d = v.replace(/[^\d+]/g, ''); if (/^8\d{10}$/.test(d)) d = '+7' + d.slice(1); else if (/^7\d{10}$/.test(d)) d = '+' + d; return 'tel:' + d; };
const plural = (n, one, few, many) => { const a = n % 10, b = n % 100; return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 10 || b >= 20) ? few : many; };
const ll = (lat, lon) => `${(+lat).toFixed(6)},${(+lon).toFixed(6)}`;
const yaRoute = (pts, mode) => `https://yandex.ru/maps/?rtext=${pts.map(p => ll(p[0], p[1])).join('~')}&rtt=${RTT[mode] || 'auto'}`;

let MAN, DI = 0, VI = -1, DOC, BV = null, map, layer, PICK_OPEN = true;

async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); }

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  return {d: h.get('d'), v: h.get('v'), b: h.get('b')};
}
function writeHash() {
  const d = MAN.districts[DI], b = DOC && BV != null ? DOC.brigades[BV] : null;
  const p = new URLSearchParams();
  p.set('d', d.slug); if (VI >= 0) p.set('v', VI); if (b) p.set('b', b.name);
  history.replaceState(null, '', '#' + p.toString().replace(/\+/g, '%20'));
  $('to-dispatch').href = `index.html#d=${encodeURIComponent(d.slug)}${VI >= 0 ? '&v=' + VI : ''}`;   // тот же план или событие
}

async function init() {
  MAN = await getJSON('manifest.json');
  const h = readHash();
  const want = (h.d || '').toLowerCase();
  DI = Math.max(0, MAN.districts.findIndex(d => d.slug === h.d || d.name.toLowerCase() === want));
  $('district').innerHTML = MAN.districts.map((d, i) => `<option value="${i}">${esc(d.name)}</option>`).join('');
  $('district').onchange = () => { DI = +$('district').value; VI = -1; load(); };
  $('version').onchange = () => { VI = +$('version').value; load(); };
  $('brigade').onchange = () => { BV = +$('brigade').value; render(); };
  $('pick-toggle').onclick = () => { PICK_OPEN = !PICK_OPEN; pickState(); };
  $('print-btn').onclick = () => window.print();
  PICK_OPEN = !h.b;                                    // бригада в адресе — выбор свёрнут в одну строку
  pickState();
  VI = h.v != null && h.v !== '' && !isNaN(+h.v) ? +h.v : -1;
  await load(h.b);
  window.addEventListener('hashchange', () => {   // ссылка с другой страницы или правка адреса вручную
    const x = readHash(), d = MAN.districts.findIndex(d => d.slug === x.d || d.name.toLowerCase() === (x.d || '').toLowerCase());
    const v = x.v != null && x.v !== '' && !isNaN(+x.v) ? +x.v : -1;
    if ((d >= 0 && d !== DI) || v !== VI) { if (d >= 0) DI = d; VI = v; load(x.b); return; }
    if (x.b && DOC) { const b = DOC.brigades.find(b => b.name === x.b); if (b && b.v !== BV) { BV = b.v; render(); } }
  });
}

function pickState() {
  $('pick').classList.toggle('closed', !PICK_OPEN);
  $('pick-toggle').setAttribute('aria-expanded', String(PICK_OPEN));
}
function pickSummary() {
  const d = MAN.districts[DI], b = DOC.brigades[BV], e = DOC.event;
  $('pick-sum').textContent = [b ? b.name : 'Бригада', d.name, e ? `план после ${fmt(e.at)}` : ''].filter(Boolean).join(' · ');
}

async function load(brigadeName) {
  const d = MAN.districts[DI], ev = d.events || [];
  if (VI >= ev.length) VI = -1;
  $('district').value = DI;
  $('version').innerHTML = `<option value="-1">Утренний план</option>` + ev.map((e, i) => `<option value="${i}">${esc(e.label)}</option>`).join('');
  $('version').value = VI;
  $('version').closest('label').classList.toggle('hidden', !ev.length);
  const prevName = brigadeName || (DOC && BV != null ? DOC.brigades[BV].name : null);
  DOC = await getJSON(VI >= 0 ? ev[VI].plan : d.plan);
  const routes = DOC.plan.routes, byName = DOC.brigades.find(b => b.name === prevName);
  BV = byName ? byName.v : (routes.length ? routes[0].v : 0);
  const used = new Set(routes.map(r => r.v));
  const opt = b => { const rt = routes.find(r => r.v === b.v); return `<option value="${b.v}">${esc(b.name)} — ${rt ? `${rt.stops.length} ${plural(rt.stops.length, 'адрес', 'адреса', 'адресов')}` : 'без заявок'}</option>`; };
  $('brigade').innerHTML = `<optgroup label="В работе">${DOC.brigades.filter(b => used.has(b.v)).map(opt).join('')}</optgroup>`
    + (DOC.brigades.some(b => !used.has(b.v)) ? `<optgroup label="В резерве">${DOC.brigades.filter(b => !used.has(b.v)).map(opt).join('')}</optgroup>` : '');
  render();
}

// ---------- отметки «приехал» и «закончил» (status.js). День плана в прошлом, поэтому «сейчас» задаётся часами на экране;
// после отметки часы переходят к ожидаемому следующему моменту: концу работ или приезду на следующий адрес.
const vkey = () => [MAN.districts[DI].slug, VI, DOC.brigades[BV].name];
const vsig = () => FieldStatus.sig(DOC, BV);
const marksNow = () => FieldStatus.read(...vkey(), vsig());
const fstops = stops => stops.map(st => FieldStatus.stop(st.o, {arr: st.arrive, beg: st.start, end: st.end}));
// В версии плана после события всё, что по плану закончено к моменту события, считается выполненным, а адрес, где
// бригада в этот момент работает, — начатым. Это допущение, а не отметки: в хранилище не пишется, отметка инженера важнее.
const EPS = 1e-6;
function marksEff(day) {
  const m = marksNow(), e = DOC.event;
  if (!e) return m;
  const out = Object.assign({}, m);
  day.stops.forEach(st => {
    const id = String(st.o.id);
    if (out[id]) return;
    if (st.end <= e.at + EPS) out[id] = {arrived: Math.round(st.start), done: Math.round(st.end), assumed: true};
    else if (st.start <= e.at + EPS) out[id] = {arrived: Math.round(st.start), assumed: true};
  });
  return out;
}
// последняя отметка инженера (не допущение) — её можно отменить
function lastReal(day, p, real) {
  if (!p.last) return null;
  const m = real[String(day.stops[p.last.i].o.id)];
  return m && m[p.last.kind] != null ? p.last : null;
}
const hm2min = v => { const m = /^(\d{1,2}):(\d{2})/.exec(v || ''); return m ? +m[1] * 60 + +m[2] - 600 : null; };
const CLOCK = 'dispatch.clock|';
function clockGet(day) {
  try { const v = localStorage.getItem(CLOCK + vkey().join('|')); if (v != null && v !== '' && !isNaN(+v)) return +v; } catch (e) { /* без хранилища */ }
  // по умолчанию: последняя отметка или первый приезд; в версии после события — не раньше момента события
  const p = FieldStatus.progress(fstops(day.stops), marksEff(day)), at = DOC.event ? Math.round(DOC.event.at) : null;
  if (p.last) return at != null ? Math.max(at, p.last.t) : p.last.t;
  return at != null ? at : day.stops.length ? Math.round(day.stops[0].arrive) : 0;
}
function clockSet(t) { try { localStorage.setItem(CLOCK + vkey().join('|'), String(Math.round(t))); } catch (e) { /* без хранилища */ } }
// расхождение с планом до FieldStatus.OK минут — «по плану» (план считает доли минуты, отметка — по часам телефона)
const vsPlan = d => d > FieldStatus.OK ? `на ${d} мин позже плана` : d < -FieldStatus.OK ? `на ${-d} мин раньше плана` : 'по плану';
const delayChip = d => d > FieldStatus.OK ? ['late', `опоздание ${d} мин`] : d < -FieldStatus.OK ? ['early', `опережение ${-d} мин`] : ['ok', 'по плану'];

function mark(day, i, kind) {
  const m = marksNow(), eff = marksEff(day), st = day.stops[i], id = String(st.o.id), prev = i > 0 ? eff[String(day.stops[i - 1].o.id)] : null;
  const cur = eff[id] || {};
  let t = clockGet(day);
  if (prev && prev.done != null) t = Math.max(t, prev.done);
  if (kind === 'done' && cur.arrived != null) t = Math.max(t, cur.arrived);
  m[id] = Object.assign(kind === 'done' && cur.arrived != null ? {arrived: cur.arrived} : {}, m[id], {[kind]: Math.round(t)});
  FieldStatus.write(...vkey(), m, vsig());
  const nx = day.stops[i + 1];
  if (kind === 'arrived') clockSet(Math.max(t, st.o.window[0]) + st.o.svc);
  else if (nx) clockSet(t + Math.max(0, Math.round(nx.arrive - st.end)));
  refresh();
}
function unmark(day) {
  const m = marksNow(), p = FieldStatus.progress(fstops(day.stops), marksEff(day)), last = lastReal(day, p, m);
  if (!last) return;
  const st = day.stops[last.i], id = String(st.o.id);
  clockSet(last.t);
  // «закончил» на адресе, начатом до события: приезд был допущением — убираем отметку целиком
  const assumedStart = DOC.event && st.start <= DOC.event.at + EPS && m[id].arrived === Math.round(st.start);
  if (last.kind === 'done' && !assumedStart) delete m[id].done; else delete m[id];
  FieldStatus.write(...vkey(), m, vsig());
  refresh();
}
function refresh() { const day = dayOf(DOC, BV); renderFact(day); renderStops(DOC, day); renderNext(day); renderDemo(day); renderMap(DOC, day, false); }

// сводка отметок: сколько адресов закрыто, опоздание или опережение
function renderFact(day) {
  if (!day.rt || !day.stops.length) { $('fact').innerHTML = ''; return; }
  const real = marksNow(), eff = marksEff(day), p = FieldStatus.progress(fstops(day.stops), eff), last = lastReal(day, p, real);
  const [cls, text] = delayChip(p.delay), n = day.stops.length;
  const closed = day.stops.filter(st => (eff[String(st.o.id)] || {}).done != null).length;
  const assumed = day.stops.filter(st => (eff[String(st.o.id)] || {}).assumed && eff[String(st.o.id)].done != null).length;
  let line;
  if (last) {
    const st = day.stops[last.i];
    line = `Закрыто ${closed} из ${n}. Последняя отметка: адрес ${last.i + 1}, ${last.kind === 'done' ? 'закончил' : 'приехал'} в ${fmt(last.t)}.`;
    if (st && last.kind === 'arrived') line += ` Работа до ${fmt(Math.max(last.t, st.o.window[0]) + st.o.svc)}.`;
  } else if (assumed) line = `К ${fmt(DOC.event.at)} по плану выполнено ${assumed} из ${n}. Отметки ставьте со следующего адреса.`;
  else line = 'На месте нажмите «Приехал», после работы — «Закончил». Кнопка внизу экрана и на карточке адреса.';
  $('fact').innerHTML = `<div class="card fact"><div class="fact-h"><h2>Отметки</h2>${last ? `<span class="fs ${cls}">${text}</span>` : '<span class="muted">ещё нет</span>'}</div>
    <div class="fact-l">${line}</div></div>`;
}

// часы для показа: план составлен на прошлый день, поэтому «сейчас» задаётся вручную; блок свёрнут внизу страницы
function renderDemo(day) {
  const box = $('demo-body');
  if (!day.rt || !day.stops.length) { $('demo').classList.add('hidden'); box.innerHTML = ''; return; }
  $('demo').classList.remove('hidden');
  const any = Object.keys(marksNow()).length, t = clockGet(day);
  box.innerHTML = `<div class="muted">План составлен на прошедший день, поэтому время «сейчас» для отметок задаётся здесь. После отметки часы сами переходят к следующему моменту по плану.</div>
    <div class="fact-clock"><label for="clock">Сейчас</label><input id="clock" type="time" value="${fmt(t)}" step="60">
      <button class="btn" type="button" data-shift="-5" aria-label="минус 5 минут">−5</button><button class="btn" type="button" data-shift="5" aria-label="плюс 5 минут">+5</button></div>
    ${any ? '<div><button class="linkbtn" type="button" id="facts-reset">Сбросить все отметки</button></div>' : ''}`;
  $('clock').onchange = e => { const v = hm2min(e.target.value); if (v != null) { clockSet(v); refresh(); } };
  box.querySelectorAll('[data-shift]').forEach(b => b.onclick = () => { clockSet(clockGet(day) + +b.dataset.shift); refresh(); });
  if ($('facts-reset')) $('facts-reset').onclick = () => { if (confirm('Сбросить все отметки этой бригады?')) { FieldStatus.write(...vkey(), {}); try { localStorage.removeItem(CLOCK + vkey().join('|')); } catch (e) { /* */ } refresh(); } };
}

// липкая панель внизу: следующий адрес и одна кнопка «Приехал» или «Закончил»
function renderNext(day) {
  const bar = $('nextbar');
  if (!day.rt || !day.stops.length) { bar.classList.add('hidden'); document.body.classList.remove('has-next'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden'); document.body.classList.add('has-next');
  const real = marksNow(), eff = marksEff(day), p = FieldStatus.progress(fstops(day.stops), eff), st = day.stops[p.cur];
  if (!st) {
    bar.innerHTML = `<div class="nb-in"><div class="nb-t"><div class="nb-l">Все адреса закрыты</div><div class="nb-a">Конец работ ${fmt(day.stops[day.stops.length - 1].end)}</div></div></div>`;
    return;
  }
  const m = eff[String(st.o.id)] || {}, shift = lastReal(day, p, real) && Math.abs(p.delay) > FieldStatus.OK ? p.delay : 0;
  const kind = m.arrived != null ? 'done' : 'arrived';
  const when = kind === 'done' ? `до ${fmt(Math.max(m.arrived, st.o.window[0]) + st.o.svc)}` : fmt(st.arrive + shift);
  bar.innerHTML = `<div class="nb-in"><span class="num">${st.i + 1}</span><a class="nb-t" href="#stop-${st.i + 1}"><div class="nb-l">${kind === 'done' ? 'На адресе' : 'Дальше'} · ${when}</div>
      <div class="nb-a">${esc(noApt(shortAddr(st.o.addr)))}</div></a>
    <button class="btn ${kind === 'done' ? 'dark' : 'primary'}" type="button" id="next-btn" data-next="${kind}" data-i="${st.i}">${kind === 'done' ? 'Закончил' : 'Приехал'}</button></div>`;
  $('next-btn').onclick = () => mark(day, st.i, kind);
}

// ---------- данные дня одной бригады
function dayOf(R, v) {
  const b = R.brigades[v], rt = R.plan.routes.find(r => r.v === v);
  const stops = rt ? rt.stops.map((s, i) => {
    const o = R.orders[s.k], p = o.plan || {};
    return {i, k: s.k, o, km: s.km, arrive: p.arrive ?? s.arr, start: p.start ?? s.beg, end: p.end ?? s.end};
  }) : [];
  return {b, rt, stops};
}

function equipOf(o) {   // {router: 1, stb: 1} или ['router', ...] → [[ключ, количество]]
  const e = o && o.equipment;
  if (!e) return [];
  if (Array.isArray(e)) { const m = {}; e.forEach(x => { const k = typeof x === 'string' ? x : (x && (x.name || x.kind)); if (k) m[k] = (m[k] || 0) + (+(x && x.count) || 1); }); return Object.entries(m); }
  if (typeof e === 'object') return Object.entries(e).filter(([, n]) => +n > 0).map(([k, n]) => [k, +n]);
  return [];
}
const asCounts = x => { const m = {}; equipOf({equipment: x}).forEach(([k, n]) => m[k] = n); return m; };
const eqText = pairs => pairs.map(([k, n]) => n > 1 ? `${eqName(k)} × ${n}` : eqName(k)).join(', ');

function hintsOf(R, st, b) {
  const o = st.o, d = o.detail || {}, pl = d.place || {}, h = d.house || {}, acc = d.access || [], out = [];
  const callAt = st.arrive - 15, s0 = (b.shift || [0])[0];
  out.push({t: callAt >= s0 ? `Позвонить клиенту в ${fmt(callAt)}, за 15 мин до приезда` : 'Позвонить клиенту при выезде', cls: 'call'});
  if (acc.includes('пропуск')) out.push({t: 'Накануне: попросить пропуск в офисное здание', cls: 'eve'});
  if (acc.includes('шлагбаум')) out.push(b.mode === 'car'
    ? {t: 'Накануне: шлагбаум, попросить открыть и назвать номер машины', cls: 'eve'}
    : {t: 'Закрытый двор: уточнить у клиента, как пройти', cls: ''});
  if (pl.apt_assumed) out.push({t: 'Нет номера квартиры, уточнить у клиента', cls: 'eve'});
  if (pl.lift === false && pl.floor >= 2) out.push({t: `${pl.floor} этаж без лифта, оборудование нести на руках`, cls: ''});
  else if (pl.lift === false && h.levels >= 4 && !pl.floor) out.push({t: `Дом ${h.levels} этажей без лифта`, cls: ''});
  if (h.year && h.year < 1960) out.push({t: `Дом ${h.year} г., возможна сложная проводка`, cls: ''});
  if (st.start > st.arrive + 5) out.push({t: `Приезд в ${fmt(st.arrive)}, окно с ${fmt(o.window[0])}: предложить клиенту начать раньше`, cls: ''});
  const early = R.early && (R.early.calls || []).find(c => c.k === st.k);
  if (early && !(st.start > st.arrive + 5)) out.push({t: `Предложить клиенту начать в ${fmt(early.start)}, на ${early.earlier} мин раньше окна`, cls: ''});
  return out;
}

function needsEveCall(st, b) {
  const d = st.o.detail || {}, acc = d.access || [], why = [];
  if (acc.includes('пропуск')) why.push('пропуск в офисное здание');
  if (acc.includes('шлагбаум') && b.mode === 'car') why.push('открыть шлагбаум, номер машины');
  if (d.place && d.place.apt_assumed) why.push('номер квартиры');
  return why;
}

// ---------- отрисовка
function render() {
  writeHash();
  $('brigade').value = BV;
  const R = DOC, day = dayOf(R, BV);
  document.title = `${day.b.name} — маршрут на ${R.meta.date || 'день'}`;
  pickSummary();
  renderDay(R, day); renderCalls(day); renderKit(day); renderFact(day); renderStops(R, day); renderNext(day); renderDemo(day); renderMap(R, day); renderSheet(R, day);
}

function renderDay(R, {b, rt, stops}) {
  const ev = R.event, last = stops[stops.length - 1];
  const chips = [b.mode_name, `смена ${b.shift_text || '10:00–22:00'}`, `${stops.length} ${plural(stops.length, 'адрес', 'адреса', 'адресов')}`];
  if (rt) chips.push(`${km1(rt.km)} км`);
  let h = `<div class="card eday">
    <div class="eday-top"><div><div class="muted">${esc(R.meta.name)}${R.meta.date ? ' · ' + esc(R.meta.date) : ''}</div><h1>${esc(b.name)}</h1></div></div>
    <div class="chips">${chips.map(c => `<span class="chip">${esc(c)}</span>`).join('')}</div>
    <div class="estart"><span class="dot-start"></span><div><div class="muted">Старт</div>${esc(shortAddr(b.start_addr) || 'офис')}</div></div>`;
  if (stops.length) h += `<div class="eday-sum"><div><b>${fmt(stops[0].arrive)}</b><span class="muted">первый адрес</span></div><div><b>${fmt(last.end)}</b><span class="muted">конец работ</span></div>
      ${(b.skills || []).length ? `<div class="skills"><span class="muted">Навыки:</span> ${esc(b.skills.join(', '))}</div>` : ''}</div>`;
  if (ev) h += eventNote(R, b);
  if (!rt) h += `<div class="enone">${ev && ev.kind === 'unavailable' && ev.brigade === b.v ? 'Бригада снята с маршрута: заявки переданы другим.' : 'Сегодня без заявок, бригада в резерве.'}</div>`;
  if (rt && stops.length > 1) h += `<a class="btn wide" target="_blank" rel="noopener" href="${yaRoute([[b.lat, b.lon]].concat(stops.map(s => [s.o.lat, s.o.lon])), b.mode)}">Маршрут дня в Яндекс Картах</a>`;
  $('day').innerHTML = h + '</div>';
}

function eventNote(R, b) {
  const e = R.event, mine = (e.changes || []).filter(c => (c.now && c.now.brigade === b.v) || (c.was && c.was.brigade === b.v));
  const label = (MAN.districts[DI].events[VI] || {}).label || fmt(e.at);
  const removed = mine.filter(c => c.was && c.was.brigade === b.v && !(c.now && c.now.brigade === b.v));
  const turned = (e.turned || []).find(t => t.brigade === b.v);
  return `<div class="enote"><b>Изменение плана в ${fmt(e.at)}</b><div>${esc(label.replace(/^\d\d:\d\d — /, ''))}</div>`
    + (mine.length ? `<div class="muted">Изменений у вас: ${mine.length}. Заявки, выполненные до ${fmt(e.at)}, без изменений.</div>` : `<div class="muted">Ваш маршрут не изменился.</div>`)
    + (turned ? `<div><b>Разворот.</b> Вместо заявки ${esc(R.orders[turned.was].id)} ехать ${turned.now != null ? 'к заявке ' + esc(R.orders[turned.now].id) : 'по новому маршруту'}</div>` : '')
    + removed.map(c => `<div>Заявка ${esc(R.orders[c.k].id)} ${c.now ? 'передана: ' + esc(R.brigades[c.now.brigade].name) : 'отменена'}</div>`).join('') + '</div>';
}

function renderCalls({b, stops}) {
  const rows = stops.map(st => ({st, why: needsEveCall(st, b)})).filter(x => x.why.length);
  $('calls').innerHTML = rows.length ? `<div class="card eblock"><div class="ehead"><h2>Обзвон накануне</h2><span class="muted">${rows.length} ${plural(rows.length, 'клиент', 'клиента', 'клиентов')}</span></div>
    ${rows.map(({st, why}) => `<a class="crow" href="#stop-${st.i + 1}"><span class="num">${st.i + 1}</span><div><div>${esc(shortAddr(st.o.addr))}</div><div class="muted">${fmt(st.start)} · заявка ${esc(st.o.id)} · ${esc(why.join('; '))}</div></div></a>`).join('')}</div>` : '';
}

// Комплект на день — один расчёт для телефона и маршрутного листа, те же числа, что в карточке бригады у диспетчера:
// «на маршрут» — сумма оборудования заявок маршрута, «взять» — выдано утром (brigade.stock, с запасом); без stock — по маршруту.
// carry — сколько единиц бригада унесёт (null — без предела).
function kitOf({b, stops}) {
  const stock = b.stock ? asCounts(b.stock) : null, need = {};
  stops.forEach(st => equipOf(st.o).forEach(([k, n]) => need[k] = (need[k] || 0) + n));
  const keys = [...new Set([...Object.keys(need), ...Object.keys(stock || {})])].filter(k => (need[k] || 0) + (stock ? stock[k] || 0 : 0) > 0);
  return {stock, rows: keys.map(k => ({k, name: eqName(k), need: need[k] || 0, take: stock ? (stock[k] || 0) : (need[k] || 0)}))};
}
const kitText = r => `на маршрут ${r.need}, взять ${r.take}${r.take < r.need ? ` (не хватает ${r.need - r.take})` : ''}`;

function renderKit(day) {
  const {b, stops} = day, has = stops.some(st => equipOf(st.o).length);
  if (!has && !b.stock) { $('kit').innerHTML = ''; return; }
  const kit = kitOf(day);
  let h = `<div class="card eblock kit"><div class="ehead"><h2>Что взять с собой утром</h2><span class="muted">${b.carry != null ? `не больше ${esc(b.carry)} шт.` : `${stops.length} ${plural(stops.length, 'адрес', 'адреса', 'адресов')}`}</span></div>`;
  if (!kit.rows.length) h += `<div class="ebody muted">Оборудование по заявкам не нужно.</div>`;
  else h += `<div class="krows">${kit.rows.map(r => `<div class="krow" data-eq="${esc(r.k)}" data-need="${r.need}" data-take="${r.take}"><span class="kn">${esc(r.name)}</span><span class="kv">${esc(kitText(r))}</span></div>`).join('')}</div>`
    + (kit.stock && kit.rows.some(r => r.take > r.need) ? `<div class="ebody muted kfoot">«Взять» — сколько выдают утром, с запасом сверх заявок.</div>` : '');
  $('kit').innerHTML = h + '</div>';
}

function placeBits(o) {
  const d = o.detail || {}, p = d.place || {}, bits = [];
  const apt = p.apt ?? o.apt;
  if (apt) bits.push(`кв. ${apt}${p.apt_assumed ? ' (уточнить)' : ''}`);
  if (p.entrance) bits.push(`подъезд ${p.entrance}`);
  if (p.floor) bits.push(`этаж ${p.floor}`);
  if (p.lift === true) bits.push('лифт есть'); else if (p.lift === false) bits.push('без лифта');
  return bits;
}
const MATERIAL = {'кирпич': 'кирпичный', 'железобетон': 'железобетонный', 'монолитные': 'монолитный', 'монолит': 'монолитный', 'блочные': 'блочный',
                  'панельные': 'панельный', 'панель': 'панельный', 'деревянные': 'деревянный', 'дерево': 'деревянный'};
function houseText(h) {
  if (!h) return '';
  const mat = h.material ? MATERIAL[h.material.toLowerCase()] : null;                 // «Иные» и неизвестное — не показываем
  const series = h.series && !/^инд/i.test(h.series) ? 'серия ' + h.series.replace(/^l-/, 'I-') : null;   // «индивидуальный проект» — не серия
  return [h.year && `построен в ${h.year} г.`, h.levels && `${h.levels} ${plural(h.levels, 'этаж', 'этажа', 'этажей')}`, mat, series,
          h.lifts != null ? (h.lifts ? `${h.lifts} ${plural(h.lifts, 'лифт', 'лифта', 'лифтов')}` : 'без лифта') : null].filter(Boolean).join(', ');
}

function stopState(R, st) {
  const e = R.event;
  if (!e) return '';
  if (st.end <= e.at) return 'done';
  if (st.start <= e.at) return 'now';
  return '';
}
function changeOf(R, st, b) {
  const e = R.event; if (!e) return null;
  const c = (e.changes || []).find(c => c.k === st.k);
  if (!c || !c.now || c.now.brigade !== b.v) return null;
  if (!c.was) return 'новая заявка';
  if (c.was.brigade !== b.v) return `передана от: ${R.brigades[c.was.brigade].name}`;
  return `новое время, было ${fmt(c.was.start)}`;
}

function renderStops(R, {b, rt, stops}) {
  if (!rt) { $('stops').innerHTML = ''; return; }
  const lunch = Array.isArray(rt.lunch) && rt.lunch.length === 2 ? rt.lunch : null;
  const lunchAt = lunch ? (i => i < 0 ? stops.length : i)(stops.findIndex(st => st.start >= lunch[0])) : -1;
  const lunchRow = lunch ? `<div class="lunch"><span>Обед</span><b>${fmt(lunch[0])}–${fmt(lunch[1])}</b></div>` : '';
  const day0 = {b, rt, stops}, real = marksNow(), marks = marksEff(day0), pr = FieldStatus.progress(fstops(stops), marks);
  const F = {marks, real, pr, last: lastReal(day0, pr, real)};
  let prev = [b.lat, b.lon], prevEnd = null, h = `<div class="ehead ehead-out"><h2>Адреса по порядку</h2><span class="muted">${stops.length}</span></div>`;
  stops.forEach((st, i) => {
    if (i === lunchAt) { h += lunchRow; }
    const travel = prevEnd != null ? Math.max(0, Math.round(st.arrive - prevEnd - (i === lunchAt && lunch ? lunch[1] - lunch[0] : 0))) : null;
    h += `<div class="leg"><span>${i === 0 ? 'от старта' : 'в пути'}${travel != null ? ` ${travel} мин` : ''} · ${km1(st.km || 0)} км</span></div>`;
    h += stopCard(R, st, b, prev, F);
    prev = [st.o.lat, st.o.lon]; prevEnd = st.end;
  });
  if (lunchAt === stops.length) h += lunchRow;
  const last = stops[stops.length - 1];
  if (last) h += `<div class="lunch fin"><span>Конец работ</span><b>${fmt(last.end)}</b></div>`;
  $('stops').innerHTML = h;
  const day = {b, rt, stops};
  $('stops').querySelectorAll('[data-mark]').forEach(x => x.onclick = () => mark(day, +x.dataset.i, x.dataset.mark));
  if ($('facts-undo')) $('facts-undo').onclick = () => unmark(day);
  $('stops').querySelectorAll('[data-show]').forEach(x => x.onclick = () => {
    const st = stops[+x.dataset.show]; if (!map) return;
    $('mapcard').scrollIntoView({behavior: 'smooth', block: 'center'}); map.setView([st.o.lat, st.o.lon], 16);
  });
}

// отметка инженера на карточке адреса: факт против плана, кнопка для текущего адреса, ожидаемое время для следующих
// допущение версии после события (assumed) показывается штампом «выполнено» / «в работе», без строки факта
function factBits(st, F) {
  const id = String(st.o.id), m = F.marks[id] || {}, real = F.real[id], p = F.pr, out = {cls: '', html: '', exp: ''};
  if (m.done != null) {
    out.cls = ' fdone';
    if (real) out.html = `<div class="fline"><b>Приехал ${fmt(m.arrived ?? m.done)}, закончил ${fmt(m.done)}</b><span class="muted">работа ${Math.round(m.done - (m.arrived ?? m.done))} мин при плане ${st.o.svc}; конец ${vsPlan(Math.round(m.done - st.end))}</span></div>`;
  } else if (m.arrived != null) {
    out.cls = ' fnow';
    out.html = (real ? `<div class="fline"><b>Приехал в ${fmt(m.arrived)}</b><span class="muted">${vsPlan(Math.round(m.arrived > st.start ? m.arrived - st.start : Math.min(0, m.arrived - st.arrive)))}</span></div>` : '')
      + `<button class="btn dark wide" type="button" data-mark="done" data-i="${st.i}">Закончил</button>`;
  } else if (st.i === p.cur) {
    out.cls = ' fnext';
    out.html = `<button class="btn primary wide" type="button" data-mark="arrived" data-i="${st.i}">Приехал</button>`;
  }
  if (F.last && F.last.i === st.i) out.html += `<button class="linkbtn undo" type="button" id="facts-undo">Отменить отметку «${F.last.kind === 'done' ? 'Закончил' : 'Приехал'}»</button>`;
  if (m.arrived == null && F.last && st.i >= p.cur && Math.abs(p.delay) > FieldStatus.OK) {
    const s0 = Math.max(st.start + p.delay, st.o.window[0]);
    out.exp = `<span class="exp ${p.delay > 0 ? 'late' : ''}">ожидается ${fmt(s0)}–${fmt(s0 + st.end - st.start)}</span>`;
  }
  return out;
}

function stopCard(R, st, b, from, F) {
  // отметки инженера важнее допущения версии плана «к моменту события уже выполнено»
  const o = st.o, d = o.detail || {}, av = o.type === 'Авария', chg = changeOf(R, st, b);
  const state = F && F.real[String(o.id)] ? '' : stopState(R, st);
  const fb = F ? factBits(st, F) : {cls: '', html: '', exp: ''};
  const bits = placeBits(o), house = houseText(d.house), eq = equipOf(o), hints = hintsOf(R, st, b), phone = phoneOf(o);
  const parts = (d.parts || []), allDay = o.window[0] <= 0 && o.window[1] >= 720;
  const addr = shortAddr(o.addr), shown = bits.some(x => x.startsWith('кв.')) ? noApt(addr) : addr;
  const when = `окно ${allDay ? 'в течение дня' : fmt(o.window[0]) + '–' + fmt(o.window[1])} · приезд ${fmt(st.arrive)}${st.start > st.arrive + 1 ? ` · начало ${fmt(st.start)}` : ''}`;
  const work = `Работа <b class="work-t">${fmt(st.start)}–${fmt(st.end)}</b> · ${o.svc} мин`;
  const more = [house && `<div>Дом: ${esc(house)}</div>`, `<div>Заявка ${esc(o.id)}${o.district ? ' · ' + esc(o.district) : ''}</div>`].filter(Boolean).join('');
  return `<article class="card stop${av ? ' avar' : ''}${state ? ' ' + state : ''}${fb.cls}" id="stop-${st.i + 1}">
    <div class="stop-h"><span class="num">${st.i + 1}</span><div class="addr">${esc(shown)}</div><div class="stop-b">${badge(o.type)}</div></div>
    <div class="when">${when}${fb.exp ? ' ' + fb.exp : ''}</div>
    ${av ? '<div class="prio">Авария, в приоритете</div>' : ''}
    ${state === 'done' ? '<div class="stamp">выполнено</div>' : state === 'now' ? '<div class="stamp now">в работе</div>' : ''}
    ${chg ? `<div class="chg">${esc(chg)}</div>` : ''}
    ${fb.html}
    ${bits.length ? `<div class="place">${bits.map(x => `<span>${esc(x)}</span>`).join('')}</div>` : ''}
    <div class="work-l"><span>${esc(workName(o))}</span>${o.gigabit ? '<span class="giga">1 Гбит/с</span>' : ''}</div>
    ${parts.length ? `<details class="why-d"><summary>${work} <span class="muted">расчёт</span></summary>
      ${parts.map(p => `<div class="dur-r"><span>${esc(p.what)}</span><b>${esc(p.value)}</b></div>`).join('')}</details>` : `<div class="why-d">${work}</div>`}
    ${eq.length ? `<div class="eqline"><span class="muted">Оборудование:</span> ${esc(eqText(eq))}</div>` : ''}
    <ul class="hints">${hints.map(x => `<li class="${x.cls}">${esc(x.t)}</li>`).join('')}</ul>
    <details class="more"><summary>Подробнее</summary>${more}</details>
    <div class="acts">${phone ? `<a class="btn call" href="${esc(telHref(phone))}" title="Позвонить: ${esc(phone)}" aria-label="Позвонить клиенту: ${esc(phone)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.46.57 3.6a1 1 0 0 1-.25 1z" fill="currentColor"/></svg></a>` : ''}<a class="btn nav" target="_blank" rel="noopener" title="Маршрут в Яндекс Картах" href="${yaRoute([from, [o.lat, o.lon]], b.mode)}">Маршрут</a>
      <button class="btn" type="button" data-show="${st.i}">На карте</button></div>
  </article>`;
}

function renderMap(R, {b, rt, stops}, fit = true) {
  if (typeof L === 'undefined') { $('mapcard').classList.add('hidden'); return; }
  if (!map) {
    map = L.map('emap', {scrollWheelZoom: false, zoomSnap: 0.25, dragging: !L.Browser.mobile, tap: false});
    map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    // CARTO Positron без ключа теперь отдаёт плитки с надписью «API KEY REQUIRED» — светлый вид даёт фильтр в engineer.css
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; участники OpenStreetMap'}).addTo(map);
    layer = L.layerGroup().addTo(map);
    map.on('click', () => { map.dragging.enable(); map.scrollWheelZoom.enable(); });
  }
  layer.clearLayers();
  const pts = [[b.lat, b.lon]].concat(stops.map(s => [s.o.lat, s.o.lon]));
  if (rt) {
    const line = rt.geometry ? rt.geometry.flat() : pts;
    const round = {lineCap: 'round', lineJoin: 'round'};
    L.polyline(line, {color: '#FFFFFF', weight: 8, opacity: .9, ...round}).addTo(layer);
    L.polyline(line, {color: '#3A3A38', weight: 4, opacity: .9, ...round}).addTo(layer);
  }
  L.marker([b.lat, b.lon], {icon: L.divIcon({className: '', iconSize: [18, 18], html: '<div class="m-start"></div>'})}).bindTooltip('Старт: ' + esc(shortAddr(b.start_addr))).addTo(layer);
  const marks = rt ? marksEff({b, rt, stops}) : {};
  stops.forEach(st => {
    const av = st.o.type === 'Авария', done = (marks[String(st.o.id)] || {}).done != null;
    L.marker([st.o.lat, st.o.lon], {zIndexOffset: 100 + st.i, icon: L.divIcon({className: '', iconSize: [26, 26], html: `<div class="m-num${av ? ' av' : ''}${done ? ' done' : ''}">${st.i + 1}</div>`})})
      .bindTooltip(`<b>${st.i + 1}. ${fmt(st.start)}–${fmt(st.end)}</b><br>${esc(shortAddr(st.o.addr))}`).addTo(layer);
  });
  if (fit) setTimeout(() => { map.invalidateSize(); map.fitBounds(L.latLngBounds(pts), {padding: [26, 26], maxZoom: 15}); }, 0);
}

// ---------- маршрутный лист (только для печати)
function renderSheet(R, {b, rt, stops}) {
  const kit = kitOf({b, stops}).rows;
  const evCalls = stops.map(st => ({st, why: needsEveCall(st, b)})).filter(x => x.why.length);
  const notes = st => { const acc = ((st.o.detail || {}).access || []); return [st.o.type === 'Авария' && 'АВАРИЯ', acc.includes('пропуск') && 'пропуск', acc.includes('шлагбаум') && b.mode === 'car' && 'шлагбаум', st.o.gigabit && '1 Гбит/с'].filter(Boolean).join(', '); };
  const lunch = rt && Array.isArray(rt.lunch) ? rt.lunch : null;
  let rows = '';
  stops.forEach((st, i) => {
    if (lunch && st.start >= lunch[0] && (i === 0 || stops[i - 1].start < lunch[0])) rows += `<tr class="lr"><td></td><td>${fmt(lunch[0])}–${fmt(lunch[1])}</td><td colspan="6">Обед</td></tr>`;
    const o = st.o, eq = equipOf(o), n = notes(st);
    rows += `<tr><td>${i + 1}</td><td><b>${fmt(st.start)}–${fmt(st.end)}</b></td><td>${fmt(o.window[0])}–${fmt(o.window[1])}</td>
      <td>${esc(shortAddr(o.addr))}<div class="sm">заявка ${esc(o.id)}${o.district ? ', ' + esc(o.district) : ''}${phoneOf(o) ? ', тел. ' + esc(phoneOf(o)) : ''}</div></td>
      <td>${esc(placeBits(o).filter(x => !x.startsWith('кв.')).join(', ') || '—')}</td>
      <td>${esc(o.type)}${o.hd && o.hd !== o.type ? `<div class="sm">${esc(workName(o))}</div>` : ''}<div class="sm">${o.svc} мин${n ? ' · ' + esc(n) : ''}</div></td>
      <td>${eq.length ? esc(eqText(eq)) : ''}</td><td class="sign"></td></tr>`;
  });
  if (lunch && !stops.some(st => st.start >= lunch[0])) rows += `<tr class="lr"><td></td><td>${fmt(lunch[0])}–${fmt(lunch[1])}</td><td colspan="6">Обед</td></tr>`;
  const ver = VI >= 0 ? ` · версия: ${esc((MAN.districts[DI].events[VI] || {}).label || '')}` : '';
  $('sheet').innerHTML = `<div class="sh-head"><div><div class="sh-title">Маршрутный лист</div><div>${esc(b.name)} · ${esc(R.meta.name)}${R.meta.date ? ' · ' + esc(R.meta.date) : ''}${ver}</div></div>
      ${(document.querySelector('.ebar .mk') || {}).outerHTML || ''}</div>
    <div class="sh-meta"><span>Транспорт: <b>${esc(b.mode_name)}</b></span><span>Смена: <b>${esc(b.shift_text || '10:00–22:00')}</b></span><span>Адресов: <b>${stops.length}</b></span>${rt ? `<span>Пробег: <b>${km1(rt.km)} км</b></span>` : ''}<span>Старт: <b>${esc(shortAddr(b.start_addr))}</b></span></div>
    ${kit.length ? `<div class="sh-note sh-kit"><b>Взять с собой утром:</b> ${kit.map(r => `<span class="sh-kr" data-eq="${esc(r.k)}" data-need="${r.need}" data-take="${r.take}">${esc(r.name)}: ${esc(kitText(r))}</span>`).join('; ')}</div>` : ''}
    ${evCalls.length ? `<div class="sh-note"><b>Обзвон накануне:</b> ${evCalls.map(x => `№${x.st.i + 1} — ${esc(x.why.join(', '))}`).join('; ')}</div>` : ''}
    ${stops.length ? `<table class="sh-t"><thead><tr><th>№</th><th>Время</th><th>Окно</th><th>Адрес</th><th>Подъезд / этаж</th><th>Работа</th><th>Оборудование</th><th>Подпись клиента</th></tr></thead><tbody>${rows}</tbody></table>` : '<p>Заявок нет.</p>'}
    <div class="sh-foot"><span>Инженер: ____________________</span><span>Выдал диспетчер: ____________________</span></div>`;
}

init().catch(e => { $('app').innerHTML = `<div class="card" style="padding:20px">Данные не загружены: ${esc(e.message)}. Откройте страницу через веб-сервер: <code>python -m dispatch serve</code>.</div>`; });
