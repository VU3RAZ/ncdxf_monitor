// Propagation report page: draws window.REPORT (built by report.py) as SVG, no libraries.
(function () {
  const R = window.REPORT;
  const $ = (id) => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const BANDS = [20, 17, 15, 12, 10];
  const MHZ = { 20: '14.100', 17: '18.110', 15: '21.150', 12: '24.930', 10: '28.200' };
  const css = getComputedStyle(document.documentElement);
  const col = (b) => css.getPropertyValue('--b' + b).trim();
  const C = (n) => css.getPropertyValue('--' + n).trim();
  const fU = (t) => new Date(t * 1000).toISOString().slice(11, 16);
  const fL = (t) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  const pct = (v) => Math.round(v * 100) + '%';
  const num = (v) => v.toLocaleString('en');
  const ML_MIN = 46, MR = 12;

  // ---- live controls (only when served by the monitor, not opened as a file) ----
  const live = location.protocol.startsWith('http');
  document.querySelectorAll('.rp-live').forEach(e => { e.hidden = !live; });
  $('rpClose').onclick = () => { window.close(); if (!window.closed) location.href = '/'; };
  if (live) {
    fetch('/api/report/days').then(r => r.json()).then(j => {
      for (const d of j.days) { const o = document.createElement('option'); o.value = o.textContent = d; $('rpDay').appendChild(o); }
      if (R) $('rpDay').value = R.day;
    }).catch(() => {});
    const go = (day) => {
      $('rpRegen').disabled = true; $('rpGen').textContent = 'reading the log…';
      fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ day }) })
        .then(r => r.json()).then(j => { if (j.url) location.href = j.url + '?t=' + Date.now(); else throw new Error(j.error); })
        .catch(e => { $('rpRegen').disabled = false; $('rpGen').textContent = 'failed: ' + e.message; });
    };
    $('rpDay').onchange = () => go($('rpDay').value);
    $('rpRegen').onclick = () => go(R ? R.day : $('rpDay').value);
  }
  if (!R || !R.summary) { if (R) $('rpTitle').textContent = 'Beacon propagation, ' + R.day; return; }

  const S = R.summary, W = R.window_s;
  $('rpGen').textContent = `generated ${fU(R.generated_t)} UTC (${fL(R.generated_t)} local) from ${R.files.join(', ')}`;
  document.title = `Propagation ${R.day}`;
  $('rpTitle').textContent = `${R.station.call || 'Beacon'} propagation, ${R.day}` + (R.station.grid ? ` · ${R.station.grid}` : '');

  // ---- headline ----
  const take = $('rpTake'); take.replaceChildren();
  const add = (s, bold) => { const e = document.createElement(bold ? 'b' : 'span'); e.textContent = s; take.appendChild(e); };
  add(`${S.hours} hours of beacon slots from `); add(`${fU(S.first_t)}`, 1); add(' to '); add(`${fU(S.last_t)} UTC`, 1);
  add(` (${fL(S.first_t)}–${fL(S.last_t)} local). The most open band was `); add(`${S.best_band_m} m`, 1);
  add(`, heard on ${S.best_band_pct}% of its slots`);
  if (S.top_beacon) { add('; the steadiest path was '); add(S.top_beacon, 1); add(` (${S.top_beacon_location}, heard ${S.top_beacon_heard} times)`); }
  add('.');
  const figs = [
    [num(S.slots), 'beacon slots measured (10 s each)'],
    [`${S.beacons_heard} of ${S.beacons_measured}`, 'beacons heard at least once'],
    [`${S.best_band_pct}%`, `of slots heard on ${S.best_band_m} m, the most open band`],
    [S.highest_band_m ? `${S.highest_band_m} m` : '—', 'highest band any beacon was heard on'],
    [S.kp_min == null ? '—' : S.kp_min === S.kp_max ? `${S.kp_min}` : `${S.kp_min}–${S.kp_max}`, 'planetary Kp during the day'],
  ];
  $('rpFigs').replaceChildren(...figs.map(([v, l]) => {
    const d = document.createElement('div'); d.className = 'rp-fig';
    const a = document.createElement('span'); a.className = 'v'; a.textContent = v;
    const b = document.createElement('span'); b.className = 'l'; b.textContent = l;
    d.append(a, b); return d;
  }));
  const ex = R.excluded, modes = Object.entries(S.modes).map(([m, n]) => `${n} ${m}`).join(', ');
  $('rpFoot').textContent = `Slots by monitor mode: ${modes}. Left out: ${ex.filter_50hz} slots with a 50 Hz filter, ` +
    `${ex.duplicate} duplicates. Heard means the beacon's callsign keying was identified and at least the 100 W dash ` +
    `reached 0 dB S/N in 30 Hz.`;

  // ---- svg helpers ----
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function txt(parent, x, y, s, cls, anchor) {
    const t = el('text', { x, y, class: cls, 'text-anchor': anchor || 'start' }, parent); t.textContent = s; return t;
  }
  function scale(d0, d1, r0, r1) {
    const f = (v) => r0 + (v - d0) / (d1 - d0) * (r1 - r0);
    f.invert = (p) => d0 + (p - r0) / (r1 - r0) * (d1 - d0);
    return f;
  }
  function measure(strings, cls) {
    const s = el('svg', { width: 0, height: 0 }, document.body);
    let w = 0;
    for (const v of strings) w = Math.max(w, txt(s, 0, 0, v, cls).getComputedTextLength());
    s.remove();
    return Math.ceil(w);
  }
  function tipFor(box) {
    const t = document.createElement('div'); t.className = 'rp-tip'; t.hidden = true; box.appendChild(t);
    return {
      show(px, py, head, lines) {
        t.replaceChildren();
        const h = document.createElement('div'); h.className = 'h'; h.textContent = head; t.appendChild(h);
        for (const [color, s, outline] of lines) {
          const r = document.createElement('div'); r.className = 'r';
          if (color || outline) { const i = document.createElement('i'); i.className = 'sw' + (outline ? ' out' : ''); if (color) i.style.background = color; r.appendChild(i); }
          const sp = document.createElement('span'); sp.textContent = s; r.appendChild(sp); t.appendChild(r);
        }
        t.hidden = false;
        t.style.left = Math.max(0, Math.min(px + 14, box.clientWidth - t.offsetWidth - 2)) + 'px';
        t.style.top = Math.max(0, py - 10) + 'px';
      },
      hide() { t.hidden = true; },
    };
  }
  function legend(box, items) {
    const lg = document.createElement('div'); lg.className = 'rp-legend';
    for (const [color, label, cls] of items) {
      const s = document.createElement('span'), i = document.createElement('i');
      i.className = 'sw' + (cls ? ' ' + cls : ''); if (color) i.style.background = color;
      s.append(i, label); lg.appendChild(s);
    }
    box.appendChild(lg);
  }

  // ---- shared time axis ----
  const t0 = Math.min(...R.windows.map(r => r.t)), t1 = Math.max(...R.windows.map(r => r.t)) + W;
  const all = []; for (let t = t0; t < t1; t += W) all.push(t);
  const measured = new Set(R.windows.map(r => r.t));
  const calls = R.beacons.map(b => b.beacon);
  const ML = Math.max(ML_MIN, measure(calls, 'lbl') + 12);
  const mid = (x, t) => x(t + W / 2);

  function timeAxis(svg, x, y0, width) {
    const every = (width < 560 ? 2 : 1) * 3600;
    el('line', { x1: x(t0), x2: x(t1), y1: y0, y2: y0, stroke: C('line') }, svg);
    for (let t = Math.ceil(t0 / every) * every; t <= t1; t += every) {
      if (x(t) - x(t0) < 24) continue;            // leave room for the UTC / local labels
      el('line', { x1: x(t), x2: x(t), y1: y0, y2: y0 + 4, stroke: C('line') }, svg);
      txt(svg, x(t), y0 + 16, fU(t), 'tick', 'middle');
      txt(svg, x(t), y0 + 30, fL(t), 'tick l', 'middle');
    }
    txt(svg, x(t0) - 8, y0 + 16, 'UTC', 'tick', 'end');
    txt(svg, x(t0) - 8, y0 + 30, 'local', 'tick l', 'end');
  }
  function gaps(svg, x, y0, y1) {
    let s = null;
    const runs = [];
    for (const t of all.concat([t1])) {
      if (t < t1 && !measured.has(t)) { if (s == null) s = t; } else if (s != null) { runs.push([s, t]); s = null; }
    }
    for (const [a, b] of runs) {
      el('rect', { x: x(a), y: y0, width: x(b) - x(a), height: y1 - y0, fill: C('grid') }, svg);
      if (x(b) - x(a) > 90) txt(svg, (x(a) + x(b)) / 2, y0 + 14, 'not monitored', 'tick', 'middle');
    }
  }
  function hover(svg, box, x, y0, y1, lines) {
    const rule = el('line', { y1: y0, y2: y1, stroke: C('dim'), opacity: 0 }, svg);
    const tip = tipFor(box);
    const hit = el('rect', { x: x(t0), y: y0, width: x(t1) - x(t0), height: y1 - y0, fill: 'transparent' }, svg);
    hit.addEventListener('pointermove', (ev) => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (svg.viewBox.baseVal.width / r.width);
      const t = t0 + Math.floor((x.invert(px) - t0) / W) * W;
      const l = measured.has(t) ? lines(t) : null;
      if (!l) { rule.setAttribute('opacity', 0); tip.hide(); return; }
      rule.setAttribute('x1', mid(x, t)); rule.setAttribute('x2', mid(x, t)); rule.setAttribute('opacity', 1);
      tip.show(ev.clientX - box.getBoundingClientRect().left, ev.clientY - box.getBoundingClientRect().top,
        `${fU(t)}–${fU(t + W)} UTC (${fL(t)} local)`, l);
    });
    hit.addEventListener('pointerleave', () => { rule.setAttribute('opacity', 0); tip.hide(); });
  }

  // ---- 1. band openings ----
  function drawBands() {
    const box = $('rpBands'); box.replaceChildren();
    const width = box.clientWidth, h = 280, mt = 10, mb = 40, ph = h - mt - mb;
    const x = scale(t0, t1, ML, width - MR);
    const ymax = Math.max(0.2, Math.ceil(Math.max(...R.windows.map(r => r.share)) * 10) / 10);
    const y = scale(0, ymax, mt + ph, mt);
    const svg = el('svg', { viewBox: `0 0 ${width} ${h}`, height: h }, box);
    gaps(svg, x, mt, mt + ph);
    const step = ymax > 0.5 ? 0.2 : 0.1;
    for (let v = 0; v <= ymax + 1e-9; v += step) {
      el('line', { x1: ML, x2: width - MR, y1: y(v), y2: y(v), stroke: C('grid') }, svg);
      txt(svg, ML - 8, y(v) + 4, pct(v), 'tick', 'end');
    }
    timeAxis(svg, x, mt + ph, width);
    const byBand = {};
    for (const r of R.windows) (byBand[r.band_m] = byBand[r.band_m] || new Map()).set(r.t, r);
    for (const b of BANDS) {
      const m = byBand[b]; if (!m) continue;
      let d = '', pen = false;
      for (const t of all) {
        const r = m.get(t);
        if (!r) { pen = false; continue; }
        d += (pen ? 'L' : 'M') + mid(x, t).toFixed(1) + ',' + y(r.share).toFixed(1); pen = true;
      }
      el('path', { d, fill: 'none', stroke: col(b), 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, svg);
      for (const [t, r] of m) el('circle', { cx: mid(x, t), cy: y(r.share), r: 3, fill: col(b), stroke: C('panel'), 'stroke-width': 1.5 }, svg);
    }
    hover(svg, box, x, mt, mt + ph, (t) => BANDS.filter(b => byBand[b] && byBand[b].get(t))
      .map(b => { const r = byBand[b].get(t); return [col(b), `${b} m  ${r.heard} of ${r.tried} heard (${pct(r.share)})`]; }));
    legend(box, BANDS.filter(b => byBand[b]).map(b => [col(b), `${b} m (${MHZ[b]})`]).concat([[null, 'not monitored', 'gap']]));
  }

  // ---- 2. highest band per beacon ----
  function drawPaths() {
    const box = $('rpPaths'); box.replaceChildren();
    const width = box.clientWidth, rowH = 17, mt = 4, ph = rowH * calls.length, h = mt + ph + 40;
    const x = scale(t0, t1, ML, width - MR);
    const svg = el('svg', { viewBox: `0 0 ${width} ${h}`, height: h }, box);
    gaps(svg, x, mt, mt + ph);
    const row = new Map(calls.map((c, i) => [c, mt + i * rowH]));
    calls.forEach(c => txt(svg, ML - 8, row.get(c) + rowH / 2 + 4, c, 'lbl', 'end'));
    const info = new Map(R.beacons.map(b => [b.beacon, b]));
    const cw = Math.max(2, x(t0 + W) - x(t0) - 2), chh = rowH - 3;
    const tip = tipFor(box);
    for (const p of R.paths) {
      const r = el('rect', { x: x(p.t) + 1, y: row.get(p.beacon) + 1, width: cw, height: chh, rx: 2,
        fill: p.top_band_m ? col(p.top_band_m) : 'transparent', stroke: p.top_band_m ? 'none' : C('muted') }, svg);
      r.addEventListener('pointerenter', (ev) => {
        const b = info.get(p.beacon), bb = box.getBoundingClientRect();
        tip.show(ev.clientX - bb.left, ev.clientY - bb.top, `${p.beacon}  ${b.location}, ${num(b.km)} km, ${b.bearing_deg}°`, [
          [p.top_band_m ? col(p.top_band_m) : null, p.top_band_m ? `highest heard: ${p.top_band_m} m` : 'not heard', !p.top_band_m],
          [null, `${fU(p.t)} UTC (${fL(p.t)} local)`], [null, p.detail]]);
      });
      r.addEventListener('pointerleave', () => tip.hide());
    }
    timeAxis(svg, x, mt + ph, width);
    legend(box, BANDS.map(b => [col(b), `${b} m`]).concat([[null, 'measured, not heard', 'out']]));
  }

  // ---- 3. sun and Kp ----
  function drawCond() {
    const box = $('rpCond'); box.replaceChildren();
    const rows = new Map(R.conditions.map(r => [r.t, r]));
    const width = box.clientWidth, mt = 16, h1 = 100, gap = 30, h2 = 56, k0 = mt + h1 + gap, h = k0 + h2 + 40;
    const x = scale(t0, t1, ML, width - MR);
    const svg = el('svg', { viewBox: `0 0 ${width} ${h}`, height: h }, box);
    const sun = R.conditions.map(r => r.rx_sun_deg);
    const lo = Math.min(-12, Math.floor(Math.min(...sun) / 10) * 10), hi = Math.max(30, Math.ceil(Math.max(...sun) / 10) * 10);
    const ys = scale(lo, hi, mt + h1, mt);
    el('rect', { x: ML, width: width - ML - MR, y: ys(6), height: ys(-6) - ys(6), fill: C('grid') }, svg);
    gaps(svg, x, mt, k0 + h2);
    txt(svg, ML + 4, ys(-6) - 3, 'greyline', 'tick');
    const sstep = hi - lo > 80 ? 30 : 15;
    for (let v = Math.ceil(lo / sstep) * sstep; v <= hi; v += sstep) {
      el('line', { x1: ML, x2: width - MR, y1: ys(v), y2: ys(v), stroke: C('grid') }, svg);
      txt(svg, ML - 8, ys(v) + 4, v + '°', 'tick', 'end');
    }
    txt(svg, ML, mt - 5, 'Sun elevation at your station', 'tick');
    let d = '', pen = false;
    for (const t of all) {
      const r = rows.get(t);
      if (!r) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + mid(x, t).toFixed(1) + ',' + ys(r.rx_sun_deg).toFixed(1); pen = true;
    }
    el('path', { d, fill: 'none', stroke: C('accent'), 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, svg);
    const yk = scale(0, 9, k0 + h2, k0);
    for (const v of [0, 4, 9]) {
      el('line', { x1: ML, x2: width - MR, y1: yk(v), y2: yk(v), stroke: v === 4 ? C('dim') : C('grid'), 'stroke-dasharray': v === 4 ? '4 3' : null }, svg);
      txt(svg, ML - 8, yk(v) + 4, String(v), 'tick', 'end');
    }
    txt(svg, width - MR - 4, yk(4) - 3, 'active', 'tick', 'end');
    txt(svg, ML, k0 - 6, 'Planetary Kp', 'tick');
    const cw = Math.max(2, x(t0 + W) - x(t0) - 2);
    for (const r of R.conditions) if (r.kp != null)
      el('rect', { x: x(r.t) + 1, width: cw, y: yk(r.kp), height: yk(0) - yk(r.kp), rx: 2, fill: r.kp >= 5 ? C('bad') : C('muted') }, svg);
    timeAxis(svg, x, k0 + h2, width);
    hover(svg, box, x, mt, k0 + h2, (t) => {
      const r = rows.get(t);
      return r ? [[C('accent'), `sun ${r.rx_sun_deg}°`], [null, `Kp ${r.kp ?? '—'}`], [null, `X-ray ${r.xray_class || '—'}`]] : null;
    });
  }

  // ---- 4. table ----
  function drawTable() {
    const t = $('rpTable'); t.replaceChildren();
    const head = ['Beacon', 'Location', 'km', 'Bearing', ...BANDS.map(b => b + ' m'), 'Best S/N dB'];
    const tr = document.createElement('tr');
    head.forEach((s, i) => { const th = document.createElement('th'); th.textContent = s; if (i >= 2) th.className = 'n'; tr.appendChild(th); });
    const thead = document.createElement('thead'); thead.appendChild(tr); t.appendChild(thead);
    const tb = document.createElement('tbody'); t.appendChild(tb);
    for (const b of R.beacons) {
      const row = document.createElement('tr');
      const cells = [[b.beacon], [b.location], [num(b.km), 'n'], [b.bearing_deg + '°', 'n'],
        ...BANDS.map(m => { const v = b.bands[String(m)] || ''; return [v, 'n ' + (!v ? '' : v.startsWith('0/') ? 'zero' : 'hit')]; }),
        [b.best_snr_db == null ? '' : b.best_snr_db.toFixed(1), 'n']];
      for (const [v, c] of cells) { const td = document.createElement('td'); td.textContent = v; if (c) td.className = c; row.appendChild(td); }
      tb.appendChild(row);
    }
  }

  function draw() { drawBands(); drawPaths(); drawCond(); }
  draw(); drawTable();
  let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
})();
