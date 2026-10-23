# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
# ]
# ///
"""Plot SQLite trunk (WAL) vs BEGIN CONCURRENT+WAL2 vs HCTree write throughput
against the number of concurrent writer processes, for a YCSB workload.

Run with `uv run plot_sqlite_workload.py d` (uv installs matplotlib per the
header above), or with any Python 3.11+ that already has matplotlib:
`python3 plot_sqlite_workload.py d`.

    plot_sqlite_workload.py d              # one workload -> sqlite_workloadd.pdf
    plot_sqlite_workload.py all            # every workload
    plot_sqlite_workload.py d --csv path/to/results.csv --outdir figs/

Input is a CSV with one row per (system, workload, thread count); the columns
read here are:

    system            one of the keys in SYSTEMS below
    workload          YCSB workload letter, lowercase
    threads           number of concurrent writer processes
    throughput_mean   ops/sec, averaged over repeats
    throughput_stddev ops/sec; blank is treated as zero (drawn as no error bar)

Other columns and other systems in the file are ignored.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent

# Looked up in order, so the script works both inside the source tree and when
# it is shipped next to a copy of results.csv.
DEFAULT_CSV_CANDIDATES = (
    HERE / "results.csv",
    HERE.parent / "results" / "fig2-multi-writer-local" / "results.csv",
)

WORKLOADS = ["a", "b", "c", "d", "f"]

# (CSV system key, legend label, line color, marker)
SYSTEMS = [
    ("trunkcpp", "trunk (WAL)", "#1f77b4", "o"),
    ("bcw2", "BEGIN CONCURRENT + WAL2", "#ff7f0e", "s"),
    ("hctree", "HCTree", "#2ca02c", "^"),
]

# Sized to drop straight into one column of an IEEE two-column paper at 100%,
# i.e. \includegraphics[width=\columnwidth]{...} with no rescaling -- rescaling
# in LaTeX would throw off every font size set below.
FIG_SIZE = (3.4, 0.9)

plt.rcParams.update(
    {
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 6.5,
        "lines.linewidth": 1.2,
        "axes.linewidth": 0.6,
        "grid.linewidth": 0.4,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.01,
        # TrueType rather than Type 3, which some conference PDF checks reject.
        "pdf.fonttype": 42,
    }
)


def default_csv() -> Path:
    for candidate in DEFAULT_CSV_CANDIDATES:
        if candidate.is_file():
            return candidate
    searched = "\n  ".join(str(c) for c in DEFAULT_CSV_CANDIDATES)
    raise SystemExit(f"No results.csv found. Looked in:\n  {searched}\nPass --csv.")


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"No such CSV: {path}")
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def series_for(
    rows: list[dict[str, str]], system: str, workload: str
) -> list[tuple[int, float, float]]:
    """Return (threads, mean, stddev) for one system+workload, sorted by threads."""
    points = []
    for r in rows:
        if r["system"] != system or r["workload"] != workload:
            continue
        stddev = r.get("throughput_stddev") or ""
        points.append(
            (
                int(r["threads"]),
                float(r["throughput_mean"]),
                float(stddev) if stddev else 0.0,
            )
        )
    points.sort(key=lambda p: p[0])
    return points


def plot_workload(rows: list[dict[str, str]], workload: str, outdir: Path) -> Path:
    fig, ax = plt.subplots(figsize=FIG_SIZE)

    all_threads: set[int] = set()
    all_means: list[float] = []
    for system, label, color, marker in SYSTEMS:
        pts = series_for(rows, system, workload)
        if not pts:
            continue
        all_threads.update(p[0] for p in pts)
        all_means.extend(p[1] for p in pts)
        ax.errorbar(
            [p[0] for p in pts],
            [p[1] for p in pts],
            yerr=[p[2] for p in pts],
            label=label,
            color=color,
            marker=marker,
            markersize=3.5,
            linewidth=1.2,
            capsize=2,
            elinewidth=0.8,
        )

    if not all_threads:
        raise SystemExit(f"No rows for workload {workload!r} in the CSV.")

    ticks = sorted(all_threads)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    # A tick (and gridline) at every power of 10 spanned by the data, but
    # only the top and bottom decade get a number, so the axis doesn't get
    # crowded with labels at this figure height.
    lo_exp = math.floor(math.log10(min(all_means)))
    hi_exp = math.ceil(math.log10(max(all_means)))
    decades = range(lo_exp, hi_exp + 1)
    ax.set_yticks([10.0**e for e in decades])
    ax.set_yticklabels(
        [f"$10^{{{e}}}$" if e in (lo_exp, hi_exp) else "" for e in decades]
    )
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(t) for t in ticks])
    ax.set_xlabel("Number of Writer Processes", labelpad=2)
    ax.set_ylabel("Throughput\n(ops/s)", labelpad=2)
    ax.grid(which="major", linestyle="--", alpha=0.5)
    ax.grid(which="minor", linestyle=":", alpha=0.3)
    ax.tick_params(pad=1.5)
    # No title: the LaTeX caption names the figure, and at this height a title
    # would cost most of the plotting area.
    ax.legend(
        loc="center right",
        frameon=False,
        handlelength=1.6,
        handletextpad=0.4,
        borderpad=0.2,
        labelspacing=0.25,
    )

    fig.tight_layout(pad=0.2)
    out_path = outdir / f"sqlite_workload{workload}.pdf"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "workloads",
        nargs="*",
        default=["a"],
        help=(
            "YCSB workloads to plot (e.g. a b c d f). "
            "Pass 'all' to render every workload. Default: a."
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Input CSV (default: results.csv beside this script, or the "
        "in-tree results/fig2-multi-writer-local/results.csv).",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=HERE,
        help="Directory for the output PDFs (default: beside this script).",
    )
    args = parser.parse_args()

    requested = WORKLOADS if args.workloads == ["all"] else args.workloads
    unknown = [w for w in requested if w.lower() not in WORKLOADS]
    if unknown:
        raise SystemExit(f"Unknown workload(s) {unknown}; expected one of {WORKLOADS}")

    rows = load_csv(args.csv if args.csv else default_csv())
    args.outdir.mkdir(parents=True, exist_ok=True)
    for w in requested:
        print(f"Saved plot to {plot_workload(rows, w.lower(), args.outdir)}")


if __name__ == "__main__":
    main()
