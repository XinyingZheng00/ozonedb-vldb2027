#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "matplotlib",
# ]
# ///
"""Throughput vs. worker count from the reopen benchmark results CSV.

The CSV holds several repeats per (mode, workers); each point is the mean of
those repeats after dropping the single lowest and highest, with the error bar
showing the sample stdev of what remains.

Usage:
  uv run plot_reopen_results.py results_20260219_180411.csv -o reopen_plot.pdf
"""

import argparse
import csv
import glob
import math
import os
import statistics
import sys
from collections import defaultdict

import matplotlib.pyplot as plt

# NOTE: the "mode" values in the CSV are misnomers. reopen_benchmark.sh runs both
# loops as a single db_bench process with --threads=$T; the only difference is that
# the "multi-process" loop passes --reopen_after_each_op. So the two curves compare
# reopen-per-op against the baseline, not processes against threads.
MODE_LABELS = {"multi-thread": "threads", "multi-process": "processes"}

COLORS = ["#2e86ab", "#a23b72", "#28a745", "#fd7e14", "#6f42c1"]
MARKERS = ["o", "s", "v", "^", "D"]


def mean_stdev_trimmed(values):
    """Mean and sample stdev after dropping one min and one max.

    Fewer than 3 samples leaves nothing to trim, so all values are used.
    """
    if len(values) >= 3:
        values = sorted(values)[1:-1]
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    return mean, stdev


def load_csv(path):
    """Load one CSV into a list of (workers, means, stdevs, label), one per mode.

    Non-positive throughputs are dropped: the y axis is log-scaled, so they
    cannot be drawn. (In results_20260219_180411.csv this removes multi-process
    at 24 workers, where every repeat recorded 0 ops/sec.)
    """
    by_mode = defaultdict(lambda: defaultdict(list))
    dropped = defaultdict(int)

    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                mode = row.get("mode") or "default"
                workers = int(row.get("workers") or row["threads"])
                ops = float(row["avg_ops_sec"])
            except (ValueError, KeyError, TypeError) as e:
                print(f"Warning: skipping row {row}: {e}", file=sys.stderr)
                continue
            if ops <= 0:
                dropped[(mode, workers)] += 1
                continue
            by_mode[mode][workers].append(ops)

    for (mode, workers), n in sorted(dropped.items()):
        print(
            f"Note: dropped {n} non-positive sample(s) for {mode} @ {workers} "
            "workers (not plottable on a log axis)",
            file=sys.stderr,
        )

    datasets = []
    for mode in sorted(by_mode):
        workers = sorted(by_mode[mode])
        stats = [mean_stdev_trimmed(by_mode[mode][w]) for w in workers]
        means = [m for m, _ in stats]
        stdevs = [s for _, s in stats]
        datasets.append((workers, means, stdevs, MODE_LABELS.get(mode, mode)))

    return datasets


def plot_results(datasets, output_path=None):
    plt.rcParams.update({"font.size": 16})
    fig, ax = plt.subplots(figsize=(5, 2.5))

    for i, (workers, means, stdevs, label) in enumerate(datasets):
        ax.errorbar(
            workers,
            means,
            yerr=stdevs,
            fmt=f"{MARKERS[i % len(MARKERS)]}-",
            color=COLORS[i % len(COLORS)],
            linewidth=2,
            markersize=7,
            capsize=3,
            capthick=1.5,
            label=label,
        )

    ax.set_xlabel("Number of Workers", fontsize=18)
    ax.set_ylabel("Throughput\n(ops/sec)", fontsize=18)
    ax.tick_params(axis="both", labelsize=14)
    # Call out these worker counts specifically, alongside the automatic
    # ticks -- pinning xlim afterwards, since set_xticks otherwise expands
    # the view to fit every fixed tick (e.g. a stray -5 and 30).
    ax.set_xticks(sorted(set(ax.get_xticks()) | {5, 15, 25}))
    ax.set_xlim(0, 25)
    ax.set_yscale("log")

    # A gridline at every power of 10 within the visible range, but only the
    # top and bottom decade get a number, so the axis doesn't get crowded
    # with labels.
    bottom, top = ax.get_ylim()
    lo_exp, hi_exp = math.ceil(math.log10(bottom)), math.floor(math.log10(top))
    if hi_exp >= lo_exp:
        decades = range(lo_exp, hi_exp + 1)
        ax.set_yticks([10.0**e for e in decades])
        ax.set_yticklabels(
            [f"$10^{{{e}}}$" if e in (lo_exp, hi_exp) else "" for e in decades]
        )

    ax.legend(fontsize=15, loc="center right")
    ax.grid(True, alpha=0.3, which="major")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to {output_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", nargs="*",
                        help="Path(s) to results CSV (default: newest results_*.csv)")
    parser.add_argument("-o", "--output",
                        help="Output image path (default: show interactively)")
    args = parser.parse_args()

    csv_paths = args.csv
    if not csv_paths:
        csv_paths = sorted(glob.glob("results_*.csv"), reverse=True)[:1]
        if not csv_paths:
            print("No results_*.csv found.", file=sys.stderr)
            sys.exit(1)
        print(f"Using {csv_paths[0]}")

    datasets = []
    for path in csv_paths:
        if not os.path.exists(path):
            print(f"File not found: {path}", file=sys.stderr)
            continue
        found = load_csv(path)
        if not found:
            print(f"No valid data in {path}", file=sys.stderr)
            continue
        datasets.extend(found)

    if not datasets:
        print("No valid data to plot.", file=sys.stderr)
        sys.exit(1)

    plot_results(datasets, args.output)


if __name__ == "__main__":
    main()
