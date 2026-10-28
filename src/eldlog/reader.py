"""Read a firmware log (plain or gzip) into (line_no, level, timestamp, body) records.

Line format produced by the device's Go logger:

    Info   :2026/09/23 21:41:09 [main] System start...
    Consol :2026/09/23 21:47:04 [MQTT Publish]Payload:{...}
    Error  :2026/09/23 23:11:51 /work/iot/src/main/Controller/controller_mqtt.go:962: [WaitForAck]Time out
    ELD    :2026/09/23 21:41:06 [restore.sh] ...

Level is the Go logger name (Info, Consol = console mirror, Error, ELD = shell
scripts). Timestamps are device-local UTC with one-second resolution and no
timezone marker; the device reports its own offset separately.
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

PREFIX = re.compile(r"^(?P<level>[A-Za-z]+)\s*:(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) ?(?P<body>.*)$")
DEVICE_IN_NAME = re.compile(r"(DEV-[0-9a-f]{10})")


@dataclass
class LogLine:
    line_no: int
    level: str
    ts: datetime | None
    body: str
    raw: str


def open_text(path: str | Path):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def iter_lines(path: str | Path) -> Iterator[LogLine]:
    last_ts = None
    with open_text(path) as f:
        for i, raw in enumerate(f, start=1):
            raw = raw.rstrip("\n")
            m = PREFIX.match(raw)
            if m:
                ts = datetime.strptime(m.group("ts"), "%Y/%m/%d %H:%M:%S")
                last_ts = ts
                yield LogLine(i, m.group("level"), ts, m.group("body"), raw)
            else:
                # continuation line (rare): attach to the previous timestamp
                yield LogLine(i, "Cont", last_ts, raw, raw)


def device_key_from_path(path: str | Path) -> str | None:
    m = DEVICE_IN_NAME.search(Path(path).name)
    return m.group(1) if m else None
