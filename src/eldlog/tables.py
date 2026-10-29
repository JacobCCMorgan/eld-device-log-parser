"""Derived event tables built from the classified `lines` table.

    boot_sessions      one row per power-on / restart: firmware version, modem signal, init and
                       self-test timings, wake-up reason, session length
    state_transitions  one row per state-machine flow start with the trigger that caused it
    uploads            one row per MQTT publish: decoded payload, matched ack and ack latency
    uart_commands      one row per serial command round-trip (BLE module / ST micro-controller)
    readings           long table of per-cycle sensor readings tagged with the state
    config_io          reads and writes of /config/* keys
    errors             Error-level lines, ERR-event emission attempts and suppressions,
                       timeouts, retries, connection losses
    upload_queue       offline buffering: fallback writes and re-upload batches
    api_calls          backend REST round-trip times
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

ACK_TOPIC = re.compile(r"/ack$")


def _params(row) -> dict:
    return json.loads(row) if isinstance(row, str) and row else {}


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


# ---------------------------------------------------------------- boot sessions

def boot_sessions(lines: pd.DataFrame) -> pd.DataFrame:
    out = []
    for dev, g in lines.groupby("device_key", sort=False):
        g = g.sort_values("line_no")
        starts = g[g["pattern"] == "boot_start"]
        pre = g[g["pattern"].isin(["shell", "boot_version"])]
        bounds = []
        for ln in starts["line_no"]:
            before = pre[(pre["line_no"] < ln) & (pre["line_no"] >= ln - 25)]
            bounds.append(int(before["line_no"].min()) if len(before) else int(ln))
        bounds.append(int(g["line_no"].max()) + 1)
        for i, (lo, hi) in enumerate(zip(bounds[:-1], bounds[1:]), start=1):
            seg = g[(g["line_no"] >= lo) & (g["line_no"] < hi)]
            p = {k: None for k in ["version", "rsrp_dbm", "rsrq_db", "snr_db", "net_type", "init_s", "selftest_ms", "wakeup_reason"]}
            ver = seg[seg["pattern"] == "boot_version"]
            if len(ver):
                p["version"] = _params(ver.iloc[0]["params"]).get("version")
            for _, r in seg[seg["pattern"] == "modem_info"].iterrows():
                d = _params(r["params"])
                f, v = d.get("field"), d.get("value", "").strip()
                num = re.search(r"-?\d+", v)
                if f == "RSRP" and num:
                    p["rsrp_dbm"] = int(num.group(0))
                elif f == "RSRQ" and num:
                    p["rsrq_db"] = int(num.group(0))
                elif f == "SNR" and num:
                    p["snr_db"] = int(num.group(0))
                elif f == "NetType":
                    p["net_type"] = v
            it = seg[seg["pattern"] == "init_timing"]
            if len(it):
                p["init_s"] = float(_params(it.iloc[0]["params"])["seconds"])
            st = seg[seg["pattern"] == "selftest_timing"]
            if len(st):
                d = _params(st.iloc[0]["params"])
                p["selftest_ms"] = float(d["ms"]) * (1000 if d.get("unit") == "s" else 1)
            wk = seg[(seg["pattern"] == "decoded_scalar") & (seg["body"].str.startswith("WakeupReason"))]
            if len(wk):
                p["wakeup_reason"] = _params(wk.iloc[0]["params"]).get("value")
            first_pub = seg[seg["pattern"] == "mqtt_pub_payload"]
            conn = seg[seg["pattern"] == "mqtt_connected"]
            out.append({"device_key": dev, "session": i, "start_ts": seg["ts"].min(), "end_ts": seg["ts"].max(), "lines": len(seg),
                        "duration_s": (seg["ts"].max() - seg["ts"].min()).total_seconds(),
                        "mqtt_connect_s": (conn["ts"].iloc[0] - seg["ts"].min()).total_seconds() if len(conn) else np.nan,
                        "first_publish_s": (first_pub["ts"].iloc[0] - seg["ts"].min()).total_seconds() if len(first_pub) else np.nan,
                        "publishes": int(len(first_pub)), "errors": int((seg["level"] == "Error").sum()), **p})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- state machine

TRIGGER_PATTERNS = {"to_sleep": "speed below threshold and ECM idle", "to_drive": "RPM or speed above threshold", "to_standby": "boot (always standby)",
                    "to_upgrade": "remote OTA command", "ota_attempt": "OTA attempt while idle"}


def state_transitions(lines: pd.DataFrame) -> pd.DataFrame:
    out = []
    for dev, g in lines.groupby("device_key", sort=False):
        g = g.sort_values("line_no").reset_index(drop=True)
        flows = g[g["pattern"] == "flow_start"]
        prev_state, prev_ts = None, None
        trig = g[g["pattern"].isin(TRIGGER_PATTERNS)]
        boots = g[g["pattern"] == "boot_start"]
        for _, r in flows.iterrows():
            state = _params(r["params"]).get("state")
            # trigger: the nearest reason line in the 40 lines before the flow start, else a boot
            before = trig[(trig["line_no"] < r["line_no"]) & (trig["line_no"] >= r["line_no"] - 40)]
            if len(before):
                reason_pat = before.iloc[-1]["pattern"]
                reason = TRIGGER_PATTERNS[reason_pat]
                reason_text = before.iloc[-1]["body"]
            elif state == "Standby" and len(boots[(boots["line_no"] < r["line_no"]) & (boots["line_no"] >= r["line_no"] - 400)]):
                reason, reason_text = "boot", "[main] System start"
            elif prev_state == "Standby" and state == "Driving":
                reason, reason_text = "standby complete (default path)", None
            else:
                reason, reason_text = "unspecified", None
            out.append({"device_key": dev, "line_no": r["line_no"], "ts": r["ts"], "from_state": prev_state, "to_state": state, "trigger": reason, "trigger_text": reason_text,
                        "previous_state_duration_s": (r["ts"] - prev_ts).total_seconds() if prev_ts is not None else np.nan})
            prev_state, prev_ts = state, r["ts"]
    df = pd.DataFrame(out)
    if len(df):
        df["duration_s"] = df.groupby("device_key")["previous_state_duration_s"].shift(-1)
    return df


# ---------------------------------------------------------------- MQTT uploads

PAYLOAD_NUMERIC = ["speed", "rpm", "gps_speed", "gps_valid", "eng_mi", "eng_hr", "e_temp", "car_bat_v", "is_comp", "mode", "lat", "lon"]


def _int_or_nan(v):
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return np.nan


def uploads(lines: pd.DataFrame) -> pd.DataFrame:
    out = []
    for dev, g in lines.groupby("device_key", sort=False):
        g = g.sort_values("line_no").reset_index(drop=True)
        pubs = g[g["pattern"] == "mqtt_pub_payload"]
        topics = g[g["pattern"].isin(["mqtt_pub_topic", "ota_ack_topic"])]
        acks = g[g["pattern"] == "mqtt_rx_payload"]
        ack_rows = []
        for _, a in acks.iterrows():
            try:
                d = json.loads(_params(a["params"]).get("json", "{}"))
            except json.JSONDecodeError:
                continue
            if "timestamp" in d and "mode" in d:
                ack_rows.append({"ack_line_no": a["line_no"], "ack_ts": a["ts"], "timestamp": str(d.get("timestamp")), "ack_mode": str(d.get("mode")), "ack_is_stored": str(d.get("is_stored"))})
        ackdf = pd.DataFrame(ack_rows)
        # state at publish time
        flows = g[g["pattern"] == "flow_start"][["line_no", "params"]].copy()
        flows["state"] = flows["params"].map(lambda s: _params(s).get("state"))
        pub_rows = []
        for _, r in pubs.iterrows():
            try:
                d = json.loads(_params(r["params"])["json"])
            except (json.JSONDecodeError, KeyError):
                continue
            tp = topics[(topics["line_no"] < r["line_no"]) & (topics["line_no"] >= r["line_no"] - 5)]
            topic = _params(tp.iloc[-1]["params"]).get("topic") if len(tp) else None
            st = flows[flows["line_no"] < r["line_no"]]
            state = st.iloc[-1]["state"] if len(st) else None
            row = {"device_key": dev, "line_no": r["line_no"], "ts": r["ts"], "state": state, "topic": topic, "mode": d.get("mode"), "is_stored": _int_or_nan(d.get("is_stored")),
                   "payload_timestamp": str(d.get("timestamp")), "msg_code": d.get("msg_code"), "event_type": d.get("event_type"), "n_fields": len(d),
                   "usn": d.get("usn"), "vin_present": bool(d.get("vin_id")) and d.get("vin_id") not in ("VINNOTYETDETECTED", ""), "driver_present": d.get("driver_id") not in (None, "", "NODRIVERID"),
                   "source_addr": d.get("source_addr"), "source_addr_lists": d.get("source_addr_lists"), "bk_bat_pct": _pct(d.get("bk_bat_v")), "disk_pct": _pct(d.get("disk_size")),
                   "fw": d.get("e_fw_v"), "hw": d.get("e_hw_v"), "st_fw": d.get("s_m_v"), "ble_fw": d.get("ble_v"), "timezone": d.get("timezone"),
                   "ack_line_no": np.nan, "ack_ts": pd.NaT, "ack_latency_s": np.nan, "acked": False}
            for k in PAYLOAD_NUMERIC:
                row[k] = pd.to_numeric(d.get(k), errors="coerce") if d.get(k) not in (None, "", "N/A") else np.nan
            pub_rows.append(row)
        # ack matching: an ack names the payload timestamp and the is_stored flag of the message it answers;
        # take the latest unmatched publish before the ack with that timestamp, preferring the same is_stored flag
        if len(ackdf):
            for _, a in ackdf.sort_values("ack_line_no").iterrows():
                cands = [i for i, pr in enumerate(pub_rows) if pr["payload_timestamp"] == a["timestamp"] and pr["line_no"] < a["ack_line_no"] and not pr["acked"]]
                same = [i for i in cands if pub_rows[i]["is_stored"] == _int_or_nan(a["ack_is_stored"])]
                pick = (same or cands)
                if pick:
                    pr = pub_rows[pick[-1]]
                    pr.update({"ack_line_no": a["ack_line_no"], "ack_ts": a["ack_ts"], "ack_latency_s": (a["ack_ts"] - pr["ts"]).total_seconds(), "acked": True})
        out.extend(pub_rows)
    df = pd.DataFrame(out)
    if len(df):
        df["interval_s"] = df.groupby("device_key")["ts"].diff().dt.total_seconds()
        df["mode_name"] = df["mode"].map(MODE_NAMES).fillna("mode " + df["mode"].astype(str))
        df.loc[df["topic"].astype(str).str.endswith("/ota/ack"), "mode_name"] = "OTA command ack"
    return df


MODE_NAMES = {1: "standby status", 2: "driving telemetry", 4: "sleeping status", 7: "OTA full update", 8: "OTA done", 9: "device event (ERR code)", 10: "boot status", 11: "unidentified driving event"}


def _pct(v):
    if v is None:
        return np.nan
    m = re.search(r"\d+", str(v))
    return float(m.group(0)) if m else np.nan


# ---------------------------------------------------------------- UART round-trips

def _bus_of_bytes(first: str) -> str:
    return {"60": "BLE", "06": "BLE", "80": "ST", "08": "ST"}.get(first, "unknown")


def _bus_of_component(c: str | None) -> str | None:
    if not c:
        return None
    if c.startswith("BLE") or c in ("TestModeGet",):
        return "BLE"
    if c.startswith("ST") or c in ("SourceMileageListGet", "SourceAddressSet"):
        return "ST"
    return None


def uart_commands(lines: pd.DataFrame) -> pd.DataFrame:
    """Pair each serial request with its reply on the same bus. The BLE module and the ST
    micro-controller sit on separate UARTs and are driven by separate goroutines, so their
    traffic interleaves in the log; pairing is therefore kept per bus. Unsolicited frames from
    the BLE module (status notifications, command 0x23) are recorded as `direction = notify`."""
    out = []
    for dev, g in lines.groupby("device_key", sort=False):
        g = g.sort_values("line_no").reset_index(drop=True)
        u = g[g["category"] == "uart"]
        component = {"BLE": None, "ST": None}
        pending = {"BLE": None, "ST": None}
        last_notify = None

        def close(bus):
            pr = pending[bus]
            if pr is not None:
                pr["outcome"] = pr.get("outcome") or "no reply"
                out.append(pr)
                pending[bus] = None

        for _, r in u.iterrows():
            pat, p = r["pattern"], _params(r["params"])
            if pat == "op_start":
                b = _bus_of_component(p.get("component"))
                if b:
                    component[b] = p.get("component")
            elif pat == "uart_tx":
                b = p["bytes"].split()
                bus = _bus_of_bytes(b[0])
                cmd = b[3] if len(b) > 3 else None
                if bus == "BLE" and last_notify is not None and last_notify["cmd"] == cmd and r["line_no"] - last_notify["line_no"] <= 6:
                    last_notify["reply_ts"] = r["ts"]
                    last_notify["outcome"] = "notification replied"
                    last_notify = None
                    continue
                if bus in pending:
                    close(bus)
                    pending[bus] = {"device_key": dev, "line_no": r["line_no"], "ts": r["ts"], "bus": bus, "direction": "request", "component": component.get(bus), "cmd": cmd,
                                    "tx_bytes": int(p["n_bytes"]), "rx_bytes": np.nan, "rx_ts": pd.NaT, "retcode": None, "outcome": None, "retries": 0}
            elif pat == "uart_rx":
                b = p["bytes"].split()
                bus = _bus_of_bytes(b[0])
                cmd = b[3] if len(b) > 3 else None
                pr = pending.get(bus)
                if pr is not None and pd.isna(pr["rx_bytes"]):
                    pr["rx_bytes"] = int(p["n_bytes"])
                    pr["rx_ts"] = r["ts"]
                    if cmd and pr["cmd"] and cmd != pr["cmd"] and cmd != "FF":
                        pr["outcome"] = "reply for other command"
                elif bus == "BLE":
                    last_notify = {"device_key": dev, "line_no": r["line_no"], "ts": r["ts"], "bus": bus, "direction": "notify", "component": "BLEStatusUpdate", "cmd": cmd,
                                   "tx_bytes": np.nan, "rx_bytes": int(p["n_bytes"]), "rx_ts": r["ts"], "retcode": None, "outcome": "notification", "retries": 0}
                    out.append(last_notify)
            elif pat in ("uart_retcode", "uart_retcode_ble_get"):
                bus = "BLE" if pat == "uart_retcode_ble_get" else _bus_of_component(p.get("component"))
                pr = pending.get(bus) if bus else None
                if pr is not None and (pr["retcode"] is None or pat == "uart_retcode"):
                    pr["retcode"] = p["retcode"]
                    pr["outcome"] = "ok" if p["retcode"] == "0001" else f"retcode {p['retcode']}"
                    if pat == "uart_retcode":
                        close(bus)
            elif pat == "uart_wait_timeout":
                pr = pending.get("ST")
                if pr is not None:
                    pr["outcome"] = "timeout"
            elif pat == "uart_mismatch_retry":
                bus = _bus_of_component(p.get("component")) or "ST"
                pr = pending.get(bus)
                if pr is not None:
                    pr["retries"] = int(p.get("attempt", 0))
                    pr["outcome"] = pr.get("outcome") or "mismatch retry"
        for bus in ("BLE", "ST"):
            close(bus)
    df = pd.DataFrame(out).sort_values("line_no").reset_index(drop=True)
    if len(df):
        df["latency_s"] = (df["rx_ts"] - df["ts"]).dt.total_seconds()
        df.loc[df["direction"] == "notify", "latency_s"] = np.nan
        df["ok"] = df["outcome"].isin(["ok", "notification replied"])
    return df


# ---------------------------------------------------------------- readings

def readings(lines: pd.DataFrame) -> pd.DataFrame:
    r = lines[lines["pattern"].isin(["reading", "free_space", "tz", "ecm_retry", "source_list"])].copy()
    if not len(r):
        return pd.DataFrame()
    P = r["params"].map(_params)
    r["state"] = P.map(lambda d: d.get("state"))
    r["group"] = P.map(lambda d: d.get("group"))
    r["metric"] = np.where(r["pattern"] == "reading", P.map(lambda d: d.get("metric")), r["pattern"])
    r["value_raw"] = P.map(lambda d: d.get("value"))
    r["value_num"] = pd.to_numeric(r["value_raw"].astype(str).str.replace("%", "", regex=False).str.strip(), errors="coerce")
    r["is_na"] = r["value_raw"].astype(str).str.strip().isin(["N/A", "", "nan"])
    return r[["device_key", "line_no", "ts", "state", "group", "metric", "value_raw", "value_num", "is_na", "pattern"]].reset_index(drop=True)


# ---------------------------------------------------------------- config IO

def config_io(lines: pd.DataFrame) -> pd.DataFrame:
    c = lines[lines["pattern"] == "config_rw"].copy()
    if not len(c):
        return pd.DataFrame()
    P = c["params"].map(_params)
    c["op"] = P.map(lambda d: d["op"])
    c["key"] = P.map(lambda d: d["key"])
    c["value"] = P.map(lambda d: d.get("value", ""))
    return c[["device_key", "line_no", "ts", "op", "key", "value"]].reset_index(drop=True)


# ---------------------------------------------------------------- errors / events

ERROR_KINDS = {"err_event": "ERR event attempt", "err_cooldown": "ERR event suppressed (cooldown)", "err_ack_timeout": "MQTT ack timeout", "mqtt_lost": "MQTT connection lost",
               "uart_wait_timeout": "ST command timeout", "uart_mismatch_retry": "serial reply mismatch, retry", "selftest_fail": "self-test failure", "ecm_notify": "ECM watchdog notification",
               "err_generic": "other error"}


def errors(lines: pd.DataFrame) -> pd.DataFrame:
    e = lines[lines["pattern"].isin(ERROR_KINDS) | (lines["level"] == "Error")].copy()
    if not len(e):
        return pd.DataFrame()
    P = e["params"].map(_params)
    e["kind"] = e["pattern"].map(ERROR_KINDS).fillna("other error")
    e["code"] = P.map(lambda d: d.get("code"))
    e["component"] = P.map(lambda d: d.get("component"))
    e["source"] = P.map(lambda d: (d.get("src") or "").split("/")[-1] + (":" + d["src_line"] if d.get("src_line") else ""))
    e["gps_speed"] = P.map(lambda d: pd.to_numeric(d.get("gps_speed"), errors="coerce"))
    e["cooldown_s"] = P.map(lambda d: _duration_s(d.get("cooldown")))
    e["cmd"] = P.map(lambda d: d.get("want"))
    return e[["device_key", "line_no", "ts", "level", "kind", "code", "component", "source", "gps_speed", "cooldown_s", "cmd", "body"]].reset_index(drop=True)


def _duration_s(s):
    if not s:
        return np.nan
    m = re.match(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", s)
    if not m or not any(m.groups()):
        return np.nan
    h, mi, se = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + se


# ---------------------------------------------------------------- offline buffering

def upload_queue(lines: pd.DataFrame) -> pd.DataFrame:
    q = lines[lines["category"] == "upload"].copy()
    if not len(q):
        return pd.DataFrame()
    P = q["params"].map(_params)
    q["event"] = q["pattern"]
    q["path"] = P.map(lambda d: d.get("path"))
    q["records"] = P.map(lambda d: pd.to_numeric(d.get("records"), errors="coerce"))
    return q[["device_key", "line_no", "ts", "event", "path", "records"]].reset_index(drop=True)


# ---------------------------------------------------------------- backend API

def api_calls(lines: pd.DataFrame) -> pd.DataFrame:
    a = lines[lines["pattern"] == "api_latency"].copy()
    if not len(a):
        return pd.DataFrame()
    P = a["params"].map(_params)
    a["latency_ms"] = P.map(lambda d: float(d["value"]) * (1000 if d.get("unit") == "s" else 1))
    return a[["device_key", "line_no", "ts", "latency_ms"]].reset_index(drop=True)


def build_all(lines: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"boot_sessions": boot_sessions(lines), "state_transitions": state_transitions(lines), "uploads": uploads(lines), "uart_commands": uart_commands(lines),
            "readings": readings(lines), "config_io": config_io(lines), "errors": errors(lines), "upload_queue": upload_queue(lines), "api_calls": api_calls(lines)}
