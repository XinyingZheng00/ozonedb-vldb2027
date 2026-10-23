#!/usr/bin/env python3
"""Generate avg_latency.pdf (mean log_metric() latency vs P, log-y) from
trials.csv.

Usage:
    python3 plot_avg_latency.py [--csv PATH] [--out PATH]
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_common import (BACKEND_COLOR, BACKEND_LABEL, BACKEND_LINESTYLE,
                          BACKEND_ORDER, all_ps, load_rows, series_with_error,
                          style_axes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "avg_latency.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ps = all_ps(rows)

    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    for backend in BACKEND_ORDER:
        xs, ys, errs = series_with_error(rows, backend, "mean_latency_ms", ps)
        if not xs:
            continue
        ax.errorbar(xs, ys, yerr=errs, marker="o", markersize=4, linewidth=1.6,
                     capsize=2.5, elinewidth=0.9, linestyle=BACKEND_LINESTYLE[backend],
                     color=BACKEND_COLOR[backend], label=BACKEND_LABEL[backend])

    style_axes(ax, ps, "avg log_metric() latency (ms, log scale)",
               "Average latency vs. concurrency")
    ax.legend(fontsize=6.5, frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
