"""Device-observability metrics computed from the derived tables."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _q(s: pd.Series, q: float) -> float:
    s = s.dropna()
    return float(s.quantile(q)) if len(s) else np.nan


def log_composition(lines: pd.DataFrame) -> pd.DataFrame:
    """Lines per category and per device, with the share of the file. The basis for the log-hygiene recommendation."""
    g = lines.groupby(["device_key", "category"]).size().rename("lines").reset_index()
    g["share"] = g["lines"] / g.groupby("device_key")["lines"].transform("sum")
    return g.sort_values(["device_key", "lines"], ascending=[True, False]).reset_index(drop=True)


def publish_cadence(uploads: pd.DataFrame) -> pd.DataFrame:
    """Interval between consecutive telemetry publishes by state (mode 2 driving, mode 4 sleeping)."""
    u = uploads[uploads["mode"].isin([2, 4])].copy()
    u["interval_s"] = u.groupby(["device_key"])["ts"].diff().dt.total_seconds()
    u = u[u["state"].isin(["Driving", "Sleeping"])]
    g = u.groupby(["device_key", "state"])["interval_s"].agg(n="size", median="median", p10=lambda s: _q(s, 0.1), p90=lambda s: _q(s, 0.9), max="max").reset_index()
    return g


def ack_summary(uploads: pd.DataFrame) -> pd.DataFrame:
    g = uploads.groupby("device_key").agg(publishes=("ts", "size"), acked=("acked", "sum"), ack_within_1s=("ack_latency_s", lambda s: (s <= 1).sum()),
                                           ack_p50_s=("ack_latency_s", "median"), ack_max_s=("ack_latency_s", "max"), stored_resends=("is_stored", lambda s: int((s == 1).sum())))
    g["ack_rate"] = g["acked"] / g["publishes"]
    g["ack_within_1s_rate"] = g["ack_within_1s"] / g["acked"].clip(lower=1)
    return g.reset_index()


def offline_recovery(uploads: pd.DataFrame, upload_queue: pd.DataFrame) -> pd.DataFrame:
    """For each stored re-send: the original publish, how long until the backend acknowledged a copy."""
    rows = []
    stored = uploads[uploads["is_stored"] == 1]
    for _, s in stored.iterrows():
        orig = uploads[(uploads["device_key"] == s["device_key"]) & (uploads["payload_timestamp"] == s["payload_timestamp"]) & (uploads["is_stored"] == 0)]
        o = orig.iloc[0] if len(orig) else None
        rows.append({"device_key": s["device_key"], "payload_timestamp": s["payload_timestamp"], "original_publish_ts": o["ts"] if o is not None else pd.NaT,
                     "original_acked": bool(o["acked"]) if o is not None else None, "resend_ts": s["ts"], "resend_acked": bool(s["acked"]), "resend_ack_ts": s["ack_ts"],
                     "delivery_delay_s": (s["ack_ts"] - o["ts"]).total_seconds() if (o is not None and pd.notna(s["ack_ts"])) else np.nan})
    return pd.DataFrame(rows)


def uart_summary(cmds: pd.DataFrame) -> pd.DataFrame:
    req = cmds[cmds["direction"] == "request"]
    g = req.groupby(["device_key", "bus"]).agg(requests=("ok", "size"), ok=("ok", "sum"), timeouts=("outcome", lambda s: int((s == "timeout").sum())),
                                              no_reply=("outcome", lambda s: int((s == "no reply").sum())), reply_within_1s=("latency_s", lambda s: int((s <= 1).sum())),
                                              reply_p50_s=("latency_s", "median"), reply_max_s=("latency_s", "max")).reset_index()
    g["ok_rate"] = g["ok"] / g["requests"]
    return g


def uart_by_command(cmds: pd.DataFrame) -> pd.DataFrame:
    req = cmds[cmds["direction"] == "request"]
    g = req.groupby(["bus", "cmd", "component"]).agg(requests=("ok", "size"), ok_rate=("ok", "mean"), timeouts=("outcome", lambda s: int((s == "timeout").sum())),
                                                     mean_latency_s=("latency_s", "mean")).reset_index()
    return g.sort_values("requests", ascending=False).reset_index(drop=True)


def state_time(transitions: pd.DataFrame) -> pd.DataFrame:
    t = transitions.dropna(subset=["duration_s"])
    g = t.groupby(["device_key", "to_state"])["duration_s"].agg(visits="size", total_s="sum", median_s="median", max_s="max").reset_index()
    g["share_of_observed_time"] = g["total_s"] / g.groupby("device_key")["total_s"].transform("sum")
    return g.rename(columns={"to_state": "state"})


def ecm_retry_summary(readings: pd.DataFrame, uploads: pd.DataFrame) -> pd.DataFrame:
    """How often the ECM query had to be retried, and how often a cycle still ended with N/A fields."""
    rows = []
    for dev, r in readings.groupby("device_key"):
        for state, rs in r.groupby("state"):
            cycles = uploads[(uploads["device_key"] == dev) & (uploads["state"] == state) & (uploads["mode"].isin([2, 4, 1]))]
            retries = rs[rs["pattern"] == "ecm_retry"]
            eh = rs[(rs["group"] == "ECM data") & (rs["metric"] == "Engine Hour")]
            vin = rs[(rs["group"] == "ECM data") & (rs["metric"] == "Vin")]
            rpm = rs[(rs["group"] == "ECM data") & (rs["metric"] == "RPM")]
            rows.append({"device_key": dev, "state": state, "publishes": len(cycles), "ecm_retry_lines": len(retries), "cycles_with_retry": int(retries["line_no"].diff().fillna(999).gt(3).sum()),
                         "engine_hour_na_share": eh["is_na"].mean() if len(eh) else np.nan, "vin_empty_share": vin["is_na"].mean() if len(vin) else np.nan,
                         "rpm_na_share": rpm["is_na"].mean() if len(rpm) else np.nan})
    return pd.DataFrame(rows)


def err_event_summary(errors: pd.DataFrame, uploads: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for dev, e in errors.groupby("device_key"):
        attempts = e[e["kind"] == "ERR event attempt"]
        suppressed = e[e["kind"] == "ERR event suppressed (cooldown)"]
        emitted = uploads[(uploads["device_key"] == dev) & (uploads["msg_code"].notna())]
        rows.append({"device_key": dev, "err_attempts": len(attempts), "suppressed_by_cooldown": len(suppressed), "events_published": len(emitted),
                     "codes": ",".join(sorted(set(emitted["msg_code"].dropna()))), "min_gps_speed_at_attempt": attempts["gps_speed"].min() if len(attempts) else np.nan,
                     "cooldown_max_s": suppressed["cooldown_s"].max() if len(suppressed) else np.nan})
    return pd.DataFrame(rows)


def session_summary(sessions: pd.DataFrame) -> pd.DataFrame:
    s = sessions.copy()
    s["abrupt_end"] = s["duration_s"] < 900
    return s


def summarize(lines: pd.DataFrame, T: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {
        "log_composition": log_composition(lines),
        "publish_cadence": publish_cadence(T["uploads"]),
        "ack_summary": ack_summary(T["uploads"]),
        "offline_recovery": offline_recovery(T["uploads"], T["upload_queue"]),
        "uart_summary": uart_summary(T["uart_commands"]),
        "uart_by_command": uart_by_command(T["uart_commands"]),
        "state_time": state_time(T["state_transitions"]),
        "ecm_retry_summary": ecm_retry_summary(T["readings"], T["uploads"]),
        "err_event_summary": err_event_summary(T["errors"], T["uploads"]),
    }
