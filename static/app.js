'use strict';

/* ------------------------------------------------------------------ */
/* Basics                                                              */
/* ------------------------------------------------------------------ */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeUrl = u => (/^https?:\/\//i.test(u || '') ? u : '#');
const enc = encodeURIComponent;

const C = { gain: '#3CC48B', loss: '#F2616B', caution: '#E9B949', neutral: '#8DA2BA', dim: '#5F7895',
  line: '#24405E', grid: '#1B3350', accent: '#E9B949', blue: '#6FA8FF', text: '#E8EEF5' };

const VERDICT = {
  'Confirmed': { cls: 'positive', color: C.gain, text: 'Stock moved the way the news suggested' },
  'Contradicted': { cls: 'negative', color: C.loss, text: 'Stock moved against the news' },
  'Ignored': { cls: 'neutral', color: C.dim, text: 'No meaningful move beyond the market' },
  'Moved on neutral news': { cls: 'caution', color: C.caution, text: 'Big move although the headline is neutral' },
  'Pending': { cls: 'neutral', color: '#35506E', text: 'Not enough trading sessions since the news' },
};

const LENSES = [
  { group: 'Start', items: [{ id: 'overview', name: 'Overview' }] },
  { group: 'Market', items: [
    { id: 'live', name: 'Live tape' }, { id: 'timeline', name: 'Price and news' }, { id: 'materials', name: 'Materials' },
    { id: 'technical', name: 'Technicals' }, { id: 'relative', name: 'Relative strength' }, { id: 'risk', name: 'Risk' }] },
  { group: 'News', items: [
    { id: 'feed', name: 'News feed' }, { id: 'impact', name: 'News impact' }, { id: 'sentiment', name: 'Sentiment' },
    { id: 'attention', name: 'Volume and attention' }] },
  { group: 'AI', items: [{ id: 'analyst', name: 'AI analyst' }, { id: 'ask', name: 'Ask AI' }] },
  { group: 'Surveillance', items: [{ id: 'monitor', name: 'Monitor and alerts' }, { id: 'incidents', name: 'Incidents and reports' }] },
  { group: 'Setup', items: [{ id: 'keywords', name: 'Keywords and sources' }, { id: 'notify', name: 'Email and notifications' }] },
];
const KIND_LABEL = { news: 'News', filing: 'Filing', forum: 'Forum', video: 'Video', social: 'Social' };
const GROUP_LABEL = { company: 'Company', people: 'Leadership', products: 'Products', industry: 'Industry', materials: 'Materials' };
const LINE_COLORS = ['#6FA8FF', '#C792EA', '#4FD1C5', '#F687B3', '#9AE6B4', '#B4C6FC'];
const FEED_POLL_MS = 120000;
const LENS_NAME = Object.fromEntries(LENSES.flatMap(g => g.items).map(i => [i.id, i.name]));

const POPULAR_IN = [['RELIANCE.NS', 'Reliance'], ['TCS.NS', 'TCS'], ['HDFCBANK.NS', 'HDFC Bank'], ['INFY.NS', 'Infosys'],
  ['ICICIBANK.NS', 'ICICI Bank'], ['SBIN.NS', 'SBI'], ['ITC.NS', 'ITC'], ['BHARTIARTL.NS', 'Bharti Airtel'], ['LT.NS', 'L&T']];
const POPULAR_US = [['AAPL', 'Apple'], ['MSFT', 'Microsoft'], ['NVDA', 'Nvidia'], ['AMZN', 'Amazon'], ['TSLA', 'Tesla'], ['GOOGL', 'Alphabet']];

const S = {
  cfg: {}, sym: null, profile: null, data: null, quote: null, lens: 'overview',
  period: '6mo', days: 30, interval: 10, es: null, charts: [], live: null,
  chat: {}, report: {}, token: 0, aiPending: false, loadError: null, feed: null, kw: null,
  impact: { sort: 'published', dir: -1, filter: new Set(Object.keys(VERDICT)), q: '' },
};

/* ------------------------------------------------------------------ */
/* Formatting                                                          */
/* ------------------------------------------------------------------ */
const CUR = { INR: '₹', USD: '$', EUR: '€', GBP: '£', JPY: '¥' };
const curCode = () => S.quote?.currency || S.profile?.currency || '';
const nf = (v, d = 2, c = curCode()) => Number(v).toLocaleString(c === 'INR' ? 'en-IN' : 'en-US',
  { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (v, c = curCode()) => (v == null ? '–' : (CUR[c] ?? (c ? c + ' ' : '')) + nf(v, 2, c));
const pct = (v, d = 2) => (v == null || isNaN(v) ? '–' : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%');
const dir = v => (v == null || isNaN(v) ? 'flat' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat');
const tone = v => (v == null ? '–' : (v >= 0 ? '+' : '') + Number(v).toFixed(2));
const toneColor = v => (v == null ? C.dim : v > 0.15 ? C.gain : v < -0.15 ? C.loss : C.neutral);
function big(v, c = curCode()) {
  if (v == null) return '–';
  if (c === 'INR') {
    if (v >= 1e7) return '₹' + nf(v / 1e7, 0, 'INR') + ' Cr';
    if (v >= 1e5) return '₹' + nf(v / 1e5, 1, 'INR') + ' L';
  }
  const s = CUR[c] ?? '';
  for (const [d, u] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) if (Math.abs(v) >= d) return s + (v / d).toFixed(1) + u;
  return s + nf(v, 0, c);
}
function qty(v) {
  if (v == null) return '–';
  for (const [d, u] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) if (v >= d) return (v / d).toFixed(1) + u;
  return String(Math.round(v));
}
const fmtDay = s => (s ? new Date(s + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '–');
const fmtTime = iso => (iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–');
const statusWord = s => ({ positive: 'Positive', negative: 'Negative', caution: 'Watch', neutral: 'Neutral' }[s] || 'Neutral');
const vPill = v => `<span class="pill ${VERDICT[v]?.cls || 'neutral'}">${esc(v)}</span>`;

/* ------------------------------------------------------------------ */
/* Network                                                             */
/* ------------------------------------------------------------------ */
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch { /* keep status text */ }
    throw new Error(m);
  }
  return r.json();
}
const post = (p, body) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

let toastTimer;
function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('error', isError);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), isError ? 7000 : 3500);
}

/* ------------------------------------------------------------------ */
/* Landing: search + popular                                           */
/* ------------------------------------------------------------------ */
function initLanding() {
  const chip = ([s, n]) => `<button class="chip" data-sym="${s}">${esc(n)}<small>${s}</small></button>`;
  $('#chipsIN').innerHTML = POPULAR_IN.map(chip).join('');
  $('#chipsUS').innerHTML = POPULAR_US.map(chip).join('');
  $$('.chips').forEach(c => c.addEventListener('click', e => {
    const b = e.target.closest('[data-sym]');
    if (b) openStock(b.dataset.sym);
  }));

  const input = $('#searchInput'), list = $('#searchResults');
  let timer, items = [], idx = -1;
  const draw = () => {
    if (!items.length) {
      const q = input.value.trim().toUpperCase();
      list.innerHTML = `<li data-raw="${esc(q)}"><span>No match found. Open <b>${esc(q)}</b> as a symbol</span></li>`;
    } else {
      list.innerHTML = items.map((m, i) => `<li role="option" data-i="${i}" aria-selected="${i === idx}">
        <span><span class="sr-sym">${esc(m.symbol)}</span> <span class="sr-name">${esc(m.name)}</span></span>
        <span class="sr-exch">${esc(m.exchange)}</span></li>`).join('');
    }
    list.hidden = false;
  };
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { list.hidden = true; items = []; return; }
    timer = setTimeout(async () => {
      try {
        const res = await api('/api/search?q=' + enc(q));
        if (input.value.trim() !== q) return;
        items = res; idx = res.length ? 0 : -1; draw();
      } catch { items = []; draw(); }
    }, 250);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' && items.length) { idx = (idx + 1) % items.length; draw(); e.preventDefault(); }
    else if (e.key === 'ArrowUp' && items.length) { idx = (idx - 1 + items.length) % items.length; draw(); e.preventDefault(); }
    else if (e.key === 'Enter') {
      const q = input.value.trim();
      if (idx >= 0 && items[idx]) openStock(items[idx].symbol);
      else if (q) openStock(q.toUpperCase());
    } else if (e.key === 'Escape') list.hidden = true;
  });
  list.addEventListener('click', e => {
    const li = e.target.closest('li');
    if (!li) return;
    if (li.dataset.raw) openStock(li.dataset.raw);
    else openStock(items[+li.dataset.i].symbol);
  });
  document.addEventListener('click', e => { if (!e.target.closest('#search')) list.hidden = true; });
}

/* ------------------------------------------------------------------ */
/* Workspace                                                           */
/* ------------------------------------------------------------------ */
function openStock(sym, lens = 'overview', sub = null) {
  stopFeedPolling(); stopSvPoll();
  S.incidentId = lens === 'incidents' ? sub : null;
  S.sym = sym.toUpperCase();
  S.token++;
  Object.assign(S, { data: null, quote: null, profile: null, loadError: null, aiPending: false, svOverview: null, sv: null, nt: null });
  S.lens = LENS_NAME[lens] ? lens : 'overview';
  $('#landing').hidden = true;
  $('#workspace').hidden = false;
  history.replaceState(null, '', `#${S.sym}/${S.lens}${S.incidentId ? '/' + S.incidentId : ''}`);
  $('#identName').textContent = S.sym;
  $('#identMeta').textContent = '';
  $('#tkPrice').textContent = '–'; $('#tkChg').textContent = ''; $('#tkState').textContent = 'Connecting';
  S.feed = newFeedState();
  S.kw = null;
  buildRail();
  renderLens();
  const tk = S.token;
  api('/api/profile/' + enc(S.sym)).then(p => {
    if (tk !== S.token) return;
    S.profile = p;
    $('#identName').textContent = p.name;
    $('#identMeta').textContent = [p.symbol, p.exchange, p.sector, p.market_cap ? 'Mkt cap ' + big(p.market_cap, p.currency) : '']
      .filter(Boolean).join('  |  ');
    document.title = `${p.name} | Headline Impact`;
    if (S.lens === 'live') renderLens();
  }).catch(() => {});
  startStream();
  refreshAnalysis();
  loadFeed();
  startFeedPolling();
}

async function refreshAnalysis() {
  const tk = S.token;
  const ok = await loadAnalysis(false);
  if (ok && S.cfg.ai_enabled && tk === S.token) loadAnalysis(true);
}

async function loadAnalysis(ai) {
  const tk = S.token;
  if (ai) S.aiPending = true;
  try {
    const d = await api(`/api/analysis/${enc(S.sym)}?period=${S.period}&days=${S.days}&ai=${ai ? 1 : 0}`);
    if (tk !== S.token) return false;
    const first = !S.data;
    S.data = d; S.loadError = null;
    if (ai) {
      S.aiPending = false;
      if (d.ai_used) toast('AI scored the headlines' + (d.hidden_irrelevant ? `, ${d.hidden_irrelevant} off-topic ones hidden.` : '.'));
      else if (d.ai_error) toast('AI scoring failed, keyword scoring kept. ' + d.ai_error, true);
    }
    updateRailSignals();
    if (first || !['live', 'ask', 'analyst', 'feed', 'keywords', 'monitor', 'incidents', 'notify'].includes(S.lens)) renderLens(true);
    return true;
  } catch (e) {
    if (tk !== S.token) return false;
    if (ai) { S.aiPending = false; toast('AI scoring failed: ' + e.message, true); renderLens(true); return false; }
    S.loadError = e.message; renderLens();
    return false;
  }
}

function goHome() {
  stopStream(); stopFeedPolling(); stopSvPoll(); S.token++; destroyCharts();
  loadLandingSurveillance();
  $('#workspace').hidden = true; $('#landing').hidden = false;
  history.replaceState(null, '', location.pathname);
  document.title = 'Headline Impact';
  $('#searchInput').value = ''; $('#searchInput').focus();
}

/* Live stream ------------------------------------------------------- */
function stopStream() { if (S.es) { S.es.close(); S.es = null; } }
function startStream() {
  stopStream();
  if (!S.sym) return;
  if (S.interval === 0) {
    api('/api/quote/' + enc(S.sym)).then(onQuote).catch(() => {});
    return;
  }
  const es = new EventSource(`/api/stream/${enc(S.sym)}?interval=${S.interval}`);
  es.onmessage = ev => { try { onQuote(JSON.parse(ev.data)); } catch { /* ignore bad frame */ } };
  es.onerror = () => { $('#tkState').innerHTML = '<span class="dot"></span>Reconnecting'; };
  S.es = es;
}
const localTime = (opts = {}) => new Date().toLocaleString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, ...opts });
function onQuote(q) {
  q.checked = localTime();  // the server may run in UTC, so show the viewer's own clock
  const prev = S.quote?.last;
  S.quote = q;
  const p = $('#tkPrice');
  p.textContent = money(q.last, q.currency);
  if (prev != null && q.last != null && q.last !== prev) {
    p.classList.remove('flash-up', 'flash-down');
    void p.offsetWidth;
    p.classList.add(q.last > prev ? 'flash-up' : 'flash-down');
    setTimeout(() => p.classList.remove('flash-up', 'flash-down'), 150);
  }
  const c = $('#tkChg');
  c.className = 'ticker-chg ' + dir(q.change);
  c.textContent = q.change == null ? '' : `${q.change >= 0 ? '▲' : '▼'} ${nf(Math.abs(q.change), 2, q.currency)} (${pct(q.change_pct)})`;
  $('#tkState').innerHTML = q.market_open
    ? `<span class="dot live"></span>Live, checked ${esc(q.checked)}`
    : `<span class="dot"></span>Market closed. Last trade ${esc(q.as_of || '–')}`;
  if (S.lens === 'live') updateLive();
  if (S.lens === 'overview') { const t = $('#liveTile'); if (t) t.outerHTML = liveTile(); }
}

/* Rail -------------------------------------------------------------- */
function buildRail() {
  $('#rail').innerHTML = LENSES.map(g => `<div class="rail-group">${g.group}</div>` + g.items.map(it =>
    `<button class="rail-item" data-lens="${it.id}" aria-current="${it.id === S.lens}">${it.name}<span class="rail-sig" data-sig="${it.id}"></span></button>`
  ).join('')).join('');
}
function updateRailSignals() {
  $$('.rail-sig').forEach(s => { s.className = 'rail-sig'; });
  (S.data?.overview || []).forEach(t => { const s = $(`[data-sig="${t.lens}"]`); if (s) s.classList.add(t.status); });
}
function setLens(id, sub = null) {
  if (!LENS_NAME[id]) return;
  stopSvPoll();
  S.lens = id;
  S.incidentId = id === 'incidents' ? sub : null;
  if (id === 'notify') S.nt = null;
  if (id === 'monitor' && !S.sv?.dirty) S.sv = null;
  history.replaceState(null, '', `#${S.sym}/${id}${S.incidentId ? '/' + S.incidentId : ''}`);
  $$('.rail-item').forEach(b => b.setAttribute('aria-current', String(b.dataset.lens === id)));
  renderLens();
  $('#lens').scrollTop = 0;
  $('#lens').focus({ preventScroll: true });
}

/* Charts ------------------------------------------------------------ */
function destroyCharts() { S.charts.forEach(c => { try { c.remove(); } catch { /* already gone */ } }); S.charts = []; S.live = null; }
function mkChart(el, opts = {}) {
  const base = {
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: C.neutral,
      fontFamily: "'IBM Plex Sans', system-ui, sans-serif", fontSize: 12 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.line },
    leftPriceScale: { borderColor: C.line, visible: false },
    timeScale: { borderColor: C.line },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  };
  for (const k of Object.keys(opts)) base[k] = typeof opts[k] === 'object' ? { ...(base[k] || {}), ...opts[k] } : opts[k];
  const ch = LightweightCharts.createChart(el, base);
  S.charts.push(ch);
  return ch;
}
const timeKey = t => {
  if (typeof t === 'string') return t;
  if (typeof t === 'number') return new Date(t * 1000).toISOString().slice(0, 10);
  return `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`;
};
const candleData = () => S.data.candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }));

/* Lens frame -------------------------------------------------------- */
const head = (title, desc, right = '') => `<div class="lens-head"><div><h2>${title}</h2><p>${desc}</p></div>${right}</div>`;
const companyName = () => esc(S.profile?.name || S.sym);
const kpis = arr => `<div class="kpis">${arr.map(k => `<div class="kpi"><div class="kpi-label">${k[0]}</div>
  <div class="kpi-value ${k[3] || ''}">${k[1]}</div>${k[2] ? `<div class="kpi-sub">${k[2]}</div>` : ''}</div>`).join('')}</div>`;
const aiNote = () => (S.aiPending ? '<p class="loading-note">AI is reading the headlines. Scores here update automatically when it finishes.</p>' : '');
const scoringNote = () => (S.data?.ai_used ? `Headline tone scored by ${esc(S.cfg.label)}.` : 'Headline tone scored by keywords. Add AI keys for better scoring.');

function renderLens(keepScroll = false) {
  const main = $('#lens');
  const top = keepScroll ? main.scrollTop : 0;
  destroyCharts();
  const needsData = !['live', 'ask', 'analyst', 'feed', 'keywords', 'monitor', 'incidents', 'notify'].includes(S.lens);
  if (needsData && S.loadError) {
    main.innerHTML = head(LENS_NAME[S.lens], '') + `<div class="empty"><h3>Could not load ${esc(S.sym)}</h3>
      <p>${esc(S.loadError)}</p><p>Check the symbol (Indian stocks end in .NS or .BO) and your internet connection.</p>
      <button class="btn" id="retryBtn">Try again</button></div>`;
    $('#retryBtn').onclick = () => { S.loadError = null; renderLens(); refreshAnalysis(); };
  } else if (needsData && !S.data) {
    main.innerHTML = head(LENS_NAME[S.lens], `Pulling prices, benchmark and headlines for ${esc(S.sym)}.`) +
      `<div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:360px;margin-top:16px"></div>`;
  } else {
    (RENDER[S.lens] || RENDER.overview)(main);
  }
  if (keepScroll) main.scrollTop = top;
}

/* ------------------------------------------------------------------ */
/* Lenses                                                              */
/* ------------------------------------------------------------------ */
function liveTile() {
  const q = S.quote;
  const status = !q ? 'neutral' : q.change > 0 ? 'positive' : q.change < 0 ? 'negative' : 'neutral';
  return `<button class="tile ${status}" id="liveTile" data-go="live">
    <div class="tile-title"><span>Live tape</span><span class="pill ${status}">${q?.market_open ? 'Live' : 'Closed'}</span></div>
    <div class="tile-metric">${q ? money(q.last, q.currency) : '–'}</div>
    <div class="tile-label">Last price</div>
    <div class="tile-reading"><span class="${dir(q?.change)}">${q ? pct(q.change_pct) : '–'}</span> today.
      ${q ? `Range ${money(q.low, q.currency)} to ${money(q.high, q.currency)}.` : 'Waiting for the first price.'}</div></button>`;
}
const tileHtml = t => `<button class="tile ${t.status}" data-go="${t.lens}">
  <div class="tile-title"><span>${esc(t.title)}</span><span class="pill ${t.status}">${esc(t.pill || statusWord(t.status))}</span></div>
  <div class="tile-metric">${esc(t.metric)}</div><div class="tile-label">${esc(t.label)}</div>
  <div class="tile-reading">${esc(t.reading)}</div></button>`;

const RENDER = {};

RENDER.overview = main => {
  const d = S.data;
  const days = new Set(d.events.map(e => e.event_day).filter(Boolean)).size;
  const timeline = { lens: 'timeline', title: 'Price and news', status: 'neutral', pill: 'Chart', metric: String(d.events.length),
    label: 'Headlines on the chart', reading: `${days} trading days reacted to news. Hover any marker to read it.` };
  const ai = { lens: 'analyst', title: 'AI analyst', status: 'neutral', pill: S.cfg.ai_enabled ? 'Connected' : 'Off',
    metric: S.cfg.ai_enabled ? 'Ready' : 'Off', label: S.cfg.ai_enabled ? S.cfg.label : 'Not connected',
    reading: 'A written post-mortem of what moved the stock and what the market ignored.' };
  const order = ['feed', 'impact', 'sentiment', 'materials', 'attention', 'technical', 'relative', 'risk'];
  const tiles = order.map(id => d.overview.find(t => t.lens === id)).filter(Boolean);
  const svw = S.svOverview;
  const svTile = { lens: 'monitor', title: 'Surveillance', status: svw?.watched ? 'positive' : 'neutral', pill: svw?.watched ? 'Watching' : 'Not watched',
    metric: svw ? String(svw.incidents) : '–', label: 'Incidents for this stock',
    reading: svw?.watched ? `Triggers at ${svw.threshold}.${svw.last ? ' Last incident ' + ago(svw.last) + '.' : ''}` : 'Add this stock to the watchlist to get movement alerts and reports.' };
  if (!svw) Promise.all([api('/api/surveillance/status'), api('/api/surveillance/incidents?symbol=' + enc(S.sym))]).then(([st, incs]) => {
    const w = st.config.watchlist.find(x => x.symbol === S.sym && x.enabled);
    S.svOverview = { watched: !!w, threshold: w ? `+${w.up_pct ?? st.config.up_pct}% / -${w.down_pct ?? st.config.down_pct}%` : '', incidents: incs.length, last: incs[0]?.created };
    if (S.lens === 'overview') renderLens(true);
  }).catch(() => {});
  main.innerHTML = head('Overview', `Every lens on ${companyName()} at a glance. Open a tile for the full view.`) +
    `<div class="tiles">${liveTile()}${tileHtml(timeline)}${tiles.map(tileHtml).join('')}${tileHtml(svTile)}${tileHtml(ai)}</div>
     <div class="panel" style="margin-top:16px"><h3>Key findings <small>${esc(fmtDay(d.summary.start))} to ${esc(fmtDay(d.summary.end))}</small></h3>
     <ul class="findings">${(d.findings.length ? d.findings : ['No findings yet for this window.']).map(f => `<li>${esc(f)}</li>`).join('')}</ul></div>`;
};

RENDER.live = main => {
  const p = S.profile || {};
  main.innerHTML = head('Live tape', S.interval
    ? `Price refreshes every ${S.interval} seconds. Change the interval in the top bar.`
    : 'Live refresh is paused. Pick an interval in the top bar to resume.') +
    `<div class="grid g-main">
      <div class="panel"><div id="liveBig"></div><div class="chart lg" id="cLive"></div></div>
      <div class="panel"><h3>Session</h3><div id="liveStats"></div>
        <h3 style="margin-top:22px">Company</h3>
        <p style="color:var(--muted);margin:0;font-size:.9rem;max-height:220px;overflow:auto">${esc((p.summary || 'No description available.').slice(0, 900))}</p></div>
    </div>`;
  const ch = mkChart($('#cLive'), { timeScale: { timeVisible: true, secondsVisible: false } });
  const series = ch.addAreaSeries({ lineWidth: 2, priceLineVisible: true });
  S.live = { chart: ch, series, prevLine: null };
  updateLive();
};
function updateLive() {
  if (!S.live || !$('#liveBig')) return;
  const q = S.quote, p = S.profile || {};
  if (!q) { $('#liveBig').innerHTML = '<p class="loading-note">Waiting for the first price.</p>'; return; }
  const up = (q.change ?? 0) >= 0;
  $('#liveBig').innerHTML = `<div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:12px">
    <span class="tile-metric" style="font-size:2.8rem">${money(q.last, q.currency)}</span>
    <span class="${dir(q.change)}" style="font-size:1.2rem;font-weight:500">${q.change == null ? '' : (up ? '▲ ' : '▼ ') + nf(Math.abs(q.change), 2, q.currency) + ' (' + pct(q.change_pct) + ')'}</span>
    <span class="ticker-state">${q.market_open ? '<span class="dot live"></span>Market open' : '<span class="dot"></span>Market closed, showing last session'}</span></div>`;
  const col = up ? C.gain : C.loss;
  S.live.series.applyOptions({ lineColor: col, topColor: up ? 'rgba(60,196,139,.28)' : 'rgba(242,97,107,.28)', bottomColor: 'rgba(13,27,42,0)' });
  if (q.intraday?.length) S.live.series.setData(q.intraday);
  if (S.live.prevLine) S.live.series.removePriceLine(S.live.prevLine);
  if (q.prev_close) S.live.prevLine = S.live.series.createPriceLine({ price: q.prev_close, color: C.dim, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: 'Prev close' });
  const rows = [['Open', money(q.open, q.currency)], ['Day high', money(q.high, q.currency)], ['Day low', money(q.low, q.currency)],
    ['Previous close', money(q.prev_close, q.currency)], ['Volume', qty(q.volume)], ['Market cap', big(p.market_cap, p.currency)],
    ['P/E (trailing)', p.pe ? nf(p.pe, 1) : '–'], ['Exchange', esc(p.exchange || '–')], ['Last trade', esc(q.as_of || '–')], ['Checked at', esc(q.checked || '–')]];
  $('#liveStats').innerHTML = `<table><tbody>${rows.map(r => `<tr><td style="color:var(--muted)">${r[0]}</td><td class="num">${r[1]}</td></tr>`).join('')}</tbody></table>`;
}

RENDER.timeline = main => {
  const d = S.data;
  main.innerHTML = head('Price and news', 'Each arrow is a trading day that reacted to news: green for good news, red for bad, grey for neutral. Hover or click a day to read its headlines.') +
    `<div class="grid g-main"><div class="panel"><div class="chart lg" id="cPrice"></div></div>
     <div class="panel"><h3 id="dayTitle">Headlines</h3><div class="side-list" id="dayList"></div></div></div>`;
  const ch = mkChart($('#cPrice'));
  const cs = ch.addCandlestickSeries({ upColor: C.gain, downColor: C.loss, borderVisible: false, wickUpColor: C.gain, wickDownColor: C.loss });
  cs.setData(candleData());
  cs.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
  const vs = ch.addHistogramSeries({ priceScaleId: '', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
  vs.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  vs.setData(d.candles.map(c => ({ time: c.time, value: c.volume, color: c.close >= c.open ? 'rgba(60,196,139,.35)' : 'rgba(242,97,107,.35)' })));
  const byDay = {};
  d.events.forEach(e => { if (e.event_day) (byDay[e.event_day] ||= []).push(e); });
  const days = Object.keys(byDay).sort();
  cs.setMarkers(days.map(day => {
    const s = byDay[day].map(e => e.sentiment).filter(v => v != null);
    const avg = s.length ? s.reduce((a, b) => a + b, 0) / s.length : 0;
    return { time: day, position: 'aboveBar', color: toneColor(avg), shape: 'arrowDown', text: byDay[day].length > 1 ? String(byDay[day].length) : '' };
  }));
  ch.timeScale().fitContent();
  let shown = null;
  const show = day => {
    if (!day || day === shown || !byDay[day]) return;
    shown = day;
    const list = byDay[day];
    const move = list[0].key_move;
    $('#dayTitle').innerHTML = `${fmtDay(day)} <small class="${dir(move)}">${pct(move)} vs market</small>`;
    $('#dayList').innerHTML = list.map(sideItem).join('');
  };
  const onPoint = p => { if (p.time) show(timeKey(p.time)); };
  ch.subscribeCrosshairMove(onPoint);
  ch.subscribeClick(onPoint);
  if (days.length) show(days[days.length - 1]);
  else $('#dayList').innerHTML = '<p class="loading-note">No headlines found in this window. Try a longer news lookback.</p>';
};
const sideItem = e => `<div class="side-item"><a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(e.title)}</a>
  <div class="side-meta"><span>${esc(e.source)}</span><span>${fmtTime(e.published)}</span>
  <span style="color:${toneColor(e.sentiment)}">Tone ${tone(e.sentiment)}</span>${vPill(e.verdict)}</div>
  ${e.why ? `<div class="reaction-why">${esc(e.why)}</div>` : ''}</div>`;

RENDER.impact = main => {
  const ev = S.data.events;
  const done = ev.filter(e => e.verdict !== 'Pending');
  const n = k => ev.filter(e => e.verdict === k).length;
  const share = k => (done.length ? Math.round(n(k) / done.length * 100) + '%' : '–');
  const movers = ev.filter(e => e.key_move != null).sort((a, b) => Math.abs(b.key_move) - Math.abs(a.key_move));
  const thr = S.data.threshold;
  main.innerHTML = head('News impact', `How ${companyName()} actually traded after each headline, after removing the ${esc(S.data.benchmark)} move.`,
    `<button class="btn ghost" id="csvBtn">Export CSV</button>`) + aiNote() +
    kpis([['Headlines analysed', ev.length, `${done.length} with a measurable reaction`],
      ['Market agreed', n('Confirmed'), share('Confirmed') + ' of measured', 'up'],
      ['Market disagreed', n('Contradicted'), share('Contradicted') + ' of measured', 'down'],
      ['Market ignored', n('Ignored'), share('Ignored') + ' of measured'],
      ['Biggest reaction', movers[0] ? pct(movers[0].key_move, 1) : '–', '2 days, vs market', dir(movers[0]?.key_move)]]) +
    `<div class="panel"><h3>Market verdict on the news</h3>
      <div class="vbar">${Object.keys(VERDICT).map(k => n(k) ? `<span style="width:${n(k) / ev.length * 100}%;background:${VERDICT[k].color}" title="${k}: ${n(k)}"></span>` : '').join('')}</div>
      <div class="legend">${Object.keys(VERDICT).map(k => `<span><i style="background:${VERDICT[k].color}"></i>${k} (${n(k)}): ${VERDICT[k].text.toLowerCase()}</span>`).join('')}</div></div>
    <div class="panel"><h3>Biggest reactions</h3><div class="reactions">${movers.slice(0, 6).map(e => `
      <div class="reaction"><div class="reaction-move ${dir(e.key_move)}">${pct(e.key_move, 1)}</div>
        <div><div class="reaction-title"><a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(e.title)}</a></div>
          <div class="reaction-meta">${esc(e.source)}, ${fmtTime(e.published)} ${vPill(e.verdict)}${e.same_day_news > 1 ? `<span style="margin-left:8px">Shared day with ${e.same_day_news - 1} other headline${e.same_day_news > 2 ? 's' : ''}</span>` : ''}</div>
          ${e.why ? `<div class="reaction-why">${esc(e.why)}</div>` : ''}</div>
        <div class="reaction-stats"><span>1st hour</span><span class="${dir(e.first_hour)}">${pct(e.first_hour, 1)}</span>
          <span>Day</span><span class="${dir(e.abn_d0)}">${pct(e.abn_d0, 1)}</span>
          <span>1 week</span><span class="${dir(e.abn_w)}">${pct(e.abn_w, 1)}</span>
          <span>Volume</span><span>${e.vol_ratio ? e.vol_ratio.toFixed(1) + 'x' : '–'}</span></div></div>`).join('') || '<p class="loading-note">No measurable reactions yet.</p>'}</div></div>
    <div class="panel"><h3>Every headline</h3>
      <div class="table-tools"><input id="impQ" type="search" placeholder="Filter headlines" value="${esc(S.impact.q)}">
        ${Object.keys(VERDICT).map(k => `<button class="filter" data-v="${k}" aria-pressed="${S.impact.filter.has(k)}">${k}</button>`).join('')}</div>
      <div class="table-wrap"><table id="impTable"></table></div>
      <details style="margin-top:14px;color:var(--muted)"><summary style="cursor:pointer">How this is calculated</summary>
        <p>Each headline is matched to the first trading session that could react to it; news after market close counts for the next session.
        Moves are measured from the previous close, and <b>vs market</b> subtracts the ${esc(S.data.benchmark)} move over the same window.
        The verdict compares the 2-day move vs market with the headline's tone. Moves under ${thr ? (thr * 100).toFixed(1) : '–'}% (this stock's normal 2-day noise) count as ignored.
        First hour compares the last hourly price before the news with the first full hour after it (last 60 days only). ${scoringNote()}</p></details></div>`;
  $('#impQ').addEventListener('input', e => { S.impact.q = e.target.value; drawImpactTable(); });
  $$('.filter', main).forEach(b => b.addEventListener('click', () => {
    const v = b.dataset.v;
    S.impact.filter.has(v) ? S.impact.filter.delete(v) : S.impact.filter.add(v);
    b.setAttribute('aria-pressed', String(S.impact.filter.has(v)));
    drawImpactTable();
  }));
  $('#csvBtn').onclick = exportCsv;
  drawImpactTable();
};
const IMPACT_COLS = [
  ['published', 'Published', false], ['title', 'Headline', false], ['source', 'Source', false], ['sentiment', 'Tone', true],
  ['category', 'Type', false], ['verdict', 'Verdict', false], ['first_hour', '1st hour', true], ['abn_d0', 'Day vs mkt', true],
  ['abn_d1', '2 days vs mkt', true], ['abn_w', '1 week vs mkt', true], ['vol_ratio', 'Volume', true], ['coverage', 'Outlets', true], ['same_day_news', 'Same day', true]];
function drawImpactTable() {
  const t = $('#impTable');
  if (!t) return;
  const hasCat = S.data.events.some(e => e.category);
  const cols = IMPACT_COLS.filter(c => c[0] !== 'category' || hasCat);
  const q = S.impact.q.toLowerCase();
  const { sort, dir: sd } = S.impact;
  const rows = S.data.events.filter(e => S.impact.filter.has(e.verdict) && (!q || (e.title + ' ' + e.source).toLowerCase().includes(q)))
    .sort((a, b) => {
      const x = a[sort], y = b[sort];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sd;
    });
  t.innerHTML = `<thead><tr>${cols.map(([k, l, n]) => `<th data-k="${k}" class="${n ? 'num' : ''}" ${k === sort ? `aria-sort="${sd > 0 ? 'ascending' : 'descending'}"` : ''}>${l}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(e => `<tr>${cols.map(([k, , n]) => {
      const v = e[k];
      if (k === 'published') return `<td style="white-space:nowrap">${fmtTime(v)}</td>`;
      if (k === 'title') return `<td class="title-cell"><a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(v)}</a></td>`;
      if (k === 'verdict') return `<td>${vPill(v)}</td>`;
      if (k === 'sentiment') return `<td class="num" style="color:${toneColor(v)}">${tone(v)}</td>`;
      if (k === 'vol_ratio') return `<td class="num">${v ? v.toFixed(1) + 'x' : '–'}</td>`;
      if (k === 'same_day_news' || k === 'coverage') return `<td class="num">${v ?? 1}</td>`;
      if (n) return `<td class="num ${dir(v)}">${pct(v)}</td>`;
      return `<td>${esc(v || '–')}</td>`;
    }).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" style="color:var(--muted)">No headlines match these filters.</td></tr>`}</tbody>`;
  $$('th', t).forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.k;
    S.impact.dir = S.impact.sort === k ? -S.impact.dir : -1;
    S.impact.sort = k;
    drawImpactTable();
  }));
}
function exportCsv() {
  const cols = ['published', 'title', 'source', 'url', 'sentiment', 'category', 'importance', 'verdict', 'first_hour',
    'ret_d0', 'abn_d0', 'ret_d1', 'abn_d1', 'ret_w', 'abn_w', 'vol_ratio', 'same_day_news', 'why'];
  const q = v => (v == null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = [cols.join(','), ...S.data.events.map(e => cols.map(c => q(e[c])).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `${S.sym}_news_impact.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

RENDER.sentiment = main => {
  const s = S.data.sentiment;
  const total = s.positive + s.negative + s.neutral;
  const shift = s.recent_avg != null && s.prior_avg != null ? s.recent_avg - s.prior_avg : null;
  const toneBar = v => {
    if (v == null) return '–';
    const w = Math.min(Math.abs(v), 1) * 50;
    return `<div class="bar-cell"><div class="bar-track" style="position:relative"><div style="position:absolute;top:0;bottom:0;left:${v < 0 ? 50 - w : 50}%;width:${w}%;background:${toneColor(v)}"></div>
      <div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--line)"></div></div><span class="num" style="width:42px;text-align:right">${tone(v)}</span></div>`;
  };
  main.innerHTML = head('Sentiment', `The tone of news coverage on ${companyName()}, set against the share price. ${scoringNote()}`) + aiNote() +
    kpis([['Average tone', tone(s.avg), '-1 very bad to +1 very good', s.avg > 0.15 ? 'up' : s.avg < -0.15 ? 'down' : ''],
      ['Positive headlines', s.positive, total ? Math.round(s.positive / total * 100) + '% of coverage' : '', 'up'],
      ['Negative headlines', s.negative, total ? Math.round(s.negative / total * 100) + '% of coverage' : '', 'down'],
      ['Neutral headlines', s.neutral, ''],
      ['Last 7 days vs before', shift == null ? '–' : tone(shift), shift == null ? 'Not enough data' : shift > 0 ? 'Tone improving' : 'Tone worsening', dir(shift)]]) +
    `<div class="panel"><h3>News tone vs price <small>Bars: average tone of each news day (left scale). Line: closing price.</small></h3><div class="chart" id="cSent"></div></div>
     <div class="grid g2"><div class="panel"><h3>By type of news</h3><div class="table-wrap"><table><thead><tr><th>Type</th><th class="num">Headlines</th><th>Average tone</th><th class="num">Avg move vs mkt</th></tr></thead>
       <tbody>${s.categories.map(c => `<tr><td>${esc(c.category)}</td><td class="num">${c.count}</td><td>${toneBar(c.avg_sentiment)}</td><td class="num">${c.avg_move == null ? '–' : (c.avg_move * 100).toFixed(2) + '%'}</td></tr>`).join('')}</tbody></table></div>
       ${S.data.ai_used ? '' : '<p class="loading-note" style="margin-top:10px">News types appear when AI scoring is switched on.</p>'}</div>
     <div class="panel"><h3>By source</h3><div class="table-wrap"><table><thead><tr><th>Source</th><th class="num">Headlines</th><th>Average tone</th></tr></thead>
       <tbody>${s.sources.map(c => `<tr><td>${esc(c.source)}</td><td class="num">${c.count}</td><td>${toneBar(c.avg_sentiment)}</td></tr>`).join('')}</tbody></table></div></div></div>`;
  const ch = mkChart($('#cSent'), { leftPriceScale: { visible: true } });
  const line = ch.addLineSeries({ color: C.blue, lineWidth: 2, priceLineVisible: false });
  line.setData(S.data.candles.map(c => ({ time: c.time, value: c.close })));
  const hs = ch.addHistogramSeries({ priceScaleId: 'left', priceLineVisible: false, lastValueVisible: false, priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
  hs.setData(s.daily.map(x => ({ time: x.time, value: x.value, color: toneColor(x.value) })));
  hs.priceScale().applyOptions({ scaleMargins: { top: 0.55, bottom: 0.02 } });
  ch.timeScale().fitContent();
};

RENDER.technical = main => {
  const t = S.data.technical;
  main.innerHTML = head('Technicals', 'Trend and momentum indicators calculated from daily prices. They describe how the price is behaving, not why.') +
    `<div class="signals">${t.signals.map(s => `<div class="signal"><div class="signal-head"><span>${esc(s.name)}</span><span class="pill ${s.status}">${statusWord(s.status)}</span></div>
      <div class="signal-value">${esc(s.value)}</div><div class="signal-reading">${esc(s.reading)}</div></div>`).join('')}</div>
    <div class="panel"><h3>Price with moving averages <small><span style="color:${C.accent}">20-day</span>, <span style="color:${C.blue}">50-day</span></small></h3><div class="chart" id="cTech"></div></div>
    <div class="grid g2"><div class="panel"><h3>RSI (14) <small>Above 70 overbought, below 30 oversold</small></h3><div class="chart md" id="cRsi"></div></div>
    <div class="panel"><h3>MACD (12, 26, 9) <small>Bars above zero mean upward momentum</small></h3><div class="chart md" id="cMacd"></div></div></div>`;
  const ch = mkChart($('#cTech'));
  ch.addCandlestickSeries({ upColor: C.gain, downColor: C.loss, borderVisible: false, wickUpColor: C.gain, wickDownColor: C.loss }).setData(candleData());
  ch.addLineSeries({ color: C.accent, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }).setData(t.sma20);
  ch.addLineSeries({ color: C.blue, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }).setData(t.sma50);
  ch.timeScale().fitContent();
  const r = mkChart($('#cRsi'));
  const rs = r.addLineSeries({ color: C.accent, lineWidth: 2, priceLineVisible: false });
  rs.setData(t.rsi);
  rs.createPriceLine({ price: 70, color: C.loss, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: '' });
  rs.createPriceLine({ price: 30, color: C.gain, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: '' });
  r.timeScale().fitContent();
  const m = mkChart($('#cMacd'));
  m.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false }).setData(t.hist.map(x => ({ ...x, color: x.value >= 0 ? 'rgba(60,196,139,.6)' : 'rgba(242,97,107,.6)' })));
  m.addLineSeries({ color: C.blue, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }).setData(t.macd);
  m.addLineSeries({ color: C.accent, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(t.signal);
  m.timeScale().fitContent();
};

RENDER.relative = main => {
  const r = S.data.relative;
  main.innerHTML = head('Relative strength', `Is ${companyName()} doing better or worse than the ${esc(r.bench_name)}? Removes market-wide moves from the picture.`) +
    `<div class="panel"><h3>Performance by window</h3><div class="table-wrap"><table><thead><tr><th>Window</th><th class="num">Stock</th><th class="num">${esc(r.bench_name)}</th><th class="num">Difference</th></tr></thead>
      <tbody>${r.windows.map(w => `<tr><td>${w.window}</td><td class="num ${dir(w.stock)}">${pct(w.stock)}</td><td class="num ${dir(w.bench)}">${pct(w.bench)}</td>
      <td class="num ${dir(w.diff)}" style="font-weight:600">${pct(w.diff)}</td></tr>`).join('') || '<tr><td colspan="4">Benchmark data not available.</td></tr>'}</tbody></table></div></div>
    <div class="grid g2"><div class="panel"><h3>Growth of 100 <small><span style="color:${C.accent}">Stock</span>, <span style="color:${C.blue}">${esc(r.bench_name)}</span></small></h3><div class="chart md" id="cRel"></div></div>
    <div class="panel"><h3>Relative strength line <small>Rising means the stock is beating the index</small></h3><div class="chart md" id="cRatio"></div></div></div>`;
  const a = mkChart($('#cRel'));
  a.addLineSeries({ color: C.accent, lineWidth: 2, priceLineVisible: false }).setData(r.stock);
  a.addLineSeries({ color: C.blue, lineWidth: 2, lineStyle: 2, priceLineVisible: false }).setData(r.bench);
  a.timeScale().fitContent();
  const b = mkChart($('#cRatio'));
  const rs = b.addAreaSeries({ lineColor: C.accent, topColor: 'rgba(233,185,73,.25)', bottomColor: 'rgba(233,185,73,0)', lineWidth: 2, priceLineVisible: false });
  rs.setData(r.ratio);
  rs.createPriceLine({ price: 100, color: C.dim, lineStyle: 2, lineWidth: 1, axisLabelVisible: false, title: 'Even' });
  b.timeScale().fitContent();
};

RENDER.attention = main => {
  const a = S.data.attention;
  const last = a.volume.length ? a.volume[a.volume.length - 1] : null;
  const avgLast = a.avg20.length ? a.avg20[a.avg20.length - 1].value : null;
  main.innerHTML = head('Volume and attention', 'Unusual trading volume shows when investors are paying attention. Spikes with no headline can mean the market knows something the news has not reported yet.') +
    kpis([['Latest volume vs usual', last && avgLast ? (last.value / avgLast).toFixed(1) + 'x' : '–', 'vs 20-day average'],
      ['Volume spikes', a.spikes.length, '2x the usual or more'],
      ['Spikes with no news', a.quiet_spikes, 'Worth a closer look', a.quiet_spikes ? 'down' : ''],
      ['Days with news', new Set(S.data.events.map(e => e.event_day).filter(Boolean)).size, `in the last ${S.days} days`]]) +
    `<div class="panel"><h3>Daily volume <small><span style="color:${C.accent}">Amber</span> bars are days with news. Line is the 20-day average.</small></h3><div class="chart" id="cVol"></div></div>
    <div class="panel"><h3>Volume spikes</h3><div class="table-wrap"><table><thead><tr><th>Date</th><th class="num">Volume vs usual</th><th class="num">Price move</th><th class="num">Headlines</th><th>What was in the news</th></tr></thead>
      <tbody>${a.spikes.map(s => `<tr><td style="white-space:nowrap">${fmtDay(s.date)}</td><td class="num">${s.ratio.toFixed(1)}x</td><td class="num ${dir(s.return)}">${pct(s.return)}</td>
      <td class="num">${s.news}</td><td>${s.news ? esc(s.headline) : '<span style="color:var(--caution)">No headline found. The move may come from information not yet in the news, or from block deals and index flows.</span>'}</td></tr>`).join('')
        || '<tr><td colspan="5" style="color:var(--muted)">No days with 2x normal volume in this period.</td></tr>'}</tbody></table></div></div>`;
  const ch = mkChart($('#cVol'));
  ch.addHistogramSeries({ priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false })
    .setData(a.volume.map(v => ({ time: v.time, value: v.value, color: v.news ? 'rgba(233,185,73,.85)' : 'rgba(111,168,255,.35)' })));
  ch.addLineSeries({ color: C.text, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: 'volume' } }).setData(a.avg20);
  ch.timeScale().fitContent();
};

RENDER.risk = main => {
  const r = S.data.risk;
  const maxCount = Math.max(1, ...r.distribution.map(b => b.count));
  main.innerHTML = head('Risk', `How much ${companyName()} swings, how deep its falls have been, and how closely it follows the ${esc(S.data.benchmark)}.`) +
    kpis([['Annual volatility', r.volatility == null ? '–' : (r.volatility * 100).toFixed(1) + '%', 'Typical yearly swing'],
      ['Beta', r.beta == null ? '–' : r.beta.toFixed(2), r.beta == null ? '' : r.beta > 1 ? 'Moves more than the index' : 'Moves less than the index'],
      ['Max drawdown', pct(r.max_drawdown, 1), 'Lowest point ' + fmtDay(r.max_drawdown_date), 'down'],
      ['From peak now', pct(r.current_drawdown, 1), 'Current drawdown', dir(r.current_drawdown)],
      ['1-day VaR (95%)', r.var95 == null ? '–' : '-' + (r.var95 * 100).toFixed(2) + '%', '1 day in 20 is worse than this']]) +
    kpis([['Best day', pct(r.best_day), fmtDay(r.best_day_date), 'up'], ['Worst day', pct(r.worst_day), fmtDay(r.worst_day_date), 'down'],
      ['Up days', r.up_days == null ? '–' : Math.round(r.up_days * 100) + '%', 'Share of sessions that closed higher'],
      ['Correlation', r.correlation == null ? '–' : r.correlation.toFixed(2), 'With the index, 1 means in lockstep']]).replace('class="kpis"', 'class="kpis" style="margin-top:12px"') +
    `<div class="grid g2"><div class="panel"><h3>Drawdown from peak <small>How far below its previous high the stock was each day</small></h3><div class="chart md" id="cDd"></div></div>
    <div class="panel"><h3>Daily returns <small>How often each size of move happened</small></h3>
      <div class="dist">${r.distribution.map(b => `<div title="${b.from.toFixed(2)}% to ${b.to.toFixed(2)}%: ${b.count} days" style="height:${b.count / maxCount * 100}%;background:${b.to <= 0 ? C.loss : b.from >= 0 ? C.gain : C.neutral}"></div>`).join('')}</div>
      <div class="dist-axis"><span>${r.distribution[0] ? r.distribution[0].from.toFixed(1) + '%' : ''}</span><span>0%</span><span>${r.distribution.length ? r.distribution[r.distribution.length - 1].to.toFixed(1) + '%' : ''}</span></div></div></div>`;
  const ch = mkChart($('#cDd'));
  ch.addAreaSeries({ lineColor: C.loss, topColor: 'rgba(242,97,107,0)', bottomColor: 'rgba(242,97,107,.35)', lineWidth: 2, priceLineVisible: false,
    priceFormat: { type: 'custom', formatter: v => (v * 100).toFixed(1) + '%' } }).setData(r.drawdown);
  ch.timeScale().fitContent();
};

const md = text => {
  if (!window.marked) return esc(text).replace(/\n/g, '<br>');
  const html = marked.parse(esc(text || '').replace(/&gt;/g, '>'));
  return window.DOMPurify ? DOMPurify.sanitize(html) : html.replace(/<(script|iframe|object|embed)/gi, '&lt;$1');
};
const aiOff = () => `<div class="empty"><h3>AI is not connected</h3>
  <p>AI insights are not available right now. Ask the administrator to add the AI settings and restart the service.</p>
  <p>Every other lens works without AI.</p></div>`;

RENDER.analyst = main => {
  const key = `${S.sym}|${S.period}|${S.days}`;
  const rep = S.report[key];
  main.innerHTML = head('AI analyst', `A written post-mortem: which news moved ${companyName()}, what the market ignored, and what to watch next.`,
    S.cfg.ai_enabled ? `<div style="display:flex;gap:10px">${rep?.markdown ? '<button class="btn ghost" id="dlRep">Download</button>' : ''}
    <button class="btn" id="genRep" ${rep?.busy ? 'disabled' : ''}>${rep?.busy ? 'Writing report' : rep?.markdown ? 'Rewrite report' : 'Write post-mortem report'}</button></div>` : '') +
    (!S.cfg.ai_enabled ? aiOff() : rep?.markdown
      ? `<div class="panel"><p class="loading-note">Written by ${esc(S.cfg.label)} on ${esc(rep.generated)}. Not investment advice.</p><div class="report">${md(rep.markdown)}</div></div>`
      : rep?.busy ? '<div class="skeleton" style="height:420px"></div>'
        : `<div class="empty"><h3>No report yet</h3><p>The AI reads every headline with its measured price reaction and writes a plain-English post-mortem. Takes about 20 to 40 seconds.</p></div>`);
  const gen = $('#genRep');
  if (gen) gen.onclick = async () => {
    const sym = S.sym;
    S.report[key] = { ...(S.report[key] || {}), busy: true };
    renderLens();
    try {
      const r = await post('/api/ai/report', { symbol: sym, period: S.period, days: S.days });
      S.report[key] = { markdown: r.markdown, generated: localTime({ day: 'numeric', month: 'short', year: 'numeric', second: undefined }) };
    } catch (e) {
      S.report[key] = { ...(S.report[key] || {}), busy: false };
      toast('Report failed: ' + e.message, true);
    }
    if (S.sym === sym && S.lens === 'analyst') renderLens();
  };
  const dl = $('#dlRep');
  if (dl) dl.onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([rep.markdown], { type: 'text/markdown' }));
    a.download = `${S.sym}_postmortem.md`;
    a.click();
  };
};

RENDER.ask = main => {
  if (!S.cfg.ai_enabled) { main.innerHTML = head('Ask AI', 'Ask questions about the price and news data.') + aiOff(); return; }
  const log = S.chat[S.sym] ||= [];
  const suggestions = ['Why did the stock move the most this month?', 'Which news did the market ignore?', 'Is the recent tone of news getting better or worse?', 'What should I watch next?'];
  main.innerHTML = head('Ask AI', `Answers come from the live price, headlines and measured reactions for ${companyName()}.`) +
    `<div class="chat"><div class="chat-log" id="chatLog">${log.length ? log.map(m => `<div class="msg ${m.role}">${m.role === 'assistant' ? md(m.content) : esc(m.content)}</div>`).join('')
      : `<div class="suggest">${suggestions.map(s => `<button class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join('')}</div>`}</div>
     <form class="chat-form" id="chatForm"><input id="chatIn" autocomplete="off" placeholder="Ask about ${companyName()}"><button class="btn">Send</button></form></div>`;
  const logEl = $('#chatLog');
  logEl.scrollTop = logEl.scrollHeight;
  const send = async question => {
    if (!question.trim()) return;
    const sym = S.sym;
    const history = log.slice();
    log.push({ role: 'user', content: question });
    renderLens();
    const l = $('#chatLog');
    l.insertAdjacentHTML('beforeend', '<div class="msg assistant" id="thinking"><span class="loading-note">Thinking</span></div>');
    l.scrollTop = l.scrollHeight;
    let answer;
    try { answer = (await post('/api/ai/chat', { symbol: sym, period: S.period, days: S.days, question, history })).answer; }
    catch (e) { answer = 'The AI request failed: ' + e.message; }
    log.push({ role: 'assistant', content: answer });
    if (S.sym === sym && S.lens === 'ask') { renderLens(); $('#chatIn')?.focus(); }
  };
  $('#chatForm').onsubmit = e => { e.preventDefault(); send($('#chatIn').value); };
  $$('[data-q]', main).forEach(b => b.onclick = () => send(b.dataset.q));
  $('#chatIn').focus();
};

/* ------------------------------------------------------------------ */
/* News feed (all sources, refreshed every 2 minutes)                  */
/* ------------------------------------------------------------------ */
const newFeedState = () => ({ items: [], health: [], tile: null, seen: new Set(), fresh: new Set(), timer: null,
  kind: 'all', group: 'all', q: '', recent: false, loaded: false, loading: false, err: null, checked: null });
function stopFeedPolling() { if (S.feed?.timer) { clearInterval(S.feed.timer); S.feed.timer = null; } }
function startFeedPolling() { stopFeedPolling(); S.feed.timer = setInterval(() => loadFeed(), FEED_POLL_MS); }
const ago = iso => {
  if (!iso) return '–';
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`;
};
async function loadFeed() {
  const tk = S.token, F = S.feed;
  if (!F || F.loading) return;
  F.loading = true;
  if (S.lens === 'feed') drawFeedStatus();
  try {
    const f = await api(`/api/feed/${enc(S.sym)}?days=${S.days}`);
    if (tk !== S.token) return;
    const fresh = f.items.filter(i => !F.seen.has(i.id));
    if (F.loaded && fresh.length) {
      fresh.forEach(i => F.fresh.add(i.id));
      toast(`${fresh.length} new item${fresh.length > 1 ? 's' : ''} in the news feed.`);
    }
    f.items.forEach(i => F.seen.add(i.id));
    Object.assign(F, { items: f.items, health: f.health, tile: f.tile, loaded: true, err: null, checked: localTime() });
  } catch (e) {
    if (tk !== S.token) return;
    F.err = e.message;
  }
  F.loading = false;
  if (tk !== S.token) return;
  if (S.lens === 'feed') drawFeed();
  if (S.lens === 'overview' && F.tile) { const t = $('[data-go="feed"]'); if (t) t.outerHTML = tileHtml(F.tile); }
}

RENDER.feed = main => {
  const F = S.feed;
  const chips = (key, map) => `<button class="filter" data-${key}="all" aria-pressed="${F[key] === 'all'}">All</button>` +
    Object.entries(map).map(([k, l]) => `<button class="filter" data-${key}="${k}" aria-pressed="${F[key] === k}">${l}</button>`).join('');
  main.innerHTML = head('News feed', `Every source in one stream, newest first. Refreshes automatically every 2 minutes.`,
    `<div style="display:flex;gap:10px;align-items:center"><span class="ticker-state" id="feedStatus"></span>
     <button class="btn ghost" id="feedRefresh">Refresh now</button></div>`) +
    `<div id="feedKpis"></div>
     <div class="panel"><div class="table-tools">${chips('kind', KIND_LABEL)}</div>
       <div class="table-tools">${chips('group', GROUP_LABEL)}
         <label class="toggle"><input type="checkbox" id="feedRecent" ${F.recent ? 'checked' : ''}> Last 24 hours only</label>
         <input id="feedQ" type="search" placeholder="Filter" value="${esc(F.q)}"></div>
       <div id="feedList" class="feed-list"></div></div>
     <div class="panel"><h3>Sources <small>Manage them on the <button class="link-btn" data-go="keywords">Keywords and sources</button> lens</small></h3>
       <div id="feedHealth" class="health-strip"></div></div>`;
  $$('[data-kind]', main).forEach(b => b.onclick = () => { F.kind = b.dataset.kind; $$('[data-kind]', main).forEach(x => x.setAttribute('aria-pressed', String(x === b))); drawFeed(); });
  $$('[data-group]', main).forEach(b => b.onclick = () => { F.group = b.dataset.group; $$('[data-group]', main).forEach(x => x.setAttribute('aria-pressed', String(x === b))); drawFeed(); });
  $('#feedRecent').onchange = e => { F.recent = e.target.checked; drawFeed(); };
  $('#feedQ').oninput = e => { F.q = e.target.value; drawFeed(); };
  $('#feedRefresh').onclick = () => loadFeed();
  drawFeed();
};
function drawFeedStatus() {
  const el = $('#feedStatus');
  if (el) el.innerHTML = S.feed.loading ? '<span class="dot live"></span>Checking sources' : S.feed.checked ? `Checked ${esc(S.feed.checked)}` : '';
}
function drawFeed() {
  const F = S.feed, list = $('#feedList');
  if (!list) return;
  drawFeedStatus();
  if (!F.loaded) {
    list.innerHTML = F.err ? `<p class="loading-note">Could not load the feed: ${esc(F.err)}</p>`
      : '<p class="loading-note">Searching every source. The first load takes 10 to 20 seconds.</p><div class="skeleton" style="height:300px"></div>';
    return;
  }
  const now = Date.now(), q = F.q.toLowerCase();
  const inHour = F.items.filter(i => now - new Date(i.published) <= 3600e3).length;
  const inDay = F.items.filter(i => now - new Date(i.published) <= 86400e3).length;
  const ok = F.health.filter(h => h.status === 'ok').length, tried = F.health.filter(h => h.status !== 'off').length;
  $('#feedKpis').innerHTML = kpis([['Last hour', inHour, 'New items'], ['Last 24 hours', inDay, 'Items'],
    ['Newest item', F.items[0] ? ago(F.items[0].published) : '–', F.items[0] ? esc(F.items[0].source) : ''],
    ['Sources working', `${ok} / ${tried}`, 'Returned results this round', ok < tried ? 'down' : ''],
    ['Total in window', F.items.length, `Last ${S.days} days`]]);
  const rows = F.items.filter(i => (F.kind === 'all' || i.kind === F.kind) && (F.group === 'all' || i.group === F.group)
    && (!F.recent || now - new Date(i.published) <= 86400e3) && (!q || (i.title + ' ' + i.source).toLowerCase().includes(q)));
  list.innerHTML = rows.slice(0, 250).map(i => `<article class="feed-item ${F.fresh.has(i.id) ? 'fresh' : ''}">
      <div class="feed-time"><b>${ago(i.published)}</b><span>${fmtTime(i.published)}</span></div>
      <div><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener" class="feed-title">${esc(i.title)}</a>
        <div class="side-meta"><span>${esc(i.source)}</span><span class="pill ${i.kind === 'filing' ? 'caution' : 'neutral'}">${KIND_LABEL[i.kind] || i.kind}</span>
          <span>${GROUP_LABEL[i.group] || ''}${i.keyword ? ': ' + esc(i.keyword) : ''}</span>
          ${i.coverage > 1 ? `<span>Reported by ${i.coverage} outlets</span>` : ''}
          ${i.engagement ? `<span>${i.engagement} votes and comments</span>` : ''}
          <span style="color:${toneColor(i.sentiment)}">Tone ${tone(i.sentiment)}</span>
          ${F.fresh.has(i.id) ? '<span class="pill caution">New</span>' : ''}</div>
        ${i.why ? `<div class="reaction-why">${esc(i.why)}</div>` : ''}</div></article>`).join('')
    || '<p class="loading-note">Nothing matches these filters.</p>';
  $('#feedHealth').innerHTML = F.health.filter(h => h.status !== 'off').map(h =>
    `<span class="health st-${h.status}" title="${esc(h.note || h.desc)}"><i></i>${esc(h.name)} <b>${h.count}</b></span>`).join('');
}

/* ------------------------------------------------------------------ */
/* Materials                                                           */
/* ------------------------------------------------------------------ */
RENDER.materials = main => {
  const m = S.data.materials;
  const rows = m.rows.filter(r => r.available);
  if (!m.rows.length) {
    main.innerHTML = head('Materials', 'How the stock moves with the commodities that drive its costs or revenue.') +
      `<div class="empty"><h3>No commodities selected</h3><p>Choose the materials that matter for ${companyName()}, such as crude oil, steel or USD/INR.</p>
       <button class="btn" data-go="keywords">Choose commodities</button></div>`;
    return;
  }
  const corrBar = v => {
    if (v == null) return '–';
    const w = Math.min(Math.abs(v), 1) * 50, col = v > 0 ? C.blue : C.caution;
    return `<div class="bar-cell"><div class="bar-track" style="position:relative"><div style="position:absolute;top:0;bottom:0;left:${v < 0 ? 50 - w : 50}%;width:${w}%;background:${col}"></div>
      <div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--line)"></div></div><span class="num" style="width:44px;text-align:right">${v >= 0 ? '+' : ''}${v.toFixed(2)}</span></div>`;
  };
  main.innerHTML = head('Materials', `How ${companyName()} moves with the commodities that drive its costs or revenue.`,
    `<button class="btn ghost" data-go="keywords">Change commodities</button>`) +
    `<div class="panel"><h3>Commodity link</h3><div class="table-wrap"><table><thead><tr><th>Commodity</th><th class="num">Last</th>
      <th class="num">1 week</th><th class="num">1 month</th><th class="num">Period</th><th>Same-day link with stock</th><th class="num">Next-day link</th><th class="num">Sensitivity</th></tr></thead>
      <tbody>${m.rows.map(r => r.available ? `<tr><td>${esc(r.name)} <small style="color:var(--dim)">${esc(r.ticker)}</small></td>
        <td class="num">${nf(r.last, 2, 'USD')}</td><td class="num ${dir(r.chg_1w)}">${pct(r.chg_1w, 1)}</td><td class="num ${dir(r.chg_1m)}">${pct(r.chg_1m, 1)}</td>
        <td class="num ${dir(r.chg_period)}">${pct(r.chg_period, 1)}</td><td style="min-width:180px">${corrBar(r.corr)}</td>
        <td class="num">${r.lead_corr == null ? '–' : (r.lead_corr >= 0 ? '+' : '') + r.lead_corr.toFixed(2)}</td>
        <td class="num">${r.beta == null ? '–' : r.beta.toFixed(2)}</td></tr>`
        : `<tr><td>${esc(r.name)}</td><td colspan="7" style="color:var(--muted)">No price data available right now.</td></tr>`).join('')}</tbody></table></div>
      <details style="margin-top:12px;color:var(--muted)"><summary style="cursor:pointer">How to read this</summary>
      <p><b>Same-day link</b> is the correlation of daily moves: +1 means the stock rises and falls with the commodity, -1 means it moves the opposite way
      (typical when the commodity is a cost), near 0 means no clear link. <b>Next-day link</b> checks whether yesterday's commodity move shows up in the stock today.
      <b>Sensitivity</b> is the stock's average move for a 1% commodity move. Commodity prices are global futures in US dollars from Yahoo Finance.</p></details></div>
    <div class="grid g-main"><div class="panel"><h3>Stock vs commodities <small>Growth of 100. <span style="color:${C.accent}">Stock</span>${rows.map((r, i) => `, <span style="color:${LINE_COLORS[i % 6]}">${esc(r.name)}</span>`).join('')}</small></h3>
      <div class="chart" id="cMat"></div></div>
      <div class="panel"><h3>Materials in the news</h3><div class="side-list">${m.news.length ? m.news.map(n => `<div class="side-item">
        <a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener">${esc(n.title)}</a>
        <div class="side-meta"><span>${esc(n.source)}</span><span>${ago(n.published)}</span><span style="color:${toneColor(n.sentiment)}">Tone ${tone(n.sentiment)}</span></div></div>`).join('')
        : '<p class="loading-note">No material headlines in this window. Add material keywords on the Keywords and sources lens.</p>'}</div></div></div>`;
  const ch = mkChart($('#cMat'));
  ch.addLineSeries({ color: C.accent, lineWidth: 3, priceLineVisible: false }).setData(m.series.stock);
  rows.forEach((r, i) => { if (m.series[r.ticker]) ch.addLineSeries({ color: LINE_COLORS[i % 6], lineWidth: 2, priceLineVisible: false, lastValueVisible: false }).setData(m.series[r.ticker]); });
  ch.timeScale().fitContent();
};

/* ------------------------------------------------------------------ */
/* Keywords and sources                                                */
/* ------------------------------------------------------------------ */
function setSourceState(src) {
  S.kw.src = src;
  S.kw.srcEdit = { disabled: new Set(src.sources.filter(x => !x.enabled).map(x => x.id)),
    feeds: JSON.parse(JSON.stringify(src.feeds)), useDefaults: !src.custom_feeds, dirty: false };
}
const KW_HELP = {
  company: 'Names and abbreviations used in the media. Upper-case words such as RIL or the ticker match exactly.',
  people: 'CEO, chairman, founders, CFO. News about them often moves the stock.',
  products: 'Brands, subsidiaries and business units.',
  industry: 'Main competitors and sector phrases.',
  materials: 'Raw materials and inputs to watch in the news, for example crude oil price.',
  exclude: 'Headlines with these words are dropped, for example a namesake company.',
};
RENDER.keywords = main => {
  if (!S.kw) {
    main.innerHTML = head('Keywords and sources', 'Loading this company\'s keyword profile.') + '<div class="skeleton" style="height:300px"></div>';
    const tk = S.token;
    Promise.all([api('/api/keywords/' + enc(S.sym)), api('/api/settings/sources?symbol=' + enc(S.sym))]).then(([k, src]) => {
      if (tk !== S.token) return;
      S.kw = { ...k, edit: JSON.parse(JSON.stringify(k.profile)), added: new Set(), busy: false };
      setSourceState(src);
      if (S.lens === 'keywords') renderLens();
    }).catch(e => { main.innerHTML = head('Keywords and sources', '') + `<div class="empty"><h3>Could not load</h3><p>${esc(e.message)}</p></div>`; });
    return;
  }
  const K = S.kw, E = K.edit;
  const groupPanel = g => `<div class="panel kw-panel"><h3>${g === 'exclude' ? 'Exclude' : esc(K.groups[g])}</h3><p class="kw-help">${KW_HELP[g]}</p>
    <div class="kw-chips">${(E[g] || []).map((t, i) => `<span class="kw-chip ${K.added.has(g + '|' + t) ? 'added' : ''}">${esc(t)}<button data-del="${g}|${i}" aria-label="Remove ${esc(t)}">✕</button></span>`).join('')}
      <input class="kw-add" data-add="${g}" placeholder="Add and press Enter"></div>
    ${g === 'people' && K.officers.length ? `<p class="kw-help" style="margin-top:10px">From company records: ${K.officers.slice(0, 6).map(o =>
      `<button class="link-btn" data-officer="${esc(o.name)}">${esc(o.name)}</button> <span style="color:var(--dim)">(${esc(o.title)})</span>`).join(', ')}</p>` : ''}</div>`;
  const health = (S.feed?.health?.length ? S.feed.health : null);
  const SE = K.srcEdit;
  const srcRows = K.src.sources.map(src => {
    const h = health?.find(x => x.id === src.id);
    const on = !SE.disabled.has(src.id);
    const status = !on ? 'off' : h ? h.status : src.state === 'off' ? 'off' : 'pending';
    const label = { ok: 'Working', empty: 'No results', error: 'Error', off: 'Off', pending: 'Waiting' }[status];
    const note = !on ? 'Switched off here. Turn on and save to use it again.' : src.locked || (h && h.note) || src.note || '';
    return `<tr><td><label class="switch" title="${src.locked ? esc(src.locked) : on ? 'Switch off' : 'Switch on'}">
        <input type="checkbox" data-src="${src.id}" ${on && !src.locked ? 'checked' : ''} ${src.locked ? 'disabled' : ''}><span></span></label></td>
      <td><b>${esc(src.name)}</b><div class="kw-help" style="margin:2px 0 0">${esc(src.desc)}</div></td><td>${KIND_LABEL[src.kind]}</td>
      <td><span class="health st-${status}"><i></i>${label}</span></td><td class="num">${h && on ? h.count : '–'}</td><td>${h?.newest && on ? ago(h.newest) : '–'}</td>
      <td style="color:var(--muted);max-width:340px">${esc(note)}</td></tr>`;
  }).join('');
  const feedRows = region => (SE.feeds[region] || []).map(([name, url], i) => {
    const st = K.src.feed_status[url];
    const cls = !st ? 'pending' : st.ok ? 'ok' : 'error';
    const txt = !st ? 'Not checked yet' : st.ok ? `${st.entries} articles${st.newest ? ', newest ' + ago(st.newest) : ''}` : st.error;
    return `<tr><td>${esc(name)}</td><td class="feed-url" title="${esc(url)}">${esc(url)}</td>
      <td><span class="health st-${cls}"><i></i>${esc(txt)}</span></td>
      <td><button class="icon-btn" data-rmfeed="${region}|${i}" aria-label="Remove ${esc(name)}">✕</button></td></tr>`;
  }).join('') || '<tr><td colspan="4" style="color:var(--muted)">No feeds.</td></tr>';
  main.innerHTML = head('Keywords and sources', `What the app searches for ${companyName()} across every source.`,
    `<div style="display:flex;gap:10px;flex-wrap:wrap">${K.ai_enabled ? `<button class="btn ghost" id="kwSuggest" ${K.busy ? 'disabled' : ''}>${K.busy ? 'AI is thinking' : 'Suggest with AI'}</button>` : ''}
     <button class="btn ghost" id="kwReset">Reset to automatic</button><button class="btn" id="kwSave">Save and refresh</button></div>`) +
    (E.auto || K.profile.auto ? `<p class="loading-note">This profile was built automatically from company data. ${K.ai_enabled ? 'Use Suggest with AI to add brands, competitors and materials, then save.' : 'Edit it and save to make it yours.'}</p>` : '') +
    (K.added.size ? '<p class="loading-note" style="color:var(--accent)">Highlighted keywords are new suggestions. Remove any you do not want, then save.</p>' : '') +
    `<div class="kw-grid">${['company', 'people', 'products', 'industry', 'materials', 'exclude'].map(groupPanel).join('')}</div>
    <div class="grid g2"><div class="panel"><h3>Commodities to track <small>Up to 6, shown on the Materials lens</small></h3>
      <div class="kw-commod">${K.commodities.map(c => `<label><input type="checkbox" data-com="${c.ticker}" ${E.commodities.includes(c.ticker) ? 'checked' : ''}> ${esc(c.name)}</label>`).join('')}</div></div>
    <div class="panel"><h3>BSE scrip code <small>6 digits, switches on BSE announcements</small></h3>
      <input id="kwBse" class="plain-in" inputmode="numeric" maxlength="6" placeholder="e.g. 543066" value="${esc(E.bse_code || '')}">
      <h3 style="margin-top:18px">Custom RSS feeds <small>Company press releases or investor-relations feeds, one URL per line</small></h3>
      <textarea id="kwFeeds" rows="5" placeholder="https://www.company.com/press/rss">${esc((E.extra_feeds || []).join('\n'))}</textarea></div></div>
    <div class="panel"><div class="lens-head" style="margin-bottom:12px"><div><h3 style="margin:0">Sources <small>Settings apply to every stock</small></h3></div>
      <div style="display:flex;gap:10px">${SE.dirty ? '<span class="loading-note" style="margin:0;align-self:center;color:var(--accent)">Unsaved changes</span>' : ''}
      <button class="btn" id="srcSave" ${SE.dirty ? '' : 'disabled'}>Save source settings</button></div></div>
      <div class="table-wrap" style="max-height:none"><table><thead><tr><th>On</th><th>Source</th><th>Type</th><th>Status</th><th class="num">Items</th><th>Newest</th><th>Note</th></tr></thead>
      <tbody>${srcRows}</tbody></table></div>
      <p class="kw-help" style="margin-top:12px">Switches you can't change need a key in the server environment: add it on Render (Environment tab), then redeploy.</p></div>
    <div class="panel"><div class="lens-head" style="margin-bottom:8px"><div><h3 style="margin:0">Publisher feeds <small>${K.src.custom_feeds ? 'Your edited list' : 'Default list from feeds.json'}</small></h3>
      <p class="kw-help" style="margin:4px 0 0">Scanned every 2 minutes for this stock's keywords. India feeds are used for NSE and BSE stocks, global feeds for the rest.</p></div>
      <button class="btn ghost" id="feedDefaults">Restore defaults</button></div>
      <div class="grid g2"><div><h3 style="font-size:.92rem">India</h3><div class="table-wrap"><table><tbody>${feedRows('IN')}</tbody></table></div></div>
      <div><h3 style="font-size:.92rem">Global</h3><div class="table-wrap"><table><tbody>${feedRows('GLOBAL')}</tbody></table></div></div></div>
      <div class="feed-add"><input id="nfName" placeholder="Name, e.g. NDTV Profit"><input id="nfUrl" placeholder="Feed address, https://...">
        <select id="nfRegion"><option value="IN">India</option><option value="GLOBAL">Global</option></select>
        <button class="btn ghost" id="nfTest">Test</button><button class="btn ghost" id="nfAdd">Add feed</button></div>
      <p class="kw-help" id="nfResult" style="margin:8px 0 0"></p></div>`;

  $$('[data-del]', main).forEach(b => b.onclick = () => { const [g, i] = b.dataset.del.split('|'); E[g].splice(+i, 1); renderLens(true); });
  $$('[data-add]', main).forEach(inp => inp.onkeydown = e => {
    if (e.key !== 'Enter') return;
    const g = inp.dataset.add, v = inp.value.trim();
    if (v && !E[g].some(x => x.toLowerCase() === v.toLowerCase())) E[g].push(v);
    renderLens(true);
    $(`[data-add="${g}"]`)?.focus();
  });
  $$('[data-officer]', main).forEach(b => b.onclick = () => { if (!E.people.includes(b.dataset.officer)) E.people.push(b.dataset.officer); renderLens(true); });
  $$('[data-com]', main).forEach(cb => cb.onchange = () => {
    const t = cb.dataset.com;
    if (cb.checked) { if (E.commodities.length >= 6) { cb.checked = false; toast('Pick up to 6 commodities.', true); return; } E.commodities.push(t); }
    else E.commodities = E.commodities.filter(x => x !== t);
  });
  const markDirty = () => { SE.dirty = true; renderLens(true); };
  $$('[data-src]', main).forEach(cb => cb.onchange = () => {
    cb.checked ? SE.disabled.delete(cb.dataset.src) : SE.disabled.add(cb.dataset.src);
    markDirty();
  });
  $$('[data-rmfeed]', main).forEach(b => b.onclick = () => { const [r, i] = b.dataset.rmfeed.split('|'); SE.feeds[r].splice(+i, 1); markDirty(); });
  $('#feedDefaults').onclick = () => { SE.feeds = JSON.parse(JSON.stringify(K.src.default_feeds)); SE.useDefaults = true; markDirty(); };
  const nf = () => ({ name: $('#nfName').value.trim(), url: $('#nfUrl').value.trim(), region: $('#nfRegion').value });
  $('#nfTest').onclick = async () => {
    const { url } = nf(), out = $('#nfResult');
    if (!url) { out.textContent = 'Enter a feed address first.'; return; }
    out.textContent = 'Testing...';
    try {
      const r = await post('/api/settings/test-feed', { url });
      out.innerHTML = r.ok ? `<span class="up">Works.</span> ${esc(r.title || '')} has ${r.entries} articles${r.newest ? ', newest ' + ago(r.newest) : ''}. Latest: ${esc((r.sample || [])[0] || '')}`
        : `<span class="down">Does not work.</span> ${esc(r.error)}`;
      if (r.ok && !$('#nfName').value.trim() && r.title) $('#nfName').value = r.title.slice(0, 60);
    } catch (e) { out.innerHTML = `<span class="down">${esc(e.message)}</span>`; }
  };
  $('#nfAdd').onclick = () => {
    const { name, url, region } = nf();
    if (!name || !/^https?:\/\//.test(url)) { $('#nfResult').textContent = 'Enter a name and a full address starting with https://'; return; }
    if ((SE.feeds[region] || []).some(f => f[1] === url)) { $('#nfResult').textContent = 'That feed is already in the list.'; return; }
    (SE.feeds[region] ||= []).push([name, url]);
    SE.useDefaults = false;
    markDirty();
    toast('Feed added. Save source settings to start using it.');
  };
  $('#srcSave').onclick = async () => {
    try {
      const feeds = SE.useDefaults ? null : SE.feeds;
      const r = await fetch('/api/settings/sources?symbol=' + enc(S.sym), { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ disabled: [...SE.disabled], feeds }) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      setSourceState(await r.json());
      toast('Source settings saved. Pulling news again.');
      S.data = null; S.feed = { ...newFeedState(), timer: S.feed.timer };
      refreshAnalysis(); loadFeed(); renderLens(true);
    } catch (e) { toast('Could not save: ' + e.message, true); }
  };
  const readFeeds = () => { E.extra_feeds = $('#kwFeeds').value.split('\n').map(x => x.trim()).filter(Boolean); E.bse_code = ($('#kwBse')?.value || '').trim(); };
  $('#kwSave').onclick = async () => {
    readFeeds();
    try {
      const r = await fetch('/api/keywords/' + enc(S.sym), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(E) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const saved = (await r.json()).profile;
      Object.assign(S.kw, { profile: saved, edit: JSON.parse(JSON.stringify(saved)), added: new Set() });
      toast('Saved. Pulling news with the new keywords.');
      S.data = null; S.feed = { ...newFeedState(), timer: S.feed.timer };
      refreshAnalysis(); loadFeed(); renderLens(true);
    } catch (e) { toast('Could not save: ' + e.message, true); }
  };
  $('#kwReset').onclick = async () => {
    try {
      const r = await fetch('/api/keywords/' + enc(S.sym), { method: 'DELETE' });
      const p = (await r.json()).profile;
      Object.assign(S.kw, { profile: p, edit: JSON.parse(JSON.stringify(p)), added: new Set() });
      S.data = null; S.feed = { ...newFeedState(), timer: S.feed.timer };
      refreshAnalysis(); loadFeed(); renderLens(true);
      toast('Back to the automatic profile.');
    } catch (e) { toast('Reset failed: ' + e.message, true); }
  };
  const sug = $('#kwSuggest');
  if (sug) sug.onclick = async () => {
    readFeeds();
    K.busy = true; renderLens(true);
    try {
      const p = (await post(`/api/keywords/${enc(S.sym)}/suggest`, E)).profile;
      const added = new Set();
      ['company', 'people', 'products', 'industry', 'materials', 'exclude'].forEach(g => (p[g] || []).forEach(t => { if (!E[g].includes(t)) added.add(g + '|' + t); }));
      K.edit = { ...p, auto: E.auto }; K.added = added;
      toast(added.size ? `AI suggested ${added.size} keywords. Review, then save.` : 'No new keywords suggested.');
    } catch (e) { toast('Suggestion failed: ' + e.message, true); }
    K.busy = false;
    if (S.lens === 'keywords') renderLens(true);
  };
};

/* ------------------------------------------------------------------ */
/* Surveillance: monitor, incidents, reports                           */
/* ------------------------------------------------------------------ */
function stopSvPoll() { if (S.svTimer) { clearTimeout(S.svTimer); S.svTimer = null; } }
function svPoll(fn, ms) { stopSvPoll(); S.svTimer = setTimeout(fn, ms); }
const yesNo = (b, strong = true) => `<span class="yn ${b ? 'yes' : 'no'}">${b ? 'YES' : 'NO'}</span>`;
const istTime = iso => iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) : '–';
const TYPE_LABEL = { official_disclosure: 'Official disclosure', confirmed_news: 'Confirmed news', unverified_rumour: 'Unverified rumour',
  analysis_opinion: 'Analysis / opinion', social_chatter: 'Social chatter', unrelated: 'Unrelated' };
const CASE_LABEL = { open: 'Open', review: 'Under review', clarification: 'Clarification sought', monitoring: 'Monitoring', closed: 'Closed' };
const KIND_TRIG = { move_up: 'High trigger', move_down: 'Low trigger', price_above: 'Price above level', price_below: 'Price below level', volume: 'Volume spike', manual: 'Manual' };
const casePill = c => `<span class="pill case-${c || 'open'}">${CASE_LABEL[c || 'open']}</span>`;
const TIER_LABEL = { official: 'Official', mainstream: 'Mainstream media', online: 'Other online', social: 'Social / forum' };
const dirPill = d => `<span class="pill ${d === 'positive' ? 'positive' : d === 'negative' ? 'negative' : 'neutral'}">${esc(d || 'neutral')}</span>`;
const pctv = (v, d = 2) => v == null ? '–' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`;

async function loadLandingSurveillance() {
  const box = $('#landingSv');
  if (!box) return;
  try {
    const [st, incs] = await Promise.all([api('/api/surveillance/status'), api('/api/surveillance/incidents')]);
    const wl = st.config.watchlist.filter(w => w.enabled);
    const recent = incs.slice(0, 4);
    box.innerHTML = `<div class="sv-land-head"><span class="ai-status ${st.config.enabled && wl.length ? 'on' : ''}">Surveillance:
      ${wl.length ? `monitoring ${wl.length} stock${wl.length > 1 ? 's' : ''} (${wl.slice(0, 5).map(w => esc(w.symbol)).join(', ')}${wl.length > 5 ? '...' : ''})` : 'no stocks on the watchlist yet'}</span></div>
      ${recent.length ? `<div class="sv-land-list">${recent.map(i => `<button class="sv-land-item" data-open-inc="${esc(i.symbol)}|${esc(i.id)}">
        <b class="${i.trigger.move >= 0 ? 'up' : 'down'}">${esc(i.symbol)} ${pctv(i.trigger.move)}</b>
        <span>${istTime(i.created)}</span><span>${i.status === 'complete' ? esc(i.category || '') : i.status === 'failed' ? 'Investigation failed' : 'Investigating'}</span></button>`).join('')}</div>` : ''}`;
    $$('[data-open-inc]', box).forEach(b => b.onclick = () => { const [sym, id] = b.dataset.openInc.split('|'); openStock(sym, 'incidents', id); });
  } catch { box.innerHTML = ''; }
}

/* Monitor lens -------------------------------------------------------- */
const numOrNull = v => { const x = parseFloat(v); return isNaN(x) ? null : x; };
RENDER.monitor = main => {
  if (!S.sv) {
    main.innerHTML = head('Monitor and alerts', 'Loading the surveillance watchlist.') + '<div class="skeleton" style="height:300px"></div>';
    api('/api/surveillance/status').then(st => { S.sv = { st, edit: JSON.parse(JSON.stringify(st.config)), dirty: false, open: null }; if (S.lens === 'monitor') renderLens(); })
      .catch(e => { main.innerHTML = head('Monitor and alerts', '') + `<div class="empty"><h3>Could not load</h3><p>${esc(e.message)}</p></div>`; });
    return;
  }
  const V = S.sv, st = V.st, E = V.edit;
  const inList = E.watchlist.some(w => w.symbol === S.sym);
  const upOf = w => w.up_pct ?? E.up_pct, downOf = w => w.down_pct ?? E.down_pct;
  const rows = E.watchlist.map((w, i) => {
    const q = st.quotes[w.symbol];
    const val = q ? q.value : null, thr = val == null ? null : val >= 0 ? upOf(w) : downOf(w);
    const near = val != null && Math.abs(val) >= thr * 0.7;
    const state = !w.enabled ? ['off', 'Paused'] : !q || q.move == null ? ['pending', 'Waiting for first check']
      : Math.abs(val) >= thr ? ['error', 'Triggered'] : near ? ['empty', 'Near trigger'] : ['ok', 'Normal'];
    const custom = w.up_pct != null || w.down_pct != null;
    const extras = [w.price_above ? `above ${nf(w.price_above, 2)}` : '', w.price_below ? `below ${nf(w.price_below, 2)}` : '',
      (w.volume_x ?? E.volume_x) ? `volume ${w.volume_x ?? E.volume_x}x` : ''].filter(Boolean);
    const open = V.open === i;
    return `<tr class="${open ? 'row-open' : ''}"><td><b>${esc(w.symbol)}</b><div class="kw-help" style="margin:0">${esc(w.name || '')}</div></td>
      <td class="num">${q?.last ? nf(q.last, 2, q.currency) : '–'}</td>
      <td class="num ${dir(q?.move)}">${pctv(q?.move)}</td><td class="num ${dir(q?.adjusted)}">${pctv(q?.adjusted)}</td>
      <td class="num">${q?.volume_x ? q.volume_x.toFixed(1) + 'x' : '–'}</td>
      <td><span class="trig up">+${upOf(w)}%</span> <span class="trig down">-${downOf(w)}%</span>${custom ? '' : ' <small class="dimtxt">default</small>'}
        ${extras.length ? `<div class="kw-help" style="margin:2px 0 0">${extras.join(', ')}</div>` : ''}</td>
      <td><span class="health st-${state[0]}"><i></i>${state[1]}</span></td>
      <td style="white-space:nowrap"><button class="btn ghost sm" data-edit="${i}">${open ? 'Close' : 'Edit triggers'}</button>
        <button class="btn ghost sm" data-inv="${esc(w.symbol)}">Investigate now</button>
        <label class="switch" title="${w.enabled ? 'Pause' : 'Resume'} monitoring"><input type="checkbox" data-wl-on="${i}" ${w.enabled ? 'checked' : ''}><span></span></label>
        <button class="icon-btn" data-wl-rm="${i}" aria-label="Remove ${esc(w.symbol)}">✕</button></td></tr>` +
      (open ? `<tr class="edit-row"><td colspan="8"><div class="trig-form">
        <label>High trigger: rise of (%)<input type="number" step="0.1" min="0.1" data-f="up_pct" value="${w.up_pct ?? ''}" placeholder="${E.up_pct} (default)"></label>
        <label>Low trigger: fall of (%)<input type="number" step="0.1" min="0.1" data-f="down_pct" value="${w.down_pct ?? ''}" placeholder="${E.down_pct} (default)"></label>
        <label>Alert if price goes above<input type="number" step="0.05" min="0" data-f="price_above" value="${w.price_above ?? ''}" placeholder="Off"></label>
        <label>Alert if price goes below<input type="number" step="0.05" min="0" data-f="price_below" value="${w.price_below ?? ''}" placeholder="Off"></label>
        <label>Volume spike (x 20-day average)<input type="number" step="0.5" min="1" data-f="volume_x" value="${w.volume_x ?? ''}" placeholder="${E.volume_x ? E.volume_x + ' (default)' : 'Off'}"></label>
        <label class="wide">Extra recipients for this stock<input data-f="recipients" value="${esc((w.recipients || []).join(', '))}" placeholder="team@company.com, analyst@company.com"></label>
        </div><p class="kw-help" style="margin:8px 0 0">Leave a box empty to use the default. Percentage triggers repeat at each multiple during the day (for a 2.5% low trigger: -2.5%, -5%, -7.5%).
        Price and volume alerts fire once a day.</p></td></tr>` : '');
  }).join('');
  const emailTxt = st.email === 'smtp' ? 'Email via SMTP' : st.email === 'resend' ? 'Email via Resend' : 'Email not set up';
  main.innerHTML = head('Monitor and alerts', 'Watches each stock against its previous close and the index. A move past its high or low trigger sends an alert, starts an investigation and produces a report.',
    `<div style="display:flex;gap:10px;flex-wrap:wrap">${inList ? '' : `<button class="btn ghost" id="wlAddCur">Add ${esc(S.sym)} to watchlist</button>`}
     <button class="btn" id="svSave" ${V.dirty ? '' : 'disabled'}>${V.dirty ? 'Save changes' : 'Saved'}</button></div>`) +
    kpis([['Monitoring', st.config.enabled ? (st.market_open || !st.config.market_hours_only ? 'Active' : 'Waiting for market') : 'Paused',
        st.last_cycle ? `Last check ${ago(st.last_cycle)}` : 'Not checked yet', st.config.enabled ? 'up' : 'down'],
      ['Stocks watched', st.config.watchlist.filter(w => w.enabled).length, `of ${st.config.watchlist.length} on the list`],
      ['Market (NSE)', st.market_open ? 'Open' : 'Closed', '9:00 to 15:40 IST, weekdays'],
      ['Alerts', emailTxt, `${st.config.recipients.length} recipient(s), ${st.webhooks || 0} webhook(s)`, st.email ? '' : 'down']]) +
    (st.email ? '' : `<p class="loading-note" style="color:var(--caution)">Email is not set up yet, so alerts cannot be sent. <button class="link-btn" data-go="notify">Set up email</button></p>`) +
    (st.last_error ? `<p class="loading-note" style="color:var(--loss)">Last monitor error: ${esc(st.last_error)}</p>` : '') +
    `<div class="panel"><h3>Watchlist</h3><div class="table-wrap" style="max-height:none"><table><thead><tr><th>Stock</th><th class="num">Last</th><th class="num">vs prev close</th>
      <th class="num">vs index</th><th class="num">Volume</th><th>Triggers</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8" style="color:var(--muted)">No stocks yet. Add one below or use the button at the top.</td></tr>'}</tbody></table></div>
      <div class="feed-add" style="grid-template-columns:2fr 1fr auto"><input id="wlSym" placeholder="Symbol, e.g. SBICARD.NS"><input id="wlName" placeholder="Name (optional)">
        <button class="btn ghost" id="wlAdd">Add to watchlist</button></div></div>
    <div class="grid g2"><div class="panel"><h3>Default triggers <small>Used for any stock without its own</small></h3>
      <div class="form-grid">
        <label>High trigger: rise of (%)<input type="number" id="cfUp" step="0.1" min="0.1" value="${E.up_pct}"></label>
        <label>Low trigger: fall of (%)<input type="number" id="cfDown" step="0.1" min="0.1" value="${E.down_pct}"></label>
        <label>Volume spike (x average)<select id="cfVol">${[0, 2, 3, 4, 5].map(v => `<option value="${v}" ${(E.volume_x || 0) === v ? 'selected' : ''}>${v ? v + 'x the 20-day average' : 'Off'}</option>`).join('')}</select></label>
        <label>Measure the move<select id="cfBasis">
          <option value="either" ${E.basis === 'either' ? 'selected' : ''}>Raw or index-adjusted, whichever is larger</option>
          <option value="raw" ${E.basis === 'raw' ? 'selected' : ''}>Raw: vs previous close</option>
          <option value="adjusted" ${E.basis === 'adjusted' ? 'selected' : ''}>Index-adjusted: minus the index move</option></select></label>
      </div>
      <p class="kw-help" style="margin-top:12px">Set these to match the thresholds in NSE/SURV/62122. Each stock can have its own high and low triggers: use Edit triggers in the watchlist.</p></div>
    <div class="panel"><h3>Monitoring</h3>
      <div class="form-grid">
        <label>Check every<select id="cfInt">${[30, 60, 120, 300].map(v => `<option value="${v}" ${E.interval === v ? 'selected' : ''}>${v < 60 ? v + ' seconds' : v / 60 + ' minute' + (v > 60 ? 's' : '')}</option>`).join('')}</select></label>
        <label>Look back before the move<select id="cfWin">${[12, 24, 48, 72].map(v => `<option value="${v}" ${E.window_hours === v ? 'selected' : ''}>${v} hours</option>`).join('')}</select></label>
      </div>
      <label class="toggle" style="margin:12px 0 0"><input type="checkbox" id="cfMkt" ${E.market_hours_only ? 'checked' : ''}> Check only during market hours</label><br>
      <label class="toggle" style="margin:6px 0 0"><input type="checkbox" id="cfOn" ${E.enabled ? 'checked' : ''}> Monitoring switched on</label><br>
      <label class="toggle" style="margin:6px 0 0"><input type="checkbox" id="cfAutoMail" ${E.auto_email_report ? 'checked' : ''}> Email the report automatically when ready</label>
      <p class="kw-help" style="margin-top:12px">Recipients, email server, Teams and Slack, and the daily summary are on the <button class="link-btn" data-go="notify">Email and notifications</button> lens.</p></div></div>`;

  const dirty = () => { V.dirty = true; renderLens(true); };
  const addSym = (sym, name) => {
    sym = sym.trim().toUpperCase();
    if (!sym) return;
    if (E.watchlist.some(w => w.symbol === sym)) { toast(`${sym} is already on the watchlist.`); return; }
    E.watchlist.push({ symbol: sym, name: name || sym, up_pct: null, down_pct: null, price_above: null, price_below: null, volume_x: null, recipients: [], enabled: true });
    V.open = E.watchlist.length - 1;
    dirty();
  };
  $('#wlAddCur')?.addEventListener('click', () => addSym(S.sym, S.profile?.name || S.sym));
  $('#wlAdd').onclick = () => addSym($('#wlSym').value, $('#wlName').value.trim());
  $$('[data-edit]', main).forEach(b => b.onclick = () => { V.open = V.open === +b.dataset.edit ? null : +b.dataset.edit; renderLens(true); });
  $$('[data-wl-rm]', main).forEach(b => b.onclick = () => { E.watchlist.splice(+b.dataset.wlRm, 1); V.open = null; dirty(); });
  $$('[data-wl-on]', main).forEach(c => c.onchange = () => { E.watchlist[+c.dataset.wlOn].enabled = c.checked; dirty(); });
  $$('.edit-row [data-f]', main).forEach(inp => inp.onchange = () => {
    const w = E.watchlist[V.open], f = inp.dataset.f;
    if (f === 'recipients') w.recipients = inp.value.split(',').map(x => x.trim()).filter(x => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(x));
    else w[f] = numOrNull(inp.value);
    if (f === 'up_pct' || f === 'down_pct') { if (w[f] != null && w[f] <= 0) { w[f] = null; toast('Use a positive percentage, for example 2.5', true); } }
    dirty();
  });
  $('#cfUp').onchange = e => { E.up_pct = numOrNull(e.target.value) || 3; dirty(); };
  $('#cfDown').onchange = e => { E.down_pct = numOrNull(e.target.value) || 3; dirty(); };
  $('#cfVol').onchange = e => { E.volume_x = +e.target.value; dirty(); };
  $('#cfBasis').onchange = e => { E.basis = e.target.value; dirty(); };
  $('#cfInt').onchange = e => { E.interval = +e.target.value; dirty(); };
  $('#cfWin').onchange = e => { E.window_hours = +e.target.value; dirty(); };
  $('#cfMkt').onchange = e => { E.market_hours_only = e.target.checked; dirty(); };
  $('#cfOn').onchange = e => { E.enabled = e.target.checked; dirty(); };
  $('#cfAutoMail').onchange = e => { E.auto_email_report = e.target.checked; dirty(); };
  $('#svSave').onclick = async () => {
    try {
      const r = await fetch('/api/surveillance/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(E) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const st2 = await r.json();
      S.sv = { st: st2, edit: JSON.parse(JSON.stringify(st2.config)), dirty: false, open: null };
      S.svOverview = null;
      toast('Surveillance settings saved.'); renderLens(true);
    } catch (e) { toast('Could not save: ' + e.message, true); }
  };
  $$('[data-inv]', main).forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Starting';
    try {
      const r = await post('/api/surveillance/investigate', { symbol: b.dataset.inv });
      toast('Investigation started.');
      if (b.dataset.inv !== S.sym) openStock(b.dataset.inv, 'incidents', r.id); else setLens('incidents', r.id);
    } catch (e) { toast(e.message, true); b.disabled = false; b.textContent = 'Investigate now'; }
  });
  svPoll(async () => {
    if (S.lens !== 'monitor') return;
    try { const st2 = await api('/api/surveillance/status'); S.sv.st = st2; if (!S.sv.dirty) S.sv.edit = JSON.parse(JSON.stringify(st2.config)); if (S.sv.open == null) renderLens(true); } catch { /* retry */ }
  }, 15000);
};

/* Email and notifications lens --------------------------------------- */
RENDER.notify = main => {
  if (!S.nt) {
    main.innerHTML = head('Email and notifications', 'Loading settings.') + '<div class="skeleton" style="height:300px"></div>';
    Promise.all([api('/api/settings/email'), api('/api/surveillance/status'), api('/api/notifications?limit=60')]).then(([em, st, log]) => {
      S.nt = { em, edit: { ...em, password: '', api_key: '' }, cfg: JSON.parse(JSON.stringify(st.config)), log, dirtyMail: false, dirtyCfg: false };
      if (S.lens === 'notify') renderLens();
    }).catch(e => { main.innerHTML = head('Email and notifications', '') + `<div class="empty"><h3>Could not load</h3><p>${esc(e.message)}</p></div>`; });
    return;
  }
  const T = S.nt, M = T.edit, CF = T.cfg, P = T.em.presets[M.provider] || {};
  const isApi = M.provider === 'resend' || M.provider === 'brevo';
  const srcTxt = T.em.source === 'app' ? (T.em.ready ? 'Configured here and ready' : 'Configured here, but incomplete')
    : T.em.source === 'env' ? `Using the server environment (${T.em.env_method.toUpperCase()}). Settings saved here take over.` : 'Not configured';
  const smtpOnRender = !isApi && /onrender\.com$/.test(location.hostname);
  main.innerHTML = head('Email and notifications', 'Where alerts, reports and daily summaries are sent, and how.') +
    kpis([['Email', T.em.ready ? 'Ready' : 'Not ready', esc(srcTxt), T.em.ready ? 'up' : 'down'],
      ['Recipients', CF.recipients.length, 'Receive every alert and report'],
      ['Teams / Slack', CF.webhooks.filter(h => h.enabled).length, 'Active webhooks'],
      ['Daily summary', CF.digest_enabled ? CF.digest_time + ' IST' : 'Off', 'Weekdays after market close']]) +
    (T.em.secret_unreadable ? '<p class="loading-note" style="color:var(--loss)">The saved password can no longer be read (the APP_SECRET changed). Please enter it again and save.</p>' : '') +
    `<div class="grid g2"><div class="panel"><div class="lens-head" style="margin-bottom:12px"><div><h3 style="margin:0">Email server</h3></div>
        <label class="switch" title="Use these settings"><input type="checkbox" id="emOn" ${M.enabled ? 'checked' : ''}><span></span></label></div>
      <div class="form-grid">
        <label class="wide">Provider<select id="emProv">${Object.entries(T.em.presets).map(([k, v]) => `<option value="${k}" ${M.provider === k ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select></label>
        ${isApi ? `<label class="wide">${M.provider === 'brevo' ? 'Brevo' : 'Resend'} API key<input type="password" id="emKey" autocomplete="new-password" placeholder="${T.em.has_api_key ? 'Saved. Leave blank to keep it' : M.provider === 'brevo' ? 'xkeysib-...' : 're_...'}" value="${esc(M.api_key)}"></label>`
          : `<label>SMTP server<input id="emHost" value="${esc(M.host)}" placeholder="smtp.example.com"></label>
             <label>Port<input type="number" id="emPort" value="${M.port}"></label>
             <label>Security<select id="emSec"><option value="starttls" ${M.security === 'starttls' ? 'selected' : ''}>STARTTLS (port 587)</option>
               <option value="ssl" ${M.security === 'ssl' ? 'selected' : ''}>SSL/TLS (port 465)</option><option value="none" ${M.security === 'none' ? 'selected' : ''}>None (not recommended)</option></select></label>
             <label>Username<input id="emUser" value="${esc(M.username)}" autocomplete="off" placeholder="alerts@company.com"></label>
             <label class="wide">Password or app password<input type="password" id="emPass" autocomplete="new-password" placeholder="${T.em.has_password ? 'Saved. Leave blank to keep it' : 'Enter password'}" value="${esc(M.password)}"></label>`}
        <label>Sender name<input id="emFromName" value="${esc(M.from_name)}" placeholder="Drishti Alerts"></label>
        <label>Sender email<input id="emFrom" value="${esc(M.from_email)}" placeholder="alerts@company.com"></label>
        <label class="wide">Reply-to (optional)<input id="emReply" value="${esc(M.reply_to)}" placeholder="compliance@company.com"></label>
      </div>
      ${P.hint ? `<p class="kw-help" style="margin-top:10px">${esc(P.hint)}</p>` : ''}
      ${smtpOnRender ? '<p class="kw-help" style="color:var(--caution)">This app runs on Render. Render\'s free plan blocks SMTP, so choose Brevo or Resend unless you are on a paid plan.</p>' : ''}
      <p class="kw-help">Passwords are encrypted on the server and never shown again.${T.em.secret_from_env ? '' : ' Set APP_SECRET on Render for the strongest protection.'}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px"><button class="btn" id="emSave" ${T.dirtyMail ? '' : 'disabled'}>Save email settings</button>
        <input id="emTestTo" class="plain-in" style="flex:1;min-width:200px" placeholder="Send a test to (defaults to recipients)"><button class="btn ghost" id="emTest">Send test</button></div>
      <div style="margin-top:14px"><button class="btn ghost" id="emDiag">Check server connection</button>
        <span class="kw-help" style="margin-left:8px">Tests from the server itself which mail services it can reach.</span>
        <div id="emDiagOut" class="diag">${T.diag ? T.diag.map(d => `<div><span class="health st-${d.ok ? 'ok' : 'error'}"><i></i>${esc(d.name)}</span><span class="dimtxt">${esc(d.target)}</span><span>${esc(d.message)}</span></div>`).join('')
          + (T.diag.some(d => !d.ok && d.target.includes('smtp')) && T.diag.some(d => d.ok && d.target.includes('443'))
            ? '<p class="kw-help" style="color:var(--caution);margin-top:8px">Email ports are blocked on this server but HTTPS works: choose Brevo or Resend as the provider.</p>' : '') : ''}</div></div></div>

    <div class="panel"><h3>Recipients <small>Every alert, report and daily summary</small></h3>
      <div class="kw-chips">${CF.recipients.map((r, i) => `<span class="kw-chip">${esc(r)}<button data-rc-rm="${i}" aria-label="Remove ${esc(r)}">✕</button></span>`).join('')}
        <input class="kw-add" id="rcAdd" type="email" placeholder="name@company.com and press Enter"></div>
      <p class="kw-help" style="margin-top:10px">Stock-specific recipients are set with Edit triggers on the Monitor and alerts lens.</p>
      <h3 style="margin-top:20px">Daily summary</h3>
      <label class="toggle" style="margin:0"><input type="checkbox" id="dgOn" ${CF.digest_enabled ? 'checked' : ''}> Email a summary every weekday at</label>
      <input type="time" id="dgTime" class="plain-in" style="width:120px;display:inline-block;margin-left:8px" value="${esc(CF.digest_time)}"> IST
      <p class="kw-help" style="margin-top:8px">Watchlist moves at close, the day's incidents and open cases.</p>
      <button class="btn ghost" id="dgNow">Send summary now</button>
      <h3 style="margin-top:20px">Teams and Slack</h3>
      ${CF.webhooks.map((h, i) => `<div class="hook-row"><span class="pill neutral">${h.type === 'teams' ? 'Teams' : h.type === 'slack' ? 'Slack' : 'Webhook'}</span>
        <b>${esc(h.name)}</b><span class="feed-url" title="${esc(h.url)}">${esc(h.url)}</span>
        <label class="switch"><input type="checkbox" data-hk-on="${i}" ${h.enabled ? 'checked' : ''}><span></span></label>
        <button class="icon-btn" data-hk-rm="${i}" aria-label="Remove">✕</button></div>`).join('') || '<p class="kw-help">No webhooks yet.</p>'}
      <div class="feed-add" style="grid-template-columns:auto 1fr 2fr auto auto"><select id="hkType"><option value="teams">Teams</option><option value="slack">Slack</option><option value="generic">Other</option></select>
        <input id="hkName" placeholder="Name, e.g. Compliance channel"><input id="hkUrl" placeholder="Webhook address, https://...">
        <button class="btn ghost" id="hkTest">Test</button><button class="btn ghost" id="hkAdd">Add</button></div>
      <p class="kw-help" style="margin-top:8px">Teams: in the channel, add the "Post to a channel when a webhook request is received" workflow and paste its address. Slack: create an Incoming Webhook.</p>
      <button class="btn" id="cfSave" style="margin-top:12px" ${T.dirtyCfg ? '' : 'disabled'}>Save recipients and channels</button></div></div>

    <div class="panel"><h3>Notification log <small>Last ${T.log.length} messages</small></h3><div class="table-wrap"><table><thead><tr><th>When</th><th>Channel</th><th>Type</th><th>To</th><th>Subject</th><th>Result</th></tr></thead><tbody>
      ${T.log.map(l => `<tr><td style="white-space:nowrap">${istTime(l.at)}</td><td>${esc(l.channel)}</td><td>${esc(l.category || '')}</td><td>${esc((l.to || []).join(', '))}</td>
        <td>${esc(l.subject || '')}</td><td><span class="health st-${l.ok ? 'ok' : 'error'}"><i></i>${esc(l.message || '')}</span></td></tr>`).join('') || '<tr><td colspan="6" style="color:var(--muted)">Nothing sent yet.</td></tr>'}</tbody></table></div></div>`;

  const md_ = () => { T.dirtyMail = true; renderLens(true); };
  const dc = () => { T.dirtyCfg = true; renderLens(true); };
  const bindVal = (id, key, num) => { const el = $('#' + id); if (el) el.onchange = () => { M[key] = num ? +el.value : el.value; md_(); }; };
  $('#emOn').onchange = e => { M.enabled = e.target.checked; md_(); };
  $('#emProv').onchange = e => {
    M.provider = e.target.value; const pr = T.em.presets[M.provider];
    if (pr.host) { M.host = pr.host; M.port = pr.port; M.security = pr.security; }
    md_();
  };
  bindVal('emHost', 'host'); bindVal('emPort', 'port', true); bindVal('emSec', 'security'); bindVal('emUser', 'username');
  bindVal('emPass', 'password'); bindVal('emKey', 'api_key'); bindVal('emFromName', 'from_name'); bindVal('emFrom', 'from_email'); bindVal('emReply', 'reply_to');
  $('#emSave').onclick = async () => {
    try {
      const r = await fetch('/api/settings/email', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(M) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      T.em = await r.json(); T.edit = { ...T.em, password: '', api_key: '' }; T.dirtyMail = false; S.sv = null;
      toast(T.em.ready ? 'Email settings saved and ready.' : 'Saved, but some details are still missing.', !T.em.ready); renderLens(true);
    } catch (e) { toast('Could not save: ' + e.message, true); }
  };
  const refreshLog = async () => { try { T.log = await api('/api/notifications?limit=60'); } catch { /* keep old */ } renderLens(true); };
  $('#emTest').onclick = async () => {
    if (T.dirtyMail) { toast('Save the email settings first, then send a test.', true); return; }
    const to = $('#emTestTo').value.trim();
    try { toast((await post('/api/surveillance/test-email', to ? { to } : {})).message); } catch (e) { toast(e.message, true); }
    refreshLog();
  };
  $('#emDiag').onclick = async () => {
    $('#emDiagOut').innerHTML = '<p class="loading-note">Checking from the server...</p>';
    try { T.diag = await api('/api/settings/email/diagnose'); } catch (e) { T.diag = [{ name: 'Check failed', target: '', ok: false, message: e.message }]; }
    renderLens(true);
  };
  $$('[data-rc-rm]', main).forEach(b => b.onclick = () => { CF.recipients.splice(+b.dataset.rcRm, 1); dc(); });
  $('#rcAdd').onkeydown = e => {
    if (e.key !== 'Enter') return;
    const v = e.target.value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { toast('Enter a valid email address.', true); return; }
    if (!CF.recipients.includes(v)) CF.recipients.push(v);
    dc(); $('#rcAdd')?.focus();
  };
  $('#dgOn').onchange = e => { CF.digest_enabled = e.target.checked; dc(); };
  $('#dgTime').onchange = e => { CF.digest_time = e.target.value; dc(); };
  $('#dgNow').onclick = async () => { try { toast((await post('/api/surveillance/digest', {})).message); } catch (e) { toast(e.message, true); } refreshLog(); };
  $$('[data-hk-on]', main).forEach(c => c.onchange = () => { CF.webhooks[+c.dataset.hkOn].enabled = c.checked; dc(); });
  $$('[data-hk-rm]', main).forEach(b => b.onclick = () => { CF.webhooks.splice(+b.dataset.hkRm, 1); dc(); });
  const hk = () => ({ type: $('#hkType').value, name: $('#hkName').value.trim() || $('#hkType').selectedOptions[0].text, url: $('#hkUrl').value.trim(), enabled: true });
  $('#hkTest').onclick = async () => { try { toast((await post('/api/surveillance/test-webhook', hk())).message); } catch (e) { toast(e.message, true); } refreshLog(); };
  $('#hkAdd').onclick = () => { const h = hk(); if (!h.url.startsWith('https://')) { toast('The webhook address must start with https://', true); return; } CF.webhooks.push(h); dc(); };
  $('#cfSave').onclick = async () => {
    try {
      const r = await fetch('/api/surveillance/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(CF) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      T.cfg = JSON.parse(JSON.stringify((await r.json()).config)); T.dirtyCfg = false; S.sv = null;
      toast('Saved.'); renderLens(true);
    } catch (e) { toast('Could not save: ' + e.message, true); }
  };
};

/* Incidents lens ------------------------------------------------------ */
RENDER.incidents = main => S.incidentId ? renderIncident(main) : renderIncidentList(main);

function renderIncidentList(main) {
  const scope = S.svScope || 'all', caseF = S.svCase || 'active';
  main.innerHTML = head('Incidents and reports', 'Every material price movement the monitor caught, and every manual investigation, with its evidence-based report.',
    `<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="filter" data-case="active" aria-pressed="${caseF === 'active'}">Open cases</button>
     <button class="filter" data-case="all" aria-pressed="${caseF === 'all'}">All cases</button><span style="width:12px"></span>
     <button class="filter" data-scope="all" aria-pressed="${scope === 'all'}">All stocks</button>
     <button class="filter" data-scope="this" aria-pressed="${scope === 'this'}">${esc(S.sym)} only</button></div>`) + '<div id="incList"><div class="skeleton" style="height:240px"></div></div>';
  $$('[data-scope]', main).forEach(b => b.onclick = () => { S.svScope = b.dataset.scope; renderLens(); });
  $$('[data-case]', main).forEach(b => b.onclick = () => { S.svCase = b.dataset.case; renderLens(); });
  const load = async () => {
    if (S.lens !== 'incidents' || S.incidentId) return;
    try {
      const all = await api('/api/surveillance/incidents' + (scope === 'this' ? '?symbol=' + enc(S.sym) : ''));
      const list = caseF === 'active' ? all.filter(i => i.case !== 'closed') : all;
      const el = $('#incList');
      if (!el) return;
      el.innerHTML = list.length ? `<div class="panel"><div class="table-wrap" style="max-height:none"><table><thead><tr><th>Detected</th><th>Stock</th><th class="num">Move</th>
        <th class="num">vs index</th><th>Trigger</th><th>Investigation</th><th>Material</th><th>Rumour</th><th>Assessment</th><th>Case</th></tr></thead><tbody>
        ${list.map(i => `<tr class="click-row" data-incident="${esc(i.symbol)}|${esc(i.id)}"><td style="white-space:nowrap">${istTime(i.created)}</td>
          <td><b>${esc(i.symbol)}</b></td><td class="num ${dir(i.trigger.move)}">${pctv(i.trigger.move)}</td><td class="num ${dir(i.trigger.adjusted)}">${pctv(i.trigger.adjusted)}</td>
          <td>${esc(KIND_TRIG[i.kind] || (i.manual ? 'Manual' : 'Automatic'))}</td>
          <td><span class="health st-${i.status === 'complete' ? 'ok' : i.status === 'failed' ? 'error' : 'empty'}"><i></i>${i.status === 'complete' ? 'Report ready' : i.status === 'failed' ? 'Failed' : 'Investigating'}</span></td>
          <td>${i.material == null ? '–' : yesNo(i.material)}</td><td>${i.rumour == null ? '–' : yesNo(i.rumour)}</td><td>${esc(i.category || '')}</td>
          <td>${casePill(i.case)}${i.assignee ? `<div class="kw-help" style="margin:2px 0 0">${esc(i.assignee)}</div>` : ''}</td></tr>`).join('')}
        </tbody></table></div></div>`
        : all.length ? `<div class="empty"><h3>No open cases</h3><p>All incidents are closed. Choose "All cases" to see them.</p></div>`
        : `<div class="empty"><h3>No incidents yet</h3><p>Add stocks on the Monitor and alerts lens. When a stock moves past its threshold, the incident and report appear here.
           You can also run a manual investigation from there.</p><button class="btn" data-go="monitor">Open Monitor and alerts</button></div>`;
      if (list.some(i => i.status === 'investigating')) svPoll(load, 5000);
    } catch (e) { const el = $('#incList'); if (el) el.innerHTML = `<p class="loading-note">${esc(e.message)}</p>`; }
  };
  load();
}

function renderIncident(main) {
  const id = S.incidentId;
  const draw = inc => {
    if (S.lens !== 'incidents' || S.incidentId !== id) return;
    destroyCharts();
    const t = inc.trigger, p = inc.price || {}, a = inc.assessment, done = inc.status === 'complete';
    const evById = Object.fromEntries((inc.evidence || []).map(e => [e.id, e]));
    const steps = `<div class="steps">${inc.steps.map(s => `<div class="step ${s.state}"><i></i><div><b>${esc(s.label)}</b><span>${esc(s.note || (s.state === 'pending' ? 'Waiting' : ''))}</span></div></div>`).join('')}</div>`;
    main.innerHTML = `<button class="link-btn" data-back style="margin-bottom:12px">All incidents</button>` +
      head(`${esc(inc.name)} <span class="${t.move >= 0 ? 'up' : 'down'}">${t.move >= 0 ? '▲' : '▼'} ${Math.abs(t.move).toFixed(2)}%</span>`,
        `${esc(KIND_TRIG[inc.kind] || (inc.manual ? 'Manual investigation' : 'Automatic trigger'))} on ${esc(inc.symbol)} at ${istTime(t.time)}. ${esc(inc.reason || '')}${done ? ` Report ready ${inc.duration_min} min after the trigger.` : ''}`,
        done ? `<div style="display:flex;gap:10px;flex-wrap:wrap"><a class="btn ghost" href="/api/surveillance/incidents/${enc(inc.id)}/report.html" download>Download report</a>
          <button class="btn" id="mailRep">Email report</button></div>` : '') +
      (inc.status === 'failed' ? `<div class="empty" style="margin-bottom:16px"><h3>The investigation failed</h3><p>${esc(inc.error || '')}</p></div>` : '') +
      `<div class="panel"><h3>Progress</h3>${steps}</div>` +
      (done ? `<div class="sv-verdict">
        <div class="vcard ${a.material ? 'neg' : 'pos'}"><span>Material movement</span>${yesNo(a.material)}<small>${esc(a.material_reason)}</small></div>
        <div class="vcard ${a.rumour ? 'neg' : 'pos'}"><span>Potential rumour</span>${yesNo(a.rumour)}<small>${a.rumour_in_mainstream ? 'In mainstream media' : a.rumour ? 'Online or social only' : 'None found in the window'}</small></div>
        <div class="vcard"><span>Assessment</span><b class="vtext">${esc(a.category)}</b><small>${a.market_wide ? 'Index move explains most of it' : ''}</small></div>
        <div class="vcard"><span>Likely driver</span><b class="vtext">${esc(a.driver || 'Not identified')}</b><small>${a.driver ? 'Score ' + a.driver_score.toFixed(2) + ' of 1' : ''}</small></div></div>
        <div class="panel sv-action"><b>Suggested action.</b> ${esc(a.action)}</div>` : '') +
      ((inc.related_triggers || []).length ? `<div class="panel"><h3>Related triggers <small>Joined this incident instead of opening new ones</small></h3>
        ${inc.related_triggers.map(r => `<div class="kw-help" style="margin:0 0 4px"><b>${esc(KIND_TRIG[r.kind] || r.kind)}</b>, ${istTime(r.at)}: ${esc(r.reason)}</div>`).join('')}</div>` : '') +
      caseHtml(inc) +
      kpis([['vs previous close', pctv(t.move), `${nf(t.prev_close || 0, 2)} to ${nf(t.last || 0, 2)}`, dir(t.move)],
        [`${esc(t.benchmark || 'Index')}`, pctv(t.index_move), 'Same day', dir(t.index_move)],
        ['Index-adjusted', pctv(t.adjusted), `Threshold ${t.threshold}%`, dir(t.adjusted)],
        [esc(p.split_label || 'Pre-9:30'), p.pre_930 == null ? '–' : pct(p.pre_930), 'Opening move', dir(p.pre_930)],
        ['After that', p.post_930 == null ? '–' : pct(p.post_930), 'Intraday move', dir(p.post_930)],
        ['Move started', esc(p.onset_local || '–'), 'Exchange time'],
        ['Volume', p.volume_ratio ? p.volume_ratio.toFixed(1) + 'x' : '–', 'vs 20-day average']]) +
      (p.series?.length ? `<div class="panel"><h3>Timeline <small>Price today with the threshold, the start of the move, and each item when it was published (red negative, green positive, grey neutral)</small></h3><div class="chart" id="cInc"></div></div>` : '') +
      (done ? `<div class="panel"><h3>Potential rumours and market information <small>Ranked by how likely each explains the move</small></h3>
        <div class="clusters">${inc.clusters.map((c, n) => `<div class="cluster ${c.is_potential_rumour ? 'rumour' : ''}">
          <div class="cluster-score"><b>${c.score.toFixed(2)}</b><div class="bar-track"><div class="bar-fill" style="width:${c.score * 100}%;background:${c.is_potential_rumour ? C.loss : C.blue}"></div></div><span>#${n + 1}</span></div>
          <div><div class="cluster-title">${esc(c.label)}</div>
            <div class="side-meta"><span class="pill ${c.type === 'unverified_rumour' || c.type === 'social_chatter' ? 'negative' : c.type === 'official_disclosure' ? 'caution' : 'neutral'}">${TYPE_LABEL[c.type] || esc(c.type)}</span>${dirPill(c.direction)}
              <span>First seen ${esc(c.first_time)} via ${esc(c.first_source)}</span><span class="${c.before_onset ? 'down' : ''}">${c.before_onset ? 'Before the move started' : 'After the move started'}</span>
              <span>${c.outlets} outlet${c.outlets > 1 ? 's' : ''}</span><span>${c.tiers.map(x => TIER_LABEL[x]).join(', ')}</span>${c.engagement ? `<span>${c.engagement} engagements</span>` : ''}</div>
            ${c.summary ? `<p class="cluster-sum">${esc(c.summary)}</p>` : ''}
            <ul class="cluster-items">${c.items.slice(0, 5).map(i => evById[i] ? `<li><a href="${esc(safeUrl(evById[i].url))}" target="_blank" rel="noopener">${esc(evById[i].title)}</a> <span>${esc(evById[i].source)}, ${esc(evById[i].time)}</span></li>` : '').join('')}</ul></div></div>`).join('')
          || '<p class="loading-note">No relevant public information found in the trigger window.</p>'}</div></div>
        <div class="panel"><h3>Analyst report <small>${inc.report_ai ? 'Written by ' + esc(S.cfg.label || 'AI') : 'Template report (AI not connected)'}</small></h3><div class="report">${md(inc.report_md)}</div></div>
        <div class="panel"><h3>Evidence trail <small>${inc.evidence.length} items</small></h3><div class="table-wrap"><table><thead><tr><th>Published</th><th class="num">Min vs start</th><th>Source</th><th>Credibility</th>
          <th>Type</th><th>Direction</th><th class="num">Confidence</th><th class="num">Engagement</th><th>Item</th></tr></thead><tbody>
          ${inc.evidence.map(e => `<tr><td style="white-space:nowrap">${esc(e.time)}</td><td class="num ${e.minutes_vs_onset <= 0 ? 'down' : ''}">${e.minutes_vs_onset > 0 ? '+' : ''}${e.minutes_vs_onset}</td>
            <td>${esc(e.source)}</td><td>${TIER_LABEL[e.tier]}</td><td>${TYPE_LABEL[e.type] || esc(e.type)}</td><td>${dirPill(e.direction)}</td><td class="num">${(e.confidence || 0).toFixed(2)}</td>
            <td class="num">${e.engagement || '–'}</td><td class="title-cell"><a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(e.title)}</a></td></tr>`).join('')}</tbody></table></div></div>
        <div class="grid g2"><div class="panel"><h3>Sources checked</h3><div class="health-strip">${(inc.health || []).filter(h => h.status !== 'off').map(h => `<span class="health st-${h.status}" title="${esc(h.note || '')}"><i></i>${esc(h.name)} <b>${h.count}</b></span>`).join('')}</div></div>
        <div class="panel"><h3>Emails</h3>${(inc.emails || []).map(m => `<div class="kw-help" style="margin:0 0 6px"><span class="${m.ok ? 'up' : 'down'}">${m.ok ? 'Sent' : 'Not sent'}</span> ${m.type === 'alert' ? 'Alert' : 'Report'}, ${istTime(m.at)}: ${esc(m.message)}</div>`).join('') || '<p class="kw-help">None yet.</p>'}
          <div class="feed-add" style="grid-template-columns:1fr auto;margin-top:10px"><input id="mailTo" placeholder="Other addresses, comma separated (optional)"><button class="btn ghost" id="mailRep2">Send</button></div></div></div>` : '');
    $('[data-back]').onclick = () => setLens('incidents');
    bindCase(inc, load);
    const send = async to => {
      try { toast((await post(`/api/surveillance/incidents/${enc(inc.id)}/email`, to ? { to } : {})).message); load(); } catch (e) { toast(e.message, true); }
    };
    $('#mailRep')?.addEventListener('click', () => send(null));
    $('#mailRep2')?.addEventListener('click', () => { const to = $('#mailTo').value.split(',').map(x => x.trim()).filter(Boolean); send(to.length ? to : null); });
    if (p.series?.length) {
      const ch = mkChart($('#cInc'), { timeScale: { timeVisible: true, secondsVisible: false } });
      const ls = ch.addLineSeries({ color: C.text, lineWidth: 2, priceLineVisible: false });
      ls.setData(p.series);
      const prev = t.prev_close || p.prev_close;
      if (prev) {
        ls.createPriceLine({ price: prev, color: C.dim, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: 'Prev close' });
        ls.createPriceLine({ price: prev * (1 + t.threshold / 100), color: C.caution, lineStyle: 1, lineWidth: 1, axisLabelVisible: false, title: `+${t.threshold}%` });
        ls.createPriceLine({ price: prev * (1 - t.threshold / 100), color: C.caution, lineStyle: 1, lineWidth: 1, axisLabelVisible: false, title: `-${t.threshold}%` });
      }
      const times = p.series.map(x => x.time);
      const tz = D_TZ(inc.symbol);
      const snap = iso => {
        const s = Math.floor(new Date(iso).getTime() / 1000) + tz;
        if (s < times[0] || s > times[times.length - 1] + 3600) return null;
        let best = times[0];
        for (const x of times) { if (x <= s) best = x; else break; }
        return best;
      };
      const marks = [];
      const onsetT = p.onset ? snap(p.onset) : null;
      if (onsetT) marks.push({ time: onsetT, position: 'belowBar', color: C.caution, shape: 'arrowUp', text: 'Move starts' });
      (inc.evidence || []).forEach(e => {
        const at = snap(e.published);
        if (at) marks.push({ time: at, position: 'aboveBar', color: e.direction === 'negative' ? C.loss : e.direction === 'positive' ? C.gain : C.neutral, shape: 'circle', text: '' });
      });
      marks.sort((x, y) => x.time - y.time);
      ls.setMarkers(marks);
      ch.timeScale().fitContent();
    }
    // keep refreshing until every step (including the report email) has finished
    if (inc.status !== 'failed' && inc.steps.some(s => s.state === 'pending' || s.state === 'running')) svPoll(load, 4000);
  };
  const load = () => api(`/api/surveillance/incidents/${enc(id)}`).then(draw).catch(e => {
    main.innerHTML = `<button class="link-btn" data-back>All incidents</button><div class="empty"><h3>Could not load the incident</h3><p>${esc(e.message)}</p></div>`;
    $('[data-back]').onclick = () => setLens('incidents');
  });
  main.innerHTML = '<div class="skeleton" style="height:400px"></div>';
  load();
}
function caseHtml(inc) {
  const wf = inc.workflow || { status: 'open', assignee: '', closure: '' };
  return `<div class="panel case-panel"><div class="lens-head" style="margin-bottom:12px"><div><h3 style="margin:0">Case ${casePill(wf.status)}</h3>
      <p class="kw-help" style="margin:4px 0 0">Track the review: who owns it, what was done, and how it closed. Every change is recorded below.</p></div></div>
    <div class="case-grid"><div>
      <div class="form-grid">
        <label>Status<select id="csStatus">${Object.entries(CASE_LABEL).map(([k, v]) => `<option value="${k}" ${wf.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
        <label>Assigned to<input id="csAssignee" value="${esc(wf.assignee || '')}" placeholder="Name or email"></label>
        <label class="wide">Closure summary<textarea id="csClosure" rows="3" placeholder="How the case was resolved, e.g. company denied the report through an exchange filing">${esc(wf.closure || '')}</textarea></label>
      </div>
      <button class="btn" id="csSave" style="margin-top:10px">Update case</button>
      <h3 style="margin-top:18px">Notes</h3>
      <div class="notes">${(inc.notes || []).map(n => `<div class="note"><div class="kw-help" style="margin:0"><b>${esc(n.by)}</b>, ${istTime(n.at)}</div><div>${esc(n.text)}</div></div>`).join('') || '<p class="kw-help">No notes yet.</p>'}</div>
      <div class="feed-add" style="grid-template-columns:1fr auto"><input id="csNote" placeholder="Add a note, e.g. called the company secretary at 12:10"><button class="btn ghost" id="csAddNote">Add note</button></div></div>
    <div><h3>Audit trail</h3><div class="audit">${(inc.audit || []).slice().reverse().map(a => `<div><span>${istTime(a.at)}</span><b>${esc(a.by)}</b><p>${esc(a.event)}</p></div>`).join('')}</div></div></div></div>`;
}
function bindCase(inc, reload) {
  const save = async body => {
    try {
      const r = await fetch(`/api/surveillance/incidents/${enc(inc.id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      toast('Case updated.'); reload();
    } catch (e) { toast('Could not update: ' + e.message, true); }
  };
  $('#csSave')?.addEventListener('click', () => save({ status: $('#csStatus').value, assignee: $('#csAssignee').value, closure: $('#csClosure').value }));
  $('#csAddNote')?.addEventListener('click', () => { const t = $('#csNote').value.trim(); if (t) save({ note: t }); });
  $('#csNote')?.addEventListener('keydown', e => { if (e.key === 'Enter') $('#csAddNote').click(); });
}
// Exchange wall-clock offset (seconds) used by the intraday chart; India is UTC+5:30.
const D_TZ = sym => (/\.(NS|BO)$/i.test(sym) ? 19800 : -4 * 3600);

/* ------------------------------------------------------------------ */
/* Data sources dialog                                                 */
/* ------------------------------------------------------------------ */
const SOURCES_HTML = () => `
<div class="flow"><div><b>1. Prices</b>Yahoo Finance via yfinance</div><div><b>2. News</b>11 sources, see below</div>
<div><b>3. Maths</b>Event study in Python</div><div><b>4. AI</b>${S.cfg.ai_enabled ? esc(S.cfg.label) : 'Not connected'}</div></div>
<h3>Live price</h3>
<p>The server uses the open-source <b>yfinance</b> library, which reads Yahoo Finance's public quote data. Every few seconds it pulls the latest price
and today's 1-minute bars, and streams them to this page over a live connection (Server-Sent Events), so the price updates without reloading.
Yahoo's data can trail the exchange by a few seconds to a few minutes. Daily and hourly history for the stock and its index
(Nifty 50 for NSE, Sensex for BSE, S&amp;P 500 for US) come from the same source.</p>
<h3>News, filings, forums and video</h3>
<p>Each company has a keyword profile: names, leadership, brands, competitors and materials (edit it on the Keywords and sources lens).
The server searches all sources in parallel with those keywords: Google News and Bing News, live RSS feeds from major publishers
(Economic Times, Moneycontrol, Mint, Business Standard, BusinessLine and more), GDELT global media monitoring, official NSE announcements,
Reddit, and optionally YouTube, NewsAPI and Finnhub. The same story from several outlets is merged and counted, keeping the earliest time.
The feed refreshes every 2 minutes. Forum posts and videos appear in the feed; the price-reaction analysis uses news and filings.</p>
<h3>The post-mortem maths</h3>
<p>Each headline is matched to the first trading session that could react to it. The app measures the stock's move from the previous close over
the first hour, the reaction day, 2 days and 1 week, then subtracts the index move over the same window. What remains is the move the market
does not explain. It also compares volume with the 20-day average. All of this is plain Python and pandas, not AI, so the numbers are exact.</p>
<h3>AI</h3>
<p>${S.cfg.ai_enabled ? `Connected to <b>${esc(S.cfg.label)}</b>.` : 'Not connected yet.'}
The server sends the AI only headlines, their measured reactions and a price summary. It never sends your keys to the browser. The AI does three jobs:
scores each headline's tone, type, importance and relevance; writes the post-mortem report; and answers your questions.
Keys are stored securely on the server.</p>
<h3>Limits</h3>
<p>Correlation is not causation: when several headlines land on one day, the move is shared. Free data can be delayed or occasionally missing.
For research and learning, not investment advice.</p>`;

/* ------------------------------------------------------------------ */
/* Init                                                                */
/* ------------------------------------------------------------------ */
async function init() {
  try { S.cfg = await api('/api/config'); } catch { S.cfg = { ai_enabled: false }; }
  const st = $('#aiStatusLanding');
  st.textContent = S.cfg.ai_enabled ? `AI connected: ${S.cfg.label}` : 'AI not connected.';
  st.classList.toggle('on', !!S.cfg.ai_enabled);
  initLanding();

  $('#rail').addEventListener('click', e => { const b = e.target.closest('[data-lens]'); if (b) setLens(b.dataset.lens); });
  $('#lens').addEventListener('click', e => {
    const inc = e.target.closest('[data-incident]');
    if (inc) {
      const [sym, id] = inc.dataset.incident.split('|');
      if (sym !== S.sym) openStock(sym, 'incidents', id); else setLens('incidents', id);
      return;
    }
    const t = e.target.closest('[data-go]');
    if (t) setLens(t.dataset.go);
  });
  loadLandingSurveillance();
  $('#homeBtn').onclick = goHome;
  $('#selPeriod').onchange = e => { S.period = e.target.value; S.data = null; renderLens(); refreshAnalysis(); };
  $('#selDays').onchange = e => { S.days = +e.target.value; S.data = null; renderLens(); refreshAnalysis(); };
  $('#selInterval').onchange = e => { S.interval = +e.target.value; startStream(); if (S.lens === 'live') renderLens(); };

  const dlg = $('#sources');
  $$('[data-open="sources"]').forEach(b => b.onclick = () => { $('#sourcesBody').innerHTML = SOURCES_HTML(); dlg.showModal(); });
  $('[data-close]', dlg).onclick = () => dlg.close();
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });

  const m = location.hash.match(/^#([^/]+)(?:\/(\w+))?(?:\/([\w.-]+))?/);
  if (m) openStock(decodeURIComponent(m[1]), m[2], m[3]);
  else $('#searchInput').focus();
}

if (!window.LightweightCharts) {
  document.body.innerHTML = '<p style="padding:40px;font-family:sans-serif">Charts could not load. This app needs an internet connection for its chart library.</p>';
} else {
  init();
}
