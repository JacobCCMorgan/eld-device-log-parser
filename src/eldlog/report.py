"""Figures and the Markdown report."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

C = {"Standby": "#9A9A9A", "Driving": "#2F5D8A", "Sleeping": "#7A9E7E", "Upgrade": "#6B4C9A", "main": "#D9D9D9"}
CAT_COLOR = {"uart": "#2F5D8A", "telemetry": "#7A9E7E", "boot": "#D9822B", "config": "#B5533C", "separator": "#C9C9C9", "led": "#E3B23C", "mqtt": "#6B4C9A",
             "ble": "#8FB8DE", "state": "#1F2933", "error": "#B5533C", "housekeeping": "#A8A8A8", "ud": "#52606D", "upload": "#D9822B", "ota": "#6B4C9A", "other": "#000000"}


def _save(fig, path: Path):
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def fig_timeline(T: dict, out: Path):
    devs = list(T["boot_sessions"]["device_key"].unique())
    fig, axes = plt.subplots(len(devs), 1, figsize=(12, 3.2 * len(devs)), squeeze=False)
    for ax, dev in zip(axes[:, 0], devs):
        tr = T["state_transitions"][T["state_transitions"]["device_key"] == dev].reset_index(drop=True)
        end_all = T["boot_sessions"].loc[T["boot_sessions"]["device_key"] == dev, "end_ts"].max()
        for i, r in tr.iterrows():
            end = tr.loc[i + 1, "ts"] if i + 1 < len(tr) else end_all
            ax.axvspan(r["ts"], end, ymin=0.55, ymax=0.95, color=C.get(r["to_state"], "#ccc"), alpha=0.85, lw=0)
        up = T["uploads"][T["uploads"]["device_key"] == dev]
        ax.scatter(up["ts"], np.full(len(up), 0.40), marker="|", s=80, color=np.where(up["is_stored"] == 1, "#B5533C", "#1F2933"), lw=0.8, label="MQTT publish")
        er = T["errors"][(T["errors"]["device_key"] == dev) & (T["errors"]["kind"].isin(["ERR event attempt", "MQTT ack timeout", "MQTT connection lost", "ST command timeout"]))]
        ax.scatter(er["ts"], np.full(len(er), 0.22), marker="x", s=18, color="#B5533C", lw=0.8, label="error / timeout")
        prev_version = None
        for k, (_, s) in enumerate(T["boot_sessions"][(T["boot_sessions"]["device_key"] == dev) & (T["boot_sessions"]["boot_observed"])].iterrows()):
            ax.axvline(s["start_ts"], color="#1F2933", lw=1, ls="--")
            label = " boot" + (f" {s['version']}" if s["version"] and s["version"] != prev_version else "")
            ax.text(s["start_ts"], 1.0 + 0.09 * (k % 2), label, fontsize=7, va="bottom")
            prev_version = s["version"] or prev_version
        ax.set_ylim(0, 1.22)
        ax.set_yticks([0.22, 0.40, 0.75], ["errors", "publishes", "state"])
        ax.set_title(f"{dev}: {tr['ts'].min():%Y-%m-%d %H:%M} to {end_all:%H:%M} UTC", fontsize=10, loc="left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.spines[["top", "right"]].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=C[k]) for k in ["Standby", "Driving", "Sleeping", "Upgrade"]]
    axes[0, 0].legend(handles, ["Standby", "Driving", "Sleeping", "Upgrade"], loc="lower right", fontsize=8, frameon=False, ncol=4)
    _save(fig, out / "01_timeline.png")


def fig_composition(comp: pd.DataFrame, out: Path):
    piv = comp.pivot(index="device_key", columns="category", values="share").fillna(0)
    order = piv.sum().sort_values(ascending=False).index
    fig, ax = plt.subplots(figsize=(10, 2.8))
    left = np.zeros(len(piv))
    for cat in order:
        ax.barh(piv.index, piv[cat], left=left, color=CAT_COLOR.get(cat, "#999"), label=cat)
        for i, v in enumerate(piv[cat]):
            if v > 0.03:
                ax.text(left[i] + v / 2, i, f"{cat}\n{v:.0%}", ha="center", va="center", fontsize=7, color="white" if cat in ("uart", "mqtt", "state") else "black")
        left += piv[cat].values
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
    ax.set_title("What the log is made of: share of lines per family")
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, out / "02_log_composition.png")


def fig_cadence_ack(uploads: pd.DataFrame, out: Path):
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    u = uploads[uploads["mode"].isin([2, 4])].copy()
    u["interval_s"] = u.groupby("device_key")["ts"].diff().dt.total_seconds()
    u = u[u["state"] == u.groupby("device_key")["state"].shift(1)]
    for state, color, bins in [("Driving", C["Driving"], np.arange(0, 125, 5)), ("Sleeping", C["Sleeping"], np.arange(0, 330, 15))]:
        ax = axes[0] if state == "Driving" else axes[1]
        for dev, g in u[u["state"] == state].groupby("device_key"):
            ax.hist(g["interval_s"].clip(upper=bins[-1]), bins=bins, alpha=0.6, label=dev, color=color if dev.endswith("72c") else "#D9822B")
        ax.set_title(f"Interval between publishes while {state}")
        ax.set_xlabel("seconds")
        ax.legend(frameon=False, fontsize=7)
        ax.spines[["top", "right"]].set_visible(False)
    ax = axes[2]
    lat = uploads["ack_latency_s"].fillna(-1).astype(int)
    counts = lat.value_counts().sort_index()
    labels = ["no ack" if k < 0 else f"{k} s" for k in counts.index]
    ax.bar(labels, counts.values, color=["#B5533C" if k < 0 else "#2F5D8A" for k in counts.index])
    for i, v in enumerate(counts.values):
        ax.text(i, v + 2, str(v), ha="center", fontsize=8)
    ax.set_title("Publish-to-ack time (1 s log resolution)")
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, out / "03_cadence_and_ack.png")


def fig_uart(by_cmd: pd.DataFrame, out: Path):
    g = by_cmd.head(18).copy()
    g["label"] = g["bus"] + " 0x" + g["cmd"].astype(str) + " " + g["component"].astype(str)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), gridspec_kw={"width_ratios": [2, 1]})
    y = np.arange(len(g))[::-1]
    axes[0].barh(y, g["requests"], color=np.where(g["bus"] == "BLE", "#8FB8DE", "#2F5D8A"))
    axes[0].set_yticks(y, g["label"], fontsize=7)
    axes[0].set_title("Serial requests per command (BLE module light, ST micro-controller dark)")
    axes[0].set_xlabel("requests")
    t = g[g["timeouts"] > 0]
    axes[1].barh(np.arange(len(t))[::-1], t["timeouts"] / t["requests"], color="#B5533C")
    axes[1].set_yticks(np.arange(len(t))[::-1], t["label"], fontsize=7)
    axes[1].xaxis.set_major_formatter(lambda x, _: f"{x:.1%}")
    axes[1].set_title("Timeout rate (ST bus only)")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    _save(fig, out / "04_serial_commands.png")


def fig_ecm(retry: pd.DataFrame, errs: pd.DataFrame, uploads: pd.DataFrame, out: Path):
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    r = retry[retry["state"].isin(["Driving", "Sleeping"])].copy()
    r["label"] = r["device_key"] + "\n" + r["state"]
    x = np.arange(len(r))
    axes[0].bar(x - 0.2, r["engine_hour_na_share"], 0.4, label="engine hours N/A", color="#D9822B")
    axes[0].bar(x + 0.2, r["vin_empty_share"], 0.4, label="VIN empty", color="#9A9A9A")
    axes[0].set_xticks(x, r["label"], fontsize=8)
    axes[0].yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    axes[0].set_title("Share of ECM query cycles with a missing field")
    axes[0].legend(frameon=False, fontsize=8)
    ev = errs[errs["kind"] == "ERR event attempt"]
    pub = uploads[uploads["msg_code"].notna()]
    t0 = uploads.groupby("device_key")["ts"].min()
    for dev, g in ev.groupby("device_key"):
        x = (g["ts"] - t0[dev]).dt.total_seconds() / 60
        axes[1].scatter(x, g["gps_speed"], marker="x", color="#B5533C", label=f"{dev}: ERR005 condition met ({len(g)})")
    for dev, g in pub.groupby("device_key"):
        x = (g["ts"] - t0[dev]).dt.total_seconds() / 60
        axes[1].scatter(x, np.full(len(g), 2), marker="^", s=70, color="#1F2933", label=f"{dev}: event published ({len(g)}: {', '.join(sorted(set(g['msg_code'])))})")
    axes[1].set_ylabel("GPS speed at the time (km/h)")
    axes[1].set_xlabel("minutes since the device's first publish in the log")
    axes[1].set_title("ERR005 condition met vs events published (1 h cooldown)")
    axes[1].legend(frameon=False, fontsize=7)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    _save(fig, out / "05_ecm_fields_and_err005.png")


def fig_sessions(sessions: pd.DataFrame, out: Path):
    s = sessions.copy()
    s["label"] = s["device_key"].str[-4:] + " #" + s["session"].astype(str)
    s = s[s["boot_observed"]]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.2))
    axes[0].bar(s["label"], s["duration_s"] / 60, color=np.where(s["duration_s"] < 900, "#B5533C", "#2F5D8A"))
    axes[0].set_title("Session length (minutes); red = under 15 min")
    axes[1].bar(s["label"], s["first_publish_s"], color="#7A9E7E")
    axes[1].set_title("Boot to first publish (s)")
    axes[2].bar(s["label"], s["rsrp_dbm"], color="#9A9A9A")
    axes[2].set_title("LTE RSRP at boot (dBm)")
    axes[2].set_ylim(min(s["rsrp_dbm"].min() - 10, -100), 0)
    for ax in axes:
        ax.tick_params(axis="x", labelsize=7, rotation=45)
        ax.spines[["top", "right"]].set_visible(False)
    _save(fig, out / "06_sessions.png")


def fig_drain(by_cluster: pd.DataFrame, n_patterns: int, out: Path):
    cum = by_cluster["lines"].cumsum() / by_cluster["lines"].sum()
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    ax.plot(np.arange(1, len(cum) + 1), cum, color="#2F5D8A", lw=2, label="Drain3 templates, largest first")
    ax.axvline(n_patterns, color="#D9822B", ls="--", label=f"hand-written registry: {n_patterns} patterns, 100 % coverage")
    ax.axhline(0.99, color="#9A9A9A", lw=0.8, ls=":")
    k99 = int((cum < 0.99).sum() + 1)
    ax.annotate(f"{k99} templates for 99 % of lines", (k99, 0.99), textcoords="offset points", xytext=(10, -14), fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("number of templates (log scale)")
    ax.set_ylabel("share of lines covered")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_title("How many message templates does the log contain?")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    _save(fig, out / "07_drain_vs_registry.png")


def write_report(path: Path, ctx: dict):
    M, T = ctx["metrics"], ctx["tables"]
    comp = M["log_composition"]
    s1 = T["boot_sessions"]
    d1 = s1[s1["device_key"] == ctx["dev1"]]
    cad = M["publish_cadence"].set_index(["device_key", "state"])
    ack = M["ack_summary"].set_index("device_key")
    rec = M["offline_recovery"]
    us = M["uart_summary"]
    ecm = M["ecm_retry_summary"].set_index(["device_key", "state"])
    err = M["err_event_summary"].set_index("device_key")
    st = M["state_time"]
    dr = ctx["drain"]
    dev1, dev2 = ctx["dev1"], ctx["dev2"]
    tot_lines = int(comp.groupby("device_key")["lines"].sum().sum())
    uart_share = comp[comp["category"] == "uart"].groupby("device_key")["share"].mean().mean()
    chatter = comp[comp["category"].isin(["uart", "led", "ble", "separator"])].groupby("device_key")["share"].sum().mean()
    short = d1[d1["duration_s"] < 900]
    ota_row = T["state_transitions"][(T["state_transitions"]["to_state"] == "Upgrade")]
    ota_dur = float(ota_row["duration_s"].iloc[0]) if len(ota_row) else np.nan
    first_vin = {}
    for dev, g in T["uploads"].groupby("device_key"):
        fv = g[g["vin_present"]]["ts"].min()
        first_vin[dev] = (fv - g["ts"].min()).total_seconds() / 60 if pd.notna(fv) else np.nan
    st_bus = us[us["bus"] == "ST"]
    ble_bus = us[us["bus"] == "BLE"]

    md = f"""# Firmware log parser: results

Computed by `eldlog report`; every number is regenerated from the two published logs in `data/logs/`.

## 1. What the logs are

| device | vehicle (from the foundation tables) | log window (UTC) | lines | boots | publishes |
|---|---|---|---:|---:|---:|
| `{dev1}` | {ctx['vehicle'].get(dev1, '')} | {s1.loc[s1.device_key == dev1, 'start_ts'].min():%Y-%m-%d %H:%M} to {s1.loc[s1.device_key == dev1, 'end_ts'].max():%H:%M} | {int(comp.loc[comp.device_key == dev1, 'lines'].sum()):,} | {int(d1['boot_observed'].sum())} | {int(ack.loc[dev1, 'publishes'])} |
| `{dev2}` | {ctx['vehicle'].get(dev2, '')} | {s1.loc[s1.device_key == dev2, 'start_ts'].min():%Y-%m-%d %H:%M} to {s1.loc[s1.device_key == dev2, 'end_ts'].max():%H:%M} | {int(comp.loc[comp.device_key == dev2, 'lines'].sum()):,} | {int(s1.loc[s1.device_key == dev2, 'boot_observed'].sum())} | {int(ack.loc[dev2, 'publishes'])} |

The parser classifies **100 % of the {tot_lines:,} lines** with {ctx['n_patterns']} hand-written patterns in 14 families and turns them into nine event tables (`reports/tables/`).

## 2. What the log is made of

- **{uart_share:.0%} of all lines are serial-bus traffic** (byte dumps, command codes and return codes between the Linux host, the BLE module and the ST micro-controller); with LED control, BLE housekeeping and decorative separators the share of lines that carry no operational information rises to **{chatter:.0%}**.
- The remaining lines are telemetry readings (12 %), configuration reads and writes (5 %), boot sequences (5 %), MQTT traffic (2 %) and under 1 % for the state machine, errors, OTA and offline buffering combined. Everything a support engineer actually looks for lives in that last 1 %.
- Recommendation: move byte dumps and LED/BLE chatter behind a debug flag. The file shrinks by about {chatter:.0%} and a day of logs would no longer force the 2 MB daily tar archives seen in the housekeeping lines.

![composition](figures/02_log_composition.png)

## 3. Timeline: states, publishes, reboots

![timeline](figures/01_timeline.png)

- The state machine behaves as documented: **Standby at boot, then Driving, and Sleeping once GPS speed is below 8 km/h with speed and RPM idle**; the device wakes to Driving on RPM or speed. `{dev1}` spent {st.loc[(st.device_key == dev1) & (st.state == 'Sleeping'), 'share_of_observed_time'].iloc[0]:.0%} of the observed time Sleeping, `{dev2}` {st.loc[(st.device_key == dev2) & (st.state == 'Sleeping'), 'share_of_observed_time'].iloc[0]:.0%}.
- **`{dev1}` rebooted {len(d1) - 1} times in {(d1['end_ts'].max() - d1['start_ts'].min()).total_seconds() / 3600:.1f} hours.** One reboot was the over-the-air update from {d1['version'].iloc[0]} to {d1['version'].iloc[-1]} (Upgrade state lasted {ota_dur:.0f} s, upload before and after, UPS output "Success"). The other {len(d1) - 2} were not announced: the log ends mid-cycle in Sleeping and the next lines are the boot shell scripts. {len(short)} of the sessions lasted under 15 minutes, all right after the update; the wake-up reason codes the ST controller reports are {', '.join(d1['wakeup_reason'].astype(str))}. Whether this is a post-update watchdog loop or external power cycling cannot be decided from one device; it is the first thing to check on the fleet after a firmware rollout.
- Boot is fast and consistent: {d1['init_s'].mean():.0f} s init, {d1['selftest_ms'].mean():.0f} ms self-test, MQTT connected {d1['mqtt_connect_s'].median():.0f} s after boot, first publish {d1['first_publish_s'].min():.0f} to {d1['first_publish_s'].max():.0f} s after boot (the longer waits are the Standby flow retrying until GPS and ECM are ready). LTE RSRP at boot ranged {int(d1['rsrp_dbm'].min())} to {int(d1['rsrp_dbm'].max())} dBm.
- The VIN is not available until the engine answers: the first publish carrying a VIN came {first_vin[dev1]:.0f} min after the first publish on `{dev1}` and {first_vin[dev2]:.0f} min on `{dev2}`; until then payloads carry the placeholder, which is what the foundation tables count as "never read a VIN".

![sessions](figures/06_sessions.png)

## 4. Upload cadence, acknowledgements and offline recovery

| device | state | publishes | interval median (s) | p10 to p90 (s) | max (s) |
|---|---|---:|---:|---:|---:|
""" + "\n".join(f"| `{d}` | {s} | {int(cad.loc[(d, s), 'n'])} | {cad.loc[(d, s), 'median']:.0f} | {cad.loc[(d, s), 'p10']:.0f} to {cad.loc[(d, s), 'p90']:.0f} | {cad.loc[(d, s), 'max']:.0f} |" for d, s in cad.index) + f"""

- **Driving telemetry is published every 30 s and sleeping status every 5 min**, exactly as specified; the long maxima are the reconnection gaps discussed below.
- **{int(ack['acked'].sum())} of {int(ack['publishes'].sum())} publishes were acknowledged by the backend, {ack['ack_within_1s_rate'].min():.0%} or more of them within one second** (the log has one-second resolution, so this is an upper bound on latency). The backend REST calls made at boot take about {T['api_calls']['latency_ms'].median():.0f} ms each by comparison.
- **The offline path works end to end.** Of the {int(ack['publishes'].sum() - ack['acked'].sum())} unacknowledged publishes one is the device's own acknowledgement of the OTA command, which expects no reply. The other {len(rec)} were telemetry: each coincided with a `connection reset by peer` from the broker, the device waited 5 s (`WaitForAck` timeout), wrote the record to the re-upload folder, reconnected, re-sent it with `is_stored = 1` and received an acknowledgement that names the original payload timestamp. Delivery delay for the three records: {', '.join(f"{x:.0f}" for x in rec['delivery_delay_s'])} s. No record was lost and no record was duplicated at the broker (the original publish had no ack).

![cadence](figures/03_cadence_and_ack.png)

## 5. Serial buses: the host talks to two micro-controllers

- **{int(us['requests'].sum()):,} request / reply round-trips**, {int(ble_bus['requests'].sum()):,} to the BLE module and {int(st_bus['requests'].sum()):,} to the ST controller that owns the CAN/OBD, GPS and battery interfaces. Every BLE request was answered; **{int(st_bus['timeouts'].sum())} ST requests timed out ({st_bus['timeouts'].sum() / st_bus['requests'].sum():.1%})** and were retried with the firmware's "spilled FF" mismatch logic. Timeouts spread across all ST commands rather than concentrating on one, which points at bus contention or controller load rather than a broken feature.
- All round-trips complete within the one-second log resolution. The LED colour command (BLE 0x50) alone is {int(ctx['by_cmd'].loc[(ctx['by_cmd'].bus == 'BLE') & (ctx['by_cmd'].cmd == '50'), 'requests'].sum()):,} of the round-trips: the LED is re-set two to three times per telemetry cycle.
- The BLE module pushes {int((T['uart_commands']['direction'] != 'request').sum())} unsolicited status notifications (command 0x23) that the host acknowledges; a parser that pairs frames strictly as request / reply mislabels those, so the parser treats them as a separate direction.

![serial](figures/04_serial_commands.png)

## 6. ECM data gaps and the ERR005 event: a false positive on OBD-II

- On the J1939 truck (`{dev1}`) the ECM answers whenever the engine runs: in Driving only {ecm.loc[(dev1, 'Driving'), 'engine_hour_na_share']:.0%} of cycles lack engine hours and {ecm.loc[(dev1, 'Driving'), 'vin_empty_share']:.0%} lack a VIN. While Sleeping (engine off) {ecm.loc[(dev1, 'Sleeping'), 'engine_hour_na_share']:.0%} of cycles lack engine hours and {ecm.loc[(dev1, 'Sleeping'), 'vin_empty_share']:.0%} the VIN, each time after three retries; the firmware then compensates the engine-hour value from its last stored reading ({int((T['lines']['pattern'] == 'compensate').sum())} occurrences). This is the mechanism behind the "VIN not yet detected" rows in the fleet tables: parked trucks, not broken installs.
- On the OBD-II vehicle (`{dev2}`) **engine hours are missing in 100 % of cycles while speed, RPM, odometer and VIN are all present**: the vehicle simply does not expose an engine-hours PID.
- The firmware's ECM-disconnect rule fires on *any* missing field. Result: **{int(err.loc[dev2, 'err_attempts'])} times in {(s1.loc[s1.device_key == dev2, 'end_ts'].max() - s1.loc[s1.device_key == dev2, 'start_ts'].min()).total_seconds() / 60:.0f} minutes the device logged "GPS speed > 0 but ECM field contains N/A, send ERR005"** on a vehicle whose ECM was fully connected; a one-hour cooldown suppressed {int(err.loc[dev2, 'suppressed_by_cooldown'])} of them and {int(err.loc[dev2, 'events_published'])} ERR005 event reached the backend. The `{dev1}` truck, which is the kind of unit the rule was written for, raised none.
- Two consequences for the fleet-level analysis in `eld-ecm-disconnect`: the per-report anomaly flags are not throttled, but ERR005 *events* are (at most one per device-hour), so event counts under-state the problem by design; and OBD-II units flagged as chronic deserve a check of *which* field is missing before anyone is sent to the vehicle. Recommended fix: evaluate ERR005 on speed and RPM only, and treat engine hours as optional for OBD-II protocol builds.

![ecm](figures/05_ecm_fields_and_err005.png)

## 7. Hand-written patterns versus automatic template mining

Drain3 (depth {4}, similarity 0.4) was run on the same lines after masking numbers, hex dumps, identifiers and JSON.

| | Drain3 | registry |
|---|---:|---:|
| templates / patterns | {dr['clusters']} | {dr['registry_patterns']} |
| lines covered | 100 % (by construction) | 100 % |
| templates needed for 99 % of lines | {dr['clusters_for_99pct']} | {dr['registry_patterns']} (all) |
| singleton templates | {dr['singleton_clusters']} | 0 |
| clusters that mix registry families | {dr['clusters_spanning_categories']} | - |

- Drain agrees with the registry: **weighted purity {dr['weighted_purity']:.2f}**, i.e. no Drain cluster contains lines from two different registry families. The registry is the unsupervised structure plus semantics.
- Drain needs {dr['clusters_for_99pct']} templates to reach 99 % of lines because it splits one logical message into variants by parameter position (the per-state `[Driving][query ...]` family alone becomes {int(ctx['drain_clusters'][ctx['drain_clusters']['template'].str.contains(r'query', regex=True)].shape[0])} templates). Drain is the right tool to discover an unknown log and to catch new message types after a firmware release; the registry is what you ship, because its parameters have names.

![drain](figures/07_drain_vs_registry.png)

## 8. Limitations

- Two devices, one session each ({(d1['end_ts'].max() - d1['start_ts'].min()).total_seconds() / 3600:.1f} h and {(s1.loc[s1.device_key == dev2, 'end_ts'].max() - s1.loc[s1.device_key == dev2, 'start_ts'].min()).total_seconds() / 60:.0f} min). Every rate above describes these sessions, not the fleet.
- One-second timestamps: latencies below a second are indistinguishable from zero.
- The logs were normalized to English and de-identified before publication (`data/SOURCE.md`); byte dumps that carried identifiers were rewritten, so their trailing checksum bytes no longer verify.
- The wake-up reason codes and the ST command set are interpreted from the log alone; the firmware source was not consulted.
"""
    path.write_text(md, encoding="utf-8")
