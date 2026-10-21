#!/usr/bin/env python3
"""Multi-node YCSB figures: cr-sqlite (async / async-10ms / fenced reads),
ozonedb-corfu (default / linearizable), slatedb (durable writes).

Reads multinode aggregate + per-writer .result files produced by
`bench.py multinode` (crsqlite), run_multinode_ycsb_with_corfu.sh
(ozonedb) and slatedb_multinode.py from any number of result dirs; a
campaign dir (cells one level down, e.g. bench/results/local/tp120) can
be passed directly and is recursed one level. When a cross-node aggregate
is missing (a laptop-side orchestrator hang killed the fetch but the
writers finished), SumWriterThroughput is rebuilt as the sum of
per-writer [OVERALL] Throughput -- identical arithmetic to the aggregate
writer.

Cells with multiple trials are summarised as mean +/- error (--err
std|sem|minmax); the printed summary carries n per cell so a partially
backfilled campaign (n=1..3) is visible at a glance.

ozonedb linearizable runs carry the same db token as default runs, so tag
their result dirs with --relabel DIR=ozonedb-corfu-linearizable (a
relabel on a campaign dir applies to its cell subdirs too).

Modes:
  faceoff (default): grouped bars, workloads x engines, at --writers N.
  scaling:           SumWriterThroughput vs total writers, per workload,
                     plus fenced-read barrier p50/p99 vs writer count.

Engine pairing (PLAN-syncread.md): cr-sqlite <-> ozonedb default (both
eventually-visible reads); cr-sqlite-syncread <-> ozonedb --linearizable
(both freshness-guaranteed reads).

Usage:
    python3 plot_throughput.py <result-dirs...> [--mode faceoff|scaling]
        [--writers N] [--err std|sem|minmax] [--relabel DIR=ENGINE ...]
        [--out NAME]
"""

import argparse
import math
import os
import re
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Ordered: eventually-visible engines first, then the freshness pair.
ENGINES = [
    "crsqlite",
    "crsqlite-sync10ms",
    "ozonedb-corfu",
    "slatedb-durable",
    "crsqlite-syncread",
    "ozonedb-corfu-linearizable",
]
ENGINE_LABELS = {
    "crsqlite": "cr-sqlite (async reads)",
    "crsqlite-sync10ms": "cr-sqlite (async reads, 10 ms sync)",
    "crsqlite-syncread": "cr-sqlite (fenced reads)",
    "ozonedb-corfu": "ozonedb (default reads)",
    "ozonedb-corfu-linearizable": "ozonedb (linearizable reads)",
    "slatedb-durable": "slatedb (durable writes)",
}
# Five hues; the two cr-sqlite async variants share the orange family and
# are told apart by marker, keeping the async/fenced pairing visible.
ENGINE_COLORS = {
    "crsqlite": "#DD8452",
    "crsqlite-sync10ms": "#CCB974",
    "crsqlite-syncread": "#C44E52",
    "ozonedb-corfu": "#8CB4E1",
    "ozonedb-corfu-linearizable": "#4C72B0",
    "slatedb-durable": "#55A868",
}
ENGINE_MARKERS = {
    "crsqlite": "o",
    "crsqlite-sync10ms": "v",
    "crsqlite-syncread": "s",
    "ozonedb-corfu": "^",
    "ozonedb-corfu-linearizable": "D",
    "slatedb-durable": "P",
}

AGG_RE = re.compile(
    r"^(?P<ks>[^-]+)-(?P<opcnt>\d+)-(?P<rc>\d+)-workload(?P<wl>[^-]+)-"
    r"(?P<db>.+?)_agg_multinode_w(?P<total>\d+)_t\d+_trial(?P<trial>\d+)\.result$"
)
PERWRITER_RE = re.compile(
    r"^(?P<ks>[^-]+)-(?P<opcnt>\d+)-(?P<rc>\d+)-workload(?P<wl>[^-]+)-"
    r"(?P<db>.+?)_w(?P<widx>\d+)of(?P<total>\d+)_t\d+_trial(?P<trial>\d+)\.result$"
)
# Per-host aggregates carry the [SYNCREAD] barrier stats.
HOSTAGG_RE = re.compile(
    r"^(?P<ks>[^-]+)-(?P<opcnt>\d+)-(?P<rc>\d+)-workload(?P<wl>[^-]+)-"
    r"(?P<db>.+?)_agg_w\d+of(?P<total>\d+)_off(?P<off>\d+)_t\d+"
    r"_trial(?P<trial>\d+)\.result$"
)


def parse_kv(path):
    """{(tag, key): value} from a YCSB-style '[TAG], Key, Value' file.
    Also returns the [SYNCREAD] WriterNBarrierP50/P99 values as a list."""
    out = {}
    p50s, p99s = [], []
    with open(path) as f:
        for line in f:
            parts = [p.strip() for p in line.strip().split(",")]
            if len(parts) >= 3 and parts[0].startswith("["):
                try:
                    v = float(parts[2])
                except ValueError:
                    continue
                out[(parts[0], parts[1])] = v
                if parts[0] == "[SYNCREAD]":
                    if "BarrierP50" in parts[1]:
                        p50s.append(v)
                    elif "BarrierP99" in parts[1]:
                        p99s.append(v)
    return out, p50s, p99s


def expand_dirs(dirs, relabel):
    """Each given dir plus its immediate subdirs (the campaign layout has
    one dir per cell). A relabel on a parent propagates to its subdirs."""
    out = []
    for d in dirs:
        if not os.path.isdir(d):
            print(f"[collect] skipping missing dir {d}")
            continue
        out.append(d)
        for sub in sorted(os.listdir(d)):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                out.append(p)
                if os.path.abspath(d) in relabel:
                    relabel[os.path.abspath(p)] = relabel[os.path.abspath(d)]
    return out


def collect(dirs, relabel):
    """Per-trial values, keyed (wl, engine, total, trial):
    {"tput", "read_ms", ..., "barrier_p50", "barrier_p99"}; plus caption
    metadata {"ks", "rc", "duration_s", "hosts"} read from the data."""
    agg_tput = {}
    writer_tput = defaultdict(dict)   # key -> widx -> ops/s
    lat_acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    barrier = defaultdict(lambda: ([], []))
    meta = {"ks": set(), "rc": set(), "duration_s": [], "hosts": defaultdict(set)}

    def engine_of(d, db):
        return relabel.get(os.path.abspath(d), db)

    for d in expand_dirs(dirs, relabel):
        for fn in sorted(os.listdir(d)):
            path = os.path.join(d, fn)
            if not os.path.isfile(path):
                continue
            m = AGG_RE.match(fn)
            if m:
                kv, _, _ = parse_kv(path)
                t = kv.get(("[AGGREGATE]", "SumWriterThroughput(ops/sec)"))
                if t is not None:
                    key = (m.group("wl"), engine_of(d, m.group("db")),
                           int(m.group("total")), int(m.group("trial")))
                    agg_tput[key] = t
                continue
            m = HOSTAGG_RE.match(fn)
            if m:
                _, p50s, p99s = parse_kv(path)
                key = (m.group("wl"), engine_of(d, m.group("db")),
                       int(m.group("total")), int(m.group("trial")))
                barrier[key][0].extend(p50s)
                barrier[key][1].extend(p99s)
                meta["hosts"][key].add(int(m.group("off")))
                continue
            m = PERWRITER_RE.match(fn)
            if m:
                kv, _, _ = parse_kv(path)
                key = (m.group("wl"), engine_of(d, m.group("db")),
                       int(m.group("total")), int(m.group("trial")))
                t = kv.get(("[OVERALL]", "Throughput(ops/sec)"))
                if t is not None:
                    writer_tput[key][int(m.group("widx"))] = t
                rt = kv.get(("[OVERALL]", "RunTime(ms)"))
                if rt:
                    meta["duration_s"].append(rt / 1000.0)
                meta["ks"].add(m.group("ks"))
                meta["rc"].add(int(m.group("rc")))
                for op in ("READ", "UPDATE", "INSERT"):
                    ops = kv.get((f"[{op}]", "Operations"))
                    avg = kv.get((f"[{op}]", "AverageLatency(us)"))
                    if ops and avg:
                        acc = lat_acc[key][op]
                        acc[0] += ops
                        acc[1] += ops * avg

    trials = defaultdict(dict)
    for key, per_w in writer_tput.items():
        total = key[2]
        if len(per_w) != total:
            print(f"[collect] WARNING {key}: {len(per_w)}/{total} per-writer "
                  "files -- computed sum UNDER-REPORTS")
        trials[key]["tput"] = sum(per_w.values())
        trials[key]["tput_source"] = "per-writer-sum"
    for key, t in agg_tput.items():
        trials[key]["tput"] = t
        trials[key]["tput_source"] = "aggregate"
    for key, per_op in lat_acc.items():
        for op, (ops, us) in per_op.items():
            if ops:
                trials[key][op.lower() + "_ms"] = (us / ops) / 1000.0
    for key, (p50s, p99s) in barrier.items():
        if p50s:
            trials[key]["barrier_p50"] = sum(p50s) / len(p50s)
        if p99s:
            trials[key]["barrier_p99"] = max(p99s)
    return trials, meta


ERR_FIELDS = ("tput", "read_ms", "update_ms", "insert_ms", "barrier_p50")


def summarize(trials, err):
    """Collapse trials: {(wl, engine, total): {field: mean,
    field+"_err": (lo, hi), "n": trials seen}}. barrier_p99 stays the max
    across trials (worst observed round, matching the per-host max)."""
    per_cell = defaultdict(lambda: defaultdict(list))
    for (wl, e, total, _trial), fields in trials.items():
        for f, v in fields.items():
            if isinstance(v, str):
                continue
            per_cell[(wl, e, total)][f].append(v)

    data = defaultdict(dict)
    for key, fields in per_cell.items():
        data[key]["n"] = max(len(v) for v in fields.values())
        for f, vals in fields.items():
            if f == "barrier_p99":
                data[key][f] = max(vals)
                continue
            mean = float(np.mean(vals))
            data[key][f] = mean
            if f in ERR_FIELDS and len(vals) > 1:
                if err == "minmax":
                    lo, hi = mean - min(vals), max(vals) - mean
                else:
                    s = float(np.std(vals, ddof=1))
                    if err == "sem":
                        s /= math.sqrt(len(vals))
                    lo = hi = s
                data[key][f + "_err"] = (lo, hi)
    return data


def caption_bits(meta):
    """Human caption fragments derived from the data, not hardcoded."""
    def fmt_keys(n):
        for unit, div in (("B", 10 ** 9), ("M", 10 ** 6), ("K", 10 ** 3)):
            if n >= div and n % div == 0:
                return f"{n // div}{unit}"
        return str(n)

    ks = "/".join(sorted(meta["ks"])) or "?"
    keys = "/".join(fmt_keys(n) for n in sorted(meta["rc"])) or "?"
    dur = (f"{np.median(meta['duration_s']):.0f} s"
           if meta["duration_s"] else "?")
    hosts = {len(v) for v in meta["hosts"].values()}
    nodes = f"up to {max(hosts)}" if hosts else "?"
    return ks, keys, dur, nodes


def yerr_of(cell, field):
    lo, hi = cell.get(field + "_err", (0.0, 0.0))
    return lo, hi


def plot_faceoff(data, meta, writers, out):
    workloads = sorted({k[0] for k in data if k[2] == writers})
    engines = [e for e in ENGINES
               if any((wl, e, writers) in data for wl in workloads)]
    _, _, dur, _ = caption_bits(meta)
    fig, (ax_t, ax_l) = plt.subplots(1, 2, figsize=(11.5, 4.2))
    x = np.arange(len(workloads))
    width = 0.8 / max(1, len(engines))
    for ax, field, title, ylabel in (
        (ax_t, "tput", f"Throughput ({writers} writers, {dur})",
         "Sum writer throughput (ops/s)"),
        (ax_l, "read_ms", "Read latency", "Avg READ latency (ms)"),
    ):
        for i, e in enumerate(engines):
            xs, ys, elo, ehi = [], [], [], []
            for j, wl in enumerate(workloads):
                cell = data.get((wl, e, writers), {})
                v = cell.get(field)
                if v is not None:
                    xs.append(x[j] + (i - (len(engines) - 1) / 2) * width)
                    ys.append(v)
                    lo, hi = yerr_of(cell, field)
                    elo.append(lo)
                    ehi.append(hi)
            bars = ax.bar(xs, ys, width * 0.92, color=ENGINE_COLORS[e],
                          label=ENGINE_LABELS[e],
                          yerr=(elo, ehi) if any(elo) or any(ehi) else None,
                          capsize=2, error_kw={"linewidth": 0.8})
            for b, v in zip(bars, ys):
                txt = f"{v:,.0f}" if field == "tput" else (
                    f"{v * 1000:.0f}µs" if v < 0.1 else f"{v:.2g}ms")
                ax.annotate(txt, (b.get_x() + b.get_width() / 2, v),
                            ha="center", va="bottom", fontsize=6.5)
        ax.set_yscale("log")
        ax.set_xticks(x)
        ax.set_xticklabels([f"workload {w}" for w in workloads])
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", alpha=0.3)
        ax.margins(y=0.2)
    ax_t.legend(fontsize=7.5, loc="upper left")
    fig.suptitle(
        "YCSB multi-node: matched read guarantees "
        "(async ↔ default, fenced ↔ linearizable)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig


def plot_scaling(data, meta, out):
    workloads = sorted({k[0] for k in data})
    totals = sorted({k[2] for k in data})
    engines = [e for e in ENGINES if any(k[1] == e for k in data)]
    ks, keys, dur, nodes = caption_bits(meta)
    ncols = len(workloads) + 1
    fig, axes = plt.subplots(1, ncols, figsize=(3.5 * ncols, 3.6))
    for ax, wl in zip(axes, workloads):
        for e in engines:
            xs = [t for t in totals if (wl, e, t) in data
                  and "tput" in data[(wl, e, t)]]
            if not xs:
                continue
            ys = [data[(wl, e, t)]["tput"] for t in xs]
            errs = np.array([yerr_of(data[(wl, e, t)], "tput")
                             for t in xs]).T
            ax.errorbar(xs, ys, yerr=errs if errs.any() else None,
                        marker=ENGINE_MARKERS[e], color=ENGINE_COLORS[e],
                        label=ENGINE_LABELS[e], linewidth=1.6, markersize=5,
                        capsize=2.5, elinewidth=0.9)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xticks(totals)
        ax.set_xticklabels([str(t) for t in totals])
        ax.set_xlabel("total writers")
        ax.set_title(f"workload {wl}", fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("Sum writer throughput (ops/s)")
    axes[0].legend(fontsize=6.5, loc="best")

    # Barrier-cost panel (B1): fenced-read round cost vs writer count.
    axb = axes[-1]
    for wl, style in zip(workloads, ("-", "--", ":", "-.", (0, (1, 1)))):
        e = "crsqlite-syncread"
        xs = [t for t in totals if (wl, e, t) in data
              and "barrier_p50" in data[(wl, e, t)]]
        if not xs:
            continue
        axb.plot(xs, [data[(wl, e, t)]["barrier_p50"] for t in xs],
                 marker="s", linestyle=style, color=ENGINE_COLORS[e],
                 label=f"p50, workload {wl}", linewidth=1.4, markersize=4)
        axb.plot(xs, [data[(wl, e, t)].get("barrier_p99") for t in xs],
                 marker="s", linestyle=style, color="#8B3A3E", alpha=0.55,
                 label=f"p99, workload {wl}", linewidth=1.0, markersize=3)
    axb.set_xscale("log", base=2)
    axb.set_xticks(totals)
    axb.set_xticklabels([str(t) for t in totals])
    axb.set_xlabel("total writers")
    axb.set_ylabel("barrier round (ms)")
    axb.set_title("fenced-read barrier cost", fontsize=10)
    axb.grid(alpha=0.3)
    axb.legend(fontsize=5.5, loc="best")

    fig.suptitle(
        f"YCSB multi-node scaling ({nodes} client nodes, {ks}, "
        f"{keys} keys, {dur})", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return fig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--mode", choices=("faceoff", "scaling"), default="faceoff")
    ap.add_argument("--writers", type=int, default=2,
                    help="faceoff mode: total writer count to plot")
    ap.add_argument("--err", choices=("std", "sem", "minmax"), default="std",
                    help="error-bar spread across trials (default std)")
    ap.add_argument("--relabel", action="append", default=[],
                    metavar="DIR=ENGINE",
                    help="override the engine for every file in DIR "
                         "(e.g. a linearizable ozonedb result dir)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    relabel = {}
    for r in args.relabel:
        d, _, e = r.partition("=")
        if not e:
            sys.exit(f"bad --relabel {r!r}, expected DIR=ENGINE")
        relabel[os.path.abspath(d)] = e

    trials, meta = collect(args.dirs, relabel)
    if not trials:
        sys.exit("no result files found in the given dirs")
    data = summarize(trials, args.err)
    for key in sorted(data):
        cell = data[key]
        lo, hi = yerr_of(cell, "tput")
        err = f" -{lo:,.0f}/+{hi:,.0f}" if (lo or hi) else ""
        print(f"  {key}: tput {cell.get('tput', float('nan')):,.1f}{err} "
              f"(n={cell['n']})"
              + (f" read {cell['read_ms']:.3g}ms" if "read_ms" in cell else "")
              + (f" barrier p50 {cell['barrier_p50']:.1f}ms"
                 f"/p99 {cell['barrier_p99']:.0f}ms"
                 if "barrier_p50" in cell else ""))

    if args.mode == "faceoff":
        fig = plot_faceoff(data, meta, args.writers, args.out)
        out = args.out or "throughput_faceoff"
    else:
        fig = plot_scaling(data, meta, args.out)
        out = args.out or "throughput_scaling"

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out_dir, exist_ok=True)
    for ext in ("png", "pdf"):
        p = os.path.join(out_dir, f"{out}.{ext}")
        fig.savefig(p, dpi=150)
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
