#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "matplotlib==3.11.1",
#     "pandas==3.0.5",
#     "numpy==2.5.2",
# ]
# [tool.uv]
# exclude-newer = "2026-08-30T00:00:00Z"
# ///
"""Grouped bar plot of SlateDB throughput by writer count and flush interval.

Usage
-----
    uv run --script plot-multi-writer-bar.py results/<run>.csv
    ./plot-multi-writer-bar.py results/<run>.csv          # same thing, via the shebang

`uv` is the only prerequisite. The PEP 723 header above pins the Python and
library versions this figure was produced with, and uv fetches them into a
throwaway environment on first run -- nothing to install, and no effect on any
Python you already have. Get uv from https://docs.astral.sh/uv/.

Running it under a plain interpreter works too, but then the pins are on you:

    python plot-multi-writer-bar.py results/<run>.csv     # needs matplotlib, pandas, numpy

Writes `<run>-bar.pdf` (vector, for LaTeX) and `<run>-bar.png` (400 dpi, for
slides) next to the input CSV. `--out` overrides the destination.

Input
-----
A CSV with one row per benchmark run and at least these columns:

    num_writers          writer node count for the run
    flush_interval_ms    SlateDB flush interval for the run
    effective_puts_per_s throughput; puts that survived fencing

Repeated runs of the same (num_writers, flush_interval_ms) pair are averaged,
and the error bars are the standard error of those repeats.

Figure sizing
-------------
The defaults target an IEEE two-column paper. An IEEE text column is ~3.5in
wide; the figure is deliberately drawn a bit narrower at 3.0 x 1.35in so it can
be included at its natural size, with the fonts set here printing at their true
6-7.5pt. Include it WITHOUT a width= key:

    \\includegraphics{<run>-bar.pdf}

Scaling it to \\columnwidth would enlarge the text past what was chosen here.
Use `--figsize W H` for a double-column or slide version instead.
"""

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

VALUE_COLUMN = "effective_puts_per_s"
REQUIRED_COLUMNS = ["num_writers", "flush_interval_ms", VALUE_COLUMN]

DEFAULT_FIGSIZE = (3.0, 1.0)  # inches; see "Figure sizing" above
BAR_GROUP_WIDTH = 0.82  # fraction of an x slot filled by one writer-count group

# Print-sized type and hairlines. Everything is in points, so it only looks
# right if the PDF is placed at 100% -- see "Figure sizing" above.
PRINT_STYLE = {
    "font.size": 6.5,
    "axes.labelsize": 7.5,
    "axes.titlesize": 7.5,
    "xtick.labelsize": 6.5,
    "ytick.labelsize": 6.5,
    "legend.fontsize": 6,
    "axes.linewidth": 0.5,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "xtick.major.size": 2,
    "ytick.major.size": 2,
    "pdf.fonttype": 42,  # embed TrueType, so PDF text stays searchable/editable
    "ps.fonttype": 42,
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv_path", type=Path, help="benchmark results CSV")
    p.add_argument("--out", type=Path, default=None,
                   help="output PDF path (default: <csv>-bar.pdf)")
    p.add_argument("--figsize", nargs=2, type=float, default=DEFAULT_FIGSIZE,
                   metavar=("W", "H"),
                   help="figure size in inches (default: %(default)s)")
    p.add_argument("--title", default=None,
                   help="in-figure title (default: none; put it in the LaTeX caption)")
    p.add_argument("--show", action="store_true", help="open an interactive window")
    return p.parse_args()


def load(csv_path):
    """Return (writer counts, flush intervals, mean throughput, standard error)."""
    df = pd.read_csv(csv_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"{csv_path}: missing column(s): {', '.join(missing)}")

    grouped = df.groupby(["num_writers", "flush_interval_ms"])[VALUE_COLUMN]
    means = grouped.mean().unstack(fill_value=0)
    sems = grouped.sem().unstack(fill_value=0)
    return sorted(means.index), sorted(means.columns), means, sems


def plot(writers, intervals, means, sems, figsize, title=None):
    fig, ax = plt.subplots(figsize=tuple(figsize))

    x = np.arange(len(writers))
    width = BAR_GROUP_WIDTH / len(intervals)
    # Flush interval is ordinal, so it gets a single-hue light-to-dark ramp
    # rather than a categorical palette: the reading order is in the color.
    colors = plt.cm.Blues(np.linspace(0.35, 0.95, len(intervals)))

    for i, interval in enumerate(intervals):
        offset = (i - len(intervals) / 2 + 0.5) * width
        ax.bar(x + offset, means[interval].reindex(writers, fill_value=0), width,
               yerr=sems[interval].reindex(writers, fill_value=0),
               color=colors[i], edgecolor="white", linewidth=0.25,
               capsize=1, error_kw={"elinewidth": 0.5, "capthick": 0.5},
               label=f"{interval}")

    ax.set_xlabel("Number of Writer Nodes")
    ax.set_ylabel("Throughput\n(ops/s)")
    if title:
        ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(writers)

    # Throughput spans ~3.5 decades across writer counts, so the y axis is log.
    # Zeros can't be shown on it; the limits are pinned to the positive data so
    # no decade is wasted below the smallest bar, with headroom above the
    # tallest for the legend.
    ax.set_yscale("log")
    positive = means.values[means.values > 0]
    if positive.size:
        bottom, top = positive.min() / 2, positive.max() * 3.5
        ax.set_ylim(bottom=bottom, top=top)
        # A gridline at every power of 10 within the visible range, but only
        # the top and bottom decade get a number, so the axis doesn't get
        # crowded with labels at this figure height. Derived from the axis
        # limits rather than the raw data min/max, so a value that just
        # grazes a decade boundary (e.g. 0.0097) doesn't pull in an extra,
        # nearly-empty decade.
        lo_exp = math.ceil(math.log10(bottom))
        hi_exp = math.floor(math.log10(top))
        decades = range(lo_exp, hi_exp + 1)
        ax.set_yticks([10.0**e for e in decades])
        ax.set_yticklabels(
            [f"$10^{{{e}}}$" if e in (lo_exp, hi_exp) else "" for e in decades]
        )
    ax.grid(True, axis="y", which="major", alpha=0.3, linewidth=0.4)
    ax.set_axisbelow(True)
    # Mirror the y-axis ticks on the right spine too (no labels there).
    ax.tick_params(axis="y", which="both", right=True)

    # The bars fall off to the right, so the legend goes in the empty
    # upper-right corner instead of costing a band of figure height above it.
    legend = ax.legend(title="Flush interval (ms)", ncol=3, loc="upper right",
                       frameon=True, framealpha=0.9, fancybox=False,
                       handlelength=0.9, handleheight=0.9, handletextpad=0.3,
                       columnspacing=0.6, labelspacing=0.25, borderpad=0.3,
                       borderaxespad=0.4, title_fontsize=6)
    legend.get_frame().set_linewidth(0.4)
    legend.get_frame().set_edgecolor("0.7")

    fig.tight_layout(pad=0.2)
    return fig


def main():
    args = parse_args()
    plt.rcParams.update(PRINT_STYLE)

    writers, intervals, means, sems = load(args.csv_path)
    fig = plot(writers, intervals, means, sems, args.figsize, args.title)

    pdf_path = args.out or args.csv_path.with_name(f"{args.csv_path.stem}-bar.pdf")
    png_path = pdf_path.with_suffix(".png")
    for path, dpi in ((pdf_path, None), (png_path, 400)):
        fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.01)
    print(f"wrote {pdf_path} and {png_path}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
