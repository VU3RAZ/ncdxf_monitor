'use strict';
const $ = (id) => document.getElementById(id);
const state = {};
let ws, bn = null, bnSkew = 0, radios = null;

// ------------------------------------------------------------ websocket
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => $('conn').classList.add('ok');
  ws.onclose = () => { $('conn').classList.remove('ok'); setTimeout(connect, 1500); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === 'state') { Object.assign(state, m.state); renderRadio(); }
    else if (m.type === 'radios') renderRadios(m);
    else if (m.type === 'clock') renderClock(m);
    else if (m.type === 'station') renderStation(m.station);
    else if (m.type === 'audio') renderAudio(m);
    else if (m.type === 'beacons') renderBeacons(m.data);
    else if (m.type === 'space') renderSpace(m.space);
    else if (m.type === 'error' || m.type === 'notice') toast(m.msg);
  };
}
function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }
function toast(msg) { const t = $('toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = 'none', 4000); }
function lsGet(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } }
const fmtAge = (s) => s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h`;
const fmtDb = (v) => v == null ? '–' : (v > 0 ? '+' : '') + v.toFixed(0);

// ------------------------------------------------------------ radio, clock, audio, station
function renderRadios(m) {
  radios = m;
  $('radioSel').innerHTML = m.radios.map((r) => `<option value="${r.name}">${r.name}${r.connected ? '' : ' (not detected)'}</option>`).join('');
  $('radioSel').value = m.current;
}
$('radioSel').onchange = () => {
  const name = $('radioSel').value;
  if (radios) $('radioSel').value = radios.current;      // updated once the server has switched
  send({ cmd: 'select_radio', name });
};
function renderRadio() {
  $('freq').textContent = state.freq ? (state.freq / 1e6).toFixed(3) + ' MHz' : '—';
  $('freq').classList.toggle('off', !state.connected);
  $('modeBadge').textContent = state.connected ? `${state.mode || '—'}${state.data ? '-D' : ''}${state.if_width ? ' ' + state.if_width + ' Hz' : ''}` : 'radio not connected';
  const s = state.smeter || 0;     // Icom scale: S9 = 120, S9+60 = 241
  $('smeter').textContent = s <= 120 ? `S${Math.round(s / 120 * 9)}` : `S9+${Math.round((s - 120) / 121 * 60)}`;
}
function renderClock(m) {
  const c = m.clock, el = $('clockBadge');
  el.textContent = c.ok ? `NTP ✓${c.offset_ms != null ? ' ' + Math.abs(c.offset_ms).toFixed(1) + ' ms' : ''}` : 'NTP ✗';
  el.className = 'badge ' + (c.ok ? 'ok' : 'bad');
  el.title = `${m.text}\n(checked ${c.checked_utc}; the 10 s beacon slots need an NTP-synced clock)`;
}
function renderAudio(m) {
  $('audioBadge').textContent = m.ok ? 'audio ✓' : 'audio ✗';
  $('audioBadge').className = 'badge ' + (m.ok ? 'ok' : 'bad');
  if (m.node) $('audioBadge').title = 'Receive audio: ' + m.node;
}
function renderStation(st) {
  if (document.activeElement !== $('stCall')) $('stCall').value = st.call || '';
  if (document.activeElement !== $('stGrid')) $('stGrid').value = st.grid || '';
}
$('stSave').onclick = () => send({ cmd: 'station', call: $('stCall').value.trim(), grid: $('stGrid').value.trim() });
for (const id of ['stCall', 'stGrid']) $(id).addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('stSave').click(); });

// ------------------------------------------------------------ beacon matrix
function bnCfg() { return { mode: $('bnMode').value, band: +$('bnBand').value, beacon: +$('bnBeacon').value }; }
function bnShowPickers() {
  $('bnBand').hidden = $('bnMode').value !== 'band';
  $('bnBeacon').hidden = $('bnMode').value !== 'follow';
}
for (const id of ['bnMode', 'bnBand', 'bnBeacon']) $(id).addEventListener('change', () => { lsSet(id, $(id).value); bnShowPickers(); });
$('bnStart').onclick = () => send({ cmd: 'beacon_start', cfg: bnCfg() });
$('bnStop').onclick = () => send({ cmd: 'beacon_stop' });

function renderBeacons(d) {
  if (d) bnSkew = Date.now() / 1000 - d.server_time;
  if (!bn && d) {                 // first data: pickers and table header
    $('bnBand').innerHTML = d.bands.map((b, i) => `<option value="${i}">${b} m</option>`).join('');
    $('bnBeacon').innerHTML = d.beacons.map((b, i) => `<option value="${i}">${b.call} ${b.loc}</option>`).join('');
    $('bnMode').value = lsGet('bnMode', d.cfg.mode); $('bnBand').value = lsGet('bnBand', d.cfg.band); $('bnBeacon').value = lsGet('bnBeacon', d.cfg.beacon);
    bnShowPickers();
    $('bnHead').innerHTML = '<th>Beacon</th><th>Location</th><th>km</th><th>Brg</th>' + d.bands.map((b) => `<th>${b} m</th>`).join('');
  }
  if (d) bn = d;
  if (!bn) return;
  const now = Date.now() / 1000 - bnSkew, cur = bn.now;
  $('bnStart').disabled = bn.active; $('bnStop').disabled = !bn.active;
  $('radioSel').disabled = bn.active;
  const heard = Object.values(bn.results).filter((r) => r.dashes > 0 && now - r.ts < 900).length;
  $('bnInfo').textContent = (bn.active ? (cur && cur.beacon != null
      ? `now ${bn.bands[cur.band]} m: ${bn.beacons[cur.beacon].call} (${bn.beacons[cur.beacon].loc})`
      : 'waiting for the beacon to reach our bands') : 'stopped') + ` · ${heard} heard in the last 15 min`;
  const body = $('bnBody'); body.textContent = '';
  bn.beacons.forEach((b, i) => {
    const tr = body.insertRow();
    for (const v of [b.call, b.loc, b.km ?? '', b.brg != null ? b.brg + '°' : '']) tr.insertCell().textContent = v;
    bn.bands.forEach((band, j) => {
      const td = tr.insertCell(), r = bn.results[`${i}:${j}`];
      td.className = 'cell';
      if (cur && cur.band === j && cur.beacon === i) td.classList.add('now');
      if (!r) { td.title = 'not measured yet'; return; }
      const age = now - r.ts;
      if (age > 900) td.classList.add('old');
      const bars = [0, 1, 2, 3].map((k) => `<i class="${k < r.dashes ? 'on' : ''}"></i>`).join('');
      td.innerHTML = `<span class="bars">${bars}</span> <span class="${r.dashes ? '' : 'dim'}">${r.dashes ? fmtDb(r.snr[0]) : '–'}</span>`;
      td.title = `${b.call} on ${band} m, ${new Date(r.ts * 1000).toISOString().slice(11, 19)} UTC, ${localHms(r.ts)} local (${fmtAge(Math.round(age))} ago)\n`
        + bn.powers.map((p, k) => `${p}: ${fmtDb(r.snr[k])} dB`).join(' · ')
        + `\ncallsign keying ${r.id_db} dB${r.dashes ? '' : ' (needs 6 dB: not identified)'} · tone ${r.hz} Hz · timing ${r.offset} s`;
    });
  });
  const p = bn.last_pass;
  $('passInfo').textContent = p ? `${p.window_start_utc.slice(11, 16)}–${p.window_end_utc.slice(11, 16)} UTC`
    + ` (${localHm(p.window_start_utc)}–${localHm(p.window_end_utc)} local) · ${p.slots_measured} slots`
    + `${p.complete ? ' (all 90 pairs)' : ''} · ${p.heard_count} heard` : 'none yet (written every 15 minutes while running)';
  $('passText').textContent = p ? p.summary : '';
  drawMap();
  renderOutlook(bn.outlook);
}
setInterval(() => renderBeacons(null), 30000);

// ------------------------------------------------------------ UTC + local time
const tzName = (() => { try { return new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(new Date()).find((x) => x.type === 'timeZoneName').value; } catch { return 'local'; } })();
const localHms = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const localHm = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
function renderTime() {
  const now = new Date();
  $('clockTime').innerHTML = `<span>${now.toISOString().slice(11, 19)} <small>UTC</small></span>`
    + `<span>${localHms(now / 1000)} <small>${tzName}</small></span>`;
}
setInterval(renderTime, 1000); renderTime();

// ------------------------------------------------------------ outlook: MUF (measured) + SSB chance (estimate)
const pct = (p) => `${Math.round(p * 100)} %`;
const dirName = (brg) => ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(brg / 45) % 8];
function mufText(p) {
  if (!p.muf_low_mhz) return 'not heard';
  return p.muf_high_mhz ? `${p.muf_low_mhz.toFixed(2)}–${p.muf_high_mhz.toFixed(2)} MHz` : `≥ ${p.muf_low_mhz.toFixed(2)} MHz`;
}
function renderOutlook(o) {
  if (!o) return;
  const heard = o.paths.filter((p) => p.best), silent = o.paths.filter((p) => !p.best);
  const top = heard[0];
  let rec;
  if (top && top.best.ssb_chance >= 0.5) {
    const alts = heard.slice(1).filter((p) => p.best.ssb_chance >= 0.5).map((p) => `${p.best.band} m ${dirName(p.brg)} (${p.loc})`);
    rec = `SSB: try ${top.best.band} m toward ${dirName(top.brg)} ${top.brg}° (${top.loc}) — estimated ${pct(top.best.ssb_chance)}`
      + (alts.length ? `; also ${alts.join(', ')}` : '') + '.';
  } else if (top) {
    const hi = heard.reduce((a, p) => (p.muf_low_mhz > a.muf_low_mhz ? p : a));
    rec = `Paths are open but weak for SSB (best estimate ${pct(top.best.ssb_chance)}: ${top.best.band} m to ${top.loc}, `
      + `${top.best.snr_ssb_db != null ? (top.best.snr_ssb_db > 0 ? '+' : '') + top.best.snr_ssb_db + ' dB' : '?'} in 2.4 kHz) — CW or FT8 will do better. `
      + `Highest band heard: ${hi.muf_low_mhz.toFixed(2)} MHz toward ${dirName(hi.brg)} (${hi.loc}).`;
  } else if (o.paths.length) {
    rec = `No beacon heard on 20–10 m in the last ${o.window_min} min (${silent.length} paths measured): these bands look closed from here right now. The beacons don't cover 40 m and below.`;
  } else rec = 'No measurements in the last hour yet — start the monitor.';
  $('olRec').textContent = rec;
  $('olBands').innerHTML = o.bands.map((b) => {
    const cls = b.best_ssb_chance >= 0.5 ? 'good' : b.beacons_heard.length ? 'weak' : b.beacons_measured ? 'closed' : '';
    const title = b.beacons_measured ? `${b.band} m: ${b.beacons_heard.length} of ${b.beacons_measured} beacons heard`
      + (b.beacons_heard.length ? ` (${b.beacons_heard.join(', ')}); best SSB estimate ${pct(b.best_ssb_chance)}` : '') : 'not measured';
    return `<span class="chip ${cls}" title="${title}">${b.band} m <b>${b.beacons_heard.length}/${b.beacons_measured}</b></span>`;
  }).join('');
  const body = $('olBody'); body.textContent = '';
  for (const p of heard) {
    const tr = body.insertRow(), best = p.best;
    const cells = [`${p.call} ${p.loc}`, `${dirName(p.brg)} ${p.brg}°`, p.km, mufText(p), `${best.band} m`, '',
                   Object.entries(p.tries).map(([b, t]) => `${b}:${t}`).join(' ')];
    cells.forEach((v, k) => { tr.insertCell().textContent = v; });
    const c = tr.cells[5];
    c.innerHTML = `<span class="pbar"><i style="width:${Math.round(best.ssb_chance * 100)}%"></i></span> ${pct(best.ssb_chance)}`;
    c.title = `${best.band} m: 100 W dash ${fmtDb(best.snr_30hz_db)} dB in 30 Hz = ${best.snr_ssb_db} dB in 2.4 kHz; heard ${best.heard}/${best.tries}`;
  }
  if (!heard.length) { const td = body.insertRow().insertCell(); td.colSpan = 7; td.className = 'hint'; td.textContent = 'no beacon heard in the last hour'; }
  $('olInfo').textContent = `last ${o.window_min} min · ${heard.length} of ${o.paths.length} measured paths heard`
    + (silent.length && heard.length ? ` · not heard: ${silent.map((p) => p.call).join(', ')}` : '');
}

// ------------------------------------------------------------ sun position (same almanac as beaconlog.py)
const RAD = Math.PI / 180;
function sunPos(t) {                 // t: UNIX seconds -> {dec, gha} in radians (gha: Greenwich hour angle of the sun)
  const d = t / 86400 + 2440587.5 - 2451545.0;
  const g = (357.529 + 0.98560028 * d) * RAD, q = 280.459 + 0.98564736 * d;
  const lam = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * RAD, eps = (23.439 - 0.00000036 * d) * RAD;
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam)), dec = Math.asin(Math.sin(eps) * Math.sin(lam));
  const gmst = ((18.697374558 + 24.06570982441908 * d) % 24) * 15 * RAD;
  return { dec, gha: gmst - ra };
}
function sunElev(lat, lon, s) {
  const la = lat * RAD, ha = s.gha + lon * RAD;
  return Math.asin(Math.sin(la) * Math.sin(s.dec) + Math.cos(la) * Math.cos(s.dec) * Math.cos(ha)) / RAD;
}

// ------------------------------------------------------------ space weather header
function renderSpace(sw) {
  if (!sw || sw.sfi == null) return;
  const set = (id, text, cls, title) => { const el = $(id); el.textContent = text; el.className = 'badge ' + cls; el.title = title; };
  set('sfiBadge', `SFI ${sw.sfi}`, sw.sfi >= 150 ? 'ok' : '', `10.7 cm solar flux ${sw.sfi} (${sw.sfi_utc}); higher = higher bands open better`);
  set('kpBadge', `Kp ${sw.kp.toFixed(1)}`, sw.kp >= 5 ? 'bad' : sw.kp >= 4 ? 'warn' : 'ok',
      `planetary K index ${sw.kp} for the 3 h from ${sw.kp_utc}, A ${sw.a_running}; 24 h max ${sw.kp_24h_max}\n`
      + 'Kp 4 = active, 5+ = geomagnetic storm (degrades high-latitude and polar paths)');
  const xl = (sw.xray_class || '?')[0];
  set('xrayBadge', `X-ray ${sw.xray_class}`, xl === 'X' ? 'bad' : xl === 'M' ? 'warn' : 'ok',
      `GOES X-ray flux now ${sw.xray_class} (${sw.xray_utc}), 6 h max ${sw.xray_6h_max_class}\n`
      + 'M/X flares cause short-wave fadeouts on the sunlit side of the Earth');
  const sc = sw.noaa_scales || {}, fmt = (k) => `${k}${sc[k] ?? '–'}`;
  set('scalesBadge', ['R', 'S', 'G'].map(fmt).join(' '), ['R', 'S', 'G'].some((k) => sc[k] >= 1) ? 'warn' : 'ok',
      'NOAA space weather scales now: R radio blackout, S solar radiation storm, G geomagnetic storm (0 = none, 1-5)'
      + `\nfetched ${sw.fetched_utc}${sw.errors ? '\nerrors: ' + JSON.stringify(sw.errors) : ''}`);
}
function renderSun() {
  const me = bn && bn.me;
  if (!me) { $('sunBadge').textContent = 'Sun —'; return; }
  const e = sunElev(me[0], me[1], sunPos(Date.now() / 1000));
  const st = e > 6 ? 'day' : e >= -6 ? 'greyline' : 'night';
  $('sunBadge').textContent = `Sun ${e >= 0 ? '+' : ''}${e.toFixed(0)}° ${st}`;
  $('sunBadge').className = 'badge ' + (st === 'greyline' ? 'warn' : '');
  $('sunBadge').title = 'Solar elevation at your station (day > 6°, greyline −6…6°, night < −6°)';
}
setInterval(renderSun, 60000);

// ------------------------------------------------------------ map
const map = { land: null, shade: null, shadeKey: '', pts: [] };
fetch('/static/land.json').then((r) => r.json()).then((d) => { map.land = d; drawMap(); }).catch(() => {});
$('mapBand').innerHTML += ['20', '17', '15', '12', '10'].map((b, i) => `<option value="${i}">${b} m</option>`).join('');
for (const id of ['mapProj', 'mapBand']) { $(id).value = lsGet(id, $(id).value); $(id).onchange = () => { lsSet(id, $(id).value); map.shadeKey = ''; drawMap(); }; }
$('mapMiss').checked = lsGet('mapMiss', true);
$('mapMiss').onchange = () => { lsSet('mapMiss', $('mapMiss').checked); drawMap(); };
window.addEventListener('resize', () => { map.shadeKey = ''; drawMap(); });
setInterval(() => { map.shadeKey = ''; drawMap(); }, 60000);   // terminator moves

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function gcPoint(a, b, f) {          // point at fraction f along the great circle a->b ([lat, lon] degrees)
  const [la1, lo1, la2, lo2] = [a[0], a[1], b[0], b[1]].map((x) => x * RAD);
  const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
  if (d < 1e-9) return a;
  const A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
  const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
  const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
  const z = A * Math.sin(la1) + B * Math.sin(la2);
  return [Math.atan2(z, Math.hypot(x, y)) / RAD, Math.atan2(y, x) / RAD];
}
function projector(W, H, me) {
  if ($('mapProj').value === 'azim' && me) {          // azimuthal equidistant centred on the station
    const R = Math.min(W, H) / 2 - 2, cx = W / 2, cy = H / 2, la0 = me[0] * RAD, lo0 = me[1] * RAD;
    return {
      R, cx, cy, azim: true,
      fwd(lat, lon) {
        const la = lat * RAD, dl = lon * RAD - lo0;
        const c = Math.acos(Math.max(-1, Math.min(1, Math.sin(la0) * Math.sin(la) + Math.cos(la0) * Math.cos(la) * Math.cos(dl))));
        const az = Math.atan2(Math.sin(dl) * Math.cos(la), Math.cos(la0) * Math.sin(la) - Math.sin(la0) * Math.cos(la) * Math.cos(dl));
        const r = c / Math.PI * R;
        return [cx + r * Math.sin(az), cy - r * Math.cos(az)];
      },
      inv(x, y) {
        const dx = x - cx, dy = cy - y, r = Math.hypot(dx, dy);
        if (r > R) return null;
        const c = r / R * Math.PI, az = Math.atan2(dx, dy);
        const la = Math.asin(Math.sin(la0) * Math.cos(c) + Math.cos(la0) * Math.sin(c) * Math.cos(az));
        const lo = lo0 + Math.atan2(Math.sin(az) * Math.sin(c) * Math.cos(la0), Math.cos(c) - Math.sin(la0) * Math.sin(la));
        return [la / RAD, ((lo / RAD + 540) % 360) - 180];
      },
    };
  }
  return {                                             // equirectangular world
    azim: false,
    fwd: (lat, lon) => [(lon + 180) / 360 * W, (90 - lat) / 180 * H],
    inv: (x, y) => [90 - y / H * 180, x / W * 360 - 180],
  };
}
function nightShade(g, P, W, H) {
  const key = `${$('mapProj').value}:${W}:${H}:${bn && bn.me}`;
  if (map.shadeKey !== key) {
    const step = 3, img = g.createImageData(W, H), s = sunPos(Date.now() / 1000);
    for (let y = 0; y < H; y += step) {
      for (let x = 0; x < W; x += step) {
        const ll = P.inv(x + step / 2, y + step / 2);
        if (!ll) continue;
        const e = sunElev(ll[0], ll[1], s);
        const a = e > 0 ? 0 : e > -6 ? 70 : e > -12 ? 110 : 140;   // greyline, then darker night
        if (!a) continue;
        for (let yy = y; yy < Math.min(H, y + step); yy++) {
          for (let xx = x; xx < Math.min(W, x + step); xx++) {
            const i = (yy * W + xx) * 4; img.data[i + 3] = a;
          }
        }
      }
    }
    map.shade = img; map.shadeKey = key;
  }
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  c.getContext('2d').putImageData(map.shade, 0, 0);
  g.drawImage(c, 0, 0);
}
function bestResult(i, now) {
  const band = $('mapBand').value;
  let best = null;
  for (let j = 0; j < 5; j++) {
    if (band !== 'best' && +band !== j) continue;
    const r = bn.results[`${i}:${j}`];
    if (!r || now - r.ts > 900) continue;
    if (!best || r.dashes > best.dashes || (r.dashes === best.dashes && (r.snr[0] ?? -99) > (best.snr[0] ?? -99))) best = r;
  }
  return best;
}
function drawMap() {
  const c = $('map'), dpr = devicePixelRatio || 1;
  const W0 = c.clientWidth || 600, azim = $('mapProj').value === 'azim';
  const H0 = azim ? Math.min(W0, 620) : Math.round(W0 / 2);
  c.style.height = H0 + 'px';
  const W = Math.round(W0 * dpr), H = Math.round(H0 * dpr);
  if (c.width !== W || c.height !== H) { c.width = W; c.height = H; map.shadeKey = ''; }
  const g = c.getContext('2d');
  g.clearRect(0, 0, W, H);
  const me = bn && bn.me, P = projector(W, H, me);
  // ocean (disc for azimuthal), graticule
  g.fillStyle = '#0b1826';
  if (P.azim) { g.beginPath(); g.arc(P.cx, P.cy, P.R, 0, 2 * Math.PI); g.fill(); } else g.fillRect(0, 0, W, H);
  g.strokeStyle = 'rgba(134,145,160,.12)'; g.lineWidth = dpr;
  if (P.azim) {
    for (const km of [5000, 10000, 15000]) { g.beginPath(); g.arc(P.cx, P.cy, km / 20015 * P.R, 0, 2 * Math.PI); g.stroke(); }
    for (let a = 0; a < 360; a += 30) { g.beginPath(); g.moveTo(P.cx, P.cy); g.lineTo(P.cx + P.R * Math.sin(a * RAD), P.cy - P.R * Math.cos(a * RAD)); g.stroke(); }
  } else {
    for (let lo = -150; lo <= 150; lo += 30) { const [x] = P.fwd(0, lo); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); }
    for (let la = -60; la <= 60; la += 30) { const [, y] = P.fwd(la, 0); g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
  }
  // land
  if (map.land) {
    g.fillStyle = '#1d2a36'; g.strokeStyle = '#33465a'; g.lineWidth = dpr * 0.8;
    for (const ring of map.land) {
      g.beginPath();
      let prev = null, broken = false;
      for (const [lon, lat] of ring) {
        const p = P.fwd(lat, lon);
        if (prev && Math.hypot(p[0] - prev[0], p[1] - prev[1]) > (P.azim ? P.R * 0.6 : W * 0.5)) { g.moveTo(p[0], p[1]); broken = true; }
        else if (!prev) g.moveTo(p[0], p[1]); else g.lineTo(p[0], p[1]);
        prev = p;
      }
      if (!broken) { g.closePath(); g.fill(); }
      g.stroke();
    }
  }
  nightShade(g, P, W, H);
  if (P.azim) {                                   // compass labels
    g.fillStyle = 'rgba(134,145,160,.7)'; g.font = `${11 * dpr}px system-ui`; g.textAlign = 'center';
    for (const [t, a] of [['N', 0], ['E', 90], ['S', 180], ['W', 270]]) g.fillText(t, P.cx + (P.R - 10 * dpr) * Math.sin(a * RAD), P.cy - (P.R - 10 * dpr) * Math.cos(a * RAD) + 4 * dpr);
  }
  if (!bn) return;
  const now = Date.now() / 1000 - bnSkew, cur = bn.now, band = $('mapBand').value;
  const colours = [cssVar('--miss'), cssVar('--d1'), cssVar('--d2'), cssVar('--d3'), cssVar('--d4')];
  map.pts = [];
  // paths: not heard first (underneath), heard on top, weakest first
  const items = bn.beacons.map((b, i) => ({ b, i, r: bestResult(i, now) }))
    .sort((x, y) => (x.r ? x.r.dashes + 1 : 0) - (y.r ? y.r.dashes + 1 : 0));
  if (me) {
    for (const { b, r } of items) {
      if (!r || (!r.dashes && !$('mapMiss').checked)) continue;
      g.strokeStyle = colours[r.dashes]; g.lineWidth = (r.dashes ? 2.4 : 1) * dpr;
      g.setLineDash(r.dashes ? [] : [4 * dpr, 4 * dpr]);
      g.beginPath();
      let prev = null;
      for (let k = 0; k <= 64; k++) {
        const ll = gcPoint(me, [b.lat, b.lon], k / 64), p = P.fwd(ll[0], ll[1]);
        if (!prev || Math.abs(p[0] - prev[0]) > W * 0.5) g.moveTo(p[0], p[1]); else g.lineTo(p[0], p[1]);
        prev = p;
      }
      g.stroke();
    }
    g.setLineDash([]);
  }
  // beacons
  g.font = `${11 * dpr}px system-ui`; g.textAlign = 'left';
  for (const { b, i, r } of items) {
    const [x, y] = P.fwd(b.lat, b.lon);
    const sending = cur && cur.beacon === i && (band === 'best' || +band === cur.band);
    g.fillStyle = r ? colours[r.dashes] : '#8691a0';
    g.beginPath(); g.arc(x, y, 4 * dpr, 0, 2 * Math.PI); g.fill();
    if (sending) { g.strokeStyle = cssVar('--accent'); g.lineWidth = 2 * dpr; g.beginPath(); g.arc(x, y, 9 * dpr, 0, 2 * Math.PI); g.stroke(); }
    g.fillStyle = r && r.dashes ? '#e6edf3' : 'rgba(215,221,228,.6)';
    g.fillText(b.call, x + 6 * dpr, y - 5 * dpr);
    map.pts.push({ x: x / dpr, y: y / dpr, b, r });
  }
  // station
  if (me) {
    const [x, y] = P.fwd(me[0], me[1]);
    g.fillStyle = cssVar('--accent'); g.strokeStyle = '#0d1117'; g.lineWidth = 2 * dpr;
    g.beginPath(); g.moveTo(x, y - 7 * dpr); g.lineTo(x + 6 * dpr, y + 5 * dpr); g.lineTo(x - 6 * dpr, y + 5 * dpr); g.closePath(); g.fill(); g.stroke();
    g.fillStyle = cssVar('--accent'); g.font = `bold ${11 * dpr}px system-ui`;
    g.fillText(($('stCall').value || 'You').toUpperCase(), x + 8 * dpr, y + 4 * dpr);
  }
  renderSun();
}
$('map').addEventListener('mousemove', (ev) => {
  const rect = $('map').getBoundingClientRect(), mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
  let best = null, bd = 12;
  for (const p of map.pts) { const d = Math.hypot(p.x - mx, p.y - my); if (d < bd) { bd = d; best = p; } }
  const tip = $('mapTip');
  if (!best) { tip.hidden = true; return; }
  const { b, r } = best, now = Date.now() / 1000 - bnSkew;
  tip.textContent = `${b.call}  ${b.loc}\n${b.km ?? '?'} km, ${b.brg ?? '?'}° from you`
    + (r ? `\n${bn.bands[r.band]} m: ${r.dashes ? 'heard to ' + bn.powers[r.dashes - 1] + `, 100 W ${fmtDb(r.snr[0])} dB` : 'not heard'} (${fmtAge(Math.round(now - r.ts))} ago)`
      : '\nnot measured in the last 15 min');
  tip.hidden = false;
  tip.style.left = Math.min(mx + 12, rect.width - tip.offsetWidth - 4) + 'px';
  tip.style.top = Math.max(4, my - tip.offsetHeight - 8) + 'px';
});
$('map').addEventListener('mouseleave', () => { $('mapTip').hidden = true; });

connect();
