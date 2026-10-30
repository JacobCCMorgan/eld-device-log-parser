"""Command-line entry points.

    eldlog parse  data/logs/*.log.gz --out reports/tables     # Parquet event tables + coverage
    eldlog report data/logs/*.log.gz --out reports            # tables, metrics, figures, Markdown report
    eldlog normalize RAW.txt OUT.log.gz --hex-identifiers ... # publication step (needs private map + key)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import drain, metrics, report
from .parse import coverage, parse_many
from .patterns import PATTERNS
from .tables import build_all


def _write_tables(T: dict[str, pd.DataFrame], out: Path):
    out.mkdir(parents=True, exist_ok=True)
    for name, df in T.items():
        if len(df):
            d = df.copy()
            for c in d.columns:
                if d[c].dtype == object:
                    d[c] = d[c].astype(str).where(d[c].notna(), None)
            d.to_parquet(out / f"{name}.parquet", index=False)


def cmd_parse(paths: list[str], out: Path) -> dict:
    lines = parse_many(paths)
    T = build_all(lines)
    T["lines"] = lines
    _write_tables(T, out)
    cov = coverage(lines)
    cov.to_csv(out / "coverage.csv", index=False)
    return {"lines": int(len(lines)), "unparsed": int((lines["category"] == "other").sum()), "tables": {k: int(len(v)) for k, v in T.items()}}


def cmd_report(paths: list[str], out: Path, sim_th: float = 0.4) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    fig_dir = out / "figures"
    fig_dir.mkdir(exist_ok=True)
    tab_dir = out / "tables"
    lines = parse_many(paths)
    T = build_all(lines)
    T["lines"] = lines
    _write_tables(T, tab_dir)
    coverage(lines).to_csv(tab_dir / "coverage.csv", index=False)
    M = metrics.summarize(lines, T)
    for k, v in M.items():
        v.to_csv(tab_dir / f"metric_{k}.csv", index=False)
    mined = drain.mine(lines, sim_th=sim_th)
    dr, by_cluster = drain.compare(mined, len(PATTERNS))
    by_cluster.to_csv(tab_dir / "drain_clusters.csv", index=False)

    devs = list(T["boot_sessions"]["device_key"].unique())
    by_cmd = M["uart_by_command"]
    report.fig_timeline(T, fig_dir)
    report.fig_composition(M["log_composition"], fig_dir)
    report.fig_cadence_ack(T["uploads"], fig_dir)
    report.fig_uart(by_cmd, fig_dir)
    report.fig_ecm(M["ecm_retry_summary"], T["errors"], T["uploads"], fig_dir)
    report.fig_sessions(T["boot_sessions"], fig_dir)
    report.fig_drain(by_cluster, len(PATTERNS), fig_dir)
    ctx = {"metrics": M, "tables": T, "drain": dr, "drain_clusters": by_cluster, "n_patterns": len(PATTERNS), "by_cmd": by_cmd,
           "dev1": devs[0], "dev2": devs[1] if len(devs) > 1 else devs[0]}
    report.write_report(out / "firmware_log_report.md", ctx)
    summary = {"lines": int(len(lines)), "patterns": len(PATTERNS), "devices": devs, "coverage_unparsed": int((lines["category"] == "other").sum()),
               "tables": {k: int(len(v)) for k, v in T.items()}, "drain": dr,
               "ack": M["ack_summary"].to_dict(orient="records"), "cadence": M["publish_cadence"].to_dict(orient="records"),
               "uart": M["uart_summary"].to_dict(orient="records"), "err_events": M["err_event_summary"].to_dict(orient="records"),
               "sessions": T["boot_sessions"].assign(start_ts=lambda d: d["start_ts"].astype(str), end_ts=lambda d: d["end_ts"].astype(str)).to_dict(orient="records")}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    return summary


def _json_default(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    return str(o)


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="eldlog", description="Parse ELD firmware logs into event tables and metrics.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("parse", help="classify lines and write the event tables")
    p1.add_argument("paths", nargs="+")
    p1.add_argument("--out", type=Path, default=Path("reports/tables"))
    p2 = sub.add_parser("report", help="tables, metrics, figures and the Markdown report")
    p2.add_argument("paths", nargs="+")
    p2.add_argument("--out", type=Path, default=Path("reports"))
    p2.add_argument("--drain-sim", type=float, default=0.4)
    p3 = sub.add_parser("normalize", help="translate localized phrases and de-identify a raw log (publication step)")
    p3.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == "parse":
        print(json.dumps(cmd_parse(a.paths, a.out), indent=2))
    elif a.cmd == "report":
        s = cmd_report(a.paths, a.out, a.drain_sim)
        print(json.dumps({k: s[k] for k in ["lines", "patterns", "coverage_unparsed", "drain"]}, indent=2, default=_json_default))
    elif a.cmd == "normalize":
        from .normalize import main as norm_main
        norm_main(a.args)


if __name__ == "__main__":
    sys.exit(main())
