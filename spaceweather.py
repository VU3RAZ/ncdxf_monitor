"""Space weather from NOAA SWPC, cached, for the propagation log.

Solar flux (SFI, 10.7 cm), planetary Kp / running A, GOES X-ray (flare class) and the
NOAA R/S/G scales. Refreshed at most every 15 minutes; a failed fetch keeps the old
values and records the error, so the log never blocks on the network."""
import asyncio
import time

import aiohttp

SWPC = "https://services.swpc.noaa.gov"
URLS = {
    "sfi": SWPC + "/products/summary/10cm-flux.json",
    "kp": SWPC + "/products/noaa-planetary-k-index.json",
    "xray": SWPC + "/json/goes/primary/xrays-6-hour.json",
    "scales": SWPC + "/products/noaa-scales.json",
}
REFRESH = 15 * 60


def xray_class(flux):
    """GOES 0.1-0.8 nm flux (W/m2) -> flare class, e.g. 5.3e-7 -> 'B5.3'."""
    for letter, base in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)):
        if flux >= base:
            return f"{letter}{flux / base:.1f}"
    return "A0.0"


class SpaceWeather:
    def __init__(self):
        self.data = {}
        self.fetched = 0.0
        self.lock = asyncio.Lock()

    async def get(self):
        async with self.lock:
            if time.time() - self.fetched > REFRESH:
                await self._fetch()
        return dict(self.data)

    async def _fetch(self):
        d, errors = dict(self.data), {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            for key, url in URLS.items():
                try:
                    async with s.get(url) as r:
                        r.raise_for_status()
                        j = await r.json(content_type=None)
                    if key == "sfi":
                        d["sfi"] = int(j[0]["flux"])
                        d["sfi_utc"] = j[0]["time_tag"] + "Z"
                    elif key == "kp":
                        last = j[-1]
                        d["kp"] = float(last["Kp"])
                        d["a_running"] = int(last["a_running"])
                        d["kp_utc"] = last["time_tag"] + "Z"
                        d["kp_24h_max"] = max(float(x["Kp"]) for x in j[-8:])
                    elif key == "xray":
                        long = [x for x in j if x.get("energy") == "0.1-0.8nm"]
                        d["xray_class"] = xray_class(long[-1]["flux"])
                        d["xray_6h_max_class"] = xray_class(max(x["flux"] for x in long))
                        d["xray_utc"] = long[-1]["time_tag"]
                    elif key == "scales":
                        now = j["0"]
                        d["noaa_scales"] = {k: int(now[k]["Scale"]) if now[k]["Scale"] not in (None, "") else None
                                            for k in ("R", "S", "G")}
                except Exception as e:
                    errors[key] = str(e)[:120]
        d["source"] = "NOAA SWPC"
        d["fetched_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if errors:
            d["errors"] = errors
        else:
            d.pop("errors", None)
        self.data, self.fetched = d, time.time()
