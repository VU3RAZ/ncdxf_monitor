"""PC clock check: is the system clock NTP-synchronised, and how far off is it?

The beacon monitor needs the clock within ~0.1 s (10 s slots, it searches -0.1..+0.6 s for
the audio latency) and FT8/FT4 within ~0.5 s. Uses chrony when it runs (offset and error
bound), else systemd's NTPSynchronized flag."""
import subprocess
import time

MAX_ERROR_MS = 100          # offset + error bound above this = not good enough for the beacon monitor


def _run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()


def status():
    out = {"checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        f = _run("chronyc", "-c", "tracking").split(",")
        # ref id, ref name/ip, stratum, ref time, system time offset (s), last offset, rms offset, freq,
        # residual freq, skew, root delay, root dispersion, update interval, leap status
        if len(f) >= 14:
            offset = float(f[4]) * 1000
            bound = (float(f[10]) / 2 + float(f[11])) * 1000
            synced = f[13] == "Normal" and int(f[2]) < 16
            out.update(source="chrony", synced=synced, reference=f[1], stratum=int(f[2]),
                       offset_ms=round(offset, 3), error_bound_ms=round(bound, 3),
                       ok=synced and abs(offset) + bound < MAX_ERROR_MS)
            return out
    except Exception:
        pass
    try:
        synced = "NTPSynchronized=yes" in _run("timedatectl", "show")
        out.update(source="timedatectl", synced=synced, offset_ms=None, error_bound_ms=None, ok=synced)
    except Exception as e:
        out.update(source=None, synced=False, ok=False, error=str(e))
    return out


def describe(s):
    if not s.get("synced"):
        return "PC clock is NOT NTP-synchronised"
    if s.get("offset_ms") is None:
        return "PC clock NTP-synchronised"
    return (f"PC clock NTP-synced to {s['reference']} (PC at stratum {s['stratum']}): offset {s['offset_ms']:+.1f} ms, "
            f"error bound {s['error_bound_ms']:.1f} ms" + ("" if s["ok"] else f" - more than {MAX_ERROR_MS} ms"))
