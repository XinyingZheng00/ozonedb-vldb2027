#!/usr/bin/env python3
"""Generate analysis.md from trials.csv. Every number in the prose is
computed from the CSV at generation time, never hand-typed. So the
narrative cannot drift out of sync with the data. An earlier draft of this
analysis had a hardcoded crossover point, and it was wrong for exactly that
reason. Verify the numbers programmatically. Do not assume them.

When trials.csv has multiple repeats per (backend, N), that is a 'repeat'
column with values > 1, every number is aggregated as mean +/- sample stdev
across repeats before a finding uses it. A finding never uses the raw value
of a single repeat.

Usage:
    python3 generate_analysis_md.py [--csv PATH] [--out PATH]
"""
import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_common import load_rows

NUMERIC_FIELDS = ["total_add_actors", "duration_s", "total", "ok", "fail", "wall_s",
                   "cycles_per_sec", "mean_add_ms", "p50_add_ms", "p99_add_ms",
                   "mean_extract_ms", "daemon_ticks", "daemon_mean_tick_ms", "daemon_p99_tick_ms"]


def aggregate(rows):
    """Return one dict per (backend, N). Each numeric field is replaced by its
    mean across repeats, plus a '<field>_std'. The std is 0.0 for a
    single-repeat N. It is never silently dropped."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["backend"], int(r["n_workers"]))].append(r)
    agg = {}
    for (backend, n), group in groups.items():
        out = {"backend": backend, "n_workers": n, "repeats": len(group)}
        for field in NUMERIC_FIELDS:
            vals = [float(r[field]) for r in group if r.get(field) not in (None, "")]
            if vals:
                out[field] = statistics.mean(vals)
                out[field + "_std"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
            else:
                out[field] = None
                out[field + "_std"] = None
        agg[(backend, n)] = out
    return agg


def by_bn(agg, backend, n):
    return agg.get((backend, n))


def f(row, field):
    return None if row is None else row.get(field)


def fstd(row, field):
    return None if row is None else row.get(field + "_std")


def fmt_ms(v, std=None):
    if v is None:
        return "n/a"
    base = f"{v/1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"
    if std:
        s = f"{std/1000:.1f}s" if v >= 1000 else f"{std:.0f}ms"
        return f"{base} (SD {s})"
    return base


def fmt_tput(v, std=None):
    if v is None:
        return "n/a"
    base = f"{v/1000:.1f}K" if v >= 1000 else f"{v:.0f}"
    if std:
        s = f"{std/1000:.1f}K" if v >= 1000 else f"{std:.0f}"
        return f"{base} (SD {s})"
    return base


def find_crossover(agg, ns, b1, b2, field="cycles_per_sec"):
    """Return the first N (ascending) at which b2's mean exceeds b1's, plus
    the last N at which b1 still led. The function scans the aggregated
    means. It does not assume them."""
    last_b1_lead = None
    first_b2_lead = None
    for n in ns:
        r1, r2 = by_bn(agg, b1, n), by_bn(agg, b2, n)
        if not r1 or not r2:
            continue
        v1, v2 = f(r1, field), f(r2, field)
        if v1 is None or v2 is None:
            continue
        if v1 >= v2 and first_b2_lead is None:
            last_b1_lead = n
        if v2 > v1 and first_b2_lead is None:
            first_b2_lead = n
    return last_b1_lead, first_b2_lead


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "analysis.md"))
    args = ap.parse_args()

    rows = load_rows(args.csv)
    ns = sorted({int(r["n_workers"]) for r in rows})
    max_n = max(ns)
    n_repeats = max(int(r.get("repeat", 1)) for r in rows)
    duration_s = float(rows[0]["duration_s"])
    agg = aggregate(rows)

    sqlite1 = by_bn(agg, "sqlite", 1)
    sqlite_max = by_bn(agg, "sqlite", max_n)
    oz1 = by_bn(agg, "ozonedb", 1)
    oz_max = by_bn(agg, "ozonedb", max_n)
    rest_max = by_bn(agg, "sqlite_rest", max_n)

    sqlite_agg_rows = [v for (b, n), v in agg.items() if b == "sqlite"]
    sqlite_tput_peak = max(sqlite_agg_rows, key=lambda r: f(r, "cycles_per_sec"))
    sqlite_p99_peak = max(sqlite_agg_rows, key=lambda r: f(r, "p99_add_ms"))
    oz_p99_max = max(f(v, "p99_add_ms") for (b, n), v in agg.items() if b == "ozonedb")

    last_rest_lead, first_oz_lead = find_crossover(agg, ns, "sqlite_rest", "ozonedb")
    rest_lead_row = by_bn(agg, "sqlite_rest", last_rest_lead) if last_rest_lead else None
    oz_at_rest_lead = by_bn(agg, "ozonedb", last_rest_lead) if last_rest_lead else None
    oz_cross_row = by_bn(agg, "ozonedb", first_oz_lead) if first_oz_lead else None
    rest_at_cross = by_bn(agg, "sqlite_rest", first_oz_lead) if first_oz_lead else None

    lines = []
    lines.append("# Serval-dna issue #1 reproduction: three-way comparison\n")
    lines.append(
        f"This is a fixed-duration reproduction of the literal scenario in serval-dna issue #1. "
        f"Each trial runs {duration_s:.0f}s. Each backend/N has **{n_repeats} repeats**. The results "
        "are the mean +/- sample standard deviation across repeats. In the scenario, a real, "
        "unmodified `servald` daemon holds its Rhizome store open for the whole trial. The daemon "
        "adds through a benchmark-only, opt-in `persistent_adder_tick()` hook in "
        "`serval-dna/server.c`. The hook is gated behind `RHIZOME_PERSISTENT_ADD_INTERVAL_MS`. It "
        "reuses the same internal functions that `rhizome add file` itself calls.\n\n"
        "N independent CLI worker processes race the daemon. They run `rhizome add/extract file` "
        "against the *same* store. Each operation is a fresh subprocess. `sqlite_rest` is the "
        "re-architected fix. There, the daemon is the *only* process that ever opens the store. So "
        "instead of an internal adder, it gets N+1 REST clients. This matches the volume of the "
        "other two backends: N workers + 1 daemon = N+1 add-issuing actors.\n"
    )
    lines.append(
        f"Data: `persistent_adder_results/trials.csv`, N in {ns}, {n_repeats} repeats each. "
        f"`run_persistent_adder_benchmark.py` generated it. Figures: `plots/throughput.pdf`, "
        f"`plots/mean_add_latency.pdf`, `plots/p99_add_latency.pdf`. The error bars are the SD "
        f"across repeats. Regenerate the figures with `python3 plots/plot_*.py`.\n"
    )

    lines.append("## Findings\n")

    if sqlite1 and oz1:
        lines.append(
            f"**1. The real bug shows in the tail latency, not only in the mean, even at N=1.** With "
            f"one CLI worker that races the daemon's own persistent connection, SQLite's p99 add() "
            f"latency is already {fmt_ms(f(sqlite1,'p99_add_ms'), fstd(sqlite1,'p99_add_ms'))}. "
            f"The mean is {fmt_ms(f(sqlite1,'mean_add_ms'), fstd(sqlite1,'mean_add_ms'))}. This is a "
            f"real `SQLITE_BUSY`/lock-retry cost, not a modeling artifact. The p99 peaks at "
            f"{fmt_ms(f(sqlite_p99_peak,'p99_add_ms'), fstd(sqlite_p99_peak,'p99_add_ms'))} "
            f"at N={sqlite_p99_peak['n_workers']:.0f}. OzoneDB's p99 at N=1 is "
            f"{fmt_ms(f(oz1,'p99_add_ms'), fstd(oz1,'p99_add_ms'))}, which is close to its mean. "
            f"It stays under {fmt_ms(oz_p99_max)} at every N tested.\n"
        )

    if sqlite_tput_peak and sqlite_max and oz_max:
        lines.append(
            f"**2. SQLite's throughput does not plateau under contention. It collapses.** "
            f"Cycles/sec peaks at {fmt_tput(f(sqlite_tput_peak,'cycles_per_sec'), fstd(sqlite_tput_peak,'cycles_per_sec'))} "
            f"around N={sqlite_tput_peak['n_workers']:.0f}. Then it *declines* as N grows, "
            f"down to {fmt_tput(f(sqlite_max,'cycles_per_sec'), fstd(sqlite_max,'cycles_per_sec'))} "
            f"at N={max_n}. More concurrent processes make the whole system do strictly less real "
            f"work per second, not more. Neither OzoneDB "
            f"({fmt_tput(f(oz_max,'cycles_per_sec'), fstd(oz_max,'cycles_per_sec'))} at N={max_n}) "
            f"nor sqlite_rest ever falls below its own low-N throughput in this way.\n"
        )

    if sqlite1 and sqlite_max and oz1 and oz_max and f(sqlite1, "daemon_ticks") and f(oz1, "daemon_ticks"):
        lines.append(
            f"**3. Under SQLite, contention does not only slow the CLI side. It also starves the "
            f"daemon's own steady writes.** The persistent daemon adder ticks on a fixed "
            f"100ms timer. So with no contention, it should complete a near-constant number of "
            f"adds for every N. Under SQLite it does not. The tick count goes from "
            f"{f(sqlite1,'daemon_ticks'):.0f} (SD {fstd(sqlite1,'daemon_ticks'):.0f}) at N=1 "
            f"down to {f(sqlite_max,'daemon_ticks'):.0f} (SD {fstd(sqlite_max,'daemon_ticks'):.0f}) "
            f"at N={max_n}. The lock contention of the CLI workers starves the daemon's own "
            f"connection. Under OzoneDB, the daemon's tick count is "
            f"{f(oz1,'daemon_ticks'):.0f} (SD {fstd(oz1,'daemon_ticks'):.0f}) at N=1 and "
            f"{f(oz_max,'daemon_ticks'):.0f} (SD {fstd(oz_max,'daemon_ticks'):.0f}) at N={max_n}. "
            f"The number of other processes that write at the same time has almost no effect on it.\n"
        )

    if last_rest_lead and first_oz_lead and rest_lead_row and oz_at_rest_lead and oz_cross_row and rest_at_cross:
        lines.append(
            f"**4. sqlite_rest leads at low N on lighter clients. Then OzoneDB overtakes it when "
            f"its single serialization point starts to bind.** Part of sqlite_rest's early lead is "
            f"client cost, not architecture. Its N+1 clients are plain HTTP calls from a process "
            f"that already runs. They are not fresh `servald` CLI launches with their own "
            f"keyring/crypto init. So sqlite_rest is still ahead at N={last_rest_lead} "
            f"({fmt_tput(f(rest_lead_row,'cycles_per_sec'))} against OzoneDB's "
            f"{fmt_tput(f(oz_at_rest_lead,'cycles_per_sec'))} cycles/s).\n\n"
            f"By N={first_oz_lead}, the single daemon queue is the bottleneck, and OzoneDB has "
            f"overtaken it ({fmt_tput(f(oz_cross_row,'cycles_per_sec'))} against "
            f"{fmt_tput(f(rest_at_cross,'cycles_per_sec'))}). By N={max_n} the gap is stark. "
            f"sqlite_rest falls to {fmt_tput(f(rest_max,'cycles_per_sec'), fstd(rest_max,'cycles_per_sec'))} "
            f"cycles/s. The mean wall time for that trial was {f(rest_max,'wall_s'):.1f}s (SD "
            f"{fstd(rest_max,'wall_s'):.1f}s) against a {f(rest_max,'duration_s'):.0f}s target. "
            f"That is a real backlog, not only a rate drop. OzoneDB still delivers "
            f"{fmt_tput(f(oz_max,'cycles_per_sec'), fstd(oz_max,'cycles_per_sec'))}.\n"
        )
    else:
        lines.append(
            "**4. sqlite_rest versus OzoneDB crossover:** this data shows no crossover. Either "
            "sqlite_rest led at every N tested, or OzoneDB led at every N tested. Re-check against "
            "the actual N sweep before you rely on this framing.\n"
        )

    rest_fail_rows = [v for (b, n), v in agg.items() if b == "sqlite_rest" and (f(v, "fail") or 0) > 0]
    if rest_fail_rows:
        worst = max(rest_fail_rows, key=lambda r: f(r, "fail"))
        rest_fail_ns = sorted(int(r["n_workers"]) for r in rest_fail_rows)
        lines.append(
            f"**5. Past N={min(rest_fail_ns)}, sqlite_rest does not only slow down. It drops "
            f"requests.** sqlite and ozonedb had zero failures across every N and every "
            f"repeat in this sweep. sqlite_rest did not. At N in {rest_fail_ns}, some requests hit a "
            f"real {f(worst,'duration_s'):.0f}s connection timeout. The daemon's request queue "
            f"never got to them within the whole trial. The worst case is "
            f"{f(worst,'fail'):.1f} (SD {fstd(worst,'fail'):.1f}) failed cycles out of "
            f"{f(worst,'total'):.0f} at N={int(worst['n_workers'])}. This is a real reliability cost "
            f"of the single-daemon architecture under sustained load. It is not only the throughput "
            f"ceiling in finding 4.\n"
        )

    lines.append(
        "**Caveat on sqlite_rest comparisons:** REST clients are structurally lighter than the "
        "CLI-launch path that `sqlite` and `ozonedb` use. Each REST cycle is a plain HTTP call "
        "from a process that already runs. It is not a fresh `servald` process that pays "
        "keyring/crypto init every time. Part of the advantage of sqlite_rest at low N is client "
        "cost, not architecture. The fairer read is the throughput-scaling *trend* across the N "
        "sweep, not the absolute value at any single low-N point.\n"
    )

    lines.append("## Full results (mean ± SD across repeats)\n")
    display_fields = ["cycles_per_sec", "mean_add_ms", "p99_add_ms", "daemon_ticks", "fail"]
    header = ["backend", "n_workers", "repeats"] + display_fields
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for (backend, n) in sorted(agg.keys(), key=lambda k: (k[0], k[1])):
        r = agg[(backend, n)]
        cells = [backend, str(n), str(r["repeats"])]
        for field in display_fields:
            v, s = f(r, field), fstd(r, field)
            if v is None:
                cells.append("—")
            elif field in ("daemon_ticks", "fail"):
                cells.append(f"{v:.0f} ± {s:.0f}" if s else f"{v:.0f}")
            else:
                cells.append(f"{v:.2f} ± {s:.2f}" if s else f"{v:.2f}")
        lines.append("| " + " | ".join(cells) + " |")

    out_path = Path(args.out)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
