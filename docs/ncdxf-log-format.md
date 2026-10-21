# NCDXF beacon propagation log format

Format `ncdxf-propagation-log/1`. JSON Lines, one object per line, UTC dates in the file names.

| File | One record per | Use |
|---|---|---|
| `passes-YYYY-MM-DD.jsonl` | 15-minute UTC window (:00 :15 :30 :45) | the compact view; start here (≈96/day, ≈2 KB each) |
| `slots-YYYY-MM-DD.jsonl` | 10-second beacon slot measured | full detail (≈8640/day in cycle mode, ≈1.3 KB each) |

## What was measured
The [NCDXF/IARU beacons](https://www.ncdxf.org/beacon/): 18 beacons on 14.100, 18.110, 21.150, 24.930, 28.200 MHz,
each sending its callsign at 22 WPM then four 1-second dashes at **100 W, 10 W, 1 W, 0.1 W**, in 10-second slots of a
3-minute cycle. Receiver: the selected radio (IC-7300 or FTDX10, see `receiver.radio`) in CW mode, at the station in `rx_station`. In `cycle` mode the radio changes band every slot,
so every 15-minute window measures all 90 beacon/band pairs once (`"complete": true`). `band` mode measures one band
(18 per 3 min), `follow` mode one beacon on all bands.

Detection is from the receiver audio: the beacon's callsign keying must stand out from the gaps between its own elements by
`id_threshold_db` (6 dB; noise alone reaches ~5 dB), and each dash's S/N is measured in a 30 Hz bandwidth against the
noise in those gaps. A dash counts as heard at S/N ≥ `heard_threshold_snr_db` (0 dB). On synthetic tests: no false
detections in 500 noise-only slots; beacons recognised down to ~6 dB below the noise in a 500 Hz bandwidth.

## Reading the results
- `heard: false` means *not detected in that one 10-second slot* — the path may be closed, the beacon in a fade (QSB),
  covered by QRM, or **the beacon may be off the air** (check its record on other bands/times before concluding the path
  is closed). Recurring silence of one beacon on all bands at all times suggests it is off the air.
- `dashes_heard` / `weakest_power_heard` is the most robust signal-strength measure: each dash is 10 dB weaker than the
  previous, so 3 dashes (down to 1 W) means roughly 20 dB more margin than 1 dash. It is counted from 100 W down and
  stops at the first dash not heard.
- `snr_db` values are compressed by the receiver AGC (see `receiver.agc`); compare them within the same settings only.
  -10.0 is the floor (nothing above noise).
- `noise_db_rel` is the relative noise level in the beacon's gaps (arbitrary dB, same settings comparable): rising values
  mean more band noise/QRM.
- In `passes`, `matrix_dashes_heard[call][band]` is `null` when that pair was not measured in the window, `0` when
  measured and not heard.

## Fields (slot record)
| Field | Meaning |
|---|---|
| `utc`, `rx_local_time` | slot start; receiver local time in the PC's time zone, with its UTC offset |
| `band_m`, `freq_khz` | band and beacon frequency |
| `beacon` | call, location, Maidenhead grid, country, lat/lon of the transmitter |
| `rx_station` | receiving station call, grid, lat/lon |
| `path` | great-circle distance, bearing from the receiver, path midpoint (short path assumed) |
| `sun.{rx,tx,path_midpoint}` | solar elevation (deg) and `day` (> 6°), `greyline` (−6…6°), `night` (< −6°) at slot time |
| `result` | `heard`, `dashes_heard` (0–4), `weakest_power_heard`, `snr_db` per dash, `id_contrast_db`, `tone_offset_hz` (vs CW pitch), `timing_offset_s` (audio latency), `noise_db_rel` |
| `receiver` | radio, mode, filter and IF width, preamp, attenuator, AGC, NB, NR, RF gain, CW pitch during the slot |
| `space_weather` | NOAA SWPC, refreshed every 15 min: `sfi` (10.7 cm flux), `kp` (latest 3-hour Kp) + `kp_utc`, `kp_24h_max`, `a_running`, `xray_class` (current GOES 0.1–0.8 nm), `xray_6h_max_class`, `noaa_scales` R/S/G |
| `monitor` | monitor mode and detection thresholds |
| `pc_clock` | NTP sync of the PC clock: synced, offset and error bound (ms), NTP reference |

## Fields (pass record)
`window_start_utc`, `window_end_utc`, `rx_local_time` (window middle), `monitor_mode`, `slots_measured`, `complete`,
`heard_count`, `rx_sun`, `space_weather` (latest in the window), `bands[band].measured` / `.heard[]` (call, location, km,
bearing, weakest power heard, 100 W S/N, time), `heard_bearings_deg`, `matrix_dashes_heard`, and `summary` — one
plain-English sentence of the window.

## Suggested questions for analysis
Which bands open to which directions at which UTC/local hours; how openings depend on sun at the path midpoint and on
SFI/Kp; the lowest power (margin) per path and band; when a band closes after local sunset; whether geomagnetic
activity (Kp ≥ 4) shortens openings on northern paths (OH2B, RR9O, VE8AT).
