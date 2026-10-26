# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
#   "numpy",
# ]
# ///
"""Plot average single-writer YCSB throughput for OzoneKV and SlateDB.

Four systems: OzoneKV-async, OzoneKV-sync, SlateDB-async, SlateDB-sync.
A "sync" run acks a write only after the write is durable in the shared
log. An "async" run acks earlier. OzoneKV's default mode is sync. SlateDB's
default mode is async.

The result directories predate this naming. "<system>-durable" holds the
sync runs, and the plain directory holds the async runs.

The expected data layout under the results directory is below. There is
one .result file per trial. The error bars are the stdev across trials.

    ozonedb/1KB-300s-1000000-workload{w}-ozonedb-corfu_w1_t1_trial{n}.result
    ozonedb-durable/1KB-300s-1000000-workload{w}-ozonedb-corfu-durable_w1_t1_trial{n}.result
    slatedb/1KB-{s}s-1000000-workload{w}-slatedb-w1-t1-trial{n}.result
    slatedb-durable/1KB-{s}s-1000000-workload{w}-slatedb-durable-w1-t1-trial{n}.result

The SlateDB names carry the run duration ({s}, 300 in the paper); any duration
matches, as in figure 10's plot script.

The two OzoneKV directories also accept the aggregate files that the
current runner (run_multinode_ycsb_with_corfu.sh) writes, so a rerun needs
no renaming: the sync run's files go into ozonedb-durable/, the
`--write-mode async` run's files into ozonedb/.

    ozonedb-durable/1KB-*-workload{w}-ozonedb-corfu-native_agg_multinode_w1_t1_trial{n}.result
    ozonedb/1KB-*-workload{w}-ozonedb-corfu-async-native_agg_multinode_w1_t1_trial{n}.result

OzoneKV files report the throughput on an "[AGGREGATE], Throughput(ops/sec), N"
line. SlateDB files report it on an "[OVERALL]" line. A system with no
result files is skipped, and a workload with none leaves an empty bar, each
with a warning, so a partial rerun still plots.

Usage:
    uv run plot_throughput.py [--results-dir DIR] [--output FILE.pdf]

The results directory defaults to "data" (or "results") next to this
script. If that directory does not exist, it defaults to one level up.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np

WORKLOADS = ["a", "b", "c", "d", "f"]


@dataclass(frozen=True)
class System:
    label: str
    subdir: str
    file_globs: tuple[str, ...]  # {w} is the workload letter; any pattern may match
    throughput_tag: str  # line tag that carries Throughput(ops/sec)
    color: str


# The current runner's aggregate file for one writer. The engine token is
# ozonedb-corfu[-async]-native; `_agg_w1_` is the single-host aggregate and
# `_agg_multinode_w1_` the orchestrator's.
_OZ_SYNC_AGG = "1KB-*-workload{w}-ozonedb-corfu-native_agg*w1_t1_trial*.result"
_OZ_ASYNC_AGG = "1KB-*-workload{w}-ozonedb-corfu-async-native_agg*w1_t1_trial*.result"

SYSTEMS = [
    System(
        "OzoneKV-async",
        "ozonedb",
        ("1KB-300s-1000000-workload{w}-ozonedb-corfu_w1_t1_trial*.result", _OZ_ASYNC_AGG),
        "[AGGREGATE]",
        "#1f77b4",
    ),
    System(
        "OzoneKV-sync",
        "ozonedb-durable",
        ("1KB-300s-1000000-workload{w}-ozonedb-corfu-durable_w1_t1_trial*.result", _OZ_SYNC_AGG),
        "[AGGREGATE]",
        "#9467bd",
    ),
    System(
        "SlateDB-async",
        "slatedb",
        ("1KB-*s-1000000-workload{w}-slatedb-w1-t1-trial*.result",),
        "[OVERALL]",
        "#ff7f0e",
    ),
    System(
        "SlateDB-sync",
        "slatedb-durable",
        ("1KB-*s-1000000-workload{w}-slatedb-durable-w1-t1-trial*.result",),
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
    patterns = [g.format(w=workload) for g in system.file_globs]
    files = sorted(
        {f for p in patterns for f in (results_dir / system.subdir).glob(p)}
    )
    if not files:
        print(
            f"warning: no result files for {system.label} workload {workload} under "
            f"{results_dir / system.subdir} ({' | '.join(patterns)}); bar left empty"
        )
        return []
    return [parse_throughput(f, system.throughput_tag) for f in files]


def has_results(system: System, results_dir: Path) -> bool:
    return any(
        True
        for g in system.file_globs
        for w in WORKLOADS
        for _ in (results_dir / system.subdir).glob(g.format(w=w))
    )


def summarize(values: list[float]) -> tuple[float, float]:
    if not values:
        return float("nan"), 0.0
    return mean(values), stdev(values) if len(values) >= 2 else 0.0


def default_results_dir() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "data", here.parent / "data", here / "results", here.parent / "results"):
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

    systems = [s for s in SYSTEMS if has_results(s, args.results_dir)]
    for s in SYSTEMS:
        if s not in systems:
            print(f"warning: no result files for {s.label} under {args.results_dir / s.subdir}; skipped")
    if not systems:
        raise SystemExit(f"no result files for any system under {args.results_dir}")

    for i, system in enumerate(systems):
        stats = [
            summarize(throughputs(system, w, args.results_dir)) for w in WORKLOADS
        ]
        offset = (i - (len(systems) - 1) / 2) * width
        ax.bar(
            x + offset,
            [m for m, _ in stats],
            width,
            yerr=[e for _, e in stats],
            capsize=4,
            label=system.label,
            color=system.color,
            edgecolor="black",
        )

    ax.set_xticks(x)
    ax.set_xticklabels([w.upper() for w in WORKLOADS])
    ax.set_ylabel("Throughput (ops/sec)")
    ax.legend(
        loc="upper left",
        ncol=2,
        columnspacing=0.8,
        handlelength=1.2,
        handletextpad=0.4,
        borderaxespad=0.2,
        framealpha=0.9,
    )
    ax.margins(y=0.12)
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()

    fig.savefig(args.output)
    print(f"Saved plot to {args.output}\n")

    for system in systems:
        for w in WORKLOADS:
            vals = throughputs(system, w, args.results_dir)
            m, e = summarize(vals)
            print(f"{system.label:14s} workload {w}: {m:8.2f} ± {e:6.2f}  (n={len(vals)})")


if __name__ == "__main__":
    main()
