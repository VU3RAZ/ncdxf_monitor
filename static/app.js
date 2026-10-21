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
      td.title = `${b.call} on ${band} m, ${new Date(r.ts * 1000).toISOString().slice(11, 19)} UTC (${fmtAge(Math.round(age))} ago)\n`
        + bn.powers.map((p, k) => `${p}: ${fmtDb(r.snr[k])} dB`).join(' · ')
        + `\ncallsign keying ${r.id_db} dB${r.dashes ? '' : ' (needs 6 dB: not identified)'} · tone ${r.hz} Hz · timing ${r.offset} s`;
    });
  });
  const p = bn.last_pass;
  $('passInfo').textContent = p ? `${p.window_start_utc.slice(11, 16)}–${p.window_end_utc.slice(11, 16)} UTC · ${p.slots_measured} slots`
    + `${p.complete ? ' (all 90 pairs)' : ''} · ${p.heard_count} heard` : 'none yet (written every 15 minutes while running)';
  $('passText').textContent = p ? p.summary : '';
}
setInterval(() => renderBeacons(null), 30000);

connect();
