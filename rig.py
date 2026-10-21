"""Supported radios: which driver, which serial port, which USB sound card.

Each radio has a CAT serial port and its own USB audio codec behind the radio's
internal USB hub, so the codec that belongs to a radio is the one sharing that hub
with its serial port (both the IC-7300 and the FTDX10 use a TI "USB Audio CODEC",
so names alone can't tell them apart)."""
import glob
import json
import os
import re
import subprocess

from serial.tools import list_ports

from ftdx10 import FTDX10
from radio import IC7300

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json")

RADIOS = {"IC-7300": IC7300, "FTDX10": FTDX10}


def _iface(device):
    """USB interface number of a tty ('/dev/ttyUSB1' on interface 1.0 -> 0), or None."""
    path = os.path.realpath(f"/sys/class/tty/{os.path.basename(device)}/device")
    m = re.findall(r":\d+\.(\d+)(?=/|$)", path)    # .../1-10.1:1.0/ttyUSB0 -> 0
    return int(m[-1]) if m else None


def _identify(p):
    """Radio name of a serial port from its USB ids, or None.
    IC-7300: CP2102 (10c4:ea60) whose USB serial number Icom sets to "IC-7300 <serial>".
    FTDX10: CP2105 dual UART (10c4:ea70); interface 0 is the Enhanced port = CAT
    (interface 1, the Standard port, carries PTT/keying lines). Other Yaesu radios use the
    CP2105 too: the driver checks the CAT ID answer (0761 = FTDX10) when it connects."""
    if p.vid == 0x10C4 and p.pid == 0xEA60 and "IC-7300" in (p.serial_number or "") + (p.product or ""):
        return "IC-7300"
    if p.vid == 0x10C4 and p.pid == 0xEA70 and _iface(p.device) == 0:
        return "FTDX10"
    return None


def detect():
    """{radio name: serial port} of the radios plugged in now (by-id path when there is one)."""
    by_id = {os.path.realpath(l): l for l in glob.glob("/dev/serial/by-id/*")}
    found = {}
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        name = _identify(p) if p.vid else None
        if name and name not in found:
            found[name] = by_id.get(os.path.realpath(p.device), p.device)
    return found


def load_settings():
    try:
        return json.load(open(SETTINGS))
    except Exception:
        return {}


def save_settings(**kw):
    s = load_settings()
    s.update(kw)
    with open(SETTINGS, "w") as f:
        json.dump(s, f, indent=2)


def find_port(name, found=None):
    """CAT serial port of the radio: settings.json "ports" override, else USB detection."""
    p = load_settings().get("ports", {}).get(name)
    if p:
        return p
    return (detect() if found is None else found).get(name)


def available():
    """[{name, connected (= port present now), port}] for every supported radio."""
    found = detect()
    out = []
    for n in RADIOS:
        p = find_port(n, found)
        out.append({"name": n, "connected": bool(p and os.path.exists(p)), "port": p})
    return out


def _usb_hub(sysfs_path):
    """'/sys/devices/.../usb1/1-10/1-10.4/1-10.4:1.0' -> '1-10' (the radio's internal hub)."""
    ports = re.findall(r"/(\d+-[\d.]+)(?=/|$)", sysfs_path)
    if not ports:
        return None
    dev = ports[-1]
    return dev.rsplit(".", 1)[0] if "." in dev else dev


def audio_nodes():
    """PipeWire capture nodes of USB sound cards: [(node name, USB hub)]."""
    try:
        dump = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout)
    except Exception:
        return []
    out = []
    for o in dump:
        p = (o.get("info") or {}).get("props") or {}
        name = str(p.get("node.name", ""))
        if p.get("media.class") != "Audio/Source" or not name.startswith("alsa_input.usb"):
            continue
        m = re.search(r":(\d+)", str(p.get("api.alsa.path", "")))
        card = p.get("api.alsa.card", m.group(1) if m else None)
        hub = None
        if card is not None:
            hub = _usb_hub(os.path.realpath(f"/sys/class/sound/card{card}/device"))
        out.append((name, hub))
    return out


def audio_node_for(port):
    """The capture node on the same USB hub as the radio's serial port."""
    nodes = audio_nodes()
    if port and os.path.exists(port):
        tty = os.path.basename(os.path.realpath(port))
        hub = _usb_hub(os.path.realpath(f"/sys/class/tty/{tty}/device"))
        for name, h in nodes:
            if hub and h == hub:
                return name
    return nodes[0][0] if len(nodes) == 1 else None      # one USB codec only: it must be this radio's


def make(name, port=None, baud=None):
    cls = RADIOS[name]
    port = port or find_port(name)
    return cls(port, baud) if baud else cls(port)
