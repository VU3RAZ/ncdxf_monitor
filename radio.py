"""Icom IC-7300 CI-V control over the USB serial port.

Runs a reader thread that parses CI-V frames (scope data, transceive
updates, poll replies) and keeps `state` up to date. Writes are serialized
with a lock. Callbacks are invoked from the reader thread.
"""
import glob
import re
import queue
import threading
import time

import serial

PREAMBLE = b"\xfe\xfe"
EOM = 0xFD
CTRL_ADDR = 0xE0

MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
         0x05: "FM", 0x07: "CW-R", 0x08: "RTTY-R"}
MODE_CODES = {v: k for k, v in MODES.items()}
AGC = {1: "FAST", 2: "MID", 3: "SLOW"}

SCOPE_PIXELS = 475
SCOPE_SPEEDS = {0: "fast", 1: "mid", 2: "slow"}


def find_port():
    import rig
    return rig.detect().get("IC-7300")


def bcd_to_int(b):
    """Little-endian BCD (frequency style) -> int."""
    v, mul = 0, 1
    for byte in b:
        v += (byte & 0x0F) * mul + ((byte >> 4) & 0x0F) * mul * 10
        mul *= 100
    return v


def int_to_bcd(v, nbytes):
    out = bytearray()
    for _ in range(nbytes):
        lo = v % 10; v //= 10
        hi = v % 10; v //= 10
        out.append((hi << 4) | lo)
    return bytes(out)


def bcd_be(b):
    """Big-endian BCD (levels, meters) -> int."""
    v = 0
    for byte in b:
        v = v * 100 + ((byte >> 4) & 0x0F) * 10 + (byte & 0x0F)
    return v


def int_to_bcd_be(v, nbytes):
    return bytes(reversed(int_to_bcd(v, nbytes)))


class IC7300:
    name = "IC-7300"
    caps = {"name": "IC-7300", "scope": False,
            "filters": {"1": "FIL1", "2": "FIL2", "3": "FIL3"}, "filter_help": "the radio's FIL1/2/3 settings",
            "preamp": ["P.AMP OFF", "P.AMP 1", "P.AMP 2"], "att": "ATT 20 dB", "notes": []}

    def __init__(self, port=None, baud=115200, addr=0x94):
        self.port = port or find_port()
        self.baud = baud
        self.addr = addr
        self.ser = None
        self.lock = threading.Lock()
        self.txq = queue.Queue()
        self.reply = threading.Event()
        self.running = False
        self.state = {
            "connected": False, "freq": 0, "mode": "", "filter": 1, "data": False,
            "af": 0, "rf": 0, "power": 0, "keyspeed": 0, "preamp": 0, "att": False,
            "nb": False, "nr": False, "agc": "", "smeter": 0, "tx": False,
            "po": 0, "swr": 0, "if_width": 0, "radio": "IC-7300", "cw_pitch": 600, "break_in": 0, "scope_span": 0, "scope_mode": "center", "scope_speed": "",
        }
        self.on_state = None   # callback(dict of changed keys)
        self.on_scope = None   # callback(low_hz, high_hz, bytes pixels)
        self._scope = {}

    # ---- connection -------------------------------------------------
    def start(self):
        self.running = True
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._poller, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()

    def _open(self):
        s = serial.Serial(self.port, self.baud, timeout=0.05)
        s.dtr = False   # DTR/RTS may be mapped to keying/PTT in the radio menu
        s.rts = False
        self.ser = s
        self._update(connected=True)
        self.scope_enable(False)       # no panadapter here: the scope stream makes the radio drop commands
        self.read_all()

    def _close(self):
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self._update(connected=False)

    def stop(self):
        self.scope_enable(False)
        t = time.time()
        while not self.txq.empty() and time.time() - t < 1:
            time.sleep(0.05)
        time.sleep(0.2)
        self.running = False
        self._close()

    # ---- low level --------------------------------------------------
    def send(self, *payload):
        """Queue a command. The radio drops commands sent back-to-back while it is
        streaming scope data, so the writer sends one at a time and waits for the reply."""
        self.txq.put(PREAMBLE + bytes([self.addr, CTRL_ADDR]) + bytes(payload) + bytes([EOM]))

    def _writer(self):
        while True:
            frame = self.txq.get()
            if frame is None:
                return
            with self.lock:
                if self.ser is None:
                    continue
                try:
                    self.reply.clear()
                    self.ser.write(frame)
                except Exception:
                    self._close()
                    continue
            self.reply.wait(0.2)

    def _reader(self):
        buf = bytearray()
        while self.running:
            if self.ser is None:
                try:
                    self._open()
                except Exception:
                    time.sleep(2)
                    continue
            try:
                data = self.ser.read(4096)
            except Exception:
                self._close()
                time.sleep(1)
                continue
            if not data:
                continue
            buf += data
            while True:
                end = buf.find(EOM)
                if end < 0:
                    break
                frame = bytes(buf[:end])
                del buf[:end + 1]
                start = frame.rfind(PREAMBLE)
                if start < 0:
                    continue
                frame = frame[start + 2:]
                if len(frame) < 3:
                    continue
                dst, src = frame[0], frame[1]
                if src != self.addr or dst not in (CTRL_ADDR, 0x00):
                    continue  # our own echo, or another controller
                if dst == CTRL_ADDR and frame[2:4] != b"\x27\x00":
                    self.reply.set()  # answer to our last command (not scope stream)
                try:
                    self._handle(frame[2:])
                except Exception as e:  # never let a bad frame kill the reader
                    print("CI-V parse error", frame.hex(" "), e)

    def _update(self, **kw):
        changed = {k: v for k, v in kw.items() if self.state.get(k) != v}
        if changed:
            self.state.update(changed)
            if self.on_state:
                self.on_state(changed)

    def _handle(self, p):
        c = p[0]
        if c in (0x00, 0x03) and len(p) >= 6:
            self._update(freq=bcd_to_int(p[1:6]))
        elif c in (0x01, 0x04) and len(p) >= 2:
            kw = {"mode": MODES.get(p[1], hex(p[1]))}
            if len(p) >= 3:
                kw["filter"] = p[2]
            self._update(**kw)
        elif c == 0x1A and len(p) >= 3 and p[1] == 0x06:
            self._update(data=p[2] != 0)
        elif c == 0x1A and len(p) >= 3 and p[1] == 0x03:
            # IF filter width index (SSB/CW/RTTY): 0-9 = 50-500 Hz, 10-40 = 600-3600 Hz
            i = bcd_be(p[2:3])
            self._update(if_width=50 * (i + 1) if i < 10 else 600 + 100 * (i - 10))
        elif c == 0x14 and len(p) >= 4:
            v = bcd_be(p[2:4])
            key = {0x01: "af", 0x02: "rf", 0x0A: "power", 0x0C: "keyspeed"}.get(p[1])
            if key:
                self._update(**{key: v})
            elif p[1] == 0x09:  # 0-255 -> 300-900 Hz in 5 Hz steps
                self._update(cw_pitch=300 + round(v * 600 / 255 / 5) * 5)
        elif c == 0x15 and len(p) >= 4:
            v = bcd_be(p[2:4])
            key = {0x02: "smeter", 0x11: "po", 0x12: "swr"}.get(p[1])
            if key:
                self._update(**{key: v})
        elif c == 0x16 and len(p) >= 3:
            if p[1] == 0x02:
                self._update(preamp=p[2])
            elif p[1] == 0x22:
                self._update(nb=p[2] != 0)
            elif p[1] == 0x40:
                self._update(nr=p[2] != 0)
            elif p[1] == 0x12:
                self._update(agc=AGC.get(p[2], ""))
            elif p[1] == 0x47:
                self._update(break_in=p[2])
        elif c == 0x11 and len(p) >= 2:
            self._update(att=p[1] != 0)
        elif c == 0x1C and len(p) >= 3 and p[1] == 0x00:
            self._update(tx=p[2] != 0)
        elif c == 0x27:
            self._handle_scope(p)

    def _handle_scope(self, p):
        sub = p[1]
        if sub == 0x00 and len(p) >= 5:
            seq, seqmax = bcd_be(p[3:4]), bcd_be(p[4:5])
            data = p[5:]
            if seq == 1:
                mode = data[0]
                f1 = bcd_to_int(data[1:6])
                f2 = bcd_to_int(data[6:11])
                if mode == 0:   # center mode: f1 = center, f2 = half span
                    lo, hi = f1 - f2, f1 + f2
                else:           # fixed / scroll: f1 = lower edge, f2 = upper edge
                    lo, hi = f1, f2
                self._scope = {"lo": lo, "hi": hi, "px": bytearray()}
                if len(data) > 12:  # LAN-style single frame, unlikely over USB
                    self._scope["px"] += data[12:]
            elif self._scope:
                self._scope["px"] += data
            if seq == seqmax and self._scope:
                px = bytes(self._scope["px"])
                if self.on_scope and px:
                    self.on_scope(self._scope["lo"], self._scope["hi"], px)
                self._scope = {}
        elif sub == 0x15 and len(p) >= 8:
            self._update(scope_span=bcd_to_int(p[3:8]))
        elif sub == 0x14 and len(p) >= 4:
            self._update(scope_mode="center" if p[3] == 0 else "fixed")
        elif sub == 0x1A and len(p) >= 4:
            self._update(scope_speed=SCOPE_SPEEDS.get(p[3], ""))

    # ---- polling ----------------------------------------------------
    def read_all(self):
        for cmd in ((0x03,), (0x04,), (0x1A, 0x06), (0x1A, 0x03), (0x14, 0x01), (0x14, 0x02),
                    (0x14, 0x0A), (0x14, 0x0C), (0x14, 0x09), (0x16, 0x47), (0x16, 0x02), (0x11,), (0x16, 0x22),
                    (0x16, 0x40), (0x16, 0x12), (0x1C, 0x00), (0x27, 0x15, 0x00),
                    (0x27, 0x14, 0x00), (0x27, 0x1A, 0x00)):
            self.send(*cmd)

    def _poller(self):
        n = 0
        while self.running:
            time.sleep(0.2)
            if self.ser is None or self.txq.qsize() > 4:
                continue
            self.send(0x15, 0x02)
            if self.state["tx"]:
                self.send(0x15, 0x11)
                self.send(0x15, 0x12)
            n += 1
            if n % 5 == 0:
                self.send(0x03)
                self.send(0x04)
                self.send(0x1C, 0x00)
            if n % 25 == 0:
                self.read_all()

    # ---- commands ---------------------------------------------------
    def scope_enable(self, on):
        self.send(0x27, 0x10, 0x01 if on else 0x00)
        self.send(0x27, 0x11, 0x01 if on else 0x00)

    def set_freq(self, hz):
        hz = int(hz)
        if not (30_000 <= hz <= 74_800_000):
            return
        self.send(0x05, *int_to_bcd(hz, 5))
        self._update(freq=hz)

    def set_mode(self, mode, filt=None, data=None):
        code = MODE_CODES.get(mode)
        if code is None:
            return
        filt = filt or self.state.get("filter") or 1
        self.send(0x06, code, filt)
        if data is not None:
            self.send(0x1A, 0x06, 0x01 if data else 0x00, filt if data else 0x00)
        self.send(0x04)
        self.send(0x1A, 0x06)
        self.send(0x1A, 0x03)

    def set_level(self, name, value):
        sub = {"af": 0x01, "rf": 0x02, "power": 0x0A, "keyspeed": 0x0C}[name]
        value = max(0, min(255, int(value)))
        self.send(0x14, sub, *int_to_bcd_be(value, 2))
        self._update(**{name: value})

    def set_preamp(self, v):
        self.send(0x16, 0x02, int(v) % 3)
        self.send(0x16, 0x02)

    def set_att(self, on):
        self.send(0x11, 0x20 if on else 0x00)
        self.send(0x11)

    def set_nb(self, on):
        self.send(0x16, 0x22, 1 if on else 0)
        self.send(0x16, 0x22)

    def set_nr(self, on):
        self.send(0x16, 0x40, 1 if on else 0)
        self.send(0x16, 0x40)

    def set_agc(self, name):
        code = {v: k for k, v in AGC.items()}.get(name)
        if code:
            self.send(0x16, 0x12, code)
            self.send(0x16, 0x12)

    def set_break_in(self, mode):
        """0 = off, 1 = semi, 2 = full."""
        self.send(0x16, 0x47, int(mode))
        self.send(0x16, 0x47)

    def set_ptt(self, on):
        self.send(0x1C, 0x00, 0x01 if on else 0x00)
        self.send(0x1C, 0x00)

    def tune(self):
        self.send(0x1C, 0x01, 0x02)

    def set_scope_span(self, half_span_hz):
        self.send(0x27, 0x15, 0x00, *int_to_bcd(int(half_span_hz), 5))
        self.send(0x27, 0x15, 0x00)

    def set_scope_mode(self, mode):
        self.send(0x27, 0x14, 0x00, 0x00 if mode == "center" else 0x01)
        self.send(0x27, 0x14, 0x00)

    def set_scope_speed(self, speed):
        code = {v: k for k, v in SCOPE_SPEEDS.items()}.get(speed)
        if code is not None:
            self.send(0x27, 0x1A, 0x00, code)
            self.send(0x27, 0x1A, 0x00)

    def send_cw(self, text):
        """Send text with the radio's internal keyer (needs CW mode + break-in)."""
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/?.,-=+ @^()':\"")
        # prosigns: the radio's keyer sends "^XY" as one run-together character
        text = re.sub(r"<([A-Z]{2})>", r"^\1", text.upper())
        text = "".join(ch for ch in text if ch in allowed)
        for i in range(0, len(text), 30):
            self.send(0x17, *text[i:i + 30].encode("ascii"))

    def stop_cw(self):
        self.send(0x17, 0xFF)

    def poll_tx(self):
        self.send(0x1C, 0x00)

    # ---- used by the beacon monitor -----------------------------------
    def cw_narrow(self):
        """CW with FIL2 (500 Hz on this radio; FIL3 is set to 50 Hz, too narrow for beacons)."""
        self.set_mode("CW", 2, data=False)

    def snapshot(self):
        return {k: self.state.get(k) for k in ("freq", "mode", "filter", "data")}

    def restore(self, s):
        if s.get("freq"):
            self.set_freq(s["freq"])
        if s.get("mode"):
            self.set_mode(s["mode"], s.get("filter"), s.get("data"))
