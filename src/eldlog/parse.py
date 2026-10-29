"""Classify every line of a log into the `lines` table."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .patterns import classify
from .reader import device_key_from_path, iter_lines

PROMOTED = ["state", "component", "cmd", "retcode", "topic", "key", "op", "metric", "value", "code"]


def parse_file(path: str | Path, device_key: str | None = None) -> pd.DataFrame:
    """One row per log line with pattern name, category and extracted parameters."""
    device_key = device_key or device_key_from_path(path) or Path(path).stem
    rows = []
    for ln in iter_lines(path):
        name, cat, params = classify(ln.body)
        row = {"device_key": device_key, "line_no": ln.line_no, "ts": ln.ts, "level": ln.level, "pattern": name, "category": cat, "body": ln.body,
               "params": json.dumps(params, separators=(",", ":")) if params else None}
        for k in PROMOTED:
            row[k] = params.get(k)
        rows.append(row)
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def parse_many(paths: list[str | Path]) -> pd.DataFrame:
    return pd.concat([parse_file(p) for p in paths], ignore_index=True)


def coverage(lines: pd.DataFrame) -> pd.DataFrame:
    """Share of lines per category and the unparsed remainder, per device."""
    g = lines.groupby(["device_key", "category"]).size().rename("lines").reset_index()
    tot = lines.groupby("device_key").size().rename("total")
    g = g.merge(tot, on="device_key")
    g["share"] = g["lines"] / g["total"]
    return g.sort_values(["device_key", "lines"], ascending=[True, False]).reset_index(drop=True)
