"""Propagation log of the NCDXF beacon monitor, written for later analysis by an LLM.

logs/ncdxf/slots-YYYY-MM-DD.jsonl   one JSON object per measured 10 s slot
logs/ncdxf/passes-YYYY-MM-DD.jsonl  one summary per 15-minute UTC window (00/15/30/45)
docs/ncdxf-log-format.md            field reference and method

Every record is self-contained: path geometry, sun elevation at both ends and the path
midpoint, receiver settings and NOAA space weather travel with the measurement."""
import json
import math
import os
import time

import geo

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs", "ncdxf")
FORMAT = "ncdxf-propagation-log/1"
WINDOW = 15 * 60


def iso(t, local=False):
    """ISO 8601 time: UTC ('...Z'), or the PC's local time zone with its offset."""
    if not local:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    s = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))
    return s[:-2] + ":" + s[-2:]


def sun_elevation(lat, lon, t):
    """Solar elevation in degrees (low-precision almanac, ~0.1 deg)."""
    d = t / 86400 + 2440587.5 - 2451545.0
    g = math.radians(357.529 + 0.98560028 * d)
    q = 280.459 + 0.98564736 * d
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians(gmst * 15 + lon) - ra
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def daylight(elev):
    return "day" if elev > 6 else "greyline" if elev >= -6 else "night"


def midpoint(a, b):
    """Great-circle midpoint of (lat, lon) a and b."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    bx, by = math.cos(la2) * math.cos(lo2 - lo1), math.cos(la2) * math.sin(lo2 - lo1)
    lat = math.atan2(math.sin(la1) + math.sin(la2), math.sqrt((math.cos(la1) + bx) ** 2 + by ** 2))
    lon = lo1 + math.atan2(by, math.cos(la1) + bx)
    return math.degrees(lat), (math.degrees(lon) + 540) % 360 - 180


def sun_block(rx, tx, t):
    mid = midpoint(rx, tx)
    out = {}
    for k, p in (("rx", rx), ("tx", tx), ("path_midpoint", mid)):
        e = sun_elevation(p[0], p[1], t)
        out[k] = {"elevation_deg": round(e, 1), "state": daylight(e)}
    return out, mid


class BeaconLog:
    def __init__(self, monitor):
        self.mon = monitor
        self.window = None           # start time of the current 15-minute window
        self.slots = []              # slot records in it
        self.last_pass = None        # the latest window summary (shown on the page)

    # ---------------------------------------------------------- per slot
    def slot(self, res, pitch, radio, space):
        from beacons import BANDS, BEACONS, POWERS, HEARD_DB, ID_DB
        app = self.mon.app
        station = app.station
        t = res["ts"]
        call, loc, grid = BEACONS[res["beacon"]]
        band, hz = BANDS[res["band"]]
        tx = geo.grid_to_latlon(grid)
        rx = app.my_location()
        rec = {
            "type": "slot", "format": FORMAT,
            "utc": iso(t), "rx_local_time": iso(t, local=True),
            "band_m": int(band), "freq_khz": hz / 1000,
            "beacon": {"call": call, "location": loc, "grid": grid, "country": (geo.lookup(call) or {}).get("country"),
                       "lat": round(tx[0], 2), "lon": round(tx[1], 2)},
            "rx_station": {"call": station.get("call") or None, "grid": station.get("grid") or None,
                           "lat": round(rx[0], 2) if rx else None, "lon": round(rx[1], 2) if rx else None},
        }
        if rx:
            km, brg = geo.dist_bearing(rx, tx)
            sun, mid = sun_block(rx, tx, t + 5)
            rec["path"] = {"km": round(km), "bearing_from_rx_deg": round(brg),
                           "midpoint": {"lat": round(mid[0], 1), "lon": round(mid[1], 1)}, "type": "short path"}
            rec["sun"] = sun
        snr = dict(zip(POWERS, res["snr"]))
        n = res["dashes"]
        rec["result"] = {
            "heard": n > 0,
            "dashes_heard": n,
            "weakest_power_heard": POWERS[n - 1] if n else None,
            "snr_db": snr,
            "id_contrast_db": res["id_db"],
            "tone_offset_hz": round(res["hz"] - pitch),
            "timing_offset_s": res["offset"],
            "noise_db_rel": res.get("noise_db"),
        }
        rec["receiver"] = {k: radio.get(k) for k in ("mode", "filter", "preamp", "att", "agc", "nb", "nr")}
        rec["receiver"]["rf_gain_pct"] = round(radio.get("rf", 0) / 255 * 100)
        rec["receiver"]["cw_pitch_hz"] = pitch
        rec["receiver"]["radio"] = radio.get("radio")
        rec["receiver"]["if_width_hz"] = radio.get("if_width") or None
        rec["space_weather"] = space
        c = getattr(app, "clock", {}) or {}
        rec["pc_clock"] = {"ntp_synced": c.get("synced"), "offset_ms": c.get("offset_ms"),
                           "error_bound_ms": c.get("error_bound_ms"), "ntp_reference": c.get("reference")}
        rec["monitor"] = {"mode": self.mon.cfg["mode"], "heard_threshold_snr_db": HEARD_DB,
                          "id_threshold_db": ID_DB}
        self._append("slots", t, rec)
        w = t - t % WINDOW
        if self.window is not None and w != self.window:
            self.flush()
        self.window = w
        self.slots.append(rec)

    # ---------------------------------------------------------- per 15 minutes
    def flush(self):
        """Write the summary of the current window (also called on Stop, possibly partial)."""
        if not self.slots:
            return
        from beacons import BANDS, BEACONS
        recs, self.slots = self.slots, []
        w = self.window
        last = recs[-1]
        bands = [b for b, _ in BANDS]
        matrix = {c: {b: None for b in bands} for c, _, _ in BEACONS}
        per_band = {b: {"measured": 0, "heard": []} for b in bands}
        for r in recs:
            b = str(r["band_m"])
            res = r["result"]
            matrix[r["beacon"]["call"]][b] = res["dashes_heard"]
            per_band[b]["measured"] += 1
            if res["heard"]:
                per_band[b]["heard"].append({
                    "call": r["beacon"]["call"], "location": r["beacon"]["location"],
                    "km": r.get("path", {}).get("km"), "bearing_deg": r.get("path", {}).get("bearing_from_rx_deg"),
                    "weakest_power_heard": res["weakest_power_heard"], "snr_100w_db": res["snr_db"]["100 W"],
                    "utc": r["utc"]})
        for b in per_band.values():
            b["heard"].sort(key=lambda h: -(h["snr_100w_db"] or -99))
        heard = [h for b in per_band.values() for h in b["heard"]]
        brgs = sorted({h["bearing_deg"] for h in heard if h["bearing_deg"] is not None})
        rx = self.mon.app.my_location()
        rec = {
            "type": "pass", "format": FORMAT,
            "window_start_utc": iso(w), "window_end_utc": iso(w + WINDOW),
            "rx_local_time": iso(w + WINDOW / 2, local=True),
            "rx_station": last["rx_station"],
            "monitor_mode": last["monitor"]["mode"],
            "slots_measured": len(recs),
            "complete": len(recs) >= 90 and last["monitor"]["mode"] == "cycle",
            "heard_count": len(heard),
            "rx_sun": ({"elevation_deg": round(sun_elevation(rx[0], rx[1], w + WINDOW / 2), 1),
                        "state": daylight(sun_elevation(rx[0], rx[1], w + WINDOW / 2))} if rx else None),
            "space_weather": last["space_weather"],
            "bands": per_band,
            "heard_bearings_deg": brgs,
            "matrix_dashes_heard": matrix,
            "summary": self._text(per_band, len(recs), brgs),
        }
        self._append("passes", w, rec)
        self.last_pass = {k: rec[k] for k in ("window_start_utc", "window_end_utc", "slots_measured", "complete",
                                              "heard_count", "summary")}

    @staticmethod
    def _text(per_band, n, brgs):
        parts = []
        for b, v in per_band.items():
            if not v["measured"]:
                continue
            if v["heard"]:
                hs = ", ".join(f"{h['call']} ({h['location']}, {h['km']} km, {h['bearing_deg']}°) "
                               f"down to {h['weakest_power_heard']}, 100 W at {h['snr_100w_db']:+.0f} dB"
                               for h in v["heard"])
                parts.append(f"{b} m: {len(v['heard'])}/{v['measured']} heard: {hs}")
            else:
                parts.append(f"{b} m: none of {v['measured']} heard")
        s = f"{n} slots measured. " + "; ".join(parts) + "."
        if brgs:
            s += f" Heard bearings {brgs[0]}-{brgs[-1]}° from the receiver."
        return s

    def _append(self, kind, t, rec):
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"{kind}-{time.strftime('%Y-%m-%d', time.gmtime(t))}.jsonl")
        with open(path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
