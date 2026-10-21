"""NCDXF/IARU International Beacon Project monitor.

18 beacons share 5 frequencies in a 3-minute cycle of 10-second slots, timed from
00:00 UTC. In slot s (0..17) beacon (s - b) mod 18 sends on band b (0 = 20 m ... 4 = 10 m).
Each transmission: callsign at 22 WPM (54 ms elements), a word space, then four
1-second dashes separated by letter spaces, at 100 W, 10 W, 1 W and 0.1 W
(https://www.ncdxf.org/beacon/, beacon controller documentation).

The monitor tunes the radio (CW mode, so the beacon lands on the CW pitch), keeps
the audio of each slot, and measures the beacon's tone against the noise in its own
key-up gaps: SNR of each dash, so "heard down to 1 W" is a measurement, not a guess.
"""
import asyncio
import collections
import csv
import math
import os
import time

import numpy as np

import geo
import beaconlog
from beaconlog import BeaconLog
from spaceweather import SpaceWeather
from morse import FS, MORSE

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "logs", "beacons.csv")

BANDS = [("20", 14100000), ("17", 18110000), ("15", 21150000), ("12", 24930000), ("10", 28200000)]
BEACONS = [  # schedule order: call, location, grid
    ("4U1UN", "New York", "FN30as"), ("VE8AT", "Inuvik", "CP38gh"), ("W6WX", "Mt. Umunhum", "CM97bd"),
    ("KH6RS", "Maui", "BL10ts"), ("ZL6B", "Masterton", "RE78tw"), ("VK6RBP", "Rolystone", "OF87av"),
    ("JA2IGY", "Mt. Asama", "PM84jk"), ("RR9O", "Novosibirsk", "NO14kx"), ("VR2B", "Hong Kong", "OL72bg"),
    ("4S7B", "Colombo", "MJ96wv"), ("ZS6DN", "Pretoria", "KG33xi"), ("5Z4B", "Kikuyu", "KI88hr"),
    ("4X6TU", "Tel Aviv", "KM72jb"), ("OH2B", "Lohja", "KP20eh"), ("CS3B", "Madeira", "IM12jt"),
    ("LU4AA", "Buenos Aires", "GF05tj"), ("OA4B", "Lima", "FH17mw"), ("YV5B", "Caracas", "FK60nd"),
]
POWERS = ("100 W", "10 W", "1 W", "0.1 W")
DOT = 0.054                 # 22 WPM element
SLOT = 10.0
CHAR = {v: k for k, v in MORSE.items()}

# analysis
HOP = 0.02                  # envelope resolution
WIN = 480                   # 40 ms frames
NFFT = 1200                 # 10 Hz bins
SEARCH_HZ = 100             # beacon may be this far off the CW pitch (seen +50 Hz live)
HEARD_DB = 0.0              # a dash counts as heard at S/N >= 0 dB (tone power = noise power)
ID_DB = 6.0                 # callsign keying contrast needed; pure noise reaches ~5 dB
TUNE_EARLY = 0.4            # retune this long before the next slot starts
USE_UNTIL = SLOT - TUNE_EARLY - 0.1


def slot_of(t):
    """(absolute slot number, slot within the 3-minute cycle) for a UNIX time."""
    n = int(t // SLOT)
    return n, n % 18


def beacon_on(slot, band):
    return (slot - band) % 18


def band_of_beacon(slot, beacon):
    """Band index the beacon uses in this slot, or None while it is off our 5 bands."""
    b = (slot - beacon) % 18
    return b if b < 5 else None


def keying(call):
    """Key-down intervals (s from slot start): callsign elements, then the 4 dashes."""
    t, marks = 0.0, []
    for i, ch in enumerate(call):
        if i:
            t += 3 * DOT
        for j, e in enumerate(CHAR[ch]):
            if j:
                t += DOT
            d = DOT * (1 if e == "." else 3)
            marks.append((t, t + d))
            t += d
    t += 7 * DOT
    dashes = []
    for k in range(4):
        dashes.append((t, t + 1.0))
        t += 1.0 + 3 * DOT
    return marks, dashes


KEYING = [keying(c) for c, _, _ in BEACONS]


def envelope(x, pitch):
    """Per-frame power of bins within +-SEARCH_HZ+20 of pitch: (power[frames, bins], bin freqs)."""
    hop = int(HOP * FS)
    n = 1 + max(0, (len(x) - WIN) // hop)
    idx = np.arange(WIN)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(WIN)[None, :]
    spec = np.abs(np.fft.rfft(frames, NFFT, axis=1)) ** 2
    f = np.fft.rfftfreq(NFFT, 1 / FS)
    keep = np.abs(f - pitch) <= SEARCH_HZ + 20
    return spec[:, keep], f[keep]


def _mask(intervals, n, offset, shrink):
    """Frame mask for intervals (s), shifted by offset and shrunk at both ends."""
    m = np.zeros(n, bool)
    for a, b in intervals:
        i, j = int((a + offset + shrink) / HOP), int((b + offset - shrink) / HOP)
        if j > i:
            m[max(0, i):max(0, min(n, j))] = True
    return m


def analyze(x, pitch, beacon, t0_offset=0.0):
    """Measure one slot. x starts t0_offset s before the slot start.

    Returns tone frequency, timing offset, callsign contrast (dB, key-down vs key-up
    within the call: proves it is the beacon and not a carrier) and the SNR of each
    dash against the noise in the key-up gaps (S/N, floor -10 dB)."""
    marks, dashes = KEYING[beacon]
    spec, freqs = envelope(x, pitch)
    n = spec.shape[0]
    usable = np.arange(n) * HOP + WIN / FS / 2 < USE_UNTIL + t0_offset    # before we retune
    gaps = [(a[1], b[0]) for a, b in zip(marks, marks[1:]) if b[0] - a[1] > 2 * DOT]
    masks = []
    for off in np.arange(-0.1, 0.62, HOP):                     # audio path + clock latency
        on = _mask(marks + dashes[:1], n, off + t0_offset, 0.012) & usable
        off_ = _mask(gaps, n, off + t0_offset, 0.03) & usable
        if on.sum() >= 5 and off_.sum() >= 5:
            masks.append((off, on, off_))
    lp = 10 * np.log10(np.stack([spec[:, k - 1:k + 2].mean(axis=1)            # 30 Hz tracks
                                 for k in range(1, spec.shape[1] - 1)], 1) + 1e-12)
    best = None
    for off, on, off_ in masks:
        c = lp[on].mean(axis=0) - lp[off_].mean(axis=0)
        k = int(np.argmax(c))
        if best is None or c[k] > best[0]:
            best = (float(c[k]), k + 1, off)
    contrast, k, off = best
    p = spec[:, k - 1:k + 2].mean(axis=1)
    o = off + t0_offset
    quiet = gaps + [(dashes[-1][1] + 0.15, USE_UNTIL)]         # + tail after the 0.1 W dash
    noise = p[_mask(quiet, n, o, 0.04) & usable].mean()
    snr = []
    for a, b in dashes:
        m = _mask([(a, b)], n, o, 0.15) & usable
        r = p[m].mean() / noise - 1 if m.sum() >= 10 else None      # S/N in the 30 Hz track
        snr.append(None if r is None else round(10 * math.log10(max(r, 0.1)), 1))
    heard = 0
    for s in snr:                       # dashes heard in a row from 100 W down
        if s is None or s < HEARD_DB:
            break
        heard += 1
    return {"noise_db": round(10 * math.log10(noise + 1e-30), 1), "hz": round(float(freqs[k])), "offset": round(float(off), 2), "id_db": round(float(contrast), 1),
            "snr": snr, "dashes": heard if contrast >= ID_DB else 0}


class BeaconMonitor:
    """Steps the radio through the beacon schedule and measures every slot.

    modes: "cycle" hops band every slot (20,17,15,12,10,...): since 18 and 5 share no
    factor, all 90 beacon/band pairs are measured in 15 minutes. "band" stays on one
    band (all 18 beacons every 3 min). "follow" tracks one beacon up through the bands."""

    def __init__(self, app):
        self.app = app
        self.task = None
        self.cfg = {"mode": "cycle", "band": 0, "beacon": 0}
        self.audio = collections.deque()     # (t_first_sample, samples)
        self.results = {}                     # "beacon:band" -> latest measurement
        self.now = None                       # what the radio is tuned to this slot
        self.log = BeaconLog(self)             # JSONL propagation log (logs/ncdxf/)
        self.space = SpaceWeather()
        self.saved = None
        self.skip = set()                     # slots the radio was not on frequency for
        self._load_recent()

    def _load_recent(self, secs=900):
        """Results of the last 15 minutes from the slot log, so a restart keeps the table and map."""
        import calendar
        import json
        calls = {c: i for i, (c, _, _) in enumerate(BEACONS)}
        bands = {int(b): i for i, (b, _) in enumerate(BANDS)}
        cutoff = time.time() - secs
        for day in sorted({time.strftime("%Y-%m-%d", time.gmtime(t)) for t in (cutoff, time.time())}):
            path = os.path.join(beaconlog.LOG_DIR, f"slots-{day}.jsonl")
            if not os.path.exists(path):
                continue
            for line in open(path):
                try:
                    r = json.loads(line)
                    ts = calendar.timegm(time.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ"))
                    if ts < cutoff:
                        continue
                    res = r["result"]
                    b, band = calls[r["beacon"]["call"]], bands[r["band_m"]]
                    self.results[f"{b}:{band}"] = {
                        "ts": ts, "beacon": b, "band": band, "dashes": res["dashes_heard"],
                        "snr": [res["snr_db"][p] for p in POWERS], "id_db": res["id_contrast_db"],
                        "hz": (r["receiver"].get("cw_pitch_hz") or 0) + (res["tone_offset_hz"] or 0),
                        "offset": res["timing_offset_s"], "noise_db": res.get("noise_db_rel")}
                except (KeyError, ValueError, TypeError):
                    continue

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def public(self):
        me = self.app.my_location()
        rows = []
        for i, (call, loc, grid) in enumerate(BEACONS):
            km = brg = None
            p = geo.grid_to_latlon(grid)
            if me and p:
                km, brg = geo.dist_bearing(me, p)
            rows.append({"call": call, "loc": loc, "grid": grid, "lat": round(p[0], 2), "lon": round(p[1], 2),
                         "km": round(km) if km is not None else None, "brg": round(brg) if brg is not None else None})
        return {"active": self.active, "cfg": self.cfg, "now": self.now, "beacons": rows,
                "me": [round(me[0], 3), round(me[1], 3)] if me else None,
                "bands": [b for b, _ in BANDS], "powers": POWERS, "heard_db": HEARD_DB,
                "results": self.results, "last_pass": self.log.last_pass, "server_time": time.time()}

    def broadcast(self):
        self.app.broadcast_json(type="beacons", data=self.public())

    def feed(self, x, t_end):
        """Audio from the server (100 ms chunks, t_end = arrival time of the last sample)."""
        if not self.active:
            return
        self.audio.append((t_end - len(x) / FS, x))
        while self.audio and self.audio[0][0] < t_end - 30:
            self.audio.popleft()

    def clip(self, t0, t1):
        parts = [(t, x) for t, x in self.audio if t + len(x) / FS > t0 and t < t1]
        if not parts:
            return None, 0
        start = parts[0][0]
        x = np.concatenate([x for _, x in parts])
        i, j = max(0, int((t0 - start) * FS)), int((t1 - start) * FS)
        return x[i:j], max(0.0, start - t0)

    def start(self, cfg):
        if self.active:
            raise RuntimeError("beacon monitor already running")
        if not self.app.clock.get("ok"):
            raise RuntimeError("beacon monitor needs the PC clock NTP-synchronised (10 s slots): "
                               + self.app.clock_msg()["text"])
        if not self.app.radio.state["connected"] or not self.app.audio_ok:
            raise RuntimeError("beacon monitor needs the radio connected (CAT and USB audio)")
        if cfg.get("mode") in ("cycle", "band", "follow"):
            self.cfg["mode"] = cfg["mode"]
        for k, hi in (("band", 4), ("beacon", 17)):
            if k in cfg:
                self.cfg[k] = max(0, min(hi, int(cfg[k])))
        self.saved = self.app.radio.snapshot()  # radio-specific: restored on Stop
        self.task = asyncio.ensure_future(self._run())
        self.task.add_done_callback(lambda _: self.broadcast())

    async def stop(self):
        if self.active:
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass

    def _band_for(self, slot):
        c = self.cfg
        if c["mode"] == "band":
            return c["band"]
        if c["mode"] == "follow":
            return band_of_beacon(slot % 18, c["beacon"])
        return slot % 5                 # absolute slot: 18 and 5 coprime -> all 90 pairs in 15 min

    async def _run(self):
        radio = self.app.radio
        try:
            radio.cw_narrow()                   # beacon on the CW pitch, 500 Hz IF
            pitch = radio.state.get("cw_pitch") or 600
            last = 0
            while True:
                # next slot we can still tune for in time (never one already under way)
                n = max(last + 1, math.ceil((time.time() + TUNE_EARLY - 0.05) / SLOT))
                last = n
                band = self._band_for(n)
                beacon = None if band is None else beacon_on(n % 18, band)
                await asyncio.sleep(max(0.0, n * SLOT - TUNE_EARLY - time.time()))
                hz = BANDS[band if band is not None else 0][1]   # follow mode parks on 20 m
                if radio.state.get("freq") != hz:
                    radio.set_freq(hz)
                if beacon is not None:
                    asyncio.ensure_future(self._measure(n, band, beacon, pitch))
                self.now = {"band": band, "beacon": beacon, "slot_start": n * SLOT}
                self.broadcast()
                # the radio can drop a command while it streams scope data: check what it reports
                await asyncio.sleep(max(0.0, n * SLOT + 1.5 - time.time()))
                if radio.state.get("freq") != hz:
                    radio.set_freq(hz)
                    self.skip.add(n)
                    print(f"beacon monitor: radio on {radio.state.get('freq')} instead of {hz}, retuned; slot skipped")
        finally:
            self.now = None
            self.log.flush()                    # summary of the partial 15-minute window
            if self.saved:
                radio.restore(self.saved)

    async def _measure(self, n, band, beacon, pitch):
        """Analyse slot n once its usable audio is in."""
        t0 = n * SLOT
        radio = dict(self.app.radio.state)      # receiver settings during this slot
        await asyncio.sleep(max(0.0, t0 + USE_UNTIL + 0.35 - time.time()))   # audio arrival lag
        x, lead = self.clip(t0 - 0.1, t0 + USE_UNTIL + 0.3)
        if x is None or len(x) < FS * 8 or n in self.skip:
            self.skip.discard(n)
            return
        res = await asyncio.get_running_loop().run_in_executor(None, analyze, x, pitch, beacon, 0.1 - lead)
        res.update({"ts": t0, "beacon": beacon, "band": band})
        self.results[f"{beacon}:{band}"] = res
        self.broadcast()
        self._log(res)
        try:
            self.log.slot(res, pitch, radio, await self.space.get())
        except Exception as e:                 # the monitor must keep running
            print("beacon log:", repr(e))

    def _log(self, r):
        new = not os.path.exists(LOG_PATH)
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["utc", "band", "khz", "beacon", "dashes_heard", "snr_100w", "snr_10w", "snr_1w",
                            "snr_0w1", "id_db", "tone_hz", "offset_s"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["ts"])), BANDS[r["band"]][0],
                        BANDS[r["band"]][1] / 1000, BEACONS[r["beacon"]][0], r["dashes"], *r["snr"],
                        r["id_db"], r["hz"], r["offset"]])
