# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
#   "numpy",
# ]
# ///
"""Plot YCSB throughput against the number of writers for OzoneKV, SlateDB,
CR-SQLite and Cassandra, one panel per workload, with a min-max range across
trials.

Four of the five series are measured the same way: a 100 GB dataset, 300 s
cells, and the steady-state throughput. The steady-state throughput is the
mean over the last 120 s of each writer, summed across writers. The window
matters. An OzoneKV linearizable writer spends about its first 45-90 s on the
replay of the shared log, and a whole-run average would charge that dead time
to the rate. scripts/extract_steady_throughput.py builds both TSVs.

  OzoneKV, Cassandra-serial   data/strict100/steady_all.tsv   (3 trials)
  CR-SQLite sync / async      data/crsqlite-300g/steady_all.tsv

The CR-SQLite campaign has 3 trials in 45 of its 50 cells and 2 in the other
5. The 5 cells are all at 16 writers.

SlateDB is the exception. It is still the 1 GB whole-run campaign. Its
durable multi-writer path collapses (0.00 ops/sec at 32 writers), and it was
never re-run at 100 GB. SAY SO IN THE CAPTION. The superseded 1 GB cells for
the other series are not in this folder
(results/{crsqlite,cassandra,ozonedb-durable}). The script read them through
multinode_throughputs / db_files, but it does not plot them."""

from __future__ import annotations

import re
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import LogLocator, NullFormatter

# IEEE two-column figure: 7.16 in spans the full text width. The script draws
# the figure at that size with print-size fonts. So LaTeX includes it at 1.0
# and never rescales the text.
FIG_WIDTH_IN = 7.16
FIG_HEIGHT_IN = 1.55

plt.rcParams.update(
    {
        "font.size": 7,
        "axes.titlesize": 8,
        "axes.labelsize": 7.5,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 7,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "grid.linewidth": 0.4,
        "legend.handlelength": 1.4,
        "legend.handletextpad": 0.5,
        "legend.columnspacing": 1.2,
        "pdf.fonttype": 42,
    }
)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "data"
WORKLOADS = ["a", "b", "c", "d", "f"]
WRITERS = [2, 4, 8, 16, 32]

SLATEDB_THROUGHPUT_RE = re.compile(r"^\[OVERALL\],\s*Throughput\(ops/sec\),\s*([\d.]+)")
OZONEKV_THROUGHPUT_RE = re.compile(
    r"^\[AGGREGATE\],\s*Throughput\(ops/sec\),\s*([\d.]+)"
)
CRSQLITE_SUM_RE = re.compile(
    r"^\[AGGREGATE\],\s*SumWriterThroughput\(ops/sec\),\s*([\d.]+)"
)


def parse_throughput(path: Path, pattern: re.Pattern[str]) -> float:
    for line in path.read_text().splitlines():
        m = pattern.match(line)
        if m:
            return float(m.group(1))
    raise ValueError(f"No throughput line found in {path}")


def db_files(db: str, workload: str, writers: int) -> list[Path]:
    if db == "ozonekv-durable":
        return sorted(
            (RESULTS_DIR / "ozonedb-durable").glob(
                f"1KB-300s-1000000-workload{workload}-ozonedb-corfu-durable"
                f"_w{writers}_t1_trial*.result"
            )
        )
    if db == "slatedb-durable":
        return sorted(
            (RESULTS_DIR / "slatedb-durable").glob(
                f"1KB-*s-1000000-workload{workload}-slatedb-durable"
                f"-w{writers}-t1-trial*.result"
            )
        )
    raise ValueError(db)


def multinode_throughputs(
    subdir: str, engine: str, workload: str, writers: int
) -> list[float]:
    """Return the multinode-campaign cells under RESULTS_DIR/<subdir>. engine
    is the result-file token: crsqlite-sync10ms, crsqlite-syncread,
    cassandra-serial or cassandra-quorum. Only exact writer counts match.
    A 7- or 14-writer cell never lands in the 8 or 16 slot."""
    d = RESULTS_DIR / subdir
    aggs = sorted(
        d.glob(
            f"1KB-*-1000000-workload{workload}-{engine}"
            f"_agg_multinode_w{writers}_t1_trial*.result"
        )
    )
    if aggs:
        return [parse_throughput(f, CRSQLITE_SUM_RE) for f in aggs]
    # If there is no cross-node aggregate (a laptop-side fetch hang), rebuild
    # each trial's SumWriterThroughput as the sum of the per-writer [OVERALL]
    # rates.
    by_trial: dict[str, list[float]] = {}
    for f in sorted(
        d.glob(
            f"1KB-*-1000000-workload{workload}-{engine}"
            f"_w*of{writers}_t1_trial*.result"
        )
    ):
        trial = f.name.rsplit("_trial", 1)[1].removesuffix(".result")
        by_trial.setdefault(trial, []).append(
            parse_throughput(f, SLATEDB_THROUGHPUT_RE)
        )
    sums = []
    for trial, vals in sorted(by_trial.items()):
        if len(vals) != writers:
            print(
                f"warning: {engine} workload {workload} w{writers} trial {trial}: "
                f"{len(vals)}/{writers} per-writer files, sum under-reports"
            )
        sums.append(sum(vals))
    return sums


# Steady-state campaigns (100 GB). Each series names the TSV it comes from
# and the engine label used inside it. The TSV has one row per (cell, trial),
# so a cell with 2 trials yields 2 values.
STEADY_SOURCES = {
    "ozonekv-linearizable": (
        RESULTS_DIR / "strict100" / "steady_all.tsv",
        "ozonedb-corfu-linearizable-native-lru16g-dc50g-ch64k-adm",
    ),
    "cassandra-serial": (
        RESULTS_DIR / "strict100" / "steady_all.tsv",
        "cassandra-serial",
    ),
    "crsqlite-sync10ms": (
        RESULTS_DIR / "crsqlite-300g" / "steady_all.tsv",
        "crsqlite-sync10ms",
    ),
    "crsqlite-syncread": (
        RESULTS_DIR / "crsqlite-300g" / "steady_all.tsv",
        "crsqlite-syncread",
    ),
}

_STEADY_CACHE: dict[Path, dict[tuple[str, str, int], list[float]]] = {}


def _load_steady(tsv: Path) -> dict[tuple[str, str, int], list[float]]:
    if tsv in _STEADY_CACHE:
        return _STEADY_CACHE[tsv]
    out: dict[tuple[str, str, int], list[float]] = {}
    if tsv.exists():
        for line in tsv.read_text().splitlines()[1:]:
            label, workload, writers, have, steady, _failed, _run = line.split("\t")
            if int(have) != int(writers):
                print(
                    f"warning: {tsv.name} {label} workload {workload} "
                    f"w{writers}: {have}/{writers} writers, sum under-reports"
                )
            out.setdefault((label, workload, int(writers)), []).append(float(steady))
    _STEADY_CACHE[tsv] = out
    return out


def db_throughputs(db: str, workload: str, writers: int) -> list[float]:
    if db in STEADY_SOURCES:
        tsv, label = STEADY_SOURCES[db]
        return _load_steady(tsv).get((label, workload, writers), [])
    # Superseded 1 GB campaigns, kept reachable but not plotted.
    if db.startswith("crsqlite"):
        return multinode_throughputs("crsqlite", db, workload, writers)
    if db.startswith("cassandra"):
        return multinode_throughputs("cassandra", db, workload, writers)
    pattern = (
        OZONEKV_THROUGHPUT_RE if db.startswith("ozonekv") else SLATEDB_THROUGHPUT_RE
    )
    return [parse_throughput(f, pattern) for f in db_files(db, workload, writers)]


def summarize(values: list[float]) -> tuple[float, float, float]:
    """Return the mean, then the distance down to the minimum and up to the
    maximum.

    The bars carry a range, not a standard deviation. 5 of the 50 CR-SQLite
    cells have only 2 trials, and a standard deviation says nothing there. One
    error-bar meaning across the whole figure is worth more than a tighter
    interval on the cells that have 3. State "min-max across trials" in the
    caption."""
    if not values:
        return float("nan"), 0.0, 0.0
    avg = mean(values)
    return avg, avg - min(values), max(values) - avg


DBS = [
    ("ozonekv-linearizable", "#1f77b4"),
    ("slatedb-durable", "#2ca02c"),
    ("crsqlite-sync10ms", "#ff7f0e"),
    ("crsqlite-syncread", "#d62728"),
    ("cassandra-serial", "#17becf"),
]

DB_LABELS = {
    "ozonekv-linearizable": "OzoneKV",
    "slatedb-durable": "SlateDB (durable)",
    "crsqlite-sync10ms": "CR-SQLite-async-10ms",
    "crsqlite-syncread": "CR-SQLite-sync",
    "cassandra-serial": "Cassandra-serial",
}


def main() -> None:
    fig, axes = plt.subplots(
        1, 5, figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN), sharey=True
    )
    flat_axes = axes.flatten()

    x = np.arange(len(WRITERS))
    width = 0.8 / len(DBS)

    for ax, w in zip(flat_axes, WORKLOADS):
        for i, (db, color) in enumerate(DBS):
            means, lo, hi = [], [], []
            for nw in WRITERS:
                vals = db_throughputs(db, w, nw)
                m, dn, up = summarize(vals)
                means.append(m)
                lo.append(dn)
                hi.append(up)
            offset = (i - (len(DBS) - 1) / 2) * width
            ax.bar(
                x + offset,
                means,
                width,
                yerr=[lo, hi],
                capsize=1.2,
                error_kw={"elinewidth": 0.5, "capthick": 0.5},
                label=DB_LABELS[db],
                color=color,
                edgecolor="black",
                linewidth=0.3,
            )

        ax.set_yscale("log")
        # Aim for about five labels. A fixed decade stride is wrong here. The
        # axis is short, so a stride locator drops to two labels. A label per
        # decade crowds nine decades at print size.
        ax.yaxis.set_major_locator(LogLocator(base=10.0, numticks=6))
        ax.yaxis.set_minor_locator(LogLocator(base=10.0))
        # A log axis labels its minor ticks when it thinks there is room. With
        # a decade-spaced minor locator, that puts a label on every decade and
        # defeats the major locator above.
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.tick_params(axis="y", which="minor", length=1.2)
        ax.set_xticks(x)
        ax.set_xticklabels([str(nw) for nw in WRITERS])
        ax.set_xlabel("Writers")
        ax.set_title(f"Workload {w.upper()}")
        ax.grid(axis="y", which="major", linestyle="--", alpha=0.5)

    axes[0].set_ylabel("Throughput (ops/sec)")
    handles, labels = flat_axes[0].get_legend_handles_labels()
    fig.tight_layout(rect=[0, 0.17, 1, 1], pad=0.3, w_pad=0.5)
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=len(DBS),
        frameon=False,
        bbox_to_anchor=(0.5, 0.0),
    )

    out_path = Path(__file__).resolve().parent.parent / "multi_throughput.pdf"
    fig.savefig(out_path)
    print(f"Saved plot to {out_path}\n")

    for db, _ in DBS:
        for w in WORKLOADS:
            for nw in WRITERS:
                vals = db_throughputs(db, w, nw)
                if not vals:
                    continue
                m, dn, up = summarize(vals)
                print(
                    f"{db:22s} workload {w} w{nw:<2d}: "
                    f"{m:10.1f}  [{m - dn:10.1f}, {m + up:10.1f}]  "
                    f"(n={len(vals)})"
                )


if __name__ == "__main__":
    main()
