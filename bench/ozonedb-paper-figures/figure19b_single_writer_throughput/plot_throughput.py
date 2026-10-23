# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
#   "numpy",
# ]
# ///
"""Plot average single-writer YCSB throughput for OzoneKV and SlateDB.

Four systems: OzoneKV-async, OzoneKV-sync, SlateDB-async, SlateDB-sync.
A "sync" run acks a write only after it is durable in the shared log; an
"async" run acks earlier. OzoneKV's default mode is sync. SlateDB's
default mode is async.

The result directories predate this naming: "<system>-durable" holds the
sync runs, and the plain directory holds the async runs.

Expected data layout under the results directory (one .result file per
trial; error bars are the stdev across trials):

    ozonedb/1KB-300s-1000000-workload{w}-ozonedb-corfu_w1_t1_trial{n}.result
    ozonedb-durable/1KB-300s-1000000-workload{w}-ozonedb-corfu-durable_w1_t1_trial{n}.result
    slatedb/1KB-300s-1000000-workload{w}-slatedb-w1-t1-trial{n}.result
    slatedb-durable/1KB-300s-1000000-workload{w}-slatedb-durable-w1-t1-trial{n}.result

OzoneKV files report throughput on an "[AGGREGATE], Throughput(ops/sec), N"
line; SlateDB files report it on an "[OVERALL]" line.

Usage:
    uv run plot_throughput.py [--results-dir DIR] [--output FILE.pdf]

The results directory defaults to "results" next to this script, else
"../results".
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

WORKLOADS = ["a", "b", "c", "d", "f"]


@dataclass(frozen=True)
class System:
    label: str
    subdir: str
    file_glob: str  # {w} is the workload letter
    throughput_tag: str  # line tag that carries Throughput(ops/sec)
    color: str


SYSTEMS = [
    System(
        "OzoneKV-async",
        "ozonedb",
        "1KB-300s-1000000-workload{w}-ozonedb-corfu_w1_t1_trial*.result",
        "[AGGREGATE]",
        "#1f77b4",
    ),
    System(
        "OzoneKV-sync",
        "ozonedb-durable",
        "1KB-300s-1000000-workload{w}-ozonedb-corfu-durable_w1_t1_trial*.result",
        "[AGGREGATE]",
        "#9467bd",
    ),
    System(
        "SlateDB-async",
        "slatedb",
        "1KB-300s-1000000-workload{w}-slatedb-w1-t1-trial*.result",
        "[OVERALL]",
        "#ff7f0e",
    ),
    System(
        "SlateDB-sync",
        "slatedb-durable",
        "1KB-300s-1000000-workload{w}-slatedb-durable-w1-t1-trial*.result",
        "[OVERALL]",
        "#2ca02c",
    ),
]


def parse_throughput(path: Path, tag: str) -> float:
    for line in path.read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0] == tag and parts[1] == "Throughput(ops/sec)":
            return float(parts[2])
    raise ValueError(f"No '{tag}, Throughput(ops/sec)' line in {path}")


def throughputs(system: System, workload: str, results_dir: Path) -> list[float]:
    pattern = system.file_glob.format(w=workload)
    files = sorted((results_dir / system.subdir).glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No result files match {results_dir / system.subdir / pattern}"
        )
    return [parse_throughput(f, system.throughput_tag) for f in files]


def summarize(values: list[float]) -> tuple[float, float]:
    return mean(values), stdev(values) if len(values) >= 2 else 0.0


def default_results_dir() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "results", here.parent / "results"):
        if candidate.is_dir():
            return candidate
    return here / "results"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", type=Path, default=default_results_dir())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "single_throughput.pdf",
    )
    args = parser.parse_args()

    plt.rcParams.update(
        {
            "font.size": 14,
            "axes.titlesize": 16,
            "axes.labelsize": 15,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": 13,
        }
    )

    x = np.arange(len(WORKLOADS))
    width = 0.2

    fig, ax = plt.subplots(figsize=(8, 3))

    for i, system in enumerate(SYSTEMS):
        stats = [
            summarize(throughputs(system, w, args.results_dir)) for w in WORKLOADS
        ]
        offset = (i - (len(SYSTEMS) - 1) / 2) * width
        ax.bar(
            x + offset,
            [m for m, _ in stats],
            width,
            yerr=[e for _, e in stats],
            capsize=4,
            label=system.label,
            color=system.color,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([w.upper() for w in WORKLOADS])
    ax.set_ylabel("Throughput (ops/sec)")
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _: f"{v/1000:g}k" if v else "0")
    )
    legend = ax.legend(
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        handlelength=1.2,
        handletextpad=0.5,
        borderaxespad=0,
    )
    ax.margins(y=0.12)
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    # Fit the axes to the figure first, then shrink the right edge just
    # enough to fit the legend, so the plot fills the rest of the figure.
    fig.tight_layout()
    fig.canvas.draw()
    legend_width = legend.get_window_extent().width / fig.get_window_extent().width
    fig.subplots_adjust(right=1 - legend_width - 0.02)

    fig.savefig(args.output)
    print(f"Saved plot to {args.output}\n")

    for system in SYSTEMS:
        for w in WORKLOADS:
            vals = throughputs(system, w, args.results_dir)
            m, e = summarize(vals)
            print(f"{system.label:14s} workload {w}: {m:8.2f} ± {e:6.2f}  (n={len(vals)})")


if __name__ == "__main__":
    main()
