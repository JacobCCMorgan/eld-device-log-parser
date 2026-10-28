# Published logs: provenance, normalization and de-identification

`data/logs/` holds two firmware logs written by the Linux host application of an ELD unit
(a Go program that drives a BLE module and an ST micro-controller over two serial ports and talks
to the backend over MQTT). Both were exported from the vendor's device-management console.

| file | device | vehicle (foundation tables) | window (UTC) | lines |
|---|---|---|---|---:|
| `DEV-40f3ef472c_2026-09-23.log.gz` | `DEV-40f3ef472c` | 2020 Freightliner, customer unit, J1939 bus | 2026-09-23 21:41 to 2026-09-24 01:29 | 69,692 |
| `DEV-ada95db42a_2026-09-15.log.gz` | `DEV-ada95db42a` | 2025 passenger vehicle, vendor test unit, OBD-II firmware build | 2026-09-15 15:00 to 15:59 | 15,639 |

`device_key` and the VIN pseudonyms inside the logs are the same keyed pseudonyms used by the
[`telematics-data-foundation`](https://github.com/JacobCCMorgan/telematics-data-foundation) tables, so
the two devices can be joined to `dim_device` / `dim_vehicle` there.

## Normalization

The raw files mix English identifiers with localized (non-English) operator messages. Before
publication every localized phrase was replaced by an English token from a private phrase map
(about 200 phrases) and full-width punctuation by its ASCII equivalent; `eldlog normalize` applies the
map and reports any residual non-ASCII character (there is none in the published files). The parser
in this repository operates on the normalized form; the map itself is not published.

## De-identification (`src/eldlog/normalize.py`)

| what | rule |
|---|---|
| device serial | `DEV-<hmac>` (10 hex chars), also inside ASCII-hex byte dumps |
| VIN | `VIN-<hmac>`, also inside ASCII-hex byte dumps (ECM frames carry the VIN as ASCII bytes) |
| IMEI, ICCID | `IMEI-<hmac>`, `ICCID-<hmac>`; MQTT topics therefore read `MProd/ELD/IMEI-...` |
| driver id | `DRV-<hmac>`; the placeholder `NODRIVERID` is kept as is |
| latitude / longitude | rounded to a 0.05 degree grid in text and JSON payloads; the binary GPS frames (ST command 0x42 replies and the decoded `GPSData:` lines) are masked with `xx` |
| broker host, IPv4 addresses, URLs | `<broker-host>`, `<ip>`, `<url>` |
| API tokens, login keys, CA checksums, OTA nonces | `<redacted>` |
| SIM carrier name | `<carrier>` |

Byte dumps whose bytes changed have their `[N byte]` length prefix recomputed; the trailing checksum
byte is left as it was and no longer verifies for those frames. Nothing else in the logs was altered:
timestamps, command codes, return codes, payload fields, error lines and ordering are original.

The HMAC key lives outside the repository (`TDF_DEID_KEY_FILE`); the phrase map likewise
(`ELDLOG_PHRASE_MAP`). Re-identification of devices, vehicles or people from these files is not
possible without both and is prohibited by the data terms in `LICENSE`.
