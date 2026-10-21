"""Yaesu FTDX10 CAT control over the USB serial port (Enhanced COM port).

Same interface as radio.IC7300 (state dict, set_* methods, on_state callback) so the
rest of the app does not care which radio is connected. Commands are from the
"FTDX10 CAT Operation Reference Manual" (rigpix.com/yaesu/ftdx10_cat_manual.pdf):
ASCII, two letters + parameters + ';'.

Values are converted to the scales the app already uses for the IC-7300:
levels 0-255, S-meter 0-241 with S9 = 120, PO 0-255, SWR in Icom meter units.

Differences from the IC-7300:
  * no spectrum scope over CAT (caps["scope"] is False);
  * no free-text CW command: text is loaded into keyer memory 1 (KM, max 50 chars)
    and played (KY1), chunk by chunk;
  * filters are a width (SH) - FIL1/2/3 here are presets per mode (see FILTERS).
"""
import glob
import queue
import re
import threading
import time

import serial

from morse import MORSE

MODES = {"1": ("LSB", False), "2": ("USB", False), "3": ("CW", False), "4": ("FM", False), "5": ("AM", False),
         "6": ("RTTY", False), "7": ("CW-R", False), "8": ("LSB", True), "9": ("RTTY-R", False),
         "A": ("FM", True), "B": ("FM", False), "C": ("USB", True), "D": ("AM", False), "E": ("RTTY", False),
         "F": ("FM", True)}
# app mode (Icom names) -> FTDX10 MD code. CW = CW-U, CW-R = CW-L, RTTY = RTTY-L (mark low), RTTY-R = RTTY-U
MODE_CODES = {("LSB", False): "1", ("USB", False): "2", ("CW", False): "3", ("FM", False): "4", ("AM", False): "5",
              ("RTTY", False): "6", ("CW-R", False): "7", ("LSB", True): "8", ("RTTY-R", False): "9",
              ("FM", True): "A", ("USB", True): "C"}
NARROW_FAMILY = {"CW", "CW-R", "RTTY", "RTTY-R"}
# SH width codes (manual table 3); 0 = default for the roofing filter
WIDTH_NARROW = [None, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 800, 1200, 1400, 1700, 2000, 2400, 3000]
WIDTH_SSB = [None, 300, 400, 600, 850, 1100, 1200, 1500, 1650, 1800, 1950, 2100, 2250, 2400, 2450, 2500, 2600,
             2700, 2800, 2900, 3000, 3200, 3500, 4000]
# FIL1/2/3 presets (SH codes), like the IC-7300 defaults: CW 1.2k/500/250, SSB 3.0k/2.4k/1.8k
FILTERS = {"narrow": {1: 13, 2: 10, 3: 5}, "ssb": {1: 20, 2: 13, 3: 9}}
AGC = {"0": "OFF", "1": "FAST", "2": "MID", "3": "SLOW", "4": "AUTO", "5": "AUTO", "6": "AUTO"}
AGC_CODES = {"FAST": 1, "MID": 2, "SLOW": 3}
# meter calibrations (raw 0-255 -> dB over S9 / SWR); from Hamlib's Yaesu tables, not measured on this radio
S_CAL = [(0, -54), (12, -48), (27, -42), (40, -36), (55, -30), (65, -24), (80, -18), (95, -12), (112, -6),
         (130, 0), (150, 10), (172, 20), (190, 30), (220, 40), (240, 50), (255, 60)]
SWR_CAL = [(0, 1.0), (26, 1.2), (52, 1.5), (89, 2.0), (255, 5.0)]
BAUDS = (38400, 19200, 9600, 4800)


def interp(table, x):
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(1, x1 - x0)
    return table[-1][1]


def smeter_icom(raw):
    """Yaesu SM 0-255 -> Icom-scale 0-241 (S9 = 120, S9+60 = 241)."""
    db = interp(S_CAL, raw)
    return round((db + 54) / 54 * 120) if db < 0 else round(120 + db / 60 * 121)


def swr_icom(raw):
    swr = interp(SWR_CAL, raw)
    return round((swr - 1) * 96 if swr <= 1.5 else 48 + (swr - 1.5) * 64 if swr <= 2 else 80 + (swr - 2) * 40)


def find_port():
    import rig
    return rig.detect().get("FTDX10")


class FTDX10:
    name = "FTDX10"
    caps = {"name": "FTDX10", "scope": False,
            "filters": {"1": "WIDE", "2": "MID", "3": "NAR"},
            "filter_help": "CW/RTTY 1.2 kHz / 500 Hz / 250 Hz, SSB 3.0 / 2.4 / 1.8 kHz (SH width)",
            "preamp": ["IPO", "AMP 1", "AMP 2"], "att": "ATT 18 dB",
            "notes": ["The FTDX10 does not send its spectrum scope over CAT: use the audio waterfall.",
                      "S-meter/SWR calibration is approximate (Hamlib tables)."]}

    def __init__(self, port=None, baud=None):
        self.port = port or find_port()
        self.baud = baud               # None: probe BAUDS
        self.ser = None
        self.lock = threading.Lock()
        self.txq = queue.Queue()
        self.running = False
        self.state = {
            "connected": False, "freq": 0, "mode": "", "filter": 0, "data": False,
            "af": 0, "rf": 0, "power": 0, "keyspeed": 0, "preamp": 0, "att": False,
            "nb": False, "nr": False, "agc": "", "smeter": 0, "tx": False,
            "po": 0, "swr": 0, "cw_pitch": 600, "break_in": 0, "scope_span": 0, "scope_mode": "center",
            "scope_speed": "", "if_width": 0, "radio": self.name,
        }
        self._sh = 0                   # raw SH width code
        self._mode_code = ""
        self._cw = None                # (cancel event, thread) of a CW message being keyed
        self.on_state = None
        self.on_scope = None

    # ---- connection -------------------------------------------------
    def start(self):
        self.running = True
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()
        threading.Thread(target=self._poller, daemon=True).start()

    def _open(self):
        for baud in ([self.baud] if self.baud else BAUDS):
            s = serial.Serial(self.port, baud, timeout=0.1, rtscts=False)
            try:
                s.dtr = False
                s.rts = True           # menu "CAT RTS" = ENABLE (default) needs RTS asserted
            except OSError:
                pass                   # not a real serial port (e.g. a test pty)
            s.reset_input_buffer()
            s.write(b"ID;")
            reply = s.read_until(b";", 32)
            if reply.endswith(b";") and reply.startswith(b"ID"):
                if reply != b"ID0761;":
                    print(f"FTDX10 driver: radio answers {reply.decode()} (FTDX10 is ID0761) - commands may differ")
                self.ser, self.baud = s, baud
                self._update(connected=True)
                self.read_all()
                return
            s.close()
        raise IOError(f"no CAT answer from {self.port}")

    def _close(self):
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self._update(connected=False)

    def stop(self):
        self.stop_cw()
        t = time.time()
        while not self.txq.empty() and time.time() - t < 1:
            time.sleep(0.05)
        self.running = False
        self.txq.put(None)
        self._close()

    # ---- low level --------------------------------------------------
    def send(self, cmd):
        """Queue one CAT command, e.g. 'FA014100000;'."""
        self.txq.put(cmd if cmd.endswith(";") else cmd + ";")

    def _writer(self):
        while True:
            cmd = self.txq.get()
            if cmd is None:
                return
            with self.lock:
                if self.ser is None:
                    continue
                try:
                    self.ser.write(cmd.encode("ascii"))
                except Exception:
                    self._close()
                    continue
            time.sleep(0.02)

    def _reader(self):
        buf, last_err = b"", None
        while self.running:
            if self.ser is None:
                try:
                    self._open()
                except Exception as e:
                    if str(e) != last_err:     # say why once, not every 2 s
                        print(f"FTDX10: {e}")
                        last_err = str(e)
                    time.sleep(2)
                    continue
                last_err = None
            try:
                data = self.ser.read(256)
            except Exception:
                self._close()
                time.sleep(1)
                continue
            if not data:
                continue
            buf += data
            while b";" in buf:
                msg, buf = buf.split(b";", 1)
                try:
                    self._handle(msg.decode("ascii", "replace").strip())
                except Exception as e:     # never let a bad answer kill the reader
                    print("CAT parse error", msg, e)

    def _update(self, **kw):
        changed = {k: v for k, v in kw.items() if self.state.get(k) != v}
        if changed:
            self.state.update(changed)
            if self.on_state:
                self.on_state(changed)

    def _handle(self, m):
        c, p = m[:2], m[2:]
        if c == "FA" and p.isdigit():
            self._update(freq=int(p))
        elif c == "MD" and len(p) >= 2:
            self._mode_code = p[1]
            mode, data = MODES.get(p[1], (p[1], False))
            self._update(mode=mode, data=data)
            self._update_filter()
        elif c == "SH" and len(p) >= 4:
            self._sh = int(p[-2:])
            self._update_filter()
        elif c == "SM" and len(p) >= 4:
            self._update(smeter=smeter_icom(int(p[1:4])))
        elif c == "RM" and len(p) >= 4:
            v = int(p[1:4])
            if p[0] == "5":
                self._update(po=v)
            elif p[0] == "6":
                self._update(swr=swr_icom(v))
        elif c == "TX" and p:
            self._update(tx=p[0] != "0")
        elif c == "AG" and len(p) >= 4:
            self._update(af=int(p[1:4]))
        elif c == "RG" and len(p) >= 4:
            self._update(rf=int(p[1:4]))
        elif c == "PC" and p.isdigit():
            self._update(power=round(int(p) / 100 * 255))
        elif c == "KS" and p.isdigit():
            self._update(keyspeed=max(0, min(255, round((int(p) - 6) * 255 / 42))))
        elif c == "KP" and p.isdigit():
            self._update(cw_pitch=300 + 10 * int(p))
        elif c == "PA" and len(p) >= 2:
            self._update(preamp=int(p[1]))
        elif c == "RA" and len(p) >= 2:
            self._update(att=p[1] != "0")
        elif c == "NB" and len(p) >= 2:
            self._update(nb=p[1] != "0")
        elif c == "NR" and len(p) >= 2:
            self._update(nr=p[1] != "0")
        elif c == "GT" and len(p) >= 2:
            self._update(agc=AGC.get(p[1], ""))
        elif c == "BI" and p:
            self._update(break_in=int(p[0]))

    def _family(self):
        return "narrow" if self.state["mode"] in NARROW_FAMILY else "ssb"

    def _update_filter(self):
        fam = self._family()
        table = WIDTH_NARROW if fam == "narrow" else WIDTH_SSB
        width = table[self._sh] if 0 < self._sh < len(table) else 0
        fil = next((k for k, v in FILTERS[fam].items() if v == self._sh), 0)
        self._update(filter=fil, if_width=width or 0)

    def read_all(self):
        for cmd in ("FA", "MD0", "SH0", "AG0", "RG0", "PC", "KS", "KP", "PA0", "RA0", "NB0", "NR0", "GT0", "BI", "TX"):
            self.send(cmd)

    def _poller(self):
        n = 0
        while self.running:
            time.sleep(0.2)
            if self.ser is None or self.txq.qsize() > 4:
                continue
            self.send("SM0")
            if self.state["tx"]:
                self.send("RM5")
                self.send("RM6")
            n += 1
            if n % 5 == 0:
                self.send("FA")
                self.send("MD0")
                self.send("TX")
            if n % 25 == 0:
                self.read_all()

    # ---- commands (same names/meaning as IC7300) ----------------------
    def set_freq(self, hz):
        hz = int(hz)
        if not (30_000 <= hz <= 75_000_000):
            return
        self.send(f"FA{hz:09d}")
        self._update(freq=hz)

    def set_mode(self, mode, filt=None, data=None):
        data = self.state["data"] if data is None else bool(data)
        code = MODE_CODES.get((mode, data)) or MODE_CODES.get((mode, False))
        if code is None:
            return
        self.send(f"MD0{code}")
        self._update(mode=mode, data=(mode, data) in MODE_CODES and data)
        fam = "narrow" if mode in NARROW_FAMILY else "ssb"
        if filt and mode not in ("AM", "FM"):
            self.send(f"SH00{FILTERS[fam][int(filt)]:02d}")
        self.send("MD0")
        self.send("SH0")

    def set_level(self, name, value):
        value = max(0, min(255, int(value)))
        if name == "af":
            self.send(f"AG0{value:03d}")
        elif name == "rf":
            self.send(f"RG0{value:03d}")
        elif name == "power":
            self.send(f"PC{max(5, min(100, round(value / 255 * 100))):03d}")
        elif name == "keyspeed":
            self.send(f"KS{max(4, min(60, round(6 + value * 42 / 255))):03d}")
        else:
            return
        self._update(**{name: value})

    def set_preamp(self, v):
        self.send(f"PA0{int(v) % 3}")
        self.send("PA0")

    def set_att(self, on):
        self.send(f"RA0{3 if on else 0}")
        self.send("RA0")

    def set_nb(self, on):
        self.send(f"NB0{1 if on else 0}")
        self.send("NB0")

    def set_nr(self, on):
        self.send(f"NR0{1 if on else 0}")
        self.send("NR0")

    def set_agc(self, name):
        code = AGC_CODES.get(name)
        if code:
            self.send(f"GT0{code}")
            self.send("GT0")

    def set_break_in(self, mode):
        """0 = off, anything else = on (the FTDX10 has one break-in switch; delay is a menu item)."""
        self.send(f"BI{1 if mode else 0}")
        self.send("BI")

    def set_ptt(self, on):
        self.send("TX1" if on else "TX0")
        self.send("TX")

    def poll_tx(self):
        self.send("TX")

    def tune(self):
        self.send("AC002")

    def set_scope_span(self, half_span_hz):
        pass                            # no scope over CAT

    def set_scope_mode(self, mode):
        pass

    def set_scope_speed(self, speed):
        pass

    def send_cw(self, text):
        """Key text with the radio's keyer: load keyer memory 1 (KM, <= 50 chars) and
        play it (KY1), one chunk after the other. Prosigns are sent as letters."""
        text = re.sub(r"<([A-Z]{2})>", r"\1", text.upper())
        text = re.sub(r"[^A-Z0-9/?.,=+ ]", "", text).strip()
        chunks, cur = [], ""
        for w in text.split():
            if cur and len(cur) + 1 + len(w) > 50:
                chunks.append(cur)
                cur = ""
            cur = (cur + " " + w).strip()[:50]
        if cur:
            chunks.append(cur)
        if not chunks:
            return
        self.stop_cw(unkey=False)
        cancel = threading.Event()
        wpm = 6 + self.state["keyspeed"] * 42 / 255

        def run():
            for ch in chunks:
                if cancel.is_set():
                    return
                self.send(f"KM1{ch}")
                self.send("KY1")
                cancel.wait(morse_seconds(ch, wpm) + 0.3)

        th = threading.Thread(target=run, daemon=True)
        self._cw = (cancel, th)
        th.start()

    def stop_cw(self, unkey=True):
        if self._cw:
            self._cw[0].set()
            self._cw = None
        if unkey and self.ser is not None:
            self.send("TX0")

    # ---- used by the beacon monitor -----------------------------------
    def cw_narrow(self):
        """CW with a 500 Hz IF width (beacons arrive up to +-50 Hz off the pitch)."""
        self.set_mode("CW", 2, data=False)

    def snapshot(self):
        return {"freq": self.state["freq"], "mode_code": self._mode_code, "sh": self._sh}

    def restore(self, s):
        if s.get("freq"):
            self.set_freq(s["freq"])
        if s.get("mode_code"):
            self.send(f"MD0{s['mode_code']}")
            self.send(f"SH00{s['sh']:02d}")
            self.send("MD0")
            self.send("SH0")



CHAR_CODE = {v: k for k, v in MORSE.items()}


def morse_seconds(text, wpm):
    """Exact keying time with PARIS timing (dot = 1.2 / wpm s): elements, gaps, word spaces."""
    units = 0
    for word in text.split():
        for ch in word:
            code = CHAR_CODE.get(ch, "")
            units += sum(1 if e == "." else 3 for e in code) + max(0, len(code) - 1) + 3
        units += 4                     # letter space (3) + 4 = word space (7)
    return units * 1.2 / max(4, wpm)
