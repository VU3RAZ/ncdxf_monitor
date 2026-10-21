#!/usr/bin/env python3
"""NCDXF/IARU beacon monitor for an Icom IC-7300 or Yaesu FTDX10.

Tunes the radio through the beacon schedule, measures every 10-second slot from the
receive audio, shows the beacon/band matrix in the browser and writes a JSONL
propagation log (logs/ncdxf/) with space weather and NTP clock status.

Usage: python3 monitor.py [--port 8075] [--call N0CALL --grid FN31] [--autostart cycle]
Then open http://localhost:8075
"""
import argparse
import asyncio
import json
import os
import shutil
import time

import numpy as np
from aiohttp import web, WSMsgType

import beaconlog
import beacons
import clock
import geo
import rig
from beacons import BeaconMonitor
from morse import FS
from radio import IC7300

HERE = os.path.dirname(os.path.abspath(__file__))


class App:
    def __init__(self, args):
        self.args = args
        self.loop = None
        self.clients = set()
        s = rig.load_settings()
        self.station = {"call": (args.call or s.get("station", {}).get("call", "")).upper(),
                        "grid": args.grid or s.get("station", {}).get("grid", "")}
        detected = [r["name"] for r in rig.available() if r["connected"]]
        last = s.get("radio")
        self.radio_name = (args.radio or (last if last in detected else None)
                           or (detected[0] if detected else None) or last or "IC-7300")
        self.radio = self.make_radio(self.radio_name, startup=True)
        self.audio = None
        self.audio_ok = False
        self.clock = clock.status()
        self.beacons = BeaconMonitor(self)
        cfg = s.get("monitor", {})
        self.beacons.cfg.update({k: cfg[k] for k in ("mode", "band", "beacon") if k in cfg})

    # ---- broadcast ------------------------------------------------------
    def _send_all(self, text):
        for ws in list(self.clients):
            if ws.closed:
                self.clients.discard(ws)
            else:
                asyncio.ensure_future(ws.send_str(text))

    def broadcast_json(self, **msg):
        if self.loop:
            self.loop.call_soon_threadsafe(self._send_all, json.dumps(msg))

    # ---- station ----------------------------------------------------------
    def my_location(self):
        """(lat, lon) of the station: its grid locator, else the callsign's country."""
        loc = geo.grid_to_latlon(self.station["grid"])
        e = None if loc or not self.station["call"] else geo.lookup(self.station["call"])
        return loc or ((e["lat"], e["lon"]) if e else None)

    def set_station(self, call, grid):
        call = "".join(ch for ch in str(call).upper() if ch.isalnum() or ch == "/")[:12]
        grid = str(grid).strip()[:8]
        if grid and not geo.grid_to_latlon(grid):
            raise RuntimeError(f"not a Maidenhead locator: {grid}")
        self.station = {"call": call, "grid": grid}
        rig.save_settings(station=self.station)
        self.broadcast_json(type="station", station=self.station)
        self.beacons.broadcast()

    # ---- radio ------------------------------------------------------------
    def make_radio(self, name, startup=False):
        a = self.args
        port = a.serial if startup and a.serial else rig.find_port(name)
        return IC7300(port) if name == "IC-7300" else rig.make(name, port)

    def radios_info(self, avail=None):
        return {"type": "radios", "current": self.radio_name, "radios": avail or rig.available(), "caps": self.radio.caps}

    def on_state(self, changed):
        self.broadcast_json(type="state", state=changed)

    def attach_radio(self):
        self.radio.on_state = self.on_state
        self.radio.on_scope = None
        self.radio.start()
        if self.audio:
            self.audio.cancel()
        self.audio_ok = False
        self.audio = asyncio.ensure_future(self.audio_task())

    async def select_radio(self, name):
        if name not in rig.RADIOS:
            raise RuntimeError(f"unknown radio {name}")
        if name == self.radio_name:
            return
        if self.beacons.active:
            raise RuntimeError("stop the monitor before switching radios")
        await self.loop.run_in_executor(None, self.radio.stop)
        self.radio_name, self.radio = name, self.make_radio(name)
        rig.save_settings(radio=name)
        self.attach_radio()
        self.broadcast_json(**self.radios_info())
        self.broadcast_json(type="state", state=self.radio.state)

    async def radio_watch(self):
        """Every 2 s: which radios are plugged in; follow a re-plugged radio, switch when the
        one in use is unplugged and another is plugged in (not while monitoring)."""
        last = None
        while True:
            await asyncio.sleep(2)
            try:
                avail = await self.loop.run_in_executor(None, rig.available)
            except Exception as e:
                print("radio detection:", e)
                continue
            key = [(r["name"], r["connected"], r["port"]) for r in avail]
            if key == last:
                continue
            if last is not None:
                was = {n: c for n, c, _ in last}
                for n, c, _ in key:
                    if c != was.get(n):
                        self.notice(f"{n} {'detected' if c else 'disconnected'}")
            last = key
            cur = next(r for r in avail if r["name"] == self.radio_name)
            if cur["connected"] and cur["port"] != self.radio.port:
                self.radio.port = cur["port"]
            other = next((r["name"] for r in avail if r["connected"] and r["name"] != self.radio_name), None)
            if not cur["connected"] and other and not self.beacons.active:
                await self.select_radio(other)
                self.notice(f"switched to {other} ({cur['name']} not connected)")
                continue
            self.broadcast_json(**self.radios_info(avail))

    def notice(self, msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)
        self.broadcast_json(type="notice", msg=msg)

    # ---- clock --------------------------------------------------------------
    def clock_msg(self):
        return {"type": "clock", "clock": self.clock, "text": clock.describe(self.clock)}

    async def clock_watch(self):
        while True:
            try:
                s = await self.loop.run_in_executor(None, clock.status)
                if s.get("ok") != self.clock.get("ok"):
                    self.notice(clock.describe(s))
                self.clock = s
                self.broadcast_json(**self.clock_msg())
            except Exception as e:
                print("clock check:", e)
            await asyncio.sleep(60)

    # ---- audio ----------------------------------------------------------------
    def audio_node(self):
        return self.args.audio or rig.audio_node_for(self.radio.port)

    async def audio_task(self):
        while True:
            node = self.audio_node()
            if not (shutil.which("pw-record") and node):
                self.notice("no receive audio: radio's USB sound card not found (PipeWire)" if node is None
                            else "pw-record not installed")
                await asyncio.sleep(5)
                continue
            proc = await asyncio.create_subprocess_exec(
                "pw-record", "--target", node, "--rate", str(FS), "--channels", "1", "--format", "s16",
                "--latency", "50ms", "--raw", "-", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            chunk = FS // 10 * 2
            try:
                while True:
                    data = await proc.stdout.readexactly(chunk)
                    if not self.audio_ok:
                        self.audio_ok = True
                        self.broadcast_json(type="audio", ok=True, node=node)
                    self.beacons.feed(np.frombuffer(data, np.int16).astype(np.float32) / 32768, time.time())
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                if proc.returncode is None:
                    proc.kill()
            self.audio_ok = False
            self.broadcast_json(type="audio", ok=False, node=node)
            await asyncio.sleep(2)

    # ---- monitor ----------------------------------------------------------------
    def start_monitor(self, cfg):
        self.beacons.start(cfg)
        rig.save_settings(monitor=dict(self.beacons.cfg))
        self.beacons.broadcast()

    async def autostart(self, mode):
        """--autostart: begin once radio, audio and clock are ready (retrying, reasons printed)."""
        last = None
        while not self.beacons.active:
            try:
                self.start_monitor({"mode": mode, "band": self.args.band, "beacon": self.args.beacon})
                self.notice(f"monitor started ({mode})")
                return
            except RuntimeError as e:
                if str(e) != last:
                    print("autostart waiting:", e, flush=True)
                    last = str(e)
            await asyncio.sleep(5)

    # ---- web ----------------------------------------------------------------------
    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        for msg in (self.radios_info(), {"type": "state", "state": self.radio.state}, self.clock_msg(),
                    {"type": "station", "station": self.station}, {"type": "audio", "ok": self.audio_ok},
                    {"type": "beacons", "data": self.beacons.public()}):
            await ws.send_str(json.dumps(msg))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    await self.handle_cmd(json.loads(msg.data))
                except Exception as e:
                    await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))
        finally:
            self.clients.discard(ws)
        return ws

    async def handle_cmd(self, m):
        c = m.get("cmd")
        if c == "beacon_start":
            self.start_monitor(m.get("cfg", {}))
        elif c == "beacon_stop":
            await self.beacons.stop()
        elif c == "select_radio":
            await self.select_radio(m["name"])
        elif c == "station":
            self.set_station(m.get("call", ""), m.get("grid", ""))

    async def status_handler(self, request):
        """GET /api/status: everything the page shows, for scripts."""
        return web.json_response({"radio": self.radio_name, "radio_state": self.radio.state, "audio": self.audio_ok,
                                  "clock": self.clock, "station": self.station, "monitor": self.beacons.public()})

    async def on_startup(self, app):
        self.loop = asyncio.get_running_loop()
        self.attach_radio()
        self.tasks = [asyncio.ensure_future(self.radio_watch()), asyncio.ensure_future(self.clock_watch())]
        print(f"{self.radio_name} on {self.radio.port or '(not detected)'}; {clock.describe(self.clock)}", flush=True)
        if self.args.autostart:
            self.tasks.append(asyncio.ensure_future(self.autostart(self.args.autostart)))

    async def on_cleanup(self, app):
        await self.beacons.stop()             # restores the radio
        for t in self.tasks + [self.audio]:
            t.cancel()
        await self.loop.run_in_executor(None, self.radio.stop)


@web.middleware
async def no_cache(request, handler):
    resp = await handler(request)
    if request.path == "/" or request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from other machines")
    ap.add_argument("--port", type=int, default=8075)
    ap.add_argument("--radio", choices=list(rig.RADIOS), help="radio to use (default: last used / detected)")
    ap.add_argument("--serial", help="CAT serial port (default: detected by USB id)")
    ap.add_argument("--audio", help="PipeWire capture node (default: the radio's USB sound card)")
    ap.add_argument("--call", help="your callsign (saved)")
    ap.add_argument("--grid", help="your Maidenhead locator, e.g. FN31pr (saved; used for distances)")
    ap.add_argument("--autostart", choices=["cycle", "band", "follow"], help="start monitoring when ready")
    ap.add_argument("--band", type=int, default=0, help="band index for --autostart band: 0=20m 1=17m 2=15m 3=12m 4=10m")
    ap.add_argument("--beacon", type=int, default=0, help="beacon index 0-17 for --autostart follow")
    ap.add_argument("--logdir", help="log folder (default ./logs)")
    ap.add_argument("--settings", help="settings file (default ./settings.json)")
    args = ap.parse_args()
    if args.settings:
        rig.SETTINGS = args.settings
    if args.logdir:
        beaconlog.LOG_DIR = os.path.join(args.logdir, "ncdxf")
        beacons.LOG_PATH = os.path.join(args.logdir, "beacons.csv")
    if args.grid and not geo.grid_to_latlon(args.grid):
        ap.error(f"--grid {args.grid}: not a Maidenhead locator")
    if args.call or args.grid:
        s = rig.load_settings().get("station", {})
        rig.save_settings(station={"call": (args.call or s.get("call", "")).upper(), "grid": args.grid or s.get("grid", "")})

    a = App(args)
    app = web.Application(middlewares=[no_cache])
    app.on_startup.append(a.on_startup)
    app.on_cleanup.append(a.on_cleanup)
    app.router.add_get("/ws", a.ws_handler)
    app.router.add_get("/api/status", a.status_handler)
    app.router.add_get("/", lambda r: web.FileResponse(os.path.join(HERE, "static", "index.html")))
    app.router.add_static("/static", os.path.join(HERE, "static"))
    print(f"Open http://{args.host}:{args.port}", flush=True)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
