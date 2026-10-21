# NCDXF Monitor

Monitors the [NCDXF/IARU International Beacon Project](https://www.ncdxf.org/beacon/) with an **Icom IC-7300** or a
**Yaesu FTDX10** on USB, shows which beacons are heard on which band in the browser, and writes a JSON Lines
propagation log (with space weather and NTP clock status) for later analysis, e.g. by an LLM.

    pip install -r requirements.txt
    ./run.sh --call N0CALL --grid FN31pr        # then open http://localhost:8075

Linux with PipeWire (`pw-record`) for the receive audio. The PC clock must be NTP-synchronised.

## The beacons
18 beacons share 14.100, 18.110, 21.150, 24.930 and 28.200 MHz in a 3-minute cycle of 10-second slots from 00:00 UTC.
Each sends its callsign at 22 WPM, then four 1-second dashes at **100 W, 10 W, 1 W and 0.1 W**.

## Using it
- **Station**: call and grid (Maidenhead) for distances, bearings and the log; saved in `settings.json`.
- **Modes**: *Cycle all bands* hops band every slot, so all 90 beacon/band pairs are measured in 15 minutes;
  *One band* hears all 18 beacons every 3 minutes; *Follow one beacon* tracks one beacon up through the bands.
- The matrix shows per beacon and band how many dashes were heard (bars, 100 W down) and the S/N of the 100 W dash;
  hover for all four dashes, callsign contrast, tone and timing. The orange cell is the slot being received.
- Receive only. While running, the radio is in CW with a 500 Hz filter (IC-7300 FIL2, FTDX10 width 500 Hz) and retuned
  0.4 s before each slot; 1.5 s into each slot the reported frequency is checked (a dropped command is retuned and that
  slot skipped). Frequency, mode and filter are restored on Stop or when the program exits.
- Run unattended: `./run.sh --autostart cycle` starts as soon as radio, audio and clock are ready.
  `--host 0.0.0.0` to open it from other machines; `GET /api/status` returns everything as JSON.

## Map and space weather
- **Map** beside the table: your station (orange triangle), the 18 beacons, and a great-circle path to each beacon
  measured in the last 15 minutes, coloured by the weakest dash heard (green = down to 0.1 W, yellow-green 1 W,
  yellow 10 W, orange 100 W only, grey dashed = not heard). *Best of all bands* or one band; the beacon sending now has
  an orange ring; hover a beacon for details. *World* shows night and greyline shading from the current sun position;
  *Azimuthal from your QTH* is centred on you, so each path is a straight line at its true bearing (rings every 5000 km).
  Coastlines: Natural Earth 110 m land (public domain) via world-atlas, converted to `static/land.json`.
- **Header**: NOAA SWPC space weather, refreshed every 15 minutes: SFI (10.7 cm solar flux), Kp (yellow at 4 = active,
  red from 5 = storm), current GOES X-ray class (yellow M, red X flares: fadeouts on the sunlit side), the NOAA R/S/G
  scales, and the sun's elevation at your station (day / greyline / night). Hover for times and details.
- On restart the last 15 minutes of results are reloaded from the slot log.

## How a beacon is detected
From the receive audio of each slot: the beacon's own callsign keying (known exactly: 54 ms elements) is matched against
the audio to find the timing (searched −0.1…+0.6 s for audio latency) and tone (±100 Hz around the CW pitch). The keying
must stand out from the gaps between its elements by 6 dB — that identifies the beacon and rejects carriers and other
stations. Each dash's S/N is measured in a 30 Hz track against the noise in those gaps; a dash counts as heard at
S/N ≥ 0 dB. On synthetic audio: no false detections in 500 noise-only slots, beacons recognised down to ~6 dB below the
noise in a 500 Hz bandwidth. Receiver AGC compresses the dB values but not the heard / not-heard steps.

## Logs (`logs/`)
- `ncdxf/passes-YYYY-MM-DD.jsonl`: one summary per 15-minute UTC window, with a plain-English `summary` — the compact
  view to analyse.
- `ncdxf/slots-YYYY-MM-DD.jsonl`: every slot: path geometry, sun elevation at both ends and the path midpoint,
  measurement, receiver settings, NOAA SWPC space weather (SFI, Kp, X-ray class, R/S/G scales), PC clock status.
- `beacons.csv`: one row per slot.

Field reference: [docs/ncdxf-log-format.md](docs/ncdxf-log-format.md).

## Radios
Detected by USB id at start and every 2 s (the radio dropdown updates live; the monitor switches to a plugged-in radio
when the one in use is unplugged, unless it is monitoring):
- **IC-7300**: CP2102 `10c4:ea60` whose USB serial number reads "IC-7300 …". CI-V at 115200 baud, address 94h.
- **FTDX10**: CP2105 `10c4:ea70`, interface 0 (Enhanced port = CAT). Baud probed 38400/19200/9600/4800. The driver
  checks the CAT ID (`ID0761`). Written from the FTDX10 CAT manual and tested against a simulated radio.

The receive audio is the USB sound card on the same USB hub as the radio's serial port (both radios use a TI
"USB Audio CODEC", so the name alone can't tell them apart). Overrides: `--radio`, `--serial`, `--audio`, or
`settings.json` → `{"ports": {"FTDX10": "/dev/ttyUSB1"}}`.

Only one program can control the radio at a time: stop other CAT software first.

## PC clock
Checked at start and every minute (`clock.py`: chrony's offset and error bound, else systemd's NTPSynchronized) and
shown in the header. Monitoring refuses to start when the clock is not synchronised or more than 100 ms off.

## Files
`monitor.py` (web server, audio, radio detection), `beacons.py` (schedule, slot analysis, monitor loop),
`beaconlog.py` (JSONL log), `spaceweather.py` (NOAA SWPC), `clock.py` (NTP check), `rig.py` (radio detection,
port + audio pairing), `radio.py` (IC-7300 CI-V), `ftdx10.py` (FTDX10 CAT), `geo.py` (cty.dat prefixes, grid,
distance/bearing; downloads `vendor/cty.dat` on first use), `morse.py`, `static/` (page).
