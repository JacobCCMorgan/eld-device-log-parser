"""Prepare a raw firmware log for publication: translate localized operator
messages to English tokens and de-identify every identifier.

This step runs once, before a log enters the repository. It needs two private
inputs that are never committed:

* ELDLOG_PHRASE_MAP  - JSON file {"phrases": {localized: english, ...},
                       "punctuation": {localized: ascii, ...}}
* TDF_DEID_KEY_FILE  - the keyed-pseudonym secret shared with the
                       telematics-data-foundation tables, so DEV-/VIN-/DRV-
                       keys join across projects

Rules applied (see data/SOURCE.md):
  device serial  -> DEV-<hmac>     also inside ASCII-hex byte dumps
  VIN            -> VIN-<hmac>     also inside ASCII-hex byte dumps
  IMEI, ICCID    -> IMEI-<hmac>, ICCID-<hmac>
  driver id      -> DRV-<hmac>     (placeholders such as NODRIVERID are kept)
  latitude / longitude, in text, JSON payloads and GPS byte frames
                 -> rounded to 0.05 degrees; GPS frames are masked
  broker hosts, IPv4 addresses, URLs, tokens, keys, checksums, nonces
                 -> <broker-host>, <ip>, <url>, <redacted>
Lines that still contain non-ASCII characters after the phrase map are
reported and the characters replaced with '?', so the published file is
guaranteed ASCII.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

COORD_GRID = 0.05
DIGEST_CHARS = 10
PLACEHOLDERS = {"NODRIVERID", "VINNOTYETDETECTED", "NOTRAILERGPSID", ""}


class Pseudonymizer:
    """Same algorithm as telematics-data-foundation: HMAC-SHA256(key, namespace|value)."""

    def __init__(self, key: bytes):
        if len(key) < 16:
            raise ValueError("de-identification key must be at least 16 bytes")
        self.key = key

    @classmethod
    def from_env(cls, var: str = "TDF_DEID_KEY_FILE") -> Pseudonymizer:
        path = os.environ.get(var)
        if not path:
            raise RuntimeError(f"set {var} to the path of the de-identification key file")
        raw = Path(path).read_text(encoding="utf-8").strip()
        return cls(bytes.fromhex(raw) if re.fullmatch(r"[0-9a-fA-F]+", raw) else raw.encode())

    def one(self, namespace: str, prefix: str, value: str) -> str:
        msg = f"{namespace}|{value.strip().upper()}".encode()
        return prefix + hmac.new(self.key, msg, hashlib.sha256).hexdigest()[:DIGEST_CHARS]


@dataclass
class PhraseMap:
    phrases: dict[str, str]
    punctuation: dict[str, str]
    _pattern: re.Pattern = field(init=False, repr=False)
    _punct: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        keys = sorted(self.phrases, key=len, reverse=True)
        self._pattern = re.compile("|".join(re.escape(k) for k in keys)) if keys else re.compile(r"(?!x)x")
        pk = sorted(self.punctuation, key=len, reverse=True)
        self._punct = re.compile("|".join(re.escape(k) for k in pk)) if pk else re.compile(r"(?!x)x")

    @classmethod
    def from_env(cls, var: str = "ELDLOG_PHRASE_MAP") -> PhraseMap:
        path = os.environ.get(var)
        if not path:
            raise RuntimeError(f"set {var} to the path of the phrase map JSON")
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d.get("phrases", {}), d.get("punctuation", {}))

    @classmethod
    def empty(cls) -> PhraseMap:
        return cls({}, {})

    def apply(self, body: str) -> str:
        s = self._pattern.sub(lambda m: " " + self.phrases[m.group(0)] + " ", body)
        s = self._punct.sub(lambda m: self.punctuation[m.group(0)], s)
        s = re.sub(r"\[ +", "[", s)
        s = re.sub(r" +\]", "]", s)
        s = re.sub(r" +([:,;.)])", r"\1", s)
        s = re.sub(r"\( +", "(", s)
        s = re.sub(r"[ \t]{2,}", " ", s)
        return s.rstrip()


PREFIX = re.compile(r"^(?P<level>[A-Za-z]+)(?P<pad>\s*):(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) ?(?P<body>.*)$")
HEX_DUMP = re.compile(r"^(?P<head>(?:Send|Read)_data\[)(?P<n>\d+)(?P<mid> byte\]: )(?P<bytes>(?:[0-9A-F]{2} ?)+)$")


def _hex(s: str) -> str:
    return " ".join(f"{b:02X}" for b in s.encode("ascii"))


def coarsen(value: str) -> str:
    try:
        v = float(value)
    except ValueError:
        return value
    return f"{round(v / COORD_GRID) * COORD_GRID:.2f}"


@dataclass
class Deidentifier:
    pseudo: Pseudonymizer
    serial_re: re.Pattern = re.compile(r"\bRT\d{8}\b")
    vin_re: re.Pattern = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]{17}\b)(?=.*\d)[A-HJ-NPR-Z0-9]{17}\b")
    imei_re: re.Pattern = re.compile(r"\b\d{15}\b")
    iccid_re: re.Pattern = re.compile(r"\b\d{19,20}\b")
    ipv4_re: re.Pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
    host_re: re.Pattern = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:amazonaws\.com|com|net|io)(?::\d+)?\b")
    url_re: re.Pattern = re.compile(r"\b(?:https?|mqtts?)://[^\s\"']+")
    coord_text_re: re.Pattern = re.compile(r"((?:Latitude|Longitude|gpsLatitude\]|gpsLongitude\]):\s*)(-?\d+\.\d+)")
    coord_json_re: re.Pattern = re.compile(r"(\"(?:lat|lon)\":\s*\")(-?\d+\.\d+)(\")")
    secret_json_re: re.Pattern = re.compile(r"(\\?\"(?:token|root_ca_checksum|root_ca_download_url|random|key)\\?\":\s*\\?\")([^\"\\]*)(\\?\")")
    login_key_re: re.Pattern = re.compile(r"(\[Login\]\s*key\s*:\s*)(\S+)")
    carrier_re: re.Pattern = re.compile(r"\b1nce\b", re.I)
    counts: dict[str, int] = field(default_factory=dict)
    _hex_subs: list[tuple[str, str]] = field(default_factory=list)

    def _bump(self, k: str):
        self.counts[k] = self.counts.get(k, 0) + 1

    def _serial(self, m: re.Match) -> str:
        self._bump("serial")
        return self.pseudo.one("device", "DEV-", m.group(0))

    def _vin(self, m: re.Match) -> str:
        self._bump("vin")
        return self.pseudo.one("vin", "VIN-", m.group(0))

    def register_hex_identifiers(self, values: list[str]):
        """Identifiers that may also appear ASCII-hex-encoded inside byte dumps."""
        for v in values:
            if self.serial_re.fullmatch(v):
                rep = self.pseudo.one("device", "DEV-", v)
            elif self.vin_re.fullmatch(v):
                rep = self.pseudo.one("vin", "VIN-", v)
            else:
                continue
            self._hex_subs.append((_hex(v), _hex(rep)))

    def body(self, text: str) -> str:
        m = HEX_DUMP.match(text)
        if m:
            return self._hex_dump(m)
        t = self.login_key_re.sub(lambda mo: mo.group(1) + "<redacted>", text)
        t = self.secret_json_re.sub(lambda mo: mo.group(1) + "<redacted>" + mo.group(3), t)
        t = self.url_re.sub("<url>", t)
        t = self.host_re.sub("<broker-host>", t)
        t = self.ipv4_re.sub("<ip>", t)
        t = self.coord_text_re.sub(lambda mo: mo.group(1) + coarsen(mo.group(2)), t)
        t = self.coord_json_re.sub(lambda mo: mo.group(1) + coarsen(mo.group(2)) + mo.group(3), t)
        t = self.vin_re.sub(self._vin, t)
        t = self.serial_re.sub(self._serial, t)
        t = self.iccid_re.sub(lambda mo: (self._bump("iccid"), self.pseudo.one("iccid", "ICCID-", mo.group(0)))[1], t)
        t = self.imei_re.sub(lambda mo: (self._bump("imei"), self.pseudo.one("imei", "IMEI-", mo.group(0)))[1], t)
        t = self.carrier_re.sub("<carrier>", t)
        for h, rep in self._hex_subs:  # decoded frames such as "ECMData: ..." repeat the bytes
            if h in t:
                t = t.replace(h, rep)
                self._bump("hex_identifier")
        if t.startswith("GPSData:"):
            self._bump("gps_frame")
            return "GPSData: " + " ".join(["xx"] * len(t.split()[1:]))
        return t

    def _hex_dump(self, m: re.Match) -> str:
        raw = m.group("bytes").strip()
        new = raw
        for h, rep in self._hex_subs:
            if h in new:
                new = new.replace(h, rep)
                self._bump("hex_identifier")
        # ST GPS reply (command 0x42): mask the coordinate bytes after the 4-byte header
        if new.startswith("08 00 10 42"):
            parts = new.split()
            new = " ".join(parts[:4] + ["xx"] * (len(parts) - 4))
            self._bump("gps_frame")
        n = len(new.split())
        return f"{m.group('head')}{n}{m.group('mid')}{new}"


def normalize_line(line: str, phrases: PhraseMap, deid: Deidentifier | None) -> tuple[str, bool]:
    """Returns (normalized line, had_residual_non_ascii)."""
    m = PREFIX.match(line.rstrip("\n"))
    if not m:
        body, prefix = line.rstrip("\n"), ""
    else:
        prefix = f"{m.group('level')}{m.group('pad')}:{m.group('ts')} "
        body = m.group("body")
    body = phrases.apply(body)
    if deid is not None:
        body = deid.body(body)
    residual = bool(re.search(r"[^\x00-\x7F]", body))
    if residual:
        body = re.sub(r"[^\x00-\x7F]", "?", body)
    return (prefix + body).rstrip(), residual


def normalize_file(src: Path, dst: Path, phrases: PhraseMap, deid: Deidentifier | None) -> dict:
    opener = gzip.open if str(dst).endswith(".gz") else open
    n = residual = 0
    with open(src, encoding="utf-8", errors="replace") as fin, opener(dst, "wt", encoding="ascii") as fout:
        for line in fin:
            out, bad = normalize_line(line, phrases, deid)
            fout.write(out + "\n")
            n += 1
            residual += bad
    stats = {"lines": n, "residual_non_ascii_lines": residual}
    if deid is not None:
        stats["replacements"] = dict(deid.counts)
    return stats


def main(argv: list[str] | None = None):
    import argparse

    ap = argparse.ArgumentParser(description="Normalize and de-identify a raw firmware log for publication.")
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path, help="output path; .gz suffix compresses")
    ap.add_argument("--hex-identifiers", nargs="*", default=[], help="serials / VINs that may appear ASCII-hex-encoded in byte dumps")
    ap.add_argument("--no-deid", action="store_true", help="phrase normalization only (for already de-identified input)")
    a = ap.parse_args(argv)
    phrases = PhraseMap.from_env() if os.environ.get("ELDLOG_PHRASE_MAP") else PhraseMap.empty()
    deid = None
    if not a.no_deid:
        deid = Deidentifier(Pseudonymizer.from_env())
        deid.register_hex_identifiers(a.hex_identifiers)
    stats = normalize_file(a.src, a.dst, phrases, deid)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
