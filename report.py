"""Propagation report: the slot log of one UTC day turned into a standalone HTML page.

From logs/ncdxf/slots-YYYY-MM-DD*.jsonl (any file of that day, e.g. a copy merged in from
another program; duplicate slots are counted once):
  * windows     per 15-minute UTC window and band: slots measured, heard, share heard
  * paths       per beacon and window: the highest band heard (a lower bound on that path's MUF)
  * conditions  per window: sun elevation at the station, Kp, highest GOES X-ray class
  * beacons     per beacon over the day: heard / measured per band, best 100 W S/N
  * summary     totals and the headline figures

render() puts the data, static/report.css and static/report.js into static/report.html,
so the page written to logs/reports/ opens without the monitor running.

Usage: python3 report.py [YYYY-MM-DD]   (default: today, UTC)"""
import calendar
import glob
import json
import os
import sys
import time

import beaconlog

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
WINDOW = 15 * 60
BANDS = [20, 17, 15, 12, 10]
MIN_TRIES_BEST = 18          # a band needs this many slots before it can be "the most open band"
XRAY_BASE = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}


def report_dir():
    return os.path.join(os.path.dirname(beaconlog.LOG_DIR), "reports")


def days():
    """UTC dates that have a slot log, newest first."""
    names = glob.glob(os.path.join(beaconlog.LOG_DIR, "slots-????-??-??*.jsonl"))
    return sorted({os.path.basename(n)[6:16] for n in names}, reverse=True)


def _ts(utc):
    return calendar.timegm(time.strptime(utc, "%Y-%m-%dT%H:%M:%SZ"))


def _hm(t):
    return time.strftime("%H:%M", time.gmtime(t))


def _flux(cls):
    try:
        return XRAY_BASE[cls[0]] * float(cls[1:])
    except (KeyError, IndexError, ValueError, TypeError):
        return 0.0


def load(day):
    """Slot records of one UTC day, oldest first, with the excluded ones counted."""
    seen, slots, files = {}, [], []
    excluded = {"filter_50hz": 0, "duplicate": 0}
    for path in sorted(glob.glob(os.path.join(beaconlog.LOG_DIR, f"slots-{day}*.jsonl"))):
        files.append(os.path.basename(path))
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") != "slot":
                    continue
                if r["utc"] in seen:
                    excluded["duplicate"] += 1
                    continue
                seen[r["utc"]] = True
                if r.get("receiver", {}).get("if_width_hz") == 50:     # too narrow for the beacons' keying
                    excluded["filter_50hz"] += 1
                    continue
                slots.append(r)
    slots.sort(key=lambda r: r["utc"])
    return slots, files, excluded


def build(day):
    slots, files, excluded = load(day)
    if not slots:
        return {"day": day, "files": files, "excluded": excluded, "summary": None}
    win, paths, cond, bcn = {}, {}, {}, {}
    band_tot = {b: [0, 0] for b in BANDS}
    kps, highest = [], None
    for r in slots:
        t = _ts(r["utc"])
        w = t - t % WINDOW
        b, heard = r["band_m"], bool(r["result"]["heard"])
        snr = r["result"]["snr_db"].get("100 W") if heard else None
        call = r["beacon"]["call"]

        x = win.setdefault((w, b), [0, 0])
        x[0] += 1; x[1] += heard
        band_tot.setdefault(b, [0, 0])
        band_tot[b][0] += 1; band_tot[b][1] += heard

        p = paths.setdefault((call, w), {})
        p.setdefault(b, [0, 0])
        p[b][0] += 1; p[b][1] += heard

        sw = r.get("space_weather") or {}
        c = cond.setdefault(w, {"n": 0, "sun": 0.0, "kp": None, "xray": ""})
        c["n"] += 1
        c["sun"] += r["sun"]["rx"]["elevation_deg"]
        if sw.get("kp") is not None:
            c["kp"] = max(c["kp"] or 0, sw["kp"])
            kps.append(sw["kp"])
        if _flux(sw.get("xray_class")) > _flux(c["xray"]):
            c["xray"] = sw["xray_class"]

        e = bcn.setdefault(call, {"beacon": call, "location": r["beacon"]["location"], "km": r["path"]["km"],
                                  "bearing_deg": r["path"]["bearing_from_rx_deg"], "bands": {}, "heard": 0,
                                  "best_snr_db": None})
        e["bands"].setdefault(b, [0, 0])
        e["bands"][b][0] += 1; e["bands"][b][1] += heard
        if heard:
            e["heard"] += 1
            highest = b if highest is None else min(highest, b)
            if snr is not None and (e["best_snr_db"] is None or snr > e["best_snr_db"]):
                e["best_snr_db"] = round(snr, 1)

    windows = [{"t": w, "band_m": b, "tried": n, "heard": h, "share": round(h / n, 3)}
               for (w, b), (n, h) in sorted(win.items())]
    path_rows = []
    for (call, w), per in sorted(paths.items()):
        heard = [b for b, (n, h) in per.items() if h]
        path_rows.append({"beacon": call, "t": w, "top_band_m": min(heard) if heard else None,
                          "detail": ", ".join(f"{b} m {h}/{n}" for b, (n, h) in sorted(per.items(), reverse=True))})
    conditions = [{"t": w, "rx_sun_deg": round(c["sun"] / c["n"], 1), "kp": c["kp"], "xray_class": c["xray"]}
                  for w, c in sorted(cond.items())]
    beacons = sorted(bcn.values(), key=lambda e: e["bearing_deg"])
    for e in beacons:
        e["bands"] = {str(b): f"{h}/{n}" for b, (n, h) in sorted(e["bands"].items(), reverse=True)}

    rated = [b for b in BANDS if band_tot[b][0] >= MIN_TRIES_BEST] or [b for b in BANDS if band_tot[b][0]]
    best = max(rated, key=lambda b: band_tot[b][1] / band_tot[b][0])
    top = max(beacons, key=lambda e: e["heard"])
    first, last = _ts(slots[0]["utc"]), _ts(slots[-1]["utc"])
    modes = {}
    for r in slots:
        m = r.get("monitor", {}).get("mode", "?")
        modes[m] = modes.get(m, 0) + 1
    summary = {
        "slots": len(slots), "hours": round((last - first) / 3600, 1), "first_t": first, "last_t": last,
        "beacons_heard": sum(1 for e in beacons if e["heard"]), "beacons_measured": len(beacons),
        "best_band_m": best, "best_band_pct": round(100 * band_tot[best][1] / band_tot[best][0]),
        "band_totals": {str(b): {"tried": n, "heard": h} for b, (n, h) in band_tot.items() if n},
        "highest_band_m": highest,
        "top_beacon": top["beacon"] if top["heard"] else None, "top_beacon_location": top["location"],
        "top_beacon_heard": top["heard"],
        "kp_min": min(kps) if kps else None, "kp_max": max(kps) if kps else None,
        "modes": modes,
    }
    st = slots[-1].get("rx_station", {})
    return {"day": day, "files": files, "excluded": excluded, "generated_t": int(time.time()),
            "station": {"call": st.get("call", ""), "grid": st.get("grid", "")},
            "window_s": WINDOW, "bands": BANDS, "summary": summary,
            "windows": windows, "paths": path_rows, "conditions": conditions, "beacons": beacons}


def render(day):
    data = build(day)
    with open(os.path.join(STATIC, "report.html")) as f:
        page = f.read()
    with open(os.path.join(STATIC, "report.css")) as f:
        css = f.read()
    with open(os.path.join(STATIC, "report.js")) as f:
        js = f.read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return (page.replace("/*REPORT_CSS*/", css).replace("/*REPORT_JS*/", js.replace("</script", "<\\/script"))
            .replace("/*REPORT_DATA*/null", blob)), data


def write(day):
    """Render the day's report to logs/reports/propagation-DAY.html; returns (file name, data)."""
    html, data = render(day)
    os.makedirs(report_dir(), exist_ok=True)
    name = f"propagation-{day}.html"
    tmp = os.path.join(report_dir(), name + ".tmp")
    with open(tmp, "w") as f:
        f.write(html)
    os.replace(tmp, os.path.join(report_dir(), name))
    return name, data


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d", time.gmtime())
    name, data = write(day)
    s = data["summary"]
    print(os.path.join(report_dir(), name), f"{s['slots']} slots" if s else "no slots")
