/* Energia Casa — dashboard */
'use strict';

const $ = (s) => document.querySelector(s);
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

// ------------------------------------------------------------------ palette (validated reference palette)
const PAL = {
  light: {
    solare: '#eda100', casa: '#eb6834', rete_prelievo: '#2a78d6', rete_immissione: '#6250d6',
    batteria_scarica: '#1baf7a', batteria_carica: '#e87ba4', other: '#a8a7a0',
    cat: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
    seq: ['#f0efec', '#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'],
  },
  dark: {
    solare: '#c98500', casa: '#d95926', rete_prelievo: '#3987e5', rete_immissione: '#9085e9',
    batteria_scarica: '#199e70', batteria_carica: '#d55181', other: '#6b6a64',
    cat: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#0ca30c', '#9085e9', '#e66767'],
    seq: ['#232321', '#104281', '#1c5cab', '#3987e5', '#6da7ec', '#b7d3f6'],
  },
};
const CAT_LABEL = {
  solare: 'Solare', casa: 'Consumo casa', rete_prelievo: 'Prelievo rete', rete_immissione: 'Immissione rete',
  batteria_scarica: 'Scarica batteria', batteria_carica: 'Carica batteria',
};
const CATS = ['solare', 'rete_prelievo', 'rete_immissione', 'batteria_scarica', 'batteria_carica'];

// ------------------------------------------------------------------ formatting
const nf = (d) => new Intl.NumberFormat('it-IT', { minimumFractionDigits: d, maximumFractionDigits: d });
const NF = [nf(0), nf(1), nf(2)];
const num = (v) => { const a = Math.abs(v); return (a >= 100 ? NF[0] : a >= 10 ? NF[1] : NF[2]).format(v); };
const kwh = (v) => `${num(v)} kWh`;
const pct = (v) => (Number.isFinite(v) ? `${NF[0].format(v * 100)}%` : '–');
const eur = (v) => new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: v >= 100 ? 0 : 2 }).format(v);
const sum = (a) => a.reduce((x, y) => x + y, 0);
const safeDiv = (a, b) => (b > 1e-9 ? a / b : NaN);

// ------------------------------------------------------------------ dates (local)
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const today = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); };
const daysBetween = (a, b) => Math.round((b - a) / 86400000);
const fmtDate = (d, o) => new Intl.DateTimeFormat('it-IT', o).format(d);

// ------------------------------------------------------------------ state
const st = {
  mode: store.get('mode', 'month'),
  anchor: today(),
  cStart: addDays(today(), -29),
  cEnd: today(),
  gran: store.get('gran', 'auto'),
  view: ['#auto', '#casa'].includes(location.hash) ? location.hash.slice(1) : store.get('view', 'casa'),
};
let META = null, DATA = null, MONTHLY = null, CUR = null, AUTO = null;
const charts = {};

function range(mode = st.mode, a = st.anchor) {
  switch (mode) {
    case 'day': return [a, a];
    case 'week': { const m = addDays(a, -((a.getDay() + 6) % 7)); return [m, addDays(m, 6)]; }
    case 'month': return [new Date(a.getFullYear(), a.getMonth(), 1), new Date(a.getFullYear(), a.getMonth() + 1, 0)];
    case 'year': return [new Date(a.getFullYear(), 0, 1), new Date(a.getFullYear(), 11, 31)];
    case 'all': return [META?.first ? parse(META.first.slice(0, 10)) : addDays(today(), -365), today()];
    default: return [st.cStart, st.cEnd];
  }
}
function shift(dir) {
  const a = st.anchor;
  if (st.mode === 'day') st.anchor = addDays(a, dir);
  else if (st.mode === 'week') st.anchor = addDays(a, 7 * dir);
  else if (st.mode === 'month') st.anchor = new Date(a.getFullYear(), a.getMonth() + dir, 1);
  else if (st.mode === 'year') st.anchor = new Date(a.getFullYear() + dir, 0, 1);
  else if (st.mode === 'custom') {
    const len = daysBetween(st.cStart, st.cEnd) + 1;
    st.cStart = addDays(st.cStart, len * dir); st.cEnd = addDays(st.cEnd, len * dir);
  }
}
function prevRange([s, e]) {
  let ps, pe;
  if (st.mode === 'all') return null;
  if (st.mode === 'month') { ps = new Date(s.getFullYear(), s.getMonth() - 1, 1); pe = new Date(s.getFullYear(), s.getMonth(), 0); }
  else if (st.mode === 'year') { ps = new Date(s.getFullYear() - 1, 0, 1); pe = new Date(s.getFullYear() - 1, 11, 31); }
  else { const len = daysBetween(s, e) + 1; ps = addDays(s, -len); pe = addDays(e, -len); }
  // periodo in corso: confronta solo la parte già trascorsa
  const t = today();
  if (e >= t && s <= t) { const elapsed = daysBetween(s, t); if (addDays(ps, elapsed) < pe) pe = addDays(ps, elapsed); }
  return [ps, pe];
}
function autoGran([s, e]) {
  const d = daysBetween(s, e) + 1;
  if (st.mode === 'day' || d <= 3) return 'hour';
  if (st.mode === 'week' || st.mode === 'month' || d <= 62) return 'day';
  if (d <= 190) return 'week';
  if (d <= 1100) return 'month';
  return 'year';
}
function periodLabel([s, e]) {
  switch (st.mode) {
    case 'day': return fmtDate(s, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    case 'month': return fmtDate(s, { month: 'long', year: 'numeric' });
    case 'year': return String(s.getFullYear());
    default: return `${fmtDate(s, { day: 'numeric', month: 'short', year: 'numeric' })} – ${fmtDate(e, { day: 'numeric', month: 'short', year: 'numeric' })}`;
  }
}
function bucketLabel(k, gran, short = false) {
  if (gran === 'hour') {
    const [d, h] = k.split('T');
    return CUR.singleDay ? h : `${fmtDate(parse(d), { day: '2-digit', month: '2-digit' })} ${h}`;
  }
  if (gran === 'day') return fmtDate(parse(k), short ? { day: 'numeric', month: 'short' } : { weekday: 'short', day: 'numeric', month: 'short' });
  if (gran === 'week') return `sett. ${fmtDate(parse(k), { day: 'numeric', month: 'short', year: short ? undefined : '2-digit' })}`;
  if (gran === 'month') return fmtDate(parse(`${k}-01`), { month: 'short', year: 'numeric' });
  return k;
}

// ------------------------------------------------------------------ theme
function themeMode() {
  const forced = document.documentElement.dataset.theme;
  if (forced) return forced;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return { surface: v('--surface'), surface2: v('--surface-2'), border: v('--border'), text: v('--text'), text2: v('--text-2'), text3: v('--text-3'), ...PAL[themeMode()] };
}

// ------------------------------------------------------------------ data shaping
function sensorsBy(cat) { return Object.entries(META.sensors).filter(([, m]) => m.categoria === cat).map(([id]) => id); }

function deviceColors(T) {
  // colore fisso per dispositivo: i 7 maggiori su tutto lo storico, gli altri confluiscono in "Altri"
  const devs = sensorsBy('dispositivo').sort((a, b) => (META.sensors[b].total_kwh || 0) - (META.sensors[a].total_kwh || 0));
  const map = {};
  devs.forEach((id, i) => { map[id] = i < 7 ? T.cat[i] : null; });
  return map;
}

function shape(data) {
  const n = data.buckets.length;
  const zero = () => new Array(n).fill(0);
  const c = {}; CATS.forEach((k) => { c[k] = zero(); });
  for (const [id, vals] of Object.entries(data.series)) {
    const cat = META.sensors[id]?.categoria;
    if (c[cat]) vals.forEach((v, i) => { c[cat][i] += v; });
  }
  c.casa = c.solare.map((s, i) => Math.max(0, s + c.rete_prelievo[i] + c.batteria_scarica[i] - c.rete_immissione[i] - c.batteria_carica[i]));
  return c;
}

function flows(sol, prel, imm, bsc, bca) {
  const s2exp = Math.min(imm, sol);
  const b2exp = Math.max(0, imm - s2exp);
  const s2bat = Math.min(bca, sol - s2exp);
  const g2bat = Math.max(0, bca - s2bat);
  return {
    s2home: Math.max(0, sol - s2exp - s2bat), s2bat, s2exp,
    g2home: Math.max(0, prel - g2bat), g2bat,
    b2home: Math.max(0, bsc - b2exp), b2exp,
  };
}

function hourlyCasa(h) {
  const g = (k) => h.cats[k] || h.dates.map(() => new Array(24).fill(0));
  const [sol, prel, imm, bsc, bca] = CATS.map(g);
  return h.dates.map((_, d) => Array.from({ length: 24 }, (_, hh) =>
    Math.max(0, sol[d][hh] + prel[d][hh] + bsc[d][hh] - imm[d][hh] - bca[d][hh])));
}

// ------------------------------------------------------------------ chart helpers
function chart(id) {
  if (!charts[id]) {
    charts[id] = echarts.init(document.getElementById(id), null, { renderer: 'canvas' });
    new ResizeObserver(() => charts[id].resize()).observe(document.getElementById(id));
  }
  return charts[id];
}
function base(T, { zoom = false, legend = true } = {}) {
  return {
    backgroundColor: 'transparent',
    animationDuration: 500,
    textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: T.text2 },
    grid: { left: 4, right: 12, top: legend ? 44 : 16, bottom: zoom ? 44 : 6, containLabel: true },
    legend: legend ? { type: 'scroll', top: 0, left: 0, right: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10, itemGap: 14, textStyle: { color: T.text2, fontSize: 12 } } : undefined,
    tooltip: {
      trigger: 'axis', confine: true, backgroundColor: T.surface, borderColor: T.border, borderWidth: 1,
      textStyle: { color: T.text, fontSize: 12 }, extraCssText: 'border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.15);',
      axisPointer: { type: 'shadow', shadowStyle: { color: T.text3, opacity: 0.08 }, lineStyle: { color: T.text3 } },
    },
    dataZoom: zoom ? [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6, borderColor: T.border, fillerColor: 'rgba(128,128,128,.12)', textStyle: { color: T.text3 }, handleStyle: { color: T.surface, borderColor: T.text3 }, dataBackground: { lineStyle: { color: T.border }, areaStyle: { color: T.surface2 } } }] : undefined,
  };
}
function catAxis(T, labels) {
  return { type: 'category', data: labels, axisLine: { lineStyle: { color: T.border } }, axisTick: { show: false }, axisLabel: { color: T.text3, fontSize: 11, hideOverlap: true } };
}
function valAxis(T, fmt = num) {
  return { type: 'value', splitLine: { lineStyle: { color: T.border, type: [4, 4] } }, axisLabel: { color: T.text3, fontSize: 11, formatter: fmt } };
}
function tipFormatter(unitFmt, { total = false, abs = false } = {}) {
  return (ps) => {
    const rows = ps.filter((p) => p.value != null && Math.abs(p.value) > 1e-6);
    let html = `<div style="font-weight:600;margin-bottom:4px">${ps[0].axisValueLabel}</div>`;
    for (const p of rows) {
      html += `<div style="display:flex;gap:10px;justify-content:space-between;align-items:center"><span>${p.marker}${p.seriesName}</span><b>${unitFmt(abs ? Math.abs(p.value) : p.value)}</b></div>`;
    }
    if (total) {
      const t = sum(rows.filter((p) => p.value > 0).map((p) => p.value));
      html += `<div style="display:flex;justify-content:space-between;margin-top:4px;padding-top:4px;border-top:1px solid rgba(128,128,128,.3)"><span>Consumo casa</span><b>${unitFmt(t)}</b></div>`;
    }
    return html;
  };
}
const barSeries = (T, name, data, color, stack, opts = {}) => ({
  name, type: 'bar', stack, data, barMaxWidth: 26,
  itemStyle: { color, borderColor: T.surface, borderWidth: stack ? 1 : 0, borderRadius: stack ? 0 : [4, 4, 0, 0] },
  emphasis: { focus: 'series' }, ...opts,
});
const lineSeries = (name, data, color, opts = {}) => ({
  name, type: 'line', data, smooth: 0.25, symbol: 'circle', symbolSize: 7, showSymbol: data.length <= 31,
  lineStyle: { width: 2, color }, itemStyle: { color }, emphasis: { focus: 'series' }, ...opts,
});

// ------------------------------------------------------------------ renderers
function renderKpis(T, c) {
  const tot = Object.fromEntries(Object.entries(c).map(([k, v]) => [k, sum(v)]));
  const price = META.price || 0;
  const autosuff = safeDiv(tot.casa - tot.rete_prelievo, tot.casa);
  const autocons = safeDiv(tot.solare - tot.rete_immissione, tot.solare);

  let prev = null;
  if (DATA.prev_totals && !(st.mode === 'day' && iso(CUR.range[0]) === iso(today()))) {
    prev = Object.fromEntries(CATS.map((k) => [k, 0]));
    for (const [id, v] of Object.entries(DATA.prev_totals)) {
      const cat = META.sensors[id]?.categoria;
      if (cat in prev) prev[cat] += v;
    }
    prev.casa = Math.max(0, prev.solare + prev.rete_prelievo + prev.batteria_scarica - prev.rete_immissione - prev.batteria_carica);
  }
  const pv = prev && {
    casa: prev.casa, solare: prev.solare, rete_prelievo: prev.rete_prelievo, rete_immissione: prev.rete_immissione,
    autosuff: safeDiv(prev.casa - prev.rete_prelievo, prev.casa), autocons: safeDiv(prev.solare - prev.rete_immissione, prev.solare),
    costo: prev.rete_prelievo * price, risp: (prev.casa - prev.rete_prelievo) * price,
  };

  const items = [
    { k: 'casa', lbl: 'Consumo casa', v: tot.casa, f: kwh, color: T.casa, good: -1 },
    { k: 'solare', lbl: 'Produzione solare', v: tot.solare, f: kwh, color: T.solare, good: 1 },
    { k: 'rete_prelievo', lbl: 'Prelievo dalla rete', v: tot.rete_prelievo, f: kwh, color: T.rete_prelievo, good: -1 },
    { k: 'rete_immissione', lbl: 'Immessa in rete', v: tot.rete_immissione, f: kwh, color: T.rete_immissione, good: 1 },
    { k: 'autosuff', lbl: 'Autosufficienza', v: autosuff, f: pct, color: T.batteria_scarica, good: 1, pp: true, hint: 'Quota del consumo coperta da solare e batteria' },
    { k: 'autocons', lbl: 'Autoconsumo', v: autocons, f: pct, color: T.solare, good: 1, pp: true, hint: 'Quota della produzione solare usata in casa' },
    { k: 'costo', lbl: 'Costo energia da rete', v: tot.rete_prelievo * price, f: eur, color: T.rete_prelievo, good: -1, hint: `Prezzo ${eur(price)}/kWh da HA` },
    { k: 'risp', lbl: 'Risparmio stimato', v: (tot.casa - tot.rete_prelievo) * price, f: eur, color: T.batteria_scarica, good: 1, hint: 'Energia non prelevata × prezzo' },
  ];
  $('#kpis').innerHTML = kpiTiles(items, pv);
}

function kpiTiles(items, pv) {
  return items.map((it) => {
    let delta = '<div class="delta">&nbsp;</div>';
    if (pv && Number.isFinite(it.v) && Number.isFinite(pv[it.k])) {
      const d = it.pp ? (it.v - pv[it.k]) * 100 : safeDiv(it.v - pv[it.k], Math.abs(pv[it.k])) * 100;
      if (Number.isFinite(d)) {
        const cls = Math.abs(d) < 0.5 || !it.good ? '' : (d > 0) === (it.good > 0) ? 'up' : 'down';
        const arrow = Math.abs(d) < 0.5 ? '＝' : d > 0 ? '▲' : '▼';
        delta = `<div class="delta"><b class="${cls}">${arrow} ${NF[0].format(Math.abs(d))}${it.pp ? ' pp' : '%'}</b> vs ${it.f(pv[it.k])}</div>`;
      }
    }
    const [val, unit] = it.unit ? [it.f(it.v), it.unit] : it.f === kwh ? [num(it.v), 'kWh'] : [it.f(it.v), ''];
    return `<div class="kpi" title="${it.hint || ''}"><div class="lbl"><span class="dot" style="background:${it.color}"></span>${it.lbl}</div>
      <div class="val">${val}${unit ? `<small>${unit}</small>` : ''}</div>${delta}</div>`;
  }).join('');
}

function renderBalance(T, c, labels) {
  const n = labels.length;
  const f = Array.from({ length: n }, (_, i) => flows(c.solare[i], c.rete_prelievo[i], c.rete_immissione[i], c.batteria_scarica[i], c.batteria_carica[i]));
  const r2 = (v) => Math.round(v * 1000) / 1000;
  chart('chBalance').setOption({
    ...base(T, { zoom: n > 62 }),
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(kwh, { total: true, abs: true }) },
    xAxis: catAxis(T, labels),
    yAxis: valAxis(T, (v) => num(Math.abs(v))),
    series: [
      barSeries(T, 'Solare diretto', f.map((x) => r2(x.s2home)), T.solare, 'in'),
      barSeries(T, 'Da batteria', f.map((x) => r2(x.b2home)), T.batteria_scarica, 'in'),
      barSeries(T, 'Da rete', f.map((x) => r2(x.g2home + x.g2bat)), T.rete_prelievo, 'in'),
      barSeries(T, 'Carica batteria', f.map((x) => -r2(x.s2bat + x.g2bat)), T.batteria_carica, 'out'),
      barSeries(T, 'Immissione', c.rete_immissione.map((v) => -r2(v)), T.rete_immissione, 'out'),
    ],
  }, true);
}

function renderSankey(T, c, devTot, devColor) {
  // flussi calcolati ora per ora quando disponibile (più preciso)
  const acc = { s2home: 0, s2bat: 0, s2exp: 0, g2home: 0, g2bat: 0, b2home: 0, b2exp: 0 };
  const add = (f) => { for (const k in acc) acc[k] += f[k]; };
  const h = DATA.hourly;
  if (h) {
    const g = (k) => h.cats[k];
    h.dates.forEach((_, d) => { for (let hh = 0; hh < 24; hh++) add(flows(...CATS.map((k) => g(k)?.[d][hh] || 0))); });
  } else {
    c.solare.forEach((_, i) => add(flows(...CATS.map((k) => c[k][i]))));
  }
  const casa = acc.s2home + acc.g2home + acc.b2home;
  const nodes = [
    { name: 'Solare', itemStyle: { color: T.solare } },
    { name: 'Rete', itemStyle: { color: T.rete_prelievo } },
    { name: 'Batteria', itemStyle: { color: T.batteria_scarica } },
    { name: 'Casa', itemStyle: { color: T.casa } },
    { name: 'Immissione', itemStyle: { color: T.rete_immissione } },
  ];
  const links = [
    ['Solare', 'Casa', acc.s2home], ['Solare', 'Batteria', acc.s2bat], ['Solare', 'Immissione', acc.s2exp],
    ['Rete', 'Casa', acc.g2home], ['Rete', 'Batteria', acc.g2bat],
    ['Batteria', 'Casa', acc.b2home], ['Batteria', 'Immissione', acc.b2exp],
  ];
  const devEntries = Object.entries(devTot).sort((a, b) => b[1] - a[1]);
  let other = 0;
  for (const [id, v] of devEntries) {
    if (v <= 0.01) continue;
    if (devColor[id]) {
      const name = META.sensors[id].label;
      nodes.push({ name, itemStyle: { color: devColor[id] } });
      links.push(['Casa', name, v]);
    } else other += v;
  }
  if (other > 0.01) { nodes.push({ name: 'Altri dispositivi', itemStyle: { color: T.other } }); links.push(['Casa', 'Altri dispositivi', other]); }
  const unmon = casa - sum(Object.values(devTot));
  if (unmon > 0.01) { nodes.push({ name: 'Non monitorato', itemStyle: { color: T.other, opacity: 0.6 } }); links.push(['Casa', 'Non monitorato', unmon]); }

  chart('chSankey').setOption({
    ...base(T, { legend: false }),
    tooltip: { ...base(T).tooltip, trigger: 'item', formatter: (p) => p.dataType === 'edge' ? `${p.data.source} → ${p.data.target}<br><b>${kwh(p.data.value)}</b>` : `${p.name}<br><b>${kwh(p.value)}</b>` },
    series: [{
      type: 'sankey', left: 4, right: 175, top: 8, bottom: 8, nodeWidth: 12, nodeGap: 10, nodeAlign: 'left',
      draggable: false, emphasis: { focus: 'adjacency' },
      data: nodes, links: links.filter((l) => l[2] > 0.01).map(([source, target, value]) => ({ source, target, value })),
      lineStyle: { color: 'gradient', opacity: 0.35, curveness: 0.5 },
      itemStyle: { borderWidth: 0, borderRadius: 3 },
      label: { color: T.text, fontSize: 12, width: 170, overflow: 'truncate', formatter: (p) => `{n|${p.name}}  {v|${num(p.value)}}`, rich: { n: { color: T.text }, v: { color: T.text3 } } },
    }],
  }, true);
}

function renderDevices(T, c, devTot, devColor) {
  const casa = sum(c.casa);
  const rows = Object.entries(devTot).map(([id, v]) => ({ name: META.sensors[id].label, v, color: devColor[id] || T.other }));
  rows.sort((a, b) => b.v - a.v);
  const unmon = casa - sum(rows.map((r) => r.v));
  if (unmon > 0) rows.push({ name: 'Non monitorato', v: unmon, color: T.other, dim: true });
  rows.reverse();
  chart('chDevices').setOption({
    ...base(T, { legend: false }),
    grid: { left: 4, right: 110, top: 8, bottom: 6, containLabel: true },
    tooltip: { ...base(T).tooltip, trigger: 'item', axisPointer: undefined, formatter: (p) => `${p.name}<br><b>${kwh(p.value)}</b> · ${pct(safeDiv(p.value, casa))} della casa` },
    xAxis: { ...valAxis(T), splitLine: { show: false }, axisLabel: { show: false } },
    yAxis: { type: 'category', data: rows.map((r) => r.name), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: T.text2, fontSize: 12, width: 150, overflow: 'truncate' } },
    series: [{
      type: 'bar', barMaxWidth: 18, data: rows.map((r) => ({ value: r.v, itemStyle: { color: r.color, opacity: r.dim ? 0.55 : 1, borderRadius: [0, 4, 4, 0] } })),
      label: { show: true, position: 'right', color: T.text2, fontSize: 11.5, formatter: (p) => `${num(p.value)} kWh · ${pct(safeDiv(p.value, casa))}` },
    }],
  }, true);
}

function renderDevTime(T, labels, devColor) {
  const n = labels.length;
  const series = [];
  const other = new Array(n).fill(0);
  const ids = sensorsBy('dispositivo').sort((a, b) => (META.sensors[b].total_kwh || 0) - (META.sensors[a].total_kwh || 0));
  for (const id of ids) {
    const vals = DATA.series[id] || new Array(n).fill(0);
    if (devColor[id]) series.push(barSeries(T, META.sensors[id].label, vals.map((v) => +v.toFixed(3)), devColor[id], 'd'));
    else vals.forEach((v, i) => { other[i] += v; });
  }
  if (sum(other) > 0) series.push(barSeries(T, 'Altri dispositivi', other.map((v) => +v.toFixed(3)), T.other, 'd'));
  chart('chDevTime').setOption({
    ...base(T, { zoom: n > 62 }),
    legend: { ...base(T).legend, type: 'scroll' },
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(kwh) },
    xAxis: catAxis(T, labels), yAxis: valAxis(T), series,
  }, true);
}

function renderSolar(T, labels) {
  const ids = sensorsBy('solare');
  const n = labels.length;
  chart('chSolar').setOption({
    ...base(T, { zoom: n > 62 }),
    legend: { ...base(T).legend, type: 'scroll' },
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(kwh) },
    xAxis: catAxis(T, labels), yAxis: valAxis(T),
    series: ids.map((id, i) => barSeries(T, META.sensors[id].label, (DATA.series[id] || new Array(n).fill(0)).map((v) => +v.toFixed(3)), T.cat[i % 8], 's')),
  }, true);
}

function renderRatio(T, c, labels) {
  const as = c.casa.map((v, i) => (v > 0.01 ? +(100 * Math.max(0, 1 - c.rete_prelievo[i] / v)).toFixed(1) : null));
  const ac = c.solare.map((v, i) => (v > 0.01 ? +(100 * Math.max(0, 1 - c.rete_immissione[i] / v)).toFixed(1) : null));
  chart('chRatio').setOption({
    ...base(T, { zoom: labels.length > 62 }),
    tooltip: { ...base(T).tooltip, axisPointer: { type: 'line', lineStyle: { color: T.text3 } }, formatter: tipFormatter((v) => `${NF[0].format(v)}%`) },
    xAxis: { ...catAxis(T, labels), boundaryGap: false },
    yAxis: { ...valAxis(T, (v) => `${v}%`), min: 0, max: 100 },
    series: [
      lineSeries('Autosufficienza', as, T.batteria_scarica, { connectNulls: false, areaStyle: { color: T.batteria_scarica, opacity: 0.08 } }),
      lineSeries('Autoconsumo', ac, T.solare, { connectNulls: false }),
    ],
  }, true);
}

function renderProfile(T) {
  const h = DATA.hourly;
  const el = chart('chProfile');
  if (!h) { el.clear(); el.setOption({ title: { text: 'Periodo troppo lungo per il profilo orario', left: 'center', top: 'middle', textStyle: { color: T.text3, fontSize: 13, fontWeight: 500 } } }); return; }
  const casa = hourlyCasa(h);
  const days = h.dates.length;
  const avg = (grid) => Array.from({ length: 24 }, (_, hh) => +(sum(grid.map((r) => r[hh])) / days).toFixed(3));
  const g = (k) => h.cats[k] || h.dates.map(() => new Array(24).fill(0));
  const hours = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`);
  el.setOption({
    ...base(T),
    tooltip: { ...base(T).tooltip, axisPointer: { type: 'line', lineStyle: { color: T.text3 } }, formatter: tipFormatter(kwh) },
    xAxis: { ...catAxis(T, hours), boundaryGap: false },
    yAxis: valAxis(T),
    series: [
      lineSeries('Consumo casa', avg(casa), T.casa, { showSymbol: false, areaStyle: { color: T.casa, opacity: 0.08 } }),
      lineSeries('Solare', avg(g('solare')), T.solare, { showSymbol: false, areaStyle: { color: T.solare, opacity: 0.1 } }),
      lineSeries('Prelievo rete', avg(g('rete_prelievo')), T.rete_prelievo, { showSymbol: false }),
      lineSeries('Immissione', avg(g('rete_immissione')), T.rete_immissione, { showSymbol: false }),
    ],
  }, true);
}

function renderBattery(T, c, labels) {
  const n = labels.length;
  chart('chBattery').setOption({
    ...base(T, { zoom: n > 62 }),
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(kwh, { abs: true }) },
    xAxis: catAxis(T, labels), yAxis: valAxis(T, (v) => num(Math.abs(v))),
    series: [
      barSeries(T, 'Scarica', c.batteria_scarica.map((v) => +v.toFixed(3)), T.batteria_scarica, 'b', { itemStyle: { color: T.batteria_scarica, borderRadius: [4, 4, 0, 0] } }),
      barSeries(T, 'Carica', c.batteria_carica.map((v) => -(+v.toFixed(3))), T.batteria_carica, 'b', { itemStyle: { color: T.batteria_carica, borderRadius: [0, 0, 4, 4] } }),
    ],
  }, true);
}

function renderHeat(T) {
  const h = DATA.hourly;
  const el = chart('chHeat');
  if (!h) { el.clear(); el.setOption({ title: { text: 'Seleziona un periodo fino a ~13 mesi per la mappa oraria', left: 'center', top: 'middle', textStyle: { color: T.text3, fontSize: 13, fontWeight: 500 } } }); return; }
  const metric = $('#heatSel').value;
  const grid = metric === 'casa' ? hourlyCasa(h) : (h.cats[metric] || h.dates.map(() => new Array(24).fill(0)));
  const now = new Date();
  const pts = [];
  h.dates.forEach((d, di) => {
    const dd = parse(d);
    for (let hh = 0; hh < 24; hh++) {
      if (new Date(dd.getFullYear(), dd.getMonth(), dd.getDate(), hh) > now) continue;
      pts.push([di, hh, +grid[di][hh].toFixed(3)]);
    }
  });
  // scala colori sul 98° percentile: un singolo picco non deve appiattire tutto il resto
  const sorted = pts.map((p) => p[2]).sort((a, b) => a - b);
  const max = sorted.length ? Math.max(0.05, sorted[Math.floor(0.98 * (sorted.length - 1))]) : 1;
  const peak = sorted.length ? sorted[sorted.length - 1] : 0;
  const labels = h.dates.map((d) => fmtDate(parse(d), { day: 'numeric', month: 'short' }));
  const hours = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}`);
  el.setOption({
    ...base(T, { legend: false }),
    grid: { left: 4, right: 12, top: 8, bottom: 56, containLabel: true },
    tooltip: { ...base(T).tooltip, trigger: 'item', axisPointer: undefined, formatter: (p) => `${fmtDate(parse(h.dates[p.value[0]]), { weekday: 'long', day: 'numeric', month: 'long' })} · ${hours[p.value[1]]}:00<br><b>${kwh(p.value[2])}</b>` },
    xAxis: { ...catAxis(T, labels), splitArea: { show: false } },
    yAxis: { type: 'category', data: hours, inverse: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: T.text3, fontSize: 10, interval: 2 } },
    visualMap: { min: 0, max: max || 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 220, itemWidth: 10, precision: 2, text: [`${num(max)}${peak > max ? '+' : ''} kWh`, '0'], textStyle: { color: T.text3, fontSize: 11 }, inRange: { color: T.seq } },
    series: [{ type: 'heatmap', data: pts, itemStyle: { borderColor: T.surface, borderWidth: h.dates.length > 120 ? 0 : 1, borderRadius: 2 }, emphasis: { itemStyle: { borderColor: T.text, borderWidth: 1 } }, progressive: 0 }],
  }, true);
}

function renderYoy(T) {
  if (!MONTHLY) return;
  const metric = $('#yoySel').value;
  const months = new Set(); Object.values(MONTHLY).forEach((m) => Object.keys(m).forEach((k) => months.add(k)));
  const get = (cat, k) => MONTHLY[cat]?.[k] || 0;
  const casa = (k) => Math.max(0, get('solare', k) + get('rete_prelievo', k) + get('batteria_scarica', k) - get('rete_immissione', k) - get('batteria_carica', k));
  const val = (k) => {
    if (metric === 'casa') return casa(k);
    if (metric === 'autosuff') { const cs = casa(k); return cs > 0 ? 100 * Math.max(0, 1 - get('rete_prelievo', k) / cs) : null; }
    return get(metric, k);
  };
  const years = [...new Set([...months].map((k) => k.slice(0, 4)))].sort();
  const mlabels = Array.from({ length: 12 }, (_, i) => fmtDate(new Date(2024, i, 1), { month: 'short' }));
  const isPct = metric === 'autosuff';
  const fmt = isPct ? (v) => `${NF[0].format(v)}%` : kwh;
  chart('chYoy').setOption({
    ...base(T),
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(fmt) },
    xAxis: catAxis(T, mlabels),
    yAxis: isPct ? { ...valAxis(T, (v) => `${v}%`), min: 0, max: 100 } : valAxis(T),
    series: years.map((y, i) => {
      const data = mlabels.map((_, m) => { const k = `${y}-${String(m + 1).padStart(2, '0')}`; if (!months.has(k)) return null; const v = val(k); return v == null ? null : +v.toFixed(isPct ? 1 : 2); });
      return barSeries(T, y, data, T.cat[i % 8], undefined, { barGap: '15%', barMaxWidth: 22 });
    }),
  }, true);
}

function renderTable(c, labels) {
  const devs = sensorsBy('dispositivo');
  const cols = [
    ['Periodo', (i) => labels[i]],
    ['Solare', (i) => c.solare[i]], ['Consumo casa', (i) => c.casa[i]], ['Prelievo', (i) => c.rete_prelievo[i]],
    ['Immissione', (i) => c.rete_immissione[i]], ['Bat. carica', (i) => c.batteria_carica[i]], ['Bat. scarica', (i) => c.batteria_scarica[i]],
    ['Autosuff. %', (i) => (c.casa[i] > 0.01 ? 100 * Math.max(0, 1 - c.rete_prelievo[i] / c.casa[i]) : NaN)],
    ...devs.map((id) => [META.sensors[id].label, (i) => DATA.series[id]?.[i] || 0]),
  ];
  CUR.table = { cols, n: labels.length };
  const fmtCell = (v) => (typeof v === 'number' ? (Number.isFinite(v) ? NF[2].format(v) : '–') : v);
  const totals = cols.map(([h, f], j) => {
    if (j === 0) return 'Totale';
    if (h === 'Autosuff. %') { const cs = sum(c.casa); return cs > 0 ? 100 * Math.max(0, 1 - sum(c.rete_prelievo) / cs) : NaN; }
    return sum(Array.from({ length: labels.length }, (_, i) => f(i)));
  });
  $('#dataTable').innerHTML =
    `<thead><tr>${cols.map(([h]) => `<th>${h}</th>`).join('')}</tr></thead><tbody>` +
    Array.from({ length: labels.length }, (_, i) => `<tr>${cols.map(([, f]) => `<td>${fmtCell(f(i))}</td>`).join('')}</tr>`).join('') +
    `<tr style="font-weight:600">${totals.map((v) => `<td>${fmtCell(v)}</td>`).join('')}</tr></tbody>`;
}

function exportCsv() {
  if (!CUR?.table) return;
  const { cols, n } = CUR.table;
  const cell = (v) => (typeof v === 'number' ? (Number.isFinite(v) ? v.toFixed(3).replace('.', ',') : '') : `"${String(v).replace(/"/g, '""')}"`);
  const lines = [cols.map(([h]) => `"${h}"`).join(';'), ...Array.from({ length: n }, (_, i) => cols.map(([, f]) => cell(f(i))).join(';'))];
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `energia_${iso(CUR.range[0])}_${iso(CUR.range[1])}.csv` });
  a.click(); URL.revokeObjectURL(a.href);
}

function renderAll() {
  if (!DATA || !META) return;
  const T = tokens();
  const c = shape(DATA);
  const labels = DATA.buckets.map((k) => bucketLabel(k, DATA.gran));
  const devColor = deviceColors(T);
  const devTot = Object.fromEntries(sensorsBy('dispositivo').map((id) => [id, sum(DATA.series[id] || [])]));
  renderKpis(T, c);
  renderBalance(T, c, labels);
  renderSankey(T, c, devTot, devColor);
  renderDevices(T, c, devTot, devColor);
  renderDevTime(T, labels, devColor);
  renderSolar(T, labels);
  renderRatio(T, c, labels);
  renderProfile(T);
  renderBattery(T, c, labels);
  renderHeat(T);
  renderYoy(T);
  renderTable(c, labels);
}

// ------------------------------------------------------------------ auto elettrica
function renderAuto() {
  if (!AUTO || st.view !== 'auto') return;
  const T = tokens();
  $('#autoEmpty').hidden = AUTO.enabled;
  $('#autoGrid').hidden = !AUTO.enabled;
  if (!AUTO.enabled) { $('#autoKpis').innerHTML = ''; return; }
  const S = AUTO.series;
  const labels = AUTO.buckets.map((k) => bucketLabel(k, AUTO.gran));
  const n = labels.length;
  const price = AUTO.price || 0;
  const tot = { home_solar: sum(S.home_solar), home_grid: sum(S.home_grid), home_added: sum(S.home_added), away: sum(S.away), km: sum(S.km), sessions: AUTO.sessions.length };
  const derive = (t) => ({
    added: t.home_added + t.away, wall: t.home_solar + t.home_grid, away: t.away,
    solar: safeDiv(t.home_solar, t.home_solar + t.home_grid), km: t.km,
    cons: t.km > 5 ? (100 * (t.home_added + t.away)) / t.km : NaN,
    cost: t.home_grid * price, sessions: t.sessions,
  });
  const cur = derive(tot);
  const pv = AUTO.prev_totals && !(st.mode === 'day' && iso(CUR.range[0]) === iso(today())) ? derive(AUTO.prev_totals) : null;
  const eff = safeDiv(tot.home_added, tot.home_solar + tot.home_grid);
  const intf = (v) => NF[0].format(v);
  $('#autoKpis').innerHTML = kpiTiles([
    { k: 'added', lbl: "Energia caricata nell'auto", v: cur.added, f: kwh, color: T.casa, good: 0 },
    { k: 'wall', lbl: 'Ricaricata a casa', v: cur.wall, f: kwh, color: T.solare, good: 0, hint: `Dalla presa di casa · efficienza ricarica ${pct(eff)}` },
    { k: 'away', lbl: 'Ricaricata fuori casa', v: cur.away, f: kwh, color: T.rete_immissione, good: -1 },
    { k: 'solar', lbl: 'Quota solare ricariche casa', v: cur.solar, f: pct, color: T.solare, good: 1, pp: true, hint: 'Parte della ricarica a casa coperta da solare e batteria di casa' },
    { k: 'km', lbl: 'Km percorsi', v: cur.km, f: intf, unit: 'km', color: T.rete_prelievo, good: 0 },
    { k: 'cons', lbl: 'Consumo medio', v: cur.cons, f: (v) => (Number.isFinite(v) ? NF[1].format(v) : '–'), unit: 'kWh/100km', color: T.batteria_scarica, good: -1, hint: 'Energia caricata ÷ km percorsi (stima: dipende da quando ricarichi)' },
    { k: 'cost', lbl: 'Costo ricarica a casa', v: cur.cost, f: eur, color: T.rete_prelievo, good: -1, hint: `Solo la parte presa dalla rete × ${eur(price)}/kWh` },
    { k: 'sessions', lbl: 'Sessioni di ricarica', v: cur.sessions, f: intf, color: T.other, good: 0 },
  ], pv);

  // energia ricaricata
  chart('chAutoEnergy').setOption({
    ...base(T, { zoom: n > 62 }),
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(kwh) },
    xAxis: catAxis(T, labels), yAxis: valAxis(T),
    series: [
      barSeries(T, 'Casa · solare/batteria', S.home_solar, T.solare, 'e'),
      barSeries(T, 'Casa · rete', S.home_grid, T.rete_prelievo, 'e'),
      barSeries(T, 'Fuori casa', S.away, T.rete_immissione, 'e'),
    ],
  }, true);

  // livello batteria (banda min-max + media)
  const P = AUTO.battery.points;
  const hourly = AUTO.battery.step === 'hour';
  chart('chAutoBattery').setOption({
    ...base(T, { zoom: P.length > 200 }),
    legend: { ...base(T).legend, data: ['Livello batteria', 'Min–max'] },
    tooltip: {
      ...base(T).tooltip, axisPointer: { type: 'line', lineStyle: { color: T.text3 } },
      formatter: (ps) => {
        const p = P[ps[0].dataIndex]; if (!p) return '';
        const when = fmtDate(new Date(p[0]), hourly ? { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' } : { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
        return `<div style="font-weight:600;margin-bottom:4px">${when}</div>Media <b>${NF[0].format(p[1])}%</b><br>Min–max <b>${NF[0].format(p[2])}–${NF[0].format(p[3])}%</b>`;
      },
    },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: T.border } }, axisTick: { show: false }, axisLabel: { color: T.text3, fontSize: 11, hideOverlap: true }, splitLine: { show: false } },
    yAxis: { ...valAxis(T, (v) => `${v}%`), min: 0, max: 100 },
    series: [
      { name: 'min', type: 'line', data: P.map((p) => [p[0], p[2]]), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { name: 'Min–max', type: 'line', data: P.map((p) => [p[0], +(p[3] - p[2]).toFixed(1)]), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: T.batteria_scarica, opacity: 0.15 }, silent: true },
      lineSeries('Livello batteria', P.map((p) => [p[0], p[1]]), T.batteria_scarica, { showSymbol: false, smooth: 0.2 }),
    ],
  }, true);

  // origine dell'energia
  const mix = [
    { name: 'Casa · solare/batteria', value: +tot.home_solar.toFixed(2), itemStyle: { color: T.solare } },
    { name: 'Casa · rete', value: +tot.home_grid.toFixed(2), itemStyle: { color: T.rete_prelievo } },
    { name: 'Fuori casa', value: +tot.away.toFixed(2), itemStyle: { color: T.rete_immissione } },
  ];
  const mixTot = sum(mix.map((m) => m.value));
  chart('chAutoMix').setOption({
    ...base(T),
    tooltip: { ...base(T).tooltip, trigger: 'item', axisPointer: undefined, formatter: (p) => `${p.marker}${p.name}<br><b>${kwh(p.value)}</b> · ${pct(safeDiv(p.value, mixTot))}` },
    series: [{
      type: 'pie', radius: ['52%', '76%'], center: ['50%', '57%'], avoidLabelOverlap: true, data: mix,
      itemStyle: { borderColor: T.surface, borderWidth: 2, borderRadius: 4 },
      label: { color: T.text2, fontSize: 12, formatter: (p) => `{v|${pct(safeDiv(p.value, mixTot))}}\n${num(p.value)} kWh`, rich: { v: { color: T.text, fontWeight: 600, fontSize: 14 } } },
      labelLine: { lineStyle: { color: T.border } },
    }],
    graphic: [{ type: 'text', left: 'center', top: '52%', style: { text: `${num(mixTot)}\nkWh`, fill: T.text, font: '600 16px Inter, system-ui', textAlign: 'center' } }],
  }, true);

  // km
  chart('chAutoKm').setOption({
    ...base(T, { legend: false, zoom: n > 62 }),
    tooltip: { ...base(T).tooltip, formatter: tipFormatter((v) => `${NF[0].format(v)} km`) },
    xAxis: catAxis(T, labels), yAxis: valAxis(T, (v) => NF[0].format(v)),
    series: [barSeries(T, 'Km percorsi', S.km.map((v) => +v.toFixed(1)), T.rete_prelievo)],
  }, true);

  // orario delle ricariche
  const hours = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`);
  chart('chAutoHours').setOption({
    ...base(T),
    tooltip: { ...base(T).tooltip, formatter: tipFormatter(kwh) },
    xAxis: catAxis(T, hours), yAxis: valAxis(T),
    series: [
      barSeries(T, 'A casa', AUTO.profile.home, T.casa, 'h'),
      barSeries(T, 'Fuori casa', AUTO.profile.away, T.rete_immissione, 'h'),
    ],
  }, true);

  renderSessions();
}

function sessionRows() {
  const hm = { hour: '2-digit', minute: '2-digit' };
  return AUTO.sessions.map((x) => {
    const s = new Date(x.start), e = new Date(x.end);
    const h = Math.round((e - s) / 3600000);
    return {
      data: fmtDate(s, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }),
      orario: `${fmtDate(s, hm)} – ${fmtDate(e, hm)}`, durata: h, luogo: x.home ? 'Casa' : 'Fuori casa', home: x.home,
      added: x.added, wall: x.home ? x.wall : null, solar: x.home ? safeDiv(x.solar, x.wall) : null,
      soc: x.soc_start != null && x.soc_end != null ? [x.soc_start, x.soc_end] : null,
      power: x.max_power, cost: x.home ? x.cost : null,
    };
  });
}

function renderSessions() {
  const rows = sessionRows();
  const d = (v, f) => (v == null || !Number.isFinite(v) ? '–' : f(v));
  $('#sessTable').innerHTML = `<thead><tr><th>Data</th><th>Orario</th><th>Durata</th><th>Luogo</th><th>kWh nell'auto</th><th>kWh dalla presa</th><th>Quota solare</th><th>Batteria</th><th>Potenza max</th><th>Costo rete</th></tr></thead><tbody>` +
    (rows.length ? rows.map((r) => `<tr><td>${r.data}</td><td>${r.orario}</td><td>~${r.durata} h</td>
      <td><span class="pill ${r.home ? 'home' : 'away'}">${r.luogo}</span></td><td>${NF[1].format(r.added)}</td>
      <td>${d(r.wall, (v) => NF[1].format(v))}</td><td>${d(r.solar, pct)}</td>
      <td>${r.soc ? `${NF[0].format(r.soc[0])}% → ${NF[0].format(r.soc[1])}%` : '–'}</td>
      <td>${d(r.power, (v) => `${NF[1].format(v)} kW`)}</td><td>${d(r.cost, eur)}</td></tr>`).join('')
      : '<tr><td colspan="10" style="text-align:center;color:var(--text-3)">Nessuna ricarica nel periodo</td></tr>') + '</tbody>';
}

function exportSessions() {
  if (!AUTO?.sessions) return;
  const c = (v) => (v == null || (typeof v === 'number' && !Number.isFinite(v)) ? '' : typeof v === 'number' ? String(+v.toFixed(3)).replace('.', ',') : `"${String(v).replace(/"/g, '""')}"`);
  const head = ['Inizio', 'Fine', 'Luogo', 'kWh auto', 'kWh presa', 'kWh da solare', '% batteria inizio', '% batteria fine', 'Potenza max kW', 'Costo rete €'];
  const lines = [head.map((h) => `"${h}"`).join(';'), ...AUTO.sessions.map((x) => [x.start, x.end, x.home ? 'Casa' : 'Fuori casa', x.added, x.home ? x.wall : null, x.home ? x.solar : null, x.soc_start, x.soc_end, x.max_power, x.home ? x.cost : null].map(c).join(';'))];
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `ricariche_${iso(CUR.range[0])}_${iso(CUR.range[1])}.csv` });
  a.click(); URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------------ backup / restore
const fmtSize = (b) => (b >= 1048576 ? `${NF[1].format(b / 1048576)} MB` : `${NF[0].format(b / 1024)} kB`);
function bkStatus(msg, kind = '') { const el = $('#bkStatus'); el.textContent = msg; el.className = `bk-status ${kind}`; }
function bkBusy(on) {
  ['#bkCreate', '#bkRestoreFile'].forEach((sel) => { $(sel).disabled = on || (sel === '#bkRestoreFile' && !$('#bkFile').files.length); });
  document.querySelectorAll('#bkTable button').forEach((b) => { b.disabled = on; });
}
function download(href) { const a = Object.assign(document.createElement('a'), { href, download: '' }); document.body.appendChild(a); a.click(); a.remove(); }

async function loadBackups() {
  const list = await api('/api/backups');
  const f = (v) => (v ? fmtDate(new Date(v), { day: '2-digit', month: '2-digit', year: '2-digit' }) : '–');
  $('#bkTable').innerHTML = '<thead><tr><th>File</th><th>Creato</th><th>Periodo dati</th><th>Punti</th><th>Dim.</th><th></th></tr></thead><tbody>' +
    (list.length ? list.map((b) => `<tr><td>${b.name}${b.invalid ? ' ⚠' : ''}</td>
      <td>${fmtDate(new Date(b.mtime), { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })}</td>
      <td>${f(b.first)} → ${f(b.last)}</td><td>${b.points != null ? NF[0].format(b.points) : '–'}</td><td>${fmtSize(b.size)}</td>
      <td><span class="row-actions"><a href="/api/backups/${encodeURIComponent(b.name)}" download>Scarica</a>${b.invalid ? '' : `<button data-restore="${b.name}">Ripristina</button>`}</span></td></tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center;color:var(--text-3)">Nessun backup sul server</td></tr>') + '</tbody>';
  document.querySelectorAll('#bkTable [data-restore]').forEach((b) => b.addEventListener('click', () => restore(b.dataset.restore)));
}

async function createBackup() {
  bkBusy(true); bkStatus('Creazione del backup in corso… (può richiedere qualche decina di secondi)');
  try {
    const r = await api('/api/backup', { method: 'POST' });
    bkStatus(`Backup creato: ${r.name} · ${NF[0].format(r.points)} punti · ${fmtSize(r.size)}`, 'ok');
    download(`/api/backups/${encodeURIComponent(r.name)}`);
    await loadBackups();
  } catch (e) { bkStatus(`Errore: ${e.message}`, 'error'); }
  finally { bkBusy(false); }
}

async function restore(savedName) {
  const file = savedName ? null : $('#bkFile').files[0];
  const what = savedName || file?.name;
  if (!what) return;
  if (!confirm(`Ripristino completo da "${what}".\n\nTUTTI i dati attuali verranno sostituiti da quelli del backup (prima viene salvato un backup di sicurezza dello stato attuale).\n\nContinuare?`)) return;
  bkBusy(true); bkStatus(file ? 'Caricamento del file e ripristino in corso… non chiudere la pagina' : 'Ripristino in corso… non chiudere la pagina');
  try {
    const r = savedName
      ? await api(`/api/restore/${encodeURIComponent(savedName)}`, { method: 'POST' })
      : await api('/api/restore', { method: 'POST', headers: { 'Content-Type': 'application/gzip' }, body: file });
    bkStatus(`Ripristino completato: ${NF[0].format(r.points)} punti. Backup di sicurezza dello stato precedente: ${r.safety_backup}`, 'ok');
    $('#bkFile').value = '';
    await loadBackups();
    await loadMeta(); await Promise.all([load(), loadMonthly()]);
  } catch (e) { bkStatus(`Errore: ${e.message}`, 'error'); }
  finally { bkBusy(false); }
}

// ------------------------------------------------------------------ impostazioni
function setStatus(msg, kind = '') { const el = $('#setStatus'); el.textContent = msg; el.className = `bk-status ${kind}`; }
function setBusy(on) { ['#setTest', '#setSave', '#setHaUrl'].forEach((sel) => { $(sel).disabled = on; }); }
function showSettings(s) {
  $('#setHaUrl').value = s.ha_url_custom;
  $('#setDefault').textContent = s.ha_url_default;
  $('#setCurrent').textContent = s.ha_url;
}

async function openSettings() {
  setStatus(''); $('#settingsDlg').showModal();
  try {
    showSettings(await api('/api/settings'));
    const err = META?.sync?.last_error;
    if (err) setStatus(`Ultimo sync fallito: ${err}`, 'error');
  } catch (e) { setStatus(`Errore: ${e.message}`, 'error'); }
}

const jsonBody = (obj) => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj) });

async function testSettings() {
  setBusy(true); setStatus('Connessione in corso…');
  try {
    const r = await api('/api/settings/test', { method: 'POST', ...jsonBody({ ha_url: $('#setHaUrl').value }) });
    setStatus(`Connessione riuscita: Home Assistant ${r.version || ''} su ${r.ha_url}`, 'ok');
  } catch (e) { setStatus(e.message, 'error'); }
  finally { setBusy(false); }
}

async function saveSettings(ev) {
  ev.preventDefault();
  setBusy(true); setStatus('Salvataggio…');
  try {
    const s = await api('/api/settings', { method: 'PUT', ...jsonBody({ ha_url: $('#setHaUrl').value }) });
    showSettings(s);
    setStatus(`Salvato: i dati verranno letti da ${s.ha_url}`, 'ok');
    $('#settingsDlg').close();
    doSync();
  } catch (e) { setStatus(e.message, 'error'); }
  finally { setBusy(false); }
}

// ------------------------------------------------------------------ IO
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { /* */ } throw new Error(m); }
  return r.json();
}
function toast(msg, error = false) {
  const t = $('#toast'); t.textContent = msg; t.className = `toast${error ? ' error' : ''}`; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, error ? 7000 : 3500);
}
function renderInfo() {
  if (!META?.last) { $('#dataInfo').textContent = 'Nessun dato ancora importato'; return; }
  const last = new Date(META.last); last.setHours(last.getHours() + 1);
  const f = (d) => fmtDate(d, { day: 'numeric', month: 'short', year: 'numeric' });
  const sync = META.sync?.last_sync ? ` · sync ${fmtDate(new Date(META.sync.last_sync), { hour: '2-digit', minute: '2-digit' })}` : '';
  $('#dataInfo').textContent = `Dati dal ${f(new Date(META.first))} fino a ${fmtDate(last, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}${sync}`;
}

async function loadMeta() { META = await api('/api/meta'); renderInfo(); }
async function loadMonthly() { MONTHLY = await api('/api/monthly'); renderYoy(tokens()); }

let reqId = 0;
async function load() {
  const r = range();
  const gran = st.gran === 'auto' ? autoGran(r) : st.gran;
  CUR = { range: r, singleDay: iso(r[0]) === iso(r[1]) };
  $('#periodLabel').textContent = periodLabel(r);
  $('#nextBtn').disabled = st.mode === 'all' || addDays(r[1], 1) > today();
  $('#prevBtn').disabled = st.mode === 'all';
  const p = new URLSearchParams({ start: iso(r[0]), end: iso(r[1]), gran });
  CUR.exportUrl = `/api/export.xlsx?${p}`;
  const pr = prevRange(r);
  if (pr) { p.set('prev_start', iso(pr[0])); p.set('prev_end', iso(pr[1])); }
  const id = ++reqId;
  document.body.style.cursor = 'progress';
  try {
    if (st.view === 'auto') {
      const d = await api(`/api/auto?${p}`);
      if (id !== reqId) return;
      AUTO = d;
      renderAuto();
    } else {
      const d = await api(`/api/data?${p}`);
      if (id !== reqId) return;
      DATA = d;
      renderAll();
    }
  } catch (e) { toast(`Errore nel caricamento: ${e.message}`, true); }
  finally { if (id === reqId) document.body.style.cursor = ''; }
}

async function doSync() {
  const b = $('#syncBtn');
  b.disabled = true; b.classList.add('spinning'); b.querySelector('span').textContent = 'Aggiornamento…';
  try {
    const s = await api('/api/sync', { method: 'POST' });
    toast(`Dati aggiornati da Home Assistant (${NF[0].format(s.last_points || 0)} punti)`);
    await loadMeta(); await Promise.all([load(), loadMonthly()]);
  } catch (e) { toast(e.message, true); }
  finally { b.disabled = false; b.classList.remove('spinning'); b.querySelector('span').textContent = 'Aggiorna da Home Assistant'; }
}

// ------------------------------------------------------------------ UI wiring
function syncView() {
  document.querySelectorAll('#viewSeg button').forEach((b) => b.classList.toggle('active', b.dataset.view === st.view));
  $('#viewCasa').hidden = st.view !== 'casa';
  $('#viewAuto').hidden = st.view !== 'auto';
}

function syncControls() {
  document.querySelectorAll('#modeSeg button').forEach((b) => b.classList.toggle('active', b.dataset.mode === st.mode));
  $('#customBox').hidden = st.mode !== 'custom';
  $('#todayBtn').hidden = st.mode === 'custom' || st.mode === 'all';
  $('#cStart').value = iso(st.cStart); $('#cEnd').value = iso(st.cEnd);
  $('#granSel').value = st.gran;
}

function init() {
  const th = store.get('theme', '');
  if (th) document.documentElement.dataset.theme = th;
  document.querySelectorAll('#modeSeg button').forEach((b) => b.addEventListener('click', () => {
    st.mode = b.dataset.mode; store.set('mode', st.mode); syncControls(); load();
  }));
  $('#prevBtn').addEventListener('click', () => { shift(-1); syncControls(); load(); });
  $('#nextBtn').addEventListener('click', () => { shift(1); syncControls(); load(); });
  $('#todayBtn').addEventListener('click', () => { st.anchor = today(); load(); });
  const onCustom = () => {
    const s = $('#cStart').value, e = $('#cEnd').value;
    if (!s || !e) return;
    st.cStart = parse(s); st.cEnd = parse(e);
    if (st.cEnd < st.cStart) [st.cStart, st.cEnd] = [st.cEnd, st.cStart];
    syncControls(); load();
  };
  $('#cStart').addEventListener('change', onCustom);
  $('#cEnd').addEventListener('change', onCustom);
  $('#granSel').addEventListener('change', (e) => { st.gran = e.target.value; store.set('gran', st.gran); load(); });
  $('#heatSel').addEventListener('change', () => renderHeat(tokens()));
  $('#yoySel').addEventListener('change', () => renderYoy(tokens()));
  $('#csvBtn').addEventListener('click', exportCsv);
  $('#xlsxBtn').addEventListener('click', () => {
    if (!CUR?.exportUrl) return;
    toast('Preparazione del file Excel…');
    const a = Object.assign(document.createElement('a'), { href: CUR.exportUrl, download: '' });
    document.body.appendChild(a); a.click(); a.remove();
  });
  $('#sessCsvBtn').addEventListener('click', exportSessions);
  document.querySelectorAll('#viewSeg button').forEach((b) => b.addEventListener('click', () => {
    if (st.view === b.dataset.view) return;
    st.view = b.dataset.view; store.set('view', st.view); history.replaceState(null, '', `#${st.view}`); syncView(); load();
  }));
  $('#syncBtn').addEventListener('click', doSync);
  $('#backupBtn').addEventListener('click', () => {
    bkStatus(''); $('#backupDlg').showModal();
    loadBackups().catch((e) => bkStatus(`Errore: ${e.message}`, 'error'));
  });
  $('#settingsBtn').addEventListener('click', openSettings);
  $('#setClose').addEventListener('click', () => $('#settingsDlg').close());
  $('#setTest').addEventListener('click', testSettings);
  $('#setForm').addEventListener('submit', saveSettings);
  $('#bkClose').addEventListener('click', () => $('#backupDlg').close());
  $('#bkCreate').addEventListener('click', createBackup);
  $('#bkFile').addEventListener('change', () => { $('#bkRestoreFile').disabled = !$('#bkFile').files.length; });
  $('#bkRestoreFile').addEventListener('click', () => restore(null));
  $('#themeBtn').addEventListener('click', () => {
    const next = themeMode() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next; store.set('theme', next); renderAll(); renderAuto();
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (!document.documentElement.dataset.theme) { renderAll(); renderAuto(); } });
  syncView();
  syncControls();
  loadMeta().then(() => Promise.all([load(), loadMonthly()])).catch((e) => toast(`Errore: ${e.message}`, true));
}
init();
