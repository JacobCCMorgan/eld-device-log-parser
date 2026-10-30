"""Compare the hand-written pattern registry with Drain3 automatic template mining.

Drain3 clusters log messages into templates without any prior knowledge. The
comparison answers two questions a team asks before investing in hand-written
parsers: how many templates does the log really contain, and does the
hand-written registry agree with the unsupervised view?

Metrics
  clusters              number of Drain templates found
  lines_in_top_k        share of lines covered by the k largest templates
  purity                for each Drain cluster, the share of its lines that fall in the
                        registry's majority category; weighted by cluster size. 1.0 means
                        the two views never disagree about which family a line belongs to.
  registry_patterns     number of hand-written patterns
"""
from __future__ import annotations

import re

import pandas as pd

MASK = [
    (re.compile(r"(?:[0-9A-Fa-fx]{2} ){3,}[0-9A-Fa-fx]{2}"), "<HEX>"),
    (re.compile(r"\{.*\}"), "<JSON>"),
    (re.compile(r"\b(?:DEV|VIN|IMEI|ICCID|DRV)-[0-9a-f]{10}\b"), "<ID>"),
    (re.compile(r"\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2}"), "<DT>"),
    (re.compile(r"-?\d+(?:\.\d+)?"), "<NUM>"),
]


def mask(body: str) -> str:
    for rx, rep in MASK:
        body = rx.sub(rep, body)
    return body


def mine(lines: pd.DataFrame, sim_th: float = 0.4, depth: int = 4) -> pd.DataFrame:
    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig

    cfg = TemplateMinerConfig()
    cfg.drain_sim_th = sim_th
    cfg.drain_depth = depth
    cfg.profiling_enabled = False
    miner = TemplateMiner(config=cfg)
    ids = []
    for body in lines["body"].fillna(""):
        res = miner.add_log_message(mask(body) if body.strip() else "<BLANK>")
        ids.append(res["cluster_id"])
    out = lines[["device_key", "line_no", "category", "pattern"]].copy()
    out["cluster_id"] = ids
    templates = {c.cluster_id: c.get_template() for c in miner.drain.clusters}
    out["template"] = out["cluster_id"].map(templates)
    return out


def compare(mined: pd.DataFrame, n_registry_patterns: int) -> tuple[dict, pd.DataFrame]:
    by_cluster = mined.groupby("cluster_id").agg(lines=("line_no", "size"), template=("template", "first"),
                                                 majority_category=("category", lambda s: s.value_counts().idxmax()),
                                                 purity=("category", lambda s: s.value_counts().iloc[0] / len(s)),
                                                 registry_patterns=("pattern", "nunique")).sort_values("lines", ascending=False).reset_index()
    total = by_cluster["lines"].sum()
    cum = by_cluster["lines"].cumsum() / total
    summary = {
        "lines": int(total), "clusters": int(len(by_cluster)), "registry_patterns": int(n_registry_patterns),
        "lines_in_top_20": float(cum.iloc[min(19, len(cum) - 1)]), "lines_in_top_50": float(cum.iloc[min(49, len(cum) - 1)]),
        "clusters_for_99pct": int((cum < 0.99).sum() + 1),
        "weighted_purity": float((by_cluster["purity"] * by_cluster["lines"]).sum() / total),
        "clusters_spanning_categories": int((by_cluster["purity"] < 1).sum()),
        "singleton_clusters": int((by_cluster["lines"] == 1).sum()),
    }
    return summary, by_cluster
