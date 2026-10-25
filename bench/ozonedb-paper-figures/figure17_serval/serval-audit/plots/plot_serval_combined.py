#!/usr/bin/env python3
"""Generate serval_combined.pdf: throughput and mean add() latency side by
side. The panels share one x-axis label and one legend. This script replaces
the two separate plot_throughput.py / plot_mean_latency.py PDFs when both
panels belong in a single figure.

Usage:
    python3 plot_serval_combined.py [--csv PATH] [--out PATH]
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_common import (BACKEND_COLOR, BACKEND_ORDER,
                          all_ns, load_rows, series_with_error, style_axes)

# (field, y-axis label, ylabel fontsize) for each panel, left to right.
# The labels and sizes are copied verbatim from plot_throughput.py / plot_mean_latency.py.
PANELS = [
    ("cycles_per_sec", "Throughput\n(cycles/sec)", 23),
    ("mean_add_ms", "Mean add()\nLatency (ms)", 22),
]

# Legend labels: bare names for the two in-process backends. The third is
# spelled out as what it is (SQLite behind a REST service).
LEGEND_LABEL = {
    "ozonedb": "OzoneDB",
    "sqlite": "SQLite",
    "sqlite_rest": "Re-architected (SQLite w/ REST daemon)",
}

# Distinct marker per backend. Before, all three lines used "o" and relied
# on color alone to tell them apart.
MARKER = {
    "ozonedb": "o",
    "sqlite": "s",
    "sqlite_rest": "^",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "serval_combined.pdf"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ns = all_ns(rows)

    # The per-panel figsize is the same as in the standalone scripts (5 x 3.5), two panels wide.
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.5))

    for ax, (field, ylabel, ylabel_size) in zip(axes, PANELS):
        for backend in BACKEND_ORDER:
            xs, ys, errs = series_with_error(rows, backend, field, ns)
            if not xs:
                continue
            ax.errorbar(xs, ys, yerr=errs, marker=MARKER[backend], markersize=8.5, linewidth=1.6,
                        capsize=2.5, elinewidth=0.9,
                        color=BACKEND_COLOR[backend], label=LEGEND_LABEL[backend])
        # style_axes sets an x-label per axis. Clear it. We use one shared
        # label via fig.text below.
        style_axes(ax, ns, ylabel, "")
        ax.set_xlabel("")
        ax.set_ylabel(ylabel, fontsize=ylabel_size)
        ax.tick_params(axis="x", labelsize=18)
        ax.tick_params(axis="y", labelsize=18)

    # Reserve the bottom strip of the figure for the shared x-label and
    # legend, stacked. We do NOT use tight_layout on purpose. It does not
    # account for fig.text / fig.legend, and it would clip or overlap them.
    fig.subplots_adjust(left=0.15, right=0.98, top=0.96, bottom=0.34, wspace=0.35)

    # One shared x-axis label under both panels.
    fig.text(0.5, 0.17, "N (concurrent CLI workers)", ha="center", fontsize=22)

    # One shared legend, centered below the x-label. Pull the handles from the
    # first axis. Every panel plots the same three backends in the same order.
    handles, labels = axes[0].get_legend_handles_labels()
    # y=0.0 is the figure's bottom edge. If we push the legend below that,
    # the canvas clips it. So bbox_inches="tight" below lets the save
    # auto-grow to fit the small overflow instead.
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False,
               fontsize=20, bbox_to_anchor=(0.5, -0.05),
               columnspacing=0.6, handletextpad=0.3, handlelength=1.3)

    fig.savefig(args.out, bbox_inches="tight", pad_inches=0.02)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
