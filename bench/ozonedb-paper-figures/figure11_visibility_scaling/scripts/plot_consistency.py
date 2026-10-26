#!/usr/bin/env python3
"""Plots for the consistency experiments.

staleness    CDF of read staleness, one curve per probe run. The label is
             the sync interval. Input: bench.py probe-staleness output dirs.
lost-updates grouped bars of the final counter against the increments
             issued. Input: bench.py check-lost-updates output dirs
             (control and crsqlite).
visibility   left: CDF of insert-visibility latency. Its complement is the
             PBS P[stale at age a] curve. Right: first-read miss fraction.
             Input: check-visibility output dirs of either engine.
table        print the [CONSISTENCY] lines of every aggregate in a results
             dir as one CSV (divergence at run end, convergence time).

python3 plot_consistency.py staleness bench/results/consistency/staleness-*
python3 plot_consistency.py lost-updates bench/results/consistency/lost-updates-*
python3 plot_consistency.py visibility bench/results/consistency/visibility-*
python3 plot_consistency.py table bench/results/local/<run_tag>
"""

import argparse
import bisect
import csv
import json
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def _load_summary(d):
    with open(os.path.join(d, "summary.json")) as f:
        return json.load(f)


def _staleness_label(s):
    # ozonedb's consistency.py stamps an "engine" field. The crsqlite dirs do not.
    if s.get("engine"):
        return f"{s['engine']} ({s['stale_read_fraction']:.0%} stale)"
    return (
        f"cr-sqlite, sync {s['sync_interval_ms']:g} ms "
        f"({s['stale_read_fraction']:.0%} stale)"
    )


def _lost_updates_label(s):
    if s["mode"].startswith("control"):
        return "single SQLite\n(control)"
    if s.get("engine"):
        return f"{s['engine']}\n{s['replicas']} writers"
    return f"cr-sqlite\n{s['replicas']} replicas, sync {s['sync_interval_ms']:g} ms"


def plot_staleness(dirs, out_name):
    fig, ax = plt.subplots(figsize=(5, 3.2))
    for d in sorted(dirs):
        s = _load_summary(d)
        commits = []
        with open(os.path.join(d, "commits.csv")) as f:
            for row in csv.DictReader(f):
                commits.append((int(row["seq"]), int(row["t_commit_ns"])))
        commit_times = [t for _, t in commits]
        staleness_ms = []
        with open(os.path.join(d, "reads.csv")) as f:
            for row in csv.DictReader(f):
                t_read, seen = int(row["t_read_ns"]), int(row["seq_seen"])
                latest = bisect.bisect_right(commit_times, t_read) - 1
                if latest > seen:
                    staleness_ms.append((t_read - commit_times[seen + 1]) / 1e6)
                else:
                    staleness_ms.append(0.0)
        staleness_ms.sort()
        n = len(staleness_ms)
        ys = [(i + 1) / n for i in range(n)]
        ax.plot(staleness_ms, ys, label=_staleness_label(s))
    ax.set_xlabel("read staleness (ms)")
    ax.set_ylabel("CDF")
    ax.set_ylim(0, 1)
    ax.legend(fontsize=8)
    ax.set_title("Staleness of reads on a remote replica")
    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    fig.savefig(out, dpi=300)
    print(f"wrote {out}")


def plot_lost_updates(dirs, out_name):
    labels, finals, issued = [], [], []
    for d in sorted(dirs):
        s = _load_summary(d)
        labels.append(_lost_updates_label(s))
        finals.append(s["final_counter"])
        issued.append(s["increments_issued"])

    fig, ax = plt.subplots(figsize=(5, 3.2))
    xs = range(len(labels))
    ax.bar(xs, issued, color="#cccccc", label="increments issued")
    ax.bar(xs, finals, color="#1f77b4", label="final counter")
    for x, (f, k) in enumerate(zip(finals, issued)):
        lost = k - f
        ax.text(
            x,
            f,
            f"{lost} lost\n({lost / k:.0%})" if lost else "0 lost",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("counter value")
    ax.legend(fontsize=8)
    ax.set_title("Lost updates under concurrent read-modify-write")
    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    fig.savefig(out, dpi=300)
    print(f"wrote {out}")


def _visibility_label(s):
    eng = s.get("engine", "cr-sqlite")
    if eng == "cr-sqlite":
        eng = f"cr-sqlite, sync {s['sync_interval_ms']:g} ms"
    return eng


def plot_visibility(dirs, out_name):
    fig, (ax_cdf, ax_miss) = plt.subplots(1, 2, figsize=(8.5, 3.2))

    labels, fracs, counts = [], [], []
    for d in sorted(dirs):
        s = _load_summary(d)
        vis_ms = []
        timeouts = 0
        with open(os.path.join(d, "visibility.csv")) as f:
            for row in csv.DictReader(f):
                t_found = int(row["t_found_ns"])
                if t_found < 0:
                    timeouts += 1
                else:
                    vis_ms.append((t_found - int(row["t_ack_ns"])) / 1e6)
        vis_ms.sort()
        # Timeouts sit above every bucket, so the CDF tops out below 1. That
        # is exactly what "never became visible within the timeout" means.
        n = len(vis_ms) + timeouts
        ys = [(i + 1) / n for i in range(len(vis_ms))]
        label = f"{_visibility_label(s)} ({s['miss_first_read_fraction']:.1%} missed)"
        ax_cdf.plot(vis_ms, ys, label=label)

        labels.append(_visibility_label(s).replace(", ", "\n"))
        fracs.append(s["miss_first_read_fraction"])
        counts.append((s["misses_first_read"], s["checked"]))

    ax_cdf.set_xscale("log")
    ax_cdf.set_xlabel("time from write ack to first successful read (ms)")
    ax_cdf.set_ylabel("CDF")
    ax_cdf.set_ylim(0, 1)
    ax_cdf.legend(fontsize=7)
    ax_cdf.set_title(
        "Insert visibility latency\n(1 - CDF = PBS P[stale at age a])", fontsize=9
    )

    xs = range(len(labels))
    ax_miss.bar(xs, fracs, color="#d62728")
    for x, (m, c) in zip(xs, counts):
        ax_miss.text(x, fracs[x], f"{m}/{c}", ha="center", va="bottom", fontsize=8)
    ax_miss.set_xticks(list(xs))
    ax_miss.set_xticklabels(labels, fontsize=7)
    ax_miss.set_ylabel("first-read miss fraction")
    ax_miss.set_ylim(0, max(0.05, max(fracs) * 1.25) if fracs else 1)
    ax_miss.set_title(
        "Acked key not found on first read\n(read-latest violations)", fontsize=9
    )

    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    fig.savefig(out, dpi=300)
    print(f"wrote {out}")


def _pub_label(label):
    # Map the _visibility_label output to the publication names: OzoneKV,
    # CR-SQLite-sync (fenced read path) and CR-SQLite-async-<interval>ms.
    if label.startswith("ozonedb"):
        return "OzoneKV"
    if "syncread" in label:
        return "CR-SQLite-sync"
    m = re.search(r"sync ([0-9.]+)", label)
    if m:
        return f"CR-SQLite-async-{float(m.group(1)):g}ms"
    return label


def _scaling_sort_key(label):
    # Sort OzoneKV first, then the fenced CR-SQLite-sync, then async by
    # ascending interval. A string sort would order 10 < 200 < 50.
    if label.startswith("OzoneKV"):
        return (0, 0.0)
    m = re.search(r"async-([0-9.]+)ms", label)
    if m:
        return (2, float(m.group(1)))
    return (1, 0.0)


def plot_visibility_scaling(dirs, out_name, layout="grid", legend_pos="bottom"):
    """CDF-only insert-visibility figure, one facet per writer count.

    The figure shows the scaling claim in one picture. The ozonedb writers
    all share one log, and the reader's fence cost is independent of the
    writer count. So the ozonedb curve stays pinned at apply-lag
    milliseconds across the facets. Each cr-sqlite reader must merge the
    changes of every writer replica. So its curves sit at about interval/2
    and shift right as the writer count grows. Timeouts keep a curve below
    1.0 (the never-visible mass), as in plot_visibility.
    """
    groups = {}  # writer count -> [(label, sorted vis_ms, total keys)]
    labels_seen = set()
    any_xnode = False
    for d in sorted(dirs):
        s = _load_summary(d)
        w = int(s.get("writers", 1))
        # Cross-node runs must reference the reader-side notify receipt.
        # t_ack is on the writer host's clock, and t_found is on the
        # reader's clock. The two CLOCK_MONOTONIC domains are incomparable.
        # The difference can even be negative. The summary analysis uses
        # the same convention.
        xnode = bool(s.get("cross_node"))
        any_xnode = any_xnode or xnode
        ref_col = "t_notify_ns" if xnode else "t_ack_ns"
        vis_ms, timeouts = [], 0
        with open(os.path.join(d, "visibility.csv")) as f:
            for row in csv.DictReader(f):
                t_found = int(row["t_found_ns"])
                if t_found < 0:
                    timeouts += 1
                else:
                    vis_ms.append((t_found - int(row[ref_col])) / 1e6)
        vis_ms.sort()
        # The topology is uniform across a scaling figure, and the -xnode
        # engine suffix folds into the caption. So the legend carries the
        # publication engine names only.
        label = _pub_label(_visibility_label(s))
        labels_seen.add(label)
        groups.setdefault(w, []).append((label, vis_ms, len(vis_ms) + timeouts))

    ordered_labels = sorted(labels_seen, key=_scaling_sort_key)
    colors = {lab: f"C{i}" for i, lab in enumerate(ordered_labels)}
    ws = sorted(groups)
    ncols = len(ws) if layout == "row" else min(2, len(ws))
    nrows = (len(ws) + ncols - 1) // ncols
    # The figure is rendered 1:1, so the point sizes below are the printed
    # sizes. grid: 3.5 in total = one IEEE column. row: 7 in = the full
    # text width (a figure* strip). That is the narrowest width at which a
    # 1x4 log-axis row stays legible.
    facet_w, facet_h = (1.75, 1.7) if layout == "row" else (1.75, 1.2)
    if legend_pos == "right":
        # The right-hand legend frees the bottom band, so the facets can
        # be shorter. The width cost comes out of the facets via rect, so
        # the grid gets a little extra width back.
        facet_h = 1.35 if layout == "row" else 1.05
        if layout != "row":
            facet_w = 1.9
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(facet_w * ncols, facet_h * nrows),
        sharex=True,
        sharey=True,
        squeeze=False,
    )
    for ax, w in zip(axes.flat, ws):
        for label, vis, n in sorted(groups[w], key=lambda t: _scaling_sort_key(t[0])):
            ys = [(i + 1) / n for i in range(len(vis))]
            ax.plot(vis, ys, label=label, color=colors[label])
        ax.set_xscale("log")
        # Narrow one-column facets prune the auto ticks down to two.
        # Pin one tick per decade and the 0/0.5/1 CDF gridlines.
        ax.set_xticks([1, 10, 100, 1000])
        ax.set_yticks([0, 0.5, 1])
        ax.set_title(f"{w} writers", fontsize=9, pad=2)
        ax.set_ylim(0, 1.02)
        ax.tick_params(labelsize=8)
        if legend_pos == "right":
            # The legend column narrows the facets, and 8 pt decade labels
            # collide at that width. The sub-0.4 ms region is empty in
            # every run, so trade it for tick spacing.
            ax.tick_params(axis="x", labelsize=7)
            ax.set_xlim(0.4, 1500)
        ax.grid(alpha=0.25)
    for ax in list(axes.flat)[len(ws) :]:
        ax.set_visible(False)
    # Cross-node runs still reference the reader-side notify receipt (see
    # the ref_col comment above). The label says "ack", and the caption
    # carries the clock-domain detail.
    xlabel = "time from ack to first successful read (ms)"
    for row in axes:
        row[0].set_ylabel("CDF", fontsize=9)
    from matplotlib.lines import Line2D

    handles = [Line2D([0], [0], color=colors[lab], label=lab) for lab in ordered_labels]
    fig_h = facet_h * nrows
    fig_w = facet_w * ncols
    if legend_pos == "right":
        # One top-down column at the right edge. The bottom band then
        # holds only the x label. That is what buys the height back.
        legend_w_in, xlabel_in = 1.45, 0.24
        axes_right = 1 - legend_w_in / fig_w
        fig.legend(
            handles=handles,
            loc="center right",
            bbox_to_anchor=(1.0, 0.5 + xlabel_in / fig_h / 2),
            ncol=1,
            fontsize=8,
            frameon=False,
            handlelength=1.2,
            handletextpad=0.4,
            labelspacing=0.4,
            borderpad=0.1,
            borderaxespad=0.1,
        )
        # The label is wider than the facet region. Center it on the full
        # figure width, and let it run under the legend column.
        fig.text(0.5, 0.015, xlabel, ha="center", va="bottom", fontsize=9)
        fig.tight_layout(
            pad=0.4, h_pad=0.7, rect=(0, xlabel_in / fig_h, axes_right, 1)
        )
    else:
        # One figure-level x-label above the legend. At publication font
        # sizes, the per-facet label no longer fits inside a facet's width.
        legend_in, xlabel_in = (0.24, 0.16) if layout == "row" else (0.56, 0.13)
        fig.legend(
            handles=handles,
            loc="lower center",
            ncol=len(ordered_labels) if layout == "row" else min(2, len(ordered_labels)),
            fontsize=8,
            frameon=False,
            handlelength=1.2,
            handletextpad=0.4,
            columnspacing=1.0,
            labelspacing=0.25,
            borderpad=0.1,
        )
        fig.text(0.5, legend_in / fig_h, xlabel, ha="center", va="bottom", fontsize=9)
        fig.tight_layout(
            pad=0.4, h_pad=0.7, rect=(0, (legend_in + xlabel_in) / fig_h, 1, 1)
        )
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    fig.savefig(out, dpi=300)
    print(f"wrote {out}")


def plot_visibility_sweep(dirs, out_name):
    """Left: ozonedb first-read miss fraction against the achieved insert
    rate (default against linearizable_reads). The strict line must sit at
    exactly 0 at every rate. Right: cr-sqlite visibility p50 against the
    sync interval (log-log). It tracks interval/2, that is, the staleness
    is structural."""
    oz = {True: [], False: []}
    cr = []
    for d in dirs:
        s = _load_summary(d)
        rate = s["checked"] / s["duration_s"] if s.get("duration_s") else 0
        if s.get("engine", "").startswith("cr-sqlite"):
            cr.append(
                (
                    s["sync_interval_ms"],
                    s["visibility_ms_p50"],
                    s["miss_first_read_fraction"],
                )
            )
        else:
            oz[bool(s.get("linearizable_reads"))].append(
                (
                    rate,
                    s["miss_first_read_fraction"],
                    s["misses_first_read"],
                    s["checked"],
                )
            )

    fig, (ax_oz, ax_cr) = plt.subplots(1, 2, figsize=(8.5, 3.2))

    for lin, marker, color in [(False, "o", "#1f77b4"), (True, "s", "#2ca02c")]:
        pts = sorted(oz[lin])
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        label = "linearizable_reads (all 0)" if lin else "default"
        ax_oz.plot(xs, ys, marker=marker, color=color, label=label)
        for x, y, m, c in pts:
            ax_oz.annotate(
                f"{m}/{c}",
                (x, y),
                textcoords="offset points",
                xytext=(0, 5),
                ha="center",
                fontsize=6,
            )
    ax_oz.set_xscale("log")
    ax_oz.set_xlabel("achieved insert rate (keys/s)")
    ax_oz.set_ylabel("first-read miss fraction")
    ax_oz.set_ylim(bottom=-0.001)
    ax_oz.legend(fontsize=8)
    ax_oz.set_title("OzoneKV: misses vs load", fontsize=9)

    if cr:
        cr.sort()
        xs = [c[0] for c in cr]
        ys = [c[1] for c in cr]
        ax_cr.plot(xs, ys, marker="o", color="#d62728", label="measured p50")
        ax_cr.plot(
            xs,
            [x / 2 for x in xs],
            linestyle="--",
            color="#888888",
            label="interval / 2",
        )
        for x, y, miss in cr:
            ax_cr.annotate(
                f"{miss:.0%} missed",
                (x, y),
                textcoords="offset points",
                xytext=(0, -12),
                ha="center",
                fontsize=6,
            )
        ax_cr.set_xscale("log")
        ax_cr.set_yscale("log")
        ax_cr.set_xlabel("sync interval (ms)")
        ax_cr.set_ylabel("visibility p50 (ms)")
        ax_cr.legend(fontsize=8)
        ax_cr.set_title("cr-sqlite: staleness tracks the interval", fontsize=9)

    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    fig.savefig(out, dpi=300)
    print(f"wrote {out}")


_LINE = re.compile(r"^\[CONSISTENCY\],\s*([^,]+?),\s*(.+?)\s*$")
_AGG = re.compile(r"_agg.*\.result$")


def print_table(results_dir):
    w = csv.writer(sys.stdout)
    w.writerow(["file", "metric", "value"])
    for fn in sorted(os.listdir(results_dir)):
        if not _AGG.search(fn):
            continue
        with open(os.path.join(results_dir, fn), errors="replace") as f:
            for line in f:
                m = _LINE.match(line.strip())
                if m:
                    w.writerow([fn, m.group(1), m.group(2)])


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("staleness")
    sp.add_argument("dirs", nargs="+")
    sp.add_argument("--out", default="staleness_cdf.pdf")

    sp = sub.add_parser("lost-updates")
    sp.add_argument("dirs", nargs="+")
    sp.add_argument("--out", default="lost_updates.pdf")

    sp = sub.add_parser("visibility")
    sp.add_argument("dirs", nargs="+")
    sp.add_argument("--out", default="visibility.pdf")

    sp = sub.add_parser("visibility-sweep")
    sp.add_argument("dirs", nargs="+")
    sp.add_argument("--out", default="visibility_sweep.pdf")

    sp = sub.add_parser("visibility-scaling")
    sp.add_argument("--layout", choices=("grid", "row"), default="grid")
    sp.add_argument("--legend", choices=("bottom", "right"), default="bottom")
    sp.add_argument("dirs", nargs="+")
    sp.add_argument("--out", default="visibility_scaling.pdf")

    sp = sub.add_parser("table")
    sp.add_argument("results_dir")

    args = p.parse_args()
    if args.cmd == "staleness":
        plot_staleness(args.dirs, args.out)
    elif args.cmd == "lost-updates":
        plot_lost_updates(args.dirs, args.out)
    elif args.cmd == "visibility":
        plot_visibility(args.dirs, args.out)
    elif args.cmd == "visibility-sweep":
        plot_visibility_sweep(args.dirs, args.out)
    elif args.cmd == "visibility-scaling":
        plot_visibility_scaling(args.dirs, args.out, args.layout, args.legend)
    elif args.cmd == "table":
        print_table(args.results_dir)


if __name__ == "__main__":
    main()
