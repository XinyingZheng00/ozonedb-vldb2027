#!/usr/bin/env python3
"""Plot Figure 16: atomic-append throughput vs writer processes, buffered
vs O_DIRECT, with and without fsync, in the paper's style.

    python3 plot_append_pagecache.py ../data/<run>/summary.csv --out fig.pdf
    python3 plot_append_pagecache.py ../data/<run>/summary.csv --compare ../data/paper_values.csv

--compare overlays a second summary (e.g. the paper's values) in gray.
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
COLOR = "#dd8452"
# (sync, mode) -> legend label, marker, linestyle, filled. Legend columns
# fill top to bottom, so this order puts the fsync series in the left column.
SERIES = [
    ("fsync", "buffered", "fsync · Buffered", "o", "-", True),
    ("fsync", "direct", "fsync · O_DIRECT", "o", "--", False),
    ("none", "buffered", "no-sync · Buffered", "s", "-", True),
    ("none", "direct", "no-sync · O_DIRECT", "s", "--", False),
]
# 1-2-5 major ticks on the log axis, as in the paper (5k, 10k, 20k, 50k).
YTICKS = [m * 10 ** e for e in range(1, 8) for m in (1, 2, 5)]


def load(path):
    data = defaultdict(dict)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            data[(r["sync"], r["mode"])][int(r["procs"])] = float(r["ops_per_sec_mean"])
    return data


def kfmt(v, _):
    return f"{v / 1000:g}k" if v >= 1000 else f"{v:g}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("summary_csv", type=Path)
    ap.add_argument("--compare", type=Path, default=None)
    ap.add_argument("--compare-label", default="paper")
    ap.add_argument("--out", type=Path, default=HERE / "fig_append_pagecache.pdf")
    args = ap.parse_args()

    data = load(args.summary_csv)
    ref = load(args.compare) if args.compare else None
    ns = sorted({n for s in data.values() for n in s})
    pos = {n: i for i, n in enumerate(ns)}

    fig, ax = plt.subplots(figsize=(5.0, 1.8))
    for sync, mode, label, marker, ls, filled in SERIES:
        if ref and (sync, mode) in ref:
            pts = sorted((pos[n], v) for n, v in ref[(sync, mode)].items() if n in pos)
            ax.plot(*zip(*pts), color="0.65", linestyle=ls, linewidth=1.0, marker=marker,
                    markersize=3, markerfacecolor="0.65" if filled else "white", zorder=1)
        pts = sorted((pos[n], v) for n, v in data.get((sync, mode), {}).items())
        if not pts:
            continue
        ax.plot(*zip(*pts), color=COLOR, linestyle=ls, linewidth=1.4, marker=marker,
                markersize=4, markerfacecolor=COLOR if filled else "white",
                markeredgecolor=COLOR, label=label, zorder=2)

    ax.set_yscale("log")
    lo, hi = ax.get_ylim()
    ax.set_yticks([t for t in YTICKS if lo <= t <= hi])
    ax.set_ylim(lo, hi)
    ax.yaxis.set_major_formatter(FuncFormatter(kfmt))
    ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
    ax.set_xticks(range(len(ns)))
    ax.set_xticklabels([str(n) for n in ns], fontsize=11)
    ax.tick_params(axis="y", labelsize=11)
    ax.set_xlabel("Writer Processes N", fontsize=13)
    ax.set_ylabel("Append\nThroughput\n(ops/s)", fontsize=13)
    ax.grid(True, which="both", linewidth=0.4, alpha=0.5)
    handles, labels = ax.get_legend_handles_labels()
    if ref:
        handles.append(plt.Line2D([], [], color="0.65", linewidth=1.0))
        labels.append(args.compare_label)
    ax.legend(handles, labels, ncol=2, fontsize=10, frameon=False,
              loc="lower center", bbox_to_anchor=(0.5, 1.0))
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
