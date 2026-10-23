#!/usr/bin/env python3
"""Generate p99_add_latency.pdf (p99 add() latency vs N, log-y) from
trials.csv.

Usage:
    python3 plot_p99_latency.py [--csv PATH] [--out PATH]
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_common import (BACKEND_COLOR, BACKEND_LABEL, BACKEND_ORDER,
                          all_ns, load_rows, series_with_error, style_axes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "p99_add_latency.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ns = all_ns(rows)

    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    for backend in BACKEND_ORDER:
        xs, ys, errs = series_with_error(rows, backend, "p99_add_ms", ns)
        if not xs:
            continue
        ax.errorbar(xs, ys, yerr=errs, marker="o", markersize=4, linewidth=1.6,
                     capsize=2.5, elinewidth=0.9,
                     color=BACKEND_COLOR[backend], label=BACKEND_LABEL[backend])

    style_axes(ax, ns, "p99 add() latency (ms, log scale)", "Tail (p99) add() latency vs. concurrency")
    ax.legend(fontsize=7.5, frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
