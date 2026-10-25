#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "matplotlib",
#     "numpy",
# ]
# ///
"""Stacked bar chart of where db_bench UpdateRandom spends its time.

The script renormalizes each bar to 100% over the two categories in the data
file (Open/Close and Operations). The bars compare the *ratio* of the two
categories, not their share of wall-clock time. See time_breakdown.csv for the
raw sample percentages and their provenance.

Usage:
  uv run plot_time_breakdown.py -o time_breakdown.pdf
  uv run plot_time_breakdown.py other.csv -o out.png
"""

import argparse
import csv
import sys
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

DEFAULT_CSV = "time_breakdown.csv"
DEFAULT_TITLE = "UpdateRandom 4 Threads: Reopen vs Baseline"

# The stack order of the segments, and the color each segment gets in every bar.
SEGMENTS = ["Open/Close", "Operations"]
SEGMENT_COLORS = {
    "Open/Close": "#2e86ab",
    "Operations": "#28a745",
}

# Segments thinner than this get their label above the bar instead of inside it.
THIN_SEGMENT_PCT = 4


def load_csv(path):
    """Read mode,segment,percent rows into {mode: {segment: percent}}.

    The function skips blank lines and '#' comment lines. It keeps the mode order.
    """
    data = defaultdict(dict)
    with open(path) as f:
        rows = (line for line in f if line.strip() and not line.startswith("#"))
        for row in csv.DictReader(rows):
            data[row["mode"]][row["segment"]] = float(row["percent_of_samples"])
    return data


def plot_stacked(data, title, output_path=None):
    fig, ax = plt.subplots(figsize=(5, 6))

    modes = list(data.keys())
    # Renormalize each mode so its segments sum to 100%.
    shares = {}
    for mode in modes:
        total = sum(data[mode].get(seg, 0) for seg in SEGMENTS)
        shares[mode] = {
            seg: (data[mode].get(seg, 0) / total * 100) if total > 0 else 0
            for seg in SEGMENTS
        }

    x = np.arange(len(modes))
    bottom = np.zeros(len(modes))

    for seg in SEGMENTS:
        values = [shares[mode][seg] for mode in modes]
        bars = ax.bar(x, values, 0.5, bottom=bottom, label=seg, color=SEGMENT_COLORS[seg])
        for bar, val in zip(bars, values):
            if val < 0.01:
                continue
            if val < THIN_SEGMENT_PCT:
                y, va, text_color = bar.get_y() + bar.get_height() + 0.5, "bottom", "black"
            else:
                y, va, text_color = bar.get_y() + bar.get_height() / 2, "center", "white"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                y,
                f"{val:.1f}%",
                ha="center",
                va=va,
                fontsize=18,
                fontweight="bold",
                color=text_color,
            )
        bottom += np.array(values)

    ax.set_xticks(x)
    ax.set_xticklabels(modes, fontsize=20)
    ax.set_ylabel("Share of total time (%)", fontsize=20)
    ax.set_title(title, fontsize=21)
    ax.set_ylim(0, 108)  # headroom for labels above thin segments
    ax.set_yticks(np.arange(0, 101, 20))
    ax.tick_params(axis="y", labelsize=18)
    ax.legend(loc="upper right", fontsize=18)
    ax.grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to {output_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", nargs="?", default=DEFAULT_CSV,
                        help=f"Breakdown CSV (default: {DEFAULT_CSV})")
    parser.add_argument("-o", "--output",
                        help="Output image path (default: show interactively)")
    parser.add_argument("-t", "--title", default=DEFAULT_TITLE, help="Plot title")
    args = parser.parse_args()

    data = load_csv(args.csv)
    if not data:
        print(f"No data in {args.csv}", file=sys.stderr)
        sys.exit(1)

    plot_stacked(data, args.title, args.output)


if __name__ == "__main__":
    main()
