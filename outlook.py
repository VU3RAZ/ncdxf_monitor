"""Propagation outlook from the last hour of beacon measurements.

Measured:
  * MUF bracket per path: the highest band the beacon was heard on is a lower bound
    (the MUF is at least that frequency); the next higher band measured at least twice
    and never heard gives an upper bound. A beacon that is off the air or in a fade can
    also look "closed", so the counts (heard / tries) are reported with it.

Estimated (rule of thumb, not calibrated):
  * SSB contact chance on a path and band. The beacon's 100 W dash S/N is measured in a
    30 Hz bandwidth; in a 2.4 kHz SSB bandwidth it is 10*log10(2400/30) = 19 dB lower.
    An SSB contact needs roughly +6 dB S/N in 2.4 kHz (weak but workable), so the chance
    is a logistic curve centred there (3 dB per e-fold), times the fraction of tries the
    beacon was heard (fading/openings). Assumes you run 100 W into an antenna like the
    beacon's and a reciprocal path; receiver AGC compresses the measured S/N, so this
    errs low.
"""
import math
import statistics

SSB_BW_DB = 10 * math.log10(2400 / 30)      # 19.0 dB: 30 Hz measurement -> 2.4 kHz SSB
SSB_NEED_DB = 6.0                             # S/N in 2.4 kHz for a workable SSB contact
SSB_SLOPE_DB = 3.0
WINDOW = 3600


def ssb_chance(snr_30hz, heard, tries):
    if snr_30hz is None or not tries:
        return 0.0
    snr = snr_30hz - SSB_BW_DB
    return heard / tries / (1 + math.exp(-(snr - SSB_NEED_DB) / SSB_SLOPE_DB))


def outlook(history, beacons, bands, now, window=WINDOW):
    """beacons: [{call, loc, km, brg}], bands: [(name, hz)] low to high."""
    stats = {}                                   # (beacon, band) -> [tries, heard, [snr of heard 100 W dashes]]
    for r in history:
        if now - r["ts"] > window:
            continue
        s = stats.setdefault((r["beacon"], r["band"]), [0, 0, []])
        s[0] += 1
        if r["dashes"] > 0:
            s[1] += 1
            if r["snr"][0] is not None:
                s[2].append(r["snr"][0])
    paths = []
    for i, b in enumerate(beacons):
        per_band = {j: stats[(i, j)] for j in range(len(bands)) if (i, j) in stats}
        if not per_band:
            continue
        heard_bands = [j for j, s in per_band.items() if s[1]]
        entry = {"call": b["call"], "loc": b["loc"], "km": b["km"], "brg": b["brg"],
                 "tries": {bands[j][0]: f"{s[1]}/{s[0]}" for j, s in sorted(per_band.items())}}
        if heard_bands:
            top = max(heard_bands)
            entry["muf_low_mhz"] = round(bands[top][1] / 1e6, 2)
            above = [j for j in range(top + 1, len(bands)) if j in per_band]
            closed = next((j for j in above if per_band[j][1] == 0 and per_band[j][0] >= 2), None)
            entry["muf_high_mhz"] = round(bands[closed][1] / 1e6, 2) if closed is not None else None
            best = None
            for j in heard_bands:
                tries, heard, snrs = per_band[j]
                snr = statistics.median(snrs) if snrs else None
                p = ssb_chance(snr, heard, tries)
                if best is None or p > best["ssb_chance"]:
                    best = {"band": bands[j][0], "ssb_chance": round(p, 2), "snr_30hz_db": snr,
                            "snr_ssb_db": round(snr - SSB_BW_DB, 1) if snr is not None else None,
                            "heard": heard, "tries": tries}
            entry["best"] = best
        paths.append(entry)
    paths.sort(key=lambda e: -(e.get("best") or {}).get("ssb_chance", -1))
    per_band = []
    for j, (name, hz) in enumerate(bands):
        heard = [beacons[i]["call"] for (i, jj), s in stats.items() if jj == j and s[1]]
        tried = sum(1 for (i, jj) in stats if jj == j)
        chances = [ssb_chance(statistics.median(s[2]) if s[2] else None, s[1], s[0])
                   for (i, jj), s in stats.items() if jj == j and s[1]]
        per_band.append({"band": name, "beacons_measured": tried, "beacons_heard": sorted(heard),
                         "best_ssb_chance": round(max(chances), 2) if chances else 0.0})
    return {"window_min": window // 60, "paths": paths, "bands": per_band,
            "assumptions": {"ssb_needs_db_in_2400hz": SSB_NEED_DB, "bandwidth_correction_db": round(SSB_BW_DB, 1),
                            "your_power_w": 100}}
