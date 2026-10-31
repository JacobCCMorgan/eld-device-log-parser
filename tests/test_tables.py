"""Table construction on synthetic mini-logs and invariants on the published logs."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eldlog import metrics
from eldlog.normalize import Deidentifier, PhraseMap, Pseudonymizer, coarsen, normalize_line
from eldlog.parse import coverage, parse_file, parse_many
from eldlog.tables import boot_sessions, build_all, state_transitions, uart_commands, uploads

ROOT = Path(__file__).resolve().parents[1]
LOGS = sorted((ROOT / "data" / "logs").glob("*.log.gz"))


def _write_log(tmp_path: Path, name: str, bodies: list[tuple[str, str, str]]) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(f"{lvl:<7}:{ts} {body}" for lvl, ts, body in bodies) + "\n")
    return p


@pytest.fixture
def mini_log(tmp_path):
    t = "2026/09/23 21:41:"
    rows = [
        ("Consol", t + "09", "[main] VERSION: v1.5.0"),
        ("Info", t + "09", "[main] System start..."),
        ("Info", t + "10", "[Get4GInfor]RSRP value: -74dBm, len: 6"),
        ("Info", t + "23", "[init]:14.0s"),
        ("Info", t + "22", "[MQTT connectHandler]Connected"),
        ("Info", t + "45", "[Standby] standby mode flow start..."),
        ("Info", t + "46", "[STGPSDataGet] query GPS data start...."),
        ("Info", t + "46", "Send_data[5 byte]: 80 00 02 42 C0"),
        ("Info", t + "46", "[BLELedSet] set LED color start...."),
        ("Info", t + "46", "Send_data[14 byte]: 60 09 80 50 89 01 02 00 05 DC 01 F4 FF E0"),
        ("Info", t + "46", "Read_data[8 byte]: 06 03 80 50 89 00 01 5D"),
        ("Info", t + "46", "[BLEMessageGet]cmd: 50"),
        ("Info", t + "46", "[BLEMessageGet]Retcode: 0001"),
        ("Info", t + "46", "[BLELedSet] Retcode: 0001"),
        ("Info", t + "47", "Read_data[19 byte]: 08 00 10 42 xx xx xx xx xx xx xx xx xx xx xx xx xx xx xx"),
        ("Info", t + "47", "[STMessageGet] cmd: 42"),
        ("Info", t + "47", "[STGPSDataGet] Retcode: 0001"),
        ("Info", t + "48", "[STBatteryCheck] query battery present start...."),
        ("Info", t + "48", "Send_data[5 byte]: 80 00 02 51 D3"),
        ("Info", t + "49", "[WaitSTCmdReply] want cmd 51 timeout, use spilled FF ret=0005 for cmd-mismatch retry"),
        ("Info", t + "49", "[STBatteryCheck] ret/cmd=0005/FF want cmd 51, retry 1/3"),
        ("Info", t + "49", "Send_data[8 byte]: 60 03 80 23 89 00 01 48"),
        ("Consol", t + "49", "[MQTT Publish]--- publish message >>:Topic:MProd/ELD/IMEI-0123456789"),
        ("Consol", t + "49", '[MQTT Publish]Payload:{"mode":"1","is_stored":"0","timestamp":"20260923214148","speed":"0","rpm":"0","gps_speed":"0"}'),
        ("Info", t + "50", "[MQTT messageHandler]--- received message <<:Topic:MProd/S/IMEI-0123456789/ack"),
        ("Info", t + "50", '[MQTT messageHandler]Payload(MSG):{"timestamp": "20260923214148", "timezone": "-04:00", "mode": "1", "is_stored": "0"}'),
        ("Info", t + "50", "[Driving] driving mode flow start..."),
        ("Info", t + "53", "[Driving]GPS vehicle speed below 8 km/h and Speed and RPM are both N/A or 0, system will enter sleeping mode"),
        ("Info", t + "53", "[Sleeping] sleeping mode flow start..."),
        ("Consol", t + "55", "[MQTT Publish]--- publish message >>:Topic:MProd/ELD/IMEI-0123456789"),
        ("Consol", t + "55", '[MQTT Publish]Payload:{"mode":"2","is_stored":"0","timestamp":"20260923214154","speed":"10","rpm":"900","gps_speed":"12"}'),
        ("Error", t + "59", "/work/iot/src/main/Controller/controller_mqtt.go:962: [WaitForAck]Time out"),
        ("Info", t + "59", "[SendDMS] backend no response, written to re-upload folder"),
        ("Consol", "2026/09/23 21:42:05", '[MQTT Publish]Payload:{"mode":"2","is_stored":"1","timestamp":"20260923214154","speed":"10","rpm":"900","gps_speed":"12"}'),
        ("Info", "2026/09/23 21:42:06", '[MQTT messageHandler]Payload(MSG):{"timestamp": "20260923214154", "timezone": "-04:00", "mode": "2", "is_stored": "1"}'),
    ]
    return _write_log(tmp_path, "DEV-abcdefabcd_test.log", rows)


def test_parse_file_shapes(mini_log):
    lines = parse_file(mini_log)
    assert len(lines) == 35
    assert lines["device_key"].iloc[0] == "DEV-abcdefabcd"
    assert (lines["category"] == "other").sum() == 0
    cov = coverage(lines)
    assert abs(cov["share"].sum() - 1) < 1e-9


def test_boot_session_starts_at_version_line(mini_log):
    s = boot_sessions(parse_file(mini_log))
    assert len(s) == 1
    assert s.iloc[0]["version"] == "v1.5.0" and s.iloc[0]["rsrp_dbm"] == -74 and s.iloc[0]["boot_observed"]


def test_state_transitions_and_triggers(mini_log):
    t = state_transitions(parse_file(mini_log))
    assert list(t["to_state"]) == ["Standby", "Driving", "Sleeping"]
    assert t.iloc[0]["trigger"] == "boot"
    assert t.iloc[1]["trigger"].startswith("standby complete")
    assert t.iloc[2]["trigger"].startswith("speed below")
    assert t.iloc[0]["duration_s"] == 5


def test_uart_pairing_is_per_bus(mini_log):
    c = uart_commands(parse_file(mini_log))
    req = c[c["direction"] == "request"].set_index("cmd")
    assert req.loc["50", "bus"] == "BLE" and req.loc["50", "outcome"] == "ok"
    assert req.loc["42", "bus"] == "ST" and req.loc["42", "outcome"] == "ok" and req.loc["42", "latency_s"] == 1
    assert req.loc["51", "outcome"] == "timeout" and req.loc["51", "retries"] == 1
    assert (c[c["cmd"] == "23"]["direction"] == "notify-reply").all()


def test_upload_ack_matching_prefers_same_is_stored(mini_log):
    u = uploads(parse_file(mini_log))
    assert len(u) == 3
    first = u.iloc[0]
    assert first["acked"] and first["ack_latency_s"] == 1
    original, resend = u.iloc[1], u.iloc[2]
    assert not original["acked"]
    assert resend["is_stored"] == 1 and resend["acked"] and resend["ack_latency_s"] == 1
    rec = metrics.offline_recovery(u, pd.DataFrame())
    assert len(rec) == 1 and rec.iloc[0]["delivery_delay_s"] == 11


# ---------------------------------------------------------------- normalization / de-identification

def test_coarsen_and_deidentify():
    p = Pseudonymizer(b"0123456789abcdef0123456789abcdef")
    d = Deidentifier(p)
    d.register_hex_identifiers(["RT12345678", "1FTSW21P05EB12345"])
    body = "[init]SN: RT12345678 IMEI: 350123456789012 ICCID: 89012345678901234567 VIN 1FTSW21P05EB12345 at 36.325472,-82.837361"
    out = d.body(body)
    assert "RT12345678" not in out and "1FTSW21P05EB12345" not in out and "350123456789012" not in out and "89012345678901234567" not in out
    assert out.count("DEV-") == 1 and out.count("VIN-") == 1 and "IMEI-" in out and "ICCID-" in out
    assert coarsen("36.325472") == "36.35" and coarsen("-82.837361") == "-82.85"
    hexdump = "Send_data[23 byte]: 60 12 80 30 89 " + " ".join(f"{b:02X}" for b in b"RTHA55 RT12345678") + " 6E"
    out2 = d.body(hexdump)
    assert "52 54 31 32 33 34 35 36 37 38" not in out2 and out2.startswith("Send_data[27 byte]: ")
    assert d.body("GPSData: 01 52 0C C6 EE 00 24 04 F7 51 00 00") == "GPSData: xx xx xx xx xx xx xx xx xx xx xx xx"
    assert d.body('Response: {"token":"abc.def","host":"broker.example.com:8883"}') == 'Response: {"token":"<redacted>","host":"<broker-host>"}'
    assert d.body("[Login]key: 035043503724056535043503") == "[Login]key: <redacted>"


def test_normalize_line_keeps_prefix_and_reports_residual():
    # non-ASCII phrases are built at runtime so the test file itself stays ASCII
    word, nbsp, other = "donn" + chr(0xE9) + "es", chr(0xA0), "fin" + chr(0xEE)
    pm = PhraseMap({word: "data"}, {nbsp: " "})
    out, residual = normalize_line(f"Info   :2026/09/23 21:41:09 [Driving] {word}{nbsp}:1\n", pm, None)
    assert out == "Info   :2026/09/23 21:41:09 [Driving] data:1" and not residual
    out, residual = normalize_line(f"Info   :2026/09/23 21:41:09 [Driving] {other}", pm, None)
    assert residual and "?" in out and out.isascii()


# ---------------------------------------------------------------- invariants on the published logs

@pytest.mark.skipif(not LOGS, reason="published logs not present")
def test_published_logs_are_ascii_and_fully_parsed():
    import gzip
    for p in LOGS:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                assert line.isascii(), (p.name, line[:80])
    lines = parse_many(LOGS)
    assert (lines["category"] == "other").sum() == 0
    assert lines["ts"].notna().all()


@pytest.mark.skipif(not LOGS, reason="published logs not present")
def test_published_tables_consistency():
    lines = parse_many(LOGS)
    T = build_all(lines)
    u = T["uploads"]
    assert u["acked"].mean() > 0.95
    assert (u.loc[u["acked"], "ack_latency_s"] >= 0).all()
    c = T["uart_commands"]
    req = c[c["direction"] == "request"]
    assert set(req["bus"]) <= {"BLE", "ST"}
    # the device clock can step backwards when it re-syncs (one 18 s step in the test-unit log); tolerate a handful
    assert (req.loc[req["rx_ts"].notna(), "latency_s"] >= 0).mean() > 0.999
    assert req["ok"].mean() > 0.95
    st = T["state_transitions"]
    assert set(st["to_state"]) <= {"Standby", "Driving", "Sleeping", "Upgrade"}
    assert (st["duration_s"].dropna() >= 0).all()
    bs = T["boot_sessions"]
    assert (bs["duration_s"] > 0).all()
    assert np.isfinite(bs["lines"]).all() and bs["lines"].sum() == len(lines)
