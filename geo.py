"""Callsign and grid-locator geography: cty.dat prefix lookup, distance, bearing."""
import math
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CTY_PATH = os.path.join(HERE, "vendor", "cty.dat")
CTY_URL = "https://www.country-files.com/cty/cty.dat"

_prefixes = {}     # prefix -> entity
_exact = {}        # full call -> entity
_grids = {}        # call -> (lat, lon) learned from reports that carry a locator (PSKReporter)


def _load():
    if _prefixes:
        return
    if not os.path.exists(CTY_PATH):
        os.makedirs(os.path.dirname(CTY_PATH), exist_ok=True)
        urllib.request.urlretrieve(CTY_URL, CTY_PATH)
    text = open(CTY_PATH, encoding="latin-1").read()
    # Entity: "Name: CQ: ITU: Cont: Lat: Lon(+W): UTC: Prefix:" then aliases up to ";"
    for m in re.finditer(r"([^:\n]+):\s*(\d+):\s*(\d+):\s*(\w+):\s*([-\d.]+):\s*([-\d.]+):\s*([-\d.]+):\s*(\S+?):\s*\n(.*?);",
                         text, re.S):
        name, _, _, cont, lat, lon, _, _, aliases = m.groups()
        ent = {"country": name.strip(), "cont": cont, "lat": float(lat), "lon": -float(lon)}
        for a in re.split(r"[,\s]+", aliases.strip()):
            if not a:
                continue
            exact = a.startswith("=")
            a = re.sub(r"\(.*?\)|\[.*?\]|<.*?>|\{.*?\}|~.*?~", "", a.lstrip("="))
            (_exact if exact else _prefixes)[a] = ent


def lookup(call):
    """Entity for a callsign ({country, cont, lat, lon}) or None."""
    try:
        _load()
    except Exception:
        return None
    call = call.upper().split("-")[0]
    if call in _exact:
        return _exact[call]
    # portable prefixes: N0CALL/4S -> 4S, but N0CALL/P stays N0CALL
    parts = [p for p in call.split("/") if p not in ("P", "M", "MM", "AM", "QRP") and not p.isdigit()]
    base = min(parts, key=len) if len(parts) > 1 else (parts[0] if parts else call)
    for n in range(len(base), 0, -1):
        if base[:n] in _prefixes:
            return _prefixes[base[:n]]
    return None


def learn_grid(call, grid):
    loc = grid_to_latlon(grid)
    if loc:
        _grids[call.upper()] = loc
    return loc


def locate(call):
    """Best known (lat, lon) for a station: a learned grid, else its country's centre."""
    call = call.upper()
    if call in _grids:
        return _grids[call]
    e = lookup(call)
    return (e["lat"], e["lon"]) if e else None


def grid_to_latlon(grid):
    """Centre of a 4/6/8-character Maidenhead locator, or None."""
    g = (grid or "").strip()
    if not re.fullmatch(r"[A-Ra-r]{2}\d{2}([A-Xa-x]{2}(\d{2})?)?", g):
        return None
    g = g[:2].upper() + g[2:4] + g[4:6].lower() + g[6:8]
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    dlon, dlat = 2.0, 1.0
    if len(g) >= 6:
        lon += (ord(g[4]) - 97) * 5 / 60
        lat += (ord(g[5]) - 97) * 2.5 / 60
        dlon, dlat = 5 / 60, 2.5 / 60
    if len(g) == 8:
        lon += int(g[6]) * 0.5 / 60
        lat += int(g[7]) * 0.25 / 60
        dlon, dlat = 0.5 / 60, 0.25 / 60
    return lat + dlat / 2, lon + dlon / 2


def dist_bearing(a, b):
    """Great-circle km and initial bearing (deg) from (lat, lon) a to b."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2
                                + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return 6371 * d, (math.degrees(math.atan2(y, x)) + 360) % 360
