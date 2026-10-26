#!/usr/bin/env python3
"""Generate throughput.pdf (cycles/sec vs N, log-y) from trials.csv.

Usage:
    python3 plot_throughput.py [--csv PATH] [--out PATH]
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
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "plots" / "throughput.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ns = all_ns(rows)

    fig, ax = plt.subplots(figsize=(5, 3.5))
    for backend in BACKEND_ORDER:
        xs, ys, errs = series_with_error(rows, backend, "cycles_per_sec", ns)
        if not xs:
            continue
        ax.errorbar(xs, ys, yerr=errs, marker="o", markersize=4, linewidth=1.6,
                     capsize=2.5, elinewidth=0.9,
                     color=BACKEND_COLOR[backend], label=BACKEND_LABEL[backend])

    # style_axes(ax, ns, "Throughput (cycles/sec, log scale)", "Throughput vs. concurrency", font=9)
    style_axes(ax, ns, "Throughput\n(cycles/sec, log scale)", "")
    ax.set_ylabel("Throughput\n(cycles/sec)", fontsize=20)
    ax.set_xlabel("N (concurrent CLI workers)", fontsize=19)
    ax.legend(
        fontsize=15, frameon=False, loc='upper center',
        bbox_to_anchor=(0.36, -0.3), ncol=3,
        columnspacing=0.8,    # gap between columns (default 2.0)
        handletextpad=0.4,    # gap between handle and its text (default 0.8)
        handlelength=1.5,     # length of the legend line/marker (default 2.0)
    )
    plt.xticks(fontsize=15)
    plt.yticks(fontsize=16)
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
