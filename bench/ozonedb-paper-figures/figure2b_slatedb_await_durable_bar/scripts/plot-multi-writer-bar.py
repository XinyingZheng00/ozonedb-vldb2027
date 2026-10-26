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
library versions that produced this figure. On the first run, uv fetches them
into a throwaway environment. There is nothing to install. The Python you
already have is not affected. Get uv from https://docs.astral.sh/uv/.

A plain interpreter also works. Then you must manage the pins yourself:

    python plot-multi-writer-bar.py results/<run>.csv     # needs matplotlib, pandas, numpy

The script writes `<run>-bar.pdf` (vector, for LaTeX) and `<run>-bar.png`
(400 dpi, for slides) next to the input CSV. `--out` overrides the destination.

Input
-----
A CSV with one row per benchmark run and at least these columns:

    num_writers          the writer node count for the run
    flush_interval_ms    the SlateDB flush interval for the run
    effective_puts_per_s the throughput: the puts that survived fencing

Repeated runs of the same (num_writers, flush_interval_ms) pair are averaged.
The error bars are the standard error of those repeats.

Figure sizing
-------------
The defaults target an IEEE two-column paper. An IEEE text column is about
3.5in wide. The figure is drawn a bit narrower, at 3.0 x 1.35in. Then it can
be included at its natural size, and the fonts set here print at their true
6-7.5pt. Include it WITHOUT a width= key:

    \\includegraphics{<run>-bar.pdf}

A scale to \\columnwidth would enlarge the text past the size chosen here.
Use `--figsize W H` for a double-column or slide version instead.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

VALUE_COLUMN = "effective_puts_per_s"
REQUIRED_COLUMNS = ["num_writers", "flush_interval_ms", VALUE_COLUMN]

DEFAULT_FIGSIZE = (3.0, 1.35)  # inches; see "Figure sizing" above
BAR_GROUP_WIDTH = 0.82  # fraction of an x slot filled by one writer-count group

# Print-sized type and hairlines. All values are in points. They look right
# only if the PDF is placed at 100%. See "Figure sizing" above.
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
    # The flush interval is ordinal. So it gets a single-hue light-to-dark ramp,
    # not a categorical palette. The color carries the reading order.
    colors = plt.cm.Blues(np.linspace(0.35, 0.95, len(intervals)))

    for i, interval in enumerate(intervals):
        offset = (i - len(intervals) / 2 + 0.5) * width
        ax.bar(x + offset, means[interval].reindex(writers, fill_value=0), width,
               yerr=sems[interval].reindex(writers, fill_value=0),
               color=colors[i], edgecolor="white", linewidth=0.25,
               capsize=1, error_kw={"elinewidth": 0.5, "capthick": 0.5},
               label=f"{interval}")

    ax.set_xlabel("Number of Writer Nodes")
    ax.set_ylabel("Throughput (ops/s)")
    if title:
        ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(writers)

    # Throughput spans about 3.5 decades across the writer counts, so the y axis
    # is log. A log axis cannot show zeros. The limits are pinned to the positive
    # data. Then no decade is wasted below the smallest bar, and there is
    # headroom above the tallest bar for the legend.
    ax.set_yscale("log")
    positive = means.values[means.values > 0]
    if positive.size:
        ax.set_ylim(bottom=positive.min() / 2, top=positive.max() * 3.5)
    ax.grid(True, axis="y", alpha=0.3, linewidth=0.4)
    ax.set_axisbelow(True)

    # The bars fall off to the right. So the legend goes in the empty
    # upper-right corner. A legend above the plot would cost a band of height.
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
